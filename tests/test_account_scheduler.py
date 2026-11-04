"""Regression tests for account-pool session reuse and fair rotation."""
from __future__ import annotations

import unittest
from pathlib import Path

from server.account_scheduler import AccountScheduler
from server.paipu_performance import performance


class FakeReader:
    def __init__(self, finished: bool = False):
        self.finished = finished

    def done(self) -> bool:
        return self.finished


class FakeWebSocket:
    def __init__(self, closed: bool = False):
        self.closed = closed


class FakeClient:
    def __init__(self, account: str, account_id: int, *, logged_in: bool = True,
                 closed: bool = False, reader_finished: bool = False):
        self.logged_in = logged_in
        self._ws = FakeWebSocket(closed=closed)
        self._reader = FakeReader(finished=reader_finished)
        self.current_account = account if logged_in else None
        self.account_id = account_id
        self.nickname = "test"
        self.access_token = "token"
        self.connect_calls = 0
        self.login_calls = 0
        self.close_calls = 0

    async def close(self):
        self.close_calls += 1
        self.logged_in = False
        self.current_account = None
        self._ws = None
        self._reader = None

    async def connect(self):
        self.connect_calls += 1
        self._ws = FakeWebSocket()
        self._reader = FakeReader()

    async def login_token(self, token: str):
        self.login_calls += 1
        self.logged_in = True


class FakeStore:
    def __init__(self, accounts: list[dict]):
        self.rows = {row["id"]: dict(row) for row in accounts}
        self.login_records = 0

    def all(self):
        return [dict(row) for row in self.rows.values()]

    def get(self, account_pk: int):
        row = self.rows.get(account_pk)
        return dict(row) if row is not None else None

    def record_login(self, account_pk: int, client: FakeClient, *, refreshed: bool):
        self.login_records += 1


def make_account(account_id: int, *, last_success_at: float | None = None) -> dict:
    return {
        "id": account_id,
        "account": f"account-{account_id}",
        "password": "password",
        "access_token": "token",
        "account_id": 10000 + account_id,
        "enabled": True,
        "auth_failed": False,
        "cooldown_until": 0,
        "last_success_at": last_success_at,
        "last_failure_at": None,
    }


class AccountSessionReuseTests(unittest.IsolatedAsyncioTestCase):
    async def test_rotation_uses_each_idle_account_before_reusing_oldest(self):
        accounts = [make_account(index) for index in range(1, 34)]
        store = FakeStore(accounts)
        scheduler = AccountScheduler(store, Path("."))

        selected_ids = []
        for attempt in range(33):
            selected = await scheduler.reserve(set())
            self.assertIsNotNone(selected)
            selected_ids.append(selected["id"])
            store.rows[selected["id"]]["last_success_at"] = float(attempt + 1)
            await scheduler.release(selected["id"])

        self.assertEqual(len(set(selected_ids)), 33)

        next_account = await scheduler.reserve(set())
        self.assertEqual(next_account["id"], selected_ids[0])
        await scheduler.release(next_account["id"])

    async def test_existing_live_session_wins_only_an_equal_recency_tie(self):
        accounts = [
            make_account(1, last_success_at=100.0),
            make_account(2, last_success_at=100.0),
        ]
        scheduler = AccountScheduler(FakeStore(accounts), Path("."))
        scheduler.clients[2] = FakeClient("account-2", 10002)

        selected = await scheduler.reserve(set())
        self.assertEqual(selected["id"], 2)
        await scheduler.release(selected["id"])

    async def test_authentication_reuses_live_identity_matched_session(self):
        account = make_account(7, last_success_at=100.0)
        store = FakeStore([account])
        scheduler = AccountScheduler(store, Path("."))
        client = FakeClient("account-7", 10007)
        stats = {}
        token = performance.set(stats)
        try:
            await scheduler._authenticate(account, client)
        finally:
            performance.reset(token)

        self.assertEqual(stats.get("雀魂会话复用次数"), 1)
        self.assertEqual(client.connect_calls, 0)
        self.assertEqual(client.login_calls, 0)
        self.assertEqual(store.login_records, 0)

    async def test_completed_reader_invalidates_an_apparently_open_session(self):
        account = make_account(8, last_success_at=100.0)
        store = FakeStore([account])
        scheduler = AccountScheduler(store, Path("."))
        client = FakeClient("account-8", 10008, reader_finished=True)
        token = performance.set({})
        try:
            await scheduler._authenticate(account, client)
        finally:
            performance.reset(token)

        self.assertEqual(client.close_calls, 1)
        self.assertEqual(client.connect_calls, 1)
        self.assertEqual(client.login_calls, 1)
        self.assertEqual(store.login_records, 1)

    async def test_541_is_counted_and_failover_attempt_is_visible(self):
        from unittest.mock import AsyncMock
        from server.majsoul.client import MajsoulError

        first = make_account(1)
        second = make_account(2)
        scheduler = AccountScheduler(FakeStore([first, second]), Path("."))
        scheduler.reserve = AsyncMock(side_effect=[first, second, None])
        scheduler._fetch_one = AsyncMock(
            side_effect=[MajsoulError("fetchGameRecord returned 541", code=541), "record"]
        )
        stats = {}
        token = performance.set(stats)
        try:
            result = await scheduler.fetch_record("260101-12345678-1234-1234-1234-123456789abc")
        finally:
            performance.reset(token)

        self.assertEqual(result, "record")
        self.assertEqual(stats.get("雀魂账号尝试次数"), 2)
        self.assertEqual(stats.get("雀魂账号尝试失败次数"), 1)
        self.assertEqual(stats.get("雀魂接口541错误次数"), 1)
        self.assertEqual(scheduler._fetch_one.await_count, 2)

    async def test_session_metrics_distinguish_reusable_and_inactive_clients(self):
        scheduler = AccountScheduler(FakeStore([]), Path("."))
        scheduler.clients = {
            1: FakeClient("account-1", 10001),
            2: FakeClient("account-2", 10002, closed=True),
            3: FakeClient("account-3", 10003, logged_in=False),
        }

        self.assertEqual(
            scheduler.session_metrics(),
            {
                "cached_clients": 3,
                "reusable_sessions": 1,
                "inactive_clients": 2,
            },
        )


if __name__ == "__main__":
    unittest.main()
