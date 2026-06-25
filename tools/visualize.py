"""
Visual dashboard of what the WiFi actually sees — real CSI, our hardware.

NOT wifi-densepose. We have 1 antenna / 1 board / 64 subcarriers and NO camera
ground truth, so we cannot and do not draw body skeletons. That demo needed many
antennas + synchronized video to train a mesh model. Drawing a silhouette here
would be faking it. Instead this shows the REAL signal and OUR honest detector:

  - CSI waterfall  : 64 subcarriers x time, gain-normalized (the "fingerprint")
  - motion trace   : scale-invariant shape-change between frames (kills AGC)
  - presence verdict: OUR L1 detector — empty / PRESENT, with a persistence rule

Why shape-change, not amplitude: ESP32 CSI is auto-gain-scaled per packet, so raw
amplitude diffs measure gain wobble, not motion. Cosine distance between
consecutive subcarrier vectors is scale-invariant → real channel change → motion.
(Proven on labeled captures: empty 0.08, walking 0.14, sitting-still 0.17.)

DISCIPLINE
  - Reads the capture FILE, not the socket — runs alongside a capture, no :5005 fight.
  - Presence here is OUR detector on RAW CSI (0xC5110001), never the vendor vitals.
  - Thresholds are provisional, fit on a handful of captures. NOT certified.

Usage:
  python tools/visualize.py                      # live: tail newest capture file
  python tools/visualize.py path/to/capture.jsonl  # replay a specific capture
"""
from __future__ import annotations

import base64
import json
import struct
import sys
import time
from collections import deque
from pathlib import Path

import numpy as np
import matplotlib.pyplot as plt
from matplotlib.animation import FuncAnimation

CSI_MAGIC = 0xC5110001
ROOT = Path(__file__).parent.parent
SEARCH_DIRS = [ROOT / "data" / "csi_labeled", ROOT / "data" / "csi_raw"]

WIN = 220                # frames shown in the waterfall / trace
SMOOTH = 30              # motion smoothing window (frames, ~2.5s @ ~12Hz)
PERSIST = 20             # consecutive frames over the adaptive threshold before flagging
FLOOR_WIN = 700          # frames (~1 min) of history used to learn this session's floor
FLOOR_PCTL = 30          # the quiet floor = this percentile of recent motion
# WHY ADAPTIVE, not a fixed threshold: CSI scale drifts between sessions (board angle,
# distance, environment). A number tuned on one capture (0.14) silently fails on the
# next — proven live 2026-06-25 (a present person read 0.06, under 0.14 -> "empty").
# So the threshold is learned LIVE from each session's own quiet floor:
#   thr = floor + max(MARGIN_REL*floor, MARGIN_ABS)
# Motion sustained above that = movement vs this room's own baseline. This is the
# calibration-wizard idea in miniature. True empty-vs-present still needs a captured
# EMPTY baseline; without one this is honestly a MOTION detector, not presence.
MARGIN_REL = 0.8         # threshold sits this fraction above the floor ...
MARGIN_ABS = 0.04        # ... or this absolute margin, whichever is larger


def newest_capture() -> Path | None:
    files = []
    for d in SEARCH_DIRS:
        if d.exists():
            files += [f for f in d.rglob("*.jsonl") if f.name != "manifest.jsonl"]
    return max(files, key=lambda f: f.stat().st_mtime) if files else None


def csi_amps(payload: bytes):
    """Per-subcarrier amplitude from one raw CSI datagram, or None."""
    if len(payload) < 4 or struct.unpack_from("<I", payload, 0)[0] != CSI_MAGIC:
        return None
    iq = payload[20:]
    pairs = struct.unpack(f"<{len(iq)}b", iq)
    return np.array([np.hypot(pairs[i], pairs[i + 1])
                     for i in range(0, len(pairs) - 1, 2)], dtype=float)


def shape_change(a, b) -> float:
    """Cosine distance between consecutive frames — scale-invariant motion."""
    na, nb = np.linalg.norm(a), np.linalg.norm(b)
    if na == 0 or nb == 0:
        return 0.0
    return float(1 - (a @ b) / (na * nb))


