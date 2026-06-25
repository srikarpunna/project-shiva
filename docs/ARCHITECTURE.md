# Architecture — how it works, how it ships, what could break

Companion to `docs/HARDWARE_BRINGUP.md` (the physical setup) and the README roadmap.
This doc answers three questions that came up during bring-up:

1. How do the 3 boards talk to each other in real time?
2. We train on our own home — how would a *customer's* home work? Is it even possible?
3. What are we over-assuming? (the honest risk list)

Everything here is grounded in the **decoded firmware** and the **one real capture** we
have. Where something is unproven, it says so. No claims of accuracy we haven't earned.

---

## 1. How the 3 boards communicate

There are **two separate planes**. Don't confuse them.

### Data plane — boards → hub (sensing)
- Each board is a **node**, keyed by `node_id` = which room it sits in.
- Every node streams UDP datagrams to **one aggregator/hub IP on port 5005**.
- Star topology: nodes don't send sensing data to each other, only to the hub.
- This is the `0xC5110001` raw CSI (our input) + the vendor hint packets.
- No broker, no MQTT, no topics. Just UDP to one IP. (Corrects the old plan.)

```
  [node: bedroom] ─┐
  [node: living ] ─┼──UDP :5005──> [hub / this Mac] ──> capture + (later) detector
  [node: kitchen] ─┘
```

### Sync plane — boards ↔ boards (clock only)
- Nodes talk to *each other* over ESP-NOW for **clock sync** — the `0xC511A110` packets.
- Purpose: line up timestamps across boards (~104 µs offset seen) so the hub can fuse
  "same moment" across rooms later. One node is leader, others follow.
- `0xC5118100` = mesh node-health/role gossip.
- **Neither carries sensing data.** It's plumbing. Our detector ignores both.

**Why this split matters:** presence/fall/breathing per room need only the *data plane* —
one node's CSI is enough for its own room. The *sync plane* only matters once we try to
**count people** or fuse across rooms (roadmap L5), where we line up multiple nodes' views
of the same instant.

---

## 2. Shipping to other homes — the real answer

### The hard truth about WiFi sensing
CSI is **environment-specific**. The signal is the WiFi wave bouncing off *this room's*
walls, furniture, bodies. Train a model on our apartment and it learns *our* apartment —
move it to a customer's home and it's worthless.

So: **per-customer ML retraining does not scale.** We can't ship a box that needs us to
collect labeled falls in every customer's house. That's the trap.

### What we ship instead: physics detectors + a short calibration
The detectors are built on **physics that holds in any room**, not on a memorized house:

| Detector | Physics (room-independent) | What calibration learns (per home) |
|----------|----------------------------|------------------------------------|
| **Presence** | Body changes the signal vs an empty room | What "empty" looks like *here* (baseline) |
| **Motion / walking** | Moving body = fast signal change | How big a change counts as motion *here* |
| **Fall** | Sudden phase acceleration then stillness | Normal-motion ceiling *here* |
| **Breathing** | Chest = tiny periodic 0.1–0.5 Hz phase wobble | Noise floor *here* |

The *method* is universal. Only the *thresholds* are local — and those come from a
**5-minute calibration wizard** the customer runs at install, not from us:

1. "Leave the house for 2 minutes." → records the **empty baseline** for this home.
2. "Walk around each room normally." → sets the **motion threshold** for this home.
3. (Optional) "Sit still on the couch 1 minute." → checks breathing is detectable here.

After that, the box runs on its own. No cloud training per customer.

### What our home captures are actually for
**Not** to train a one-house model. They prove the **method works at all** —
that the physics detector can separate empty from occupied, catch a fall, see breathing.
If it works in our home with honest thresholds, it works in others *after their own
calibration*. We're validating the recipe, not cooking one meal.

### The exception: counting people (L5)
Counting is the one thing physics alone can't do — it genuinely needs ML + labeled data,
and a single cheap board can't do it reliably (our one capture: vendor counted "4" with 2
people present). So counting ships **last**, **coarse** (0 / 1 / 2+), and only where we
have a 3-node mesh in one room. It's a bet, not a guarantee. See risk #6.

---

## 3. What we're over-assuming (the honest risk list)

Each row is an assumption we're carrying that is **not yet proven**. The captures ahead
exist to try to **break** these, not confirm them.

| # | Assumption | Why it might be wrong | How we test it |
|---|------------|------------------------|----------------|
| 1 | Empty home = clean, quiet signal | Fans, AC, curtains, **pets**, neighbor WiFi all move the wave → false motion in an empty house. Biggest false-alarm risk. | Capture `empty` **with fan/AC on**, not silent. |
| 2 | 1 node per room is enough | Fall/breathing may need 2+ nodes for coverage. | Test detectors on 1-node captures; add nodes if recall is bad. |
| 3 | Breathing is extractable in a real home | A still person far from the board may be invisible. We haven't shown WE can pull it (vendor's number is a guess). | Capture `breathing` from across the room. |
| 4 | Fall = phase-acceleration spike | Plausible but **zero real falls captured**. Could miss soft falls or fire on sitting down fast. | Capture one controlled fall onto a mattress. |
| 5 | A 5-min calibration holds over time | CSI **drifts** — move a couch, router hops channel (our 2437/ch6 can change) → baseline stale. | Re-capture `empty` days apart; measure drift. |
| 6 | Coarse counting works on a 3-node mesh | Might not work at all on this hardware. | Prove L1–L4 first; treat L5 as optional. |
| 7 | UDP stream stays stable for hours/days | No retransmit; long-run packet loss untested. | Long-duration capture; watch for gaps. |

### The discipline that protects us from all of this
We **assume nothing works** until a real labeled capture proves it.
- `UNVALIDATED_NO_REAL_DATA = True` stays locked until the eval rig **passes on real
  labeled data**, referenced by run ID.
- Vendor vitals (`0002/0003/0006`) = hints, never ground truth.
- Eval rig returns **NO_DATA / cannot-certify** — never a green pass — until that run exists.
- The LLM is **never** in the real-time detection or alerting path.
- No synthetic data. No video, no audio, no location tracking.

---

## Build order (life-safety first)

| Layer | What | Status |
|-------|------|--------|
| L0 | Capture real labeled CSI | ✅ started (occupied=1; empty/breathing/walking/fall=0) |
| L1 | Presence (someone vs empty room) | next — needs `empty` on disk |
| L2 | Fall detection | after L1 |
| L3 | Breathing + stop-breathing alert | after L2 |
| L4 | Per-room routing (which room) | after L3 |
| L5 | Coarse count (0/1/2+) | last, optional, needs 3-node mesh |

**Immediate next step: capture `empty`.** Everything downstream is blocked on it.
