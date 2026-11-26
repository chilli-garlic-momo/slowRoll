from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Health:
    healthy: bool | None
    detail: str | None = None
