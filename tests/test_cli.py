from __future__ import annotations

import pytest

from slowroll.cli import main


def test_help_exits_zero():
    with pytest.raises(SystemExit) as e:
        main(["--help"])
    assert e.value.code == 0


@pytest.mark.parametrize("command", ["doctor", "apply", "plan", "tick", "loop", "status", "halt", "reset"])
def test_commands_registered_and_unimplemented(command):
    assert main([command, "rollout.yml"]) == 1
