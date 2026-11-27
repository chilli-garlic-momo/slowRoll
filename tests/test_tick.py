from __future__ import annotations

from datetime import datetime, timezone

import pytest

from slowroll.config import parse_config
from slowroll.flagsmith import SlowrollError, percent_rules
from slowroll.state import State, parse_marker
from slowroll.tick import apply, halt, reset, status

NOW = datetime(2026, 11, 27, 12, 0, tzinfo=timezone.utc)
CFG = parse_config({
    "flag": "f",
    "project": 1,
    "environment": "env",
    "steps": [5, 25, 50, 100],
    "soak": "10m",
    "approval": {"before_step": 50},
    "health": {"url": "https://x.test/h"},
})


class FakeFs:
    def __init__(self, default_enabled=False):
        self.default_enabled = default_enabled
        self.segments: dict[str, dict] = {}
        self.overrides: dict[int, bool] = {}
        self.writes: list[tuple] = []

    def feature_id(self, project, name):
        return 7

    def get_flag(self, env, fid):
        return {
            "environment_default": {"enabled": self.default_enabled},
            "segment_overrides": [{"segment": {"id": i}, "enabled": e} for i, e in self.overrides.items()],
        }

    def find_segment(self, project, name):
        return self.segments.get(name)

    def create_segment(self, project, name, percent):
        self.writes.append(("create_segment", name, percent))
        seg = {"id": len(self.segments) + 1, "name": name, "rules": percent_rules(percent)}
        self.segments[name] = seg
        return seg

    def set_segment_percent(self, project, segment_id, percent):
        self.writes.append(("percent", segment_id, percent))
        for s in self.segments.values():
            if s["id"] == segment_id:
                s["rules"] = percent_rules(percent)

    def set_override(self, env, fid, segment_id, enabled):
        self.writes.append(("override", segment_id, enabled))
        self.overrides[segment_id] = enabled


class FakeGh:
    def __init__(self):
        self.issues: list[dict] = []
        self.comments: dict[int, list[dict]] = {}
        self.labels_ensured = 0
        self.writes: list[str] = []

    def ensure_labels(self):
        self.writes.append("labels")
        self.labels_ensured += 1

    def find_issue(self, title):
        return next((i for i in self.issues if i["title"] == title), None)

    def create_issue(self, title, body):
        self.writes.append("issue")
        issue = {"number": len(self.issues) + 1, "title": title}
        self.issues.append(issue)
        self.comments[issue["number"]] = []
        return issue

    def first_comment(self, number):
        return self.comments[number][0] if self.comments[number] else None

    def add_comment(self, number, body):
        self.writes.append("comment")
        c = {"id": sum(len(v) for v in self.comments.values()) + 1, "body": body}
        self.comments[number].append(c)
        return c

    def update_comment(self, comment_id, body):
        self.writes.append("update")
        for cs in self.comments.values():
            for c in cs:
                if c["id"] == comment_id:
                    c["body"] = body


def lines_of(fn, *a, **kw):
    out: list[str] = []
    fn(*a, out=out.append, **kw)
    return out


def applied():
    fs, gh = FakeFs(), FakeGh()
    lines_of(apply, CFG, fs, gh, NOW)
    return fs, gh


def state_of(gh) -> State:
    return parse_marker(gh.comments[1][0]["body"])


def test_apply_creates_everything_once():
    fs, gh = applied()
    assert fs.segments["slowroll:f"]["rules"] == percent_rules(5)
    assert fs.overrides == {1: False}
    assert gh.issues[0]["title"] == "Slowroll: f" and gh.labels_ensured == 1
    assert state_of(gh) == State()


def test_second_apply_is_up_to_date_and_writes_nothing():
    fs, gh = applied()
    fs.writes.clear()
    gh.writes.clear()
    assert lines_of(apply, CFG, fs, gh, NOW) == ["up to date"]
    assert fs.writes == [] and gh.writes == []


def test_dry_run_writes_nothing_and_describes_the_plan():
    fs, gh = FakeFs(), FakeGh()
    out = lines_of(apply, CFG, fs, gh, NOW, dry_run=True)
    assert fs.writes == [] and gh.writes == []
    assert len(out) == 3 and all(l.startswith("would ") for l in out)


def test_apply_refuses_when_default_is_on():
    with pytest.raises(SlowrollError, match="environment default"):
        lines_of(apply, CFG, FakeFs(default_enabled=True), FakeGh(), NOW)


def test_status_reports_state_percent_and_override():
    fs, gh = applied()
    (line,) = lines_of(status, CFG, fs, gh)
    assert "status=idle" in line and "percent=5" in line and "override=off" in line


def test_status_without_issue_says_apply():
    with pytest.raises(SlowrollError, match="slowroll apply"):
        lines_of(status, CFG, FakeFs(), FakeGh())


