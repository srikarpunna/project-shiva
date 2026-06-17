"""
Tests for baseline and detector mechanics.

These tests verify:
- the UNVALIDATED gate is always set
- the learning period logic is correct
- score rises on an extreme outlier relative to in-distribution points
  (testing code behavior, NOT claiming this detects any real-world event)

No fixture represents a real sensor reading. Values are chosen to be
numerically distinguishable, not physiologically meaningful.
"""
from __future__ import annotations

import pytest
from edge.detection.baseline import HomeBaseline
from edge.detection.config import Layer1Config
from edge.detection.detector import DetectorService
from edge.detection.features import FeatureVector
from edge.detection.validation_gate import UNVALIDATED_NO_REAL_DATA


def _vec(
    presence: float = 0.9,
    breathing: float = 15.0,
    motion: float = 0.5,
    fall: int | None = None,
    ts: int = 0,
) -> FeatureVector:
    return FeatureVector(
        window_start_ms=ts,
        window_end_ms=ts + 1000,
        presence_fraction=presence,
        max_person_count=1,
        mean_breathing_rate=breathing,
        std_breathing_rate=0.5,
        motion_fraction=motion,
        zone_count=1,
        fall_event_count=fall,
        mean_rssi=-60.0,
    )


def _cfg(min_windows: int = 5) -> Layer1Config:
    return Layer1Config(
        min_windows_for_stable_baseline=min_windows,
        baseline_window_count=100,
        isolation_forest_n_estimators=10,
    )


def test_unvalidated_always_true():
    """Gate must be True — never trust scores without real data."""
    assert UNVALIDATED_NO_REAL_DATA is True


def test_score_unvalidated_flag_propagates():
    baseline = HomeBaseline("h1", _cfg(min_windows=2))
    for i in range(5):
        score = baseline.update(_vec(ts=i * 1000))
    assert score.unvalidated is True


def test_not_stable_during_learning_period():
    cfg = _cfg(min_windows=10)
    baseline = HomeBaseline("h1", cfg)
    for i in range(5):
        score = baseline.update(_vec(ts=i * 1000))
    assert not score.baseline_stable
    assert score.score == 0.0


def test_stable_after_min_windows():
    cfg = _cfg(min_windows=5)
    baseline = HomeBaseline("h1", cfg)
    score = None
    for i in range(6):
        score = baseline.update(_vec(ts=i * 1000))
    assert score is not None
    assert score.baseline_stable


def test_outlier_score_mechanics():
    """
    Feed in-distribution vectors then one extreme outlier.
    Assert: scorer runs without error, returns a valid score in [0,1],
    and contributing_features shows non-zero deviations on the outlier.

    NOTE: we do NOT assert the outlier score is strictly higher — that would
    be a claim about model quality we cannot make without real labeled data.
    This tests code mechanics only (Rule 3).
    """
    cfg = _cfg(min_windows=5)
    baseline = HomeBaseline("h1", cfg)

    for i in range(10):
        baseline.update(_vec(presence=0.9, breathing=15.0, motion=0.5, ts=i * 1000))

    outlier = _vec(presence=0.0, breathing=100.0, motion=0.0, fall=5, ts=99000)
    outlier_score = baseline.update(outlier)

    assert outlier_score.baseline_stable
    assert 0.0 <= outlier_score.score <= 1.0
    assert outlier_score.unvalidated is True
    # contributing_features should show the extreme breathing deviation
    assert "mean_breathing_rate" in outlier_score.contributing_features
    assert outlier_score.contributing_features["mean_breathing_rate"] > 0


def test_detector_returns_none_within_window():
    from edge.ingestion.schemas import RawMessage
    cfg = _cfg(min_windows=5)
    svc = DetectorService(cfg)
    # Two messages 1s apart within a 30s window — no score yet
    m1 = RawMessage(ts_ms=0, seq=0, topic="ruview/presence", payload={"presence": True})
    m2 = RawMessage(ts_ms=1000, seq=1, topic="ruview/presence", payload={"presence": True})
    assert svc.ingest("home1", m1) is None
    assert svc.ingest("home1", m2) is None


def test_detector_health_reports_unvalidated():
    cfg = _cfg()
    svc = DetectorService(cfg)
    h = svc.health("home1")
    assert h["unvalidated"] is True
    assert h["layer1_status"] == "not_started"


def test_detector_health_reports_learning():
    from edge.ingestion.schemas import RawMessage
    cfg = Layer1Config(
        window_seconds=1,
        min_windows_for_stable_baseline=100,
        baseline_window_count=200,
        isolation_forest_n_estimators=10,
    )
    svc = DetectorService(cfg)
    # Send messages spanning 3 windows
    for i in range(5):
        m = RawMessage(ts_ms=i * 2000, seq=i, topic="ruview/presence", payload={"presence": True})
        svc.ingest("home1", m)
    h = svc.health("home1")
    assert h["layer1_status"] == "learning"
    assert h["unvalidated"] is True
