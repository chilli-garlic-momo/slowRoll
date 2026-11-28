"""Let an AI model drive the Slowroll MCP server, using any OpenAI-compatible chat API.

This is a small agent loop: it starts `slowroll-mcp` over stdio, offers the server's three tools to a
model, runs the tool calls the model makes and feeds the results back. It works with any provider that
speaks the OpenAI chat-completions format with tool calling, including free tiers (Google AI Studio,
Groq, OpenRouter) and local models (Ollama). You choose the provider and model; nothing here is tied to one.

    export LLM_BASE_URL=...   # e.g. https://api.groq.com/openai/v1 or http://localhost:11434/v1
    export LLM_API_KEY=...    # for Ollama any non-empty text works
    export LLM_MODEL=...      # a model your provider lists as supporting tool calling
    python scripts/agent_demo.py --config rollout.local.yml --log agent-run.md

The model can only plan (open a pull request), read status and halt. There is no approve tool, so a
person still has to merge the pull request, run `slowroll apply` and add the approval label.
`rollout_plan` opens a real branch and pull request in GITHUB_REPOSITORY: close it afterwards.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
import time
from pathlib import Path
from typing import Any, Callable

import requests

SYSTEM = (
    "You operate Slowroll, a progressive rollout controller, through the tools you are given. "
    "You can propose a rollout as a pull request (rollout_plan), read its status (rollout_status) and halt it (rollout_halt). "
    "You cannot approve or start a rollout: a person merges the pull request, applies it and approves gated steps. "
    "Use exactly the configuration the user gives you and do not invent values. Never claim to have done something no tool did. "
    "When you are done, reply in at most three sentences: what you did, and what a person must do next."
)

PASS_THROUGH_ENV = ("FLAGSMITH_API_URL", "FLAGSMITH_API_KEY", "GITHUB_TOKEN", "GITHUB_REPOSITORY")
STRIP_KEYS = {"title", "default", "additionalProperties", "$schema"}
RETRY_STATUSES = {429, 500, 502, 503, 504}
RETRY_WAITS = (3, 10, 30)


def simplify_schema(schema: Any) -> Any:
    """Drop JSON Schema keywords that several OpenAI-compatible providers reject, and unwrap Optional[...]."""
    if isinstance(schema, list):
        return [simplify_schema(s) for s in schema]
    if not isinstance(schema, dict):
        return schema
    for key in ("anyOf", "oneOf"):
        options = schema.get(key)
        if isinstance(options, list):
            real = [o for o in options if not (isinstance(o, dict) and o.get("type") == "null")]
            if len(real) == 1:
                merged = {**{k: v for k, v in schema.items() if k != key}, **real[0]}
                return simplify_schema(merged)
    return {k: simplify_schema(v) for k, v in schema.items() if k not in STRIP_KEYS}


def field(obj: Any, *names: str) -> Any:
    """The MCP SDK renamed camelCase fields to snake_case in 2.x (inputSchema -> input_schema, isError -> is_error)."""
    for name in names:
        value = getattr(obj, name, None)
        if value is not None:
            return value
    return None


def openai_tools(mcp_tools: list[Any]) -> list[dict[str, Any]]:
    return [
        {
            "type": "function",
            "function": {
                "name": t.name,
                "description": (t.description or "").strip(),
                "parameters": simplify_schema(field(t, "input_schema", "inputSchema") or {"type": "object", "properties": {}}),
            },
        }
        for t in mcp_tools
    ]


def chat(
    base_url: str,
    api_key: str,
    model: str,
    messages: list[dict[str, Any]],
    tools: list[dict[str, Any]],
    sleep: Callable[[float], None] = time.sleep,
    post: Callable[..., Any] = requests.post,
) -> dict[str, Any]:
    """One chat-completions call. Free tiers rate-limit hard, so retry 429 and 5xx with a short backoff."""
    for attempt in range(len(RETRY_WAITS) + 1):
        resp = post(
            base_url.rstrip("/") + "/chat/completions",
            headers={"Authorization": f"Bearer {api_key}"},
            json={"model": model, "messages": messages, "tools": tools, "temperature": 0},
            timeout=120,
        )
        if resp.status_code in RETRY_STATUSES and attempt < len(RETRY_WAITS):
            wait = RETRY_WAITS[attempt]
            retry_after = getattr(resp, "headers", {}).get("Retry-After", "")
            if str(retry_after).isdigit():
                wait = max(wait, min(int(retry_after), 60))
            sleep(wait)
            continue
        if resp.status_code >= 400:
            raise SystemExit(f"the model API answered HTTP {resp.status_code}: {resp.text[:300]}")
        try:
            return resp.json()["choices"][0]["message"]
        except (KeyError, IndexError, ValueError):
            raise SystemExit(f"unexpected reply from the model API: {resp.text[:300]}") from None
    raise SystemExit("unreachable")


def server_env() -> dict[str, str]:
    """Give the MCP server only what it needs, never the model API key."""
    from mcp.client.stdio import get_default_environment

    env = dict(get_default_environment())
    env.update({k: os.environ[k] for k in PASS_THROUGH_ENV if os.environ.get(k)})
    return env


async def run_agent(
    server_command: list[str],
    base_url: str,
    api_key: str,
    model: str,
    task: str,
    max_steps: int = 8,
    log: Callable[[str], None] = lambda s: None,
    env: dict[str, str] | None = None,
) -> str:
    from mcp import ClientSession, StdioServerParameters
    from mcp.client.stdio import stdio_client

    def say(text: str) -> None:
        print(text, flush=True)
        log(text)

    params = StdioServerParameters(command=server_command[0], args=server_command[1:], env=env if env is not None else server_env())
    async with stdio_client(params) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            mcp_tools = (await session.list_tools()).tools
            tools = openai_tools(mcp_tools)
            say("tools offered: " + ", ".join(t.name for t in mcp_tools))
            messages: list[dict[str, Any]] = [{"role": "system", "content": SYSTEM}, {"role": "user", "content": task}]
            for step in range(1, max_steps + 1):
                message = await asyncio.to_thread(chat, base_url, api_key, model, messages, tools)
                calls = message.get("tool_calls") or []
                messages.append({k: v for k, v in message.items() if v is not None})
                if not calls:
                    final = (message.get("content") or "").strip()
                    say(f"\nagent: {final}")
                    return final
                for n, call in enumerate(calls):
                    fn = call.get("function", {})
                    name = fn.get("name", "")
                    call_id = call.get("id") or f"call_{step}_{n}"
                    try:
                        arguments = json.loads(fn.get("arguments") or "{}")
                        if not isinstance(arguments, dict):
                            raise ValueError("arguments must be a JSON object")
                    except ValueError as e:
                        result_text = f"tool error: could not read the arguments ({e}); send a JSON object"
                    else:
                        say(f"\n[{step}] agent calls {name}({json.dumps(arguments)})")
                        result = await session.call_tool(name, arguments)
                        body = "\n".join(c.text for c in result.content if getattr(c, "text", None))
                        result_text = ("tool error: " if field(result, "is_error", "isError") else "") + body
                    say(f"    result: {result_text}")
                    messages.append({"role": "tool", "tool_call_id": call_id, "content": result_text})
            say("\nstopped: the step limit was reached before the model finished")
            return ""


def default_task(config_path: Path) -> str:
    return (
        "Propose a progressive rollout for this configuration by calling rollout_plan with exactly this config "
        f"(parsed from the YAML below). Then call rollout_status with config set to {config_path.name!r}. "
        "If rollout_status reports an error, say what it says.\n\n" + config_path.read_text()
    )


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="Drive the Slowroll MCP server with an AI model")
    p.add_argument("--config", default="rollout.local.yml", help="rollout config the agent should plan")
    p.add_argument("--task", help="replace the default task text")
    p.add_argument("--server", default="slowroll-mcp", help="command that starts the MCP server")
    p.add_argument("--max-steps", type=int, default=8)
    p.add_argument("--log", help="also write a markdown transcript to this file")
    args = p.parse_args(argv)

    missing = [v for v in ("LLM_BASE_URL", "LLM_API_KEY", "LLM_MODEL") if not (os.environ.get(v) or "").strip()]
    if missing:
        print(f"set {', '.join(missing)} (see the top of this file for examples)", file=sys.stderr)
        return 2
    task = args.task or default_task(Path(args.config))

    lines: list[str] = []
    final = asyncio.run(
        run_agent(
            args.server.split(),
            os.environ["LLM_BASE_URL"].strip(),
            os.environ["LLM_API_KEY"].strip(),
            os.environ["LLM_MODEL"].strip(),
            task,
            args.max_steps,
            log=lines.append,
        )
    )
    if args.log:
        header = f"# Agent run\n\nModel: `{os.environ['LLM_MODEL'].strip()}` via an OpenAI-compatible API. Tools: the Slowroll MCP server (plan, status, halt; no approve).\n\n```\n"
        Path(args.log).write_text(header + "\n".join(lines) + "\n```\n")
        print(f"\ntranscript written to {args.log}")
    print("\nA person still has to merge the pull request, run `slowroll apply` and add the approval label.")
    return 0 if final else 1


if __name__ == "__main__":
    raise SystemExit(main())
