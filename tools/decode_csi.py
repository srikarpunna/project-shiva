"""
Decode captured RuView UDP packets into typed records.

Reads a capture file produced by tools/udp_capture.py (JSONL, one base64
datagram per line) and parses each packet according to the on-wire structs
defined in the RuView esp32-csi-node firmware (v0.6.x / v0.8.x). Every field
below is transcribed from the firmware C source, not guessed:

    firmware/esp32-csi-node/main/csi_collector.c   -> 0xC5110001 (raw CSI)
    firmware/esp32-csi-node/main/edge_processing.h -> 0xC5110002 (vitals)
                                                      0xC5110003 (feature vec)
                                                      0xC5110004 (fused vitals)
    firmware/esp32-csi-node/main/rv_feature_state.h-> 0xC5110006 (feature state)
    firmware/esp32-csi-node/main/csi_collector.c   -> 0xC511A110 (C6 time-sync)
    firmware/esp32-csi-node/main/rv_mesh.h         -> 0xC5118100 (mesh envelope)

DISCIPLINE
    The vitals / feature packets (0002/0003/0006) are the VENDOR's own derived
    output. They are a hint, NOT ground truth. We decode them to inspect, never
    to certify. Our detector is validated against real LABELED data, not against
    these numbers. The authoritative sensing input is the raw CSI (0001).

    Pure parser. Reads real captured bytes off disk. No synthetic data, no
    network, no scoring, no alerting.

Usage:
    python tools/decode_csi.py data/csi_raw/csi_*.jsonl            # summary
    python tools/decode_csi.py <file> --magic 0xc5110002 --limit 5 # peek vitals
    python tools/decode_csi.py <file> --out data/decoded.jsonl     # full decode
"""
from __future__ import annotations

import argparse
import base64
import glob
import json
import struct
import sys
from collections import Counter
from pathlib import Path

MAGIC_CSI = 0xC5110001
MAGIC_VITALS = 0xC5110002
MAGIC_FEATURE = 0xC5110003
MAGIC_FUSED = 0xC5110004
MAGIC_COMPRESSED = 0xC5110005
MAGIC_FEATURE_STATE = 0xC5110006
MAGIC_SYNC = 0xC511A110
MAGIC_MESH = 0xC5118100

NAMES = {
    MAGIC_CSI: "raw_csi",
    MAGIC_VITALS: "vitals",
    MAGIC_FEATURE: "feature_vec",
    MAGIC_FUSED: "fused_vitals",
    MAGIC_COMPRESSED: "compressed_csi",
    MAGIC_FEATURE_STATE: "feature_state",
    MAGIC_SYNC: "c6_timesync",
    MAGIC_MESH: "mesh_envelope",
}

CSI_HEADER_SIZE = 20


def decode_csi(b: bytes) -> dict:
    """0xC5110001 — raw CSI frame. 20-byte LE header + int8 I/Q pairs."""
    node, n_ant = b[4], b[5]
    n_sub = struct.unpack_from("<H", b, 6)[0]
    freq = struct.unpack_from("<I", b, 8)[0]
    seq = struct.unpack_from("<I", b, 12)[0]
    rssi = struct.unpack_from("<b", b, 16)[0]
    noise = struct.unpack_from("<b", b, 17)[0]
    ppdu, flags = b[18], b[19]
    iq = b[CSI_HEADER_SIZE:]
    pairs = struct.unpack(f"<{len(iq)}b", iq)  # signed int8
    # amplitude per subcarrier = sqrt(I^2 + Q^2); cheap quality glance only
    amps = [(pairs[i] ** 2 + pairs[i + 1] ** 2) ** 0.5 for i in range(0, len(pairs) - 1, 2)]
    return {
        "node_id": node, "n_antennas": n_ant, "n_subcarriers": n_sub,
        "freq_mhz": freq, "seq": seq, "rssi": rssi, "noise_floor": noise,
        "ppdu_type": ppdu, "sync_valid": bool(flags & 0x10),
        "iq_bytes": len(iq), "amp_mean": round(sum(amps) / len(amps), 2) if amps else 0.0,
    }


def decode_vitals(b: bytes) -> dict:
    """0xC5110002 — 32-byte packed vitals. VENDOR HINT, not ground truth."""
    (magic, node, flags, br_fp, hr_fp, rssi, n_persons,
     motion, presence, ts_ms, _r2) = struct.unpack_from("<IBBHIbB2xffII", b, 0)
    return {
        "node_id": node,
        "presence": bool(flags & 0x01), "fall": bool(flags & 0x02),
        "motion": bool(flags & 0x04),
        "breathing_bpm": br_fp / 100.0, "heartrate_bpm": hr_fp / 10000.0,
        "rssi": rssi,
        "n_persons": n_persons, "motion_energy": round(motion, 4),
        "presence_score": round(presence, 4), "timestamp_ms": ts_ms,
        "_note": "vendor-derived hint, NOT ground truth",
    }


