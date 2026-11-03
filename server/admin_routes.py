"""Authenticated admin HTTP routes; account state is owned by admin_accounts."""
from __future__ import annotations

import asyncio
import hmac
import os

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field


def _get_manager():
    # Late lookup avoids a circular import and preserves monkeypatchable state.
    from .admin_accounts import _get_manager as lookup
    return lookup()


def _get_auto_register_scheduler():
    from .admin_accounts import _get_auto_register_scheduler as lookup
    return lookup()


class AdminAccount(BaseModel):
    account: str
    password: str
    access_token: str
    nickname: str = ""


class AdminBulk(BaseModel):
    accounts: list[AdminAccount] = Field(min_length=1, max_length=500)


class AdminEdit(BaseModel):
    account: str | None = None
    password: str | None = None
    access_token: str | None = None
    nickname: str | None = None
    enabled: bool | None = None


admin_router = APIRouter(prefix="/api/admin", tags=["管理员账号池"])


def _admin_key() -> str:
    return os.environ.get("MAJSOUL_ADMIN_KEY", "").strip()


def _authorize(request: Request):
    key = _admin_key()
    if not key:
        raise HTTPException(status_code=503, detail="未配置管理员密钥")
    supplied = request.headers.get("x-admin-key", "")
    if not supplied or not hmac.compare_digest(supplied.encode("utf-8"), key.encode("utf-8")):
        raise HTTPException(status_code=401, detail="管理员密钥错误")


@admin_router.post("/login")
async def admin_login(request: Request):
    _authorize(request)
    return {"ok": True}


@admin_router.post("/logout")
async def admin_logout(request: Request):
    _authorize(request)
    return {"ok": True}


@admin_router.get("/status")
async def admin_status(request: Request):
    _authorize(request)
    return await asyncio.to_thread(_get_manager().overview)


@admin_router.get("/accounts")
async def admin_accounts(request: Request):
    _authorize(request)
    return await asyncio.to_thread(_get_manager().overview)


@admin_router.post("/accounts")
async def admin_add_account(request: Request, data: AdminAccount):
    _authorize(request)
    current = _get_manager()
    try:
        pk = await asyncio.to_thread(current.store.add, data.model_dump())
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    await current.changed()
    return {"ok": True, "id": pk}


@admin_router.post("/accounts/import")
async def admin_import_accounts(request: Request, data: AdminBulk):
    _authorize(request)
    current = _get_manager()
    try:
        result = await asyncio.to_thread(current.store.bulk_add, [item.model_dump() for item in data.accounts])
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    await current.changed()
    return result


@admin_router.patch("/accounts/{account_pk}")
@admin_router.post("/accounts/{account_pk}/edit")
async def admin_edit_account(account_pk: int, request: Request, data: AdminEdit):
    _authorize(request)
    current = _get_manager()
    try:
        await asyncio.to_thread(current.store.edit, account_pk, data.model_dump(exclude_unset=True))
    except (ValueError, KeyError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    await current.changed(account_pk)
    return {"ok": True}


@admin_router.delete("/accounts/{account_pk}")
@admin_router.post("/accounts/{account_pk}/delete")
async def admin_delete_account(account_pk: int, request: Request):
    _authorize(request)
    current = _get_manager()
    try:
        await asyncio.to_thread(current.store.delete, account_pk)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    await current.changed(account_pk)
    return {"ok": True}


class AdminRegisterToggle(BaseModel):
    enabled: bool


@admin_router.get("/register/status")
async def admin_register_status(request: Request):
    _authorize(request)
    return await asyncio.to_thread(_get_auto_register_scheduler().overview)


@admin_router.post("/register/toggle")
async def admin_register_toggle(request: Request, data: AdminRegisterToggle):
    _authorize(request)
    return await _get_auto_register_scheduler().toggle_async(data.enabled)


@admin_router.post("/register/trigger")
async def admin_register_trigger(request: Request):
    _authorize(request)
    return await _get_auto_register_scheduler().trigger_manual()


@admin_router.get("/register/history")
async def admin_register_history(request: Request):
    _authorize(request)
    return await asyncio.to_thread(_get_manager().store.get_auto_register_logs, limit=100)
