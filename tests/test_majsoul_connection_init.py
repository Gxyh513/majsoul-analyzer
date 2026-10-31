"""Connection initialization with a pinned local schema and discovered routes."""
from __future__ import annotations

import asyncio
from concurrent.futures import ThreadPoolExecutor
import hashlib
import json
import threading
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import httpx
import pytest

from server.majsoul import client as ms
from server.paipu_performance import performance


@pytest.fixture
def anyio_backend():
    return "asyncio"


@pytest.fixture(autouse=True)
def isolate_initialization_caches(monkeypatch):
    monkeypatch.setattr(ms, "_ROUTE_CACHE", {})
    monkeypatch.setattr(ms, "_PROTOCOL_CACHE", {})
    ms._load_local_protocol.cache_clear()
    yield
    ms._load_local_protocol.cache_clear()


@pytest.fixture
def route_service(monkeypatch):
    """Allow only explicit clientgate discovery, never other HTTP requests."""
    requests = []
    routes = [
        {"domain": "ws-a.example:443", "id": "logical-a", "state": "idle"},
        {"domain": "maintenance.example:443", "id": "logical-off", "state": "maintenancing"},
        {"domain": "ws-b.example:8443", "id": "logical-b", "state": "idle"},
    ]

    async def handle(request):
        requests.append(request)
        assert str(request.url) == (
            "https://discover.example/api/clientgate/routes"
            "?platform=Web&version=4.0.51&lang=chs_t"
        )
        return httpx.Response(200, json={"data": {"routes": routes}})

    real_client = httpx.AsyncClient
    monkeypatch.setattr(ms, "DEFAULT_ROUTE_HOSTS", ["https://discover.example"])
    monkeypatch.setattr(ms.httpx, "AsyncClient", lambda **kwargs: real_client(transport=httpx.MockTransport(handle), **kwargs))
    return requests, routes


@pytest.mark.anyio
async def test_prepare_loads_verified_local_schema_and_discovers_only_websocket_domains(tmp_path, route_service):
    requests, _ = route_service
    client = ms.MajsoulClient(tmp_path)
    stats = {}
    token = performance.set(stats)
    try:
        await client.prepare()
    finally:
        performance.reset(token)

    assert len(requests) == 1
    assert client.domains == ["ws-a.example:443", "ws-b.example:8443"]
    assert client._route_ids == {"ws-a.example:443": "logical-a", "ws-b.example:8443": "logical-b"}
    assert "discover.example" not in client.domains
    assert client.protocol_version == ms.LOCAL_PROTOCOL_VERSION == "0.11.252.w"
    assert client._record_proto == (ms.LOCAL_PROTOCOL_VERSION, client.proto)
    assert client.proto.get("ClientDeviceInfo").DESCRIPTOR.fields_by_name["uuid"].number == 14
    assert client.client_version_info == {"resource": "0.16.289", "package": "4.0.51"}
    assert client.client_version_string == "WebGL_2022-0.16.289"
    assert {"Protobuf协议本地缓存读取耗时", "Protobuf JSON解析及协议初始化耗时", "雀魂routes线路请求耗时"} <= stats.keys()
    assert not {"雀魂首页请求耗时", "version.json请求耗时", "协议资源索引请求耗时", "协议文件下载耗时"} & stats.keys()


@pytest.mark.anyio
async def test_protocol_and_routes_are_reused_across_clients_with_independent_route_lists(monkeypatch, tmp_path, route_service):
    requests, _ = route_service
    builder = Mock(wraps=ms.LiqiProto)
    monkeypatch.setattr(ms, "LiqiProto", builder)
    first = ms.MajsoulClient(tmp_path / "first")
    second = ms.MajsoulClient(tmp_path / "second")
    await first.prepare()
    first.domains.clear()
    first._route_ids["ws-b.example:8443"] = "mutated"
    stats = {}
    token = performance.set(stats)
    try:
        await second.prepare()
    finally:
        performance.reset(token)

    assert first.proto is second.proto
    assert builder.call_count == 1
    assert len(requests) == 1
    assert second.domains == ["ws-a.example:443", "ws-b.example:8443"]
    assert second._route_ids["ws-b.example:8443"] == "logical-b"
    assert "雀魂routes线路请求耗时" not in stats
    assert "Protobuf协议本地缓存读取耗时" not in stats
    assert "Protobuf JSON解析及协议初始化耗时" not in stats


