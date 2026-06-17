"""
Pydantic models for RuView MQTT entity payloads.

All TODO(verify) comments mark fields where the real RuView firmware output has
not yet been confirmed. Run tools/inspect_stream.py against a live puck to
resolve each one before trusting downstream detection.
"""
from __future__ import annotations

from typing import Any
import time

from pydantic import BaseModel, Field, field_validator, model_validator


class RawMessage(BaseModel):
    """Wire-format record as captured from MQTT and stored to disk."""

    ts_ms: int = Field(description="Wall-clock receive time, Unix milliseconds")
    seq: int = Field(description="Monotonic sequence number within this session")
    topic: str
    payload: dict[str, Any]

    @classmethod
    def now(cls, seq: int, topic: str, payload: dict[str, Any]) -> "RawMessage":
        return cls(ts_ms=int(time.time() * 1000), seq=seq, topic=topic, payload=payload)


# ---------------------------------------------------------------------------
# Per-entity models
# Each corresponds to one (or one class of) RuView MQTT topic.
# TODO(verify:V1) — topic names below are guesses; confirm against real output.
# ---------------------------------------------------------------------------


class PresenceEntity(BaseModel):
    """TODO(verify:V2) — presence may be bool or enum string."""

    model_config = {"extra": "ignore"}

    # TODO(verify:V2): confirm type. Using Optional[bool] as safest assumption.
    presence: bool | None = None
    # TODO(verify:V3): confirm int vs float, sentinel for unknown (-1? null?)
    person_count: int | None = Field(None, ge=-1)


class BreathingEntity(BaseModel):
    """TODO(verify:V4) — units and null sentinel unconfirmed."""

    model_config = {"extra": "ignore"}

    # TODO(verify:V4): confirm units (breaths/min assumed) and null sentinel
    breathing_rate: float | None = None


class HeartRateEntity(BaseModel):
    """TODO(verify:V5) — may not exist in current firmware."""

    model_config = {"extra": "ignore"}

    # TODO(verify:V5): confirm field exists and is on same or separate topic
    heart_rate: float | None = None


class MotionEntity(BaseModel):
    model_config = {"extra": "ignore"}

    motion: bool | None = None
    # TODO(verify:V7): confirm zones type — list[str]? dict?
    zones: list[str] | None = None


class FallEntity(BaseModel):
    """TODO(verify:V6) — edge event vs sustained state unconfirmed."""

    model_config = {"extra": "ignore"}

    # TODO(verify:V6): bool edge event assumed; confirm if retriggered/sustained
    fall: bool | None = None


class SignalQualityEntity(BaseModel):
    model_config = {"extra": "ignore"}

    # TODO(verify:V10): per-puck or per-person?
    rssi: float | None = None


# Map of topic suffix -> model class for validation dispatch.
# TODO(verify:V1): update keys once real topic namespace is confirmed.
TOPIC_SCHEMA_MAP: dict[str, type[BaseModel]] = {
    "presence": PresenceEntity,
    "breathing": BreathingEntity,
    "heartrate": HeartRateEntity,
    "motion": MotionEntity,
    "fall": FallEntity,
    "signal": SignalQualityEntity,
}


def topic_suffix(topic: str) -> str:
    """Extract the last segment of an MQTT topic path."""
    return topic.rsplit("/", 1)[-1]


def validate_payload(topic: str, payload: dict[str, Any]) -> BaseModel | None:
    """
    Validate payload against the schema for its topic suffix.
    Returns None (and caller must log) if topic is unrecognized.
    Raises ValidationError on schema mismatch — caller must not swallow it.
    """
    suffix = topic_suffix(topic)
    schema = TOPIC_SCHEMA_MAP.get(suffix)
    if schema is None:
        return None
    return schema.model_validate(payload)
