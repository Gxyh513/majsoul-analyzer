"""Small SQLite-backed administrator account pool and idle-account scheduler.

This module is intentionally separate from legacy account configuration and from
the ONNX, record-storage, and SSE code paths.
"""
from __future__ import annotations

import asyncio
from contextlib import contextmanager
import hmac
import os
import random
import sqlite3

import httpx
from websockets.exceptions import ConnectionClosed
import threading
import time
from pathlib import Path

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

from .account_pool import _redact_error
from .logger import log_warning
from .majsoul.client import MajsoulClient, MajsoulError, MajsoulTokenInvalid
from .paipu_performance import measure, set_value


def _db_path() -> Path:
    override = os.environ.get("MAJSOUL_ADMIN_DB_PATH", "").strip()
    if override:
        return Path(override).expanduser()
    workspace = Path("/mnt/workspace")
    return (workspace / "majsoul-analyzer" if workspace.is_dir() else Path(__file__).resolve().parents[1] / "data") / "admin_accounts.sqlite3"


class AccountStore:
    """Low-frequency, transactional account/configuration and counter operations."""

    def __init__(self, path: Path):
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.lock = threading.RLock()
        self._accounts_cache: tuple[tuple[int, int, int, int], list[dict]] | None = None
        with self._connect() as db:
            try:
                db.execute("PRAGMA journal_mode=WAL")
            except sqlite3.Error:
                pass
            db.executescript("""
                CREATE TABLE IF NOT EXISTS admin_meta (
                    key TEXT PRIMARY KEY, value TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS accounts (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    account TEXT NOT NULL COLLATE NOCASE UNIQUE,
                    password TEXT NOT NULL,
                    access_token TEXT NOT NULL,
                    nickname TEXT NOT NULL DEFAULT '',
                    account_id INTEGER,
                    enabled INTEGER NOT NULL DEFAULT 1,
                    auth_failed INTEGER NOT NULL DEFAULT 0,
                    cooldown_until REAL NOT NULL DEFAULT 0,
                    created_at REAL NOT NULL,
                    token_updated_at REAL NOT NULL,
                    last_success_at REAL,
                    last_failure_at REAL,
                    last_checked_at REAL,
                    last_error TEXT,
                    last_error_code INTEGER,
                    success_count INTEGER NOT NULL DEFAULT 0,
                    failure_count INTEGER NOT NULL DEFAULT 0,
                    total_fetch_ms REAL NOT NULL DEFAULT 0
                );
                CREATE TABLE IF NOT EXISTS auto_register_state (
                    id INTEGER PRIMARY KEY CHECK (id = 1),
                    enabled INTEGER NOT NULL DEFAULT 1,
                    last_run_at REAL,
                    next_run_at REAL,
                    last_status TEXT,
                    last_error TEXT,
                    last_account_masked TEXT,
                    success_count INTEGER NOT NULL DEFAULT 0,
                    failure_count INTEGER NOT NULL DEFAULT 0,
                    locked_until REAL NOT NULL DEFAULT 0,
                    locked_by TEXT
                );
                CREATE TABLE IF NOT EXISTS auto_register_logs (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    executed_at REAL NOT NULL,
                    status TEXT NOT NULL,
                    account_masked TEXT NOT NULL DEFAULT '',
                    duration_ms REAL NOT NULL DEFAULT 0,
                    error_message TEXT,
                    details TEXT
                );
            """)
            db.execute("INSERT OR IGNORE INTO auto_register_state (id, enabled, next_run_at) VALUES (1, 1, ?)", (time.time() + 3600,))
        try:
            os.chmod(self.path, 0o600)
        except OSError:
            pass

    @contextmanager
    def _connect(self):
        started = time.perf_counter()
        db = sqlite3.connect(str(self.path), timeout=10)
        try:
            db.row_factory = sqlite3.Row
            db.execute("PRAGMA busy_timeout=10000")
            yield db
            db.commit()
        except BaseException:
            db.rollback()
            raise
        finally:
            db.close()
            elapsed_ms = (time.perf_counter() - started) * 1000
            if elapsed_ms >= 150:
                # Do not log database path, SQL statements or account data.
                log_warning(事件="账号数据库慢操作", 耗时毫秒=round(elapsed_ms, 1))

    def _db_signature(self) -> tuple[int, int, int, int] | None:
        try:
            st = self.path.stat()
            wal_path = self.path.with_name(self.path.name + "-wal")
            if wal_path.exists():
                wst = wal_path.stat()
                return (st.st_mtime_ns, st.st_size, wst.st_mtime_ns, wst.st_size)
            return (st.st_mtime_ns, st.st_size, 0, 0)
        except OSError:
            return None

    def _invalidate_cache(self) -> None:
        self._accounts_cache = None

    def all(self) -> list[dict]:
        with self.lock:
            sig = self._db_signature()
            if self._accounts_cache is not None and sig is not None and self._accounts_cache[0] == sig:
                return [dict(row) for row in self._accounts_cache[1]]
            with self._connect() as db:
                rows = [dict(row) for row in db.execute("SELECT * FROM accounts ORDER BY id")]
            new_sig = self._db_signature()
            if new_sig is not None:
                self._accounts_cache = (new_sig, [dict(row) for row in rows])
            return rows

    def count(self) -> int:
        with self.lock:
            sig = self._db_signature()
            if self._accounts_cache is not None and sig is not None and self._accounts_cache[0] == sig:
                return len(self._accounts_cache[1])
            with self._connect() as db:
                row = db.execute("SELECT COUNT(*) FROM accounts").fetchone()
                return int(row[0]) if row else 0

    def get(self, account_pk: int) -> dict | None:
        with self.lock:
            sig = self._db_signature()
            if self._accounts_cache is not None and sig is not None and self._accounts_cache[0] == sig:
                for row in self._accounts_cache[1]:
                    if row.get("id") == account_pk:
                        return dict(row)
                return None
            with self._connect() as db:
                row = db.execute("SELECT * FROM accounts WHERE id=?", (account_pk,)).fetchone()
                return dict(row) if row else None

    @staticmethod
    def _required(item: dict):
        account = str(item.get("account") or "").strip()
        password = item.get("password")
        token = str(item.get("access_token") or "").strip()
        if not account or not isinstance(password, str) or not password.strip() or not token:
            raise ValueError("账号、密码和 Token 三项均为必填")
        if max(len(account), len(password), len(token)) > 4096:
            raise ValueError("账号信息过长")
        return account, password, token

    def add(self, item: dict) -> int:
        account, password, token = self._required(item)
        nickname = str(item.get("nickname") or "").strip()[:100]
        account_id = item.get("account_id")
        with self.lock, self._connect() as db:
            try:
                cur = db.execute(
                    """INSERT INTO accounts(account,password,access_token,nickname,account_id,created_at,token_updated_at)
                       VALUES (?,?,?,?,?,?,?)""",
                    (account, password, token, nickname, account_id, time.time(), time.time()),
                )
                db.commit()
                self._invalidate_cache()
                return cur.lastrowid
            except sqlite3.IntegrityError as exc:
                raise ValueError("账号已存在") from exc

    def has_account(self, account: str, token: str | None = None) -> bool:
        acc_norm = str(account or "").strip().lower()
        tok_norm = str(token or "").strip()
        with self.lock, self._connect() as db:
            if acc_norm and tok_norm:
                row = db.execute("SELECT id FROM accounts WHERE LOWER(account)=? OR access_token=?", (acc_norm, tok_norm)).fetchone()
            elif acc_norm:
                row = db.execute("SELECT id FROM accounts WHERE LOWER(account)=?", (acc_norm,)).fetchone()
            elif tok_norm:
                row = db.execute("SELECT id FROM accounts WHERE access_token=?", (tok_norm,)).fetchone()
            else:
                return False
            return bool(row)

    def get_auto_register_state(self) -> dict:
        with self.lock, self._connect() as db:
            row = db.execute("SELECT * FROM auto_register_state WHERE id=1").fetchone()
            if not row:
                now = time.time()
                db.execute("INSERT OR IGNORE INTO auto_register_state (id, enabled, next_run_at) VALUES (1, 1, ?)", (now + 3600,))
                db.commit()
                row = db.execute("SELECT * FROM auto_register_state WHERE id=1").fetchone()
            return dict(row)

    def update_auto_register_state(self, **kwargs):
        permitted = {
            "enabled", "last_run_at", "next_run_at", "last_status",
            "last_error", "last_account_masked", "success_count",
            "failure_count", "locked_until", "locked_by",
        }
        changes = {k: v for k, v in kwargs.items() if k in permitted}
        if not changes:
            return
        columns = ", ".join(f"{name}=?" for name in changes)
        with self.lock, self._connect() as db:
            db.execute(f"UPDATE auto_register_state SET {columns} WHERE id=1", (*changes.values(),))
            db.commit()

    def acquire_auto_register_lock(self, instance_id: str, timeout_sec: float = 300) -> bool:
        now = time.time()
        with self.lock, self._connect() as db:
            cur = db.execute(
                """UPDATE auto_register_state
                   SET locked_until=?, locked_by=?
                   WHERE id=1 AND locked_until < ?""",
                (now + timeout_sec, instance_id, now),
            )
            db.commit()
            return cur.rowcount > 0

    def renew_auto_register_lock(self, instance_id: str, timeout_sec: float = 300) -> bool:
        """Extend an unexpired lease only if this instance still owns it."""
        now = time.time()
        with self.lock, self._connect() as db:
            cur = db.execute(
                """UPDATE auto_register_state SET locked_until=?
                   WHERE id=1 AND locked_by=? AND locked_until>=?""",
                (now + timeout_sec, instance_id, now),
            )
            return cur.rowcount == 1

    def release_auto_register_lock(self, instance_id: str):
        with self.lock, self._connect() as db:
            db.execute(
                """UPDATE auto_register_state
                   SET locked_until=0, locked_by=NULL
                   WHERE id=1 AND locked_by=?""",
                (instance_id,),
            )
            db.commit()

    def add_auto_register_log(
        self,
        status: str,
        account_masked: str,
        duration_ms: float,
        error_message: str | None = None,
        details: str | None = None,
    ):
        with self.lock, self._connect() as db:
            db.execute(
                """INSERT INTO auto_register_logs(executed_at, status, account_masked, duration_ms, error_message, details)
                   VALUES (?, ?, ?, ?, ?, ?)""",
                (time.time(), status, account_masked, duration_ms, (error_message or "")[:256], (details or "")[:1024]),
            )
            db.execute("DELETE FROM auto_register_logs WHERE id NOT IN (SELECT id FROM auto_register_logs ORDER BY id DESC LIMIT 200)")
            db.commit()

    def get_auto_register_logs(self, limit: int = 50) -> list[dict]:
        with self.lock, self._connect() as db:
            rows = db.execute("SELECT * FROM auto_register_logs ORDER BY id DESC LIMIT ?", (limit,)).fetchall()
            return [dict(r) for r in rows]

    def bulk_add(self, items: list[dict]) -> dict:
        valid = [(*self._required(item), str(item.get("nickname") or "").strip()[:100]) for item in items]
        added = 0
        duplicated = 0
        with self.lock, self._connect() as db:
            for account, password, token, nickname in valid:
                cur = db.execute(
                    """INSERT OR IGNORE INTO accounts(account,password,access_token,nickname,created_at,token_updated_at)
                       VALUES (?,?,?,?,?,?)""",
                    (account, password, token, nickname, time.time(), time.time()),
                )
                added += cur.rowcount
                duplicated += not cur.rowcount
            db.commit()
            self._invalidate_cache()
        return {"added": added, "duplicates": duplicated}

    def edit(self, account_pk: int, values: dict):
        permitted = {"account", "password", "access_token", "nickname", "enabled"}
        changes = {k: v for k, v in values.items() if k in permitted and v is not None}
        if not changes:
            return
        current = self.get(account_pk)
        if not current:
            raise KeyError("账号不存在")
        combined = dict(current)
        combined.update(changes)
        self._required(combined)
        if "account" in changes:
            changes["account"] = combined["account"].strip()
        if "access_token" in changes:
            changes["access_token"] = combined["access_token"].strip()
            changes["token_updated_at"] = time.time()
        if "enabled" in changes:
            changes["enabled"] = int(bool(changes["enabled"]))
        if "nickname" in changes:
            changes["nickname"] = str(changes["nickname"])[:100]
        if {"account", "password", "access_token"} & changes.keys():
            changes["auth_failed"] = 0
            changes["account_id"] = None
        columns = ", ".join(f"{name}=?" for name in changes)
        with self.lock, self._connect() as db:
            try:
                db.execute(f"UPDATE accounts SET {columns} WHERE id=?", (*changes.values(), account_pk))
                db.commit()
                self._invalidate_cache()
            except sqlite3.IntegrityError as exc:
                raise ValueError("账号已存在") from exc

    def delete(self, account_pk: int):
        with self.lock, self._connect() as db:
            cur = db.execute("DELETE FROM accounts WHERE id=?", (account_pk,))
            db.commit()
            self._invalidate_cache()
            if not cur.rowcount:
                raise KeyError("账号不存在")

    def record_login(self, account_pk: int, client: MajsoulClient, *, refreshed: bool):
        fields = ["account_id=?", "nickname=?", "auth_failed=0", "last_error=NULL", "last_error_code=NULL"]
        args = [client.account_id, client.nickname or ""]
        if refreshed and client.access_token:
            fields.extend(["access_token=?", "token_updated_at=?"])
            args.extend([client.access_token, time.time()])
        with self.lock, self._connect() as db:
            db.execute(f"UPDATE accounts SET {', '.join(fields)} WHERE id=?", (*args, account_pk))
            db.commit()
            self._invalidate_cache()

    def record_result(self, account_pk: int, *, success: bool, elapsed_ms: float,
                      error: str | None = None, code: int | None = None, auth_failed: bool = False):
        with self.lock, self._connect() as db:
            if success:
                db.execute("""UPDATE accounts SET success_count=success_count+1,
                           total_fetch_ms=total_fetch_ms+?,last_success_at=?,last_error=NULL,last_error_code=NULL
                           WHERE id=?""", (elapsed_ms, time.time(), account_pk))
            else:
                db.execute("""UPDATE accounts SET failure_count=failure_count+1,
                           last_failure_at=?,last_error=?,last_error_code=?,auth_failed=?,
                           cooldown_until=CASE WHEN ?=540 THEN ? ELSE cooldown_until END
                           WHERE id=?""",
                           (time.time(), (error or "")[:160], code, int(auth_failed),
                            code, time.time() + 86400, account_pk))
            db.commit()
            self._invalidate_cache()

    def checked(self, account_pk: int):
        with self.lock, self._connect() as db:
            db.execute("UPDATE accounts SET last_checked_at=? WHERE id=?", (time.time(), account_pk))
            db.commit()
            self._invalidate_cache()

    def bootstrap(self, legacy_accounts: list[dict]):
        """Import complete legacy triples once, never reintroduce deleted accounts."""
        with self.lock, self._connect() as db:
            if db.execute("SELECT value FROM admin_meta WHERE key='initialized'").fetchone():
                return
            for item in legacy_accounts:
                try:
                    account, password, token = self._required(item)
                except ValueError:
                    continue
                db.execute("""INSERT OR IGNORE INTO accounts
                    (account,password,access_token,nickname,account_id,created_at,token_updated_at)
                    VALUES (?,?,?,?,?,?,?)""",
                    (account, password, token, str(item.get("nickname") or "")[:100],
                     item.get("account_id"), time.time(), time.time()))
            db.execute("INSERT INTO admin_meta(key,value) VALUES('initialized','1')")
            db.commit()
            self._invalidate_cache()


