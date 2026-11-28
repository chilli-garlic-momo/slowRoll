from __future__ import annotations

import asyncio
import json
import subprocess
import sys
from datetime import datetime, timezone

import pytest

pytest.importorskip("mcp")

from slowroll import mcp_server  # noqa: E402

NOW = datetime(2026, 11, 28, 10, 0, 0, tzinfo=timezone.utc)
RAW = {
    "flag": "new_checkout",
    "project": 1,
    "environment": "envkey",
    "steps": [5, 25, 50, 100],
    "soak": "10m",
    "approval": {"before_step": 50},
    "health": {"url": "https://x.test/health"},
}


class StubGh:
    def __init__(self, existing_sha=None):
        self.calls = []
        self.existing_sha = existing_sha

    def default_branch(self):
        return "main"

    def branch_sha(self, branch):
        self.calls.append(("branch_sha", branch))
        return "abc123"

    def create_branch(self, name, sha):
        self.calls.append(("create_branch", name, sha))

    def file_sha(self, path, ref):
        return self.existing_sha

    def put_file(self, path, text, message, branch, sha=None):
        self.calls.append(("put_file", path, text, branch, sha))

    def create_pull(self, title, head, base, body):
        self.calls.append(("create_pull", title, head, base, body))
        return {"html_url": "https://github.com/o/r/pull/7"}


def test_server_exposes_exactly_three_tools_and_no_approve():
    names = sorted(t.name for t in asyncio.run(mcp_server.mcp.list_tools()))
    assert names == ["rollout_halt", "rollout_plan", "rollout_status"]


def test_plan_opens_a_pull_request_with_the_config_on_a_new_branch():
    gh = StubGh()
    out = mcp_server.build_pull_request(gh, RAW, "rollout.yml", NOW)
    assert "pull/7" in out
    kinds = [c[0] for c in gh.calls]
    assert kinds == ["branch_sha", "create_branch", "put_file", "create_pull"]
    branch = gh.calls[1][1]
    assert branch == "slowroll/new_checkout-20261128-100000" and gh.calls[1][2] == "abc123"
    _, path, text, put_branch, sha = gh.calls[2]
    assert (path, put_branch, sha) == ("rollout.yml", branch, None)
    assert "steps:" in text and "new_checkout" in text
    assert "slowroll/approve" in gh.calls[3][4]


def test_plan_updates_an_existing_file_using_its_sha():
    gh = StubGh(existing_sha="deadbeef")
    mcp_server.build_pull_request(gh, RAW, "rollout.yml", NOW)
    assert gh.calls[2][4] == "deadbeef"


def test_plan_rejects_an_invalid_config_with_every_error(monkeypatch):
    monkeypatch.setenv("GITHUB_TOKEN", "t")
    with pytest.raises(ValueError) as e:
        mcp_server.rollout_plan({"project": 0, "steps": [5, 5]})
    assert "flag" in str(e.value) and "steps" in str(e.value)


def test_halt_never_writes_to_stdout_because_stdout_is_the_protocol(monkeypatch, capsys, tmp_path):
    cfg = tmp_path / "r.yml"
    cfg.write_text("flag: f\nproject: 1\nenvironment: e\nsteps: [5]\nsoak: 1m\nhealth:\n  url: https://x.test/h\ngithub:\n  repo: o/r\n")
    monkeypatch.setenv("GITHUB_TOKEN", "t")
    monkeypatch.setattr(mcp_server, "client_from_env", lambda: object())

    def fake_halt(cfg, fs, gh, now, reason):
        print("::warning::Slowroll halted f")

    monkeypatch.setattr(mcp_server, "halt", fake_halt)
    result = mcp_server.rollout_halt(str(cfg), "because")
    assert result == "halted f: because"
    assert capsys.readouterr().out == ""


def test_real_server_process_starts_and_lists_the_three_tools():
    proc = subprocess.Popen(
        [sys.executable, "-m", "slowroll.mcp_server"],
        stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True,
    )

    def send(msg):
        proc.stdin.write(json.dumps(msg) + "\n")
        proc.stdin.flush()

    def read(want_id):
        while True:
            msg = json.loads(proc.stdout.readline())
            if msg.get("id") == want_id:
                return msg

    try:
        send({"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {
            "protocolVersion": "2025-06-18", "capabilities": {}, "clientInfo": {"name": "test", "version": "0"}}})
        read(1)
        send({"jsonrpc": "2.0", "method": "notifications/initialized"})
        send({"jsonrpc": "2.0", "id": 2, "method": "tools/list"})
        tools = read(2)["result"]["tools"]
        assert sorted(t["name"] for t in tools) == ["rollout_halt", "rollout_plan", "rollout_status"]
    finally:
        proc.kill()