def test_preload_is_thread_safe_and_does_not_mark_connection_ready(monkeypatch, tmp_path):
    builder = Mock(wraps=ms.LiqiProto)
    monkeypatch.setattr(ms, "LiqiProto", builder)
    network = Mock(side_effect=AssertionError("no network expected"))
    monkeypatch.setattr(ms.httpx, "AsyncClient", network)
    clients = [ms.MajsoulClient(tmp_path / str(i)) for i in range(8)]
    ready = threading.Barrier(len(clients))

    def preload(client):
        ready.wait(timeout=5)
        return client.preload_protocol()

    with ThreadPoolExecutor(max_workers=len(clients)) as pool:
        schemas = list(pool.map(preload, clients))
    assert all(schema is schemas[0] for schema in schemas)
    assert builder.call_count == 1
    assert all(client.proto is None and client.domains == [] for client in clients)
    assert all(client._record_proto == (ms.LOCAL_PROTOCOL_VERSION, schemas[0]) for client in clients)
    network.assert_not_called()


@pytest.mark.anyio
async def test_preloaded_client_still_discovers_routes(tmp_path, route_service):
    requests, _ = route_service
    client = ms.MajsoulClient(tmp_path)
    proto = client.preload_protocol()
    assert client.proto is None
    await client.prepare()
    assert client.proto is proto
    assert len(requests) == 1
    assert client.domains == ["ws-a.example:443", "ws-b.example:8443"]


@pytest.mark.anyio
async def test_missing_protocol_fails_explicitly_before_network(monkeypatch, tmp_path):
    monkeypatch.setattr(ms, "BUNDLED_PROTOCOL_DIR", tmp_path / "absent")
    network = Mock(side_effect=AssertionError("no network expected"))
    monkeypatch.setattr(ms.httpx, "AsyncClient", network)
    client = ms.MajsoulClient(tmp_path / "cache")
    stats = {}
    token = performance.set(stats)
    try:
        with pytest.raises(ms.MajsoulError, match="找不到本地雀魂协议文件"):
            await client.connect()
    finally:
        performance.reset(token)
    assert client.proto is None
    assert stats["雀魂连接准备失败次数"] == 1
    network.assert_not_called()


@pytest.mark.anyio
async def test_changed_or_corrupt_local_protocol_is_not_reused_or_downloaded(monkeypatch, tmp_path, route_service):
    requests, _ = route_service
    cache_dir = tmp_path / "cache"
    cache_dir.mkdir()
    schema = cache_dir / f"liqi-{ms.LOCAL_PROTOCOL_VERSION}.json"
    bundled = ms.BUNDLED_PROTOCOL_DIR / schema.name
    schema.write_bytes(bundled.read_bytes())
    await ms.MajsoulClient(cache_dir).prepare()

    schema.write_bytes(b'{"corrupt": true}')
    client = ms.MajsoulClient(cache_dir)
    with pytest.raises(ms.MajsoulError, match="完整性校验失败"):
        await client.prepare()
    assert len(requests) == 1
    assert schema.read_bytes() == b'{"corrupt": true}'
    schema.write_bytes(b"invalid json")
    with pytest.raises(ms.MajsoulError, match="协议文件.*无法解析"):
        await client.prepare()
    assert len(requests) == 1
    assert schema.read_bytes() == b"invalid json"


@pytest.mark.anyio
async def test_equivalent_json_format_in_custom_cache_is_accepted(tmp_path, route_service):
    cache_dir = tmp_path / "cache"
    cache_dir.mkdir()
    filename = f"liqi-{ms.LOCAL_PROTOCOL_VERSION}.json"
    schema = json.loads((ms.BUNDLED_PROTOCOL_DIR / filename).read_text(encoding="utf8"))
    (cache_dir / filename).write_text(json.dumps(schema, sort_keys=True, indent=2), encoding="utf8")
    client = ms.MajsoulClient(cache_dir)
    bundled_client = ms.MajsoulClient(tmp_path / "empty-cache")
    await client.prepare()
    await bundled_client.prepare()
    assert client.proto.get("Wrapper")
    assert client.proto is bundled_client.proto
    assert client.protocol_version == ms.LOCAL_PROTOCOL_VERSION


