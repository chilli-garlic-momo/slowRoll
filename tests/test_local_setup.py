from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest
import yaml

from slowroll.config import parse_config
from slowroll.flagsmith import Flagsmith, SlowrollError

from test_flagsmith import FakeResponse, FakeSession

SPEC = importlib.util.spec_from_file_location("local_setup", Path(__file__).resolve().parents[1] / "scripts" / "local_setup.py")
local_setup = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(local_setup)

PROJECT = {"id": 1, "name": "slowroll-demo"}
ENVS = {"results": [{"name": "Development", "api_key": "devkey"}, {"name": "Production", "api_key": "prodkey"}]}


def client(*responses):
    session = FakeSession(*responses)
    return Flagsmith("http://fs.test", "secret", session=session, sleep=lambda s: None), session


def test_items_accepts_a_bare_list_and_a_paginated_object_and_rejects_the_rest():
    assert local_setup.items([{"a": 1}]) == [{"a": 1}]
    assert local_setup.items({"results": [{"a": 1}]}) == [{"a": 1}]
    with pytest.raises(SlowrollError, match="unexpected response shape"):
        local_setup.items({"results": "nope"})


def test_pick_project_takes_the_only_project():
    fs, _ = client(FakeResponse(200, [PROJECT]))
    assert local_setup.pick_project(fs, None) == PROJECT


def test_pick_project_asks_for_a_choice_when_there_are_several():
    fs, _ = client(FakeResponse(200, [PROJECT, {"id": 2, "name": "other"}]))
    with pytest.raises(SlowrollError, match="pass --project"):
        local_setup.pick_project(fs, None)


def test_pick_project_by_id_and_when_missing():
    fs, _ = client(FakeResponse(200, [PROJECT]))
    assert local_setup.pick_project(fs, 1) == PROJECT
    fs, _ = client(FakeResponse(200, [PROJECT]))
    with pytest.raises(SlowrollError, match="project 9 not found"):
        local_setup.pick_project(fs, 9)


def test_pick_project_with_no_projects_says_what_to_do():
    fs, _ = client(FakeResponse(200, []))
    with pytest.raises(SlowrollError, match="create one in the dashboard"):
        local_setup.pick_project(fs, None)


def test_ensure_feature_leaves_an_existing_flag_alone():
    fs, s = client(FakeResponse(200, {"results": [{"id": 1, "name": "new_checkout"}]}))
    assert local_setup.ensure_feature(fs, 1, "new_checkout") == "exists"
    assert [c["method"] for c in s.calls] == ["GET"]


def test_ensure_feature_creates_a_missing_flag_switched_off():
    fs, s = client(FakeResponse(200, {"results": [{"id": 5, "name": "new_checkout_v2"}]}), FakeResponse(201, {"id": 6}))
    assert local_setup.ensure_feature(fs, 1, "new_checkout") == "created, off"
    post = s.calls[1]
    assert post["method"] == "POST" and post["url"].endswith("/api/v1/projects/1/features/")
    assert post["json"] == {"name": "new_checkout", "default_enabled": False}


def test_pick_environment_matches_the_name_ignoring_case():
    fs, _ = client(FakeResponse(200, ENVS))
    assert local_setup.pick_environment(fs, 1, "development")["api_key"] == "devkey"


def test_pick_environment_lists_the_choices_when_missing():
    fs, _ = client(FakeResponse(200, ENVS))
    with pytest.raises(SlowrollError, match="available: \\['Development', 'Production'\\]"):
        local_setup.pick_environment(fs, 1, "Staging")


def test_pick_environment_reports_a_response_without_the_key_field():
    fs, _ = client(FakeResponse(200, [{"name": "Development", "key": "x"}]))
    with pytest.raises(SlowrollError, match="no api_key field"):
        local_setup.pick_environment(fs, 1, "Development")


def test_the_config_it_builds_passes_slowrolls_own_validation():
    raw = local_setup.build_config("new_checkout", 1, "devkey", "http://127.0.0.1:8080/api/health", "me/repo")
    cfg = parse_config(raw)
    assert list(cfg.steps) == [5, 25, 50, 100] and cfg.approval_before_step == 50
    assert "github" not in local_setup.build_config("f", 1, "k", "http://h/x", None)


def test_main_writes_the_config_and_prints_next_steps(monkeypatch, tmp_path, capsys):
    fs, _ = client(
        FakeResponse(200, [PROJECT]),
        FakeResponse(200, {"results": [{"name": "new_checkout"}]}),
        FakeResponse(200, {"results": [{"name": "inject_errors"}]}),
        FakeResponse(200, ENVS),
    )
    monkeypatch.setattr(local_setup, "client_from_env", lambda: fs)
    out = tmp_path / "rollout.local.yml"
    assert local_setup.main(["--out", str(out), "--github-repo", "me/repo"]) == 0
    written = yaml.safe_load(out.read_text())
    assert written["environment"] == "devkey" and written["project"] == 1 and written["github"] == {"repo": "me/repo"}
    text = capsys.readouterr().out
    assert "export FLAGSMITH_ENVIRONMENT_KEY=devkey" in text
    assert "flag new_checkout: exists" in text


def test_main_reports_errors_on_stderr_and_writes_nothing(monkeypatch, tmp_path, capsys):
    fs, _ = client(FakeResponse(200, []))
    monkeypatch.setattr(local_setup, "client_from_env", lambda: fs)
    out = tmp_path / "x.yml"
    assert local_setup.main(["--out", str(out)]) == 1
    assert "error:" in capsys.readouterr().err
    assert not out.exists()
