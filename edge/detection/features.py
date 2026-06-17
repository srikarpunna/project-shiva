"""
Feature extraction for Layer 1.

Takes a window of RawMessages and produces a typed FeatureVector.
All per-field logic is isolated here so it can be unit-tested independently
of the anomaly model.

NOTE: Field presence is optional throughout — missing sensor values yield None.
The scorer must handle sparse vectors. We do NOT impute with guesses.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Sequence

from edge.ingestion.schemas import RawMessage, topic_suffix


@dataclass(frozen=True)
class FeatureVector:
    """
    Numeric summary of one sliding window.

    All fields are Optional — a missing value means the sensor did not
    report in this window, NOT that the value was zero or normal.
    The anomaly scorer must treat None as 'unknown', never as 'ok'.
    """

    window_start_ms: int
    window_end_ms: int

    # Presence
    presence_fraction: float | None  # fraction of msgs where presence=True
    max_person_count: int | None     # peak person_count seen in window

    # Breathing
    mean_breathing_rate: float | None
    std_breathing_rate: float | None  # variability within window

    # Motion
    motion_fraction: float | None    # fraction of msgs where motion=True
    zone_count: int | None           # distinct zones with activity

    # Fall — edge events in window
    fall_event_count: int | None

    # Signal quality
    mean_rssi: float | None

    # Derived: consecutive no-motion windows fed in from the rolling context
    # (filled by the baseline learner, not here)
    consecutive_still_windows: int | None = None


def extract(
    messages: Sequence[RawMessage],
    window_start_ms: int,
    window_end_ms: int,
) -> FeatureVector:
    """
    Extract a FeatureVector from a window of RawMessages.

    Messages should already be filtered to [window_start_ms, window_end_ms].
    Returns a vector with all-None fields if messages is empty.
    """
    if not messages:
        return FeatureVector(
            window_start_ms=window_start_ms,
            window_end_ms=window_end_ms,
            presence_fraction=None,
            max_person_count=None,
            mean_breathing_rate=None,
            std_breathing_rate=None,
            motion_fraction=None,
            zone_count=None,
            fall_event_count=None,
            mean_rssi=None,
        )

    presence_vals: list[bool] = []
    person_counts: list[int] = []
    breathing_rates: list[float] = []
    motion_vals: list[bool] = []
    zones_seen: set[str] = set()
    fall_count = 0
    rssi_vals: list[float] = []

    for msg in messages:
        suffix = topic_suffix(msg.topic)
        p = msg.payload

        if suffix == "presence":
            if isinstance(p.get("presence"), bool):
                presence_vals.append(p["presence"])
            if isinstance(p.get("person_count"), int) and p["person_count"] >= 0:
                person_counts.append(p["person_count"])

        elif suffix == "breathing":
            br = p.get("breathing_rate")
            if isinstance(br, (int, float)) and br is not None:
                breathing_rates.append(float(br))

        elif suffix == "motion":
            if isinstance(p.get("motion"), bool):
                motion_vals.append(p["motion"])
            zones = p.get("zones")
            if isinstance(zones, list):
                zones_seen.update(z for z in zones if isinstance(z, str))

        elif suffix == "fall":
            if p.get("fall") is True:
                fall_count += 1

        elif suffix == "signal":
            rssi = p.get("rssi")
            if isinstance(rssi, (int, float)) and rssi is not None:
                rssi_vals.append(float(rssi))

    def _mean(vals: list[float]) -> float | None:
        return sum(vals) / len(vals) if vals else None

    def _std(vals: list[float]) -> float | None:
        if len(vals) < 2:
            return None
        m = sum(vals) / len(vals)
        variance = sum((v - m) ** 2 for v in vals) / len(vals)
        return variance ** 0.5

    return FeatureVector(
        window_start_ms=window_start_ms,
        window_end_ms=window_end_ms,
        presence_fraction=(
            sum(presence_vals) / len(presence_vals) if presence_vals else None
        ),
        max_person_count=max(person_counts) if person_counts else None,
        mean_breathing_rate=_mean(breathing_rates),
        std_breathing_rate=_std(breathing_rates),
        motion_fraction=(
            sum(motion_vals) / len(motion_vals) if motion_vals else None
        ),
        zone_count=len(zones_seen) if zones_seen else None,
        fall_event_count=fall_count if fall_count > 0 else None,
        mean_rssi=_mean(rssi_vals),
    )
