from __future__ import annotations

import asyncio
import json
import logging
import os
from pathlib import Path
from typing import AsyncIterator

from config.base import AppConfig
from edge.ingestion.schemas import RawMessage
from edge.sources.base import Source

logger = logging.getLogger(__name__)

_BLOCKED_IN_PROD = "ReplaySource cannot run in production. Set APP_ENV=development."


class ReplaySource(Source):
    """
    Replays a real captured JSONL log file at configurable speed.

    SAFETY GUARD: raises RuntimeError on import when APP_ENV=production.
    This is enforced in __init__, not just documented.
    """

    def __init__(self, log_path: str | Path, speed: float = 1.0, env: str = "development") -> None:
        if env == "production":
            raise RuntimeError(_BLOCKED_IN_PROD)
        self._log_path = Path(log_path)
        self._speed = speed

    async def stream(self) -> AsyncIterator[RawMessage]:
        if not self._log_path.exists():
            raise FileNotFoundError(f"Replay log not found: {self._log_path}")

        logger.info(
            "ReplaySource starting log=%s speed=%.1f×",
            self._log_path,
            self._speed,
        )

        prev_ts_ms: int | None = None

        with self._log_path.open() as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    record = json.loads(line)
                    msg = RawMessage.model_validate(record)
                except Exception as exc:
                    logger.warning("ReplaySource skip malformed line: %s", exc)
                    continue

                if prev_ts_ms is not None:
                    gap_ms = msg.ts_ms - prev_ts_ms
                    if gap_ms > 0:
                        await asyncio.sleep(gap_ms / 1000.0 / self._speed)

                prev_ts_ms = msg.ts_ms
                yield msg

        logger.info("ReplaySource exhausted log=%s", self._log_path)


def make_source_from_config(cfg: AppConfig) -> Source:
    """Factory — returns live or replay source based on config."""
    if cfg.source_type == "replay":
        if not cfg.replay_log_path:
            raise ValueError("replay_log_path must be set when source_type=replay")
        return ReplaySource(
            log_path=cfg.replay_log_path,
            speed=cfg.replay_speed,
            env=cfg.env,
        )
    from edge.sources.mqtt_source import MqttSource
    return MqttSource(cfg.mqtt)
