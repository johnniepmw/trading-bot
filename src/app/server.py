"""Composition root and HTTP adapter (stdlib only). Example app: replace the routes during
bootstrap, keep /healthz (smoke tests and uptime checks depend on it). For a batch job
(Cloud Run job), keep a tiny /healthz-equivalent smoke command instead.
"""

from __future__ import annotations

import html
import json
import logging
import os
import signal
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

from app.core.greeting import greeting
from app.flags import Flags, GcsSource, load_registry

VERSION = os.environ.get("APP_VERSION", "dev")


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        return json.dumps({"severity": record.levelname, "message": record.getMessage(), "version": VERSION})


def build_flags() -> Flags:
    bucket = os.environ.get("FLAGS_BUCKET")
    return Flags(load_registry(), source=GcsSource(bucket) if bucket else None)


FLAGS = build_flags()


class Handler(BaseHTTPRequestHandler):
    server_version = "app"

    def _send(self, status: int, content_type: str, body: str) -> None:
        data = body.encode("utf-8")
        self.send_response(status)
        self.send_header("content-type", content_type)
        self.send_header("cache-control", "no-store")
        self.send_header("content-length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self) -> None:
        url = urlparse(self.path)
        try:
            if url.path == "/healthz":
                self._send(200, "application/json", json.dumps({"status": "ok", "version": VERSION}))
            elif url.path == "/":
                name = parse_qs(url.query).get("name", [None])[0]
                heading = greeting(name) if FLAGS.is_enabled("home-greeting") else "Welcome"
                page = (
                    '<!doctype html><html lang="en"><head><meta charset="utf-8"><title>App</title></head>'
                    f"<body><h1>{html.escape(heading)}</h1>"
                    f"<p>version {html.escape(VERSION)}</p></body></html>"
                )
                self._send(200, "text/html; charset=utf-8", page)
            else:
                self._send(404, "application/json", json.dumps({"error": "not found"}))
        except Exception:
            logging.exception("request failed: %s", url.path)
            self._send(500, "application/json", json.dumps({"error": "internal error"}))

    def log_message(self, format: str, *args: object) -> None:  # noqa: A002 - stdlib signature
        return  # access logs come from Cloud Run


def main() -> None:
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(JsonFormatter())
    logging.basicConfig(level=os.environ.get("LOG_LEVEL", "INFO").upper(), handlers=[handler])
    port = int(os.environ.get("PORT", "8080"))
    server = ThreadingHTTPServer(("0.0.0.0", port), Handler)  # noqa: S104 - container port
    signal.signal(signal.SIGTERM, lambda *_: threading.Thread(target=server.shutdown).start())
    logging.info("listening on %s", port)
    server.serve_forever()


if __name__ == "__main__":
    main()
