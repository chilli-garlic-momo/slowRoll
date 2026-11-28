from __future__ import annotations

import asyncio
import importlib.util
import json
import sys
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

import pytest

pytest.importorskip("mcp")

from slowroll import mcp_server  # noqa: E402

SPEC = importlib.util.spec_from_file_location("agent_demo", Path(__file__).resolve().parents[1] / "scripts" / "agent_demo.py")
agent_demo = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(agent_demo)

FAKE_SERVER = '''
from typing import Any
try:
    from mcp.server.mcpserver import MCPServer
except ImportError:
    from mcp.server.fastmcp import FastMCP as MCPServer

mcp = MCPServer("fake-slowroll")


@mcp.tool()
def rollout_plan(config: dict[str, Any], path: str = "rollout.yml") -> str:
    """Propose a rollout."""
    return f"opened pull request for {config['flag']} at {path}"


@mcp.tool()
def rollout_status(config: str = "rollout.yml") -> str:
    """Report status."""
    return f"status of {config}: idle"


@mcp.tool()
def rollout_halt(config: str = "rollout.yml", reason: str | None = None) -> str:
    """Halt."""
    return f"halted: {reason}"


mcp.run()
'''


class FakeLlm:
    """A stand-in for an OpenAI-compatible API that replies from a script and records every request."""

    def __init__(self, script):
        self.script = list(script)
        self.requests = []
        outer = self

        class Handler(BaseHTTPRequestHandler):
            def do_POST(self):
                body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
                outer.requests.append({"path": self.path, "auth": self.headers.get("Authorization"), "body": body})
                raw = json.dumps({"choices": [{"message": outer.script.pop(0)}]}).encode()
                self.send_response(200)
                self.send_header("Content-Length", str(len(raw)))
                self.end_headers()
                self.wfile.write(raw)

            def log_message(self, *args):
                pass

        self.server = HTTPServer(("127.0.0.1", 0), Handler)
        self.url = f"http://127.0.0.1:{self.server.server_address[1]}/v1"
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    def close(self):
        self.server.shutdown()
        self.server.server_close()


def tool_call(name, arguments, call_id="c1"):
    args = arguments if isinstance(arguments, str) else json.dumps(arguments)
    return {"role": "assistant", "content": None, "tool_calls": [{"id": call_id, "type": "function", "function": {"name": name, "arguments": args}}]}


@pytest.fixture
def fake_server(tmp_path):
    path = tmp_path / "fake_server.py"
    path.write_text(FAKE_SERVER)
    return [sys.executable, str(path)]


@pytest.fixture(autouse=True)
def no_proxy(monkeypatch):
    monkeypatch.setenv("NO_PROXY", "127.0.0.1")
    monkeypatch.setenv("no_proxy", "127.0.0.1")


def run(fake_server, llm, task="do it", max_steps=8, log=None):
    return asyncio.run(
        agent_demo.run_agent(fake_server, llm.url, "sk-test", "some-model", task, max_steps, log=log or (lambda s: None), env={})
    )


def test_simplify_schema_unwraps_optional_and_drops_unsupported_keywords():
    schema = {
        "type": "object",
        "title": "Args",
        "additionalProperties": False,
        "properties": {
            "reason": {"anyOf": [{"type": "string"}, {"type": "null"}], "default": None, "title": "Reason"},
            "nested": {"type": "object", "properties": {"x": {"type": "integer", "default": 3}}},
        },
        "required": ["nested"],
    }
    assert agent_demo.simplify_schema(schema) == {
        "type": "object",
        "properties": {"reason": {"type": "string"}, "nested": {"type": "object", "properties": {"x": {"type": "integer"}}}},
        "required": ["nested"],
    }


def test_the_real_slowroll_tools_convert_to_clean_openai_tools():
    tools = agent_demo.openai_tools(asyncio.run(mcp_server.mcp.list_tools()))
    assert sorted(t["function"]["name"] for t in tools) == ["rollout_halt", "rollout_plan", "rollout_status"]
    text = json.dumps(tools)
    for banned in ('"title"', '"default"', '"additionalProperties"', '"anyOf"'):
        assert banned not in text
    halt = next(t for t in tools if t["function"]["name"] == "rollout_halt")
    assert halt["function"]["parameters"]["properties"]["reason"]["type"] == "string"


class Reply:
    def __init__(self, status, payload=None, headers=None, text=""):
        self.status_code = status
        self._payload = payload
        self.headers = headers or {}
        self.text = text

    def json(self):
        return self._payload


def test_chat_retries_rate_limits_and_honours_retry_after():
    replies = [Reply(429, headers={"Retry-After": "20"}), Reply(503), Reply(200, {"choices": [{"message": {"content": "hi"}}]})]
    sleeps = []
    out = agent_demo.chat("http://x/v1/", "k", "m", [], [], sleep=sleeps.append, post=lambda *a, **kw: replies.pop(0))
    assert out == {"content": "hi"}
    assert sleeps == [20, 10]


def test_chat_gives_up_with_the_status_and_a_snippet_on_a_client_error():
    with pytest.raises(SystemExit, match="HTTP 401: bad key"):
        agent_demo.chat("http://x/v1", "k", "m", [], [], sleep=lambda s: None, post=lambda *a, **kw: Reply(401, text="bad key"))


def test_chat_gives_up_after_the_retry_budget():
    replies = [Reply(429)] * 4
    with pytest.raises(SystemExit, match="HTTP 429"):
        agent_demo.chat("http://x/v1", "k", "m", [], [], sleep=lambda s: None, post=lambda *a, **kw: replies.pop(0))


