"""
Ingestion service — wires source → validate → log-harness → store.

Exposes:
  GET /health  — ok | degraded (with reason)
  GET /stats   — message counts, schema error rate
"""
from __future__ import annotations

import asyncio
import logging
import os
import sys
from contextlib import asynccontextmanager
from dataclasses import dataclass, field

from fastapi import FastAPI
from pydantic import ValidationError

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

from config import load_config
from edge.detection.validation_gate import UNVALIDATED_NO_REAL_DATA, VALIDATION_REASON
from edge.ingestion.schemas import RawMessage, validate_payload
from edge.sources.replay_source import make_source_from_config
from edge.store.sqlite_store import SqliteStore
from tools.log_harness import LogHarness

logger = logging.getLogger(__name__)
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s %(message)s",
)


@dataclass
class Stats:
    received: int = 0
    schema_errors: int = 0
    store_errors: int = 0
    unknown_topics: int = 0
    broker_connected: bool = False
    last_error: str = ""

    def schema_error_rate(self) -> float:
        if self.received == 0:
            return 0.0
        return self.schema_errors / self.received


_stats = Stats()
_cfg = load_config()


@asynccontextmanager
async def lifespan(app: FastAPI):
    store = SqliteStore(_cfg.store.db_path)
    harness = LogHarness(_cfg.log.log_dir)
    source = make_source_from_config(_cfg)

    async with store:
        harness.open()
        task = asyncio.create_task(_ingest_loop(source, store, harness))
        try:
            yield
        finally:
            task.cancel()
            harness.close()


app = FastAPI(title="Green Dot Ingestion", lifespan=lifespan)


@app.get("/health")
async def health():
    reasons = []
    if not _stats.broker_connected and _cfg.source_type == "mqtt":
        reasons.append("broker_disconnected")
    if _stats.schema_error_rate() > 0.1:
        reasons.append(f"high_schema_error_rate={_stats.schema_error_rate():.2%}")
    if _stats.store_errors > 0:
        reasons.append(f"store_errors={_stats.store_errors}")
    if UNVALIDATED_NO_REAL_DATA:
        reasons.append(f"layer1_unvalidated: {VALIDATION_REASON}")
    if reasons:
        return {"status": "degraded", "reasons": reasons}
    return {"status": "ok"}


@app.get("/stats")
async def stats():
    return {
        "received": _stats.received,
        "schema_errors": _stats.schema_errors,
        "unknown_topics": _stats.unknown_topics,
        "store_errors": _stats.store_errors,
        "schema_error_rate": f"{_stats.schema_error_rate():.2%}",
    }


async def _ingest_loop(source, store: SqliteStore, harness: "LogHarness") -> None:
    _stats.broker_connected = False
    try:
        async for msg in source.stream():
            _stats.received += 1
            _stats.broker_connected = True

            # Always write raw to disk — this is the authoritative corpus
            harness.write(msg)

            # Validate schema — drop on error, never silently pass
            try:
                validated = validate_payload(msg.topic, msg.payload)
                if validated is None:
                    _stats.unknown_topics += 1
                    logger.debug("UNKNOWN_TOPIC topic=%s", msg.topic)
            except ValidationError as exc:
                _stats.schema_errors += 1
                logger.error(
                    "SCHEMA_ERROR topic=%s seq=%d errors=%s",
                    msg.topic,
                    msg.seq,
                    exc.errors(),
                )
                continue

            # Persist to store
            try:
                await store.write_raw(msg)
            except Exception as exc:
                _stats.store_errors += 1
                logger.error("STORE_ERROR seq=%d error=%s", msg.seq, exc)
    except asyncio.CancelledError:
        pass
    except Exception as exc:
        logger.critical("INGEST_LOOP_CRASHED error=%s", exc, exc_info=True)
        _stats.last_error = str(exc)
        _stats.broker_connected = False


if __name__ == "__main__":
    import uvicorn
    uvicorn.run("edge.ingestion.service:app", host="0.0.0.0", port=8000, reload=False)
