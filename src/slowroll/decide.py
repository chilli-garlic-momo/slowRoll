from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from .config import Config
from .health import Health
from .state import State


@dataclass(frozen=True)
class Action:
    kind: str
    index: int | None = None
    reason: str | None = None
    comment: bool = False


def _soak_elapsed(state: State, cfg: Config, now: datetime) -> bool:
    if state.advanced_at is None:
        return True
    return now - datetime.fromisoformat(state.advanced_at) >= cfg.soak


def decide(state: State, cfg: Config, health: Health, now: datetime, approved: bool) -> Action:
    if state.status in ("halted", "done"):
        return Action("NOOP")
    if health.healthy is False:
        if state.strikes + 1 >= cfg.fail_after:
            return Action("HALT", reason=health.detail or "unhealthy")
        return Action("STRIKE", reason=health.detail)
    if health.healthy is None:
        return Action("HOLD_UNKNOWN", reason=health.detail, comment=(state.unknown + 1) % cfg.unknown_comment_every == 0)
    if not _soak_elapsed(state, cfg, now):
        return Action("NOOP")
    if state.index == len(cfg.steps) - 1:
        return Action("FINISH")
    nxt = state.index + 1
    gate = cfg.approval_before_step
    if gate is not None and cfg.steps[nxt] >= gate and not approved:
        return Action("NOOP" if state.status == "waiting" else "WAIT_APPROVAL")
    return Action("ADVANCE", index=nxt)
