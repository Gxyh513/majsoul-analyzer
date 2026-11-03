"""雀魂账号自动注册与账号池自动补充服务。

对接私有注册仓库 (Gxyh513/majsoul-register)，提供：
1. 每小时定时自动注册（最多1个）与账号池补充
2. 任务锁与多实例防重
3. 凭据完整性与网关登录有效性校验
4. 账号去重与安全持久化
5. 失败熔断与平台限制保护（遇到验证码/限流自动暂停）
6. 服务重启后按计划恢复任务状态
7. 管理员手动触发与启停控制
"""
from __future__ import annotations

import asyncio
import io
import json
import logging
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
import uuid
import zipfile
from pathlib import Path
from typing import TYPE_CHECKING, Any

import httpx

from .account_pool import mask_account
from .logger import log_error, log_info, log_warning
from .majsoul.client import MajsoulClient, MajsoulError, MajsoulTokenInvalid

if TYPE_CHECKING:
    from .admin_accounts import AccountScheduler, AccountStore

log = logging.getLogger(__name__)

# 默认一小时执行一次
DEFAULT_REGISTER_INTERVAL_SECONDS = 3600
# 注册任务执行超时时间（秒）
REGISTER_TASK_TIMEOUT_SECONDS = 150
# 锁超时时间（秒），防止单实例异常崩溃后死锁
LOCK_EXPIRY_SECONDS = 300
LOCK_RENEW_SECONDS = 60
# Detect externally changed schedules and retry unexpected scheduler failures.
SCHEDULER_POLL_SECONDS = 60
SCHEDULER_ERROR_RETRY_SECONDS = 60
MAX_ZIPBALL_UNCOMPRESSED_BYTES = 32 * 1024 * 1024


# Compatibility imports for callers of server.account_register.
from .register_repository import (
    MAX_ZIPBALL_UNCOMPRESSED_BYTES, redact_secrets, resolve_project_root,
    get_register_dir, _safe_extract_repo_zip, _git_clone_into_dir,
    _download_repo_zipball, ensure_private_repo,
)
from .register_runner import RegisterRunner


from .register_ingest import (
    DEFAULT_REGISTER_INTERVAL_SECONDS, verify_majsoul_credentials,
    is_platform_restriction_error, register_and_ingest,
)


