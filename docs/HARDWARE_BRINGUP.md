# Hardware Bring-Up — CSI Working (status note for humans + agents)

**Status: ✅ Real CSI captured to disk on real hardware. 2026-06-21.**
This is the Phase 3 milestone the whole build plan front-loaded. Everything before it
was an educated guess; everything after it is grounded in this.

> Share this file with another agent to bring it up to speed on the physical setup.
> No secrets here (WiFi password is NOT recorded — it lives only on the board's NVS).

---

## Hardware
- **3× ESP32-S3-DevKitC-1 N16R8** (16MB flash / 8MB PSRAM), LUIRSAY. One in use so far.
- First board MAC: `44:1b:f6:d3:1c:d8`.
- Enumerates as **native USB** `/dev/cu.usbmodem*` on macOS — **no CP210x driver needed**
  (RuView docs assume a CP210x `/dev/cu.SLAB_USBtoUART`; ours differs — fine).
- **Cable + port gotcha:** needs a **data-capable USB-C cable plugged directly into the Mac.**
  A Dell dock / USB hub blocked enumeration; a charge-only cable powers the RGB LED but gives
  no serial. Symptom of a bad cable/port: board's RGB demo runs but `ls /dev/cu.*` shows no
  `usbmodem`.

## Firmware
- **RuView `esp32-csi-node` v0.6.7**, repo **[ruvnet/RuView](https://github.com/ruvnet/RuView)**
  (renamed from `ruvnet/wifi-densepose`; "RuView" = ruvnet).
- Cloned to `vendor/RuView`. Flashed the **top-level S3 `release_bins`**:
  `bootloader.bin@0x0`, `partition-table.bin@0x8000`, `ota_data_initial.bin@0xf000`,
  `esp32-csi-node.bin@0x20000`, via `esptool --chip esp32s3 --flash_mode dio --flash_size 8MB`.
  (Verified both top-level and `s3-adr110/` app bins are ESP32-S3 images via `esptool image_info`.)
- WiFi + aggregator IP provisioned with `vendor/RuView/firmware/esp32-csi-node/provision.py`
  (requires `pip install esp-idf-nvs-partition-gen`). Credentials written to NVS, stay on-device.

## ⚠️ Key discovery — corrects the pre-hardware plan
The original README/plan assumed RuView published over **MQTT**. **It does not.**
The firmware **streams UDP datagrams to an aggregator IP on port 5005** — no broker, no topics.
The MQTT ingestion path (`mqtt_source.py`, `log_harness.py`, `inspect_stream.py`, the V1–V10
*topic* questions) is **superseded for capture** by `tools/udp_capture.py`.

### UDP packet types — DECODED from firmware C structs (2026-06-21)
All layouts transcribed from `vendor/RuView/firmware/esp32-csi-node/main/`
(`csi_collector.{c,h}`, `edge_processing.h`, `rv_feature_state.h`, `rv_mesh.h`),
verified by parsing the real capture with `tools/decode_csi.py`. Little-endian.

| Magic | Meaning | Bytes | Rate | Sensing? |
|-------|---------|-------|------|----------|
| `0xC5110001` | **Raw CSI frame.** 20B header (node, n_ant, n_sub, freq, seq, rssi, noise, ppdu, flags-bit4=sync) + int8 I/Q pairs. Real: 1 ant × 64 sub, 2437 MHz (ch6), 148 B. | 148 | ~9–20 Hz, **rises with motion** | ✅ **our detector input** |
| `0xC5110002` | **Vitals.** flags bit0=presence/1=fall/2=motion; breathing=BPM×100; heartrate=BPM×10000; rssi; n_persons; motion_energy f32; presence_score f32; ts_ms. **Vendor hint, NOT ground truth.** | 32 | 1 Hz | hint only |
| `0xC5110003` | **Feature vector** — node, seq, ts_us, `features[8]` f32. Vendor-derived. | 48 | ~1 Hz | hint only |
| `0xC5110006` | **Feature state** — motion/presence/respiration/heartbeat/anomaly/env_shift/coherence scores + quality_flags + crc32(IEEE, [0..end-4]). Vendor-derived. | 60 | ~5 Hz | hint only |
| `0xC5110004` | Fused vitals (CSI+mmWave). Only emitted if mmWave board attached — **not seen.** | 48 | — | n/a |
| `0xC5110005` | Compressed CSI (reassigned from old 0003). | var | — | n/a |
| `0xC511A110` | **C6 mesh time-sync** — leader/epoch/local µs. **Plumbing, not sensing.** | 32 | sparse | ignore |
| `0xC5118100` | **Mesh envelope** — node health/status/role. **Plumbing, not sensing.** | var | sparse | ignore |

Decode + inspect: `python tools/decode_csi.py data/csi_raw/csi_*.jsonl --magic 0xc5110002`

## Tools (all real-data-only, no synthetic)
- `tools/udp_capture.py` — raw capture to `data/csi_raw/`.
- `tools/decode_csi.py` — parse a capture by magic into typed records (all 8 magics).
- `tools/capture_labeled.py` — timed session with a **human-asserted ground-truth label** →
  `data/csi_labeled/<label>/` + appends `manifest.jsonl`.
- `tools/live_view.py` — tails an in-progress capture **file** (not the socket) and renders a
  live dashboard; runs alongside a capture without fighting for UDP :5005.

## Labeled corpus (started 2026-06-21)
| label | runs | notes |
|-------|------|-------|
| `occupied` | 1 | 180 s, 2686 pkts (1373 raw CSI). 2 people calm (phone + typing). |
| `empty` | 0 | **NEEDED — most important negative; empty home must never alert.** |
| `breathing` | 0 | solo, low-motion. |
| `walking` | 0 | motion positive. |
| `fall` | 0 | controlled, once. |

Eval rig is blocked until ≥2 contrasting classes exist (esp. `empty` vs `occupied`).

**Vendor derived layer fails the 2-person test:** across the `occupied` capture the vendor
`n_persons` read a constant **4** and the `presence` flag said "empty" in 161/165 packets — with
two people present. One 1-antenna/64-subcarrier ESP32 cannot count people, and we never count
anyway (location-based "someone", not numbers). We build OUR detector on the **raw CSI** and
validate against these labels — never against vendor vitals.

## Capture tool
`tools/udp_capture.py` — binds UDP `:5005`, writes every packet losslessly
(`ts_ms`, `seq`, `src`, `magic`, `len`, `payload_b64`) to `data/csi_raw/csi_*.jsonl`.
`data/` is gitignored — these are recordings of a real home and **must stay local**.
First capture: 131 packets / 10 s from board at `192.168.1.82`; raw CSI yield rose while a
hand was waved near the board (the physics, visible directly in the capture rate).

## How to reproduce in one minute (board already flashed + provisioned)
```bash
python3 tools/udp_capture.py --duration 10   # wave a hand near the board while it runs
# watch 0xC5110001 pkt/s climb; serial log shows presence/motion jump 0.00 -> non-zero
```

## Discipline (unchanged — still binding)
- Vendor vitals (`0xC5110002`) = a hint, never ground truth.
- We capture **raw CSI** and validate **our own** detection against **real labeled data**.
- `UNVALIDATED_NO_REAL_DATA = True` stays until the eval rig passes on labeled real data.
- **No synthetic data, ever.** No video, no audio, no location tracking.

## Next steps
1. ~~Decode packet types~~ ✅ done 2026-06-21 — all 8 magics decoded (`tools/decode_csi.py`).
2. Build a labeled corpus: timed captures of empty / still / walking / lying-still(breathing),
   each labeled, for the eval rig.
3. Capture a controlled `fall` at least once.
4. Build OUR presence/breathing detector on the raw CSI (`0xC5110001`); validate against the
   labeled corpus — never against the vendor vitals (`0002`/`0003`/`0006`), which are hints.
