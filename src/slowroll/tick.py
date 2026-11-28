from __future__ import annotations

import sys
import time
from dataclasses import dataclass, replace
from datetime import datetime, timezone
from typing import Any, Callable

from .config import Config, parse_duration
from .decide import decide
from .flagsmith import SlowrollError, segment_percent
from .health import Health
from .state import State, StateError, dump_marker, parse_marker

MAX_LOOP_FAILURES = 5

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


def save(cfg: Config, gh: Any, issue: Issue, state: State, event: str | None, now: datetime) -> None:
    gh.update_comment(issue.comment_id, render_status(cfg, state))
    if event:
        gh.add_comment(issue.number, f"`{now.strftime('%Y-%m-%d %H:%M:%S')} UTC` {event}")


def _wait_until_listed(gh: Any, issue_title: str, sleep: Callable[[float], None], tries: int = 10) -> bool:
    for _ in range(tries):
        if gh.find_issue(issue_title) is not None:
            return True
        sleep(3)
    return False


def apply(cfg: Config, fs: Any, gh: Any, now: datetime, dry_run: bool = False, out: Callable[[str], None] = print, sleep: Callable[[float], None] = time.sleep) -> int:
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
            if not _wait_until_listed(gh, title(cfg), sleep):
                changes.append("note: GitHub is slow to list the new issue; wait a few seconds before ticking")

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


def _count(state: State, health: Health) -> State:
    if health.healthy is True:
        return replace(state, strikes=0, unknown=0)
    if health.healthy is False:
        return replace(state, strikes=state.strikes + 1, unknown=0)
    return replace(state, unknown=state.unknown + 1)


def _line(cfg: Config, state: State, kind: str, health: Health, dry_run: bool = False) -> str:
    percent = cfg.steps[state.index] if state.index >= 0 else "-"
    label = {True: "healthy", False: "unhealthy", None: "unknown"}[health.healthy]
    return f"{cfg.flag} status={state.status} percent={percent} action={kind} health={label}" + (" (dry run)" if dry_run else "")


def tick(cfg: Config, fs: Any, gh: Any, now: datetime, fetch: Callable[[str], Health], dry_run: bool = False, out: Callable[[str], None] = print) -> int:
    issue = find(cfg, gh)
    if issue is None:
        if gh.find_issue(title(cfg), state="closed") is None:
            raise SlowrollError(f"no open issue {title(cfg)!r}; run slowroll apply")
        out(f"{cfg.flag}: rollout finished and its issue is closed; nothing to do")
        return 0
    state = issue.state
    health = fetch(cfg.health_url)
    if state.status in ("halted", "done"):
        out(_line(cfg, state, "NOOP", health, dry_run))
        return 0

    approved = gh.approved(issue.number) if cfg.approval_before_step is not None else False
    action = decide(state, cfg, health, now, approved)
    new = _count(state, health)
    detail = health.detail or "no detail"
    event = None

    if action.kind == "ADVANCE":
        new = replace(new, status="ramping", index=action.index, advanced_at=now.isoformat(), reason=None)
        percent = cfg.steps[action.index]
        event = f"advanced to step {action.index + 1} of {len(cfg.steps)} ({percent}%); health is healthy"
        if not dry_run:
            seg = fs.find_segment(cfg.project, segment_name(cfg))
            if seg is None:
                raise SlowrollError(f"segment {segment_name(cfg)!r} is missing; run slowroll apply")
            fs.set_segment_percent(cfg.project, seg["id"], percent)
            if state.index == -1:
                fs.set_override(cfg.environment, fs.feature_id(cfg.project, cfg.flag), seg["id"], True)
    elif action.kind == "HALT":
        new = replace(new, status="halted", reason=action.reason)
        event = f"HALTED: {action.reason}. The segment override is off, so the flag is off for everyone."
        if not dry_run:
            _override_off(cfg, fs)
    elif action.kind == "WAIT_APPROVAL":
        new = replace(new, status="waiting")
        event = f"waiting for approval before the {cfg.steps[state.index + 1]}% step. Add the label `slowroll/approve` to continue."
    elif action.kind == "FINISH":
        new = replace(new, status="done")
        event = f"reached {cfg.steps[-1]}% and soaked; done. The flag stays at {cfg.steps[-1]}%; promote it or clean up the segment and override by hand."
    elif action.kind == "STRIKE":
        event = f"unhealthy ({detail}); strike {new.strikes} of {cfg.fail_after}"
    elif action.kind == "HOLD_UNKNOWN" and action.comment:
        event = f"health has been unknown for {new.unknown} ticks ({detail}); holding at the current step and not reverting"

    if not dry_run:
        if new != state or event:
            save(cfg, gh, issue, new, event, now)
        if action.kind == "FINISH":
            gh.close_issue(issue.number)
    out(_line(cfg, new, action.kind, health, dry_run))
    if action.kind == "HALT":
        print(f"::warning::Slowroll halted {cfg.flag}: {action.reason}")
    return 0


def loop(cfg: Config, fs: Any, gh: Any, every: str, fetch: Callable[[str], Health], sleep: Callable[[float], None] = time.sleep) -> int:
    interval = parse_duration(every)
    if interval is None:
        raise SlowrollError("--every must look like 10s, 1m or 1h")
    failures = 0
    try:
        while True:
            try:
                tick(cfg, fs, gh, datetime.now(timezone.utc), fetch)
                failures = 0
            except SlowrollError as e:
                failures += 1
                print(f"error: {e}", file=sys.stderr)
                if failures >= MAX_LOOP_FAILURES:
                    print(f"error: giving up after {failures} failed ticks in a row", file=sys.stderr)
                    return 1
            sleep(interval.total_seconds())
    except KeyboardInterrupt:
        return 0
