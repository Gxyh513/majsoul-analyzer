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
import json
import logging
import os
import shutil
import subprocess
import sys
import tempfile
import time
import uuid
from pathlib import Path
from typing import TYPE_CHECKING, Any

import httpx

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


def mask_account(name_or_acc: str | None) -> str:
    """对账号进行脱敏（例如 mj***@nqmo.com），日志和普通响应绝不输出明文。"""
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


def redact_secrets(text: str, *secrets: Any) -> str:
    """清除文本中可能夹带的 Token、密码或密钥信息。"""
    cleaned = str(text)
    values = sorted({str(s) for s in secrets if isinstance(s, (str, int)) and str(s).strip()}, key=len, reverse=True)
    for v in values:
        if len(v) >= 4:
            cleaned = cleaned.replace(v, "[已隐藏]")
    return cleaned


def resolve_project_root() -> Path:
    return Path(__file__).resolve().parents[1]


def get_register_dir() -> Path:
    """获取注册私有模块所在目录。"""
    override = os.environ.get("MAJSOUL_REGISTER_DIR", "").strip()
    if override:
        return Path(override).expanduser().resolve()
    # 优先检查 data/register
    project_root = resolve_project_root()
    default_dir = project_root / "data" / "register"
    if default_dir.is_dir() and (default_dir / "majsoul_register.py").is_file():
        return default_dir
    # 兼容父级或同级临时开发目录
    alt_dir = project_root.parent / "temp_majsoul_register"
    if alt_dir.is_dir() and (alt_dir / "majsoul_register.py").is_file():
        return alt_dir
    return default_dir


def ensure_private_repo() -> Path | None:
    """
    检查私有注册仓库是否存在；若不存在且配置了 GitHub Token，则通过 Git 克隆到 data/register。
    完全在服务端运行，且 data/register 已被 .gitignore / .dockerignore 排除。
    """
    reg_dir = get_register_dir()
    script_path = reg_dir / "majsoul_register.py"
    if script_path.is_file():
        return reg_dir

    # 尝试通过环境变量 Token 克隆
    token = (
        os.environ.get("MAJSOUL_REGISTER_GITHUB_TOKEN")
        or os.environ.get("GITHUB_TOKEN")
        or os.environ.get("GH_TOKEN")
        or ""
    ).strip()
    repo_url = os.environ.get("MAJSOUL_REGISTER_REPO", "https://github.com/Gxyh513/majsoul-register.git").strip()

    if not token:
        # 检查本地是否有 git 凭据可用
        if shutil.which("git"):
            try:
                reg_dir.parent.mkdir(parents=True, exist_ok=True)
                cmd = ["git", "clone", "--depth", "1", repo_url, str(reg_dir)]
                subprocess.run(cmd, capture_output=True, timeout=30, check=True)
                if script_path.is_file():
                    return reg_dir
            except Exception:
                pass
        return None

    # 使用 Token 安全克隆
    if "@" not in repo_url and repo_url.startswith("https://"):
        auth_repo_url = f"https://x-access-token:{token}@{repo_url[8:]}"
    else:
        auth_repo_url = repo_url

    try:
        reg_dir.parent.mkdir(parents=True, exist_ok=True)
        cmd = ["git", "clone", "--depth", "1", auth_repo_url, str(reg_dir)]
        # 绝不将包含 token 的命令或输出记入常规日志
        res = subprocess.run(cmd, capture_output=True, timeout=60, text=True, encoding="utf-8", errors="replace")
        if res.returncode == 0 and script_path.is_file():
            log_info(事件="私有注册仓库拉取", 状态="成功")
            return reg_dir
        sanitized_err = redact_secrets(res.stderr or res.stdout, token)
        log_warning(事件="私有注册仓库拉取失败", 错误原因=sanitized_err[:200])
    except Exception as exc:
        sanitized_exc = redact_secrets(str(exc), token)
        log_warning(事件="私有注册仓库拉取异常", 错误原因=sanitized_exc[:200])

    return None


