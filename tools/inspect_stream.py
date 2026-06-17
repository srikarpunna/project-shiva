"""
Inspect the live MQTT stream and print every unique topic + one sample payload.

Run this the moment your puck connects. It answers V1–V10 in one glance:
  - exact topic namespace (V1)
  - presence type (V2), person_count range (V3)
  - breathing_rate units / nulls (V4)
  - heart_rate presence (V5)
  - fall event type (V6)
  - zones schema (V7)
  - message rate (V8)
  - QoS / retained (V9)
  - rssi structure (V10)

Usage:
    python tools/inspect_stream.py [--broker localhost] [--port 1883] [--duration 30]
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
import time
from collections import defaultdict

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))


async def _inspect(broker: str, port: int, duration: int) -> None:
    import paho.mqtt.client as mqtt

    seen: dict[str, dict] = {}
    counts: dict[str, int] = defaultdict(int)
    start = time.time()

    ready = asyncio.Event()
    loop = asyncio.get_running_loop()
    queue: asyncio.Queue = asyncio.Queue()

    def on_connect(client, userdata, flags, rc, props=None):
        if rc == 0:
            client.subscribe("#")
            loop.call_soon_threadsafe(ready.set)
            print(f"Connected to {broker}:{port}. Listening for {duration}s...\n")
        else:
            print(f"Connection failed rc={rc}")

    def on_message(client, userdata, msg):
        try:
            payload = json.loads(msg.payload.decode())
        except Exception:
            payload = {"_raw": msg.payload.decode(errors="replace")[:200]}
        loop.call_soon_threadsafe(queue.put_nowait, (msg.topic, payload, msg.qos, msg.retain))

    client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2)
    client.on_connect = on_connect
    client.on_message = on_message
    client.connect_async(broker, port)
    client.loop_start()

    await asyncio.wait_for(ready.wait(), timeout=10)

    deadline = start + duration
    while time.time() < deadline:
        try:
            topic, payload, qos, retain = await asyncio.wait_for(
                queue.get(), timeout=deadline - time.time()
            )
        except asyncio.TimeoutError:
            break
        counts[topic] += 1
        if topic not in seen:
            seen[topic] = {"payload": payload, "qos": qos, "retain": retain}

    client.loop_stop()
    client.disconnect()

    elapsed = time.time() - start
    print(f"\n{'='*70}")
    print(f"UNIQUE TOPICS ({len(seen)}) — {sum(counts.values())} messages in {elapsed:.1f}s")
    print(f"{'='*70}")
    for topic in sorted(seen):
        rate = counts[topic] / elapsed
        meta = seen[topic]
        print(f"\n  TOPIC : {topic}")
        print(f"  rate  : {rate:.2f} msg/s   qos={meta['qos']}  retain={meta['retain']}")
        print(f"  sample: {json.dumps(meta['payload'], indent=4)}")

    print(f"\n{'='*70}")
    print("TODO(verify) resolution guide:")
    print("  V1  — topic namespace = prefixes above")
    print("  V2  — presence field type from sample payloads")
    print("  V3  — person_count range from samples")
    print("  V4  — breathing_rate units from samples (null sentinel visible if absent)")
    print("  V5  — heart_rate topic present above?")
    print("  V6  — fall topic: does it send True then False or just True?")
    print("  V7  — zones field structure in motion/presence samples")
    print(f"  V8  — message rates above ({rate:.2f} msg/s for last seen topic)")
    print("  V9  — qos and retain flags above")
    print("  V10 — rssi per-puck vs per-person: check if it's in a list or scalar")


def main() -> None:
    p = argparse.ArgumentParser(description="Inspect live MQTT stream topics + payloads")
    p.add_argument("--broker", default="localhost")
    p.add_argument("--port", type=int, default=1883)
    p.add_argument("--duration", type=int, default=30,
                   help="Seconds to listen before printing summary")
    args = p.parse_args()
    try:
        asyncio.run(_inspect(args.broker, args.port, args.duration))
    except KeyboardInterrupt:
        print("\nStopped.")


if __name__ == "__main__":
    main()
