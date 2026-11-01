"""Prove slow SQLite-backed account reads don't block the FastAPI event loop."""
import asyncio
from pathlib import Path
import threading
from types import SimpleNamespace

from server import admin_accounts


def test_account_reservation_keeps_event_loop_responsive(tmp_path):
    entered = threading.Event()
    release = threading.Event()

    class SlowStore:
        def all(self):
            entered.set()
            assert release.wait(3)
            return []

    async def scenario():
        scheduler = admin_accounts.AccountScheduler(SlowStore(), tmp_path)
        pending = asyncio.create_task(scheduler.reserve(set()))
        try:
            assert await asyncio.to_thread(entered.wait, 2)
            # This tick must succeed while the SQLite-shaped store call is blocked.
            await asyncio.wait_for(asyncio.sleep(0.01), 1)
            assert not pending.done()
        finally:
            release.set()
        assert await asyncio.wait_for(pending, 2) is None

    asyncio.run(scenario())


def test_slow_sqlite_warning_excludes_sql_and_credentials(tmp_path, monkeypatch):
    store = admin_accounts.AccountStore(tmp_path / "accounts.sqlite3")
    observed = []
    monkeypatch.setattr(admin_accounts, "log_warning", lambda **fields: observed.append(fields))
    readings = iter((0, 0.2))
    monkeypatch.setattr(admin_accounts.time, "perf_counter", lambda: next(readings))
    assert store.all() == []
    assert len(observed) == 1
    assert observed[0] == {"事件": "账号数据库慢操作", "耗时毫秒": 200.0}
    assert "accounts.sqlite3" not in str(observed)
    assert "SELECT" not in str(observed)


def test_toggle_async_runs_storage_off_loop_and_starts_on_loop(tmp_path):
    main_thread = threading.get_ident()

    class Store:
        def get_auto_register_state(self):
            assert threading.get_ident() != main_thread
            return {"enabled": 0, "next_run_at": None, "last_run_at": 0}

        def update_auto_register_state(self, **values):
            assert threading.get_ident() != main_thread

        def get_auto_register_logs(self, **kwargs):
            return []

        def all(self):
            return []

    async def scenario():
        scheduler = admin_accounts.auto_register_scheduler()
        from server.account_register import AutoRegisterScheduler
        s = AutoRegisterScheduler(Store(), tmp_path)
        started = []
        s.start = lambda: started.append(asyncio.get_running_loop())
        result = await s.toggle_async(True)
        assert result["enabled"] is False  # fake store deliberately does not persist state
        assert started == [asyncio.get_running_loop()]

    asyncio.run(scenario())
