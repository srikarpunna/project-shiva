"""
Layer 2 fusion configuration.

All thresholds are TODO(verify) — set from eval rig metrics, not guessed.
Per-mode operating points are separate so Calm and Guard tuning is explicit.
"""
from __future__ import annotations

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class ModeConfig(BaseSettings):
    """
    Operating point for one mode (Calm or Guard).

    yellow_threshold and red_threshold are chosen by a human reading eval rig
    output (false-negative rate, false-positive rate). Do not set them from vibes.
    """
    model_config = SettingsConfigDict(env_prefix="")

    # TODO(verify): set after reading eval rig FNR/FPR curves on real labeled data.
    yellow_threshold: float = Field(
        0.0,  # placeholder — not a real operating point
        description="Confidence >= this → yellow state. UNVERIFIED — set from eval rig.",
        ge=0.0,
        le=1.0,
    )
    # TODO(verify): set after reading eval rig FNR/FPR curves on real labeled data.
    red_threshold: float = Field(
        0.0,  # placeholder — not a real operating point
        description="Confidence >= this → red state. UNVERIFIED — set from eval rig.",
        ge=0.0,
        le=1.0,
    )
    # TODO(verify): how many consecutive windows at yellow before escalating to possible-red?
    yellow_persistence_windows: int = Field(
        3,
        description="Consecutive yellow windows before soft-confirm. UNVERIFIED.",
    )


class FusionConfig(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="FUSION_")

    calm: ModeConfig = Field(
        default_factory=lambda: ModeConfig(
            yellow_threshold=0.0,
            red_threshold=0.0,
            yellow_persistence_windows=3,
        )
    )
    guard: ModeConfig = Field(
        default_factory=lambda: ModeConfig(
            yellow_threshold=0.0,
            red_threshold=0.0,
            yellow_persistence_windows=1,
        )
    )

    # TODO(verify): Platt scaling or isotonic regression?
    # Cannot choose until we have a calibration curve from real labeled data.
    calibration_method: str = Field(
        "platt",
        description="Calibration method: 'platt' or 'isotonic'. UNVERIFIED.",
    )

    # Fall signal weight in fusion — how much to boost confidence on a fall event.
    # TODO(verify): set from eval rig FNR on labeled fall events.
    fall_signal_weight: float = Field(
        0.0,
        description="Additive boost to confidence when fall event present. UNVERIFIED.",
        ge=0.0,
        le=1.0,
    )
