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
    # 优先检查 ModelScope 持久化工作空间目录 /mnt/workspace/register
    workspace = Path("/mnt/workspace")
    if workspace.is_dir():
        ws_reg = workspace / "register"
        if (ws_reg / "majsoul_register.py").is_file():
            return ws_reg
        ws_app_reg = workspace / "majsoul-analyzer" / "data" / "register"
        if (ws_app_reg / "majsoul_register.py").is_file():
            return ws_app_reg
    # 检查当前项目下 data/register
    project_root = resolve_project_root()
    default_dir = project_root / "data" / "register"
    if default_dir.is_dir() and (default_dir / "majsoul_register.py").is_file():
        return default_dir
    # 兼容父级或同级临时开发目录
    alt_dir = project_root.parent / "temp_majsoul_register"
    if alt_dir.is_dir() and (alt_dir / "majsoul_register.py").is_file():
        return alt_dir
    if workspace.is_dir():
        return workspace / "register"
    return default_dir


def _download_repo_zipball(token: str, repo_identifier: str, reg_dir: Path) -> tuple[bool, str]:
    """
    通过 GitHub REST API 下载仓库 zipball 并解压到目标目录。
    纯 Python 实现，零外部 git CLI 依赖，完美适配无 git 环境的容器（如魔搭 ModelScope）。
    """
    clean_repo = repo_identifier
    if "github.com/" in clean_repo:
        clean_repo = clean_repo.split("github.com/", 1)[1]
    clean_repo = clean_repo.removesuffix(".git").strip("/")
    if not clean_repo or "/" not in clean_repo:
        clean_repo = "Gxyh513/majsoul-register"

    token = token.strip().strip("'\"")
    api_base = os.environ.get("MAJSOUL_GITHUB_API_BASE", "").strip().rstrip("/") or "https://api.github.com"
    branches = ["main", "master"]
    auth_variants = [f"Bearer {token}", f"token {token}"]
    last_err = ""

    for branch in branches:
        url = f"{api_base}/repos/{clean_repo}/zipball/{branch}"
        for auth_header in auth_variants:
            headers = {
                "Authorization": auth_header,
                "Accept": "application/vnd.github+json",
                "User-Agent": "majsoul-analyzer",
                "X-GitHub-Api-Version": "2022-11-28",
            }
            for attempt in range(2):
                try:
                    with httpx.Client(headers=headers, follow_redirects=True, timeout=45) as client:
                        resp = client.get(url)
                        if resp.status_code == 200 and resp.content:
                            reg_dir.mkdir(parents=True, exist_ok=True)
                            with zipfile.ZipFile(io.BytesIO(resp.content)) as z:
                                for member in z.infolist():
                                    parts = Path(member.filename).parts
                                    if len(parts) <= 1:
                                        continue
                                    rel_path = Path(*parts[1:])
                                    target_path = reg_dir / rel_path
                                    if member.is_dir():
                                        target_path.mkdir(parents=True, exist_ok=True)
                                    else:
                                        target_path.parent.mkdir(parents=True, exist_ok=True)
                                        with z.open(member) as src, open(target_path, "wb") as dst:
                                            shutil.copyfileobj(src, dst)
                            if (reg_dir / "majsoul_register.py").is_file():
                                log_info(事件="私有注册仓库API拉取", 状态="成功", 分支=branch)
                                return True, ""
                        elif resp.status_code == 404:
                            last_err = f"HTTP 404（分支 {branch} 不存在或 Token 无权访问此私有仓库）"
                            break
                        elif resp.status_code == 401:
                            last_err = "HTTP 401 Unauthorized（MAJSOUL_REGISTER_GITHUB_TOKEN 无效或已过期）"
                            break
                        elif resp.status_code == 403:
                            last_err = f"HTTP 403 Forbidden（Token 权限不足或被限流: {resp.text[:100]}）"
                            break
                        else:
                            last_err = f"HTTP {resp.status_code}: {resp.text[:100]}"
                except Exception as exc:
                    last_err = str(exc)
                    time.sleep(1)

    return False, last_err


def ensure_private_repo(force_update: bool = False) -> tuple[Path | None, str]:
    """
    检查私有注册仓库是否存在；若不存在且配置了 GitHub Token，优先通过 GitHub REST API 下载解压，
    备选通过 git clone 拉取。返回 (reg_dir_or_none, failure_reason)。
    """
    reg_dir = get_register_dir()
    script_path = reg_dir / "majsoul_register.py"
    if not force_update and script_path.is_file():
        return reg_dir, ""

    token = (
        os.environ.get("MAJSOUL_REGISTER_GITHUB_TOKEN")
        or os.environ.get("GITHUB_TOKEN")
        or os.environ.get("GH_TOKEN")
        or ""
    ).strip().strip("'\"")
    repo_url = os.environ.get("MAJSOUL_REGISTER_REPO", "https://github.com/Gxyh513/majsoul-register.git").strip().strip("'\"")

    if not token:
        if shutil.which("git"):
            try:
                reg_dir.parent.mkdir(parents=True, exist_ok=True)
                cmd = ["git", "clone", "--depth", "1", repo_url, str(reg_dir)]
                subprocess.run(cmd, capture_output=True, timeout=30, check=True)
                if script_path.is_file():
                    return reg_dir, ""
            except Exception as exc:
                return None, f"未配置 Token 且 git clone 失败: {str(exc)[:150]}"
        return None, "未配置 MAJSOUL_REGISTER_GITHUB_TOKEN 环境变量且未找到本地注册脚本"

    # 1. 优先使用纯 Python GitHub REST API 下载（免 git 命令行环境）
    api_ok, api_err = _download_repo_zipball(token, repo_url, reg_dir)
    if api_ok and script_path.is_file():
        return reg_dir, ""

    # 2. 备选用 git 命令行（若系统已安装 git）
    git_err = ""
    if shutil.which("git"):
        if "@" not in repo_url and repo_url.startswith("https://"):
            auth_repo_url = f"https://x-access-token:{token}@{repo_url[8:]}"
        else:
            auth_repo_url = repo_url
        try:
            reg_dir.parent.mkdir(parents=True, exist_ok=True)
            cmd = ["git", "clone", "--depth", "1", auth_repo_url, str(reg_dir)]
            res = subprocess.run(cmd, capture_output=True, timeout=60, text=True, encoding="utf-8", errors="replace")
            if res.returncode == 0 and script_path.is_file():
                log_info(事件="私有注册仓库Git拉取", 状态="成功")
                return reg_dir, ""
            git_err = redact_secrets(res.stderr or res.stdout, token).strip()
        except Exception as exc:
            git_err = redact_secrets(str(exc), token).strip()

    combined_err = f"API拉取失败 ({api_err})" + (f", Git克隆失败 ({git_err})" if git_err else ", 系统未安装 git")
    log_warning(事件="私有注册仓库拉取失败", 错误原因=combined_err[:250])
    return None, combined_err


