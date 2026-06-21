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

### UDP packet types seen on the wire (little-endian magic = first 4 bytes)
| Magic | Meaning | Rate | Status |
|-------|---------|------|--------|
| `0xC5110001` | Raw CSI frame — header (node/ant/subcarrier/freq/seq/rssi/noise) + per-subcarrier I/Q. Seen: 1 antenna × 64 subcarriers, freq 2437 MHz (ch 6). | ~9–20 Hz, **rises with motion** | documented |
| `0xC5110002` | Vitals — presence, breathing, heart rate, fall flag. **Vendor's own guess: a hint, NOT ground truth.** | 1 Hz | documented |
| `0xC5110003` | undocumented | ~1 Hz | **TODO: decode** |
| `0xC5110006` | undocumented, ~60 B | ~5 Hz | **TODO: decode** |
| `0xC511A110` | undocumented | sparse | **TODO: decode** |
| `0xC5118100` | undocumented | sparse | **TODO: decode** |

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
1. Decode the undocumented packet types + the `0xC5110001` / `0xC5110002` byte layouts.
2. Build a labeled corpus: timed captures of empty / still / walking / lying-still(breathing),
   each labeled, for the eval rig.
3. Capture a controlled `fall` at least once.
