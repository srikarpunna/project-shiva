"""
Labeled CSI capture — one session, one human-asserted ground-truth label.

Wraps the same lossless UDP capture as tools/udp_capture.py, but ties each
recording to a LABEL the human asserts was true for the whole window
(e.g. "empty", "still", "walking", "breathing", "fall"). This is the ONLY
source of ground truth in the system: a person says "for these 60 seconds the
room was empty / I was lying still breathing / I walked around". The raw CSI
bytes + that assertion are what the eval rig validates OUR detector against.

It writes:
  data/csi_labeled/<label>/<label>_<utc>.jsonl   raw packets (lossless, b64)
  data/csi_labeled/manifest.jsonl                one appended manifest line

Manifest line:
  {"label","file","start_ms","end_ms","duration_s","n_packets","by_magic",
   "note","src"}

DISCIPLINE
  - The label is a HUMAN claim of what physically happened. Never auto-derived
    from vendor vitals (0002/0003/0006) — those are hints, not truth.
  - "empty" must mean genuinely nobody present. An empty home must never alert,
    so empty captures are the most important negative class — get them clean.
  - data/ is gitignored. Recordings of a real home. Stay local. No synthetic.

Usage:
  python tools/capture_labeled.py --label empty    --duration 60 --note "nobody home"
  python tools/capture_labeled.py --label breathing --duration 90 --note "lying on couch, still"
  python tools/capture_labeled.py --label walking  --duration 60 --note "pacing living room"

A 3-2-1 countdown precedes capture so you can get into position.
"""
from __future__ import annotations

import argparse
import base64
import json
import signal
import socket
import struct
import sys
import time
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

DEFAULT_PORT = 5005
ROOT = Path(__file__).parent.parent
LABEL_DIR = ROOT / "data" / "csi_labeled"
MANIFEST = LABEL_DIR / "manifest.jsonl"

# Known labels = the corpus classes. Free-text allowed but warned, so a typo
# ("walkign") never silently becomes a new class.
KNOWN = {"empty", "still", "walking", "breathing", "fall", "sitting", "transition"}


def magic_of(data: bytes) -> str:
    if len(data) >= 4:
        return hex(struct.unpack("<I", data[:4])[0])
    return "0x?"


def countdown(label: str, seconds: int = 3) -> None:
    print(f"\nLabel = '{label}'. Get into position.")
    for n in range(seconds, 0, -1):
        print(f"  starting in {n}...")
        time.sleep(1)
    print("  GO — recording now.\n")


def main() -> None:
    p = argparse.ArgumentParser(description="Capture a labeled CSI session.")
    p.add_argument("--label", required=True, help="ground-truth label for this whole window")
    p.add_argument("--duration", type=float, required=True, help="seconds to capture")
    p.add_argument("--note", default="", help="free-text context (where, who, what)")
    p.add_argument("--bind", default="0.0.0.0")
    p.add_argument("--port", type=int, default=DEFAULT_PORT)
    p.add_argument("--no-countdown", action="store_true")
    args = p.parse_args()

    label = args.label.strip().lower()
    if label not in KNOWN:
        print(f"WARNING: '{label}' is not a known class {sorted(KNOWN)}.", file=sys.stderr)
        print("Continuing — but check for a typo (a misspelled label = a fake class).",
              file=sys.stderr)

    out_dir = LABEL_DIR / label
    out_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(tz=timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    out_path = out_dir / f"{label}_{stamp}.jsonl"

    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    try:
        sock.bind((args.bind, args.port))
    except OSError as exc:
        print(f"ERROR: cannot bind {args.bind}:{args.port} — {exc}", file=sys.stderr)
        print("Another capture running? Close it and retry.", file=sys.stderr)
        sys.exit(1)
    sock.settimeout(1.0)

    stop = {"flag": False}
    signal.signal(signal.SIGINT, lambda *_: stop.update(flag=True))
    signal.signal(signal.SIGTERM, lambda *_: stop.update(flag=True))

    if not args.no_countdown:
        countdown(label)

    print(f"Recording '{label}' for {args.duration:.0f}s -> {out_path}")
    seq = 0
    total = Counter()
    window = Counter()
    srcs: set[str] = set()
    start = time.time()
    start_ms = int(start * 1000)
    last_tick = start
    fh = out_path.open("w")
    try:
        while not stop["flag"]:
            remaining = args.duration - (time.time() - start)
            if remaining <= 0:
                break
            try:
                data, addr = sock.recvfrom(65535)
            except socket.timeout:
                data = None
            if data:
                m = magic_of(data)
                fh.write(json.dumps({
                    "ts_ms": int(time.time() * 1000), "seq": seq, "src": addr[0],
                    "magic": m, "len": len(data),
                    "payload_b64": base64.b64encode(data).decode("ascii"),
                }) + "\n")
                seq += 1
                total[m] += 1
                window[m] += 1
                srcs.add(addr[0])
            now = time.time()
            if now - last_tick >= 1.0:
                print(f"  {int(remaining):>3}s left  {sum(window.values()):>3} pkt/s")
                window.clear()
                last_tick = now
    finally:
        fh.close()
        sock.close()
    end_ms = int(time.time() * 1000)

    if seq == 0:
        print("\nNO PACKETS — board off / wrong WiFi / target-ip not this Mac. "
              "File kept but empty; DISCARD it.", file=sys.stderr)

    manifest_rec = {
        "label": label, "file": str(out_path.relative_to(ROOT)),
        "start_ms": start_ms, "end_ms": end_ms,
        "duration_s": round((end_ms - start_ms) / 1000, 1),
        "n_packets": seq, "by_magic": dict(total),
        "note": args.note, "src": sorted(srcs),
    }
    LABEL_DIR.mkdir(parents=True, exist_ok=True)
    with MANIFEST.open("a") as mf:
        mf.write(json.dumps(manifest_rec) + "\n")

    print("\n=== session done ===")
    print(f"label:    {label}")
    print(f"file:     {out_path}")
    print(f"packets:  {seq}  ({dict(total)})")
    print(f"manifest: {MANIFEST}  (+1 line)")


if __name__ == "__main__":
    main()
