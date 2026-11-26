from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass

STATUSES = {"idle", "ramping", "waiting", "halted", "done"}
MARKER = re.compile(r"\A<!-- slowroll:state (\{.*\}) -->")
HINT = "run slowroll apply"


class StateError(Exception):
    pass


@dataclass
class State:
    v: int = 1
    status: str = "idle"
    index: int = -1
    advanced_at: str | None = None
    strikes: int = 0
    unknown: int = 0
    reason: str | None = None


def dump_marker(state: State) -> str:
    return f"<!-- slowroll:state {json.dumps(asdict(state), separators=(',', ':'))} -->"


def parse_marker(body: str) -> State:
    m = MARKER.match(body)
    if not m:
        raise StateError(f"status comment has no state marker; {HINT}")
    try:
        data = json.loads(m.group(1))
    except json.JSONDecodeError as e:
        raise StateError(f"state marker is not valid JSON; {HINT}") from e
    if not isinstance(data, dict) or data.get("v") != 1:
        raise StateError(f"unsupported state version {data.get('v') if isinstance(data, dict) else data!r}")
    try:
        state = State(**data)
    except TypeError as e:
        raise StateError(f"state marker has unexpected fields; {HINT}") from e
    if state.status not in STATUSES:
        raise StateError(f"unknown status {state.status!r}; {HINT}")
    return state
