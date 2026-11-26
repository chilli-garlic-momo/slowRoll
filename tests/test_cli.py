from __future__ import annotations

from pathlib import Path

import pytest

from slowroll.cli import main

EXAMPLE = str(Path(__file__).parent.parent / "examples" / "rollout.yml")


def test_help_exits_zero():
    with pytest.raises(SystemExit) as e:
        main(["--help"])
    assert e.value.code == 0


@pytest.mark.parametrize("command", ["doctor", "apply", "plan", "tick", "loop", "status", "halt", "reset"])
def test_commands_registered_and_unimplemented(command):
    assert main([command, EXAMPLE]) == 1


def test_tick_on_bad_config_exits_2_and_lists_every_error(tmp_path, capsys):
    bad = tmp_path / "bad.yml"
    bad.write_text("project: 0\nsteps: []\n")
    assert main(["tick", str(bad)]) == 2
    assert capsys.readouterr().err.count("error:") >= 4


def test_missing_config_exits_2(tmp_path):
    assert main(["tick", str(tmp_path / "nope.yml")]) == 2
