"""
Tests for Layer 2 fusion mechanics.

Rules:
- No test asserts the fusion "correctly detects" anything real.
- No synthetic sensor data. Fixtures are minimal typed structs.
- Tests verify: state transitions fire at configured cutoffs,
  calibration math is monotonic, NO_DATA path returns cannot_certify.
"""
from __future__ import annotations

import pytest
from edge.detection.baseline import AnomalyScore
from edge.detection.validation_gate import UNVALIDATED_NO_REAL_DATA
from edge.fusion.calibration import CalibrationNotFitted, ScoreCalibrator
from edge.fusion.config import FusionConfig, ModeConfig
from edge.fusion.fusion import FusionService, _apply_thresholds


# --- helpers ---

def _score(raw: float = 0.5, stable: bool = True) -> AnomalyScore:
    return AnomalyScore(
        score=raw,
        contributing_features={},
        baseline_stable=stable,
        windows_seen=100,
        unvalidated=UNVALIDATED_NO_REAL_DATA,
    )


def _cfg(yellow: float = 0.3, red: float = 0.7) -> FusionConfig:
    mode = ModeConfig(yellow_threshold=yellow, red_threshold=red)
    return FusionConfig(calm=mode, guard=mode)


# --- validation gate ---

def test_unvalidated_returns_cannot_certify():
    """While UNVALIDATED_NO_REAL_DATA=True, every fuse() returns cannot_certify."""
    assert UNVALIDATED_NO_REAL_DATA is True
    svc = FusionService(_cfg())
    result = svc.fuse("h1", "calm", _score(0.9, stable=True))
    assert result.cannot_certify is True
    assert result.state == "unknown"
    assert result.unvalidated is True


def test_unstable_baseline_returns_cannot_certify():
    """Unstable baseline → cannot_certify before UNVALIDATED check."""
    svc = FusionService(_cfg())
    result = svc.fuse("h1", "calm", _score(0.9, stable=False))
    assert result.cannot_certify is True
    assert result.baseline_stable is False


# --- state transitions (config mechanics) ---

def test_apply_thresholds_green():
    cfg = ModeConfig(yellow_threshold=0.3, red_threshold=0.7)
    assert _apply_thresholds(0.1, cfg) == "green"


def test_apply_thresholds_yellow():
    cfg = ModeConfig(yellow_threshold=0.3, red_threshold=0.7)
    assert _apply_thresholds(0.5, cfg) == "yellow"


def test_apply_thresholds_red():
    cfg = ModeConfig(yellow_threshold=0.3, red_threshold=0.7)
    assert _apply_thresholds(0.8, cfg) == "red"


def test_apply_thresholds_unconfigured_returns_unknown():
    """Both thresholds at 0.0 (placeholder) → refuse to emit state."""
    cfg = ModeConfig(yellow_threshold=0.0, red_threshold=0.0)
    assert _apply_thresholds(0.5, cfg) == "unknown"


def test_red_takes_priority_over_yellow():
    cfg = ModeConfig(yellow_threshold=0.3, red_threshold=0.3)
    assert _apply_thresholds(0.3, cfg) == "red"


# --- calibration mechanics ---

def test_calibrator_unfitted_raises():
    cal = ScoreCalibrator()
    with pytest.raises(CalibrationNotFitted):
        cal.calibrate(0.5)


def test_calibrator_rejects_empty_data():
    cal = ScoreCalibrator()
    with pytest.raises(ValueError, match="no data"):
        cal.fit([], [])


def test_calibrator_rejects_single_class():
    cal = ScoreCalibrator()
    with pytest.raises(ValueError, match="both 0 and 1"):
        cal.fit([0.1, 0.2, 0.3], [0, 0, 0])


def test_calibrator_platt_output_in_range():
    """After fitting, output is in [0,1]. Does not test detection quality."""
    cal = ScoreCalibrator(method="platt")
    # Minimal two-class hand-written fixture
    scores = [0.1, 0.2, 0.8, 0.9]
    labels = [0,   0,   1,   1]
    cal.fit(scores, labels)
    assert cal.is_fitted
    for s in [0.0, 0.3, 0.7, 1.0]:
        out = cal.calibrate(s)
        assert 0.0 <= out <= 1.0, f"calibrate({s}) = {out} out of range"


