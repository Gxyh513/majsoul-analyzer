"""Mahjong Soul (CN server) client: login + fetch game records.

Protocol notes (web client):
  * the bundled, verified ``liqi`` schema provides protobuf descriptors
  * ``{route}/api/clientgate/routes?platform=Web&version=...`` -> ws domains
  * websocket ``wss://{domain}/gateway``; frames:
      0x01 + Wrapper            notify
      0x02 + idx(u16le) + Wrapper  request
      0x03 + idx(u16le) + Wrapper  response
"""
from __future__ import annotations

import asyncio
from contextlib import nullcontext
from functools import lru_cache
import hashlib
import hmac
import json
import logging
import random
import re
import threading
import time
import uuid
from pathlib import Path

import httpx
import websockets

from ..logger import log_debug, log_warning
from ..paipu_performance import measure
from .proto import LiqiProto

log = logging.getLogger(__name__)

UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/141.0.0.0 Safari/537.36"
)
CN_BASE = "https://game.maj-soul.com/1"
CN_ORIGIN = "https://game.maj-soul.com"
DEFAULT_RES_VERSION = "0.16.289"
DEFAULT_PKG_VERSION = "4.0.51"
# Re-export stable protocol constants and caches from the isolated loader.
from .protocol_loader import (
    LOCAL_PROTOCOL_VERSION, LOCAL_PROTOCOL_SHA256, BUNDLED_PROTOCOL_DIR,
    _PROTOCOL_LOCK, _PROTOCOL_CACHE, _load_local_protocol, _local_protocol,
)
ROUTES_CACHE_TTL_SECONDS = 300
# Bound individual network waits and give each download one overall retry budget.
ROUTES_HTTP_TIMEOUT_SECONDS = 3
ROUTES_DISCOVERY_TIMEOUT_SECONDS = 15
WEBSOCKET_OPEN_TIMEOUT_SECONDS = 3
WEBSOCKET_HANDSHAKE_TIMEOUT_SECONDS = 3
RECORD_RPC_TIMEOUT_SECONDS = 3
RECORD_HTTP_TIMEOUT_SECONDS = 3
GATEWAY_FAILURE_COOLDOWN_SECONDS = 60
_GATEWAY_FAILED_UNTIL: dict[str, float] = {}
_PROTOCOL_LOCK = threading.Lock()
_PROTOCOL_CACHE: dict[str, LiqiProto] = {}
_ROUTE_CACHE: dict[tuple, tuple[float, tuple[str, ...], tuple[tuple[str, str], ...]]] = {}
DEFAULT_ROUTE_HOSTS = [
    "https://route-2.maj-soul.com",
    "https://route-4.maj-soul.com",
    "https://route-5.maj-soul.com",
    "https://route-6.maj-soul.com",
    "https://route-3.maj-soul.com:8443",
]


def _encode_varint(v: int) -> bytes:
    out = bytearray()
    while v > 0x7F:
        out.append((v & 0x7F) | 0x80)
        v >>= 7
    out.append(v & 0x7F)
    return bytes(out)


def _build_route_request_connection(route_id: str) -> bytes:
    rid = route_id.encode("utf-8")
    ts = _encode_varint(int(time.time()))
    return b"\x10\x01\x1a" + bytes([len(rid)]) + rid + b"\x20" + ts + b"\x32\x03Web"


from .errors import MajsoulError, MajsoulTokenInvalid
from .paipu_links import decode_log_id, decode_account_id, parse_paipu, parse_paipu_seat


from .record_codec import RecordCodecMixin, _to_dict


