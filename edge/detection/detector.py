"""
Layer 1 public API — DetectorService.

Owns one HomeBaseline per home_id. Accepts RawMessages, manages windowing,
emits AnomalyScores. Does NOT emit alerts — that is Layer 2's job.

The service checks UNVALIDATED_NO_REAL_DATA at every score emission and
embeds the flag in every AnomalyScore so callers cannot accidentally trust
an unvalidated score.
"""
from __future__ import annotations

import logging
from collections import defaultdict

from edge.detection.baseline import AnomalyScore, HomeBaseline
from edge.detection.config import Layer1Config
from edge.detection.features import FeatureVector, extract
from edge.detection.validation_gate import UNVALIDATED_NO_REAL_DATA, VALIDATION_REASON
from edge.ingestion.schemas import RawMessage

logger = logging.getLogger(__name__)


class DetectorService:
    """
    Stateful per-home anomaly detection service.

    Usage:
        svc = DetectorService(cfg)
        score = svc.ingest(home_id, message)
        # score is None until the first window closes
        # score.unvalidated is True until real data clears the gate
        # score.baseline_stable is False during the learning period
    """

    def __init__(self, cfg: Layer1Config) -> None:
        self._cfg = cfg
        self._baselines: dict[str, HomeBaseline] = {}
        # Pending messages per home, accumulating toward next window boundary
        self._pending: dict[str, list[RawMessage]] = defaultdict(list)
        self._window_start_ms: dict[str, int] = {}

        if UNVALIDATED_NO_REAL_DATA:
            logger.warning(
                "DetectorService started in UNVALIDATED state. %s", VALIDATION_REASON
            )

    def ingest(self, home_id: str, msg: RawMessage) -> AnomalyScore | None:
        """
        Accept one message. Returns an AnomalyScore when a window closes,
        None otherwise.

        The returned score always includes unvalidated=True until the gate is cleared.
        Callers MUST NOT use this score for alerting while unvalidated=True.
        """
        if home_id not in self._baselines:
            self._baselines[home_id] = HomeBaseline(home_id, self._cfg)
            self._window_start_ms[home_id] = msg.ts_ms

        self._pending[home_id].append(msg)

        window_start = self._window_start_ms[home_id]
        window_end = window_start + self._cfg.window_seconds * 1000

        if msg.ts_ms < window_end:
            return None

        # Window closed — extract features and score
        window_msgs = self._pending[home_id]
        self._pending[home_id] = []
        self._window_start_ms[home_id] = msg.ts_ms

        vec: FeatureVector = extract(window_msgs, window_start, window_end)
        score = self._baselines[home_id].update(vec)

        if not score.baseline_stable:
            logger.info(
                "home=%s Layer1 learning (%d/%d windows)",
                home_id,
                score.windows_seen,
                self._cfg.min_windows_for_stable_baseline,
            )

        return score

    def health(self, home_id: str) -> dict:
        """Return Layer 1 health dict for inclusion in /health endpoint."""
        if home_id not in self._baselines:
            return {
                "layer1_status": "not_started",
                "unvalidated": UNVALIDATED_NO_REAL_DATA,
                "reason": VALIDATION_REASON,
            }
        baseline = self._baselines[home_id]
        status = "stable" if baseline.is_stable else "learning"
        return {
            "layer1_status": status,
            "windows_seen": baseline.windows_seen(),
            "min_windows_required": self._cfg.min_windows_for_stable_baseline,
            "unvalidated": UNVALIDATED_NO_REAL_DATA,
            "reason": VALIDATION_REASON if UNVALIDATED_NO_REAL_DATA else None,
        }
