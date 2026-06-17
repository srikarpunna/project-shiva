import pytest
from pydantic import ValidationError

from edge.ingestion.schemas import (
    PresenceEntity,
    FallEntity,
    validate_payload,
    RawMessage,
    topic_suffix,
)


def test_presence_valid():
    e = PresenceEntity(presence=True, person_count=1)
    assert e.presence is True
    assert e.person_count == 1


def test_presence_unknown_fields_ignored():
    e = PresenceEntity.model_validate({"presence": True, "future_field": "x"})
    assert e.presence is True


def test_presence_nulls_allowed():
    e = PresenceEntity(presence=None, person_count=None)
    assert e.presence is None


def test_presence_person_count_negative_one_allowed():
    e = PresenceEntity(person_count=-1)
    assert e.person_count == -1


def test_validate_payload_known_topic():
    result = validate_payload("ruview/room1/presence", {"presence": True, "person_count": 2})
    assert result is not None
    assert isinstance(result, PresenceEntity)


def test_validate_payload_unknown_topic_returns_none():
    result = validate_payload("ruview/room1/unknown_entity", {"x": 1})
    assert result is None


def test_raw_message_roundtrip():
    msg = RawMessage(ts_ms=1700000000000, seq=1, topic="t", payload={"a": 1})
    restored = RawMessage.model_validate_json(msg.model_dump_json())
    assert restored == msg


def test_topic_suffix():
    assert topic_suffix("ruview/room1/presence") == "presence"
    assert topic_suffix("fall") == "fall"
