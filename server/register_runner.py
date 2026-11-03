"""Registration subprocess and service adapter; never log returned secrets."""
from __future__ import annotations

import asyncio
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any

import httpx

from .logger import log_info, log_warning
from .register_repository import get_register_dir, resolve_project_root, ensure_private_repo, redact_secrets

REGISTER_TASK_TIMEOUT_SECONDS = 150


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