def test_chat_reports_a_reply_without_choices():
    with pytest.raises(SystemExit, match="unexpected reply"):
        agent_demo.chat("http://x/v1", "k", "m", [], [], post=lambda *a, **kw: Reply(200, {"oops": 1}, text="{oops}"))


def test_server_env_passes_slowroll_settings_but_never_the_model_key(monkeypatch):
    monkeypatch.setenv("LLM_API_KEY", "sk-secret")
    monkeypatch.setenv("FLAGSMITH_API_KEY", "fs-key")
    monkeypatch.setenv("GITHUB_TOKEN", "gh-token")
    env = agent_demo.server_env()
    assert env["FLAGSMITH_API_KEY"] == "fs-key" and env["GITHUB_TOKEN"] == "gh-token"
    assert "LLM_API_KEY" not in env and "sk-secret" not in env.values()


def test_agent_runs_a_tool_and_feeds_the_result_back_to_the_model(fake_server):
    llm = FakeLlm([tool_call("rollout_status", {"config": "rollout.local.yml"}), {"role": "assistant", "content": "It is idle. A person must apply it."}])
    transcript = []
    try:
        final = run(fake_server, llm, log=transcript.append)
    finally:
        llm.close()
    assert final == "It is idle. A person must apply it."
    first, second = llm.requests
    assert first["path"] == "/v1/chat/completions" and first["auth"] == "Bearer sk-test"
    assert sorted(t["function"]["name"] for t in first["body"]["tools"]) == ["rollout_halt", "rollout_plan", "rollout_status"]
    assert first["body"]["messages"][0]["role"] == "system" and "cannot approve" in first["body"]["messages"][0]["content"]
    tool_message = second["body"]["messages"][-1]
    assert tool_message == {"role": "tool", "tool_call_id": "c1", "content": "status of rollout.local.yml: idle"}
    assert any("agent calls rollout_status" in line for line in transcript)


def test_agent_can_make_several_calls_in_a_row(fake_server):
    llm = FakeLlm(
        [
            tool_call("rollout_plan", {"config": {"flag": "new_checkout"}}, "a"),
            tool_call("rollout_halt", {"reason": "demo"}, "b"),
            {"role": "assistant", "content": "done"},
        ]
    )
    try:
        assert run(fake_server, llm) == "done"
    finally:
        llm.close()
    contents = [m["content"] for m in llm.requests[-1]["body"]["messages"] if m["role"] == "tool"]
    assert contents == ["opened pull request for new_checkout at rollout.yml", "halted: demo"]


def test_a_tool_error_is_reported_to_the_model_not_raised(fake_server):
    llm = FakeLlm([tool_call("rollout_plan", {"wrong": 1}), {"role": "assistant", "content": "it failed"}])
    try:
        assert run(fake_server, llm) == "it failed"
    finally:
        llm.close()
    tool_message = llm.requests[-1]["body"]["messages"][-1]
    assert tool_message["content"].startswith("tool error: ")


def test_unreadable_arguments_go_back_to_the_model_so_it_can_retry(fake_server):
    llm = FakeLlm([tool_call("rollout_status", "not json"), {"role": "assistant", "content": "ok"}])
    try:
        run(fake_server, llm)
    finally:
        llm.close()
    assert "could not read the arguments" in llm.requests[-1]["body"]["messages"][-1]["content"]


def test_a_missing_tool_call_id_is_filled_in(fake_server):
    message = tool_call("rollout_status", {})
    message["tool_calls"][0]["id"] = ""
    llm = FakeLlm([message, {"role": "assistant", "content": "ok"}])
    try:
        run(fake_server, llm)
    finally:
        llm.close()
    assert llm.requests[-1]["body"]["messages"][-1]["tool_call_id"] == "call_1_0"


def test_the_step_limit_stops_a_model_that_never_finishes(fake_server):
    llm = FakeLlm([tool_call("rollout_status", {}, f"c{i}") for i in range(3)])
    transcript = []
    try:
        assert run(fake_server, llm, max_steps=3, log=transcript.append) == ""
    finally:
        llm.close()
    assert any("step limit" in line for line in transcript)


def test_default_task_embeds_the_config_and_names_the_status_file(tmp_path):
    cfg = tmp_path / "rollout.local.yml"
    cfg.write_text("flag: new_checkout\nproject: 1\n")
    task = agent_demo.default_task(cfg)
    assert "flag: new_checkout" in task and "'rollout.local.yml'" in task


def test_main_asks_for_the_model_settings(monkeypatch, capsys):
    for name in ("LLM_BASE_URL", "LLM_API_KEY", "LLM_MODEL"):
        monkeypatch.delenv(name, raising=False)
    assert agent_demo.main([]) == 2
    assert "LLM_BASE_URL, LLM_API_KEY, LLM_MODEL" in capsys.readouterr().err


def test_main_writes_a_transcript(monkeypatch, tmp_path, fake_server, capsys):
    llm = FakeLlm([tool_call("rollout_status", {}), {"role": "assistant", "content": "Idle."}])
    cfg = tmp_path / "rollout.local.yml"
    cfg.write_text("flag: new_checkout\n")
    log = tmp_path / "run.md"
    monkeypatch.setenv("LLM_BASE_URL", llm.url)
    monkeypatch.setenv("LLM_API_KEY", "sk-test")
    monkeypatch.setenv("LLM_MODEL", "some-model")
    try:
        code = agent_demo.main(["--config", str(cfg), "--server", " ".join(fake_server), "--log", str(log)])
    finally:
        llm.close()
    assert code == 0
    text = log.read_text()
    assert "some-model" in text and "agent calls rollout_status" in text and "sk-test" not in text
    assert "still has to merge the pull request" in capsys.readouterr().out
