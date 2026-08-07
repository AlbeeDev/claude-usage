#!/usr/bin/env python3
"""Receives usage readings pushed by the browser userscript.

The browser volunteers the data instead of anything scraping it, so there is no
automation for Cloudflare to detect. Stores only the latest reading; the server
stamps its own receive time so a stale browser can be spotted.
"""

import json
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from time import time

LATEST = Path("/data/latest.json")


class Handler(BaseHTTPRequestHandler):
    def _cors(self):
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.send_header("Access-Control-Allow-Methods", "POST, GET, OPTIONS")

    def do_OPTIONS(self):
        self.send_response(204)
        self._cors()
        self.end_headers()

    def do_POST(self):
        if self.path != "/report":
            self.send_error(404)
            return
        try:
            body = self.rfile.read(int(self.headers.get("Content-Length", 0)))
            data = json.loads(body)
        except Exception:
            self.send_error(400)
            return

        LATEST.parent.mkdir(parents=True, exist_ok=True)
        LATEST.write_text(json.dumps({"received_at": time(), "usage": data}))
        self.send_response(200)
        self._cors()
        self.end_headers()
        self.wfile.write(b'{"ok":true}')

    def do_GET(self):
        if self.path != "/latest":
            self.send_error(404)
            return
        payload = LATEST.read_bytes() if LATEST.exists() else b'{"error":"no reading yet"}'
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self._cors()
        self.end_headers()
        self.wfile.write(payload)

    def log_message(self, *args):
        pass  # quiet


if __name__ == "__main__":
    HTTPServer(("0.0.0.0", 8000), Handler).serve_forever()
