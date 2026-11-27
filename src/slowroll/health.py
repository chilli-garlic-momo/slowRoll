from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import requests


@dataclass(frozen=True)
class Health:
    healthy: bool | None
    detail: str | None = None


def fetch_health(url: str, session: Any = requests) -> Health:
    try:
        resp = session.get(url, timeout=10)
    except requests.RequestException as e:
        return Health(None, f"request failed: {type(e).__name__}")
    if resp.status_code != 200:
        return Health(None, f"HTTP {resp.status_code}")
    try:
        data = resp.json()
    except ValueError:
        return Health(None, "response is not JSON")
    if not isinstance(data, dict) or "healthy" not in data:
        return Health(None, "response has no healthy field")
    healthy = data["healthy"]
    if healthy is not None and not isinstance(healthy, bool):
        return Health(None, "healthy must be true, false or null")
    detail = data.get("detail")
    return Health(healthy, detail if isinstance(detail, str) else None)