@pytest.mark.anyio
async def test_verified_but_structurally_invalid_protocol_is_reported(monkeypatch, tmp_path):
    cache_dir = tmp_path / "cache"
    cache_dir.mkdir()
    schema = cache_dir / f"liqi-{ms.LOCAL_PROTOCOL_VERSION}.json"
    schema.write_bytes(b"{}")
    monkeypatch.setattr(ms, "LOCAL_PROTOCOL_SHA256", hashlib.sha256(b"{}").hexdigest())
    network = Mock(side_effect=AssertionError("no network expected"))
    monkeypatch.setattr(ms.httpx, "AsyncClient", network)
    with pytest.raises(ms.MajsoulError, match="协议文件.*无法解析"):
        await ms.MajsoulClient(cache_dir).prepare()
    network.assert_not_called()


@pytest.mark.anyio
async def test_routes_cache_expires_and_is_keyed_by_package(monkeypatch, tmp_path):
    requests = []

    async def handle(request):
        requests.append(request)
        return httpx.Response(200, json={"data": {"routes": [{"domain": f"ws-{len(requests)}.example", "id": "r"}]}})

    real_client = httpx.AsyncClient
    monkeypatch.setattr(ms, "DEFAULT_ROUTE_HOSTS", ["https://discover.example"])
    monkeypatch.setattr(ms.httpx, "AsyncClient", lambda **kwargs: real_client(transport=httpx.MockTransport(handle), **kwargs))
    monkeypatch.setattr(ms, "ROUTES_CACHE_TTL_SECONDS", 0)
    first = ms.MajsoulClient(tmp_path / "first")
    await first.prepare()
    monkeypatch.setattr(ms, "ROUTES_CACHE_TTL_SECONDS", 300)
    second = ms.MajsoulClient(tmp_path / "second")
    await second.prepare()
    third = ms.MajsoulClient(tmp_path / "third")
    third.package_version = "different-package"
    await third.prepare()
    assert [first.domains, second.domains, third.domains] == [["ws-1.example"], ["ws-2.example"], ["ws-3.example"]]
    assert len(requests) == 3


@pytest.mark.anyio
async def test_failed_route_discovery_is_not_cached(monkeypatch, tmp_path):
    requests = []

    async def handle(request):
        requests.append(request)
        if len(requests) == 1:
            return httpx.Response(503)
        return httpx.Response(200, json={"data": {"routes": [{"domain": "ws.example", "id": "actual-route"}]}})

    real_client = httpx.AsyncClient
    monkeypatch.setattr(ms, "DEFAULT_ROUTE_HOSTS", ["https://discover.example"])
    monkeypatch.setattr(ms.httpx, "AsyncClient", lambda **kwargs: real_client(transport=httpx.MockTransport(handle), **kwargs))
    with pytest.raises(ms.MajsoulError, match="获取雀魂服务器线路失败"):
        await ms.MajsoulClient(tmp_path / "first").prepare()
    second = ms.MajsoulClient(tmp_path / "second")
    await second.prepare()
    assert len(requests) == 2
    assert second.domains == ["ws.example"]
    assert second._route_ids == {"ws.example": "actual-route"}


class HandshakeSocket:
    def __init__(self, client, route_ids):
        self.client = client
        self.route_ids = route_ids
        self.messages = asyncio.Queue()

    def __aiter__(self):
        return self

    async def __anext__(self):
        return await self.messages.get()

    async def send(self, packet):
        name, payload = self.client.proto.unwrap(packet[3:])
        assert name == ".lq.Route.requestConnection"
        self.route_ids.append(self.client.proto.decode("ReqRequestConnection", payload).route_id)
        self.messages.put_nowait(b"\x03" + packet[1:3] + self.client.proto.wrap("", b""))

    async def close(self):
        pass


@pytest.mark.anyio
async def test_same_client_reconnect_uses_cache_then_refreshes_expired_routes(monkeypatch, tmp_path, route_service):
    requests, routes = route_service
    client = ms.MajsoulClient(tmp_path)
    handshakes = []
    connect = AsyncMock(side_effect=lambda url, **kwargs: HandshakeSocket(client, handshakes))
    monkeypatch.setattr(ms.websockets, "connect", connect)
    try:
        await client.connect()
        proto = client.proto
        await client.close()
        routes[:] = [{"domain": "ws-new.example:443", "id": "logical-new", "state": "idle"}]
        await client.connect()
        assert len(requests) == 1
        assert client.proto is proto
        await client.close()
        key = (client.package_version, tuple(ms.DEFAULT_ROUTE_HOSTS))
        _, domains, route_ids = ms._ROUTE_CACHE[key]
        ms._ROUTE_CACHE[key] = (0, domains, route_ids)
        await client.connect()
        assert len(requests) == 2
        assert client.proto is proto
        assert handshakes == ["logical-a", "logical-a", "logical-new"]
        assert [call.args[0] for call in connect.await_args_list] == [
            "wss://ws-a.example:443/gateway", "wss://ws-a.example:443/gateway", "wss://ws-new.example:443/gateway"
        ]
    finally:
        await client.close()


