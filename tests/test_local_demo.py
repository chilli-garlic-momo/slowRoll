from __future__ import annotations

import importlib.util
import json
import threading
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

import pytest

SPEC = importlib.util.spec_from_file_location("local_demo", Path(__file__).resolve().parents[1] / "demo" / "local_demo.py")
local_demo = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(local_demo)


def counts(c_req=0, c_err=0, t_req=0, t_err=0):
    return {"control": {"req": c_req, "err": c_err}, "treatment": {"req": t_req, "err": t_err}}


def test_health_is_unknown_when_treatment_has_too_few_requests():
    out = local_demo.health_payload(counts(c_req=50, t_req=3), 60, 10, 0.05)
    assert out["healthy"] is None
    assert "fewer than 10" in out["detail"]


def test_health_is_true_when_there_is_no_treatment_traffic_but_control_has_a_baseline():
    out = local_demo.health_payload(counts(c_req=20), 60, 10, 0.05)
    assert out["healthy"] is True
    assert "nothing to compare" in out["detail"]


def test_health_is_unknown_when_nothing_has_arrived():
    assert local_demo.health_payload(counts(), 60, 10, 0.05)["healthy"] is None


def test_health_is_false_when_treatment_errors_exceed_control_by_more_than_the_delta():
    out = local_demo.health_payload(counts(c_req=100, c_err=0, t_req=20, t_err=10), 60, 10, 0.05)
    assert out["healthy"] is False
    assert out["detail"] == "treatment errors 10/20 (50.0%) vs control 0/100 (0.0%) over 60s"


def test_health_is_true_when_the_gap_is_within_the_delta():
    assert local_demo.health_payload(counts(c_req=100, c_err=5, t_req=100, t_err=8), 60, 10, 0.05)["healthy"] is True


def test_counters_drop_buckets_outside_the_window():
    now = [1000.0]
    c = local_demo.Counters(lambda: now[0])
    c.add("treatment", True)
    c.add("control", False)
    assert c.window(60) == counts(c_req=1, t_req=1, t_err=1)
    now[0] += 61
    assert c.window(60) == counts()


def test_counters_forget_buckets_older_than_the_keep_time():
    now = [1000.0]
    c = local_demo.Counters(lambda: now[0])
    c.add("control", False)
    now[0] += 400
    c.add("control", False)
    assert len(c.data) == 1


class Running:
    def __init__(self, flags, clock=None, **kw):
        self.flags = flags
        self.calls = []

        def fetch(uid):
            self.calls.append(uid)
            result = self.flags(uid) if callable(self.flags) else self.flags
            if isinstance(result, Exception):
                raise result
            return result

        extra = {"clock": clock} if clock else {}
        self.server = local_demo.make_server("127.0.0.1", 0, fetch_flags=fetch, **extra, **kw)
        self.base = f"http://127.0.0.1:{self.server.server_address[1]}"
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)

    def __enter__(self):
        self.thread.start()
        return self

    def __exit__(self, *exc):
        self.server.shutdown()
        self.server.server_close()

    def get(self, path):
        try:
            with urllib.request.urlopen(self.base + path, timeout=5) as r:
                return r.status, r.read().decode(), dict(r.headers)
        except urllib.error.HTTPError as e:
            return e.code, e.read().decode(), dict(e.headers)


def test_checkout_assigns_variant_from_the_flag_and_counts_it():
    with Running({"new_checkout": True, "inject_errors": False}) as app:
        status, body, _ = app.get("/api/checkout?uid=u1")
        assert status == 200
        assert json.loads(body) == {"uid": "u1", "variant": "treatment", "ok": True}
        _, health, _ = app.get("/api/health")
        assert json.loads(health)["counts"]["treatment"] == {"req": 1, "err": 0}


def test_chaos_flag_fails_treatment_requests_only():
    with Running(lambda uid: {"new_checkout": uid == "t", "inject_errors": True}) as app:
        assert app.get("/api/checkout?uid=t")[0] == 500
        assert app.get("/api/checkout?uid=c")[0] == 200
        health = json.loads(app.get("/api/health")[1])
        assert health["counts"] == counts(c_req=1, t_req=1, t_err=1)


def test_flag_service_failure_degrades_to_control_and_is_not_counted():
    with Running(OSError("down")) as app:
        status, body, _ = app.get("/api/checkout?uid=u1")
        assert status == 200
        assert json.loads(body) == {"uid": "u1", "variant": "control", "ok": True, "degraded": True}
        assert json.loads(app.get("/api/health")[1])["counts"] == counts()


