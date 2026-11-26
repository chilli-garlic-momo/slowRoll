from __future__ import annotations

import pytest

from slowroll.state import State, StateError, dump_marker, parse_marker


def test_round_trip():
    s = State(status="ramping", index=1, advanced_at="2026-11-27T10:00:00+00:00", strikes=1, unknown=2, reason=None)
    body = dump_marker(s) + "\n\n| table |"
    assert parse_marker(body) == s


def test_marker_is_one_line():
    assert "\n" not in dump_marker(State())


@pytest.mark.parametrize("body", ["", "hello", "\n<!-- slowroll:state {} -->"])
def test_missing_marker(body):
    with pytest.raises(StateError, match="slowroll apply"):
        parse_marker(body)


def test_malformed_json():
    with pytest.raises(StateError, match="slowroll apply"):
        parse_marker("<!-- slowroll:state {nope} -->")


def test_unknown_version():
    with pytest.raises(StateError, match="version"):
        parse_marker('<!-- slowroll:state {"v":2} -->')


def test_bad_status_and_unknown_field():
    with pytest.raises(StateError):
        parse_marker('<!-- slowroll:state {"v":1,"status":"weird"} -->')
    with pytest.raises(StateError):
        parse_marker('<!-- slowroll:state {"v":1,"extra":1} -->')
