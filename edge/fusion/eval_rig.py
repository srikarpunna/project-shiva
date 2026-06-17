"""
Layer 2 eval rig.

Consumes real captured JSONL logs + human labels from label_cli.py.
Reports false-negative rate, false-positive rate, latency-to-first-detection,
and calibration quality (Brier score, reliability diagram data).

DOES NOT:
- accept synthetic data (no in-memory fabrication path)
- pick thresholds automatically
- return a pass/fail verdict

It reports numbers. A human reads them and sets operating points in config.
A human then clears UNVALIDATED_NO_REAL_DATA by running tools/clear_validation.py
with the run_id returned here.

If no labeled real data exists: returns EvalResult with status=NO_DATA.
"""
from __future__ import annotations

import json
import logging
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal

logger = logging.getLogger(__name__)

EvalStatus = Literal["NO_DATA", "INSUFFICIENT_LABELS", "COMPLETE"]

# Minimum labeled events required before the rig produces meaningful metrics.
# TODO(verify): set based on how many labeled events we can realistically get
# per day of real capture. This is a floor, not a target.
MIN_LABELED_EVENTS = 10


@dataclass
class EvalResult:
    """
    Output of one eval rig run.

    All metric fields are None when status != COMPLETE.
    A human reads these numbers and sets config thresholds — the rig
    does not set them.
    """

    run_id: str
    status: EvalStatus

    # Available when status == COMPLETE
    n_events: int = 0
    n_positive: int = 0       # labeled anomalous
    n_negative: int = 0       # labeled normal

    # Metrics at each candidate threshold (list of dicts for plotting)
    # Each entry: {"threshold": float, "fnr": float, "fpr": float, "latency_ms": float | None}
    threshold_curve: list[dict] = field(default_factory=list)

    # Calibration quality
    brier_score: float | None = None   # lower = better calibrated; None until COMPLETE
    # Reliability diagram bins: list of {"mean_confidence": float, "fraction_positive": float, "n": int}
    reliability_bins: list[dict] = field(default_factory=list)

    # Human-readable summary printed by the CLI runner
    summary: str = ""

    # Reason when status != COMPLETE
    reason: str = ""


@dataclass
class LabeledEvent:
    ts_ms: int
    label: str   # from STANDARD_LABELS in label_cli.py
    note: str


@dataclass
class ScoredWindow:
    ts_ms: int
    raw_score: float
    calibrated_confidence: float | None  # None if calibrator not fitted


def load_labels(label_path: Path) -> list[LabeledEvent]:
    """Load human labels from a .labels.jsonl file."""
    if not label_path.exists():
        return []
    events = []
    with label_path.open() as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                d = json.loads(line)
                events.append(LabeledEvent(
                    ts_ms=d["ts_ms"],
                    label=d["label"],
                    note=d.get("note", ""),
                ))
            except (json.JSONDecodeError, KeyError) as exc:
                logger.warning("eval_rig: skip malformed label line: %s", exc)
    return events


def load_scored_windows(scored_path: Path) -> list[ScoredWindow]:
    """
    Load scored windows from a JSONL file produced by running the pipeline
    over a replay log.

    Format per line: {"ts_ms": int, "raw_score": float, "calibrated_confidence": float|null}
    """
    if not scored_path.exists():
        return []
    windows = []
    with scored_path.open() as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                d = json.loads(line)
                windows.append(ScoredWindow(
                    ts_ms=d["ts_ms"],
                    raw_score=d["raw_score"],
                    calibrated_confidence=d.get("calibrated_confidence"),
                ))
            except (json.JSONDecodeError, KeyError) as exc:
                logger.warning("eval_rig: skip malformed window line: %s", exc)
    return windows


def _is_anomalous_label(label: str) -> bool:
    """True for labels that represent a real emergency or anomaly."""
    return label in {"fall", "breathing_abnormal"}


