from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import timedelta
from pathlib import Path
from typing import Any

import yaml

TOP_KEYS = {"flag", "project", "environment", "steps", "soak", "approval", "health", "github"}
SOAK_UNITS = {"s": 1, "m": 60, "h": 3600}


class ConfigError(Exception):
    def __init__(self, errors: list[str]):
        super().__init__("; ".join(errors))
        self.errors = errors


@dataclass(frozen=True)
class Config:
    flag: str
    project: int
    environment: str
    steps: tuple[int, ...]
    soak: timedelta
    health_url: str
    approval_before_step: int | None = None
    fail_after: int = 2
    unknown_comment_every: int = 6
    repo: str | None = None


def _is_int(v: Any) -> bool:
    return isinstance(v, int) and not isinstance(v, bool)


def _section(raw: dict, key: str, allowed: set[str], errs: list[str]) -> dict:
    val = raw.get(key)
    if val is None:
        return {}
    if not isinstance(val, dict):
        errs.append(f"{key} must be a mapping")
        return {}
    errs.extend(f"unknown key {key}.{k}" for k in val if k not in allowed)
    return val


def parse_config(raw: Any) -> Config:
    if not isinstance(raw, dict):
        raise ConfigError(["config must be a YAML mapping"])
    errs: list[str] = [f"unknown key {k}" for k in raw if k not in TOP_KEYS]

    flag = raw.get("flag")
    if not isinstance(flag, str) or not flag.strip():
        errs.append("flag must be a non-empty string")
    project = raw.get("project")
    if not _is_int(project) or project < 1:
        errs.append("project must be a positive integer")
    environment = raw.get("environment")
    if not isinstance(environment, str) or not environment.strip():
        errs.append("environment must be a non-empty string (the environment client key)")

    steps = raw.get("steps")
    if not isinstance(steps, list) or not steps or not all(_is_int(s) and 1 <= s <= 100 for s in steps):
        errs.append("steps must be a non-empty list of integers from 1 to 100")
        steps = []
    elif any(a >= b for a, b in zip(steps, steps[1:])):
        errs.append("steps must be strictly increasing")

    soak = raw.get("soak")
    soak_td = timedelta(0)
    if isinstance(soak, str) and re.fullmatch(r"\d+[smh]", soak):
        soak_td = timedelta(seconds=int(soak[:-1]) * SOAK_UNITS[soak[-1]])
    else:
        errs.append("soak must look like 30s, 10m or 2h")

    approval = _section(raw, "approval", {"before_step"}, errs)
    before = approval.get("before_step")
    if "approval" in raw and raw["approval"] is not None and steps and before not in steps:
        errs.append("approval.before_step must be one of steps")

    health = _section(raw, "health", {"url", "fail_after", "unknown_comment_every"}, errs)
    url = health.get("url")
    if not isinstance(url, str) or not url.startswith(("http://", "https://")):
        errs.append("health.url must start with http:// or https://")
    fail_after = health.get("fail_after", 2)
    if not _is_int(fail_after) or fail_after < 1:
        errs.append("health.fail_after must be an integer of at least 1")
    every = health.get("unknown_comment_every", 6)
    if not _is_int(every) or every < 1:
        errs.append("health.unknown_comment_every must be an integer of at least 1")

    repo = _section(raw, "github", {"repo"}, errs).get("repo")
    if repo is not None and (not isinstance(repo, str) or "/" not in repo):
        errs.append("github.repo must look like owner/name")

    if errs:
        raise ConfigError(errs)
    return Config(
        flag=flag,
        project=project,
        environment=environment,
        steps=tuple(steps),
        soak=soak_td,
        health_url=url,
        approval_before_step=before,
        fail_after=fail_after,
        unknown_comment_every=every,
        repo=repo,
    )


def load_config(path: str | Path) -> Config:
    try:
        raw = yaml.safe_load(Path(path).read_text())
    except OSError as e:
        raise ConfigError([f"cannot read {path}: {e.strerror}"]) from e
    except yaml.YAMLError as e:
        raise ConfigError([f"invalid YAML in {path}"]) from e
    return parse_config(raw)