def test_calibrator_platt_monotonic():
    """Higher input → higher or equal output. Tests math, not detection."""
    cal = ScoreCalibrator(method="platt")
    cal.fit([0.1, 0.2, 0.8, 0.9], [0, 0, 1, 1])
    outputs = [cal.calibrate(s) for s in [0.1, 0.3, 0.5, 0.7, 0.9]]
    for i in range(len(outputs) - 1):
        assert outputs[i] <= outputs[i + 1] + 1e-6, (
            f"Non-monotonic at {i}: {outputs[i]} > {outputs[i+1]}"
        )


def test_calibrator_isotonic_output_in_range():
    cal = ScoreCalibrator(method="isotonic")
    cal.fit([0.1, 0.2, 0.8, 0.9], [0, 0, 1, 1])
    for s in [0.0, 0.5, 1.0]:
        out = cal.calibrate(s)
        assert 0.0 <= out <= 1.0


def test_register_unfitted_calibrator_raises():
    svc = FusionService(_cfg())
    cal = ScoreCalibrator()
    with pytest.raises(ValueError, match="unfitted"):
        svc.register_calibrator("h1", "calm", cal)


# --- eval rig NO_DATA path ---

def test_eval_rig_no_labels_returns_no_data(tmp_path):
    from edge.fusion.eval_rig import run
    label_path = tmp_path / "empty.labels.jsonl"
    scored_path = tmp_path / "scored.jsonl"
    label_path.touch()
    scored_path.touch()
    result = run(label_path, scored_path)
    assert result.status == "NO_DATA"
    assert result.brier_score is None
    assert result.threshold_curve == []


def test_eval_rig_no_scored_windows_returns_no_data(tmp_path):
    from edge.fusion.eval_rig import run
    import json
    label_path = tmp_path / "test.labels.jsonl"
    scored_path = tmp_path / "scored.jsonl"
    with label_path.open("w") as fh:
        fh.write(json.dumps({"ts_ms": 1000, "label": "fall", "note": ""}) + "\n")
    scored_path.touch()
    result = run(label_path, scored_path)
    assert result.status == "NO_DATA"


def test_eval_rig_insufficient_labels_returns_insufficient(tmp_path):
    from edge.fusion.eval_rig import run, MIN_LABELED_EVENTS
    import json
    label_path = tmp_path / "few.labels.jsonl"
    scored_path = tmp_path / "scored.jsonl"
    # Write fewer than MIN_LABELED_EVENTS labels
    with label_path.open("w") as fh:
        for i in range(MIN_LABELED_EVENTS - 1):
            fh.write(json.dumps({"ts_ms": i * 1000, "label": "fall", "note": ""}) + "\n")
    with scored_path.open("w") as fh:
        for i in range(MIN_LABELED_EVENTS - 1):
            fh.write(json.dumps({"ts_ms": i * 1000, "raw_score": 0.8, "calibrated_confidence": 0.8}) + "\n")
    result = run(label_path, scored_path)
    assert result.status == "INSUFFICIENT_LABELS"
    assert result.brier_score is None


def test_eval_rig_complete_with_minimal_real_data(tmp_path):
    """
    Mechanics test: rig completes and reports metrics when given sufficient
    labeled + scored data. Values are hand-written fixtures, not real sensor data.
    Does NOT assert any metric meets any target — that is a human decision.
    """
    from edge.fusion.eval_rig import run, MIN_LABELED_EVENTS
    import json

    label_path = tmp_path / "labels.jsonl"
    scored_path = tmp_path / "scored.jsonl"

    labels = (
        [{"ts_ms": i * 1000, "label": "fall", "note": ""} for i in range(5)] +
        [{"ts_ms": (i + 5) * 1000, "label": "normal_activity", "note": ""} for i in range(5)]
    )
    # Ensure MIN_LABELED_EVENTS is satisfied
    while len(labels) < MIN_LABELED_EVENTS:
        labels.append({"ts_ms": len(labels) * 1000, "label": "normal_stillness", "note": ""})

    with label_path.open("w") as fh:
        for l in labels:
            fh.write(json.dumps(l) + "\n")

    with scored_path.open("w") as fh:
        for i in range(len(labels)):
            fh.write(json.dumps({"ts_ms": i * 1000, "raw_score": 0.5, "calibrated_confidence": 0.5}) + "\n")

    result = run(label_path, scored_path)
    assert result.status == "COMPLETE"
    assert result.n_positive > 0
    assert result.n_negative > 0
    assert len(result.threshold_curve) > 0
    assert result.brier_score is not None
    assert 0.0 <= result.brier_score <= 1.0
    # Confirm the summary tells the human to set thresholds, not the rig
    assert "set operating point" in result.summary.lower() or "next steps" in result.summary.lower()