def _match_event_to_window(
    event: LabeledEvent,
    windows: list[ScoredWindow],
    tolerance_ms: int = 30_000,
) -> ScoredWindow | None:
    """
    Find the first scored window within tolerance_ms after a labeled event.
    Returns None if no match.
    """
    for w in windows:
        if event.ts_ms <= w.ts_ms <= event.ts_ms + tolerance_ms:
            return w
    return None


def run(
    label_path: Path,
    scored_windows_path: Path,
    # TODO(verify): set tolerance from real signal delay once we know message rate (V8)
    detection_tolerance_ms: int = 30_000,
) -> EvalResult:
    """
    Run the eval rig.

    label_path: output of tools/label_cli.py (<log>.labels.jsonl)
    scored_windows_path: JSONL of ScoredWindows produced by replaying the same log
    detection_tolerance_ms: how late an alert can fire and still count as detected

    Returns EvalResult. Caller prints result.summary and reads the metrics.
    """
    run_id = str(uuid.uuid4())[:8]
    labels = load_labels(label_path)
    windows = load_scored_windows(scored_windows_path)

    if not labels:
        return EvalResult(
            run_id=run_id,
            status="NO_DATA",
            reason=(
                "No human labels found. "
                "Run tools/label_cli.py on a real captured log first. "
                "Cannot certify model without labeled real data."
            ),
        )

    if not windows:
        return EvalResult(
            run_id=run_id,
            status="NO_DATA",
            reason=(
                "No scored windows found. "
                "Run the pipeline in replay mode over the captured log first."
            ),
        )

    positive_labels = [e for e in labels if _is_anomalous_label(e.label)]
    negative_labels = [e for e in labels if not _is_anomalous_label(e.label)]

    if len(positive_labels) + len(negative_labels) < MIN_LABELED_EVENTS:
        return EvalResult(
            run_id=run_id,
            status="INSUFFICIENT_LABELS",
            n_events=len(labels),
            n_positive=len(positive_labels),
            n_negative=len(negative_labels),
            reason=(
                f"Only {len(labels)} labeled events — need at least {MIN_LABELED_EVENTS}. "
                "Label more events in real captured logs."
            ),
        )

    if len(positive_labels) == 0:
        return EvalResult(
            run_id=run_id,
            status="INSUFFICIENT_LABELS",
            n_events=len(labels),
            n_positive=0,
            n_negative=len(negative_labels),
            reason=(
                "No positive (anomalous) labeled events. "
                "Need at least one labeled fall or breathing_abnormal event."
            ),
        )

    # Sort windows by time for matching
    windows_sorted = sorted(windows, key=lambda w: w.ts_ms)

    # Build threshold curve
    all_confidences = [
        w.calibrated_confidence for w in windows_sorted
        if w.calibrated_confidence is not None
    ]
    thresholds = sorted(set(all_confidences)) if all_confidences else []

    threshold_curve = []
    for thresh in thresholds:
        # FNR: fraction of positive events NOT detected within tolerance
        detected = 0
        latencies_ms: list[float] = []
        for event in positive_labels:
            match = _match_event_to_window(event, windows_sorted, detection_tolerance_ms)
            if match and match.calibrated_confidence is not None and match.calibrated_confidence >= thresh:
                detected += 1
                latencies_ms.append(float(match.ts_ms - event.ts_ms))

        fnr = 1.0 - (detected / len(positive_labels)) if positive_labels else None

        # FPR: fraction of negative events that fire at this threshold
        false_alarms = 0
        for event in negative_labels:
            match = _match_event_to_window(event, windows_sorted, detection_tolerance_ms)
            if match and match.calibrated_confidence is not None and match.calibrated_confidence >= thresh:
                false_alarms += 1

        fpr = (false_alarms / len(negative_labels)) if negative_labels else None

        threshold_curve.append({
            "threshold": thresh,
            "fnr": fnr,
            "fpr": fpr,
            "mean_latency_ms": (
                sum(latencies_ms) / len(latencies_ms) if latencies_ms else None
            ),
        })

    # Brier score (requires calibrated confidences on labeled windows)
    brier = _compute_brier(labels, windows_sorted, detection_tolerance_ms)
    reliability_bins = _compute_reliability(labels, windows_sorted, detection_tolerance_ms)

    summary = _format_summary(
        run_id=run_id,
        n_pos=len(positive_labels),
        n_neg=len(negative_labels),
        threshold_curve=threshold_curve,
        brier=brier,
    )

    return EvalResult(
        run_id=run_id,
        status="COMPLETE",
        n_events=len(labels),
        n_positive=len(positive_labels),
        n_negative=len(negative_labels),
        threshold_curve=threshold_curve,
        brier_score=brier,
        reliability_bins=reliability_bins,
        summary=summary,
    )


