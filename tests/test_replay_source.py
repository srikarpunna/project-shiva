import json
import pytest
import tempfile
from pathlib import Path

from edge.ingestion.schemas import RawMessage
from edge.sources.replay_source import ReplaySource


def _write_log(path: Path, messages: list[RawMessage]) -> None:
    with path.open("w") as fh:
        for msg in messages:
            fh.write(msg.model_dump_json() + "\n")


@pytest.mark.asyncio
async def test_replay_yields_messages(tmp_path):
    msgs = [
        RawMessage(ts_ms=1000 * i, seq=i, topic=f"t/{i}", payload={"i": i})
        for i in range(5)
    ]
    log_path = tmp_path / "test.jsonl"
    _write_log(log_path, msgs)

    source = ReplaySource(log_path=log_path, speed=1000.0, env="development")
    collected = []
    async for msg in source.stream():
        collected.append(msg)

    assert len(collected) == 5
    assert [m.seq for m in collected] == list(range(5))


@pytest.mark.asyncio
async def test_replay_blocked_in_production():
    with pytest.raises(RuntimeError, match="production"):
        ReplaySource(log_path="any.jsonl", env="production")


@pytest.mark.asyncio
async def test_replay_missing_file(tmp_path):
    source = ReplaySource(log_path=tmp_path / "nonexistent.jsonl", speed=1000.0, env="development")
    with pytest.raises(FileNotFoundError):
        async for _ in source.stream():
            pass


@pytest.mark.asyncio
async def test_replay_skips_malformed_lines(tmp_path):
    log_path = tmp_path / "mixed.jsonl"
    good = RawMessage(ts_ms=1000, seq=0, topic="t", payload={"ok": True})
    with log_path.open("w") as fh:
        fh.write(good.model_dump_json() + "\n")
        fh.write("NOT JSON\n")
        fh.write(good.model_copy(update={"seq": 1}).model_dump_json() + "\n")

    source = ReplaySource(log_path=log_path, speed=1000.0, env="development")
    collected = [m async for m in source.stream()]
    assert len(collected) == 2
