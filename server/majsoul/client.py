"""Mahjong Soul (CN server) client: login + fetch game records.

Protocol notes (web client):
  * ``https://game.maj-soul.com/1/version.json`` -> current version
  * ``/1/resversion{ver}.json`` -> prefix of ``res/proto/liqi.json``
  * ``/1/v{ver}/config.json`` -> ``ip[0].gateways`` (route urls)
  * ``{route}/api/clientgate/routes?platform=Web&version=...`` -> ws domains
  * websocket ``wss://{domain}/gateway``; frames:
      0x01 + Wrapper            notify
      0x02 + idx(u16le) + Wrapper  request
      0x03 + idx(u16le) + Wrapper  response
"""
from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
import logging
import os
import tempfile
import random
import re
import time
import uuid
from pathlib import Path

import httpx
import websockets

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


class MajsoulError(Exception):
    def __init__(self, message: str, code: int | None = None):
        super().__init__(message)
        self.code = code


# --------------------------------------------------------------------------
# paipu id helpers (port of tensoul/deobfuse.js)
# --------------------------------------------------------------------------
def decode_log_id(s: str) -> str:
    out = []
    for i, ch in enumerate(s):
        if "0" <= ch <= "9":
            o = ord(ch) - 48
        elif "a" <= ch <= "z":
            o = ord(ch) - 97 + 10
        else:
            out.append(ch)
            continue
        o = (o + 55 - i) % 36
        out.append(chr(o + 48) if o < 10 else chr(o - 10 + 97))
    return "".join(out)


def decode_account_id(n: int) -> int:
    return (((n - 1358437) ^ 86216345) - 1117113) // 7


def parse_paipu(text: str) -> tuple[str, int | None]:
    """Accept a full share link or a raw id. Returns (game_uuid, account_id|None)."""
    text = text.strip()
    m = re.search(r"paipu=([^&#\s]+)", text)
    pid = m.group(1) if m else text
    seps = pid.split("_")
    uuid_ = seps[0]
    target = None
    if len(seps) >= 3 and seps[2] == "2":
        uuid_ = decode_log_id(uuid_)
    if len(seps) >= 2 and seps[1]:
        if seps[1][0] == "a":
            target = decode_account_id(int(seps[1][1:]))
        elif seps[1].isdigit():
            target = int(seps[1])
    if not re.fullmatch(r"\d{6}-[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}", uuid_):
        raise MajsoulError(f"无法识别的牌谱 ID: {pid}")
    return uuid_, target


