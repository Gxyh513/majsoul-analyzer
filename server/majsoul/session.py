"""Coordinate concurrent RPCs with exclusive session changes."""
from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager


class SessionLock:
    def __init__(self):
        self._condition = asyncio.Condition()
        self._readers = 0
        self._writer = False
        self._waiting_writers = 0

    async def __aenter__(self):
        async with self._condition:
            self._waiting_writers += 1
            try:
                await self._condition.wait_for(lambda: not self._writer and not self._readers)
                self._writer = True
            finally:
                self._waiting_writers -= 1
                self._condition.notify_all()
        return self

    async def __aexit__(self, *args):
        async with self._condition:
            self._writer = False
            self._condition.notify_all()

    @asynccontextmanager
    async def read(self):
        async with self._condition:
            await self._condition.wait_for(lambda: not self._writer and not self._waiting_writers)
            self._readers += 1
        try:
            yield
        finally:
            async with self._condition:
                self._readers -= 1
                self._condition.notify_all()
