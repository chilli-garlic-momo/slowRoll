from __future__ import annotations

import argparse
import sys

from .config import Config, ConfigError, load_config
from .flagsmith import SlowrollError, client_from_env


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="slowroll", description="Health-gated progressive rollout for Flagsmith")
    sub = p.add_subparsers(dest="command", required=True)

    def cmd(name: str, help: str) -> argparse.ArgumentParser:
        sp = sub.add_parser(name, help=help)
        sp.add_argument("config", help="path to rollout.yml")
        return sp

    cmd("doctor", "check connectivity, auth and setup")
    for name, help in (("apply", "create segment, override and issue"), ("tick", "run one decision step")):
        cmd(name, help).add_argument("--dry-run", action="store_true")
    cmd("plan", "alias for apply --dry-run")
    cmd("loop", "run tick forever (demo only)").add_argument("--every", default="10s")
    cmd("status", "print state and current percent")
    cmd("halt", "turn the override off and mark halted").add_argument("--reason")
    cmd("reset", "override off, segment back to first step, state idle")
    return p


def doctor(cfg: Config, fs, out=print) -> int:
    failed = False

    def step(name: str, fn):
        nonlocal failed
        try:
            value, detail = fn()
        except SlowrollError as e:
            out(f"FAIL {name}: {e}")
            failed = True
            return None
        out(f"ok   {name}: {detail}")
        return value

    step("reachable", lambda: (fs.request("GET", "/health/liveness/"), "instance responds"))
    step("auth", lambda: (fs.request("GET", "/api/v1/projects/"), "Api-Key accepted"))
    step("project", lambda: (None, fs.request("GET", f"/api/v1/projects/{cfg.project}/")["name"]))
    fid = step("feature id", lambda: (fid := fs.feature_id(cfg.project, cfg.flag), f"{cfg.flag} is {fid}"))

    def default_off():
        if fs.get_flag(cfg.environment, fid)["environment_default"]["enabled"]:
            raise SlowrollError("environment default is on; Slowroll needs it off")
        return None, "off"

    if fid is None:
        out("skip environment default: needs the feature id")
        failed = True
    else:
        step("environment default", default_off)

    def segment():
        seg = fs.find_segment(cfg.project, f"slowroll:{cfg.flag}")
        return None, f"exists (id {seg['id']})" if seg else "missing, apply will create it"

    step("segment", segment)
    return 1 if failed else 0


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        cfg = load_config(args.config)
    except ConfigError as e:
        for err in e.errors:
            print(f"error: {err}", file=sys.stderr)
        return 2
    if args.command == "doctor":
        try:
            return doctor(cfg, client_from_env())
        except SlowrollError as e:
            print(f"error: {e}", file=sys.stderr)
            return 1
    print(f"slowroll {args.command}: not implemented", file=sys.stderr)
    return 1
