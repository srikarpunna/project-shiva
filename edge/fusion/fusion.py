"""
Layer 2 fusion — combines Layer 1 AnomalyScore + raw signal flags into
one calibrated confidence and a discrete state (green/yellow/red).

Fusion is mode-aware: Calm and Guard use different operating points.
Operating points are config fields, not hardcoded — they are set by a human
after reading eval rig output.

Output is always tagged with unvalidated=True while validation gate is set.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Literal

from edge.detection.baseline import AnomalyScore
from edge.detection.validation_gate import UNVALIDATED_NO_REAL_DATA, VALIDATION_REASON
from edge.fusion.calibration import CalibrationNotFitted, ScoreCalibrator
from edge.fusion.config import FusionConfig, ModeConfig

logger = logging.getLogger(__name__)

Mode = Literal["calm", "guard"]
State = Literal["green", "yellow", "red", "unknown"]


@dataclass(frozen=True)
class FusionResult:
    """Output of one fusion call."""

    confidence: float
    """
    Calibrated probability [0,1] that something is genuinely wrong.
    Only meaningful after calibrator is fitted on real labeled data.
    """

    state: State
    """Discrete state: green / yellow / red / unknown."""

    mode: Mode
    """The operating mode this result was computed under."""

    raw_anomaly_score: float
    """Layer 1 score before calibration, for logging/explainability."""

    contributing_features: dict[str, float]
    """Passed through from Layer 1 AnomalyScore."""

    baseline_stable: bool
    """False during Layer 1 learning period."""

    unvalidated: bool
    """True until validation gate is cleared via the eval rig."""

    cannot_certify: bool
    """True when the system lacks sufficient data to produce a valid result."""

    reason: str
    """Human-readable explanation of the state, for logging."""


class FusionService:
    """
    Stateful per-home fusion service.

    Manages calibrators per home, per mode. Tracks consecutive yellow windows
    for persistence logic (caller is responsible for escalation).
    """

    def __init__(self, cfg: FusionConfig) -> None:
        self._cfg = cfg
        # calibrators[(home_id, mode)] = ScoreCalibrator
        self._calibrators: dict[tuple[str, str], ScoreCalibrator] = {}
        # consecutive yellow window counts per (home_id, mode)
        self._yellow_streak: dict[tuple[str, str], int] = {}

    def fuse(
        self,
        home_id: str,
        mode: Mode,
        layer1_score: AnomalyScore,
        fall_event_present: bool = False,
    ) -> FusionResult:
        """
        Fuse Layer 1 score + signal flags into a calibrated FusionResult.

        Returns cannot_certify=True when:
        - Layer 1 baseline is not yet stable
        - Calibrator is not fitted (no real labeled data yet)
        - UNVALIDATED_NO_REAL_DATA is set

        In all cannot_certify cases, state = 'unknown'.
        """
        key = (home_id, mode)
        mode_cfg: ModeConfig = self._cfg.calm if mode == "calm" else self._cfg.guard

        # --- cannot-certify cases ---

        if not layer1_score.baseline_stable:
            return FusionResult(
                confidence=0.0,
                state="unknown",
                mode=mode,
                raw_anomaly_score=layer1_score.score,
                contributing_features={},
                baseline_stable=False,
                unvalidated=UNVALIDATED_NO_REAL_DATA,
                cannot_certify=True,
                reason="Layer 1 baseline not yet stable — still learning home's normal.",
            )

        if UNVALIDATED_NO_REAL_DATA:
            return FusionResult(
                confidence=0.0,
                state="unknown",
                mode=mode,
                raw_anomaly_score=layer1_score.score,
                contributing_features=layer1_score.contributing_features,
                baseline_stable=True,
                unvalidated=True,
                cannot_certify=True,
                reason=f"Layer 1 unvalidated. {VALIDATION_REASON}",
            )

        calibrator = self._calibrators.get(key)
        if calibrator is None or not calibrator.is_fitted:
            return FusionResult(
                confidence=0.0,
                state="unknown",
                mode=mode,
                raw_anomaly_score=layer1_score.score,
                contributing_features=layer1_score.contributing_features,
                baseline_stable=True,
                unvalidated=False,
                cannot_certify=True,
                reason="Calibrator not fitted — run eval rig on real labeled data first.",
            )

        # --- calibrated path ---

        try:
            confidence = calibrator.calibrate(layer1_score.score)
        except CalibrationNotFitted as exc:
            return FusionResult(
                confidence=0.0,
                state="unknown",
                mode=mode,
                raw_anomaly_score=layer1_score.score,
                contributing_features=layer1_score.contributing_features,
                baseline_stable=True,
                unvalidated=False,
                cannot_certify=True,
                reason=str(exc),
            )

        # Apply fall signal boost (additive, capped at 1.0)
        # TODO(verify): fall_signal_weight set from eval rig FNR on labeled falls
        if fall_event_present and self._cfg.fall_signal_weight > 0:
            confidence = min(1.0, confidence + self._cfg.fall_signal_weight)

        # Map confidence → state using mode operating points
        # TODO(verify): thresholds set by human reading eval rig FNR/FPR curves
        state = _apply_thresholds(confidence, mode_cfg)

        # Track yellow streak for persistence
        if state == "yellow":
            self._yellow_streak[key] = self._yellow_streak.get(key, 0) + 1
        else:
            self._yellow_streak[key] = 0

        return FusionResult(
            confidence=confidence,
            state=state,
            mode=mode,
            raw_anomaly_score=layer1_score.score,
            contributing_features=layer1_score.contributing_features,
            baseline_stable=True,
            unvalidated=False,
            cannot_certify=False,
            reason=f"confidence={confidence:.3f} state={state} mode={mode}",
        )

    def register_calibrator(
        self, home_id: str, mode: Mode, calibrator: ScoreCalibrator
    ) -> None:
        """Install a fitted calibrator for a (home_id, mode) pair."""
        if not calibrator.is_fitted:
            raise ValueError("Cannot register unfitted calibrator.")
        self._calibrators[(home_id, mode)] = calibrator

    def yellow_streak(self, home_id: str, mode: Mode) -> int:
        return self._yellow_streak.get((home_id, mode), 0)


def _apply_thresholds(confidence: float, cfg: ModeConfig) -> State:
    """
    Map confidence to state using configured thresholds.

    Both thresholds default to 0.0 (placeholder) so they never accidentally
    fire before being set from eval rig output. With both at 0.0, any positive
    confidence → red — intentionally conservative until a human sets real values.
    """
    if cfg.red_threshold > 0.0 and confidence >= cfg.red_threshold:
        return "red"
    if cfg.yellow_threshold > 0.0 and confidence >= cfg.yellow_threshold:
        return "yellow"
    if cfg.yellow_threshold == 0.0 and cfg.red_threshold == 0.0:
        # Thresholds not configured — refuse to emit a state
        return "unknown"
    return "green"