def test_halt_turns_override_off_marks_state_and_audits(capsys):
    fs, gh = applied()
    fs.overrides[1] = True
    halt(CFG, fs, gh, NOW, reason="bad deploy")
    assert fs.overrides[1] is False
    s = state_of(gh)
    assert (s.status, s.reason) == ("halted", "bad deploy")
    assert "bad deploy" in gh.comments[1][-1]["body"] and "2026-11-27" in gh.comments[1][-1]["body"]
    assert "::warning::" in capsys.readouterr().out


def test_halt_defaults_reason():
    fs, gh = applied()
    halt(CFG, fs, gh, NOW)
    assert state_of(gh).reason == "halted manually"


def test_reset_restores_first_step_and_idle():
    fs, gh = applied()
    fs.overrides[1] = True
    fs.set_segment_percent(1, 1, 50)
    halt(CFG, fs, gh, NOW)
    lines_of(reset, CFG, fs, gh, NOW)
    assert fs.overrides[1] is False
    assert fs.segments["slowroll:f"]["rules"] == percent_rules(5)
    assert state_of(gh) == State()


def test_reset_without_open_issue_still_resets_flagsmith_and_says_apply():
    fs, gh = applied()
    gh.issues.clear()
    fs.overrides[1] = True
    out = lines_of(reset, CFG, fs, gh, NOW)
    assert fs.overrides[1] is False and any("slowroll apply" in l for l in out)


def test_apply_checks_github_before_writing_to_flagsmith():
    class BrokenGh(FakeGh):
        def find_issue(self, title):
            raise SlowrollError("GET /issues: HTTP 401")

    fs = FakeFs()
    with pytest.raises(SlowrollError):
        lines_of(apply, CFG, fs, BrokenGh(), NOW)
    assert fs.writes == []


from datetime import timedelta

import requests

from slowroll.config import ConfigError
from slowroll.health import Health, fetch_health
from slowroll.tick import loop, tick


class TickGh(FakeGh):
    def __init__(self):
        super().__init__()
        self.approved_flag = False
        self.closed: list[int] = []
        self.fail_update_once = False

    def approved(self, number):
        return self.approved_flag

    def close_issue(self, number):
        self.writes.append("close")
        self.closed.append(number)

    def update_comment(self, comment_id, body):
        if self.fail_update_once:
            self.fail_update_once = False
            raise SlowrollError("PATCH comment: HTTP 502")
        super().update_comment(comment_id, body)


NO_APPROVAL = parse_config({k: v for k, v in {
    "flag": "f", "project": 1, "environment": "env", "steps": [5, 25, 50, 100], "soak": "10m",
    "health": {"url": "https://x.test/h"},
}.items()})


class Rig:
    def __init__(self, cfg):
        self.cfg, self.fs, self.gh = cfg, FakeFs(), TickGh()
        self.health = Health(True)
        self.t = NOW
        lines_of(apply, cfg, self.fs, self.gh, NOW)

    def tick(self, minutes=0):
        self.t += timedelta(minutes=minutes)
        return lines_of(tick, self.cfg, self.fs, self.gh, self.t, lambda url: self.health)[0]

    @property
    def state(self):
        return parse_marker(self.gh.comments[1][0]["body"])

    @property
    def percent(self):
        from slowroll.flagsmith import segment_percent

        return segment_percent(self.fs.segments["slowroll:f"])


def test_scenario_a_ramps_to_done_over_several_ticks():
    r = Rig(NO_APPROVAL)
    assert "action=ADVANCE" in r.tick()
    assert (r.percent, r.fs.overrides[1], r.state.index) == (5, True, 0)
    assert "action=NOOP" in r.tick(1)
    for percent in (25, 50, 100):
        assert "action=ADVANCE" in r.tick(11)
        assert r.percent == percent
    assert "action=FINISH" in r.tick(11)
    assert r.state.status == "done" and r.gh.closed == [1]
    assert r.percent == 100 and r.fs.overrides[1] is True
    assert "soaked; done" in r.gh.comments[1][-1]["body"]


def test_scenario_b_halts_after_two_unhealthy_ticks_and_third_does_nothing(capsys):
    r = Rig(NO_APPROVAL)
    r.tick()
    r.health = Health(False, "treatment errors up")
    assert "action=STRIKE" in r.tick(1) and r.state.strikes == 1 and r.state.status == "ramping"
    assert "action=HALT" in r.tick(1)
    assert r.fs.overrides[1] is False
    assert (r.state.status, r.state.reason) == ("halted", "treatment errors up")
    assert "treatment errors up" in r.gh.comments[1][-1]["body"]
    assert "::warning::" in capsys.readouterr().out
    fs_writes, gh_writes, comments = len(r.fs.writes), len(r.gh.writes), len(r.gh.comments[1])
    assert "action=NOOP" in r.tick(1)
    assert (len(r.fs.writes), len(r.gh.writes), len(r.gh.comments[1])) == (fs_writes, gh_writes, comments)


