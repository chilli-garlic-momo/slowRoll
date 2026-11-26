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
