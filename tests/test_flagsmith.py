from __future__ import annotations

import pytest
import requests

from slowroll.cli import doctor
from slowroll.config import parse_config
from slowroll.flagsmith import Flagsmith, SlowrollError, percent_rules

BASE = "https://fs.test"
ENV = "envkey"


class FakeResponse:
    def __init__(self, status=200, payload=None, text=""):
        self.status_code = status
        self._payload = payload
        self.text = text or ("" if payload is None else str(payload))

    def json(self):
        if self._payload is None:
            raise ValueError("no json")
        return self._payload


class FakeSession:
    def __init__(self, *responses):
        self.responses = list(responses)
        self.calls = []

    def request(self, method, url, headers=None, json=None, timeout=None):
        self.calls.append({"method": method, "url": url, "headers": headers, "json": json, "timeout": timeout})
        r = self.responses.pop(0)
        if isinstance(r, Exception):
            raise r
        return r


def client(*responses):
    session = FakeSession(*responses)
    sleeps: list[float] = []
    return Flagsmith(BASE + "/", "secret", session=session, sleep=sleeps.append), session, sleeps


def test_auth_header_timeout_and_url():
    fs, s, _ = client(FakeResponse(200, {"results": []}))
    fs.request("GET", "/api/v1/projects/")
    call = s.calls[0]
    assert call["url"] == BASE + "/api/v1/projects/"
    assert call["headers"] == {"Authorization": "Api-Key secret"}
    assert call["timeout"] == 90


def test_feature_id_exact_match():
    fs, s, _ = client(FakeResponse(200, {"results": [{"id": 9, "name": "new_checkout_v2"}, {"id": 7, "name": "new_checkout"}]}))
    assert fs.feature_id(1, "new_checkout") == 7
    assert s.calls[0]["url"].endswith("/api/v1/projects/1/features/?search=new_checkout")


def test_feature_id_missing():
    fs, _, _ = client(FakeResponse(200, {"results": []}))
    with pytest.raises(SlowrollError, match="not found"):
        fs.feature_id(1, "nope")


def test_find_segment_exact_name_or_none():
    fs, s, _ = client(FakeResponse(200, {"results": [{"id": 3, "name": "slowroll:f"}]}), FakeResponse(200, {"results": []}))
    assert fs.find_segment(1, "slowroll:f")["id"] == 3
    assert "q=slowroll%3Af" in s.calls[0]["url"]
    assert fs.find_segment(1, "slowroll:f") is None


def test_create_segment_body():
    fs, s, _ = client(FakeResponse(201, {"id": 4}))
    assert fs.create_segment(1, "slowroll:f", 5) == {"id": 4}
    assert s.calls[0]["method"] == "POST"
    assert s.calls[0]["json"] == {"name": "slowroll:f", "project": 1, "rules": percent_rules(5)}


def test_percent_rules_shape():
    cond = percent_rules(25)[0]["rules"][0]["conditions"][0]
    assert cond == {"operator": "PERCENTAGE_SPLIT", "property": "$.identity.key", "value": "25"}


def test_set_segment_percent_patch():
    fs, s, _ = client(FakeResponse(200, {"id": 4}))
    fs.set_segment_percent(1, 4, 25)
    assert (s.calls[0]["method"], s.calls[0]["json"]) == ("PATCH", {"rules": percent_rules(25)})
    assert s.calls[0]["url"].endswith("/api/v1/projects/1/segments/4/")


def test_get_flag_and_set_override_and_delete():
    fs, s, _ = client(FakeResponse(200, {}), FakeResponse(200, {}), FakeResponse(200, {}))
    fs.get_flag(ENV, 7)
    fs.set_override(ENV, 7, 4, True)
    fs.delete_override(ENV, 7, 4)
    base = f"/api/__future__/environments/{ENV}/features/7/"
    assert s.calls[0]["url"] == BASE + base
    assert s.calls[1]["method"] == "PATCH"
    assert s.calls[1]["json"] == {"segment_overrides": [{"segment": {"id": 4}, "enabled": True}]}
    assert (s.calls[2]["method"], s.calls[2]["url"]) == ("DELETE", BASE + base + "segment-overrides/4/")


def test_retry_on_503_then_success():
    fs, s, sleeps = client(FakeResponse(503, text="waking"), FakeResponse(200, {"ok": 1}))
    assert fs.request("GET", "/x") == {"ok": 1}
    assert len(s.calls) == 2 and sleeps == [2]


def test_retry_on_connection_error_waits_2_then_6_then_gives_up():
    err = requests.ConnectionError("boom")
    fs, s, sleeps = client(err, err, err)
    with pytest.raises(SlowrollError, match="ConnectionError"):
        fs.request("GET", "/x")
    assert len(s.calls) == 3 and sleeps == [2, 6]


def test_no_retry_on_400_and_error_is_trimmed_without_headers():
    fs, s, sleeps = client(FakeResponse(400, text="x" * 500))
    with pytest.raises(SlowrollError) as e:
        fs.request("POST", "/api/v1/y", {"a": 1})
    assert len(s.calls) == 1 and sleeps == []
    msg = str(e.value)
    assert "POST /api/v1/y" in msg and "400" in msg and "secret" not in msg and msg.count("x") == 300


class FakeFs:
    def __init__(self, enabled=False, segment=None, fail_auth=False):
        self.enabled, self.segment, self.fail_auth = enabled, segment, fail_auth

    def request(self, method, path, body=None):
        if self.fail_auth and path == "/api/v1/projects/":
            raise SlowrollError("GET /api/v1/projects/: HTTP 401: nope")
        return {"name": "slowroll-demo"}

    def feature_id(self, project, name):
        return 7

    def get_flag(self, env, fid):
        return {"environment_default": {"enabled": self.enabled}}

    def find_segment(self, project, name):
        return self.segment


CFG = parse_config({"flag": "f", "project": 1, "environment": "e", "steps": [5, 100], "soak": "1m", "health": {"url": "https://x.test/h"}})


def run_doctor(fs):
    lines: list[str] = []
    return doctor(CFG, fs, out=lines.append), lines


def test_doctor_all_ok_with_missing_segment():
    code, lines = run_doctor(FakeFs())
    assert code == 0 and len(lines) == 6 and any("missing" in l for l in lines)


def test_doctor_fails_when_default_is_on():
    code, lines = run_doctor(FakeFs(enabled=True))
    assert code == 1 and any(l.startswith("FAIL") and "default" in l for l in lines)


def test_doctor_reports_auth_failure_and_keeps_going():
    code, lines = run_doctor(FakeFs(fail_auth=True))
    assert code == 1 and sum(l.startswith("FAIL") for l in lines) == 1 and len(lines) == 6


def test_segment_percent_reads_the_real_shape_and_tolerates_garbage():
    from slowroll.flagsmith import segment_percent

    assert segment_percent({"rules": percent_rules(25)}) == 25
    assert segment_percent({"rules": []}) is None
    assert segment_percent({}) is None
