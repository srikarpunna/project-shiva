from __future__ import annotations

import asyncio
import json
import logging
from typing import AsyncIterator

import paho.mqtt.client as mqtt

from config.base import MqttConfig
from edge.ingestion.schemas import RawMessage
from edge.sources.base import Source

logger = logging.getLogger(__name__)


class MqttSource(Source):
    """
    Connects to a Mosquitto broker and streams validated RawMessages.

    Reconnects automatically with exponential backoff up to
    MqttConfig.reconnect_delay_max seconds.
    """

    def __init__(self, cfg: MqttConfig) -> None:
        self._cfg = cfg
        self._queue: asyncio.Queue[RawMessage] = asyncio.Queue()
        self._seq = 0
        self._connected = asyncio.Event()
        self._client: mqtt.Client | None = None

    # ------------------------------------------------------------------
    # paho callbacks (run in paho's background thread)
    # ------------------------------------------------------------------

    def _on_connect(
        self,
        client: mqtt.Client,
        userdata: object,
        flags: dict,
        rc: int,
        properties: object = None,
    ) -> None:
        if rc != 0:
            logger.error("MQTT connect failed rc=%d", rc)
            return
        logger.info("MQTT connected to %s:%d", self._cfg.host, self._cfg.port)
        client.subscribe(self._cfg.topic_prefix)
        # Signal from paho thread — use call_soon_threadsafe
        if self._loop:
            self._loop.call_soon_threadsafe(self._connected.set)

    def _on_disconnect(
        self,
        client: mqtt.Client,
        userdata: object,
        rc: int,
        properties: object = None,
    ) -> None:
        logger.warning("MQTT disconnected rc=%d — will reconnect", rc)
        if self._loop:
            self._loop.call_soon_threadsafe(self._connected.clear)

    def _on_message(
        self, client: mqtt.Client, userdata: object, msg: mqtt.MQTTMessage
    ) -> None:
        try:
            payload = json.loads(msg.payload.decode())
        except (json.JSONDecodeError, UnicodeDecodeError) as exc:
            logger.error(
                "SCHEMA_ERROR topic=%s decode_error=%s raw=%r",
                msg.topic,
                exc,
                msg.payload[:200],
            )
            return

        self._seq += 1
        raw = RawMessage.now(seq=self._seq, topic=msg.topic, payload=payload)
        if self._loop:
            self._loop.call_soon_threadsafe(self._queue.put_nowait, raw)

    # ------------------------------------------------------------------
    # Source interface
    # ------------------------------------------------------------------

    async def stream(self) -> AsyncIterator[RawMessage]:
        self._loop = asyncio.get_running_loop()
        client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2)
        client.on_connect = self._on_connect
        client.on_disconnect = self._on_disconnect
        client.on_message = self._on_message
        client.reconnect_delay_set(max_delay=self._cfg.reconnect_delay_max)
        self._client = client

        client.connect_async(self._cfg.host, self._cfg.port, self._cfg.keepalive)
        client.loop_start()

        try:
            while True:
                yield await self._queue.get()
        finally:
            client.loop_stop()
            client.disconnect()

    async def close(self) -> None:
        if self._client:
            self._client.loop_stop()
            self._client.disconnect()
