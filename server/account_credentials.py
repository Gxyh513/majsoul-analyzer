"""Account credential normalization, deduplication and safe token refresh."""
from __future__ import annotations

import hashlib
import json
import os
from typing import Any, Callable

from .logger import log_warning
from .majsoul.client import MajsoulClient

# Environment-injected credentials never persist their refreshed tokens to disk.
_env_pool_token_overrides: dict[str, tuple[str | None, str]] = {}


def _account_pool_key(acc: dict) -> str:
    name = str(acc.get("account") or "").strip().lower()
    if name:
        return "account:" + name
    account_id = str(acc.get("account_id") or "").strip()
    return "id:" + account_id if account_id and account_id != "0" else "token:" + str(acc.get("access_token") or "")


def _managed_token_key(acc: dict) -> str:
    key = _account_pool_key(acc)
    if "MAJSOUL_ACCOUNTS_JSON" not in os.environ and acc.get("is_primary"):
        values = [os.environ.get(name, "") for name in ("MAJSOUL_ACCOUNT", "MAJSOUL_PASSWORD", "MAJSOUL_ACCESS_TOKEN")]
        key += ":" + hashlib.sha256(json.dumps(values).encode("utf8")).hexdigest()
    return key


def _mask_account(name_or_acc: str | None) -> str:
    """Mask account for safe diagnostic logging (e.g. u***@xxx.com)."""
    if not name_or_acc:
        return "未知账号"
    s = str(name_or_acc).strip()
    if "@" in s:
        parts = s.split("@", 1)
        user = parts[0]
        domain = parts[1]
        masked_user = (user[:2] + "***" + user[-1:]) if len(user) > 3 else (user[:1] + "***")
        return f"{masked_user}@{domain}"
    if len(s) > 4:
        return s[:2] + "***" + s[-2:]
    return s[:1] + "***"


mask_account = _mask_account


def _redact_error(error: Exception, *secrets: Any) -> str:
    """Keep useful server-side diagnostics without logging known credentials."""
    text = str(error)
    # Longer credentials must be removed first when secrets overlap.
    values = sorted({secret for secret in secrets if isinstance(secret, str) and secret}, key=len, reverse=True)
    for secret in values:
        text = text.replace(secret, "[已隐藏]")
    return text


def get_all_configured_accounts(
    cfg: dict | None = None,
    load_config_fn: Callable[[], dict[str, Any]] | None = None,
) -> list[dict]:
    """
    Extracts and stably deduplicates all configured accounts (primary + backups + pool).
    Deduplication rules:
    - Stable identifiers: normalized account name (email/phone), account_id, and access_token.
    - If any identifier matches an already seen account, treat as duplicate and skip (merging missing non-empty fields).
    - Preserves order: primary first, followed by backups in configured order.
    """
    if cfg is None:
        if load_config_fn is not None:
            cfg = load_config_fn()
        else:
            import server.app as server_app
            cfg = server_app.load_config()

    raw_candidates: list[tuple[dict, bool]] = []

    # 1. Primary account (top-level config)
    if cfg.get("account") or cfg.get("access_token") or cfg.get("account_id"):
        raw_candidates.append(
            (
                {
                    "account": cfg.get("account"),
                    "password": cfg.get("password"),
                    "access_token": cfg.get("access_token"),
                    "nickname": cfg.get("nickname"),
                    "account_id": cfg.get("account_id"),
                },
                True,
            )
        )

    # 2. Backup accounts
    for b in cfg.get("backup_accounts", []):
        if isinstance(b, dict) and (b.get("account") or b.get("access_token") or b.get("account_id")):
            raw_candidates.append((b, False))

    # 3. Optional account_pool or accounts list (for advanced pool configs)
    for pool_key in ("accounts", "account_pool"):
        for item in cfg.get(pool_key, []):
            if isinstance(item, dict) and (item.get("account") or item.get("access_token") or item.get("account_id")):
                raw_candidates.append((item, False))

    deduped: list[dict] = []
    seen_names: set[str] = set()
    seen_ids: set[str] = set()
    seen_tokens: set[str] = set()

    for raw, is_primary in raw_candidates:
        name = str(raw.get("account") or "").strip().lower()
        acc_id = str(raw.get("account_id") or "").strip()
        if acc_id in ("None", "0"):
            acc_id = ""
        token = str(raw.get("access_token") or "").strip()

        # Must have at least one identifier
        if not name and not acc_id and not token:
            continue

        # Check if matched against existing accounts
        matched_idx = -1
        if name and name in seen_names:
            matched_idx = next(
                (i for i, a in enumerate(deduped) if str(a.get("account") or "").strip().lower() == name),
                -1,
            )
        elif acc_id and acc_id in seen_ids:
            matched_idx = next(
                (i for i, a in enumerate(deduped) if str(a.get("account_id") or "").strip() == acc_id),
                -1,
            )
        elif token and token in seen_tokens:
            matched_idx = next(
                (i for i, a in enumerate(deduped) if str(a.get("access_token") or "").strip() == token),
                -1,
            )

        if matched_idx >= 0:
            # Duplicate entry: merge any missing credentials into existing entry
            existing = deduped[matched_idx]
            for k in ("password", "access_token", "nickname", "account_id", "account"):
                if not existing.get(k) and raw.get(k):
                    existing[k] = raw[k]
            continue

        # New unique account
        acc_entry = {
            "account": raw.get("account"),
            "password": raw.get("password"),
            "access_token": raw.get("access_token"),
            "nickname": raw.get("nickname"),
            "account_id": raw.get("account_id"),
            "is_primary": is_primary,
        }
        if name:
            seen_names.add(name)
        if acc_id:
            seen_ids.add(acc_id)
        if token:
            seen_tokens.add(token)

        if "MAJSOUL_ACCOUNTS_JSON" in os.environ or (is_primary and any(
            os.environ.get(key) for key in ("MAJSOUL_ACCOUNT", "MAJSOUL_PASSWORD", "MAJSOUL_ACCESS_TOKEN")
        )):
            override = _env_pool_token_overrides.get(_managed_token_key(acc_entry))
            if override and acc_entry.get("access_token") == override[0]:
                acc_entry["access_token"] = override[1]
        deduped.append(acc_entry)

    return deduped


