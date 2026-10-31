"""Multi-account pool and failover management for Mahjong Soul.

Handles account deduplication, credential pooling, safe diagnostic masking,
retry with backup accounts on rate limit (error 540) or connection drops,
and in-memory token refreshes for environment-managed pools.
"""
from __future__ import annotations

import asyncio

import httpx

import hashlib
import json
import logging
import os
import time
from typing import TYPE_CHECKING, Any, Callable

from fastapi import HTTPException

from server.logger import log_debug, log_info, log_warning
from server.majsoul.client import MajsoulClient, MajsoulError, MajsoulTokenInvalid
from server.paipu_performance import get_value, measure, set_value

if TYPE_CHECKING:
    import server.app as server_app

log = logging.getLogger(__name__)

# Refreshed credentials for environment-managed pools remain in memory only.
_env_pool_token_overrides: dict[str, tuple[str | None, str]] = {}
# A 540 response has no reliable reset timestamp; avoid retrying it for a full day.
_RATE_LIMIT_COOLDOWN = 24 * 60 * 60
# Entire remote record download, including route lookup, logins and failovers.
FETCH_RECORD_TOTAL_TIMEOUT_SECONDS = 60
_rate_limited_until: dict[str, float] = {}


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


async def login_with_account_spec(
    acc: dict,
    client: MajsoulClient | None = None,
):
    """Log in using a specific account dict (token first, fallback to password)."""
    if client is None:
        import server.app as server_app
        client = server_app.ms_client

    log_debug(事件="WebSocket连接", 状态="建立会话")
    await client.close()
    set_value("雀魂连接结果", "进行中")
    try:
        await client.connect()
    except Exception:
        set_value("雀魂连接结果", "失败")
        raise
    else:
        set_value("雀魂连接结果", "成功")
    token = acc.get("access_token")
    account = acc.get("account")
    password = acc.get("password")
    masked = _mask_account(account or str(acc.get("account_id")))

    logged_in = False
    if token:
        try:
            with measure("雀魂登录", failure_count="雀魂登录失败次数"):
                await client.login_token(token)
            client.current_account = account
            logged_in = True
            log_info(事件="雀魂登录", 状态="成功", 登录方式="令牌登录", 账号=masked, 昵称=client.nickname)
        except MajsoulTokenInvalid as e:
            log_warning(事件="雀魂登录", 状态="失败", 登录方式="令牌登录", 失败阶段="令牌失效", 账号=masked, 错误原因=_redact_error(e, token, password))
        except BaseException:
            # Transient errors and cancellation must never trigger password login.
            await client.close()
            raise

    if not logged_in:
        if account and password:
            try:
                with measure("雀魂登录", failure_count="雀魂登录失败次数"):
                    await client.login_password(account, password)
                client.current_account = account
                logged_in = True
                log_info(事件="雀魂登录", 状态="成功", 登录方式="密码登录", 账号=masked, 昵称=client.nickname)
                if client.access_token and client.access_token != token:
                    _persist_updated_token(acc, client.access_token, client.nickname, client.account_id)
            except Exception:
                await client.close()
                raise
        else:
            await client.close()
            raise MajsoulError(f"账号 {masked} 缺少有效密码或 Token")


async def ensure_ms_logged_in(client: MajsoulClient | None = None):
    """Ensure client is logged in with an available account."""
    import server.app as server_app
    if client is None:
        client = server_app.ms_client

    if client.logged_in and client._ws is not None and not getattr(client._ws, "closed", False):
        return

    get_accounts_fn = getattr(server_app, "get_all_configured_accounts", get_all_configured_accounts)
    login_spec_fn = getattr(server_app, "login_with_account_spec", login_with_account_spec)

    accs = get_accounts_fn()
    if not accs:
        raise HTTPException(
            status_code=401,
            detail="雀魂服务暂时无法认证，请检查账号设置或使用演示牌谱/本地 JSON。",
        )

    for idx, acc in enumerate(accs):
        acc_label = acc.get("nickname") or _mask_account(acc.get("account")) or f"账号{idx+1}"
        try:
            await login_spec_fn(acc)
            return
        except Exception as e:
            log_warning(
                事件="雀魂登录",
                状态="失败",
                失败阶段="账号认证",
                账号=acc_label,
                昵称=acc.get("nickname"),
                错误原因=_redact_error(e, acc.get("password"), acc.get("access_token"), getattr(client, "access_token", None)),
            )

    raise HTTPException(status_code=401, detail="雀魂服务暂时无法认证，请检查账号设置或稍后重试。")


