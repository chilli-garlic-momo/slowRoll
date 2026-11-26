from __future__ import annotations

import os
import time
from typing import Any
from urllib.parse import quote

import requests

TIMEOUT = 90
RETRY_WAITS = (2, 6)
RETRY_STATUSES = {502, 503, 504}


class SlowrollError(Exception):
    pass


def percent_rules(percent: int) -> list[dict[str, Any]]:
    condition = {"operator": "PERCENTAGE_SPLIT", "property": "$.identity.key", "value": str(percent)}
    return [{"type": "ALL", "rules": [{"type": "ANY", "conditions": [condition]}], "conditions": []}]


class Flagsmith:
    def __init__(self, base_url: str, api_key: str, session: Any = None, sleep: Any = time.sleep):
        self.base = base_url.rstrip("/")
        self.headers = {"Authorization": f"Api-Key {api_key}"}
        self.session = session or requests.Session()
        self.sleep = sleep

    def request(self, method: str, path: str, body: Any = None) -> Any:
        for attempt in range(len(RETRY_WAITS) + 1):
            last = attempt == len(RETRY_WAITS)
            try:
                resp = self.session.request(method, self.base + path, headers=self.headers, json=body, timeout=TIMEOUT)
            except (requests.ConnectionError, requests.Timeout) as e:
                if last:
                    raise SlowrollError(f"{method} {path}: {type(e).__name__}") from e
                self.sleep(RETRY_WAITS[attempt])
                continue
            if resp.status_code in RETRY_STATUSES and not last:
                self.sleep(RETRY_WAITS[attempt])
                continue
            if resp.status_code >= 400:
                raise SlowrollError(f"{method} {path}: HTTP {resp.status_code}: {resp.text[:300]}")
            try:
                return resp.json()
            except ValueError:
                return None

    def feature_id(self, project: int, name: str) -> int:
        data = self.request("GET", f"/api/v1/projects/{project}/features/?search={quote(name)}")
        for f in (data or {}).get("results", []):
            if f.get("name") == name:
                return f["id"]
        raise SlowrollError(f"feature {name!r} not found in project {project}")

    def find_segment(self, project: int, name: str) -> dict[str, Any] | None:
        data = self.request("GET", f"/api/v1/projects/{project}/segments/?q={quote(name)}")
        return next((s for s in (data or {}).get("results", []) if s.get("name") == name), None)

    def create_segment(self, project: int, name: str, percent: int) -> dict[str, Any]:
        return self.request("POST", f"/api/v1/projects/{project}/segments/", {"name": name, "project": project, "rules": percent_rules(percent)})

    def set_segment_percent(self, project: int, segment_id: int, percent: int) -> Any:
        return self.request("PATCH", f"/api/v1/projects/{project}/segments/{segment_id}/", {"rules": percent_rules(percent)})

    def get_flag(self, env: str, feature_id: int) -> dict[str, Any]:
        return self.request("GET", f"/api/__future__/environments/{env}/features/{feature_id}/")

    def set_override(self, env: str, feature_id: int, segment_id: int, enabled: bool) -> Any:
        body = {"segment_overrides": [{"segment": {"id": segment_id}, "enabled": enabled}]}
        return self.request("PATCH", f"/api/__future__/environments/{env}/features/{feature_id}/", body)

    def delete_override(self, env: str, feature_id: int, segment_id: int) -> Any:
        return self.request("DELETE", f"/api/__future__/environments/{env}/features/{feature_id}/segment-overrides/{segment_id}/")


def client_from_env() -> Flagsmith:
    url, key = os.environ.get("FLAGSMITH_API_URL"), os.environ.get("FLAGSMITH_API_KEY")
    if not url or not key:
        raise SlowrollError("set FLAGSMITH_API_URL and FLAGSMITH_API_KEY")
    return Flagsmith(url, key)


def segment_percent(segment: dict[str, Any]) -> int | None:
    try:
        return int(segment["rules"][0]["rules"][0]["conditions"][0]["value"])
    except (KeyError, IndexError, TypeError, ValueError):
        return None