class MajsoulClient(RecordCodecMixin):
    def __init__(self, cache_dir: Path, base: str = CN_BASE, origin: str = CN_ORIGIN):
        self.base = base
        self.origin = origin
        self.cache_dir = Path(cache_dir)
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        self.version: str = DEFAULT_RES_VERSION
        self.package_version: str = DEFAULT_PKG_VERSION
        self.proto: LiqiProto | None = None
        self.domains: list[str] = []
        self._route_ids: dict[str, str] = {}
        self._ws = None
        self._connected_domain: str | None = None
        self._idx = 1
        self._pending: dict[int, asyncio.Future] = {}
        self._reader: asyncio.Task | None = None
        self.logged_in = False
        self.account_id: int | None = None
        self.nickname: str | None = None
        self.access_token: str | None = None
        self.current_account: str | None = None
        self._record_proto: tuple[str, LiqiProto] | None = None
        self.protocol_version: str | None = None

    # ---------------------------------------------------------------- setup
    @property
    def client_version_string(self) -> str:
        clean_ver = re.sub(r"\.w$", "", self.version or DEFAULT_RES_VERSION)
        return f"WebGL_2022-{clean_ver}"

    @property
    def client_version_info(self) -> dict[str, str]:
        clean_ver = re.sub(r"\.w$", "", self.version or DEFAULT_RES_VERSION)
        return {"resource": clean_ver, "package": self.package_version}

    async def _http_json(self, client: httpx.AsyncClient, url: str, *, request_stage: str | None = None,
                         json_stage: str | None = None):
        with measure(request_stage) if request_stage else nullcontext():
            r = await client.get(url, headers={"User-Agent": UA}, timeout=ROUTES_HTTP_TIMEOUT_SECONDS)
        r.raise_for_status()
        with measure(json_stage) if json_stage else nullcontext():
            return r.json()

    def preload_protocol(self) -> LiqiProto:
        """Validate and cache the local schema without connecting to Mahjong Soul."""
        proto = _local_protocol(self.cache_dir)
        self._record_proto = (LOCAL_PROTOCOL_VERSION, proto)
        return proto

    async def prepare(self):
        """Load the verified local schema and discover WebSocket gateway routes."""
        self.proto = self.preload_protocol()
        self.protocol_version = LOCAL_PROTOCOL_VERSION
        cache_key = (self.package_version, tuple(DEFAULT_ROUTE_HOSTS))
        cached = _ROUTE_CACHE.get(cache_key)
        if cached is None or cached[0] <= time.monotonic():
            domains: list[str] = []
            route_ids: dict[str, str] = {}
            route_hosts = list(DEFAULT_ROUTE_HOSTS)
            random.shuffle(route_hosts)
            try:
                async with asyncio.timeout(ROUTES_DISCOVERY_TIMEOUT_SECONDS):
                    async with httpx.AsyncClient(follow_redirects=True) as c:
                        for gw in route_hosts:
                            try:
                                data = await self._http_json(
                                    c,
                                    f"{gw}/api/clientgate/routes?platform=Web&version={self.package_version}&lang=chs_t",
                                    request_stage="雀魂routes线路请求耗时",
                                )
                                for rt in data.get("data", {}).get("routes", []):
                                    if rt.get("state") != "maintenancing" and rt.get("domain"):
                                        dom = rt["domain"]
                                        domains.append(dom)
                                        route_ids[dom] = rt.get("id") or dom.split(".")[0]
                                if domains:
                                    break
                            except Exception as e:  # noqa: BLE001
                                log_warning(事件="雀魂线路获取", 状态="失败", 网关=gw, 错误原因=str(e))
            except TimeoutError as exc:
                raise MajsoulError("获取雀魂服务器线路超时") from exc
            if not domains:
                raise MajsoulError("获取雀魂服务器线路失败")
            cached = (time.monotonic() + ROUTES_CACHE_TTL_SECONDS, tuple(domains), tuple(route_ids.items()))
            _ROUTE_CACHE[cache_key] = cached

        self.domains = list(cached[1])
        self._route_ids = dict(cached[2])

    async def connect(self):
        with measure("雀魂连接准备", failure_count="雀魂连接准备失败次数"):
            await self.prepare()
        last_err = None
        # Prefer gateways that have not recently timed out, but still attempt all
        # discovered routes if necessary instead of permanently blacklisting them.
        now = time.monotonic()
        domains = sorted(
            self.domains,
            key=lambda domain: _GATEWAY_FAILED_UNTIL.get(domain, 0) > now,
        )
        for domain in domains:
            url = f"wss://{domain}/gateway"
            try:
                with measure("WebSocket连接", failure_count="WebSocket连接失败次数"):
                    self._ws = await websockets.connect(
                        url,
                        origin=self.origin,
                        user_agent_header=UA,
                        max_size=2**26,
                        open_timeout=WEBSOCKET_OPEN_TIMEOUT_SECONDS,
                    )
                self._reader = asyncio.create_task(self._read_loop())

                # A TCP/WebSocket connection is not usable until the route
                # handshake succeeds. Try another gateway on handshake failure.
                route_id = self._route_ids.get(domain, "route-2")
                idx = self._idx
                self._idx = self._idx % 60007 + 1
                pkt = (
                    b"\x02"
                    + idx.to_bytes(2, "little")
                    + self.proto.wrap(".lq.Route.requestConnection", _build_route_request_connection(route_id))
                )
                fut = asyncio.get_running_loop().create_future()
                self._pending[idx] = fut
                try:
                    with measure("雀魂线路握手", failure_count="雀魂线路握手失败次数"):
                        # One deadline for WebSocket send + route response.
                        async with asyncio.timeout(WEBSOCKET_HANDSHAKE_TIMEOUT_SECONDS):
                            await self._ws.send(pkt)
                            await fut
                finally:
                    self._pending.pop(idx, None)
                    if not fut.done():
                        fut.cancel()

                _GATEWAY_FAILED_UNTIL.pop(domain, None)
                self._connected_domain = domain
                log_debug(事件="WebSocket连接", 状态="成功", 地址=url)
                return
            except asyncio.CancelledError:
                await self.close()
                raise
            except Exception as exc:  # noqa: BLE001
                last_err = exc
                _GATEWAY_FAILED_UNTIL[domain] = time.monotonic() + GATEWAY_FAILURE_COOLDOWN_SECONDS
                log_warning(事件="WebSocket连接", 状态="失败", 地址=url, 错误原因=str(exc))
                await self.close()
        raise MajsoulError(f"无法连接雀魂服务器: {last_err}") from last_err

    def mark_current_gateway_unhealthy(self):
        """After a stalled RPC, prefer a different gateway on reconnect."""
        if self._connected_domain:
            _GATEWAY_FAILED_UNTIL[self._connected_domain] = time.monotonic() + GATEWAY_FAILURE_COOLDOWN_SECONDS

    async def close(self):
        self._connected_domain = None
        self.logged_in = False
        self.current_account = None
        if self._reader:
            reader, self._reader = self._reader, None
            reader.cancel()
            try:
                await reader
            except asyncio.CancelledError:
                pass
        if self._ws:
            try:
                await asyncio.wait_for(self._ws.close(), timeout=3)
            except Exception:  # noqa: BLE001
                pass
        self._ws = None

    async def _read_loop(self):
        try:
            async for msg in self._ws:
                if not isinstance(msg, (bytes, bytearray)) or not msg:
                    continue
                t = msg[0]
                if t == 3:
                    idx = int.from_bytes(msg[1:3], "little")
                    fut = self._pending.pop(idx, None)
                    if fut and not fut.done():
                        fut.set_result(bytes(msg[3:]))
                elif t == 1:
                    try:
                        name, _ = self.proto.unwrap(bytes(msg[1:]))
                        if name.endswith("NotifyAccountLogout") or name.endswith("NotifyAnotherLogin"):
                            self.logged_in = False
                    except Exception:  # noqa: BLE001
                        pass
        except Exception as e:  # noqa: BLE001
            log_debug(事件="WebSocket连接", 状态="已关闭", 信息=str(e))
        finally:
            self.logged_in = False
            for fut in self._pending.values():
                if not fut.done():
                    fut.set_exception(MajsoulError("连接已断开"))
            self._pending.clear()

    async def call(self, method: str, timeout: float = RECORD_RPC_TIMEOUT_SECONDS, *, return_payload: bool = False, **fields):
        """Call ``.lq.Lobby.<method>`` with keyword fields."""
        full = method if method.startswith(".") else f".lq.Lobby.{method}"
        req_t, res_t = self.proto.methods[full]
        req = self.proto.get(req_t)()
        _fill(req, fields)
        for _ in range(60007):
            idx = self._idx
            self._idx = self._idx % 60007 + 1
            if idx not in self._pending:
                break
        else:
            raise MajsoulError("雀魂会话的并发请求过多")
        pkt = b"\x02" + idx.to_bytes(2, "little") + self.proto.wrap(full, req.SerializeToString())
        fut = asyncio.get_running_loop().create_future()
        self._pending[idx] = fut
        try:
            with measure("fetchGameRecord请求") if full == ".lq.Lobby.fetchGameRecord" else nullcontext():
                # Count send backpressure against the same RPC deadline.
                async with asyncio.timeout(timeout):
                    await self._ws.send(pkt)
                    raw = await fut
        finally:
            self._pending.pop(idx, None)
            if not fut.done():
                fut.cancel()
        with measure("Protobuf实际解码") if return_payload else nullcontext():
            _, data = self.proto.unwrap(raw)
            res = self.proto.decode(res_t, data)
        if res.HasField("error") and res.error.code:
            code = int(res.error.code)
            raise MajsoulError(f"雀魂接口 {method} 返回错误码 {code}" + _err_hint(code), code=code)
        return (res, data) if return_payload else res

    # ---------------------------------------------------------------- auth
    def _device(self, random_key: str):
        return {
            "platform": "pc",
            "hardware": "unknown",
            "os": "windows",
            "os_version": "Windows 10",
            "is_browser": True,
            "sale_platform": "web",
            "screen_width": 1920,
            "screen_height": 1080,
            "user_agent": UA,
            "screen_type": 1,
            "uuid": random_key,
        }

    async def login_password(self, account: str, password: str):
        pw = hmac.new(b"lailai", password.encode(), hashlib.sha256).hexdigest()
        # In CN Mahjong Soul: 0 = email/account, 1 = mobile phone
        primary_type = 1 if account.strip().isdigit() else 0
        types_to_try = [primary_type, 1 - primary_type]
        last_err = None
        res = None
        for acc_type in types_to_try:
            rkey = str(uuid.uuid4())
            try:
                res = await self.call(
                    "login",
                    account=account,
                    password=pw,
                    reconnect=False,
                    device=self._device(rkey),
                    random_key=rkey,
                    client_version=self.client_version_info,
                    gen_access_token=True,
                    currency_platforms=[1, 2, 5, 6, 8, 10, 11],
                    type=acc_type,
                    client_version_string=self.client_version_string,
                    tag="cn",
                )
                break
            except MajsoulError as e:
                last_err = e
                # Only retry with alternate type on account-not-found / wrong-type errors
                if "1003" in str(e) or "104" in str(e) or "1004" in str(e):
                    continue
                raise
        if res is None:
            raise last_err or MajsoulError("登录失败")
        self._after_login(res)
        self.access_token = res.access_token or self.access_token
        self.current_account = account
        return res

    async def login_token(self, token: str):
        # Only an explicit token rejection permits a password fallback.
        try:
            chk = await self.call("oauth2Check", type=0, access_token=token)
        except MajsoulError as exc:
            if exc.code == 109:
                raise MajsoulTokenInvalid("access_token 无效或已过期", code=109) from exc
            raise
        if not getattr(chk, "has_account", True):
            raise MajsoulTokenInvalid("access_token 无效或已过期", code=109)
        rkey = str(uuid.uuid4())
        try:
            res = await self.call(
                "oauth2Login",
                type=0,
                access_token=token,
                reconnect=False,
                device=self._device(rkey),
                random_key=rkey,
                client_version=self.client_version_info,
                gen_access_token=False,
                currency_platforms=[1, 2, 5, 6, 8, 10, 11],
                client_version_string=self.client_version_string,
                tag="cn",
            )
        except MajsoulError as exc:
            if exc.code == 109:
                raise MajsoulTokenInvalid("access_token 无效或已过期", code=109) from exc
            raise
        self._after_login(res)
        self.access_token = token
        return res

    def _after_login(self, res):
        if not res.account_id:
            raise MajsoulError("登录失败（未返回账号信息）")
        self.logged_in = True
        self.account_id = res.account_id
        self.nickname = res.account.nickname if res.HasField("account") else None

    # ---------------------------------------------------------------- records
    async def fetch_record(self, game_uuid: str):
        from server.record_store import RawRecord

        with measure("雀魂牌谱请求", failure_count="雀魂牌谱请求失败次数"):
            res, payload = await self.call("fetchGameRecord", game_uuid=game_uuid,
                                           client_version_string=self.client_version_string,
                                           timeout=RECORD_RPC_TIMEOUT_SECONDS, return_payload=True)
        detail = b""
        if not res.data and res.data_url:
            with measure("外部数据下载", failure_count="外部数据下载失败次数"):
                async with httpx.AsyncClient(follow_redirects=True) as c:
                    r = await c.get(res.data_url, headers={"User-Agent": UA}, timeout=RECORD_HTTP_TIMEOUT_SECONDS)
                    r.raise_for_status()
                    detail = r.content
        if not self.protocol_version or res.head.uuid != game_uuid or not (res.data or detail):
            raise MajsoulError("雀魂返回的牌谱数据无效或 UUID 不匹配")
        return RawRecord(self.protocol_version, payload, detail)

    async def fetch_recent_records(self, count: int = 15) -> list[dict]:
        from google.protobuf.json_format import MessageToDict
        from .convert import game_meta

        res = await self.call("fetchGameRecordList", start=1, count=count, type=0)
        items = []
        for r in res.record_list:
            hd = MessageToDict(r, preserving_proto_field_name=True, always_print_fields_with_no_presence=True)
            items.append(game_meta({"head": hd}))
        return items


def _fill(msg, fields: dict):
    for k, v in fields.items():
        if v is None:
            continue
        f = msg.DESCRIPTOR.fields_by_name.get(k)
        if f is None:
            continue  # field not present in this protocol version
        if isinstance(v, dict):
            _fill(getattr(msg, k), v)
        elif isinstance(v, list):
            getattr(msg, k).extend(v)
        else:
            setattr(msg, k, v)


def _err_hint(code: int) -> str:
    hints = {
        103: "（账号或密码错误）",
        104: "（账号不存在）",
        109: "（access_token 已失效，请重新用密码登录）",
        151: "（客户端版本号过旧，请更新协议版本）",
        540: "（雀魂官方频控限制：单账号单日抓取额度已达上限）",
        1002: "（客户端版本过旧）",
        1003: "（账号不存在）",
        1004: "（账号或密码错误）",
        1005: "（客户端资源版本不一致）",
        1203: "（牌谱不存在或无权查看）",
    }
    return hints.get(code, "")
