from __future__ import annotations

import contextlib
import io
import re
from datetime import datetime, timezone
from typing import Any

import yaml

try:
    from mcp.server.mcpserver import MCPServer
    from mcp.server.mcpserver.exceptions import ToolError
except ImportError:
    from mcp.server.fastmcp import FastMCP as MCPServer
    from mcp.server.fastmcp.exceptions import ToolError

from .config import ConfigError, load_config, parse_config
from .flagsmith import SlowrollError, client_from_env
from .github import github_from_env
from .tick import halt, status

mcp = MCPServer("slowroll")


def _fail(e: Exception) -> ToolError:
    errors = getattr(e, "errors", None)
    return ToolError("; ".join(errors) if errors else str(e))


def build_pull_request(gh: Any, raw: dict[str, Any], path: str, now: datetime) -> str:
    cfg = parse_config(raw)
    text = yaml.safe_dump(raw, sort_keys=False)
    branch = f"slowroll/{re.sub(r'[^A-Za-z0-9_-]', '-', cfg.flag)}-{now.strftime('%Y%m%d-%H%M%S')}"
    base = gh.default_branch()
    gh.create_branch(branch, gh.branch_sha(base))
    gh.put_file(path, text, f"slowroll: plan rollout of {cfg.flag}", branch, gh.file_sha(path, base))
    steps = ", ".join(f"{s}%" for s in cfg.steps)
    gate = f"approval needed before {cfg.approval_before_step}%" if cfg.approval_before_step else "no approval gate"
    pr = gh.create_pull(
        f"Slowroll: roll out {cfg.flag} ({steps})",
        branch,
        base,
        f"Proposed by an agent through the Slowroll MCP server.\n\n- flag: `{cfg.flag}`\n- steps: {steps}\n- soak per step: `{raw['soak']}`\n"
        f"- {gate}\n- health URL: {cfg.health_url}\n\nMerge to put `{path}` on the default branch, then run `slowroll apply {path}`. "
        "Slowroll never approves a gated step by itself; a human adds the `slowroll/approve` label.",
    )
    return f"opened pull request {pr['html_url']} from branch {branch}"


@mcp.tool()
def rollout_plan(config: dict[str, Any], path: str = "rollout.yml") -> str:
    """Propose a progressive rollout. Writes the rollout config to a new branch and opens a pull request.

    `config` uses the rollout.yml keys: flag (string), project (integer), environment (client-side environment key),
    steps (strictly increasing integers from 1 to 100), soak (like 30s, 10m or 2h), health ({url, fail_after,
    unknown_comment_every}), optional approval ({before_step}, which must be one of steps) and optional
    github ({repo}). Invalid configs are rejected with every problem listed. This tool cannot approve anything.
    """
    try:
        cfg = parse_config(config)
        return build_pull_request(github_from_env(cfg), config, path, datetime.now(timezone.utc))
    except (ConfigError, SlowrollError) as e:
        raise _fail(e) from None


@mcp.tool()
def rollout_status(config: str = "rollout.yml") -> str:
    """Report where a rollout stands: its status, step, current percent, override and health counters."""
    try:
        cfg = load_config(config)
        lines: list[str] = []
        status(cfg, client_from_env(), github_from_env(cfg), out=lines.append)
        return "\n".join(lines)
    except (ConfigError, SlowrollError) as e:
        raise _fail(e) from None


@mcp.tool()
def rollout_halt(config: str = "rollout.yml", reason: str | None = None) -> str:
    """Stop a rollout now: turn the segment override off and mark the rollout halted, with a reason."""
    try:
        cfg = load_config(config)
        with contextlib.redirect_stdout(io.StringIO()):
            halt(cfg, client_from_env(), github_from_env(cfg), datetime.now(timezone.utc), reason)
        return f"halted {cfg.flag}: {reason or 'halted manually'}"
    except (ConfigError, SlowrollError) as e:
        raise _fail(e) from None


def main() -> None:
    mcp.run()


if __name__ == "__main__":
    main()
