# Project Shiva — Green Dot

**Camera-free, wearable-free home guardian.**

Two small sensor units plug into a home — one per main room. They detect whether someone is present, moving, resting, and breathing, using WiFi radio reflections — no camera, no microphone, no wearable. A caregiver opens a web app from anywhere in the world and sees one plain sentence: everything looks normal, or something is wrong. Alerts fire only on a real emergency — a fall, or breathing stopping while someone is present. The monitored person does nothing — no app, no login, no device.

> Think **"Find My," but for *are they okay?*** instead of *where is their phone?*

---

## Non-negotiable safety rules

Read these before touching any code. Violating them is a defect, not a style issue.

| Rule | Why |
|------|-----|
| LLM is **never** in the alert path | If the LLM is down, alerts must still fire |
| Safety logic runs **on-edge** | Must work with internet down |
| **No synthetic sensor data** — ever | Fake data produces fake confidence. Real emergencies look nothing like random numbers. |
| Fail loud, fail safe | Unknown state = `degraded`, never false-green |
| Degrade honestly | If still learning, the app says so |
| No video, no audio, no location | If a feature needs these, don't build it |
| Never count or name people | WiFi sensing knows "someone is there," not who or how many. Show "someone," never "2 people," never "Mom" |
| Empty home is never an emergency | Families travel. Absence is shown calmly, never as an alert |
| One product, not two personas | Watchfulness is a setting, not an identity the caregiver must understand |
| Never imply medical-grade accuracy | Not a medical device. Copy and UI must never claim it is |

---

## Honest current state

```
Phase 1 complete — scaffolding only
UNVALIDATED_NO_REAL_DATA = True
Hardware: not yet ordered
Real sensor data: zero bytes ever captured
Tests: 50 passing — mechanics only, no real signal
```

Green CI does not mean this is safe to deploy. The pipeline is built and wired. It has never seen a real breath.

---

## What v1 delivers (target scope — proven only after hardware)

Everything below is the **intended** v1 capability. None of it is proven until real sensor data exists. Each capability stays behind `UNVALIDATED_NO_REAL_DATA` until a real device proves it on real signal. No claim here is built on assumption — it becomes true only after hardware arrives and the data backs it.

**The kit:** two small sensor units per home — one per main room (e.g. bedroom + living room). One unit covers one room well. Whole-home coverage = two units.

**What the caregiver sees (passive, glance):**

| Capability | Confidence | Note |
|------------|-----------|------|
| Someone home vs empty | Strong | presence is the most reliable WiFi-sensing signal |
| Which room | Strong | one unit per room |
| Moving vs resting | Strong | motion energy |
| Breathing present + steady | Moderate | only when the person is still; best while sleeping. Skipped when moving — motion already proves life |
| Fall | To prove on hardware | physics is sound; must be verified on a real device before trusted |

**What it alerts on — only a real emergency:**
- **Fall** — sudden motion spike, then dead stillness
- **Breathing stops while a person is present** — they were there, the breathing signal dies = possible collapse

