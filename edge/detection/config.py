"""
Layer 1 configuration.

Every value that will eventually come from real data is a TODO(verify).
Nothing here is a tuned number — these are typed placeholders.
"""
from __future__ import annotations

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Layer1Config(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="L1_")

    # ------------------------------------------------------------------
    # Window / baseline parameters
    # TODO(verify) all of these after analyzing real captured logs.
    # ------------------------------------------------------------------

    # TODO(verify): how many seconds of history form one feature window?
    # Will depend on actual message rate (V8) and signal stability.
    window_seconds: int = Field(
        30,
        description="Sliding window length for feature extraction (seconds). UNVERIFIED.",
    )

    # TODO(verify): how many windows of history to use for the per-home baseline?
    # Too short → noisy baseline. Too long → slow to adapt. Needs real data to tune.
    baseline_window_count: int = Field(
        1440,
        description="Number of windows retained for rolling baseline (≈1 day at 1/30s). UNVERIFIED.",
    )

    # TODO(verify): minimum windows required before baseline is considered stable.
    # Model should report 'learning' state until this many windows are seen.
    min_windows_for_stable_baseline: int = Field(
        288,
        description="Minimum windows before baseline is trusted (≈2.4h at 1/30s). UNVERIFIED.",
    )

    # ------------------------------------------------------------------
    # IsolationForest parameters
    # TODO(verify) after running on real data + reviewing contamination empirically.
    # ------------------------------------------------------------------

    # TODO(verify): contamination = expected fraction of anomalous windows.
    # Cannot be set without real labeled data. sklearn default (0.1) is a guess.
    isolation_forest_contamination: float = Field(
        0.05,
        description="IsolationForest contamination param. UNVERIFIED — set from eval rig.",
    )

    isolation_forest_n_estimators: int = Field(
        100,
        description="IsolationForest n_estimators. UNVERIFIED.",
    )

    # ------------------------------------------------------------------
    # Feature extraction parameters
    # TODO(verify) after inspecting real signal characteristics.
    # ------------------------------------------------------------------

    # TODO(verify): breathing rate range considered physiologically plausible.
    # Values outside this range may indicate sensor noise, not anomaly.
    breathing_rate_min: float = Field(
        6.0,
        description="Min plausible breathing rate (breaths/min). UNVERIFIED.",
    )
    breathing_rate_max: float = Field(
        40.0,
        description="Max plausible breathing rate (breaths/min). UNVERIFIED.",
    )

    # TODO(verify): stillness threshold — how many consecutive windows with no
    # motion before it's flagged as a feature. Depends on real household patterns.
    stillness_window_count: int = Field(
        6,
        description="Consecutive no-motion windows to flag as stillness feature. UNVERIFIED.",
    )
