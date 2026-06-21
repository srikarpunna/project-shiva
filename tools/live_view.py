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


def bar(x: float, lo: float, hi: float, width: int = 20) -> str:
    if hi <= lo:
        return " " * width
    frac = max(0.0, min(1.0, (x - lo) / (hi - lo)))
    n = int(frac * width)
    return "█" * n + "·" * (width - n)


def render(path: Path, state: dict, rates: dict) -> None:
    sys.stdout.write("\033[2J\033[H")  # clear + home
    print(f"  LIVE  {path.name}")
    print(f"  (vendor hints — NOT ground truth, NOT our detector)\n")

    print("  packet rate (last ~1s):")
    for name in ("raw_csi", "vitals", "feature_state", "feature_vec",
                 "c6_timesync", "mesh_envelope"):
        r = rates.get(name, 0)
        if r:
            print(f"    {name:>15}  {r:>3}/s")
    print()

    csi = state.get("raw_csi", {})
    if csi:
        amp = csi.get("amp_mean", 0)
        rssi = csi.get("rssi", 0)
        print(f"  RAW CSI    amp {amp:6.2f}  [{bar(amp, 0, 40)}]")
        print(f"             rssi {rssi:>4} dBm   sub {csi.get('n_subcarriers','?')}  "
              f"{csi.get('freq_mhz','?')}MHz")
    print()

    v = state.get("vitals", {})
    if v:
        pres = "PRESENT" if v.get("presence") else "  empty"
        fall = "  FALL!" if v.get("fall") else ""
        mot = v.get("motion_energy", 0)
        print(f"  vitals     {pres}{fall}   persons {v.get('n_persons','?')}")
        print(f"             motion {mot:6.2f}  [{bar(mot, 0, 10)}]")
        print(f"             breathing {v.get('breathing_bpm',0):5.1f} bpm   "
              f"hr {v.get('heartrate_bpm',0):5.1f} bpm")

    fs = state.get("feature_state", {})
    if fs:
        print(f"  feat_state presence {fs.get('presence_score',0):5.2f}  "
              f"motion {fs.get('motion_score',0):4.2f}  "
              f"resp {fs.get('respiration_bpm',0):5.1f}  "
              f"coherence {fs.get('node_coherence',0):4.2f}")

    print(f"\n  Ctrl-C to stop view (capture keeps running).")


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

    state: dict[str, dict] = {}
    recent = deque(maxlen=400)  # (ts, type) for rate calc
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
                    data = base64.b64decode(raw["payload_b64"])
                    rec = decode_packet(data)
                except (json.JSONDecodeError, KeyError, ValueError):
                    continue
                t = rec.get("type", "unknown")
                state[t] = rec
                recent.append((time.time(), t))

            now = time.time()
            if now - last_render >= 0.5:
                cutoff = now - 1.0
                rates: dict[str, int] = {}
                for ts, t in recent:
                    if ts >= cutoff:
                        rates[t] = rates.get(t, 0) + 1
                render(path, state, rates)
                last_render = now
    except KeyboardInterrupt:
        sys.stdout.write("\033[2J\033[H")
        print("view stopped. capture still running.")


if __name__ == "__main__":
    main()