def test_scenario_c_waits_at_the_gate_then_advances_after_the_label():
    r = Rig(CFG)
    r.tick()
    assert "action=ADVANCE" in r.tick(11)
    assert "action=WAIT_APPROVAL" in r.tick(11)
    assert r.state.status == "waiting" and r.percent == 25
    posted = len(r.gh.comments[1])
    assert "action=NOOP" in r.tick(11)
    assert len(r.gh.comments[1]) == posted
    r.gh.approved_flag = True
    assert "action=ADVANCE" in r.tick(1)
    assert (r.percent, r.state.status, r.state.index) == (50, "ramping", 2)


def test_repeated_advance_is_idempotent_when_the_state_write_fails():
    r = Rig(NO_APPROVAL)
    r.gh.fail_update_once = True
    with pytest.raises(SlowrollError):
        r.tick()
    assert (r.percent, r.fs.overrides[1], r.state.index) == (5, True, -1)
    assert "action=ADVANCE" in r.tick()
    assert (r.percent, r.fs.overrides[1], r.state.index, r.state.status) == (5, True, 0, "ramping")


def test_healthy_tick_resets_counters_and_unknown_never_halts():
    r = Rig(NO_APPROVAL)
    r.tick()
    r.health = Health(False, "bad")
    r.tick(1)
    r.health = Health(True)
    r.tick(1)
    assert (r.state.strikes, r.state.unknown) == (0, 0)
    r.health = Health(None, "no data")
    for _ in range(7):
        assert "action=HOLD_UNKNOWN" in r.tick(1)
    assert r.state.status == "ramping" and r.state.unknown == 7 and r.fs.overrides[1] is True
    comments = [c["body"] for c in r.gh.comments[1] if "unknown for" in c["body"]]
    assert len(comments) == 1 and "6 ticks" in comments[0]


def test_dry_run_prints_the_action_and_writes_nothing():
    r = Rig(NO_APPROVAL)
    before = (len(r.fs.writes), len(r.gh.writes), r.gh.comments[1][0]["body"])
    out: list[str] = []
    tick(r.cfg, r.fs, r.gh, NOW, lambda url: Health(True), dry_run=True, out=out.append)
    assert "action=ADVANCE" in out[0] and "dry run" in out[0]
    assert (len(r.fs.writes), len(r.gh.writes), r.gh.comments[1][0]["body"]) == before


def test_tick_without_issue_says_apply():
    with pytest.raises(SlowrollError, match="slowroll apply"):
        lines_of(tick, CFG, FakeFs(), FakeGh(), NOW, lambda url: Health(True))


def test_loop_runs_ticks_until_interrupted_and_survives_errors(capsys):
    r = Rig(NO_APPROVAL)
    calls = {"n": 0}

    def sleep(seconds):
        calls["n"] += 1
        if calls["n"] == 2:
            raise KeyboardInterrupt

    assert loop(r.cfg, r.fs, r.gh, "5s", lambda url: Health(True), sleep=sleep) == 0
    assert calls["n"] == 2 and r.state.index == 0
    with pytest.raises(SlowrollError, match="--every"):
        loop(r.cfg, r.fs, r.gh, "soon", lambda url: Health(True))


class Resp:
    def __init__(self, status=200, payload=None):
        self.status_code, self._payload = status, payload

    def json(self):
        if self._payload is None:
            raise ValueError
        return self._payload


class Sess:
    def __init__(self, result):
        self.result = result

    def get(self, url, timeout):
        assert timeout == 10
        if isinstance(self.result, Exception):
            raise self.result
        return self.result


@pytest.mark.parametrize("result,expected", [
    (Resp(200, {"healthy": True}), Health(True)),
    (Resp(200, {"healthy": False, "detail": "errors"}), Health(False, "errors")),
    (Resp(200, {"healthy": None, "detail": "no data"}), Health(None, "no data")),
    (Resp(500, {"healthy": False}), Health(None, "HTTP 500")),
    (Resp(200, None), Health(None, "response is not JSON")),
    (Resp(200, {"healthy": "yes"}), Health(None, "healthy must be true, false or null")),
    (Resp(200, {"ok": True}), Health(None, "response has no healthy field")),
    (Resp(200, [1, 2]), Health(None, "response has no healthy field")),
    (requests.Timeout(), Health(None, "request failed: Timeout")),
    (requests.ConnectionError(), Health(None, "request failed: ConnectionError")),
])
def test_fetch_health_contract(result, expected):
    assert fetch_health("https://x.test/h", Sess(result)) == expected
