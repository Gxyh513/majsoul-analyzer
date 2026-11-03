"""Request schemas shared by REST and SSE endpoints."""
from __future__ import annotations

from typing import Any
from pydantic import BaseModel


class LoginReq(BaseModel):
    account: str | None = None
    password: str | None = None
    access_token: str | None = None
    remember: bool = True


class AnalyzePaipuReq(BaseModel):
    paipu: str
    target_actor: int | None = None
    engine: str = "auto"
    temperature: float = 0.1


class AnalyzeStreamReq(BaseModel):
    paipu: str | None = None
    content: Any | None = None
    target_actor: int | None = None
    engine: str = "auto"
    temperature: float = 0.1
    title: str | None = None
    accept_compression: str | None = None


class AnalyzeDemoReq(BaseModel):
    target_actor: int = 0
    engine: str = "auto"
    temperature: float = 0.1


