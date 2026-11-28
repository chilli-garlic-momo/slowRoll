from __future__ import annotations

import json
import subprocess
import sys

CONFIG = {
    "flag": "new_checkout",
    "project": 1,
    "environment": "YY7jkBc7fLLpnqhyfUbwXb",
    "steps": [5, 25, 50, 100],
    "soak": "10m",
    "approval": {"before_step": 50},
    "health": {"url": "https://slowroll-demo.vercel.app/api/health"},
}


def main() -> int:
    command = sys.argv[1:] or ["slowroll-mcp"]
    proc = subprocess.Popen(command, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True)
    next_id = 0

    def send(message: dict) -> None:
        proc.stdin.write(json.dumps(message) + "\n")
        proc.stdin.flush()

    def call(method: str, params: dict | None = None) -> dict:
        nonlocal next_id
        next_id += 1
        send({"jsonrpc": "2.0", "id": next_id, "method": method, "params": params or {}})
        while True:
            line = proc.stdout.readline()
            if not line:
                raise SystemExit("the MCP server exited unexpectedly")
            message = json.loads(line)
            if message.get("id") == next_id:
                return message

    def run_tool(name: str, arguments: dict) -> None:
        print(f"\n== calling {name} ==")
        reply = call("tools/call", {"name": name, "arguments": arguments})
        if "error" in reply:
            print(f"protocol error: {reply['error'].get('message')}")
            return
        result = reply["result"]
        text = "\n".join(c.get("text", "") for c in result.get("content", []) if c.get("type") == "text")
        print(("tool error: " if result.get("isError") else "") + text)

    try:
        call("initialize", {"protocolVersion": "2025-06-18", "capabilities": {}, "clientInfo": {"name": "slowroll-demo", "version": "0"}})
        send({"jsonrpc": "2.0", "method": "notifications/initialized"})
        print("== tools offered by the Slowroll MCP server ==")
        for tool in call("tools/list")["result"]["tools"]:
            print(f"{tool['name']}: {(tool.get('description') or '').strip().splitlines()[0]}")
        run_tool("rollout_plan", {"config": CONFIG})
        run_tool("rollout_status", {"config": "rollout.yml"})
    finally:
        proc.kill()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
