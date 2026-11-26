from __future__ import annotations

import os
from typing import Any

import requests

from .config import Config
from .flagsmith import SlowrollError

API = "https://api.github.com"
LABELS = ("slowroll", "slowroll/approve")
APPROVE = "slowroll/approve"
WRITE_ROLES = {"admin", "maintain", "write"}


class GitHub:
    def __init__(self, repo: str, token: str, session: Any = None):
        self.repo = repo
        self.headers = {
            "Authorization": f"Bearer {token}",
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
        }
        self.session = session or requests.Session()

    def request(self, method: str, path: str, body: Any = None, allow: tuple[int, ...] = ()) -> Any:
        resp = self.session.request(method, f"{API}/repos/{self.repo}{path}", headers=self.headers, json=body, timeout=30)
        if resp.status_code >= 400 and resp.status_code not in allow:
            need = resp.headers.get("X-Accepted-GitHub-Permissions")
            hint = f" (needs: {need})" if need else ""
            raise SlowrollError(f"{method} {path}: HTTP {resp.status_code}: {resp.text[:300]}{hint}")
        try:
            return resp.json()
        except ValueError:
            return None

    def ensure_labels(self) -> None:
        for name in LABELS:
            self.request("POST", "/labels", {"name": name}, allow=(422,))

    def find_issue(self, title: str) -> dict[str, Any] | None:
        issues = self.request("GET", "/issues?labels=slowroll&state=open&per_page=100") or []
        return next((i for i in issues if i["title"] == title and "pull_request" not in i), None)

    def create_issue(self, title: str, body: str) -> dict[str, Any]:
        return self.request("POST", "/issues", {"title": title, "body": body, "labels": ["slowroll"]})

    def first_comment(self, number: int) -> dict[str, Any] | None:
        comments = self.request("GET", f"/issues/{number}/comments?per_page=1")
        return comments[0] if comments else None

    def add_comment(self, number: int, body: str) -> dict[str, Any]:
        return self.request("POST", f"/issues/{number}/comments", {"body": body})

    def update_comment(self, comment_id: int, body: str) -> Any:
        return self.request("PATCH", f"/issues/comments/{comment_id}", {"body": body})

    def close_issue(self, number: int) -> Any:
        return self.request("PATCH", f"/issues/{number}", {"state": "closed"})

    def approved(self, number: int) -> bool:
        actor = None
        for e in self.request("GET", f"/issues/{number}/events?per_page=100") or []:
            if (e.get("label") or {}).get("name") != APPROVE:
                continue
            if e["event"] == "labeled":
                actor = e.get("actor")
            elif e["event"] == "unlabeled":
                actor = None
        if not actor or actor.get("type") != "User":
            return False
        perm = self.request("GET", f"/collaborators/{actor['login']}/permission")
        return (perm or {}).get("permission") in WRITE_ROLES


def github_from_env(cfg: Config) -> GitHub:
    repo = cfg.repo or os.environ.get("GITHUB_REPOSITORY")
    token = os.environ.get("GITHUB_TOKEN")
    if not repo or not token:
        raise SlowrollError("set GITHUB_TOKEN and github.repo in the config (or GITHUB_REPOSITORY)")
    return GitHub(repo, token)
