"""Persist complete SQLite snapshots, including metadata and pending writes, encrypted."""

from contextlib import asynccontextmanager

import aiosqlite
from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver

from app.security.vault import Job, VaultPath


class EncryptedSaver(AsyncSqliteSaver):
    async def persist(self):
        async with self.lock:
            # aiosqlite has no serialize API; the lock excludes concurrent SQLite operations.
            data = self.conn._conn.serialize()
            self.target.write_bytes(data)

    async def aput(self, *args, **kwargs):
        result = await super().aput(*args, **kwargs)
        await self.persist()
        return result

    async def aput_writes(self, *args, **kwargs):
        await super().aput_writes(*args, **kwargs)
        await self.persist()


@asynccontextmanager
async def checkpoint(job: Job):
    target = VaultPath(job) / "_checkpoint.sqlite"
    async with aiosqlite.connect(":memory:", check_same_thread=False) as connection:
        if target.is_file():
            connection._conn.deserialize(target.read_bytes())
        saver = EncryptedSaver(connection)
        saver.target = target
        yield saver
