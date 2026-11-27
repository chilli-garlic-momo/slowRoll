from __future__ import annotations

import argparse
import json
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

VALUES = {"healthy": True, "unhealthy": False, "unknown": None}


def make_handler(path: Path):
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            try:
                word = path.read_text().split()[0].lower()
            except (OSError, IndexError):
                word = "unknown"
            body = json.dumps({"healthy": VALUES.get(word), "detail": f"fake health: {word}"}).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *args):
            pass

    return Handler


def main() -> None:
    p = argparse.ArgumentParser(description="Serves healthy true, false or null depending on a file")
    p.add_argument("--file", default="/tmp/health", help="file holding healthy, unhealthy or unknown")
    p.add_argument("--port", type=int, default=8099)
    args = p.parse_args()
    HTTPServer(("127.0.0.1", args.port), make_handler(Path(args.file))).serve_forever()


if __name__ == "__main__":
    main()