# --------------------------------------------------------------------------
class MajsoulClient:
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
        self._idx = 1
        self._pending: dict[int, asyncio.Future] = {}
        self._reader: asyncio.Task | None = None
        self.logged_in = False
        self.account_id: int | None = None
        self.nickname: str | None = None
        self.access_token: str | None = None
        self.current_account: str | None = None
        self._lock = asyncio.Lock()

    # ---------------------------------------------------------------- setup
    @property
    def client_version_string(self) -> str:
        clean_ver = re.sub(r"\.w$", "", self.version or DEFAULT_RES_VERSION)
        return f"WebGL_2022-{clean_ver}"

    @property
    def client_version_info(self) -> dict[str, str]:
        clean_ver = re.sub(r"\.w$", "", self.version or DEFAULT_RES_VERSION)
        return {"resource": clean_ver, "package": self.package_version}

    async def _http_json(self, client: httpx.AsyncClient, url: str):
        r = await client.get(url, headers={"User-Agent": UA}, timeout=20)
        r.raise_for_status()
        return r.json()

    async def prepare(self):
        """Fetch version, protobuf schema and gateway list."""
        async with httpx.AsyncClient(follow_redirects=True) as c:
            # 1. Check live WebGL productVersion from index.html if available
            try:
                idx_resp = await c.get(f"{self.base}/", headers={"User-Agent": UA}, timeout=15)
                m_pkg = re.search(r'__webglProductVersion\s*=\s*"([^"]+)"', idx_resp.text)
                if m_pkg:
                    self.package_version = m_pkg.group(1)
            except Exception as e:  # noqa: BLE001
                log.warning("fetch index.html package version failed: %s", e)

            # 2. Load or fetch liqi.json schema (via legacy version.json if not cached)
            ver_data = await self._http_json(c, f"{self.base}/version.json?randv={random.randint(10**15, 10**16)}")
            legacy_ver = ver_data["version"]
            liqi_cache = self.cache_dir / f"liqi-{legacy_ver}.json"
            if liqi_cache.exists():
                liqi = json.loads(liqi_cache.read_text(encoding="utf8"))
            else:
                r = await c.get(f"{self.base}/resversion{legacy_ver}.json", headers={"User-Agent": UA}, timeout=30)
                prefix = r.json()["res"]["res/proto/liqi.json"]["prefix"]
                liqi = await self._http_json(c, f"{self.base}/{prefix}/res/proto/liqi.json")
                temp_path = None
                try:
                    with tempfile.NamedTemporaryFile(mode="w", encoding="utf8", dir=self.cache_dir,
                                                     prefix=".liqi-", suffix=".tmp", delete=False) as f:
                        temp_path = Path(f.name)
                        json.dump(liqi, f)
                        f.flush()
                        os.fsync(f.fileno())
                    os.replace(temp_path, liqi_cache)
                finally:
                    if temp_path is not None:
                        temp_path.unlink(missing_ok=True)

            # Ensure Unity WebGL 4.0 fields exist on ClientDeviceInfo
            try:
                dev_fields = liqi["nested"]["lq"]["nested"]["ClientDeviceInfo"]["fields"]
                if "uuid" not in dev_fields:
                    dev_fields["uuid"] = {"type": "string", "id": 14}
            except Exception:  # noqa: BLE001
                pass
            self.proto = LiqiProto(liqi)

            # 3. Fetch active WebSocket gateway routes for WebGL 4.0
            domains: list[str] = []
            route_ids: dict[str, str] = {}
            route_hosts = list(DEFAULT_ROUTE_HOSTS)
            random.shuffle(route_hosts)
            for gw in route_hosts:
                try:
                    data = await self._http_json(
                        c,
                        f"{gw}/api/clientgate/routes?platform=Web&version={self.package_version}&lang=chs_t",
                    )
                    for rt in data.get("data", {}).get("routes", []):
                        if rt.get("state") != "maintenancing" and rt.get("domain"):
                            dom = rt["domain"]
                            domains.append(dom)
                            route_ids[dom] = rt.get("id") or dom.split(".")[0]
                    if domains:
                        break
                except Exception as e:  # noqa: BLE001
                    log.warning("route fetch from %s failed: %s", gw, e)

            if not domains:
                raise MajsoulError("获取雀魂服务器线路失败")
            self.domains = domains
            self._route_ids = route_ids

    async def connect(self):
        if self.proto is None:
            await self.prepare()
        last_err = None
        connected_domain = None
        for d in self.domains:
            url = f"wss://{d}/gateway"
            try:
                self._ws = await websockets.connect(
                    url,
                    origin=self.origin,
                    user_agent_header=UA,
                    max_size=2**26,
                    open_timeout=15,
                )
                connected_domain = d
                log.info("connected to %s", url)
                break
            except Exception as e:  # noqa: BLE001
                last_err = e
                log.warning("connect %s failed: %s", url, e)
        else:
            raise MajsoulError(f"无法连接雀魂服务器: {last_err}")
        self._reader = asyncio.create_task(self._read_loop())

        # Send Unity WebGL 4.0 Route.requestConnection handshake
        route_id = self._route_ids.get(connected_domain or "", "route-2")
        idx = self._idx
        self._idx = self._idx % 60007 + 1
        pkt = (
            b"\x02"
            + idx.to_bytes(2, "little")
            + self.proto.wrap(".lq.Route.requestConnection", _build_route_request_connection(route_id))
        )
        fut = asyncio.get_running_loop().create_future()
        self._pending[idx] = fut
        await self._ws.send(pkt)
        await asyncio.wait_for(fut, timeout=15)

    async def close(self):
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
                await self._ws.close()
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
            log.info("ws closed: %s", e)
        finally:
            self.logged_in = False
            for fut in self._pending.values():
                if not fut.done():
                    fut.set_exception(MajsoulError("连接已断开"))
            self._pending.clear()

    async def call(self, method: str, timeout: float = 20, **fields):
        """Call ``.lq.Lobby.<method>`` with keyword fields."""
        full = method if method.startswith(".") else f".lq.Lobby.{method}"
        req_t, res_t = self.proto.methods[full]
        req = self.proto.get(req_t)()
        _fill(req, fields)
        idx = self._idx
        self._idx = self._idx % 60007 + 1
        pkt = b"\x02" + idx.to_bytes(2, "little") + self.proto.wrap(full, req.SerializeToString())
        fut = asyncio.get_running_loop().create_future()
        self._pending[idx] = fut
        await self._ws.send(pkt)
        raw = await asyncio.wait_for(fut, timeout)
        _, data = self.proto.unwrap(raw)
        res = self.proto.decode(res_t, data)
        if res.HasField("error") and res.error.code:
            code = int(res.error.code)
            raise MajsoulError(f"雀魂接口 {method} 返回错误码 {code}" + _err_hint(code), code=code)
        return res

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
        # check token validity first
        chk = await self.call("oauth2Check", type=0, access_token=token)
        if not getattr(chk, "has_account", True):
            raise MajsoulError("access_token 无效或已过期")
        rkey = str(uuid.uuid4())
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
    async def fetch_record(self, game_uuid: str) -> dict:
        res = await self.call("fetchGameRecord", game_uuid=game_uuid, client_version_string=self.client_version_string)
        data = res.data
        if not data and res.data_url:
            async with httpx.AsyncClient(follow_redirects=True) as c:
                r = await c.get(res.data_url, headers={"User-Agent": UA}, timeout=60)
                r.raise_for_status()
                data = r.content
        name, payload = self.proto.unwrap(data)
        detail = self.proto.decode(name, payload)
        actions = []
        if getattr(detail, "version", 0) < 210715 and len(detail.records) > 0:
            for rec in detail.records:
                n, d = self.proto.unwrap(rec)
                actions.append(_to_dict(n, self.proto.decode(n, d)))
        else:
            for act in detail.actions:
                if act.result:
                    n, d = self.proto.unwrap(act.result)
                    actions.append(_to_dict(n, self.proto.decode(n, d)))
        from google.protobuf.json_format import MessageToDict

        head = MessageToDict(res.head, preserving_proto_field_name=True, always_print_fields_with_no_presence=True)
        return {"head": head, "actions": actions, "fetched_at": int(time.time())}

    async def fetch_recent_records(self, count: int = 15) -> list[dict]:
        from google.protobuf.json_format import MessageToDict
        from .convert import game_meta

        res = await self.call("fetchGameRecordList", start=1, count=count, type=0)
        items = []
        for r in res.record_list:
            hd = MessageToDict(r, preserving_proto_field_name=True, always_print_fields_with_no_presence=True)
            items.append(game_meta({"head": hd}))
        return items


def _to_dict(name, msg):
    from google.protobuf.json_format import MessageToDict

    d = MessageToDict(msg, preserving_proto_field_name=True, always_print_fields_with_no_presence=True)
    return {"name": name.split(".")[-1], "data": d}


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
