"""
Logging harness — captures every raw MQTT message to JSONL on disk.

File-per-day, rotated at midnight UTC.
Format per line: {"ts_ms": int, "seq": int, "topic": str, "payload": {...}}

This is the authoritative replay corpus. Nothing downstream is trustworthy
without real signal captured here first.

Usage (standalone):
    python tools/log_harness.py [--broker localhost] [--port 1883] [--out data/logs]
"""
from __future__ import annotations

import argparse
import asyncio
import json
import logging
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from edge.ingestion.schemas import RawMessage

logger = logging.getLogger(__name__)


def _log_path(log_dir: str, ts_ms: int) -> Path:
    date = datetime.fromtimestamp(ts_ms / 1000, tz=timezone.utc).strftime("%Y-%m-%d")
    return Path(log_dir) / f"raw_{date}.jsonl"


class LogHarness:
    """Write RawMessages to rotating daily JSONL files."""

    def __init__(self, log_dir: str) -> None:
        self._log_dir = log_dir
        self._current_path: Path | None = None
        self._fh = None

    def open(self) -> None:
        Path(self._log_dir).mkdir(parents=True, exist_ok=True)
        logger.info("LogHarness ready dir=%s", self._log_dir)

    def write(self, msg: RawMessage) -> None:
        target = _log_path(self._log_dir, msg.ts_ms)
        if target != self._current_path:
            self._rotate(target)
        assert self._fh
        self._fh.write(msg.model_dump_json() + "\n")
        self._fh.flush()

    def _rotate(self, new_path: Path) -> None:
        if self._fh:
            self._fh.close()
        self._current_path = new_path
        self._fh = new_path.open("a", encoding="utf-8")
        logger.info("LogHarness rotated file=%s", new_path)

    def close(self) -> None:
        if self._fh:
            self._fh.close()
            self._fh = None


# ------------------------------------------------------------------
# Standalone capture mode
# ------------------------------------------------------------------

async def _capture(broker: str, port: int, topic: str, log_dir: str) -> None:
    from config.base import MqttConfig
    from edge.sources.mqtt_source import MqttSource

    cfg = MqttConfig(host=broker, port=port, topic_prefix=topic)
    harness = LogHarness(log_dir)
    harness.open()

    seq = 0
    print(f"Connecting to {broker}:{port} — capturing all messages to {log_dir}/")
    print("Ctrl-C to stop.\n")

    async with MqttSource(cfg) as source:
        async for msg in source.stream():
            seq += 1
            harness.write(msg)
            print(f"[{seq:06d}] {msg.topic}  {json.dumps(msg.payload)[:120]}")


def main() -> None:
    logging.basicConfig(level=logging.WARNING)
    p = argparse.ArgumentParser(description="Capture all MQTT messages to JSONL logs")
    p.add_argument("--broker", default="localhost")
    p.add_argument("--port", type=int, default=1883)
    p.add_argument("--topic", default="ruview/#",
                   help="TODO(verify:V1) — update once real topic namespace confirmed")
    p.add_argument("--out", default="data/logs")
    args = p.parse_args()
    try:
        asyncio.run(_capture(args.broker, args.port, args.topic, args.out))
    except KeyboardInterrupt:
        print("\nCapture stopped.")


if __name__ == "__main__":
    main()
