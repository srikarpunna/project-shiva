"""
UDP CSI capture-to-disk harness.

The RuView esp32-csi-node firmware (v0.6.x) streams over UDP, NOT MQTT.
Packets land on the aggregator IP at port 5005:

    0xC5110001  CSI frame (raw per-subcarrier I/Q)   ~20 Hz
    0xC5110002  Vitals packet (presence/breathing/HR/fall) 1 Hz
    0xC5110003  (undocumented in v0.6.x firmware)
    0xC5110006  (undocumented in v0.6.x firmware)
    0xC511a110  (undocumented in v0.6.x firmware)

This tool does ONE thing: write every received packet to disk, losslessly,
with a wall-clock timestamp and sequence number. It does NOT interpret,
score, or judge anything. The raw bytes are the authoritative replay corpus
everything downstream is validated against.

NO synthetic data. This only ever records real packets off a real socket.
Output goes under data/ which is gitignored — these are sensor recordings of
a real home and must stay local.

Usage:
    python tools/udp_capture.py                 # listen :5005 until Ctrl-C
    python tools/udp_capture.py --port 5005 --duration 60
    python tools/udp_capture.py --quiet         # no per-second live stats

Each output line (JSONL):
    {"ts_ms": <unix_ms>, "seq": <int>, "src": "<ip>", "magic": "0x...",
     "len": <int>, "payload_b64": "<base64 of full datagram>"}
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
DATA_DIR = Path(__file__).parent.parent / "data" / "csi_raw"


def magic_of(data: bytes) -> str:
    if len(data) >= 4:
        return hex(struct.unpack("<I", data[:4])[0])
    return "0x?"


def main() -> None:
    p = argparse.ArgumentParser(description="Capture RuView UDP CSI packets to disk.")
    p.add_argument("--bind", default="0.0.0.0", help="Bind address (default all)")
    p.add_argument("--port", type=int, default=DEFAULT_PORT, help="UDP port (default 5005)")
    p.add_argument("--duration", type=float, default=0.0,
                   help="Seconds to capture, 0 = until Ctrl-C (default)")
    p.add_argument("--quiet", action="store_true", help="Suppress per-second live stats")
    args = p.parse_args()

    DATA_DIR.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(tz=timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    out_path = DATA_DIR / f"csi_{stamp}.jsonl"

    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    try:
        sock.bind((args.bind, args.port))
    except OSError as exc:
        print(f"ERROR: cannot bind {args.bind}:{args.port} — {exc}", file=sys.stderr)
        print("Is another capture already running? Close it and retry.", file=sys.stderr)
        sys.exit(1)
    sock.settimeout(1.0)

    stop = {"flag": False}

    def _handle(signum, frame):  # noqa: ARG001
        stop["flag"] = True
    signal.signal(signal.SIGINT, _handle)
    signal.signal(signal.SIGTERM, _handle)

    print(f"Capturing UDP {args.bind}:{args.port} -> {out_path}")
    print("Wave your hand near the board — watch the counts move. Ctrl-C to stop.\n")

    seq = 0
    total = Counter()          # magic -> total count
    window = Counter()         # magic -> count this second
    srcs: set[str] = set()
    start = time.time()
    last_tick = start
    fh = out_path.open("w")
    try:
        while not stop["flag"]:
            if args.duration and (time.time() - start) >= args.duration:
                break
            try:
                data, addr = sock.recvfrom(65535)
            except socket.timeout:
                data = None
            if data:
                m = magic_of(data)
                rec = {
                    "ts_ms": int(time.time() * 1000),
                    "seq": seq,
                    "src": addr[0],
                    "magic": m,
                    "len": len(data),
                    "payload_b64": base64.b64encode(data).decode("ascii"),
                }
                fh.write(json.dumps(rec) + "\n")
                seq += 1
                total[m] += 1
                window[m] += 1
                srcs.add(addr[0])

            now = time.time()
            if not args.quiet and now - last_tick >= 1.0:
                pps = sum(window.values())
                brk = " ".join(f"{k}:{v}" for k, v in sorted(window.items()))
                print(f"  {pps:>3} pkt/s  [{brk}]")
                window.clear()
                last_tick = now
    finally:
        fh.close()
        sock.close()

    elapsed = time.time() - start
    print("\n=== capture done ===")
    print(f"file:     {out_path}")
    print(f"duration: {elapsed:.1f}s")
    print(f"packets:  {seq}")
    print(f"sources:  {', '.join(sorted(srcs)) or '(none)'}")
    print("by magic:")
    for k, v in sorted(total.items(), key=lambda kv: -kv[1]):
        print(f"  {k:>12}  {v}")
    if seq == 0:
        print("\nNO PACKETS. Is the board powered + on WiFi? Is target-ip set to this Mac?")


if __name__ == "__main__":
    main()