def decode_feature(b: bytes) -> dict:
    """0xC5110003 — 48-byte feature vector (8-dim f32)."""
    node = b[4]
    seq = struct.unpack_from("<H", b, 6)[0]
    ts_us = struct.unpack_from("<q", b, 8)[0]
    feats = struct.unpack_from("<8f", b, 16)
    return {"node_id": node, "seq": seq, "ts_us": ts_us,
            "features": [round(f, 5) for f in feats]}


def decode_feature_state(b: bytes) -> dict:
    """0xC5110006 — 60-byte feature state + crc32. VENDOR HINT."""
    (magic, node, mode, seq, ts_us, motion, presence, resp_bpm, resp_conf,
     hb_bpm, hb_conf, anomaly, env_shift, coherence, qflags, _r, crc) = \
        struct.unpack_from("<IBBHQ9fHHI", b, 0)
    return {
        "node_id": node, "mode": mode, "seq": seq, "ts_us": ts_us,
        "motion_score": round(motion, 4), "presence_score": round(presence, 4),
        "respiration_bpm": round(resp_bpm, 3), "respiration_conf": round(resp_conf, 3),
        "heartbeat_bpm": round(hb_bpm, 3), "heartbeat_conf": round(hb_conf, 3),
        "anomaly_score": round(anomaly, 4), "env_shift_score": round(env_shift, 4),
        "node_coherence": round(coherence, 4), "quality_flags": qflags,
        "crc32": crc, "_note": "vendor-derived hint, NOT ground truth",
    }


def decode_sync(b: bytes) -> dict:
    """0xC511A110 — 32-byte C6 mesh time-sync. Plumbing, not sensing."""
    node, ver, flags = b[4], b[5], b[6]
    local_us = struct.unpack_from("<Q", b, 8)[0]
    epoch_us = struct.unpack_from("<Q", b, 16)[0]
    seq = struct.unpack_from("<I", b, 24)[0]
    return {"node_id": node, "version": ver, "is_leader": bool(flags & 0x01),
            "sync_valid": bool(flags & 0x02), "has_offset": bool(flags & 0x04),
            "local_us": local_us, "epoch_us": epoch_us, "seq": seq,
            "_note": "mesh clock sync, not sensing"}


def decode_mesh(b: bytes) -> dict:
    """0xC5118100 — mesh envelope header. Node health/status, not sensing."""
    ver, mtype, role, auth = b[4], b[5], b[6], b[7]
    epoch = struct.unpack_from("<I", b, 8)[0]
    plen = struct.unpack_from("<H", b, 12)[0]
    return {"version": ver, "msg_type": mtype, "sender_role": role,
            "auth_class": auth, "epoch": epoch, "payload_len": plen,
            "_note": "mesh node health, not sensing"}


DECODERS = {
    MAGIC_CSI: decode_csi, MAGIC_VITALS: decode_vitals,
    MAGIC_FEATURE: decode_feature, MAGIC_FEATURE_STATE: decode_feature_state,
    MAGIC_SYNC: decode_sync, MAGIC_MESH: decode_mesh,
}


def decode_packet(b: bytes) -> dict:
    if len(b) < 4:
        return {"type": "short", "len": len(b)}
    magic = struct.unpack_from("<I", b, 0)[0]
    name = NAMES.get(magic, "unknown")
    rec = {"type": name, "magic": hex(magic), "len": len(b)}
    fn = DECODERS.get(magic)
    if fn:
        try:
            rec.update(fn(b))
        except (struct.error, ZeroDivisionError) as exc:
            rec["decode_error"] = str(exc)
    return rec


def main() -> None:
    p = argparse.ArgumentParser(description="Decode captured RuView UDP packets.")
    p.add_argument("file", help="capture .jsonl (glob ok) from udp_capture.py")
    p.add_argument("--magic", help="only show this magic, e.g. 0xc5110002")
    p.add_argument("--limit", type=int, default=0, help="max decoded rows to print")
    p.add_argument("--out", help="write full decoded JSONL here")
    args = p.parse_args()

    paths = sorted(glob.glob(args.file))
    if not paths:
        print(f"No files match {args.file}", file=sys.stderr)
        sys.exit(1)

    want = int(args.magic, 16) if args.magic else None
    counts = Counter()
    shown = 0
    out_fh = open(args.out, "w") if args.out else None

    for path in paths:
        for line in open(path):
            line = line.strip()
            if not line:
                continue
            raw = json.loads(line)
            data = base64.b64decode(raw["payload_b64"])
            rec = decode_packet(data)
            rec["ts_ms"] = raw.get("ts_ms")
            rec["src"] = raw.get("src")
            counts[rec["type"]] += 1

            magic = struct.unpack_from("<I", data, 0)[0] if len(data) >= 4 else None
            if want is not None and magic != want:
                continue
            if out_fh:
                out_fh.write(json.dumps(rec) + "\n")
            if args.limit == 0 or shown < args.limit:
                if not out_fh:
                    print(json.dumps(rec))
                    shown += 1

    if out_fh:
        out_fh.close()
        print(f"wrote decoded -> {args.out}")

    print("\n=== packet types ===", file=sys.stderr)
    for name, n in counts.most_common():
        print(f"  {name:>15}  {n}", file=sys.stderr)


if __name__ == "__main__":
    main()
