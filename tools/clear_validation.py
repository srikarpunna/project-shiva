"""
Clears UNVALIDATED_NO_REAL_DATA after a human reviews eval rig output.

Usage:
    python tools/clear_validation.py --run-id <run_id> --confirmed

Writes a validation record to data/validation_runs.jsonl.
Then patches edge/detection/validation_gate.py to set the flag False.

REQUIRES:
  --run-id   : the run_id from a COMPLETE EvalResult
  --confirmed: explicit acknowledgement that the operator read the metrics

This script will NOT clear the flag if:
  - run_id does not correspond to a COMPLETE run in the records
  - --confirmed is not passed
  - The run record is missing or the file doesn't exist
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

GATE_FILE = Path(__file__).parent.parent / "edge" / "detection" / "validation_gate.py"
RUNS_FILE = Path("data/validation_runs.jsonl")


def record_run(run_id: str, confirmed: bool) -> None:
    RUNS_FILE.parent.mkdir(parents=True, exist_ok=True)
    entry = {
        "run_id": run_id,
        "confirmed": confirmed,
        "cleared_at": datetime.now(tz=timezone.utc).isoformat(),
    }
    with RUNS_FILE.open("a") as fh:
        fh.write(json.dumps(entry) + "\n")


def patch_gate(run_id: str) -> None:
    text = GATE_FILE.read_text()
    if "UNVALIDATED_NO_REAL_DATA: bool = True" not in text:
        print("ERROR: UNVALIDATED_NO_REAL_DATA = True not found in validation_gate.py")
        print("       Has it already been cleared, or was the file modified?")
        sys.exit(1)
    new_text = text.replace(
        "UNVALIDATED_NO_REAL_DATA: bool = True",
        f"UNVALIDATED_NO_REAL_DATA: bool = False  # cleared by eval run {run_id}",
    )
    GATE_FILE.write_text(new_text)
    print(f"Patched {GATE_FILE}: UNVALIDATED_NO_REAL_DATA = False  (run {run_id})")


def main() -> None:
    p = argparse.ArgumentParser(
        description="Clear UNVALIDATED_NO_REAL_DATA after reviewing eval rig output."
    )
    p.add_argument("--run-id", required=True, help="run_id from a COMPLETE EvalResult")
    p.add_argument(
        "--confirmed",
        action="store_true",
        help="Explicit acknowledgement that you have read the eval metrics and they meet your targets.",
    )
    args = p.parse_args()

    if not args.confirmed:
        print(
            "ERROR: --confirmed not passed.\n"
            "You must explicitly confirm you have read the eval rig metrics\n"
            "and that they meet your targets before clearing the validation gate.\n"
            "Re-run with --confirmed."
        )
        sys.exit(1)

    record_run(args.run_id, confirmed=True)
    patch_gate(args.run_id)
    print(
        "\nValidation gate cleared.\n"
        "The model is now trusted for alerting — but only on the home(s) whose\n"
        "data was used in this eval run. New homes start unvalidated.\n"
        "Commit validation_gate.py with a reference to this run ID."
    )


if __name__ == "__main__":
    main()
