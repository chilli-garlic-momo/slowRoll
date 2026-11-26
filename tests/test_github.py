from __future__ import annotations

import pytest

from slowroll.flagsmith import SlowrollError
from slowroll.github import GitHub


class FakeResponse:
    def __init__(self, status=200, payload=None, text="", headers=None):
        self.status_code = status
        self._payload = payload
        self.text = text or ("" if payload is None else str(payload))
        self.headers = headers or {}

    def json(self):
        if self._payload is None:
            raise ValueError("no json")
        return self._payload


class FakeSession:
    def __init__(self, *responses):
        self.responses = list(responses)
        self.calls = []

    def request(self, method, url, headers=None, json=None, timeout=None):
        self.calls.append({"method": method, "url": url, "headers": headers, "json": json})
        return self.responses.pop(0)


def gh(*responses):
    s = FakeSession(*responses)
    return GitHub("o/r", "tok", session=s), s


def event(kind, login="alice", type_="User", label="slowroll/approve"):
    return {"event": kind, "label": {"name": label}, "actor": {"login": login, "type": type_}}


def test_headers_and_base_url():
    g, s = gh(FakeResponse(200, []))
    g.find_issue("t")
    assert s.calls[0]["headers"] == {
        "Authorization": "Bearer tok",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
    }
    assert s.calls[0]["url"].startswith("https://api.github.com/repos/o/r/issues?labels=slowroll&state=open")


def test_find_issue_exact_title_and_skips_pull_requests():
    g, _ = gh(FakeResponse(200, [
        {"number": 1, "title": "Slowroll: f", "pull_request": {}},
        {"number": 2, "title": "Slowroll: f2"},
        {"number": 3, "title": "Slowroll: f"},
    ]))
    assert g.find_issue("Slowroll: f")["number"] == 3


def test_ensure_labels_ignores_422_but_not_other_errors():
    g, s = gh(FakeResponse(201, {}), FakeResponse(422, text="exists"))
    g.ensure_labels()
    assert [c["json"] for c in s.calls] == [{"name": "slowroll"}, {"name": "slowroll/approve"}]
    g, _ = gh(FakeResponse(500, text="boom"))
    with pytest.raises(SlowrollError):
        g.ensure_labels()


def test_issue_and_comment_calls():
    g, s = gh(FakeResponse(201, {"number": 5}), FakeResponse(201, {"id": 9}), FakeResponse(200, {}), FakeResponse(200, {}), FakeResponse(200, [{"id": 9, "body": "b"}]))
    assert g.create_issue("T", "B")["number"] == 5
    g.add_comment(5, "hello")
    g.update_comment(9, "new")
    g.close_issue(5)
    assert g.first_comment(5)["id"] == 9
    assert s.calls[0]["json"] == {"title": "T", "body": "B", "labels": ["slowroll"]}
    assert (s.calls[1]["method"], s.calls[1]["json"]) == ("POST", {"body": "hello"})
    assert (s.calls[2]["method"], s.calls[2]["url"].endswith("/issues/comments/9")) == ("PATCH", True)
    assert s.calls[3]["json"] == {"state": "closed"}


def test_first_comment_none_when_empty():
    g, _ = gh(FakeResponse(200, []))
    assert g.first_comment(5) is None


def test_approved_when_writer_labeled():
    g, s = gh(FakeResponse(200, [event("labeled")]), FakeResponse(200, {"permission": "write"}))
    assert g.approved(5) is True
    assert s.calls[1]["url"].endswith("/collaborators/alice/permission")


def test_not_approved_without_label_or_after_unlabel():
    g, _ = gh(FakeResponse(200, []))
    assert g.approved(5) is False
    g, _ = gh(FakeResponse(200, [event("labeled"), event("unlabeled")]))
    assert g.approved(5) is False


def test_approved_again_after_relabel():
    g, _ = gh(FakeResponse(200, [event("labeled"), event("unlabeled"), event("labeled", "bob")]), FakeResponse(200, {"permission": "admin"}))
    assert g.approved(5) is True


def test_other_labels_are_ignored():
    g, _ = gh(FakeResponse(200, [event("labeled", label="bug")]))
    assert g.approved(5) is False


def test_bot_actor_and_read_only_actor_do_not_approve():
    g, _ = gh(FakeResponse(200, [event("labeled", "github-actions[bot]", "Bot")]))
    assert g.approved(5) is False
    g, _ = gh(FakeResponse(200, [event("labeled")]), FakeResponse(200, {"permission": "read"}))
    assert g.approved(5) is False


def test_permission_hint_in_403_error_without_token():
    g, _ = gh(FakeResponse(403, text="forbidden", headers={"X-Accepted-GitHub-Permissions": "issues=write"}))
    with pytest.raises(SlowrollError) as e:
        g.close_issue(1)
    assert "issues=write" in str(e.value) and "tok" not in str(e.value)


def test_malformed_token_is_rejected_without_echoing_it(monkeypatch):
    from slowroll.config import parse_config
    from slowroll.github import github_from_env

    cfg = parse_config({"flag": "f", "project": 1, "environment": "e", "steps": [5], "soak": "1m",
                        "health": {"url": "https://x.test/h"}, "github": {"repo": "o/r"}})
    monkeypatch.setenv("GITHUB_TOKEN", "unknown command\nUsage: gh")
    with pytest.raises(SlowrollError) as e:
        github_from_env(cfg)
    assert "unknown command" not in str(e.value)
    monkeypatch.setenv("GITHUB_TOKEN", " ghp_abc\n")
    assert github_from_env(cfg).headers["Authorization"] == "Bearer ghp_abc"
