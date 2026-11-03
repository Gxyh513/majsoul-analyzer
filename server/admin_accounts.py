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


# Stable import surface: public callers still access the two types here.
from .account_store import AccountStore, _db_path
from .account_scheduler import AccountScheduler, _public_account


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