async def fetch_record_with_failover(
    game_uuid: str,
    client: MajsoulClient | None = None,
    client_lock=None,
):
    """Bound the complete remote download, not just individual RPC retries."""
    if client is None and client_lock is None:
        from server.admin_accounts import manager as admin_manager
        current = admin_manager()
        if current is not None:
            return await current.fetch_record(game_uuid)
    try:
        async with asyncio.timeout(FETCH_RECORD_TOTAL_TIMEOUT_SECONDS):
            return await _fetch_record_with_failover_inner(game_uuid, client, client_lock)
    except TimeoutError as exc:
        set_value("最近雀魂失败类型", "网络失败")
        raise HTTPException(status_code=503, detail="连接雀魂服务器超时，请稍后重试。") from exc


async def _fetch_record_with_failover_inner(
    game_uuid: str,
    client: MajsoulClient | None = None,
    client_lock=None,
):
    """Fetch through a shared session; reconnect or switch accounts exclusively."""
    from contextlib import asynccontextmanager
    import server.app as server_app

    client = client or server_app.ms_client
    client_lock = client_lock or server_app._ms_client_lock
    get_accounts = getattr(server_app, "get_all_configured_accounts", get_all_configured_accounts)
    login = getattr(server_app, "login_with_account_spec", login_with_account_spec)
    is_logged_in = getattr(server_app, "is_client_logged_in_as", is_client_logged_in_as)
    accounts = list(get_accounts())
    if client.logged_in and client._ws is not None and not getattr(client._ws, "closed", False):
        if not any(is_logged_in(account) for account in accounts):
            accounts.insert(0, {"account": client.current_account, "account_id": client.account_id,
                                "access_token": client.access_token})
    if not accounts:
        set_value("最近雀魂失败类型", "未配置账号")
        raise HTTPException(status_code=401, detail="雀魂牌谱服务暂不可用，请检查账号设置或上传牌谱 JSON 文件。")

    @asynccontextmanager
    async def read_session():
        if hasattr(client_lock, "read"):
            async with client_lock.read():
                yield
        else:
            async with client_lock:
                yield

    attempted: set[str] = set()
    outcomes: list[str] = []
    while True:
        available = []
        for account in accounts:
            key = _account_pool_key(account)
            if key in attempted:
                continue
            cooldown = hashlib.sha256(key.encode("utf8")).hexdigest()
            if _rate_limited_until.get(cooldown, 0) > time.monotonic():
                attempted.add(key)
                outcomes.append("rate_limited")
                set_value("最近雀魂失败类型", "抓取限流")
            else:
                _rate_limited_until.pop(cooldown, None)
                available.append(account)
        if not available:
            break

        async with read_session():
            account = next((a for a in available if is_logged_in(a)), None)
            if account is not None:
                session = client._ws
                try:
                    return await client.fetch_record(game_uuid)
                except Exception as error:
                    failure = error

        if account is not None and isinstance(failure, (TimeoutError, httpx.TimeoutException)):
            # A stalled RPC usually indicates a bad route, not a bad account.
            # Retry the same account on a different gateway before switching accounts.
            async with client_lock:
                if client._ws is session:
                    mark_unhealthy = getattr(client, "mark_current_gateway_unhealthy", None)
                    if callable(mark_unhealthy):
                        mark_unhealthy()
                    await client.close()
                    try:
                        await login(account)
                        if not is_logged_in(account):
                            raise MajsoulError("更换线路后雀魂会话不可用")
                        return await client.fetch_record(game_uuid)
                    except Exception as retry_error:
                        failure = retry_error

        if account is None:
            async with client_lock:
                if any(is_logged_in(a) for a in available):
                    continue
                account = available[0]
                key = _account_pool_key(account)
                try:
                    await login(account)
                    if not is_logged_in(account):
                        raise MajsoulError("登录后雀魂会话不可用")
                except Exception as error:
                    log_warning(
                        事件="雀魂登录",
                        状态="失败",
                        失败阶段="会话建立",
                        账号=_mask_account(account.get("account")),
                        昵称=account.get("nickname"),
                        错误原因=_redact_error(error, account.get("password"), account.get("access_token")),
                    )
                    text = str(error).lower()
                    network_failure = isinstance(error, (TimeoutError, ConnectionError, OSError, httpx.TimeoutException, httpx.NetworkError)) or any(
                        x in text for x in ("timeout", "timed out", "connect", "network", "getaddrinfo", "超时", "连接", "断开", "线路")
                    )
                    outcomes.append("network_error" if network_failure else "login_failed")
                    if get_value("雀魂连接结果") == "失败":
                        set_value("最近雀魂失败类型", "连接准备或网络失败")
                    else:
                        set_value("最近雀魂失败类型", "网络失败" if outcomes[-1] == "network_error" else "登录失败")
                    attempted.add(key)
                    await client.close()
            continue

        key = _account_pool_key(account)
        code = getattr(failure, "code", None)
        text = str(failure)
        if code == 1203 or "1203" in text:
            set_value("最近雀魂失败类型", "牌谱不存在或无权访问")
            raise HTTPException(status_code=400, detail="雀魂牌谱不存在、已被删除或属于匿名/私密对局无权查看 (错误码 1203)。") from failure
        attempted.add(key)
        if code == 540 or "540" in text:
            cooldown = hashlib.sha256(key.encode("utf8")).hexdigest()
            _rate_limited_until[cooldown] = time.monotonic() + _RATE_LIMIT_COOLDOWN
            outcomes.append("rate_limited")
        elif code in (103, 104, 109, 1003, 1004) or any(x in text for x in ("鉴权失效", "未登录")):
            outcomes.append("auth_error")
        elif isinstance(failure, MajsoulError) and not any(x in text.lower() for x in ("连接已断开", "timeout", "网络")):
            outcomes.append("majsoul_error")
        else:
            outcomes.append("network_error")
        set_value("最近雀魂失败类型", {
            "rate_limited": "抓取限流", "auth_error": "登录失效",
            "majsoul_error": "雀魂接口失败", "network_error": "网络失败",
        }[outcomes[-1]])
        log_warning(
            事件="牌谱抓取",
            状态="失败",
            失败阶段="雀魂牌谱请求",
            错误原因=_redact_error(failure, account.get("password"), account.get("access_token"), getattr(client, "access_token", None)),
        )
        async with client_lock:
            if client._ws is session:
                await client.close()

    if outcomes and all(outcome == "rate_limited" for outcome in outcomes):
        raise HTTPException(status_code=429, detail="雀魂官方抓谱额度暂时耗尽 (错误码 540)。请稍后再试或导入牌谱 JSON 文件。")
    if "rate_limited" in outcomes:
        raise HTTPException(status_code=400, detail="抓取牌谱暂时不可用：部分抓谱额度已满，其他连接失败。请稍后重试或导入牌谱 JSON 文件。")
    if outcomes and all(outcome in ("login_failed", "auth_error") for outcome in outcomes):
        raise HTTPException(status_code=401, detail="雀魂服务暂时无法认证，请检查账号设置或稍后重试。")
    if "network_error" in outcomes:
        raise HTTPException(status_code=503, detail="连接雀魂服务器超时或失败，请稍后重试。")
    raise HTTPException(status_code=500, detail="抓取牌谱失败，请稍后重试或导入牌谱 JSON 文件。")