def _public_account(acc: dict, busy: bool) -> dict:
    now = time.time()
    if not acc["enabled"]:
        status = "已禁用"
    elif busy:
        status = "正在使用"
    elif acc["cooldown_until"] > now:
        status = "540冷却"
    elif acc["auth_failed"]:
        status = "认证异常"
    else:
        status = "空闲"
    return {
        "id": acc["id"], "account": acc["account"], "nickname": acc["nickname"],
        "account_id": acc["account_id"], "enabled": bool(acc["enabled"]), "status": status,
        "has_token": bool(acc["access_token"]), "token_updated_at": acc["token_updated_at"],
        "cooldown_until": acc["cooldown_until"], "last_success_at": acc["last_success_at"],
        "last_failure_at": acc["last_failure_at"], "last_checked_at": acc["last_checked_at"],
        "last_error": acc["last_error"], "last_error_code": acc["last_error_code"],
        "success_count": acc["success_count"], "failure_count": acc["failure_count"],
        "avg_fetch_ms": round(acc["total_fetch_ms"] / acc["success_count"], 1) if acc["success_count"] else None,
    }


class AccountScheduler:
    """Random choice among actually idle accounts, with one task per account."""

    def __init__(self, store: AccountStore, cache_dir: Path):
        self.store = store
        self.cache_dir = cache_dir
        self.clients: dict[int, MajsoulClient] = {}
        self.busy: set[int] = set()
        self.invalidated: set[int] = set()
        self.condition = asyncio.Condition()
        self.waiting = 0
        self.periodic_task: asyncio.Task | None = None

    def overview(self) -> dict:
        accounts = self.store.all()
        busy_snapshot = set(self.busy)
        public = [_public_account(acc, acc["id"] in busy_snapshot) for acc in accounts]
        res = {
            "accounts": public,
            "total": len(public),
            "idle": sum(a["status"] == "空闲" for a in public),
            "busy": len(busy_snapshot),
            "cooldown": sum(a["status"] == "540冷却" for a in public),
            "waiting": self.waiting,
        }
        if _auto_register_scheduler:
            res["auto_register"] = _auto_register_scheduler.overview(total_accounts=len(public))
        return res

    async def reserve(self, attempted: set[int]):
        async with self.condition:
            while True:
                accounts = await asyncio.to_thread(self.store.all)
                now = time.time()
                possible = [
                    acc for acc in accounts if acc["id"] not in attempted and acc["enabled"]
                    and not acc["auth_failed"] and acc["cooldown_until"] <= now
                ]
                idle = [acc for acc in possible if acc["id"] not in self.busy]
                if idle:
                    selected = random.choice(idle)
                    self.busy.add(selected["id"])
                    return selected
                if not possible:
                    return None
                self.waiting += 1
                try:
                    await self.condition.wait()
                finally:
                    self.waiting -= 1

    async def release(self, account_pk: int):
        # Keep the slot reserved until the obsolete client closes, but never
        # strand it if close() fails or the request is cancelled.
        try:
            row = await asyncio.to_thread(self.store.get, account_pk)
            if row is None or not row["enabled"] or account_pk in self.invalidated:
                self.invalidated.discard(account_pk)
                client = self.clients.pop(account_pk, None)
                if client:
                    await client.close()
        finally:
            async with self.condition:
                self.busy.discard(account_pk)
                self.condition.notify_all()

    async def changed(self, account_pk: int | None = None):
        async with self.condition:
            if account_pk is not None and account_pk in self.busy:
                self.invalidated.add(account_pk)
            self.condition.notify_all()
        if account_pk is not None and account_pk not in self.busy:
            client = self.clients.pop(account_pk, None)
            if client:
                await client.close()

    async def _authenticate(self, acc: dict, client: MajsoulClient):
        if client.logged_in and client._ws is not None and not getattr(client._ws, "closed", False):
            if client.current_account == acc["account"] and (
                not acc["account_id"] or str(client.account_id) == str(acc["account_id"])
            ):
                return
        await client.close()
        set_value("雀魂连接结果", "进行中")
        try:
            await client.connect()
        except Exception:
            set_value("雀魂连接结果", "失败")
            raise
        else:
            set_value("雀魂连接结果", "成功")
        refreshed = False
        try:
            with measure("雀魂登录", failure_count="雀魂登录失败次数"):
                await client.login_token(acc["access_token"])
        except MajsoulTokenInvalid:
            with measure("雀魂登录", failure_count="雀魂登录失败次数"):
                await client.login_password(acc["account"], acc["password"])
            refreshed = True
        if acc["account_id"] and str(acc["account_id"]) != str(client.account_id):
            await client.close()
            raise MajsoulError("Token 与账号身份不一致")
        client.current_account = acc["account"]
        await asyncio.to_thread(self.store.record_login, acc["id"], client, refreshed=refreshed)

    @staticmethod
    def _network_failure(error: Exception) -> bool:
        return isinstance(error, (TimeoutError, ConnectionError, OSError, httpx.TimeoutException,
                                  httpx.NetworkError, ConnectionClosed)) or any(
            word in str(error).lower() for word in ("timeout", "timed out", "连接", "网络", "超时")
        )

    async def _fetch_one(self, acc: dict, game_uuid: str):
        client = self.clients.setdefault(acc["id"], MajsoulClient(self.cache_dir))
        start = time.monotonic()
        try:
            await self._authenticate(acc, client)
            try:
                record = await client.fetch_record(game_uuid)
            except (TimeoutError, httpx.TimeoutException):
                # A stalled RPC should switch gateways with the SAME account first.
                client.mark_current_gateway_unhealthy()
                await client.close()
                await self._authenticate(acc, client)
                record = await client.fetch_record(game_uuid)
            await asyncio.to_thread(self.store.record_result, acc["id"], success=True,
                                    elapsed_ms=(time.monotonic() - start) * 1000)
            return record
        except Exception as exc:
            code = getattr(exc, "code", None)
            is_auth = isinstance(exc, MajsoulTokenInvalid) or code in (103, 104, 109, 1003, 1004)
            safe_error = "网络失败" if self._network_failure(exc) else _redact_error(
                exc, acc.get("password"), acc.get("access_token"), getattr(client, "access_token", None)
            )
            await asyncio.to_thread(self.store.record_result, acc["id"], success=False,
                                    elapsed_ms=(time.monotonic() - start) * 1000,
                                    error=safe_error,
                                    code=code, auth_failed=is_auth)
            if self._network_failure(exc) or is_auth:
                await client.close()
            raise

    async def fetch_record(self, game_uuid: str):
        from fastapi import HTTPException
        attempted: set[int] = set()
        errors: list[Exception] = []
        try:
            async with asyncio.timeout(60):
                while True:
                    acc = await self.reserve(attempted)
                    if acc is None:
                        break
                    try:
                        return await self._fetch_one(acc, game_uuid)
                    except Exception as exc:
                        attempted.add(acc["id"])
                        errors.append(exc)
                        if getattr(exc, "code", None) == 1203:
                            set_value("最近雀魂失败类型", "牌谱不存在或无权访问")
                            raise HTTPException(status_code=400, detail="牌谱不存在或无权查看（1203）") from exc
                    finally:
                        await self.release(acc["id"])
        except TimeoutError as exc:
            set_value("最近雀魂失败类型", "网络失败")
            raise HTTPException(status_code=503, detail="雀魂牌谱下载累计等待超过60秒") from exc
        if errors and all(getattr(exc, "code", None) == 540 for exc in errors):
            set_value("最近雀魂失败类型", "抓取限流")
            raise HTTPException(status_code=429, detail="当前账号池抓谱额度已满，请稍后重试")
        if errors:
            if all(isinstance(exc, MajsoulTokenInvalid) or getattr(exc, "code", None) in (103, 104, 109, 1003, 1004) for exc in errors):
                set_value("最近雀魂失败类型", "登录失效")
            elif any(self._network_failure(exc) for exc in errors):
                set_value("最近雀魂失败类型", "网络失败")
            else:
                set_value("最近雀魂失败类型", "雀魂接口失败")
            raise HTTPException(status_code=503, detail="雀魂账号或网络暂不可用，请稍后重试")
        enabled = [acc for acc in await asyncio.to_thread(self.store.all) if acc["enabled"]]
        if enabled and all(acc["cooldown_until"] > time.time() for acc in enabled):
            set_value("最近雀魂失败类型", "抓取限流")
            raise HTTPException(status_code=429, detail="所有启用账号均处于540冷却期")
        set_value("最近雀魂失败类型", "未配置账号" if not enabled else "登录失败")
        raise HTTPException(status_code=503, detail="当前没有空闲且可用的雀魂账号")

    async def _check_idle_accounts(self):
        for acc in await asyncio.to_thread(self.store.all):
            if not acc["enabled"] or acc["auth_failed"] or acc["cooldown_until"] > time.time() or acc["id"] in self.busy:
                continue
            # Reserve the same slot used by regular download jobs; no races.
            async with self.condition:
                if acc["id"] in self.busy:
                    continue
                self.busy.add(acc["id"])
            try:
                client = self.clients.setdefault(acc["id"], MajsoulClient(self.cache_dir))
                try:
                    await asyncio.wait_for(self._authenticate(acc, client), timeout=15)
                except Exception as exc:
                    if not self._network_failure(exc):
                        await asyncio.to_thread(self.store.record_result, acc["id"], success=False,
                            elapsed_ms=0, error="账号检测失败", code=getattr(exc, "code", None),
                            auth_failed=isinstance(exc, MajsoulTokenInvalid) or getattr(exc, "code", None) in (103, 104, 109, 1003, 1004))
                finally:
                    await asyncio.to_thread(self.store.checked, acc["id"])
            finally:
                await self.release(acc["id"])

    async def periodic(self):
        while True:
            await asyncio.sleep(3600)
            try:
                await self._check_idle_accounts()
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                log_warning(事件="账号定时检测", 状态="失败", 错误原因=type(exc).__name__)

    async def shutdown(self):
        if self.periodic_task:
            self.periodic_task.cancel()
            await asyncio.gather(self.periodic_task, return_exceptions=True)
        for client in self.clients.values():
            await client.close()


