"""
Live terminal view of an in-progress capture.

Tails the JSONL file a capture tool is writing (udp_capture.py or
capture_labeled.py) and renders a refreshing dashboard. Reads the FILE, not
the socket — so it runs alongside a capture without fighting for UDP :5005.

What it shows (all decoded from real packets):
  - packet rate per type (raw CSI / vitals / feature_state ...)
  - raw CSI amplitude mean + RSSI  (0xC5110001 — the real sensing signal)
  - VENDOR vitals: presence / motion / breathing / n_persons (0xC5110002)
  - VENDOR feature_state scores (0xC5110006)

DISCIPLINE — read this on the screen too:
  presence / breathing / motion shown here are the VENDOR firmware's own
  output. They are a HINT to eyeball that the rig is alive, NOT ground truth
  and NOT our detector. Do not trust these numbers for anything. Our detector
  is built on the raw CSI and validated against labeled captures.

Usage:
  python tools/live_view.py                       # auto-tail newest capture file
  python tools/live_view.py path/to/capture.jsonl # tail a specific file
"""
from __future__ import annotations

import base64
import json
import struct
import sys
import time
from collections import deque
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from decode_csi import decode_packet  # reuse the verified decoders

ROOT = Path(__file__).parent.parent
SEARCH_DIRS = [ROOT / "data" / "csi_labeled", ROOT / "data" / "csi_raw"]


def newest_capture() -> Path | None:
    files = []
    for d in SEARCH_DIRS:
        if d.exists():
            files += list(d.rglob("*.jsonl"))
    files = [f for f in files if f.name != "manifest.jsonl"]
    return max(files, key=lambda f: f.stat().st_mtime) if files else None


def render(state: dict, rate_total: int, stale: bool) -> None:
    """One self-overwriting line. No screen clear — never smears."""
    csi = state.get("raw_csi", {})
    v = state.get("vitals", {})
    amp = csi.get("amp_mean", 0.0)
    rssi = csi.get("rssi", 0)
    vend = "?"
    if v:
        vend = ("present" if v.get("presence") else "empty")
        if v.get("fall"):
            vend += "+FALL"
    flag = "STALE(capture ended?)" if stale else f"{rate_total:>3} pkt/s"
    # OURS: deliberately blank — our detector is not built yet (roadmap L1).
    line = (f"\r{flag} | CSI amp {amp:5.1f} rssi {rssi:>4} | "
            f"vendor[hint]: {vend:<10} mot {v.get('motion_energy',0):5.1f} "
            f"br {v.get('breathing_bpm',0):4.1f} | OURS: not-built(L1)   ")
    sys.stdout.write(line[:160])
    sys.stdout.flush()


def main() -> None:
    if len(sys.argv) > 1:
        path = Path(sys.argv[1])
    else:
        path = newest_capture()
        if path is None:
            print("No capture file found. Start a capture first.", file=sys.stderr)
            sys.exit(1)
        print(f"Tailing newest: {path}")
        time.sleep(0.8)

    if not path.exists():
        print(f"No such file: {path}", file=sys.stderr)
        sys.exit(1)

    print(f"  live view: {path.name}")
    print("  vendor[hint] = firmware guess, NOT ground truth. OURS = our detector (not built yet, L1).\n")

    state: dict[str, dict] = {}
    recent = deque(maxlen=400)  # ts of each packet for rate calc
    last_data = time.time()
    fh = path.open()
    fh.seek(0, 2)  # start at end — show live, not history
    last_render = 0.0
    try:
        while True:
            line = fh.readline()
            if not line:
                time.sleep(0.05)
            else:
                try:
                    raw = json.loads(line)
                    rec = decode_packet(base64.b64decode(raw["payload_b64"]))
                except (json.JSONDecodeError, KeyError, ValueError):
                    continue
                state[rec.get("type", "unknown")] = rec
                recent.append(time.time())
                last_data = time.time()

            now = time.time()
            if now - last_render >= 0.5:
                cutoff = now - 1.0
                rate_total = sum(1 for ts in recent if ts >= cutoff)
                stale = (now - last_data) > 3.0
                render(state, rate_total, stale)
                last_render = now
    except KeyboardInterrupt:
        print("\nview stopped. capture (if any) still running.")


if __name__ == "__main__":
    main()
