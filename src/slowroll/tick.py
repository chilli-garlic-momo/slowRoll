from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import datetime
from typing import Any, Callable

from .config import Config
from .flagsmith import SlowrollError, segment_percent
from .state import State, StateError, dump_marker, parse_marker

ISSUE_BODY = (
    "Managed by Slowroll. Live status is the first comment below; every action adds a comment after it.\n\n"
    "To approve a gated step, add the label `slowroll/approve`. Anyone with write access can."
)


@dataclass
class Issue:
    number: int
    comment_id: int
    state: State


def title(cfg: Config) -> str:
    return f"Slowroll: {cfg.flag}"


def segment_name(cfg: Config) -> str:
    return f"slowroll:{cfg.flag}"


def render_status(cfg: Config, state: State) -> str:
    step = f"{state.index + 1} of {len(cfg.steps)} ({cfg.steps[state.index]}%)" if state.index >= 0 else "not started"
    rows = [
        ("Status", f"`{state.status}`"),
        ("Step", step),
        ("Last advance", state.advanced_at or "never"),
        ("Unhealthy strikes", str(state.strikes)),
        ("Unknown health ticks", str(state.unknown)),
        ("Reason", state.reason or "none"),
    ]
    table = "\n".join(f"| {k} | {v} |" for k, v in rows)
    return f"{dump_marker(state)}\n\n**Slowroll: {cfg.flag}**\n\n| | |\n|---|---|\n{table}\n"


def find(cfg: Config, gh: Any) -> Issue | None:
    issue = gh.find_issue(title(cfg))
    if issue is None:
        return None
    comment = gh.first_comment(issue["number"])
    if comment is None:
        raise SlowrollError(f"issue #{issue['number']} has no status comment; run slowroll apply")
    try:
        return Issue(issue["number"], comment["id"], parse_marker(comment["body"]))
    except StateError as e:
        raise SlowrollError(str(e)) from e


def load(cfg: Config, gh: Any) -> Issue:
    issue = find(cfg, gh)
    if issue is None:
        raise SlowrollError(f"no open issue {title(cfg)!r}; run slowroll apply")
    return issue


def save(cfg: Config, gh: Any, issue: Issue, state: State, event: str, now: datetime) -> None:
    gh.update_comment(issue.comment_id, render_status(cfg, state))
    gh.add_comment(issue.number, f"`{now.strftime('%Y-%m-%d %H:%M:%S')} UTC` {event}")


def apply(cfg: Config, fs: Any, gh: Any, now: datetime, dry_run: bool = False, out: Callable[[str], None] = print) -> int:
    fid = fs.feature_id(cfg.project, cfg.flag)
    flag = fs.get_flag(cfg.environment, fid)
    if flag["environment_default"]["enabled"]:
        raise SlowrollError("the flag is on in the environment default; turn it off first")
    existing_issue = gh.find_issue(title(cfg))

    verb = "would " if dry_run else ""
    changes: list[str] = []

    seg = fs.find_segment(cfg.project, segment_name(cfg))
    if seg is None:
        changes.append(f"{verb}create segment {segment_name(cfg)} at {cfg.steps[0]}%")
        if not dry_run:
            seg = fs.create_segment(cfg.project, segment_name(cfg), cfg.steps[0])

    if seg is None or not any(o["segment"]["id"] == seg["id"] for o in flag["segment_overrides"]):
        changes.append(f"{verb}create disabled segment override")
        if not dry_run:
            fs.set_override(cfg.environment, fid, seg["id"], False)

    if existing_issue is None:
        changes.append(f"{verb}open issue {title(cfg)!r} with labels slowroll and slowroll/approve")
        if not dry_run:
            gh.ensure_labels()
            issue = gh.create_issue(title(cfg), ISSUE_BODY)
            gh.add_comment(issue["number"], render_status(cfg, State()))

    for line in changes or ["up to date"]:
        out(line)
    return 0


def _override_off(cfg: Config, fs: Any) -> dict[str, Any] | None:
    seg = fs.find_segment(cfg.project, segment_name(cfg))
    if seg is not None:
        fs.set_override(cfg.environment, fs.feature_id(cfg.project, cfg.flag), seg["id"], False)
    return seg


def status(cfg: Config, fs: Any, gh: Any, out: Callable[[str], None] = print) -> int:
    issue = load(cfg, gh)
    seg = fs.find_segment(cfg.project, segment_name(cfg))
    percent, override = "n/a", "none"
    if seg is not None:
        percent = str(segment_percent(seg))
        flag = fs.get_flag(cfg.environment, fs.feature_id(cfg.project, cfg.flag))
        enabled = next((o["enabled"] for o in flag["segment_overrides"] if o["segment"]["id"] == seg["id"]), None)
        override = "none" if enabled is None else "on" if enabled else "off"
    s = issue.state
    out(f"{cfg.flag}: status={s.status} step={s.index} percent={percent} override={override} strikes={s.strikes} unknown={s.unknown}")
    return 0


def halt(cfg: Config, fs: Any, gh: Any, now: datetime, reason: str | None = None) -> int:
    issue = load(cfg, gh)
    reason = reason or "halted manually"
    _override_off(cfg, fs)
    save(cfg, gh, issue, replace(issue.state, status="halted", reason=reason), f"halted: {reason}", now)
    print(f"::warning::Slowroll halted {cfg.flag}: {reason}")
    return 0


def reset(cfg: Config, fs: Any, gh: Any, now: datetime, out: Callable[[str], None] = print) -> int:
    seg = _override_off(cfg, fs)
    if seg is None:
        raise SlowrollError(f"segment {segment_name(cfg)!r} is missing; run slowroll apply")
    fs.set_segment_percent(cfg.project, seg["id"], cfg.steps[0])
    issue = find(cfg, gh)
    if issue is None:
        out("flag reset, but there is no open issue; run slowroll apply to open one")
    else:
        save(cfg, gh, issue, State(), "reset: override off, back to the first step", now)
    return 0
