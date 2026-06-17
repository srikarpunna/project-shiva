"""
Probability calibration for Layer 2.

Takes raw IsolationForest scores (uncalibrated [0,1]) and outputs
calibrated probabilities using Platt scaling or isotonic regression.

Calibrator is UNFITTED until trained on real labeled data via the eval rig.
An unfitted calibrator raises CalibrationNotFitted — never silently returns
a score that looks real.
"""
from __future__ import annotations

import logging
from typing import Literal

logger = logging.getLogger(__name__)


class CalibrationNotFitted(Exception):
    """Raised when calibration is attempted before fitting on real labeled data."""


class ScoreCalibrator:
    """
    Wraps sklearn's CalibratedClassifierCV in a simple interface.

    Must be fit() on real labeled data before use.
    Fitting on synthetic data is the caller's responsibility to prevent —
    this class has no way to detect it.
    """

    def __init__(self, method: Literal["platt", "isotonic"] = "platt") -> None:
        self._method = method
        self._calibrator = None  # set after fit()
        self._fitted = False

    @property
    def is_fitted(self) -> bool:
        return self._fitted

    def fit(self, raw_scores: list[float], labels: list[int]) -> None:
        """
        Fit the calibrator on real labeled data.

        raw_scores: list of Layer 1 anomaly scores [0,1]
        labels: 0 = normal, 1 = anomalous (from human labels via label_cli)

        Raises ValueError if labels list is empty or has only one class.
        """
        if not raw_scores or not labels:
            raise ValueError("Cannot fit calibrator: no data provided.")
        if len(set(labels)) < 2:
            raise ValueError(
                "Cannot fit calibrator: labels must contain both 0 and 1 classes. "
                "Need labeled normal AND anomalous examples from real captured logs."
            )
        if len(raw_scores) != len(labels):
            raise ValueError("raw_scores and labels must be same length.")

        from sklearn.linear_model import LogisticRegression
        from sklearn.isotonic import IsotonicRegression
        import numpy as np

        scores = np.array(raw_scores).reshape(-1, 1)
        labs = np.array(labels)

        if self._method == "platt":
            clf = LogisticRegression()
            clf.fit(scores, labs)
            self._calibrator = clf
        else:
            iso = IsotonicRegression(out_of_bounds="clip")
            iso.fit(raw_scores, labels)
            self._calibrator = iso

        self._fitted = True
        logger.info(
            "ScoreCalibrator fitted method=%s n=%d pos=%d neg=%d",
            self._method,
            len(labels),
            sum(labels),
            len(labels) - sum(labels),
        )

    def calibrate(self, raw_score: float) -> float:
        """
        Map a raw [0,1] anomaly score to a calibrated probability [0,1].
        Raises CalibrationNotFitted if fit() has not been called.
        """
        if not self._fitted or self._calibrator is None:
            raise CalibrationNotFitted(
                "Calibrator has not been fitted. "
                "Run the eval rig on real labeled data first."
            )
        import numpy as np

        if self._method == "platt":
            prob = self._calibrator.predict_proba([[raw_score]])[0][1]
        else:
            prob = float(self._calibrator.predict([raw_score])[0])

        return float(np.clip(prob, 0.0, 1.0))
