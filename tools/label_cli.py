"""
Labeling CLI — annotate real events in a captured JSONL log.

Labels are stored alongside the log as <logfile>.labels.jsonl.
Each label: {"ts_ms": int, "label": str, "note": str}

Standard labels: fall, normal_stillness, normal_activity, breathing_normal,
                 breathing_abnormal, false_alarm, unknown

Usage:
    python tools/label_cli.py data/logs/raw_2024-01-15.jsonl
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from datetime import datetime, timezone

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

STANDARD_LABELS = [
    "fall",
    "normal_stillness",
    "normal_activity",
    "breathing_normal",
    "breathing_abnormal",
    "false_alarm",
    "unknown",
]


def ts_human(ts_ms: int) -> str:
    return datetime.fromtimestamp(ts_ms / 1000, tz=timezone.utc).strftime(
        "%Y-%m-%d %H:%M:%S UTC"
    )


def load_messages(path: Path) -> list[dict]:
    msgs = []
    with path.open() as fh:
        for line in fh:
            line = line.strip()
            if line:
                try:
                    msgs.append(json.loads(line))
                except json.JSONDecodeError:
                    pass
    return msgs


def load_labels(label_path: Path) -> list[dict]:
    if not label_path.exists():
        return []
    labels = []
    with label_path.open() as fh:
        for line in fh:
            line = line.strip()
            if line:
                try:
                    labels.append(json.loads(line))
                except json.JSONDecodeError:
                    pass
    return labels


def save_label(label_path: Path, entry: dict) -> None:
    with label_path.open("a") as fh:
        fh.write(json.dumps(entry) + "\n")


def main() -> None:
    p = argparse.ArgumentParser(description="Label events in a captured JSONL log")
    p.add_argument("log_file", help="Path to raw_*.jsonl capture file")
    p.add_argument("--step", type=int, default=10,
                   help="Show every Nth message (default 10)")
    args = p.parse_args()

    log_path = Path(args.log_file)
    if not log_path.exists():
        print(f"File not found: {log_path}")
        sys.exit(1)

    label_path = log_path.with_suffix(".labels.jsonl")
    msgs = load_messages(log_path)
    existing = load_labels(label_path)

    labeled_ts = {e["ts_ms"] for e in existing}
    print(f"\nLog: {log_path.name}  ({len(msgs)} messages)")
    print(f"Labels file: {label_path.name}  ({len(existing)} existing labels)")
    print("\nCommands: <label_name> | s(skip) | q(quit) | ?(list labels)\n")

    for i, msg in enumerate(msgs):
        if i % args.step != 0:
            continue
        if msg["ts_ms"] in labeled_ts:
            continue

        print(f"\n[{i:05d}/{len(msgs)}] {ts_human(msg['ts_ms'])}")
        print(f"  topic  : {msg['topic']}")
        print(f"  payload: {json.dumps(msg['payload'])[:160]}")

        while True:
            try:
                raw = input("  label> ").strip().lower()
            except (EOFError, KeyboardInterrupt):
                print("\nQuitting.")
                return

            if raw == "q":
                print("Quit.")
                return
            if raw in ("s", ""):
                break
            if raw == "?":
                print("  Labels: " + ", ".join(STANDARD_LABELS))
                continue
            note_parts = raw.split(" ", 1)
            label = note_parts[0]
            note = note_parts[1] if len(note_parts) > 1 else ""
            entry = {"ts_ms": msg["ts_ms"], "label": label, "note": note}
            save_label(label_path, entry)
            labeled_ts.add(msg["ts_ms"])
            print(f"  Saved: {entry}")
            break

    print(f"\nDone. Total labels: {len(load_labels(label_path))}")


if __name__ == "__main__":
    main()
