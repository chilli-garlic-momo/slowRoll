from __future__ import annotations

from copy import deepcopy
from datetime import timedelta
from pathlib import Path

import pytest

from slowroll.config import ConfigError, load_config, parse_config

EXAMPLE = Path(__file__).parent.parent / "examples" / "rollout.yml"

VALID = {
    "flag": "new_checkout",
    "project": 12,
    "environment": "abc",
    "steps": [5, 25, 50, 100],
    "soak": "10m",
    "approval": {"before_step": 50},
    "health": {"url": "https://x.test/health"},
}


def bad(**changes):
    d = deepcopy(VALID)
    d.update(changes)
    return d


def errors(raw) -> list[str]:
    with pytest.raises(ConfigError) as e:
        parse_config(raw)
    return e.value.errors


def test_valid_example_file():
    cfg = load_config(EXAMPLE)
    assert cfg.flag == "new_checkout"
    assert cfg.steps == (5, 25, 50, 100)
    assert cfg.soak == timedelta(minutes=10)
    assert cfg.approval_before_step == 50
    assert cfg.fail_after == 2
    assert cfg.unknown_comment_every == 6


def test_defaults_and_optional_sections():
    raw = deepcopy(VALID)
    del raw["approval"]
    cfg = parse_config(raw)
    assert cfg.approval_before_step is None
    assert cfg.fail_after == 2 and cfg.unknown_comment_every == 6
    assert cfg.repo is None


def test_steps_not_increasing():
    assert any("steps" in e for e in errors(bad(steps=[5, 5, 50], approval={"before_step": 50})))


@pytest.mark.parametrize("steps", [[0, 50], [5, 101]])
def test_step_out_of_range(steps):
    assert any("steps" in e for e in errors(bad(steps=steps, approval=None)))


def test_bad_soak():
    assert any("soak" in e for e in errors(bad(soak="10")))


def test_before_step_not_in_steps():
    assert any("before_step" in e for e in errors(bad(approval={"before_step": 30})))


def test_missing_flag():
    raw = deepcopy(VALID)
    del raw["flag"]
    assert any("flag" in e for e in errors(raw))


def test_bad_url():
    assert any("health.url" in e for e in errors(bad(health={"url": "ftp://x"})))


def test_unknown_key():
    assert any("surprise" in e for e in errors(bad(surprise=1)))


def test_all_errors_reported_together():
    errs = errors({"project": 0, "steps": [], "soak": "soon", "health": {"url": "nope"}, "extra": 1})
    assert len(errs) >= 6


def test_missing_file_and_bad_yaml(tmp_path):
    with pytest.raises(ConfigError):
        load_config(tmp_path / "nope.yml")
    p = tmp_path / "bad.yml"
    p.write_text("flag: [unclosed")
    with pytest.raises(ConfigError):
        load_config(p)


def test_parse_duration():
    from slowroll.config import parse_duration

    assert parse_duration("5s") == timedelta(seconds=5)
    assert parse_duration("2h") == timedelta(hours=2)
    assert parse_duration("5") is None and parse_duration(5) is None