@pytest.mark.anyio
async def test_same_client_retries_discovery_on_next_connect_after_failure(monkeypatch, tmp_path):
    requests = []

    async def handle(request):
        requests.append(request)
        if len(requests) == 1:
            return httpx.Response(503)
        return httpx.Response(200, json={"data": {"routes": [{"domain": "ws.example", "id": "actual-route"}]}})

    real_client = httpx.AsyncClient
    monkeypatch.setattr(ms, "DEFAULT_ROUTE_HOSTS", ["https://discover.example"])
    monkeypatch.setattr(ms.httpx, "AsyncClient", lambda **kwargs: real_client(transport=httpx.MockTransport(handle), **kwargs))
    client = ms.MajsoulClient(tmp_path)
    handshakes = []
    connect = AsyncMock(side_effect=lambda url, **kwargs: HandshakeSocket(client, handshakes))
    monkeypatch.setattr(ms.websockets, "connect", connect)
    try:
        with pytest.raises(ms.MajsoulError, match="获取雀魂服务器线路失败"):
            await client.connect()
        proto = client.proto
        assert proto is not None
        connect.assert_not_awaited()
        await client.connect()
        assert len(requests) == 2
        assert client.proto is proto
        assert handshakes == ["actual-route"]
        connect.assert_awaited_once()
    finally:
        await client.close()


@pytest.mark.anyio
async def test_connect_preserves_discovered_route_failover_and_handshake(monkeypatch, tmp_path, route_service):
    client = ms.MajsoulClient(tmp_path)

    class Socket:
        def __init__(self):
            self.messages = asyncio.Queue()
            self.closed = False

        def __aiter__(self):
            return self

        async def __anext__(self):
            return await self.messages.get()

        async def send(self, packet):
            assert packet[0] == 2
            name, payload = client.proto.unwrap(packet[3:])
            assert name == ".lq.Route.requestConnection"
            request = client.proto.decode("ReqRequestConnection", payload)
            assert request.route_id == "logical-b"
            assert payload.endswith(b"\x32\x03Web")
            self.messages.put_nowait(b"\x03" + packet[1:3] + client.proto.wrap("", b""))

        async def close(self):
            self.closed = True

    socket = Socket()
    connect = AsyncMock(side_effect=[OSError("first gateway unavailable"), socket])
    monkeypatch.setattr(ms.websockets, "connect", connect)
    stats = {}
    token = performance.set(stats)
    try:
        await client.connect()
        assert [call.args[0] for call in connect.await_args_list] == [
            "wss://ws-a.example:443/gateway", "wss://ws-b.example:8443/gateway"
        ]
        assert connect.await_args_list[1].kwargs["open_timeout"] == 15
        assert stats["WebSocket连接失败次数"] == 1
        assert stats["雀魂线路握手失败次数"] == 0
    finally:
        await client.close()
        performance.reset(token)
    assert socket.closed


@pytest.mark.anyio
async def test_token_login_keeps_check_and_success_flow(tmp_path):
    client = ms.MajsoulClient(tmp_path)
    invalid_check = SimpleNamespace(has_account=False)
    client.call = AsyncMock(return_value=invalid_check)
    with pytest.raises(ms.MajsoulError, match="无效或已过期"):
        await client.login_token("invalid-test-token")
    assert [call.args[0] for call in client.call.await_args_list] == ["oauth2Check"]

    account = SimpleNamespace(account_id=42, account=SimpleNamespace(nickname="test"), HasField=lambda name: name == "account")
    client.call = AsyncMock(side_effect=[SimpleNamespace(has_account=True), account])
    result = await client.login_token("valid-test-token")
    assert result is account
    assert [call.args[0] for call in client.call.await_args_list] == ["oauth2Check", "oauth2Login"]
    assert client.logged_in and client.account_id == 42 and client.nickname == "test"
    assert client.access_token == "valid-test-token"