class RegisterRunner:
    """注册任务执行器，负责与私有注册模块或私有服务交互。"""

    def __init__(self, register_dir: Path | None = None, cache_dir: Path | None = None):
        self.register_dir = register_dir or get_register_dir()
        self.cache_dir = cache_dir or (resolve_project_root() / "data" / "cache")

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
        # Private-repository retrieval performs blocking HTTP and git operations.
        # Never block the FastAPI event loop while the scheduled job starts.
        reg_dir, pull_err = await asyncio.to_thread(ensure_private_repo)
        if not reg_dir:
            reg_dir = self.register_dir
        script_path = reg_dir / "majsoul_register.py"
        if not script_path.is_file():
            err_hint = f"（拉取详情: {pull_err}）" if pull_err else ""
            raise FileNotFoundError(
                f"未找到私有注册脚本 (majsoul_register.py){err_hint}。请检查 MAJSOUL_REGISTER_GITHUB_TOKEN 权限或配置 MAJSOUL_REGISTER_DIR。"
            )

        token = (
            os.environ.get("MAJSOUL_REGISTER_GITHUB_TOKEN")
            or os.environ.get("GITHUB_TOKEN")
            or os.environ.get("GH_TOKEN")
            or ""
        ).strip().strip("'\"")

        mailtd_token = (
            os.environ.get("MAJSOUL_MAILTD_TOKEN")
            or os.environ.get("MAILTD_TOKEN")
            or ""
        ).strip().strip("'\"")

        project_root = resolve_project_root()
        cache_dir = getattr(self, "cache_dir", None) or (project_root / "data" / "cache")

        env = dict(os.environ)
        # 确保子进程能正确加载父级项目模块与私有模块，以及注入缓存路径与项目根路径
        env["PYTHONPATH"] = f"{str(reg_dir)}{os.pathsep}{str(project_root)}"
        env["MAJSOUL_CACHE_DIR"] = str(cache_dir)
        env["MAJSOUL_PROJECT_ROOT"] = str(project_root)
        env["PROJECT_ROOT"] = str(project_root)

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
            if mailtd_token:
                cmd.extend(["--mailtd-token", mailtd_token])

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

            log_info(事件="自动注册任务", 状态="正在执行底层注册流程")
            try:
                proc = await asyncio.to_thread(_run)
            except subprocess.TimeoutExpired as exc:
                raise TimeoutError(f"注册流程超时（超过 {REGISTER_TASK_TIMEOUT_SECONDS} 秒）") from exc

            # 若首次执行失败（如本地持久化目录残留旧版本脚本），且配置了 Token，尝试强制拉取最新仓库代码并重试一次
            if proc.returncode != 0 and token:
                log_warning(事件="注册脚本首次执行失败，尝试强制拉取最新仓库代码", 退出码=proc.returncode)
                updated_dir, update_err = await asyncio.to_thread(ensure_private_repo, force_update=True)
                if updated_dir and (updated_dir / "majsoul_register.py").is_file():
                    try:
                        proc = await asyncio.to_thread(_run)
                    except subprocess.TimeoutExpired as exc:
                        raise TimeoutError(f"重试注册流程超时（超过 {REGISTER_TASK_TIMEOUT_SECONDS} 秒）") from exc

            if proc.returncode != 0:
                err_msg = ((proc.stderr or "") + "\n" + (proc.stdout or "")).strip()
                sanitized = redact_secrets(err_msg, mailtd_token, token)
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
        self.store.release_auto_register_lock(self.instance_id)

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

                if not self.store.acquire_auto_register_lock(self.instance_id, timeout_sec=LOCK_EXPIRY_SECONDS):
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
                    self.store.release_auto_register_lock(self.instance_id)

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
        if not self.store.acquire_auto_register_lock(self.instance_id, timeout_sec=LOCK_EXPIRY_SECONDS):
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
            self.store.release_auto_register_lock(self.instance_id)
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

    def overview(self) -> dict[str, Any]:
        """Expose both persisted status and whether the in-process timer is alive."""
        state = self.store.get_auto_register_state()
        now = time.time()
        is_running = (state.get("locked_until") or 0) > now
        scheduler_alive = self.task is not None and not self.task.done()
        recent_logs = self.store.get_auto_register_logs(limit=10)

        all_accounts = self.store.all()

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
            "current_pool_total": len(all_accounts),
            "recent_logs": recent_logs,
        }
