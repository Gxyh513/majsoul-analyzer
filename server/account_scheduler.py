"""Asynchronous idle-account selection, authentication and record fetch."""
from __future__ import annotations

import asyncio
import random
import time
from pathlib import Path

import httpx
from websockets.exceptions import ConnectionClosed
from fastapi import HTTPException

from .account_pool import _redact_error
from .account_store import AccountStore
from .logger import log_warning
from .majsoul.client import MajsoulClient, MajsoulError, MajsoulTokenInvalid
from .paipu_performance import measure, set_value


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
        from .admin_accounts import auto_register_scheduler
        registration = auto_register_scheduler()
        if registration:
            res["auto_register"] = registration.overview(total_accounts=len(public))
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
                    # Spread the remote record quota across accounts that have
                    # been idle the longest; randomize ties to avoid fixed bias.
                    def last_attempt(acc: dict) -> float:
                        return max(
                            float(acc.get("last_success_at") or 0),
                            float(acc.get("last_failure_at") or 0),
                        )

                    oldest_attempt = min(last_attempt(acc) for acc in idle)
                    selected = random.choice([acc for acc in idle if last_attempt(acc) == oldest_attempt])
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
        accounts = await asyncio.to_thread(self.store.all)
        # A small fixed bound prevents a large pool from opening many
        # simultaneous login sessions during the hourly health check.
        semaphore = asyncio.Semaphore(3)

        async def check_one(acc: dict):
            account_pk = acc["id"]
            async with semaphore:
                async with self.condition:
                    if account_pk in self.busy:
                        return
                    self.busy.add(account_pk)
                try:
                    current = await asyncio.to_thread(self.store.get, account_pk)
                    if (current is None or not current["enabled"] or current["auth_failed"]
                            or current["cooldown_until"] > time.time()):
                        return

                    client = self.clients.setdefault(account_pk, MajsoulClient(self.cache_dir))
                    try:
                        await asyncio.wait_for(self._authenticate(current, client), timeout=15)
                    except asyncio.CancelledError:
                        try:
                            await client.close()
                        except Exception:
                            pass
                        raise
                    except Exception as exc:
                        if self._network_failure(exc):
                            await client.close()
                        else:
                            await asyncio.to_thread(
                                self.store.record_result, account_pk, success=False,
                                elapsed_ms=0, error="账号检测失败", code=getattr(exc, "code", None),
                                auth_failed=isinstance(exc, MajsoulTokenInvalid) or getattr(exc, "code", None) in (103, 104, 109, 1003, 1004),
                            )
                    finally:
                        await asyncio.to_thread(self.store.checked, account_pk)
                except asyncio.CancelledError:
                    raise
                except Exception as exc:
                    log_warning(事件="账号定时检测", 状态="失败", 错误原因=type(exc).__name__)
                finally:
                    try:
                        await self.release(account_pk)
                    except asyncio.CancelledError:
                        raise
                    except Exception as exc:
                        log_warning(事件="账号定时检测", 状态="释放连接失败", 错误原因=type(exc).__name__)

        await asyncio.gather(*(check_one(acc) for acc in accounts))

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


