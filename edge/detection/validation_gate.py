"""
Validation gate for Layer 1.

UNVALIDATED_NO_REAL_DATA = True until:
  1. Real captured logs from a real puck exist in the logging harness.
  2. Those logs pass through the Layer 2 eval rig.
  3. Eval rig reports false-negative rate, false-positive rate, latency,
     and calibration at or below targets defined in Layer 2 config.
  4. A human explicitly sets this to False and commits the change with
     a reference to the eval run that cleared it.

Until then, Layer 1 MUST NOT influence any alert or escalation decision.
The /health endpoint reports this flag's state.
"""

UNVALIDATED_NO_REAL_DATA: bool = True

VALIDATION_REASON = (
    "Layer 1 anomaly model has not been trained or validated on real captured data. "
    "No puck logs exist yet. Model output must not be trusted for alerting."
)


def assert_validated() -> None:
    """Raise at any call site that must not proceed without validation."""
    if UNVALIDATED_NO_REAL_DATA:
        raise RuntimeError(
            f"Layer 1 is unvalidated. {VALIDATION_REASON} "
            "Set UNVALIDATED_NO_REAL_DATA=False only after passing the Layer 2 eval rig."
        )
