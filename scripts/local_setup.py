"""Prepare a local Flagsmith for the Slowroll demo.

Given an organisation Admin API key, this finds your project, creates the two demo flags if they are
missing (both off), reads the environment's client-side key and writes a ready rollout config.

    export FLAGSMITH_API_URL=http://localhost:8000
    export FLAGSMITH_API_KEY=<organisation admin api key>
    python scripts/local_setup.py --github-repo your-name/your-repo

It only creates what is missing and never turns a flag on. Safe to run again.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Any
from urllib.parse import quote

import yaml

from slowroll.flagsmith import Flagsmith, SlowrollError, client_from_env

STEPS = [5, 25, 50, 100]


def items(data: Any) -> list[dict[str, Any]]:
    """Flagsmith returns either a bare list or a paginated {"results": [...]} depending on the endpoint."""
    if isinstance(data, dict):
        data = data.get("results", [])
    if not isinstance(data, list):
        raise SlowrollError(f"unexpected response shape: {str(data)[:200]}")
    return data


def pick_project(fs: Flagsmith, wanted: int | None) -> dict[str, Any]:
    projects = items(fs.request("GET", "/api/v1/projects/"))
    if wanted is not None:
        for p in projects:
            if p.get("id") == wanted:
                return p
        raise SlowrollError(f"project {wanted} not found; the key can see: {[(p.get('id'), p.get('name')) for p in projects]}")
    if not projects:
        raise SlowrollError("this key sees no projects; create one in the dashboard first (open-source Flagsmith allows one)")
    if len(projects) > 1:
        raise SlowrollError(f"several projects found, pass --project: {[(p.get('id'), p.get('name')) for p in projects]}")
    return projects[0]


def ensure_feature(fs: Flagsmith, project: int, name: str) -> str:
    found = items(fs.request("GET", f"/api/v1/projects/{project}/features/?search={quote(name)}"))
    if any(f.get("name") == name for f in found):
        return "exists"
    fs.request("POST", f"/api/v1/projects/{project}/features/", {"name": name, "default_enabled": False})
    return "created, off"


def pick_environment(fs: Flagsmith, project: int, name: str) -> dict[str, Any]:
    envs = items(fs.request("GET", f"/api/v1/projects/{project}/environments/"))
    for e in envs:
        if e.get("name", "").lower() == name.lower():
            if not e.get("api_key"):
                raise SlowrollError(f"environment {name!r} has no api_key field in the response: {sorted(e)}")
            return e
    raise SlowrollError(f"environment {name!r} not found; available: {[e.get('name') for e in envs]}")


def build_config(flag: str, project: int, env_key: str, health_url: str, repo: str | None) -> dict[str, Any]:
    cfg: dict[str, Any] = {
        "flag": flag,
        "project": project,
        "environment": env_key,
        "steps": STEPS,
        "soak": "10m",
        "approval": {"before_step": 50},
        "health": {"url": health_url, "fail_after": 2, "unknown_comment_every": 6},
    }
    if repo:
        cfg["github"] = {"repo": repo}
    return cfg


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="Prepare a local Flagsmith for the Slowroll demo")
    p.add_argument("--project", type=int, help="project id (needed only if the key sees several projects)")
    p.add_argument("--environment", default="Development", help="environment name, default Development")
    p.add_argument("--flag", default="new_checkout")
    p.add_argument("--chaos-flag", default="inject_errors")
    p.add_argument("--health-url", default="http://127.0.0.1:8080/api/health")
    p.add_argument("--github-repo", help="owner/name; leave out to use GITHUB_REPOSITORY")
    p.add_argument("--out", default="rollout.local.yml")
    args = p.parse_args(argv)

    try:
        fs = client_from_env()
        project = pick_project(fs, args.project)
        print(f"project: {project['id']} ({project.get('name')})")
        for name in (args.flag, args.chaos_flag):
            print(f"flag {name}: {ensure_feature(fs, project['id'], name)}")
        env = pick_environment(fs, project["id"], args.environment)
    except SlowrollError as e:
        print(f"error: {e}", file=sys.stderr)
        return 1

    out = Path(args.out)
    out.write_text(yaml.safe_dump(build_config(args.flag, project["id"], env["api_key"], args.health_url, args.github_repo), sort_keys=False))
    print(f"wrote {out}")
    print("\nNext:")
    print(f"  export FLAGSMITH_ENVIRONMENT_KEY={env['api_key']}")
    print("  python demo/local_demo.py &")
    print(f"  slowroll doctor {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
