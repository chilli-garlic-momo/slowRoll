from __future__ import annotations

import argparse
import sys


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


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    print(f"slowroll {args.command}: not implemented", file=sys.stderr)
    return 1
