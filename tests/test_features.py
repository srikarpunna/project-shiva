"""
Tests for feature extraction mechanics.

Fixtures are minimal hand-written RawMessages that test code logic only.
No fixture claims to represent a real sensor reading or real event.
"""
import pytest
from edge.detection.features import extract, FeatureVector
from edge.ingestion.schemas import RawMessage


def _msg(topic_suffix: str, payload: dict, ts_ms: int = 1000) -> RawMessage:
    return RawMessage(ts_ms=ts_ms, seq=0, topic=f"ruview/{topic_suffix}", payload=payload)


def test_empty_window_returns_all_none():
    vec = extract([], window_start_ms=0, window_end_ms=1000)
    assert vec.presence_fraction is None
    assert vec.mean_breathing_rate is None
    assert vec.motion_fraction is None
    assert vec.fall_event_count is None


def test_presence_fraction_computed():
    msgs = [
        _msg("presence", {"presence": True}),
        _msg("presence", {"presence": True}),
        _msg("presence", {"presence": False}),
        _msg("presence", {"presence": False}),
    ]
    vec = extract(msgs, 0, 5000)
    assert vec.presence_fraction == pytest.approx(0.5)


def test_fall_event_counted():
    msgs = [
        _msg("fall", {"fall": True}),
        _msg("fall", {"fall": True}),
        _msg("motion", {"motion": False}),
    ]
    vec = extract(msgs, 0, 5000)
    assert vec.fall_event_count == 2


def test_fall_false_not_counted():
    msgs = [_msg("fall", {"fall": False})]
    vec = extract(msgs, 0, 5000)
    assert vec.fall_event_count is None


def test_breathing_mean_and_std():
    msgs = [
        _msg("breathing", {"breathing_rate": 10.0}),
        _msg("breathing", {"breathing_rate": 20.0}),
    ]
    vec = extract(msgs, 0, 5000)
    assert vec.mean_breathing_rate == pytest.approx(15.0)
    assert vec.std_breathing_rate is not None and vec.std_breathing_rate > 0


def test_breathing_std_none_for_single_value():
    msgs = [_msg("breathing", {"breathing_rate": 15.0})]
    vec = extract(msgs, 0, 5000)
    assert vec.std_breathing_rate is None


def test_zones_counted():
    msgs = [
        _msg("motion", {"motion": True, "zones": ["bedroom", "hallway"]}),
        _msg("motion", {"motion": True, "zones": ["bedroom"]}),
    ]
    vec = extract(msgs, 0, 5000)
    assert vec.zone_count == 2


def test_unknown_topic_suffix_ignored():
    msgs = [_msg("unknown_entity", {"foo": "bar"})]
    vec = extract(msgs, 0, 5000)
    # Nothing explodes, all fields None
    assert vec.presence_fraction is None
    assert vec.fall_event_count is None


def test_none_payload_fields_not_included():
    # person_count=-1 (unknown sentinel) should be excluded from max
    msgs = [_msg("presence", {"presence": True, "person_count": -1})]
    vec = extract(msgs, 0, 5000)
    # -1 filtered out → no valid counts
    assert vec.max_person_count is None
