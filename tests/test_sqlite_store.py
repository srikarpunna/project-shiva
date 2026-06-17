import pytest
import pytest_asyncio
import tempfile
import os

from edge.ingestion.schemas import RawMessage
from edge.store.sqlite_store import SqliteStore


@pytest.fixture
async def store(tmp_path):
    s = SqliteStore(str(tmp_path / "test.db"))
    async with s:
        yield s


@pytest.mark.asyncio
async def test_write_and_query(store):
    msg = RawMessage(ts_ms=1000, seq=1, topic="ruview/presence", payload={"presence": True})
    await store.write_raw(msg, home_id="home1")

    results = await store.query_window("home1", start_ms=0, end_ms=2000)
    assert len(results) == 1
    assert results[0].topic == "ruview/presence"
    assert results[0].payload == {"presence": True}


@pytest.mark.asyncio
async def test_query_window_bounds(store):
    for i, ts in enumerate([1000, 2000, 3000, 4000]):
        msg = RawMessage(ts_ms=ts, seq=i, topic="t", payload={})
        await store.write_raw(msg, home_id="home1")

    results = await store.query_window("home1", start_ms=1500, end_ms=3500)
    assert len(results) == 2
    assert [r.ts_ms for r in results] == [2000, 3000]


@pytest.mark.asyncio
async def test_home_isolation(store):
    for home in ("home1", "home2"):
        msg = RawMessage(ts_ms=1000, seq=1, topic="t", payload={"home": home})
        await store.write_raw(msg, home_id=home)

    r1 = await store.query_window("home1", 0, 9999)
    r2 = await store.query_window("home2", 0, 9999)
    assert r1[0].payload["home"] == "home1"
    assert r2[0].payload["home"] == "home2"