class RegisterRunner:
    """注册任务执行器，负责与私有注册模块或私有服务交互。"""

    def __init__(self, register_dir: Path | None = None):
        self.register_dir = register_dir or get_register_dir()

    async def execute(self) -> dict[str, Any]:
        """
        执行一次单账号注册，返回包含 account, password, access_token, nickname 的凭据字典。
        若配置了 MAJSOUL_REGISTER_SERVICE_URL，则优先通过微服务请求。
        否则在隔离子进程中调用 majsoul_register.py。
        """
        service_url = os.environ.get("MAJSOUL_REGISTER_SERVICE_URL", "").strip()
        if service_url:
            return await self._execute_via_service(service_url)

        return await self._execute_via_script()

    async def _execute_via_service(self, service_url: str) -> dict[str, Any]:
        headers = {"Accept": "application/json"}
        auth_token = os.environ.get("MAJSOUL_REGISTER_SERVICE_TOKEN", "").strip()
        if auth_token:
            headers["Authorization"] = f"Bearer {auth_token}"

        async with httpx.AsyncClient(timeout=REGISTER_TASK_TIMEOUT_SECONDS) as client:
            resp = await client.post(service_url, headers=headers, json={"use_mailtd": True})
            if resp.status_code != 200:
                raise RuntimeError(f"注册服务响应异常: HTTP {resp.status_code}")
            data = resp.json()
            return self._normalize_credentials(data)

    async def _execute_via_script(self) -> dict[str, Any]:
        reg_dir = ensure_private_repo() or self.register_dir
        script_path = reg_dir / "majsoul_register.py"
        if not script_path.is_file():
            raise FileNotFoundError(
                "未找到私有注册脚本 (majsoul_register.py)。请配置 MAJSOUL_REGISTER_DIR 或 MAJSOUL_REGISTER_GITHUB_TOKEN。"
            )

        with tempfile.TemporaryDirectory() as temp_dir:
            temp_out_txt = Path(temp_dir) / "output.txt"
            temp_out_json = Path(temp_dir) / "output.json"

            cmd = [
                sys.executable,
                "-u",
                str(script_path),
                "--mailtd",
                "--save-to",
                str(temp_out_txt),
            ]

            mailtd_token = (
                os.environ.get("MAJSOUL_MAILTD_TOKEN")
                or os.environ.get("MAILTD_TOKEN")
                or ""
            ).strip()
            if mailtd_token:
                cmd.extend(["--mailtd-token", mailtd_token])

            env = dict(os.environ)
            project_root = resolve_project_root()
            # 确保子进程能正确加载父级项目模块与私有模块
            env["PYTHONPATH"] = f"{str(reg_dir)}{os.pathsep}{str(project_root)}"

            log_info(事件="自动注册任务", 状态="正在执行底层注册流程")

            def _run():
                return subprocess.run(
                    cmd,
                    cwd=str(reg_dir),
                    env=env,
                    capture_output=True,
                    timeout=REGISTER_TASK_TIMEOUT_SECONDS,
                    text=True,
                    encoding="utf-8",
                    errors="replace",
                )

            try:
                proc = await asyncio.to_thread(_run)
            except subprocess.TimeoutExpired as exc:
                raise TimeoutError(f"注册流程超时（超过 {REGISTER_TASK_TIMEOUT_SECONDS} 秒）") from exc

            if proc.returncode != 0:
                err_msg = (proc.stderr or proc.stdout or "").strip()
                # 检查平台封禁或验证码等限制提示
                sanitized = redact_secrets(err_msg, mailtd_token)
                raise RuntimeError(f"注册脚本执行失败 (退出码 {proc.returncode}): {sanitized[-300:]}")

            # 解析结果
            raw_acc = None
            if temp_out_json.is_file():
                try:
                    loaded = json.loads(temp_out_json.read_text(encoding="utf-8"))
                    if isinstance(loaded, list) and loaded:
                        raw_acc = loaded[-1]
                    elif isinstance(loaded, dict):
                        raw_acc = loaded
                except Exception:
                    pass

            if not raw_acc and temp_out_txt.is_file():
                lines = [line.strip() for line in temp_out_txt.read_text(encoding="utf-8").splitlines() if line.strip()]
                if lines:
                    parts = lines[-1].split("|")
                    if len(parts) >= 3:
                        raw_acc = {
                            "account": parts[0],
                            "password": parts[1],
                            "Token": parts[2],
                            "remark": parts[3] if len(parts) > 3 else "国服",
                        }

            if not raw_acc:
                # 尝试从 stdout 中解析
                for line in reversed((proc.stdout or "").splitlines()):
                    if "|" in line:
                        parts = line.strip().split("|")
                        if len(parts) >= 3 and "@" in parts[0]:
                            raw_acc = {
                                "account": parts[0],
                                "password": parts[1],
                                "Token": parts[2],
                                "remark": parts[3] if len(parts) > 3 else "国服",
                            }
                            break

            if not raw_acc:
                raise RuntimeError("注册脚本成功退出，但未能解析到有效的注册凭证输出")

            return self._normalize_credentials(raw_acc)

    @staticmethod
    def _normalize_credentials(data: dict[str, Any]) -> dict[str, Any]:
        account = str(data.get("account") or "").strip()
        password = str(data.get("password") or "").strip()
        token = str(data.get("Token") or data.get("access_token") or "").strip()
        nickname = str(data.get("remark") or data.get("nickname") or "国服").strip()

        if not account or not password or not token:
            raise ValueError("注册返回的数据中缺少必要凭据字段 (account, password, Token)")

        return {
            "account": account,
            "password": password,
            "access_token": token,
            "nickname": nickname,
        }


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
    runner = runner or RegisterRunner()
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
        if store.has_account(account_name, token):
            store.add_auto_register_log(
                status="跳过",
                account_masked=mask_account(account_name),
                duration_ms=(time.monotonic() - start_time) * 1000,
                error_message="账号或Token已存在于现有账号池，自动忽略",
            )
            store.update_auto_register_state(
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
        account_pk = store.add(verified_data)

        # 6. 通知调度器有新账号就绪
        if scheduler:
            await scheduler.changed()

        duration_ms = (time.monotonic() - start_time) * 1000
        masked = mask_account(verified_data["account"])

        # 7. 更新状态和持久化日志
        current_state = store.get_auto_register_state()
        new_success_count = current_state.get("success_count", 0) + 1
        store.update_auto_register_state(
            last_run_at=time.time(),
            next_run_at=time.time() + DEFAULT_REGISTER_INTERVAL_SECONDS,
            last_status="成功",
            last_error=None,
            last_account_masked=masked,
            success_count=new_success_count,
        )
        store.add_auto_register_log(
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
            当前账号池数=len(store.all()),
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

        current_state = store.get_auto_register_state()
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

        store.update_auto_register_state(**state_updates)
        store.add_auto_register_log(
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


class AutoRegisterScheduler:
    """自动注册调度器，独立调度每小时自动注册任务。"""

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
        self.runner = runner or RegisterRunner()
        self.instance_id = f"inst-{uuid.uuid4().hex[:8]}"
        self.task: asyncio.Task | None = None
        self._stopped = False

    def start(self):
        """启动后台定时注册任务。"""
        if self.task is None or self.task.done():
            self._stopped = False
            self.task = asyncio.create_task(self._periodic_loop())

    async def stop(self):
        """停止调度任务并释放可能占用的任务锁。"""
        self._stopped = True
        if self.task:
            self.task.cancel()
            try:
                await self.task
            except asyncio.CancelledError:
                pass
            self.task = None
        self.store.release_auto_register_lock(self.instance_id)

    async def _periodic_loop(self):
        """定时主循环：保证每小时最多注册一次，服务重启后按计划恢复。"""
        # 1. 服务启动初始化与恢复检查
        try:
            state = self.store.get_auto_register_state()
            now = time.time()
            last_run = state.get("last_run_at") or 0
            elapsed = now - last_run

            # 若上次执行距今不足 1 小时，则先等待剩余时间，绝不因为重启而提前/重复执行
            if last_run > 0 and elapsed < DEFAULT_REGISTER_INTERVAL_SECONDS:
                initial_sleep = DEFAULT_REGISTER_INTERVAL_SECONDS - elapsed
                self.store.update_auto_register_state(next_run_at=now + initial_sleep)
                await asyncio.sleep(initial_sleep)
            else:
                self.store.update_auto_register_state(next_run_at=now)

            while not self._stopped:
                state = self.store.get_auto_register_state()
                now = time.time()

                if state.get("enabled"):
                    # 尝试获取任务锁（排他执行，防止多个副本重复注册）
                    if self.store.acquire_auto_register_lock(self.instance_id, timeout_sec=LOCK_EXPIRY_SECONDS):
                        try:
                            self.store.update_auto_register_state(last_status="运行中")
                            await register_and_ingest(
                                store=self.store,
                                cache_dir=self.cache_dir,
                                scheduler=self.scheduler,
                                manual=False,
                                runner=self.runner,
                            )
                        finally:
                            self.store.release_auto_register_lock(self.instance_id)

                # 设置下次计划时间并休眠 1 小时
                next_time = time.time() + DEFAULT_REGISTER_INTERVAL_SECONDS
                self.store.update_auto_register_state(next_run_at=next_time)
                await asyncio.sleep(DEFAULT_REGISTER_INTERVAL_SECONDS)

        except asyncio.CancelledError:
            raise
        except Exception as exc:
            log_error(事件="自动注册调度器异常", 错误原因=type(exc).__name__)

    async def trigger_manual(self) -> dict[str, Any]:
        """手动触发一次注册任务（带频控与排他锁）。"""
        now = time.time()
        state = self.store.get_auto_register_state()

        # 检查是否正在运行
        if (state.get("locked_until") or 0) > now:
            from fastapi import HTTPException
            raise HTTPException(status_code=409, detail="当前已有注册任务在执行中，请稍候")

        # 避免过于频繁的手动触发（限制至少间隔 30 秒）
        last_run = state.get("last_run_at") or 0
        if last_run > 0 and (now - last_run) < 30:
            from fastapi import HTTPException
            raise HTTPException(status_code=429, detail="请勿过于频繁手动触发，距离上次执行不足30秒")

        if not self.store.acquire_auto_register_lock(self.instance_id, timeout_sec=LOCK_EXPIRY_SECONDS):
            from fastapi import HTTPException
            raise HTTPException(status_code=409, detail="无法获取注册任务锁")

        try:
            self.store.update_auto_register_state(last_status="运行中")
            result = await register_and_ingest(
                store=self.store,
                cache_dir=self.cache_dir,
                scheduler=self.scheduler,
                manual=True,
                runner=self.runner,
            )
            return result
        finally:
            self.store.release_auto_register_lock(self.instance_id)

    def toggle(self, enabled: bool) -> dict[str, Any]:
        """开启或暂停自动注册。"""
        new_val = 1 if enabled else 0
        self.store.update_auto_register_state(
            enabled=new_val,
            last_status="空闲" if enabled else "已暂停",
        )
        return self.overview()

    def overview(self) -> dict[str, Any]:
        """为管理后台提供自动注册状态、计划与汇总统计。"""
        state = self.store.get_auto_register_state()
        now = time.time()
        is_running = (state.get("locked_until") or 0) > now
        recent_logs = self.store.get_auto_register_logs(limit=10)

        all_accounts = self.store.all()

        return {
            "enabled": bool(state.get("enabled", 1)),
            "is_running": is_running,
            "next_run_at": state.get("next_run_at"),
            "last_run_at": state.get("last_run_at"),
            "last_status": "运行中" if is_running else (state.get("last_status") or "未开始"),
            "last_error": state.get("last_error"),
            "last_account": state.get("last_account_masked"),
            "success_count": state.get("success_count", 0),
            "failure_count": state.get("failure_count", 0),
            "current_pool_total": len(all_accounts),
            "recent_logs": recent_logs,
        }
