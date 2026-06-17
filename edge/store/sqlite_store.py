from __future__ import annotations

import json
import logging
from pathlib import Path

import aiosqlite

from edge.ingestion.schemas import RawMessage
from edge.store.base import Store

logger = logging.getLogger(__name__)

_CREATE_TABLE = """
CREATE TABLE IF NOT EXISTS raw_messages (
    id       INTEGER PRIMARY KEY AUTOINCREMENT,
    ts_ms    INTEGER NOT NULL,
    seq      INTEGER NOT NULL,
    home_id  TEXT NOT NULL DEFAULT '',
    topic    TEXT NOT NULL,
    payload  TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_ts_ms   ON raw_messages (ts_ms);
CREATE INDEX IF NOT EXISTS idx_home_ts ON raw_messages (home_id, ts_ms);
"""


class SqliteStore(Store):
    def __init__(self, db_path: str) -> None:
        self._db_path = db_path
        self._db: aiosqlite.Connection | None = None

    async def open(self) -> None:
        Path(self._db_path).parent.mkdir(parents=True, exist_ok=True)
        self._db = await aiosqlite.connect(self._db_path)
        await self._db.executescript(_CREATE_TABLE)
        await self._db.commit()
        logger.info("SqliteStore opened db=%s", self._db_path)

    async def close(self) -> None:
        if self._db:
            await self._db.close()

    async def write_raw(self, msg: RawMessage, home_id: str = "") -> None:
        assert self._db, "Store not opened"
        await self._db.execute(
            "INSERT INTO raw_messages (ts_ms, seq, home_id, topic, payload) VALUES (?,?,?,?,?)",
            (msg.ts_ms, msg.seq, home_id, msg.topic, json.dumps(msg.payload)),
        )
        await self._db.commit()

    async def query_window(
        self, home_id: str, start_ms: int, end_ms: int
    ) -> list[RawMessage]:
        assert self._db, "Store not opened"
        async with self._db.execute(
            "SELECT ts_ms, seq, topic, payload FROM raw_messages "
            "WHERE home_id=? AND ts_ms>=? AND ts_ms<=? ORDER BY ts_ms",
            (home_id, start_ms, end_ms),
        ) as cursor:
            rows = await cursor.fetchall()
        return [
            RawMessage(ts_ms=r[0], seq=r[1], topic=r[2], payload=json.loads(r[3]))
            for r in rows
        ]
