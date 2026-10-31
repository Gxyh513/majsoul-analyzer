import asyncio
from types import SimpleNamespace
from unittest.mock import Mock

import server.app as app
from server.majsoul.client import MajsoulError


def test_startup_preloads_local_protocol_without_connecting(monkeypatch):
    async def scenario():
        preload = Mock()
        connect = Mock(side_effect=AssertionError("startup must not connect"))
        monkeypatch.setattr(app, "ms_client", SimpleNamespace(preload_protocol=preload, connect=connect))
        monkeypatch.setattr(app, "engine_mgr", SimpleNamespace(status=lambda: {}))
        monkeypatch.setattr(app, "_review_tasks", set())
        async with app.lifespan(app.app):
            preload.assert_called_once_with()
        connect.assert_not_called()

    asyncio.run(scenario())


def test_startup_reports_missing_protocol_without_stopping_offline_use(monkeypatch, caplog):
    async def scenario():
        preload = Mock(side_effect=MajsoulError("本地协议文件缺失"))
        monkeypatch.setattr(app, "ms_client", SimpleNamespace(preload_protocol=preload))
        monkeypatch.setattr(app, "engine_mgr", SimpleNamespace(status=lambda: {}))
        monkeypatch.setattr(app, "_review_tasks", set())
        with caplog.at_level("ERROR", logger="majsoul-analyzer"):
            async with app.lifespan(app.app):
                assert preload.call_count == 1
        assert "雀魂本地协议初始化失败" in caplog.text
        assert "本地协议文件缺失" in caplog.text

    asyncio.run(scenario())
