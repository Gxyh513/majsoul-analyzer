"""Mahjong Soul account login, logout and recent-record endpoints."""
from __future__ import annotations

import asyncio
import hmac
import os

from fastapi import APIRouter, HTTPException

from .logger import log_warning
from .majsoul.client import MajsoulError
from .api_schemas import LoginReq
from .runtime import ms_client, _ms_client_lock
from .app_config import load_config, save_config
from .account_credentials import is_client_logged_in_as, _redact_error
from .account_pool import ensure_ms_logged_in
from .api_status import _build_api_status

router = APIRouter()


@router.post("/api/login")
async def api_login(req: LoginReq):
    if req.remember and ("MAJSOUL_ACCOUNTS_JSON" in os.environ or any(
        os.environ.get(key) for key in ("MAJSOUL_ACCOUNT", "MAJSOUL_PASSWORD", "MAJSOUL_ACCESS_TOKEN")
    )):
        raise HTTPException(status_code=400, detail="账号由环境变量管理，无法记住登录；请修改部署密文或选择临时登录")
    if not req.access_token and not (req.account and req.password):
        raise HTTPException(status_code=400, detail="请提供雀魂账号密码或 Access Token")
    async with _ms_client_lock:
        try:
            cfg = await asyncio.to_thread(load_config)
            same_token = bool(req.access_token and req.access_token.strip() == ms_client.access_token)
            same_password = bool(not req.access_token and req.account and req.password and cfg.get("account") and cfg.get("password")
                                 and req.account.strip().lower() == str(cfg["account"]).strip().lower()
                                 and hmac.compare_digest(req.password.encode(), str(cfg["password"]).encode()))
            live = is_client_logged_in_as({"account": req.account, "access_token": req.access_token}, ms_client)
            if not (live and (same_token or same_password)):
                await ms_client.close()
                await ms_client.connect()
                if req.access_token:
                    await ms_client.login_token(req.access_token.strip())
                else:
                    await ms_client.login_password(req.account.strip(), req.password)

            if req.remember:
                cfg = await asyncio.to_thread(load_config)
                if req.account:
                    cfg["account"] = req.account.strip()
                if req.password:
                    cfg["password"] = req.password
                if ms_client.access_token:
                    cfg["access_token"] = ms_client.access_token
                if ms_client.nickname:
                    cfg["nickname"] = ms_client.nickname
                if ms_client.account_id:
                    cfg["account_id"] = ms_client.account_id
                await asyncio.to_thread(save_config, cfg)

            return await asyncio.to_thread(_build_api_status, trusted=True)
        except HTTPException:
            raise
        except Exception as e:  # noqa: BLE001
            normalized_token = req.access_token.strip() if req.access_token else None
            log_warning(
                事件="雀魂登录",
                状态="失败",
                失败阶段="接口登录",
                错误原因=_redact_error(e, req.password, req.access_token, normalized_token, getattr(ms_client, "access_token", None)),
            )
            status_code = 400 if isinstance(e, MajsoulError) else 500
            raise HTTPException(status_code=status_code, detail="连接或登录失败，请检查账号设置后重试。") from e


@router.post("/api/logout")
async def api_logout():
    async with _ms_client_lock:
        await ms_client.close()
        cfg = await asyncio.to_thread(load_config)
        cfg.pop("access_token", None)
        cfg.pop("password", None)
        await asyncio.to_thread(save_config, cfg, clear_local_credentials=True)
        return await asyncio.to_thread(_build_api_status, trusted=True)


@router.get("/api/recent_games")
async def api_recent_games():
    async with _ms_client_lock:
        await ensure_ms_logged_in()
        try:
            games = await ms_client.fetch_recent_records(count=15)
            return {"games": games}
        except Exception as e:  # noqa: BLE001
            log_warning(
                事件="获取最近牌谱",
                状态="失败",
                错误原因=_redact_error(e, getattr(ms_client, "access_token", None)),
            )
            raise HTTPException(status_code=400, detail="获取最近牌谱失败，请稍后重试。") from e


