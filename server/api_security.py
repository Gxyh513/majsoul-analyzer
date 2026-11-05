"""CORS boundary, API identity enforcement and request log policy."""
from __future__ import annotations

import hmac
import ipaddress
import os
import time

from fastapi import Request, Response
from fastapi.responses import JSONResponse

from .logger import log_debug, log_warning


def _is_loopback_host(host: str | None) -> bool:
    if host == "localhost":
        return True
    try:
        return ipaddress.ip_address(host or "").is_loopback
    except ValueError:
        return False


def _request_is_authorized(request: Request) -> bool:
    """Use the same identity check for protected routes and private status."""
    token = os.environ.get("MAJSOUL_API_TOKEN", "").strip().strip('"').strip("'")
    if token:
        auth_header = request.headers.get("authorization", "").strip()
        x_token = request.headers.get("x-api-token", "").strip()
        supplied = x_token or (auth_header[7:].strip() if auth_header.startswith("Bearer ") else "")
        return hmac.compare_digest(supplied.encode("utf8"), token.encode("utf8"))

    peer = request.client.host if request.client else None
    local = _is_loopback_host(peer) and _is_loopback_host(request.url.hostname)
    origin = request.headers.get("origin")
    own_origin = f"{request.url.scheme}://{request.headers.get('host', '')}"
    return local and (not origin or origin == own_origin)


async def protect_api_and_log(request: Request, call_next):
    path = request.url.path
    is_api = path.startswith("/api/")
    origin = request.headers.get("origin")
    own_origin = f"{request.url.scheme}://{request.headers.get('host', '')}"
    allowed_origins = {o.strip().rstrip("/") for o in os.environ.get("MAJSOUL_ALLOWED_ORIGINS", "").split(",") if o.strip()}
    origin_allowed = not origin or origin == own_origin or origin in allowed_origins

    if is_api and not origin_allowed:
        return JSONResponse({"detail": "来源不允许访问此服务"}, status_code=403)
    if is_api and request.method == "OPTIONS":
        headers = {"Vary": "Origin"}
        if origin:
            headers.update({"Access-Control-Allow-Origin": origin,
                            "Access-Control-Allow-Methods": "GET, POST, OPTIONS",
                            "Access-Control-Allow-Headers": "Content-Type, Accept, Authorization, X-Api-Token, X-Admin-Key, X-Request-ID",
                            "Access-Control-Allow-Private-Network": "true"})
        return Response(status_code=204, headers=headers)

    # CORS origins are not identities. A configured token protects even
    # loopback traffic, including traffic arriving through a local proxy.
    authorized = _request_is_authorized(request) if is_api else False
    request.state.trusted_api = authorized
    if is_api and path != "/api/status" and not authorized:
        headers = {"Access-Control-Allow-Origin": origin, "Vary": "Origin"} if origin else None
        return JSONResponse({"detail": "远程访问需要有效的 MAJSOUL_API_TOKEN"}, status_code=401, headers=headers)

    is_biz = is_api and path != "/api/status"
    t0 = time.time()
    if is_biz:
        client_ip = request.client.host if request.client else "未知IP"
        log_debug(事件="HTTP请求进入", 请求方法=request.method, 请求路径=path, 客户端地址=client_ip)
    response = await call_next(request)
    if is_api and origin and origin_allowed:
        response.headers["Access-Control-Allow-Origin"] = origin
        response.headers["Vary"] = "Origin"
        response.headers["Access-Control-Allow-Private-Network"] = "true"
        if path == "/api/analyze/stream":
            response.headers["Access-Control-Expose-Headers"] = "X-Request-ID"
    if path == "/api/status":
        # Anonymous and authenticated callers receive different status bodies;
        # neither may be stored by a shared intermediary.
        response.headers["Cache-Control"] = "no-store"
        response.headers.add_vary_header("Authorization")
        response.headers.add_vary_header("X-Api-Token")
    if path in ("/", "/index.html", "/admin", "/admin/", "/admin/index.html"):
        response.headers["Cache-Control"] = "no-cache"
    elif path.startswith("/tiles/") or (
        "v" in request.query_params
        and (
            path == "/app.js"
            or path.startswith(("/js/", "/css/"))
            or (path.startswith("/admin/") and path.endswith((".js", ".css")))
        )
    ):
        response.headers["Cache-Control"] = "public, max-age=604800, immutable"
    if is_biz:
        cost_ms = (time.time() - t0) * 1000
        if response.status_code >= 400:
            log_warning(事件="HTTP请求异常", 请求方法=request.method, 请求路径=path, 状态码=response.status_code, 耗时毫秒=round(cost_ms, 1))
        else:
            log_debug(事件="HTTP响应完成", 请求方法=request.method, 请求路径=path, 状态码=response.status_code, 耗时毫秒=round(cost_ms, 1))
    return response


