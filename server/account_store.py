"""SQLite account storage, registration lease and transaction primitives."""
from __future__ import annotations

from contextlib import contextmanager
import os
import sqlite3
import threading
import time
from pathlib import Path

from .logger import log_warning
from .majsoul.client import MajsoulClient


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