def _compute_brier(
    labels: list[LabeledEvent],
    windows: list[ScoredWindow],
    tolerance_ms: int,
) -> float | None:
    pairs = []
    for event in labels:
        match = _match_event_to_window(event, windows, tolerance_ms)
        if match and match.calibrated_confidence is not None:
            truth = 1.0 if _is_anomalous_label(event.label) else 0.0
            pairs.append((match.calibrated_confidence, truth))
    if not pairs:
        return None
    return sum((p - t) ** 2 for p, t in pairs) / len(pairs)


def _compute_reliability(
    labels: list[LabeledEvent],
    windows: list[ScoredWindow],
    tolerance_ms: int,
    n_bins: int = 10,
) -> list[dict]:
    bins: list[list[tuple[float, float]]] = [[] for _ in range(n_bins)]
    for event in labels:
        match = _match_event_to_window(event, windows, tolerance_ms)
        if match and match.calibrated_confidence is not None:
            truth = 1.0 if _is_anomalous_label(event.label) else 0.0
            bin_idx = min(int(match.calibrated_confidence * n_bins), n_bins - 1)
            bins[bin_idx].append((match.calibrated_confidence, truth))
    result = []
    for i, bin_pairs in enumerate(bins):
        if not bin_pairs:
            continue
        mean_conf = sum(p for p, _ in bin_pairs) / len(bin_pairs)
        frac_pos = sum(t for _, t in bin_pairs) / len(bin_pairs)
        result.append({"mean_confidence": mean_conf, "fraction_positive": frac_pos, "n": len(bin_pairs)})
    return result


def _format_summary(
    run_id: str,
    n_pos: int,
    n_neg: int,
    threshold_curve: list[dict],
    brier: float | None,
) -> str:
    lines = [
        f"=== Eval Rig Run {run_id} ===",
        f"Labeled events: {n_pos} anomalous, {n_neg} normal",
        f"Brier score: {brier:.4f}" if brier is not None else "Brier score: N/A (calibrator not fitted)",
        "",
        "Threshold curve (set operating point by reading FNR/FPR tradeoff):",
        f"  {'threshold':>10}  {'FNR':>8}  {'FPR':>8}  {'latency_ms':>12}",
    ]
    for row in threshold_curve:
        fnr_s = f"{row['fnr']:.3f}" if row["fnr"] is not None else "   N/A"
        fpr_s = f"{row['fpr']:.3f}" if row["fpr"] is not None else "   N/A"
        lat_s = f"{row['mean_latency_ms']:.0f}" if row["mean_latency_ms"] is not None else "N/A"
        lines.append(f"  {row['threshold']:>10.3f}  {fnr_s:>8}  {fpr_s:>8}  {lat_s:>12}")
    lines += [
        "",
        "Next steps:",
        "  1. Read FNR/FPR curve above and choose operating points for Calm and Guard modes.",
        "  2. Set FUSION_CALM__YELLOW_THRESHOLD, FUSION_CALM__RED_THRESHOLD, etc. in config.",
        "  3. Run tools/clear_validation.py --run-id " + run_id + " to clear UNVALIDATED_NO_REAL_DATA.",
        "     (Only do this if metrics meet your targets.)",
    ]
    return "\n".join(lines)
