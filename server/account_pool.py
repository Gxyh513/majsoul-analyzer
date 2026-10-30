"""Multi-account pool and failover management for Mahjong Soul.

Handles account deduplication, credential pooling, safe diagnostic masking,
retry with backup accounts on rate limit (error 540) or connection drops,
and in-memory token refreshes for environment-managed pools.
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import os
import re
import tempfile
import threading
import time
from pathlib import Path
from typing import TYPE_CHECKING, Any, Callable

from fastapi import HTTPException

from server.majsoul.client import MajsoulClient, MajsoulError

if TYPE_CHECKING:
    import server.app as server_app

log = logging.getLogger(__name__)

# Refreshed credentials for environment-managed pools remain in memory only.
_env_pool_token_overrides: dict[str, tuple[str | None, str]] = {}
# A 540 response has no reliable reset timestamp; avoid retrying it for a full day.
_RATE_LIMIT_COOLDOWN = 24 * 60 * 60
_rate_limited_until: dict[str, float] = {}
_record_cache_io_lock = threading.Lock()


def _parse_valid_record(record: Any, game_uuid: str) -> tuple[list[dict], dict] | None:
    from server.majsoul.convert import game_meta, to_mjai

    if not isinstance(record, dict) or not isinstance(record.get("head"), dict) or not isinstance(record.get("actions"), list):
        return None
    if record["head"].get("uuid") != game_uuid:
        return None
    try:
        meta = game_meta(record)
        return to_mjai(record), meta
    except (ValueError, TypeError, KeyError, IndexError, AttributeError):
        return None
    except Exception:
        log.warning("[牌谱缓存校验失败] UUID: %s", game_uuid)
        return None


def _valid_record(record: Any, game_uuid: str) -> bool:
    return _parse_valid_record(record, game_uuid) is not None


def load_cached_record(records_dir: Path, game_uuid: str, *, include_parsed: bool = False,
                       timings: dict | None = None) -> dict | tuple[dict, list[dict], dict] | None:
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]{0,119}", game_uuid):
        return None
    path = records_dir / f"{game_uuid}.json"
    try:
        started = time.perf_counter()
        raw = path.read_text(encoding="utf8")
        if timings is not None:
            timings["原始牌谱读取"] = timings.get("原始牌谱读取", 0.0) + (time.perf_counter() - started) * 1000
        started = time.perf_counter()
        record = json.loads(raw)
        if timings is not None:
            timings["JSON解析"] = timings.get("JSON解析", 0.0) + (time.perf_counter() - started) * 1000
        started = time.perf_counter()
        parsed = _parse_valid_record(record, game_uuid)
        if timings is not None:
            timings["MJAI转换"] = timings.get("MJAI转换", 0.0) + (time.perf_counter() - started) * 1000
        if parsed is not None:
            try:
                path.touch()
            except OSError:
                pass
            log.info("[牌谱缓存命中] UUID: %s", game_uuid)
            return (record, *parsed) if include_parsed else record
        log.warning("[牌谱缓存校验失败] UUID: %s", game_uuid)
    except FileNotFoundError:
        pass
    except (OSError, ValueError, UnicodeError):
        log.warning("[牌谱缓存读取失败] UUID: %s", game_uuid)
    return None


def _without_credentials(value: Any) -> Any:
    """Exclude authentication fields from persisted game data at every depth."""
    if isinstance(value, dict):
        secret_keys = {"password", "access_token", "token", "refresh_token", "secret"}
        return {k: _without_credentials(v) for k, v in value.items()
                if not (isinstance(k, str) and k.lower() in secret_keys)}
    if isinstance(value, list):
        return [_without_credentials(item) for item in value]
    return value


def _save_record(records_dir: Path, game_uuid: str, record: dict, *,
                 parsed: tuple[list[dict], dict] | None = None) -> None:
    """Atomically persist a validated record; propagate any local write error."""
    sanitized = _without_credentials(record)
    if (not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]{0,119}", game_uuid)
            or ((parsed is None or sanitized != record) and not _valid_record(sanitized, game_uuid))):
        raise ValueError("invalid record")
    record = sanitized
    temp_path = None
    try:
        records_dir.mkdir(parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile(mode="w", encoding="utf8", dir=records_dir, prefix=".record-", suffix=".tmp", delete=False) as f:
            temp_path = Path(f.name)
            json.dump(record, f, ensure_ascii=False)
            f.flush()
            os.fsync(f.fileno())
        with _record_cache_io_lock:
            os.replace(temp_path, records_dir / f"{game_uuid}.json")
    finally:
        if temp_path is not None:
            temp_path.unlink(missing_ok=True)


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
        log.warning("[配置保存异常] 自动更新 access_token 失败: %s", _redact_error(e, new_token, acc.get("access_token"), acc.get("password")))


async def login_with_account_spec(
    acc: dict,
    client: MajsoulClient | None = None,
):
    """Log in using a specific account dict (token first, fallback to password)."""
    if client is None:
        import server.app as server_app
        client = server_app.ms_client

    log.info("[连接重建] 建立雀魂 WebSocket 会话")
    await client.close()
    await client.connect()
    token = acc.get("access_token")
    account = acc.get("account")
    password = acc.get("password")
    masked = _mask_account(account or str(acc.get("account_id")))

    logged_in = False
    if token:
        try:
            await client.login_token(token)
            client.current_account = account
            logged_in = True
            log.info("[雀魂登录] Token 登录成功 | 账号: %s | 昵称: %s | ID: %s", masked, client.nickname, client.account_id)
        except Exception as e:
            log.warning("[雀魂登录] 账号 %s 的 access_token 登录失败 (%s)，尝试密码登录...", masked, _redact_error(e, token, password))

    if not logged_in:
        if account and password:
            try:
                await client.login_password(account, password)
                client.current_account = account
                logged_in = True
                log.info("[雀魂登录] 密码登录成功 | 账号: %s | 昵称: %s | ID: %s", masked, client.nickname, client.account_id)
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
            log.warning(
                "[账号连接异常] 尝试登录账号 [%d/%d] (%s) 失败: %s",
                idx + 1, len(accs), acc_label,
                _redact_error(e, acc.get("password"), acc.get("access_token"), getattr(client, "access_token", None)),
            )

    raise HTTPException(status_code=401, detail="雀魂服务暂时无法认证，请检查账号设置或稍后重试。")


async def fetch_record_with_failover(
    game_uuid: str,
    client: MajsoulClient | None = None,
    records_dir: Path | None = None,
    client_lock: asyncio.Lock | None = None,
    *,
    include_parsed: bool = False,
    timings: dict | None = None,
) -> dict | tuple[dict, list[dict], dict]:
    """
    Fetches game record with automatic multi-account failover.
    Guarantees:
    - Serialized execution via _ms_client_lock (no race conditions/connection corruption)
    - Re-checks local disk cache inside lock in case a concurrent fetch already saved it
    - Tries each distinct configured account exactly once in order
    - Stops immediately if error is 1203 (牌谱不存在/无权查看) to avoid wasting other accounts' quota
    - Distinguishes 540 rate limits, auth/login failures, and network errors
    - Raises HTTP 429 ONLY if ALL actually attempted accounts returned 540
    - Keeps per-account diagnostics in server logs, not public error messages
    - Closes failed login or fetch connections before trying another account
    - Atomically saves fetched records; local persistence failures return an explicit error
    - Does not switch accounts, refetch, or close a valid connection after a local save failure
    - Does not replace the previous record when an atomic cache write fails
    """
    import server.app as server_app
    if client is None:
        client = server_app.ms_client
    if records_dir is None:
        records_dir = server_app.RECORDS_DIR
    if client_lock is None:
        client_lock = server_app._ms_client_lock

    get_accounts_fn = getattr(server_app, "get_all_configured_accounts", get_all_configured_accounts)
    login_spec_fn = getattr(server_app, "login_with_account_spec", login_with_account_spec)
    is_logged_in_fn = getattr(server_app, "is_client_logged_in_as", is_client_logged_in_as)

    async with client_lock:
        # Re-check cache inside lock to eliminate duplicate requests in concurrent scenarios
        cached_record = await asyncio.to_thread(load_cached_record, records_dir, game_uuid,
                                                include_parsed=include_parsed, timings=timings)
        if cached_record is not None:
            return cached_record

        accs = get_accounts_fn()
        if not accs:
            raise HTTPException(
                status_code=401,
                detail="雀魂牌谱服务暂不可用，请检查账号设置或上传牌谱 JSON 文件。",
            )

        attempt_outcomes: list[dict[str, Any]] = []
        # Keep the live session at the front, including when it belongs to a backup.
        accs = sorted(accs, key=lambda acc: not is_logged_in_fn(acc))

        for idx, acc in enumerate(accs):
            pool_key = hashlib.sha256(_account_pool_key(acc).encode("utf8")).hexdigest()
            if _rate_limited_until.get(pool_key, 0) > time.monotonic():
                log.info("[频控冷却] 跳过已限流账号")
                attempt_outcomes.append({"status": "rate_limited", "account": f"账号{idx + 1}"})
                continue
            _rate_limited_until.pop(pool_key, None)
            acc_label = acc.get("nickname") or _mask_account(acc.get("account")) or f"账号{idx+1}"
            acc_id_str = f" (ID: {acc.get('account_id')})" if acc.get("account_id") else ""
            display_name = f"{acc_label}{acc_id_str}"

            # Step A: Ensure logged into THIS specific account
            if not is_logged_in_fn(acc):
                if idx > 0:
                    log.info("[账号切换] 尝试下一个可用账号")
                try:
                    await login_spec_fn(acc)
                except Exception as login_err:
                    log.warning(
                        "[账号登录失败] 轮转尝试账号 [%d/%d] (%s) 登录失败: %s",
                        idx + 1,
                        len(accs),
                        display_name,
                        _redact_error(login_err, acc.get("password"), acc.get("access_token"), getattr(client, "access_token", None)),
                    )
                    err_str = str(login_err)
                    is_net = any(k in err_str.lower() for k in ("timeout", "connect", "connection", "network", "getaddrinfo"))
                    status_type = "network_error" if is_net else "login_failed"
                    attempt_outcomes.append({
                        "status": status_type,
                        "account": display_name,
                    })
                    await client.close()
                    continue

            # Update display name with actual nickname/id after login if available
            actual_display = f"{client.nickname or acc_label} (ID: {client.account_id or acc.get('account_id') or '未知'})"

            # Step B: Attempt fetchGameRecord with this account
            try:
                log.info(
                    "[牌谱抓取] 正在使用账号 [%d/%d] (%s) 抓取牌谱 (UUID: %s)...",
                    idx + 1,
                    len(accs),
                    actual_display,
                    game_uuid,
                )
                record = await client.fetch_record(game_uuid)
                started = time.perf_counter()
                parsed = await asyncio.to_thread(_parse_valid_record, record, game_uuid)
                if timings is not None:
                    timings["MJAI转换"] = timings.get("MJAI转换", 0.0) + (time.perf_counter() - started) * 1000
                if parsed is None:
                    raise MajsoulError("雀魂返回的牌谱数据无效或 UUID 不匹配")
                try:
                    save_task = asyncio.create_task(asyncio.to_thread(
                        _save_record, records_dir, game_uuid, record, parsed=parsed))
                    try:
                        await asyncio.shield(save_task)
                    except asyncio.CancelledError:
                        try:
                            await save_task
                        finally:
                            raise
                except (OSError, ValueError, TypeError) as e:
                    log.error("[牌谱缓存写入失败] UUID: %s (%s)", game_uuid, type(e).__name__)
                    raise HTTPException(status_code=500, detail="牌谱抓取成功，但原始牌谱保存失败，请检查缓存目录。") from e
                log.info("[牌谱抓取成功] 账号 (%s) 成功抓取牌谱: %s", actual_display, game_uuid)
                attempt_outcomes.append({"status": "success", "account": actual_display})
                return (record, *parsed) if include_parsed else record
            except HTTPException:
                raise
            except MajsoulError as e:
                err_str = str(e)
                safe_err = _redact_error(e, acc.get("password"), acc.get("access_token"), getattr(client, "access_token", None))
                code = getattr(e, "code", None)

                # 1203: 牌谱不存在或无权查看 - 立即终止轮换，避免无意义浪费其他账号配额
                if code == 1203 or "1203" in err_str:
                    log.warning("[牌谱不存在] 雀魂返回 1203 牌谱不存在或无权查看，终止轮换: %s", safe_err)
                    raise HTTPException(
                        status_code=400,
                        detail="雀魂牌谱不存在、已被删除或属于匿名/私密对局无权查看 (错误码 1203)。",
                    ) from e

                # 540: 官方频控限制 (单账号单日抓取额度已达上限)
                if code == 540 or "540" in err_str:
                    _rate_limited_until[pool_key] = time.monotonic() + _RATE_LIMIT_COOLDOWN
                    log.warning(
                        "[单账号额度耗尽] 账号 [%d/%d] (%s) 今日抓谱配额已达官方上限 (错误码 540)",
                        idx + 1,
                        len(accs),
                        actual_display,
                    )
                    attempt_outcomes.append({
                        "status": "rate_limited",
                        "code": 540,
                        "error": "单账号额度耗尽(540)",
                        "account": actual_display,
                    })
                    await client.close()
                    continue

                # 账号鉴权失效 (109 token过期, 1004/1003/104/103 等)
                is_auth_error = (code in (103, 104, 109, 1003, 1004)) or any(
                    k in err_str for k in ("109", "1004", "1003", "104", "103", "鉴权失效", "未登录")
                )
                if is_auth_error:
                    log.warning("[账号鉴权失效] 账号 (%s) 鉴权失效: %s", actual_display, safe_err)
                    attempt_outcomes.append({"status": "auth_error", "account": actual_display})
                    await client.close()
                    continue

                is_net = any(k in err_str for k in ("连接已断开", "timeout", "网络"))
                if is_net:
                    log.warning("[网络传输异常] 账号 (%s) 业务网络异常: %s", actual_display, safe_err)
                    attempt_outcomes.append({"status": "network_error", "account": actual_display})
                    await client.close()
                    continue

                log.warning("[接口业务异常] 账号 (%s) 抓谱异常: %s", actual_display, safe_err)
                attempt_outcomes.append({"status": "majsoul_error", "code": code, "account": actual_display})
                await client.close()
                continue
            except Exception as e:
                safe_err = _redact_error(e, acc.get("password"), acc.get("access_token"), getattr(client, "access_token", None))
                log.warning("[网络传输异常] 账号 (%s) 网络异常: %s", actual_display, safe_err)
                attempt_outcomes.append({"status": "network_error", "account": actual_display})
                await client.close()
                continue

        # All configured accounts exhausted. Analyze exact outcomes:
        num_accounts = len(accs)
        rate_limited_count = sum(1 for v in attempt_outcomes if v.get("status") == "rate_limited")
        login_failed_count = sum(1 for v in attempt_outcomes if v.get("status") in ("login_failed", "auth_error"))
        network_error_count = sum(1 for v in attempt_outcomes if v.get("status") == "network_error")

        # Case 1: All actually attempted accounts were verified to return 540
        if rate_limited_count == num_accounts:
            log.error("[全账号频控] 已配置的全部 %d 个雀魂账号今日抓取配额均已耗尽 (错误码 540)", num_accounts)
            raise HTTPException(
                status_code=429,
                detail="雀魂官方抓谱额度暂时耗尽 (错误码 540)。请稍后再试或导入牌谱 JSON 文件分析。",
            )

        # Build detailed diagnostic per-account outcome strings
        reasons = []
        for info in attempt_outcomes:
            acc_name = info.get("account", "未知账号")
            st = info.get("status")
            if st == "rate_limited":
                reasons.append(f"{acc_name}: 额度已满(540)")
            elif st == "login_failed":
                reasons.append(f"{acc_name}: 登录失败")
            elif st == "auth_error":
                reasons.append(f"{acc_name}: Token已失效")
            elif st == "network_error":
                reasons.append(f"{acc_name}: 网络异常")
            else:
                reasons.append(f"{acc_name}: 业务异常")
        detail_msg = "；".join(reasons)

        # Case 2: Some returned 540, but others failed login/network/other
        if rate_limited_count > 0:
            log.error("[账号容灾失败] 牌谱拉取失败 (部分额度耗尽但备用账号不可用): %s", detail_msg)
            raise HTTPException(
                status_code=400,
                detail="抓取牌谱暂时不可用：部分抓谱额度已满，其他连接失败。请稍后重试或导入牌谱 JSON 文件。",
            )

        # Case 3: All accounts failed login/auth
        if login_failed_count == num_accounts:
            log.error("[全账号认证失败] 所有已配置账号均登录或鉴权失败: %s", detail_msg)
            raise HTTPException(
                status_code=401,
                detail="雀魂服务暂时无法认证，请检查账号设置或稍后重试。",
            )

        # Case 4: Network errors
        if network_error_count > 0:
            log.error("[网络连接失败] 所有尝试连接均出现网络异常: %s", detail_msg)
            raise HTTPException(
                status_code=503,
                detail="连接雀魂服务器超时或失败，请稍后重试。",
            )

        log.error("[全账号抓谱失败] 所有已配置账号均无法抓取牌谱: %s", detail_msg)
        raise HTTPException(
            status_code=500,
            detail="抓取牌谱失败，请稍后重试或导入牌谱 JSON 文件。",
        )
