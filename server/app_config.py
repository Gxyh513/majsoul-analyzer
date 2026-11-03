"""Configuration layering and safe persistence for account secrets."""
from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

DATA_DIR = Path(__file__).resolve().parent.parent / "data"
CONFIG_PATH = DATA_DIR / "config.json"
LOCAL_CONFIG_PATH = DATA_DIR / "config.local.json"
EXAMPLE_CONFIG_PATH = DATA_DIR / "config.example.json"


def load_config() -> dict[str, Any]:
    cfg: dict[str, Any] = {}
    if CONFIG_PATH.is_file():
        try:
            cfg = json.loads(CONFIG_PATH.read_text(encoding="utf8"))
        except Exception:  # noqa: BLE001
            pass
    elif EXAMPLE_CONFIG_PATH.is_file():
        try:
            cfg = json.loads(EXAMPLE_CONFIG_PATH.read_text(encoding="utf8"))
        except Exception:  # noqa: BLE001
            pass

    # Safe local config overrides git-tracked template
    if LOCAL_CONFIG_PATH.is_file():
        try:
            local_cfg = json.loads(LOCAL_CONFIG_PATH.read_text(encoding="utf8"))
            cfg.update(local_cfg)
        except Exception:  # noqa: BLE001
            pass

    if os.environ.get("MAJSOUL_ACCOUNT"):
        cfg["account"] = os.environ["MAJSOUL_ACCOUNT"]
    if os.environ.get("MAJSOUL_PASSWORD"):
        cfg["password"] = os.environ["MAJSOUL_PASSWORD"]
    if os.environ.get("MAJSOUL_ACCESS_TOKEN"):
        cfg["access_token"] = os.environ["MAJSOUL_ACCESS_TOKEN"]

    # A secret-managed pool is authoritative, so editing it can add or remove
    # accounts without stale local or individual environment entries returning.
    if "MAJSOUL_ACCOUNTS_JSON" in os.environ:
        try:
            pool = json.loads(os.environ["MAJSOUL_ACCOUNTS_JSON"])
        except (TypeError, ValueError):
            raise ValueError("MAJSOUL_ACCOUNTS_JSON 必须是账号对象组成的 JSON 数组") from None
        if not isinstance(pool, list) or any(not isinstance(item, dict) for item in pool):
            raise ValueError("MAJSOUL_ACCOUNTS_JSON 必须是账号对象组成的 JSON 数组")
        for item in pool:
            if any(item.get(k) is not None and not isinstance(item[k], str) for k in ("account", "password", "access_token", "nickname")):
                raise ValueError("MAJSOUL_ACCOUNTS_JSON 中的账号、密码和令牌必须为字符串")
            if not (item.get("access_token") or (item.get("account") and item.get("password"))):
                raise ValueError("账号池每一项须提供访问令牌，或账号和密码")
        for key in ("account", "password", "access_token", "nickname", "account_id", "backup_accounts", "accounts", "account_pool"):
            cfg.pop(key, None)
        if pool:
            cfg.update({k: pool[0][k] for k in ("account", "password", "access_token", "nickname", "account_id") if k in pool[0]})
            cfg["backup_accounts"] = pool[1:]
    return cfg


def save_config(cfg: dict[str, Any], *, clear_local_credentials: bool = False):
    cfg = dict(cfg)
    if "MAJSOUL_ACCOUNTS_JSON" in os.environ or any(
        os.environ.get(key) for key in ("MAJSOUL_ACCOUNT", "MAJSOUL_PASSWORD", "MAJSOUL_ACCESS_TOKEN")
    ):
        # Preserve existing local credentials rather than persisting environment
        # values or tokens derived from an environment-managed primary account.
        try:
            local = json.loads(LOCAL_CONFIG_PATH.read_text(encoding="utf8")) if LOCAL_CONFIG_PATH.is_file() else {}
        except (ValueError, OSError):
            raise RuntimeError("本地配置无法读取，已取消保存") from None
        keys = ["account", "password", "access_token", "nickname", "account_id"]
        if "MAJSOUL_ACCOUNTS_JSON" in os.environ:
            keys += ["backup_accounts", "accounts", "account_pool"]
        for key in keys:
            cfg.pop(key, None)
            if key in local and not (clear_local_credentials and key in ("password", "access_token")):
                cfg[key] = local[key]
    LOCAL_CONFIG_PATH.write_text(json.dumps(cfg, ensure_ascii=False, indent=2), encoding="utf8")


