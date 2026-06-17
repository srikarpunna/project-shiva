from __future__ import annotations

import os
from typing import Literal

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class MqttConfig(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="MQTT_")

    host: str = Field("localhost", description="Broker host")
    port: int = Field(1883, description="Broker port")
    # TODO(verify:V1) — confirm exact topic prefix RuView publishes under
    topic_prefix: str = Field("ruview/#", description="Subscription filter")
    keepalive: int = Field(60)
    reconnect_delay_max: int = Field(30)


class StoreConfig(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="STORE_")

    db_path: str = Field("data/raw.db", description="SQLite file path")


class LogConfig(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="LOG_")

    log_dir: str = Field("data/logs", description="Directory for JSONL capture files")


class AppConfig(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="APP_",
        env_file=".env",
        env_file_encoding="utf-8",
    )

    env: Literal["development", "production"] = Field(
        "development",
        description="Runtime environment. ReplaySource is blocked when production.",
    )
    source_type: Literal["mqtt", "replay"] = Field(
        "mqtt",
        description="Live MQTT or replay from captured log file.",
    )
    replay_log_path: str | None = Field(
        None,
        description="Path to JSONL log file for ReplaySource. Required when source_type=replay.",
    )
    replay_speed: float = Field(
        1.0,
        description="Replay speed multiplier. 1.0 = real time, 10.0 = 10× faster.",
        gt=0,
    )
    mqtt: MqttConfig = Field(default_factory=MqttConfig)
    store: StoreConfig = Field(default_factory=StoreConfig)
    log: LogConfig = Field(default_factory=LogConfig)


def load_config() -> AppConfig:
    return AppConfig()
