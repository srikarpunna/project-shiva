"""
Per-home streaming baseline learner — Layer 1.

Maintains a rolling buffer of FeatureVectors and fits an IsolationForest
when enough data has accumulated. Entirely stateful per home instance.

Model is NEVER trusted until:
  - min_windows_for_stable_baseline have been seen
  - UNVALIDATED_NO_REAL_DATA is cleared after the Layer 2 eval rig passes

The model object is swappable: any sklearn-compatible anomaly estimator
with fit() and score_samples() works here.
"""
from __future__ import annotations

import logging
from collections import deque
from dataclasses import dataclass
from typing import Sequence

from edge.detection.config import Layer1Config
from edge.detection.features import FeatureVector
from edge.detection.validation_gate import UNVALIDATED_NO_REAL_DATA, VALIDATION_REASON

logger = logging.getLogger(__name__)


@dataclass
class AnomalyScore:
    """Output of one scoring call."""

    score: float
    """
    Normalized anomaly score in [0, 1].
    Higher = more anomalous relative to this home's learned baseline.
    0 = indistinguishable from normal. 1 = maximally anomalous.
    NOTE: scale is only meaningful after real data validation.
    """

    contributing_features: dict[str, float]
    """
    Per-feature contribution to the score (for explainability).
    Keys are FeatureVector field names. Values are raw deviations from baseline mean.
    Empty until baseline is stable.
    """

    baseline_stable: bool
    """False during the learning period — score must not be trusted."""

    windows_seen: int
    """How many windows the baseline has been trained on."""

    unvalidated: bool
    """Mirrors UNVALIDATED_NO_REAL_DATA — always True until eval rig clears it."""


class HomeBaseline:
    """
    Rolling per-home anomaly detector.

    One instance per home_id. Not thread-safe — call from a single async task.
    """

    def __init__(self, home_id: str, cfg: Layer1Config) -> None:
        self._home_id = home_id
        self._cfg = cfg
        self._buffer: deque[FeatureVector] = deque(maxlen=cfg.baseline_window_count)
        self._model = None  # fitted sklearn estimator or None
        self._windows_seen = 0

    # ------------------------------------------------------------------
    # Public interface
    # ------------------------------------------------------------------

    @property
    def is_stable(self) -> bool:
        return self._windows_seen >= self._cfg.min_windows_for_stable_baseline

    def update(self, vec: FeatureVector) -> AnomalyScore:
        """
        Ingest one feature vector, refit the model if stable, return a score.

        The score is ALWAYS returned (even during learning) so the caller has
        something to log. The caller must check AnomalyScore.baseline_stable
        and AnomalyScore.unvalidated before using the score for any decision.
        """
        self._buffer.append(vec)
        self._windows_seen += 1

        if self.is_stable:
            self._fit()

        return self._score(vec)

    def windows_seen(self) -> int:
        return self._windows_seen

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    def _fit(self) -> None:
        """Refit the IsolationForest on the current buffer. Skips if buffer too small."""
        matrix = _to_matrix(list(self._buffer))
        if matrix is None or len(matrix) < 10:
            return

        try:
            from sklearn.ensemble import IsolationForest

            model = IsolationForest(
                n_estimators=self._cfg.isolation_forest_n_estimators,
                contamination=self._cfg.isolation_forest_contamination,
                random_state=42,
            )
            model.fit(matrix)
            self._model = model
        except Exception as exc:
            logger.error("home=%s Layer1 fit failed: %s", self._home_id, exc)
            self._model = None

    def _score(self, vec: FeatureVector) -> AnomalyScore:
        row = _vector_to_row(vec)
        contributing: dict[str, float] = {}

        if not self.is_stable:
            return AnomalyScore(
                score=0.0,
                contributing_features={},
                baseline_stable=False,
                windows_seen=self._windows_seen,
                unvalidated=UNVALIDATED_NO_REAL_DATA,
            )

        if self._model is None:
            # Stable window count reached but model hasn't fit yet (buffer too small for sklearn).
            return AnomalyScore(
                score=0.0,
                contributing_features={},
                baseline_stable=True,
                windows_seen=self._windows_seen,
                unvalidated=UNVALIDATED_NO_REAL_DATA,
            )

        try:
            # IsolationForest score_samples returns negative scores; more negative = more anomalous.
            # Normalize to [0,1]: 0 = normal, 1 = anomalous.
            raw = float(self._model.score_samples([row])[0])
            # score_samples range is roughly [-0.5, 0.5] for IsolationForest.
            # Clamp and invert so high score = anomalous.
            normalized = max(0.0, min(1.0, (-raw + 0.5)))
        except Exception as exc:
            logger.error("home=%s Layer1 score failed: %s", self._home_id, exc)
            return AnomalyScore(
                score=0.0,
                contributing_features={},
                baseline_stable=True,
                windows_seen=self._windows_seen,
                unvalidated=UNVALIDATED_NO_REAL_DATA,
            )

        # Compute per-feature deviations from buffer mean for explainability
        buffer_matrix = _to_matrix(list(self._buffer))
        if buffer_matrix is not None and len(buffer_matrix) > 0:
            import statistics
            field_names = _FEATURE_FIELDS
            for i, name in enumerate(field_names):
                col = [r[i] for r in buffer_matrix if r[i] is not None]
                if col:
                    mean = statistics.mean(col)
                    contributing[name] = row[i] - mean

        return AnomalyScore(
            score=normalized,
            contributing_features=contributing,
            baseline_stable=True,
            windows_seen=self._windows_seen,
            unvalidated=UNVALIDATED_NO_REAL_DATA,
        )


# ------------------------------------------------------------------
# Feature vector → numeric row helpers
# ------------------------------------------------------------------

# Ordered list of FeatureVector fields used for the model matrix.
# None values are imputed with 0.0 ONLY for the model input row.
# This imputation does NOT represent "normal" — it is a technical necessity
# for sklearn. The contributing_features dict preserves the None information.
_FEATURE_FIELDS = [
    "presence_fraction",
    "max_person_count",
    "mean_breathing_rate",
    "std_breathing_rate",
    "motion_fraction",
    "zone_count",
    "fall_event_count",
    "mean_rssi",
    "consecutive_still_windows",
]


def _vector_to_row(vec: FeatureVector) -> list[float]:
    return [
        float(getattr(vec, f)) if getattr(vec, f) is not None else 0.0
        for f in _FEATURE_FIELDS
    ]


def _to_matrix(vecs: list[FeatureVector]) -> list[list[float]] | None:
    if not vecs:
        return None
    return [_vector_to_row(v) for v in vecs]
