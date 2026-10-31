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

from .logger import log_warning
from .majsoul.client import MajsoulClient, MajsoulError, MajsoulTokenInvalid


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
        with self._connect() as db:
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
            """)
        try:
            os.chmod(self.path, 0o600)
        except OSError:
            pass

    @contextmanager
    def _connect(self):
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

    def all(self) -> list[dict]:
        with self.lock, self._connect() as db:
            return [dict(row) for row in db.execute("SELECT * FROM accounts ORDER BY id")]

    def get(self, account_pk: int) -> dict | None:
        with self.lock, self._connect() as db:
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
        with self.lock, self._connect() as db:
            try:
                cur = db.execute(
                    """INSERT INTO accounts(account,password,access_token,nickname,created_at,token_updated_at)
                       VALUES (?,?,?,?,?,?)""",
                    (account, password, token, str(item.get("nickname") or "").strip()[:100], time.time(), time.time()),
                )
                db.commit()
                return cur.lastrowid
            except sqlite3.IntegrityError as exc:
                raise ValueError("账号已存在") from exc

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
            except sqlite3.IntegrityError as exc:
                raise ValueError("账号已存在") from exc

    def delete(self, account_pk: int):
        with self.lock, self._connect() as db:
            cur = db.execute("DELETE FROM accounts WHERE id=?", (account_pk,))
            db.commit()
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

    def checked(self, account_pk: int):
        with self.lock, self._connect() as db:
            db.execute("UPDATE accounts SET last_checked_at=? WHERE id=?", (time.time(), account_pk))
            db.commit()

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
        public = [_public_account(acc, acc["id"] in self.busy) for acc in accounts]
        return {
            "accounts": public,
            "total": len(public),
            "idle": sum(a["status"] == "空闲" for a in public),
            "busy": len(self.busy),
            "cooldown": sum(a["status"] == "540冷却" for a in public),
            "waiting": self.waiting,
        }

    async def reserve(self, attempted: set[int]):
        async with self.condition:
            while True:
                accounts = self.store.all()
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
            row = self.store.get(account_pk)
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
        await client.connect()
        refreshed = False
        try:
            await client.login_token(acc["access_token"])
        except MajsoulTokenInvalid:
            await client.login_password(acc["account"], acc["password"])
            refreshed = True
        if acc["account_id"] and str(acc["account_id"]) != str(client.account_id):
            await client.close()
            raise MajsoulError("Token 与账号身份不一致")
        client.current_account = acc["account"]
        self.store.record_login(acc["id"], client, refreshed=refreshed)

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
            except TimeoutError:
                # A stalled RPC should switch gateways with the SAME account first.
                client.mark_current_gateway_unhealthy()
                await client.close()
                await self._authenticate(acc, client)
                record = await client.fetch_record(game_uuid)
            self.store.record_result(acc["id"], success=True, elapsed_ms=(time.monotonic() - start) * 1000)
            return record
        except Exception as exc:
            code = getattr(exc, "code", None)
            is_auth = isinstance(exc, MajsoulTokenInvalid) or code in (103, 104, 109, 1003, 1004)
            self.store.record_result(acc["id"], success=False,
                elapsed_ms=(time.monotonic() - start) * 1000,
                error="网络失败" if self._network_failure(exc) else str(exc),
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
                            raise HTTPException(status_code=400, detail="牌谱不存在或无权查看（1203）") from exc
                    finally:
                        await self.release(acc["id"])
        except TimeoutError as exc:
            raise HTTPException(status_code=503, detail="雀魂牌谱下载累计等待超过60秒") from exc
        if errors and all(getattr(exc, "code", None) == 540 for exc in errors):
            raise HTTPException(status_code=429, detail="当前账号池抓谱额度已满，请稍后重试")
        if errors:
            raise HTTPException(status_code=503, detail="雀魂账号或网络暂不可用，请稍后重试")
        enabled = [acc for acc in self.store.all() if acc["enabled"]]
        if enabled and all(acc["cooldown_until"] > time.time() for acc in enabled):
            raise HTTPException(status_code=429, detail="所有启用账号均处于540冷却期")
        raise HTTPException(status_code=503, detail="当前没有空闲且可用的雀魂账号")

    async def _check_idle_accounts(self):
        for acc in self.store.all():
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
                        self.store.record_result(acc["id"], success=False, elapsed_ms=0,
                            error="账号检测失败", code=getattr(exc, "code", None),
                            auth_failed=isinstance(exc, MajsoulTokenInvalid) or getattr(exc, "code", None) in (103, 104, 109, 1003, 1004))
                finally:
                    self.store.checked(acc["id"])
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


def manager() -> AccountScheduler | None:
    return _manager


async def start_manager(cache_dir: Path, legacy_accounts: list[dict]) -> AccountScheduler:
    global _manager
    path = _db_path()
    try:
        store = AccountStore(path)
    except (sqlite3.Error, OSError):
        if os.environ.get("MAJSOUL_ADMIN_DB_PATH"):
            raise
        # Some ModelScope persistent FUSE mounts lack SQLite locking semantics.
        # Prefer a usable local database over preventing the service from starting.
        path = Path(__file__).resolve().parents[1] / "data" / "admin_accounts.sqlite3"
        log_warning(事件="账号数据库", 状态="已降级", 信息="持久化目录不可用，已使用本地数据库")
        store = AccountStore(path)
    store.bootstrap(legacy_accounts)
    _manager = AccountScheduler(store, cache_dir)
    _manager.periodic_task = asyncio.create_task(_manager.periodic())
    return _manager


async def stop_manager():
    global _manager
    current, _manager = _manager, None
    if current:
        await current.shutdown()


def _get_manager() -> AccountScheduler:
    current = manager()
    if current is None:
        raise HTTPException(status_code=503, detail="管理员账号池尚未初始化")
    return current


class AdminAccount(BaseModel):
    account: str
    password: str
    access_token: str
    nickname: str = ""


class AdminBulk(BaseModel):
    accounts: list[AdminAccount] = Field(min_length=1, max_length=500)


class AdminEdit(BaseModel):
    account: str | None = None
    password: str | None = None
    access_token: str | None = None
    nickname: str | None = None
    enabled: bool | None = None


admin_router = APIRouter(prefix="/api/admin", tags=["管理员账号池"])


def _admin_key() -> str:
    return os.environ.get("MAJSOUL_ADMIN_KEY", "").strip()


def _authorize(request: Request):
    key = _admin_key()
    if not key:
        raise HTTPException(status_code=503, detail="未配置管理员密钥")
    supplied = request.headers.get("x-admin-key", "")
    if not supplied or not hmac.compare_digest(supplied.encode("utf-8"), key.encode("utf-8")):
        raise HTTPException(status_code=401, detail="管理员密钥错误")


@admin_router.post("/login")
async def admin_login(request: Request):
    _authorize(request)
    return {"ok": True}


@admin_router.post("/logout")
async def admin_logout(request: Request):
    _authorize(request)
    return {"ok": True}


@admin_router.get("/status")
async def admin_status(request: Request):
    _authorize(request)
    return _get_manager().overview()


@admin_router.get("/accounts")
async def admin_accounts(request: Request):
    _authorize(request)
    return _get_manager().overview()


@admin_router.post("/accounts")
async def admin_add_account(request: Request, data: AdminAccount):
    _authorize(request)
    current = _get_manager()
    try:
        pk = current.store.add(data.model_dump())
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    await current.changed()
    return {"ok": True, "id": pk}


@admin_router.post("/accounts/import")
async def admin_import_accounts(request: Request, data: AdminBulk):
    _authorize(request)
    current = _get_manager()
    try:
        result = current.store.bulk_add([item.model_dump() for item in data.accounts])
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    await current.changed()
    return result


@admin_router.patch("/accounts/{account_pk}")
async def admin_edit_account(account_pk: int, request: Request, data: AdminEdit):
    _authorize(request)
    current = _get_manager()
    try:
        current.store.edit(account_pk, data.model_dump(exclude_unset=True))
    except (ValueError, KeyError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    await current.changed(account_pk)
    return {"ok": True}


@admin_router.delete("/accounts/{account_pk}")
async def admin_delete_account(account_pk: int, request: Request):
    _authorize(request)
    current = _get_manager()
    try:
        current.store.delete(account_pk)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    await current.changed(account_pk)
    return {"ok": True}