def test_flags_are_cached_per_user_for_ten_seconds():
    now = [5000.0]
    with Running({"new_checkout": False}, clock=lambda: now[0]) as app:
        app.get("/api/checkout?uid=u1")
        app.get("/api/checkout?uid=u1")
        assert app.calls == ["u1"]
        now[0] += 11
        app.get("/api/checkout?uid=u1")
        assert app.calls == ["u1", "u1"]


def test_health_json_is_compact_so_the_demo_script_can_grep_it():
    with Running({"new_checkout": False}) as app:
        for i in range(12):
            app.get(f"/api/checkout?uid=u{i}")
        status, body, headers = app.get("/api/health")
        assert status == 200
        assert '"healthy":true' in body
        assert headers["Cache-Control"] == "no-store"


def test_slowrolls_own_health_reader_understands_the_local_demo(monkeypatch):
    from slowroll.health import fetch_health

    monkeypatch.setenv("NO_PROXY", "127.0.0.1")
    with Running(lambda uid: {"new_checkout": uid.startswith("t"), "inject_errors": False}) as app:
        for i in range(12):
            app.get(f"/api/checkout?uid=c{i}")
        assert fetch_health(app.base + "/api/health").healthy is True  # baseline only, nothing to compare
        for i in range(12):
            app.get(f"/api/checkout?uid=t{i}")
        ok = fetch_health(app.base + "/api/health")
        assert ok.healthy is True and "treatment errors 0/12" in ok.detail

    with Running(lambda uid: {"new_checkout": uid.startswith("t"), "inject_errors": True}) as app:
        for i in range(12):
            app.get(f"/api/checkout?uid=c{i}")
            app.get(f"/api/checkout?uid=t{i}")
        bad = fetch_health(app.base + "/api/health")
        assert bad.healthy is False and "treatment errors 12/12" in bad.detail


def test_uid_defaults_and_is_cut_to_64_characters():
    with Running({"new_checkout": False}) as app:
        assert json.loads(app.get("/api/checkout")[1])["uid"] == "anonymous"
        assert len(json.loads(app.get("/api/checkout?uid=" + "x" * 200)[1])["uid"]) == 64


def test_serves_the_demo_page_and_404s_elsewhere():
    with Running({}) as app:
        status, body, headers = app.get("/")
        assert status == 200 and "Slowroll demo checkout" in body
        assert headers["Content-Type"].startswith("text/html")
        assert app.get("/nope")[0] == 404


def test_identity_flags_posts_the_identifier_with_the_environment_key():
    seen = {}

    class FakeFlagsmith(BaseHTTPRequestHandler):
        def do_POST(self):
            seen["path"] = self.path
            seen["key"] = self.headers.get("X-Environment-Key")
            seen["body"] = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            raw = json.dumps({"flags": [{"feature": {"name": "new_checkout"}, "enabled": True}, {"feature": {"name": "inject_errors"}, "enabled": False}]}).encode()
            self.send_response(200)
            self.send_header("Content-Length", str(len(raw)))
            self.end_headers()
            self.wfile.write(raw)

        def log_message(self, *args):
            pass

    fake = HTTPServer(("127.0.0.1", 0), FakeFlagsmith)
    threading.Thread(target=fake.serve_forever, daemon=True).start()
    try:
        flags = local_demo.identity_flags(f"http://127.0.0.1:{fake.server_address[1]}/", "envkey", "user-9")
    finally:
        fake.shutdown()
        fake.server_close()
    assert flags == {"new_checkout": True, "inject_errors": False}
    assert seen == {"path": "/api/v1/identities/", "key": "envkey", "body": {"identifier": "user-9", "traits": []}}


@pytest.mark.parametrize("name", ["FLAGSMITH_API_URL", "FLAGSMITH_ENVIRONMENT_KEY"])
def test_main_refuses_to_start_without_flagsmith_settings(monkeypatch, name):
    monkeypatch.setenv("FLAGSMITH_API_URL", "http://x")
    monkeypatch.setenv("FLAGSMITH_ENVIRONMENT_KEY", "k")
    monkeypatch.delenv(name)
    monkeypatch.setattr("sys.argv", ["local_demo.py"])
    with pytest.raises(SystemExit) as e:
        local_demo.main()
    assert "FLAGSMITH_ENVIRONMENT_KEY" in str(e.value)
