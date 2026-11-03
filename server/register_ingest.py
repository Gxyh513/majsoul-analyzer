"""New-account credential verification, deduplication and ingestion."""
from __future__ import annotations

import asyncio
import time
from pathlib import Path
from typing import TYPE_CHECKING, Any

from .account_pool import mask_account
from .logger import log_info, log_warning
from .majsoul.client import MajsoulClient, MajsoulError, MajsoulTokenInvalid
from .register_runner import RegisterRunner
from .register_repository import redact_secrets

if TYPE_CHECKING:
    from .admin_accounts import AccountScheduler, AccountStore

DEFAULT_REGISTER_INTERVAL_SECONDS = 3600


async def verify_majsoul_credentials(acc_info: dict[str, Any], cache_dir: Path) -> dict[str, Any]:
    """
    通过现有 Majsoul 客户端验证新注册账号的有效性。
    握手网关并验证 Token 登录，确保能正常获取 account_id。
    """
    client = MajsoulClient(cache_dir=cache_dir)
    try:
        await client.connect()
        # 优先使用 access_token 登录
        token = acc_info["access_token"]
        try:
            await client.login_token(token)
        except MajsoulTokenInvalid:
            # Token 若无效则回退至密码登录以获取有效 Token
            await client.login_password(acc_info["account"], acc_info["password"])

        account_id = client.account_id
        if not account_id:
            raise MajsoulError("网关验证通过，但未能获取到 account_id")

        return {
            "account": acc_info["account"],
            "password": acc_info["password"],
            "access_token": client.access_token or acc_info["access_token"],
            "nickname": client.nickname or acc_info.get("nickname") or "",
            "account_id": account_id,
        }
    finally:
        await client.close()


def is_platform_restriction_error(msg: str) -> bool:
    """检查是否属于平台限制、频控、人机验证等需要自动暂停流程的错误。"""
    keywords = (
        "115",
        "过于频繁",
        "1018",
        "通道已关闭",
        "人机验证",
        "验证码错误次数过多",
        "受限字符",
        "captcha",
        "rate limit",
        "forbidden",
    )
    lower = msg.lower()
    return any(k.lower() in lower for k in keywords)


async def register_and_ingest(
    store: AccountStore,
    cache_dir: Path,
    scheduler: AccountScheduler | None = None,
    manual: bool = False,
    runner: RegisterRunner | None = None,
) -> dict[str, Any]:
    """
    执行完整的注册 -> 凭据验证 -> 去重检查 -> 写入账号池 -> 更新状态与历史记录。
    """
    runner = runner or RegisterRunner(cache_dir=cache_dir)
    start_time = time.monotonic()
    raw_credentials = None

    try:
        # 1. 执行注册模块
        raw_credentials = await runner.execute()
        account_name = raw_credentials["account"]
        token = raw_credentials["access_token"]

        # 2. 账号合法性格式检查
        if len(account_name) < 3 or "@" not in account_name:
            raise ValueError(f"返回的账号格式不符合要求: {mask_account(account_name)}")
        if len(raw_credentials["password"]) < 6:
            raise ValueError("返回的账号密码过短")

        # 3. 检查账号池是否已存在该账号
        if await asyncio.to_thread(store.has_account, account_name, token):
            await asyncio.to_thread(store.add_auto_register_log,
                status="跳过",
                account_masked=mask_account(account_name),
                duration_ms=(time.monotonic() - start_time) * 1000,
                error_message="账号或Token已存在于现有账号池，自动忽略",
            )
            await asyncio.to_thread(store.update_auto_register_state,
                last_run_at=time.time(),
                next_run_at=time.time() + DEFAULT_REGISTER_INTERVAL_SECONDS,
                last_status="成功",
                last_error=None,
                last_account_masked=mask_account(account_name),
            )
            return {
                "ok": True,
                "status": "duplicate",
                "account": mask_account(account_name),
                "detail": "账号已在账号池中，已跳过重复写入",
            }

        # 4. 雀魂网关登录有效性验证
        verified_data = await verify_majsoul_credentials(raw_credentials, cache_dir)

        # 5. 写入现有账号数据库
        account_pk = await asyncio.to_thread(store.add, verified_data)

        # 6. 通知调度器有新账号就绪
        if scheduler:
            await scheduler.changed()

        duration_ms = (time.monotonic() - start_time) * 1000
        masked = mask_account(verified_data["account"])

        # 7. 更新状态和持久化日志
        current_state = await asyncio.to_thread(store.get_auto_register_state)
        new_success_count = current_state.get("success_count", 0) + 1
        await asyncio.to_thread(store.update_auto_register_state,
            last_run_at=time.time(),
            next_run_at=time.time() + DEFAULT_REGISTER_INTERVAL_SECONDS,
            last_status="成功",
            last_error=None,
            last_account_masked=masked,
            success_count=new_success_count,
        )
        await asyncio.to_thread(store.add_auto_register_log,
            status="成功",
            account_masked=masked,
            duration_ms=duration_ms,
            error_message=None,
            details=f"成功加入账号池 (ID: {account_pk})",
        )

        log_info(
            事件="自动注册",
            状态="成功",
            账号=masked,
            耗时毫秒=round(duration_ms, 1),
            当前账号池数=len(await asyncio.to_thread(store.all)),
        )

        return {
            "ok": True,
            "status": "success",
            "account": masked,
            "id": account_pk,
            "duration_ms": duration_ms,
        }

    except Exception as exc:
        duration_ms = (time.monotonic() - start_time) * 1000
        masked = mask_account(raw_credentials.get("account")) if raw_credentials else ""
        secret_items = []
        if raw_credentials:
            secret_items.extend([raw_credentials.get("password"), raw_credentials.get("access_token")])
        sanitized_error = redact_secrets(str(exc), *secret_items)

        current_state = await asyncio.to_thread(store.get_auto_register_state)
        new_failure_count = current_state.get("failure_count", 0) + 1

        # 检查是否需要触发平台熔断保护
        should_pause = is_platform_restriction_error(sanitized_error)
        state_updates: dict[str, Any] = {
            "last_run_at": time.time(),
            "next_run_at": time.time() + DEFAULT_REGISTER_INTERVAL_SECONDS,
            "last_status": "失败",
            "last_error": sanitized_error[:200],
            "failure_count": new_failure_count,
        }
        if should_pause:
            state_updates["enabled"] = 0
            state_updates["last_error"] = f"[触发平台限制，已自动暂停] {sanitized_error[:150]}"

        await asyncio.to_thread(store.update_auto_register_state,**state_updates)
        await asyncio.to_thread(store.add_auto_register_log,
            status="失败",
            account_masked=masked,
            duration_ms=duration_ms,
            error_message=sanitized_error[:200],
        )

        log_warning(
            事件="自动注册",
            状态="失败",
            错误原因=sanitized_error[:150],
            耗时毫秒=round(duration_ms, 1),
            已暂停自动注册=should_pause,
        )

        return {
            "ok": False,
            "status": "failed",
            "error": sanitized_error,
            "paused": should_pause,
        }


