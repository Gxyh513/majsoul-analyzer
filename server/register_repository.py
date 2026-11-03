"""Safely retrieve the private registration program and redact credentials."""
from __future__ import annotations

import io
import os
import re
import shutil
import subprocess
import tempfile
import time
import zipfile
from pathlib import Path
from typing import Any

import httpx

from .logger import log_info, log_warning

MAX_ZIPBALL_UNCOMPRESSED_BYTES = 32 * 1024 * 1024


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


def _safe_extract_repo_zip(content: bytes, reg_dir: Path) -> None:
    """Safely extract a GitHub zipball into reg_dir without path traversal or symlinks."""
    reg_dir.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix=".reg-zip-", dir=reg_dir.parent) as tmp_dir:
        staging_root = Path(tmp_dir).resolve()
        total_bytes = 0
        with zipfile.ZipFile(io.BytesIO(content)) as z:
            for member in z.infolist():
                mode = (member.external_attr >> 16) & 0o170000
                if mode == 0o120000:
                    raise ValueError("压缩包包含不允许的符号链接")
                raw_parts = [p for p in re.split(r"[/\\]+", member.filename) if p]
                if any(part in (".", "..") or ":" in part for part in raw_parts):
                    raise ValueError(f"压缩包包含不安全路径: {member.filename}")
                if len(raw_parts) <= 1:
                    continue
                rel_path = Path(*raw_parts[1:])
                target_path = (staging_root / rel_path).resolve()
                if not target_path.is_relative_to(staging_root):
                    raise ValueError(f"压缩包路径越界: {member.filename}")
                if member.is_dir() or member.filename.endswith(("/", "\\")):
                    target_path.mkdir(parents=True, exist_ok=True)
                    continue
                if member.file_size > MAX_ZIPBALL_UNCOMPRESSED_BYTES:
                    raise ValueError("压缩包单文件体积超过安全上限")
                target_path.parent.mkdir(parents=True, exist_ok=True)
                with z.open(member) as src, open(target_path, "wb") as dst:
                    while chunk := src.read(65536):
                        total_bytes += len(chunk)
                        if total_bytes > MAX_ZIPBALL_UNCOMPRESSED_BYTES:
                            raise ValueError("压缩包解压总大小超过安全上限")
                        dst.write(chunk)
        reg_dir.mkdir(parents=True, exist_ok=True)
        shutil.copytree(staging_root, reg_dir, dirs_exist_ok=True)


def _git_clone_into_dir(repo_url: str, reg_dir: Path, timeout: int = 60) -> subprocess.CompletedProcess[str]:
    """Clone into a temporary directory and copy into reg_dir so existing directories never fail git clone."""
    reg_dir.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix=".reg-git-", dir=reg_dir.parent) as tmp_dir:
        clone_target = Path(tmp_dir) / "repo"
        cmd = ["git", "clone", "--depth", "1", repo_url, str(clone_target)]
        res = subprocess.run(cmd, capture_output=True, timeout=timeout, text=True, encoding="utf-8", errors="replace")
        if res.returncode == 0 and clone_target.is_dir():
            reg_dir.mkdir(parents=True, exist_ok=True)
            shutil.copytree(clone_target, reg_dir, dirs_exist_ok=True, ignore=shutil.ignore_patterns(".git"))
        return res


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
                            _safe_extract_repo_zip(resp.content, reg_dir)
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
                res = _git_clone_into_dir(repo_url, reg_dir, timeout=30)
                if res.returncode == 0 and script_path.is_file():
                    return reg_dir, ""
                err_msg = (res.stderr or res.stdout or f"exit {res.returncode}").strip()
                return None, f"未配置 Token 且 git clone 失败: {err_msg[:150]}"
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
            res = _git_clone_into_dir(auth_repo_url, reg_dir, timeout=60)
            if res.returncode == 0 and script_path.is_file():
                log_info(事件="私有注册仓库Git拉取", 状态="成功")
                return reg_dir, ""
            git_err = redact_secrets(res.stderr or res.stdout, token).strip()
        except Exception as exc:
            git_err = redact_secrets(str(exc), token).strip()

    combined_err = f"API拉取失败 ({api_err})" + (f", Git克隆失败 ({git_err})" if git_err else ", 系统未安装 git")
    log_warning(事件="私有注册仓库拉取失败", 错误原因=combined_err[:250])
    return None, combined_err


