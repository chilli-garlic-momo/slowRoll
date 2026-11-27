from __future__ import annotations

import argparse
import random
import threading
import time
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor


def main() -> None:
    p = argparse.ArgumentParser(description="Simulated traffic for the Slowroll demo")
    p.add_argument("--url", required=True, help="base URL of the demo, for example https://slowroll-demo.vercel.app")
    p.add_argument("--rps", type=float, default=3)
    p.add_argument("--users", type=int, default=300)
    p.add_argument("--duration", type=int, default=120)
    args = p.parse_args()

    lock = threading.Lock()
    counts = {"sent": 0, "errors": 0}

    def hit() -> None:
        uid = f"user-{random.randrange(args.users)}"
        failed = False
        try:
            urllib.request.urlopen(f"{args.url.rstrip('/')}/api/checkout?uid={uid}", timeout=10).read()
        except (urllib.error.URLError, OSError):
            failed = True
        with lock:
            counts["sent"] += 1
            counts["errors"] += failed

    start = time.monotonic()
    next_report = start + 10
    sent_at = 0
    with ThreadPoolExecutor(max_workers=max(4, int(args.rps * 4))) as pool:
        while time.monotonic() - start < args.duration:
            pool.submit(hit)
            sent_at += 1
            time.sleep(max(0.0, start + sent_at / args.rps - time.monotonic()))
            if time.monotonic() >= next_report:
                with lock:
                    print(f"sent={counts['sent']} errors={counts['errors']}", flush=True)
                next_report += 10
    with lock:
        print(f"done: sent={counts['sent']} errors={counts['errors']}", flush=True)


if __name__ == "__main__":
    main()