_manager: AccountScheduler | None = None
_auto_register_scheduler: Any | None = None


def manager() -> AccountScheduler | None:
    return _manager


def auto_register_scheduler() -> Any | None:
    return _auto_register_scheduler


async def start_manager(cache_dir: Path, legacy_accounts: list[dict]) -> AccountScheduler:
    global _manager, _auto_register_scheduler
    from .account_register import AutoRegisterScheduler
    path = _db_path()
    try:
        store = await asyncio.to_thread(AccountStore, path)
    except (sqlite3.Error, OSError):
        if os.environ.get("MAJSOUL_ADMIN_DB_PATH"):
            raise
        # Some ModelScope persistent FUSE mounts lack SQLite locking semantics.
        # Prefer a usable local database over preventing the service from starting.
        path = Path(__file__).resolve().parents[1] / "data" / "admin_accounts.sqlite3"
        log_warning(事件="账号数据库", 状态="已降级", 信息="持久化目录不可用，已使用本地数据库")
        store = await asyncio.to_thread(AccountStore, path)
    await asyncio.to_thread(store.bootstrap, legacy_accounts)
    _manager = AccountScheduler(store, cache_dir)
    _manager.periodic_task = asyncio.create_task(_manager.periodic())
    _auto_register_scheduler = AutoRegisterScheduler(store, cache_dir, _manager)
    _auto_register_scheduler.start()
    return _manager


async def stop_manager():
    global _manager, _auto_register_scheduler
    current_reg, _auto_register_scheduler = _auto_register_scheduler, None
    if current_reg:
        await current_reg.stop()
    current, _manager = _manager, None
    if current:
        await current.shutdown()


def _get_auto_register_scheduler():
    current = auto_register_scheduler()
    if current is None:
        raise HTTPException(status_code=503, detail="自动注册调度器尚未初始化")
    return current


def _get_manager() -> AccountScheduler:
    current = manager()
    if current is None:
        raise HTTPException(status_code=503, detail="管理员账号池尚未初始化")
    return current


# Keep the public import surface stable for existing callers.
from .admin_routes import (  # noqa: E402
    AdminAccount, AdminBulk, AdminEdit, AdminRegisterToggle,
    admin_router, _admin_key, _authorize,
    admin_login, admin_logout, admin_status, admin_accounts,
    admin_add_account, admin_import_accounts, admin_edit_account,
    admin_delete_account, admin_register_status, admin_register_toggle,
    admin_register_trigger, admin_register_history,
)
