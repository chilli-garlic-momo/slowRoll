from __future__ import annotations

from datetime import datetime, timedelta, timezone

from slowroll.config import parse_config
from slowroll.decide import decide
from slowroll.health import Health
from slowroll.state import State

NOW = datetime(2026, 11, 27, 12, 0, tzinfo=timezone.utc)
RAW = {
    "flag": "f",
    "project": 1,
    "environment": "e",
    "steps": [5, 25, 50, 100],
    "soak": "10m",
    "approval": {"before_step": 50},
    "health": {"url": "https://x.test/h", "fail_after": 2, "unknown_comment_every": 6},
}
CFG = parse_config(RAW)

HEALTHY = Health(True)
UNHEALTHY = Health(False, "errors up")
UNKNOWN = Health(None, "no data")


def run(status, index, health, elapsed=True, approved=False, strikes=0, unknown=0, cfg=CFG):
    advanced = None if index == -1 else (NOW - timedelta(minutes=11 if elapsed else 1)).isoformat()
    state = State(status=status, index=index, advanced_at=advanced, strikes=strikes, unknown=unknown)
    return decide(state, cfg, health, NOW, approved)


def test_case_1_halted():
    assert run("halted", 1, HEALTHY).kind == "NOOP"


def test_case_2_done():
    assert run("done", 3, UNHEALTHY).kind == "NOOP"


def test_case_3_first_advance():
    a = run("idle", -1, HEALTHY)
    assert (a.kind, a.index) == ("ADVANCE", 0)


def test_case_4_idle_unknown():
    assert run("idle", -1, UNKNOWN).kind == "HOLD_UNKNOWN"


def test_case_5_soak_not_elapsed():
    assert run("ramping", 0, HEALTHY, elapsed=False).kind == "NOOP"


def test_case_6_advance_after_soak():
    a = run("ramping", 0, HEALTHY)
    assert (a.kind, a.index) == ("ADVANCE", 1)


def test_case_7_wait_for_approval():
    assert run("ramping", 1, HEALTHY).kind == "WAIT_APPROVAL"


def test_case_8_already_waiting():
    assert run("waiting", 1, HEALTHY).kind == "NOOP"


def test_case_9_approved_advances():
    a = run("waiting", 1, HEALTHY, approved=True)
    assert (a.kind, a.index) == ("ADVANCE", 2)


def test_case_10_first_strike():
    assert run("ramping", 0, UNHEALTHY).kind == "STRIKE"


def test_case_10_variant_fail_after_1_halts():
    cfg = parse_config({**RAW, "health": {**RAW["health"], "fail_after": 1}})
    assert run("ramping", 0, UNHEALTHY, cfg=cfg).kind == "HALT"


def test_case_11_second_strike_halts_with_reason():
    a = run("ramping", 0, UNHEALTHY, strikes=1)
    assert a.kind == "HALT" and "errors up" in a.reason


def test_case_12_halts_even_when_waiting():
    assert run("waiting", 1, UNHEALTHY, approved=True, strikes=1).kind == "HALT"


def test_case_13_unknown_comments_every_nth():
    a = run("ramping", 2, UNKNOWN, unknown=5)
    assert a.kind == "HOLD_UNKNOWN" and a.comment


def test_case_14_unknown_quiet_otherwise():
    a = run("ramping", 2, UNKNOWN, unknown=1)
    assert a.kind == "HOLD_UNKNOWN" and not a.comment


def test_case_15_finish():
    assert run("ramping", 3, HEALTHY).kind == "FINISH"


def test_case_16_last_step_soaking():
    assert run("ramping", 3, HEALTHY, elapsed=False).kind == "NOOP"


def test_case_7_variant_no_approval_advances():
    cfg = parse_config({k: v for k, v in RAW.items() if k != "approval"})
    a = run("ramping", 1, HEALTHY, cfg=cfg)
    assert (a.kind, a.index) == ("ADVANCE", 2)