**What v1 deliberately does NOT do:**
- Count exact people — shows "someone," never "2 people" (WiFi sensing can't count cleanly)
- Identify who — cannot tell Mom from Dad; no names, ever
- Alert on an empty home — family travels; empty is shown calmly, never as alarm
- Learn personal patterns — "unusual for this home" is v2, and only after real data earns it
- Camera, audio, location, wearable — never

**One product, not two personas.** Watchfulness is a setting — "Standard" by default, "Extra watchful" for recovery/overdose-risk homes (tighter thresholds, faster escalation). The caregiver never picks a "mode identity"; they install, glance, and optionally turn watchfulness up.

---

## Full phase map

| Phase | What | Status | Blocked on |
|-------|------|--------|-----------|
| 1 | Ingestion + logging + raw store | **Done** | — |
| 2 | Layer 1 + Layer 2 scaffolding + eval rig | **Done** | — |
| 3 | Hardware + real signal on disk | **Next** | ESP32-S3 board arriving |
| 4 | Resolve V1–V10, fix schemas, validate pipeline | Blocked | Phase 3 |
| 5 | Label real events, run eval rig, set thresholds | Blocked | Phase 4 |
| 6 | Clear validation gate, wire Calm mode end-to-end | Blocked | Phase 5 |
| 7 | Web app — caregiver view (web first) | Parallel | with Phase 4–6 |
| 8 | Escalation state machine — push → call → emergency | Blocked | Phase 6 |
| 9 | Layer 3: longitudinal drift | Blocked | weeks of real data |
| 10 | Layer 4: LLM communication layer | Blocked | Phase 6 |
| 11 | Guard mode (recovery / overdose risk) | Blocked | Phase 6 + field validation of Calm |
| 12 | Ship Standard watchfulness to one real home | Blocked | Phase 8 + eval targets met |
| 13 | Native iOS / Android apps | Blocked | Phase 7 web app proven in the field |

---

## Phase 1 — Ingestion + logging + raw store

### Vision
Get every byte the sensor unit ever emits onto disk, timestamped, validated, and queryable. Nothing downstream is trustworthy without this. This is the foundation.

### What was built
- `edge/sources/mqtt_source.py` — connects to Mosquitto, auto-reconnects, streams typed `RawMessage` objects
- `edge/sources/replay_source.py` — replays real captured logs at configurable speed. Hard-blocked in production. The only permitted non-live data source.
- `edge/ingestion/schemas.py` — Pydantic models per topic. Unknown fields ignored. Missing required fields = logged `SCHEMA_ERROR`, message dropped, never silently passed.
- `edge/ingestion/service.py` — FastAPI service: source → validate → log → store. `/health` returns `degraded` if broker disconnected, schema error rate too high, or store failing.
- `edge/store/sqlite_store.py` — SQLite time-series store behind a swappable interface. Zero callers touch SQL directly.
- `tools/log_harness.py` — rotating daily JSONL capture. This is the authoritative replay corpus.
- `tools/inspect_stream.py` — on hardware day, prints every unique topic + sample payload. Resolves all 10 schema unknowns (V1–V10) in one run.
- `tools/label_cli.py` — CLI to annotate real events in captured logs for the eval rig.

### What's needed to run it
```bash
python3.11 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
docker compose up          # starts local Mosquitto broker
python tools/log_harness.py --broker localhost
```

### Open unknowns (TODO(verify))
All tagged `TODO(verify:V1–V10)` in `edge/ingestion/schemas.py`. Unresolvable without real hardware:

| ID | Question |
|----|----------|
| V1 | Exact MQTT topic namespace from RuView |
| V2 | `presence` — bool or enum string? |
| V3 | `person_count` — int/float? sentinel for unknown? |
| V4 | `breathing_rate` units and null sentinel |
| V5 | `heart_rate` — exists in firmware? separate topic? |
| V6 | `fall` — edge event or sustained state? |
| V7 | `zones` — list[str]? dict? |
| V8 | Message rate per topic |
| V9 | QoS level, retained messages on reconnect |
| V10 | `rssi` — per-unit or per-person? |

### Exit criteria → Phase 3
- Real sensor unit connects to local Mosquitto
- `tools/inspect_stream.py` prints topics and payloads
- `tools/log_harness.py` writes growing JSONL to `data/logs/`
- Schema models updated with real field names
- At least one full day of real logs on disk

---

## Phase 2 — Layer 1 + Layer 2 scaffolding + eval rig

### Vision
Build the full detection + fusion pipeline before data exists so that when data arrives, it flows straight through rather than blocking on architecture. Every unknown is a typed config field. Every path that requires real data raises loudly instead of returning fake confidence.

### What was built

**Layer 1 — per-home anomaly detection** (`edge/detection/`)
- `features.py` — `extract()` converts a window of `RawMessage` objects to a `FeatureVector`. `None` means sensor absent, not zero.
- `baseline.py` — `HomeBaseline` maintains a rolling IsolationForest per home. Returns `AnomalyScore` with `baseline_stable=False` during learning period, `unvalidated=True` always until gate cleared.
- `detector.py` — `DetectorService` manages windowing and one `HomeBaseline` per home.
- `validation_gate.py` — `UNVALIDATED_NO_REAL_DATA = True`. Every score carries this flag. Cleared only by `tools/clear_validation.py` after a human reads real eval metrics.

**Layer 2 — fusion + false-alarm suppression** (`edge/fusion/`)
- `calibration.py` — `ScoreCalibrator` wraps Platt scaling or isotonic regression. Raises `CalibrationNotFitted` if called before fit. Rejects single-class label sets.
- `fusion.py` — `FusionService` produces one calibrated `confidence ∈ [0,1]` and discrete state `green/yellow/red/unknown`. Returns `cannot_certify=True` while unvalidated, baseline unstable, or calibrator unfitted. State thresholds default to `0.0` — emit `unknown` until a human sets real values.
- `eval_rig.py` — reads real labeled JSONL logs + scored windows. Reports FNR, FPR, latency-to-alert, Brier score, reliability bins. Returns `NO_DATA` when real labeled data is absent. Does not pick thresholds. A human reads the output and sets config.

**Validation clearance** (`tools/clear_validation.py`)
- Requires `--run-id` (from `EvalResult.run_id`) + `--confirmed` flag
- Patches `UNVALIDATED_NO_REAL_DATA = False` and writes audit record to `data/validation_runs.jsonl`
- Commit the patched file with the run ID in the commit message

### What's needed to use it
Nothing yet. Calibrator and eval rig require real labeled data. They will raise or return `NO_DATA` until then. That is correct behavior.

### Exit criteria → Phase 4 / Phase 5
- `tools/inspect_stream.py` output used to fix all `TODO(verify)` in schemas
- Real logs captured and replayed through pipeline without schema errors
- At least `MIN_LABELED_EVENTS` (currently 10) real events labeled in `label_cli.py`
- `eval_rig.run()` returns `status=COMPLETE`
- Human reads FNR/FPR curve, sets `FUSION_CALM__YELLOW_THRESHOLD` and `FUSION_CALM__RED_THRESHOLD`
- `clear_validation.py --run-id <id> --confirmed` clears the gate

---

## Phase 3 — Hardware + real signal on disk

### Vision
This is the most important phase in the project. Not because it's technically complex — it isn't — but because everything before it is an educated guess about the real world and everything after it is grounded in reality. The goal is one thing: real CSI flowing to disk.

### What's needed
- **ESP32-S3-DevKitC-1** (N8R8 or N16R8) — ~$12. The CSI sensing node.
- **Seeed SenseCAP MR60BHA2** mmWave module — ~$40. Needed for breathing rate reliability in Guard mode.
- RuView Docker container running locally: `docker run ruvnet/wifi-densepose --source esp32 --mqtt`
- Local Mosquitto broker (already in `docker-compose.yml`)

### Day-one sequence
```bash
# 1. Start broker
docker compose up

# 2. Connect board, run RuView container (see RuView docs for exact flags)
docker run ruvnet/wifi-densepose --source esp32 --mqtt mqtt://localhost:1883

# 3. Resolve all unknowns in 60 seconds
python tools/inspect_stream.py --broker localhost --duration 60

# 4. Fix schemas with real topic names
# Edit edge/ingestion/schemas.py — update TOPIC_SCHEMA_MAP keys

# 5. Start capturing
python tools/log_harness.py --broker localhost --out data/logs/

# 6. Verify pipeline end-to-end
python -m edge.ingestion.service
curl localhost:8000/health
```

### The only test that matters on day one
Breathe slowly while watching `inspect_stream.py` output. Does `breathing_rate` change? If yes, the sensor is working. If no, stop and debug before proceeding.

### Exit criteria → Phase 4
- At least 24 hours of continuous real logs in `data/logs/`
- No schema errors in ingestion service logs
- `breathing_rate` observed to respond to real breathing
- `fall` event observed at least once (controlled test: simulate a fall)

---

## Phase 4 — Schema fixes, pipeline validation

### Vision
Make the pipeline actually correct, not just structurally sound. Every `TODO(verify)` gets resolved or explicitly deferred with a real reason.

### What to do
1. Run `tools/inspect_stream.py` output → update every `TODO(verify:V1–V10)` in `schemas.py`
2. Update `TOPIC_SCHEMA_MAP` keys with real topic names
3. Replay 24h log through ingestion service — zero schema errors expected
4. Check `/health` — should show `broker_connected`, no schema errors, no store errors
5. Run `pytest` — still 50/50

### Exit criteria → Phase 5
- All `TODO(verify:V1–V10)` resolved in code
- 24h replay produces zero `SCHEMA_ERROR` log lines
- `pytest` still green

---

## Phase 5 — Labeling, eval rig, threshold setting

### Vision
The first time numbers mean something. Not code correctness — real-world performance. FNR and FPR on real events. The eval rig produces a curve; a human picks the operating point.

### What to do
1. Run `tools/label_cli.py data/logs/raw_<date>.jsonl` — label fall events, normal stillness, normal activity
2. Replay labeled log through pipeline → produce scored windows JSONL
3. Run eval rig:
```python
from edge.fusion.eval_rig import run
result = run(label_path, scored_windows_path)
print(result.summary)
```
4. Read FNR/FPR curve. Set `FUSION_CALM__YELLOW_THRESHOLD` and `FUSION_CALM__RED_THRESHOLD` in config based on acceptable tradeoffs.
5. Run `tools/clear_validation.py --run-id <result.run_id> --confirmed`
6. Commit `validation_gate.py` with run ID in message

### What the eval rig reports
```
Threshold curve (set operating point by reading FNR/FPR tradeoff):
  threshold       FNR       FPR   latency_ms
      0.300     0.100     0.250         8200
      0.500     0.150     0.080         9100
      0.700     0.300     0.020        11000
```
You pick the row. The code never picks it for you.

### Exit criteria → Phase 6
- `UNVALIDATED_NO_REAL_DATA = False` in `validation_gate.py`
- Thresholds set in config with run ID reference
- Eval rig `status=COMPLETE` with at least 10 labeled events

---

## Phase 6 — Calm mode end-to-end

### Vision
First time the full pipeline runs live: sensor unit → ingestion → detection → fusion → escalation → caregiver notification. Calm mode only. One home. No Guard mode yet.

### What to build
- `edge/escalation/` — state machine: `green → yellow → red`. Soft confirm on yellow (push with "All good?" reply). Escalation ladder: stronger push → phone call → emergency contact.
- Wire `DetectorService` → `FusionService` → escalation in the ingestion service loop
- Web push integration for caregiver alerts (native APNs/FCM deferred to Phase 13)
- Twilio for SMS/voice call escalation

### Escalation state machine
```
green   — normal. No action.
yellow  — unusual. Send soft confirm: "Everything okay at Dad's?"
          If "All good" tapped → resolve, log as false alarm, retrain signal.
          If no response AND signal still bad after N minutes → escalate.
red     — likely emergency. Push alert → call → emergency contact.
          Timings from config. Calm mode: slower. Guard mode: faster.
```

### Exit criteria → Phase 7 (app) / Phase 8
- Live alert fires on a simulated fall in a real home
- Soft confirm flow works end-to-end
- False alarm tap captured and logged as labeled training signal
- Zero false alerts in 7 days of normal activity at one home

---

## Phase 7 — Web app (caregiver view, web first)

### Vision
The monitored person never sees this. The caregiver — often in another city or country — opens a web app and in two seconds knows their parent is okay. **Web first**, because anyone, anywhere, on any device, can open a link with no install. Native apps come later (Phase 13). The main screen is mostly one plain sentence — color is a secondary cue, never the only signal.

### The screens
```
1. Home / status
   — Pick which home (a caregiver may watch several)
   — ONE plain sentence: "Everything looks normal" / "No one home right now"
   — Time since last activity: "2m ago"
   — Small color cue (calm / needs attention) as a SECONDARY signal
   — Tap the activity line → per-room plain text:
       "Living room — someone moving, breathing steady
        Bedroom — quiet"

2. Alert
   — What happened, when, which room
   — Two actions: "I'll call them" / "All good (false alarm)"
   — "False alarm" tap = labeled training signal, captured to store

3. Settings
   — Who gets alerts, emergency contacts
   — Which rooms / units
   — Quiet hours
   — Watchfulness: Standard (default) / Extra watchful
```

### What v1 honestly shows
- Presence per room — "someone," never a head count, never a name
- Moving vs resting
- Breathing steady — only when the person is still; skipped when moving
- Empty home — shown calmly, never as an alert

The plain sentence is written by the LLM from raw numbers (cosmetic translation only). If the LLM is down, the app falls back to the structured state. The LLM is never in the alert path.

### Setup flow
1. Caregiver buys the kit (2 units) → ships to parent → parent plugs into USB (that's all they do)
2. Caregiver opens the web app → "Add home" → connects to that home's edge service
3. App shows "Getting to know your home — status goes live shortly"
4. Status goes live

### What to build
- Next.js / React PWA, mobile-first responsive
- Real-time status via WebSocket / SSE from the edge service
- Multi-home selector (one caregiver, many homes)
- Web push for alerts
- Pairing flow (add a home, connect to its edge service)
- **Data layer stubbed and CLEARLY marked until real hardware feeds it** — no synthetic sensor values are ever presented as real. The screen can be built and seen before hardware; the data behind it stays honestly empty/stubbed until a real device feeds it.

### Exit criteria → Phase 8
- Web app shows live status from one real home
- Alert screen receives and displays a real web push
- False alarm tap writes to store
- Monitored person has not opened the app, logged in, or been filmed

---

## Phase 8 — Escalation + real deployment

### Vision
First real deployment. One caregiver. One parent. Calm mode. Seven days without incident. This is the moment the product either earns trust or reveals what needs fixing.

### What to do
- Deploy edge service to Raspberry Pi in the parent's home
- Configure APNs production certificates
- Configure Twilio voice call fallback
- Set quiet hours per family preference
- Monitor `/health` and structured logs remotely
- After 7 days stable: evaluate whether to onboard a second home

### Exit criteria → Phase 9 / Phase 11
- 7 days continuous operation without false alert waking caregiver at night
- At least one real yellow event handled correctly (soft confirm resolved)
- Every alert decision reconstructable from logs
- `/health` stays `ok` for 7 days

---

## Phase 9 — Layer 3: longitudinal drift

### Vision
The slowest, most clinically meaningful signal. Week-over-week change in sleep patterns, nighttime bathroom trips, daytime activity levels. Not "I predict a fall" — never that. "Worth a check-in." This is the feature that separates a guardian from a panic button.

### What to build (`intelligence/drift/`)
- Weekly feature aggregation from raw store (sleep onset/offset, active hours, zone transitions)
- Drift detector on aggregated weekly features (mean/std shift, trend direction)
- Plain-language trend summary output for Layer 4 / caregiver digest

### Blocked on
- Weeks of real data from Phase 8
- Enough homes to distinguish real decline from individual variation

### Exit criteria → Phase 10
- Given 4+ weeks of real logs, produces honest trend report
- Distinguishes "stable" from "declining" with explicit uncertainty
- No acute-event overclaim ("worth a check-in," never "I predict a fall")

---

## Phase 10 — Layer 4: LLM communication

### Vision
The LLM's entire job is translation. It takes structured state from Layers 1–3 and turns it into calm, plain English. It never decides anything. If it goes down, detection and alerting keep working without any code change.

### What the LLM does
1. `"Mom's home, breathing normal, last moved 8 min ago"` — from current state
2. Weekly digest from Layer 3 drift output
3. Answer caregiver questions: "Has Dad been sleeping worse this week?" — retrieval over structured data, not inference
4. Phrase alert messages by severity

### What the LLM never does
- Touch detection decisions
- Gate an alert
- Be a dependency for any alert to fire

### Implementation (`intelligence/llm/`)
- Anthropic API, claude-sonnet-4-6 or later
- Structured inputs only — no raw sensor data to LLM
- Offline fallback: return structured state as-is if LLM unreachable

### Exit criteria → Phase 11
- Disabling the LLM API key leaves detection and alerting 100% functional
- Status sentences are calm, honest, non-medical in language
- Weekly digest accurately reflects Layer 3 trend output

---

## Phase 11 — "Extra watchful" profile (internally: Guard mode)

### Vision
Not a separate persona or app — the **same product** with a tighter operating point the caregiver turns on in Settings. Higher sensitivity, faster escalation, shorter confirm window. Intended for households with overdose or recovery risk — a bathroom with abnormal stillness for 3 minutes is a different signal than the same stillness in a bedroom at 2pm. The caregiver flips one toggle; everything else is identical.

### How it differs from Standard
| Parameter | Standard | Extra watchful |
|-----------|----------|----------------|
| Yellow threshold | Higher (fewer alarms) | Lower (catch more) |
| Confirm window | Longer | Shorter |
| Escalation speed | Slower | Faster |
| Breathing depression weight | Normal | Elevated |

### What to build
- Per-mode config already exists in `FusionConfig`
- Needs separate eval rig run on Guard-specific labeled scenarios
- Needs separate threshold setting — Guard FNR target is stricter than Calm
- App settings screen already has mode toggle

### Blocked on
- Calm mode field validation (Phase 8) — Guard only ships after Calm has earned trust
- Labeled Guard-mode events (overdose simulation is ethically complex — work with harm reduction org)

---

## Repo structure

```
project-shiva/
├── edge/                   # runs in-home: ingestion, layers 1-2, escalation
│   ├── ingestion/          # FastAPI service, Pydantic schemas
│   ├── sources/            # MqttSource (live), ReplaySource (real logs only)
│   ├── detection/          # Layer 1: feature extraction, IsolationForest baseline
│   ├── fusion/             # Layer 2: calibration, fusion, eval rig
│   ├── escalation/         # [Phase 6] state machine, APNs, Twilio
│   └── store/              # SQLite store (swappable interface)
├── intelligence/           # off-hot-path: never in alert path
│   ├── drift/              # [Phase 9] Layer 3: longitudinal change
│   └── llm/                # [Phase 10] Layer 4: status → plain language
├── app-web/                # [Phase 7] Next.js/React PWA: Status / Alert / Settings
├── app-native/             # [Phase 13] iOS / Android — after web proven
├── tools/                  # log_harness, inspect_stream, label_cli, clear_validation
├── config/                 # typed config, per-home overrides
└── tests/                  # 50 tests today, all mechanics-only
```

---

## Getting started (dev)

```bash
git clone <repo>
cd project-shiva
python3.11 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
docker compose up              # starts Mosquitto on :1883
python tools/log_harness.py    # connect sensor unit and start capturing
pytest                         # 50 passing, mechanics only
```

---

## Data discipline

`data/` is in `.gitignore` and must stay there forever. Once real sensor logs exist, they are recordings of a real person's home — sleep patterns, bathroom trips, breathing rate at 3am. They are sensitive. Keep them local. Never push to a remote.

The only permitted non-live data source is `ReplaySource` playing back real captured logs. It is hard-blocked in production config. There is no synthetic data path anywhere in this codebase, and there never will be.

---

## Definition of done (full product)

- Real CSI from a real sensor unit flows through ingestion → detection → fusion → escalation → app with no synthetic data anywhere on the live path
- Eval rig reports FNR/FPR/latency/calibration on labeled real events; thresholds set from those metrics
- Killing the LLM API key leaves detection and alerting fully working
- Web app shows an honest plain-language status sentence (color a secondary cue) across its three screens
- Calm and Guard modes are real, distinct, config-driven operating points
- Every alert decision is reconstructable from structured logs alone
- Nothing in code or copy implies medical-grade accuracy
- One real family has used Calm mode for 7+ days without a false-alarm wake-up call
