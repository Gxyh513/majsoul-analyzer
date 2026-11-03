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


from .auto_register_scheduler import (
    AutoRegisterScheduler, LOCK_EXPIRY_SECONDS, LOCK_RENEW_SECONDS,
    SCHEDULER_POLL_SECONDS, SCHEDULER_ERROR_RETRY_SECONDS,
)