def main() -> None:
    path = Path(sys.argv[1]) if len(sys.argv) > 1 else newest_capture()
    if path is None or not path.exists():
        print("No capture file found. Start a capture first.", file=sys.stderr)
        sys.exit(1)
    print(f"visualizing: {path.name}  (reading file, not socket)")

    frames = deque(maxlen=WIN)     # per-subcarrier amplitude vectors (normalized)
    motion = deque(maxlen=WIN)     # smoothed shape-change (the visible trace)
    raw_motion = deque(maxlen=SMOOTH)
    floor_buf = deque(maxlen=FLOOR_WIN)  # long history of motion to learn the quiet floor
    prev = {"v": None}
    run = {"n": 0}                 # consecutive frames above the ADAPTIVE threshold
    thr_live = {"v": None}         # current adaptive threshold (None until warmed up)
    last_data = {"t": time.time()}

    fh = path.open()
    fh.seek(0, 2)                  # live tail; for replay we still catch up below
    # replay convenience: if the file is already complete, start from the top
    if path.stat().st_mtime < time.time() - 5:
        fh.seek(0)

    fig, (axw, axm) = plt.subplots(2, 1, figsize=(11, 6),
                                   gridspec_kw={"height_ratios": [3, 1]})
    fig.suptitle("What the WiFi sees — real CSI (our board) + OUR presence detector",
                 weight="bold")
    im = axw.imshow(np.zeros((64, WIN)), aspect="auto", cmap="viridis",
                    vmin=0, vmax=0.3, origin="lower")
    axw.set_ylabel("subcarrier 0-63")
    axw.set_xticks([])
    banner = axw.text(0.5, 1.02, "", transform=axw.transAxes, ha="center",
                      fontsize=14, weight="bold")
    (line,) = axm.plot([], [], color="#d62728", lw=1.2)
    thr_line = axm.axhline(MARGIN_ABS, ls="--", color="gray", lw=0.9,
                           label="adaptive threshold")
    axm.set_ylim(0, 0.4)
    axm.set_xlim(0, WIN)
    axm.set_ylabel("motion\n(shape change)")
    axm.set_xlabel("time ->")
    axm.legend(loc="upper left", fontsize=8)

    def pump():
        """Drain new lines from the file into the rolling buffers."""
        got = 0
        for _ in range(400):                  # cap work per frame
            ln = fh.readline()
            if not ln:
                break
            try:
                amps = csi_amps(base64.b64decode(json.loads(ln)["payload_b64"]))
            except (json.JSONDecodeError, KeyError, ValueError):
                continue
            if amps is None:
                continue
            n = np.linalg.norm(amps)
            norm = amps / n if n else amps
            if prev["v"] is not None:
                raw_motion.append(shape_change(amps, prev["v"]))
                sm = sum(raw_motion) / len(raw_motion)
                motion.append(sm)
                floor_buf.append(sm)
                # learn this session's quiet floor, set the adaptive threshold
                if len(floor_buf) >= 60:
                    floor = float(np.percentile(floor_buf, FLOOR_PCTL))
                    thr_live["v"] = floor + max(MARGIN_REL * floor, MARGIN_ABS)
                    over = thr_live["v"] is not None and sm > thr_live["v"]
                    run["n"] = run["n"] + 1 if over else 0
            prev["v"] = amps
            frames.append(norm)
            got += 1
        if got:
            last_data["t"] = time.time()
        return got

    def update(_):
        pump()
        if frames:
            arr = np.array(frames).T               # 64 x t
            buf = np.zeros((64, WIN))
            buf[:, -arr.shape[1]:] = arr[:64, :]
            im.set_data(buf)
        if motion:
            y = list(motion)
            line.set_data(range(WIN - len(y), WIN), y)
        if thr_live["v"] is not None:
            thr_line.set_ydata([thr_live["v"], thr_live["v"]])
        stale = time.time() - last_data["t"] > 3.0
        warming = thr_live["v"] is None
        moving = run["n"] >= PERSIST
        if stale:
            banner.set_text("— STALE (capture ended / no data) —")
            banner.set_color("gray")
        elif warming:
            banner.set_text("calibrating to this room's floor ...")
            banner.set_color("#1f77b4")
        elif moving:
            banner.set_text("MOTION  (movement above room baseline)")
            banner.set_color("#d62728")
        else:
            banner.set_text("quiet  (at room baseline)")
            banner.set_color("#2ca02c")
        return im, line, banner, thr_line

    ani = FuncAnimation(fig, update, interval=200, blit=False, cache_frame_data=False)
    plt.tight_layout(rect=[0, 0, 1, 0.95])
    try:
        plt.show()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