def is_client_logged_in_as(acc: dict, client: MajsoulClient | None = None) -> bool:
    """Checks if client is currently active, connected, and logged in as the specified account."""
    if client is None:
        import server.app as server_app
        client = server_app.ms_client

    if not client.logged_in:
        return False
    if client._ws is None or getattr(client._ws, "closed", False):
        return False

    acc_id = acc.get("account_id")
    if acc_id is not None and client.account_id is not None:
        try:
            if int(acc_id) == int(client.account_id):
                return True
        except (ValueError, TypeError):
            pass

    token = acc.get("access_token")
    if token and getattr(client, "access_token", None):
        if token.strip() == str(client.access_token).strip():
            return True

    name = (acc.get("account") or "").strip().lower()
    client_name = (getattr(client, "current_account", None) or "").strip().lower()
    if name and client_name and name == client_name:
        return True

    return False


def _persist_updated_token(
    acc: dict,
    new_token: str,
    nickname: str | None = None,
    account_id: int | None = None,
    load_config_fn: Callable[[], dict[str, Any]] | None = None,
    save_config_fn: Callable[[dict[str, Any]], None] | None = None,
):
    """Keep managed-pool token refreshes in memory; persist local accounts normally."""
    if "MAJSOUL_ACCOUNTS_JSON" in os.environ or (acc.get("is_primary") and any(
        os.environ.get(key) for key in ("MAJSOUL_ACCOUNT", "MAJSOUL_PASSWORD", "MAJSOUL_ACCESS_TOKEN")
    )):
        key = _managed_token_key(acc)
        old = _env_pool_token_overrides.get(key)
        source_token = old[0] if old and old[1] == acc.get("access_token") else acc.get("access_token")
        _env_pool_token_overrides[key] = (source_token, new_token)
        return
    try:
        import server.app as server_app
        loader = load_config_fn or server_app.load_config
        saver = save_config_fn or server_app.save_config
        cfg = loader()
        updated = False
        target_acc = (acc.get("account") or "").strip().lower()
        target_id = str(acc.get("account_id") or "").strip()

        if acc.get("is_primary"):
            cfg["access_token"] = new_token
            if nickname and not cfg.get("nickname"):
                cfg["nickname"] = nickname
            if account_id and not cfg.get("account_id"):
                cfg["account_id"] = account_id
            updated = True
        else:
            for b in cfg.get("backup_accounts", []):
                b_acc = str(b.get("account") or "").strip().lower()
                b_id = str(b.get("account_id") or "").strip()
                if (target_acc and b_acc == target_acc) or (target_id and b_id == target_id):
                    b["access_token"] = new_token
                    if nickname and not b.get("nickname"):
                        b["nickname"] = nickname
                    if account_id and not b.get("account_id"):
                        b["account_id"] = account_id
                    updated = True
                    break
        if updated:
            saver(cfg)
    except Exception as e:
        log_warning(事件="配置保存异常", 错误原因="自动更新 access_token 失败: " + _redact_error(e, new_token, acc.get("access_token"), acc.get("password")))