class AutoRegisterScheduler:
    """Persistent, wakeable registration scheduler; one attempt per due interval."""

    def __init__(
        self,
        store: AccountStore,
        cache_dir: Path,
        scheduler: AccountScheduler | None = None,
        runner: RegisterRunner | None = None,
    ):
        self.store = store
        self.cache_dir = cache_dir
        self.scheduler = scheduler
        self.runner = runner or RegisterRunner(cache_dir=cache_dir)
        self.instance_id = f"inst-{uuid.uuid4().hex[:8]}"
        self.task: asyncio.Task | None = None
        self._stopped = False
        self._wake = asyncio.Event()

    def start(self):
        """Start or recover the scheduled task without changing its persisted due date."""
        if self.task is None or self.task.done():
            self._stopped = False
            self._wake.clear()
            self.task = asyncio.create_task(self._periodic_loop())

    async def stop(self):
        self._stopped = True
        self._wake.set()
        if self.task:
            self.task.cancel()
            try:
                await self.task
            except asyncio.CancelledError:
                pass
            self.task = None
        await asyncio.to_thread(self.store.release_auto_register_lock, self.instance_id)

    async def _wait_for_wake(self, seconds: float):
        if seconds <= 0:
            return
        try:
            await asyncio.wait_for(self._wake.wait(), timeout=seconds)
        except TimeoutError:
            pass
        finally:
            self._wake.clear()

    async def _register_with_lease(self, *, manual: bool) -> dict[str, Any]:
        """Renew an owned lock while a long-running registration is in progress."""
        async def keep_alive():
            while True:
                await asyncio.sleep(LOCK_RENEW_SECONDS)
                try:
                    renewed = await asyncio.to_thread(
                        self.store.renew_auto_register_lock, self.instance_id, LOCK_EXPIRY_SECONDS
                    )
                except Exception as exc:
                    log_warning(事件="注册锁续租异常", 错误原因=type(exc).__name__)
                    continue
                if not renewed:
                    log_error(事件="注册锁续租失败", 状态="锁已不属于当前任务")
                    return

        heartbeat = asyncio.create_task(keep_alive())
        try:
            return await register_and_ingest(
                store=self.store, cache_dir=self.cache_dir,
                scheduler=self.scheduler, manual=manual, runner=self.runner,
            )
        finally:
            heartbeat.cancel()
            await asyncio.gather(heartbeat, return_exceptions=True)

    async def _periodic_loop(self):
        """Use the DB schedule as truth, wake on toggle, and survive recoverable errors."""
        while not self._stopped:
            try:
                state = await asyncio.to_thread(self.store.get_auto_register_state)
                if not state.get("enabled"):
                    await self._wait_for_wake(SCHEDULER_POLL_SECONDS)
                    continue

                now = time.time()
                last_run = float(state.get("last_run_at") or 0)
                due = state.get("next_run_at")
                if due is None:
                    # Fresh databases already schedule the first run one hour later.
                    due = max(now, last_run + DEFAULT_REGISTER_INTERVAL_SECONDS) if last_run else now + DEFAULT_REGISTER_INTERVAL_SECONDS
                    await asyncio.to_thread(self.store.update_auto_register_state, next_run_at=due)
                due = max(float(due), last_run + DEFAULT_REGISTER_INTERVAL_SECONDS if last_run else 0)

                if due > now:
                    await self._wait_for_wake(min(due - now, SCHEDULER_POLL_SECONDS))
                    continue

                if not await asyncio.to_thread(
                    self.store.acquire_auto_register_lock, self.instance_id, timeout_sec=LOCK_EXPIRY_SECONDS
                ):
                    # Another process may be registering. Re-read its new due date.
                    await self._wait_for_wake(min(SCHEDULER_POLL_SECONDS, 30))
                    continue

                try:
                    # Recheck under the shared DB lock; another worker or a manual
                    # trigger might have completed after our initial read.
                    locked_state = await asyncio.to_thread(self.store.get_auto_register_state)
                    now = time.time()
                    last_run = float(locked_state.get("last_run_at") or 0)
                    next_run = locked_state.get("next_run_at")
                    if (not locked_state.get("enabled")
                            or (next_run is not None and float(next_run) > now)
                            or (last_run and now < last_run + DEFAULT_REGISTER_INTERVAL_SECONDS)):
                        continue

                    # Persist the attempt before starting external I/O, preventing
                    # a restart from immediately repeating an interrupted attempt.
                    await asyncio.to_thread(self.store.update_auto_register_state,
                        last_run_at=now,
                        next_run_at=now + DEFAULT_REGISTER_INTERVAL_SECONDS,
                        last_status="运行中",
                    )
                    await self._register_with_lease(manual=False)
                finally:
                    await asyncio.to_thread(self.store.release_auto_register_lock, self.instance_id)

            except asyncio.CancelledError:
                raise
            except Exception as exc:
                # A single DB/network failure must not permanently kill the loop.
                log_error(事件="自动注册调度器异常", 错误原因=type(exc).__name__)
                try:
                    await asyncio.to_thread(self.store.update_auto_register_state,
                        last_status="调度异常",
                        last_error=f"调度器异常: {type(exc).__name__}",
                        next_run_at=time.time() + SCHEDULER_ERROR_RETRY_SECONDS,
                    )
                except Exception:
                    pass
                await self._wait_for_wake(SCHEDULER_ERROR_RETRY_SECONDS)

    async def trigger_manual(self) -> dict[str, Any]:
        """Manually trigger one attempt, preserving the shared rate/instance lock."""
        from fastapi import HTTPException
        now = time.time()
        state = await asyncio.to_thread(self.store.get_auto_register_state)
        if (state.get("locked_until") or 0) > now:
            raise HTTPException(status_code=409, detail="当前已有注册任务在执行中，请稍候")
        last_run = state.get("last_run_at") or 0
        if last_run > 0 and now - last_run < 30:
            raise HTTPException(status_code=429, detail="请勿过于频繁手动触发，距离上次执行不足30秒")
        if not await asyncio.to_thread(
            self.store.acquire_auto_register_lock, self.instance_id, timeout_sec=LOCK_EXPIRY_SECONDS
        ):
            raise HTTPException(status_code=409, detail="无法获取注册任务锁")
        try:
            started = time.time()
            await asyncio.to_thread(self.store.update_auto_register_state,
                last_run_at=started,
                next_run_at=started + DEFAULT_REGISTER_INTERVAL_SECONDS,
                last_status="运行中",
            )
            return await self._register_with_lease(manual=True)
        finally:
            await asyncio.to_thread(self.store.release_auto_register_lock, self.instance_id)
            self._wake.set()

    def _toggle_persisted(self, enabled: bool) -> None:
        """Synchronous state mutation; async routes run it outside the event loop."""
        state = self.store.get_auto_register_state()
        updates = {
            "enabled": int(bool(enabled)),
            "last_status": "空闲" if enabled else "已暂停",
        }
        if enabled:
            now = time.time()
            last_run = float(state.get("last_run_at") or 0)
            updates["next_run_at"] = max(now, last_run + DEFAULT_REGISTER_INTERVAL_SECONDS) if last_run else now
        else:
            updates["next_run_at"] = None
        self.store.update_auto_register_state(**updates)

    def toggle(self, enabled: bool) -> dict[str, Any]:
        """Backwards-compatible synchronous interface for internal callers."""
        self._toggle_persisted(enabled)
        self.start()
        self._wake.set()
        return self.overview()

    async def toggle_async(self, enabled: bool) -> dict[str, Any]:
        """Keep database reads/writes off the ASGI loop, start tasks on that loop."""
        await asyncio.to_thread(self._toggle_persisted, enabled)
        self.start()
        self._wake.set()
        return await asyncio.to_thread(self.overview)

    def overview(self, *, total_accounts: int | None = None) -> dict[str, Any]:
        """Expose both persisted status and whether the in-process timer is alive."""
        state = self.store.get_auto_register_state()
        now = time.time()
        is_running = (state.get("locked_until") or 0) > now
        scheduler_alive = self.task is not None and not self.task.done()
        recent_logs = self.store.get_auto_register_logs(limit=10)

        if total_accounts is None:
            count_fn = getattr(self.store, "count", None)
            total_accounts = count_fn() if callable(count_fn) else len(self.store.all())

        return {
            "scheduler_alive": scheduler_alive,
            "enabled": bool(state.get("enabled", 1)),
            "is_running": is_running,
            "next_run_at": state.get("next_run_at"),
            "last_run_at": state.get("last_run_at"),
            "last_status": "运行中" if is_running else (state.get("last_status") or "未开始"),
            "last_error": state.get("last_error"),
            "last_account": state.get("last_account_masked"),
            "success_count": state.get("success_count", 0),
            "failure_count": state.get("failure_count", 0),
            "current_pool_total": total_accounts,
            "recent_logs": recent_logs,
        }
