from __future__ import annotations

from pathlib import Path

import pytest

from slowroll.cli import main

EXAMPLE = str(Path(__file__).parent.parent / "examples" / "rollout.yml")


def test_help_exits_zero():
    with pytest.raises(SystemExit) as e:
        main(["--help"])
    assert e.value.code == 0


@pytest.mark.parametrize("command", ["tick", "loop"])
def test_commands_registered_and_unimplemented(command, monkeypatch):
    monkeypatch.setenv("FLAGSMITH_API_URL", "https://fs.test")
    monkeypatch.setenv("FLAGSMITH_API_KEY", "k")
    assert main([command, EXAMPLE]) == 1


def test_tick_on_bad_config_exits_2_and_lists_every_error(tmp_path, capsys):
    bad = tmp_path / "bad.yml"
    bad.write_text("project: 0\nsteps: []\n")
    assert main(["tick", str(bad)]) == 2
    assert capsys.readouterr().err.count("error:") >= 4


def test_missing_config_exits_2(tmp_path):
    assert main(["tick", str(tmp_path / "nope.yml")]) == 2


@pytest.mark.parametrize("command", ["doctor", "apply", "plan", "status", "halt", "reset"])
def test_live_commands_without_env_vars_exit_1(command, monkeypatch, capsys):
    monkeypatch.delenv("FLAGSMITH_API_URL", raising=False)
    monkeypatch.delenv("FLAGSMITH_API_KEY", raising=False)
    assert main([command, EXAMPLE]) == 1
    assert "FLAGSMITH_API_URL" in capsys.readouterr().err
