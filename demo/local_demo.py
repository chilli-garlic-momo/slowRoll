"""Local stand-in for the Vercel + Upstash demo app, so the demo runs with no cloud accounts.

It mirrors demo/api/checkout.js and demo/api/health.js: the same routes, the same flag lookup through
Flagsmith's identities endpoint, the same 10 second buckets and the same health rule. Counters live in
memory, so restarting it resets them. Standard library only.

    export FLAGSMITH_API_URL=http://localhost:8000
    export FLAGSMITH_ENVIRONMENT_KEY=<client-side environment key>
    python demo/local_demo.py            # serves http://127.0.0.1:8080
"""

from __future__ import annotations

import argparse
import json
import os
import threading
import time
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Callable
from urllib.parse import parse_qs, urlparse

BUCKET_SECONDS = 10
KEEP_SECONDS = 180
CACHE_SECONDS = 10
INDEX = Path(__file__).with_name("index.html")


class Counters:
    """Request and error counts per variant in 10 second buckets, like the Redis keys in checkout.js."""

    def __init__(self, clock: Callable[[], float] = time.time):
        self.clock = clock
        self.lock = threading.Lock()
        self.data: dict[tuple[str, str, int], int] = {}

    def bucket(self) -> int:
        return int(self.clock() // BUCKET_SECONDS)

    def add(self, variant: str, failed: bool) -> None:
        now = self.bucket()
        with self.lock:
            for kind in (("req", "err") if failed else ("req",)):
                key = (variant, kind, now)
                self.data[key] = self.data.get(key, 0) + 1
            oldest = now - KEEP_SECONDS // BUCKET_SECONDS
            for key in [k for k in self.data if k[2] < oldest]:
                del self.data[key]

    def window(self, seconds: int) -> dict[str, dict[str, int]]:
        buckets = max(1, round(seconds / BUCKET_SECONDS))
        now = self.bucket()
        with self.lock:
            return {
                variant: {kind: sum(self.data.get((variant, kind, now - i), 0) for i in range(buckets)) for kind in ("req", "err")}
                for variant in ("control", "treatment")
            }


def health_payload(counts: dict[str, dict[str, int]], window: int, min_samples: int, max_err_delta: float) -> dict[str, Any]:
    def rate(c: dict[str, int]) -> float:
        return c["err"] / c["req"] if c["req"] else 0.0

    def pct(c: dict[str, int]) -> str:
        return f"{c['err']}/{c['req']} ({rate(c) * 100:.1f}%)"

    control, treatment = counts["control"], counts["treatment"]
    healthy: bool | None = None
    detail = f"treatment has {treatment['req']} requests in the last {window}s, fewer than {min_samples}"
    if treatment["req"] >= min_samples:
        healthy = not (rate(treatment) - rate(control) > max_err_delta)
        detail = f"treatment errors {pct(treatment)} vs control {pct(control)} over {window}s"
    elif treatment["req"] == 0 and control["req"] >= min_samples:
        healthy = True
        detail = f"no treatment traffic yet and control has {control['req']} requests in the last {window}s; nothing to compare"
    return {"healthy": healthy, "detail": detail, "counts": counts, "window_seconds": window}


def identity_flags(api_url: str, env_key: str, uid: str) -> dict[str, bool]:
    req = urllib.request.Request(
        api_url.rstrip("/") + "/api/v1/identities/",
        data=json.dumps({"identifier": uid, "traits": []}).encode(),
        headers={"X-Environment-Key": env_key, "Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=5) as resp:
        data = json.loads(resp.read())
    return {f["feature"]["name"]: f["enabled"] for f in data["flags"]}


def make_server(
    host: str = "127.0.0.1",
    port: int = 8080,
    *,
    fetch_flags: Callable[[str], dict[str, bool]],
    flag: str = "new_checkout",
    chaos_flag: str = "inject_errors",
    window: int = 60,
    min_samples: int = 10,
    max_err_delta: float = 0.05,
    clock: Callable[[], float] = time.time,
) -> ThreadingHTTPServer:
    counters = Counters(clock)
    cache: dict[str, tuple[float, dict[str, bool]]] = {}
    cache_lock = threading.Lock()

    def flags_for(uid: str) -> dict[str, bool]:
        with cache_lock:
            hit = cache.get(uid)
        if hit and clock() - hit[0] < CACHE_SECONDS:
            return hit[1]
        flags = fetch_flags(uid)
        with cache_lock:
            if len(cache) > 2000:
                cache.clear()
            cache[uid] = (clock(), flags)
        return flags

    class Handler(BaseHTTPRequestHandler):
        def send_json(self, status: int, body: dict[str, Any], no_store: bool = False) -> None:
            raw = json.dumps(body, separators=(",", ":")).encode()
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(raw)))
            if no_store:
                self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(raw)

        def do_GET(self) -> None:
            url = urlparse(self.path)
            if url.path == "/api/checkout":
                return self.checkout(parse_qs(url.query))
            if url.path == "/api/health":
                counts = counters.window(window)
                return self.send_json(200, health_payload(counts, window, min_samples, max_err_delta), no_store=True)
            if url.path in ("/", "/index.html") and INDEX.exists():
                raw = INDEX.read_bytes()
                self.send_response(200)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.send_header("Content-Length", str(len(raw)))
                self.end_headers()
                self.wfile.write(raw)
                return
            self.send_json(404, {"error": "not found"})

        def checkout(self, query: dict[str, list[str]]) -> None:
            uid = (query.get("uid") or ["anonymous"])[0][:64]
            try:
                flags = flags_for(uid)
            except Exception:
                return self.send_json(200, {"uid": uid, "variant": "control", "ok": True, "degraded": True})
            variant = "treatment" if flags.get(flag) else "control"
            failed = variant == "treatment" and flags.get(chaos_flag) is True
            counters.add(variant, failed)
            self.send_json(500 if failed else 200, {"uid": uid, "variant": variant, "ok": not failed})

        def log_message(self, *args: Any) -> None:
            pass

    return ThreadingHTTPServer((host, port), Handler)


def main() -> None:
    p = argparse.ArgumentParser(description="Local Slowroll demo app (replaces the Vercel and Upstash deployment)")
    p.add_argument("--host", default="127.0.0.1")
    p.add_argument("--port", type=int, default=int(os.environ.get("PORT", "8080")))
    args = p.parse_args()

    api_url = (os.environ.get("FLAGSMITH_API_URL") or "").strip()
    env_key = (os.environ.get("FLAGSMITH_ENVIRONMENT_KEY") or "").strip()
    if not api_url or not env_key:
        raise SystemExit("set FLAGSMITH_API_URL and FLAGSMITH_ENVIRONMENT_KEY (the client-side environment key)")

    server = make_server(
        args.host,
        args.port,
        fetch_flags=lambda uid: identity_flags(api_url, env_key, uid),
        flag=os.environ.get("FLAG_NAME", "new_checkout"),
        chaos_flag=os.environ.get("CHAOS_FLAG", "inject_errors"),
        window=int(os.environ.get("WINDOW_SECONDS", "60")),
        min_samples=int(os.environ.get("MIN_SAMPLES", "10")),
        max_err_delta=float(os.environ.get("MAX_ERR_DELTA", "0.05")),
    )
    print(f"local demo on http://{args.host}:{args.port} using Flagsmith at {api_url}", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
