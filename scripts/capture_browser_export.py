#!/usr/bin/env python3
"""Receive one supervised browser export and persist it as JSON.

This helper binds to loopback only. It exists so the controlled browser can hand
DOM-extracted public research records to the local workspace without scraping
the source from a second HTTP client.
"""

from __future__ import annotations

import argparse
import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs

MAX_BODY_BYTES = 8 * 1024 * 1024


def build_handler(output_path: Path) -> type[BaseHTTPRequestHandler]:
    class CaptureHandler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:  # noqa: N802
            if self.path != "/":
                self.send_error(404)
                return
            body = (
                "<!doctype html><html lang='zh-CN'><meta charset='utf-8'>"
                "<title>HouseRadar Browser Export</title>"
                "<form method='post' action='/capture'>"
                "<textarea id='payload' name='payload'></textarea>"
                "<button id='save' type='submit'>保存</button></form></html>"
            ).encode()
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_POST(self) -> None:  # noqa: N802
            if self.path != "/capture":
                self.send_error(404)
                return
            try:
                length = int(self.headers.get("Content-Length", "0"))
            except ValueError:
                self.send_error(400, "invalid content length")
                return
            if length <= 0 or length > MAX_BODY_BYTES:
                self.send_error(413, "payload size rejected")
                return

            form = parse_qs(self.rfile.read(length).decode("utf-8"), keep_blank_values=True)
            try:
                records = json.loads(form["payload"][0])
            except (KeyError, IndexError, UnicodeDecodeError, json.JSONDecodeError):
                self.send_error(400, "payload must be a JSON array")
                return
            if not isinstance(records, list):
                self.send_error(400, "payload must be a JSON array")
                return

            output_path.parent.mkdir(parents=True, exist_ok=True)
            temporary = output_path.with_suffix(output_path.suffix + ".tmp")
            temporary.write_text(
                json.dumps(records, ensure_ascii=False, indent=2) + "\n",
                encoding="utf-8",
            )
            temporary.replace(output_path)

            body = json.dumps(
                {"saved": len(records), "output": str(output_path)},
                ensure_ascii=False,
            ).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, format: str, *args: object) -> None:
            return

    return CaptureHandler


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("output", type=Path)
    parser.add_argument("--port", type=int, default=8765)
    args = parser.parse_args()

    server = ThreadingHTTPServer(
        ("127.0.0.1", args.port),
        build_handler(args.output.resolve()),
    )
    print(f"capture server ready on http://127.0.0.1:{args.port}", flush=True)
    server.serve_forever()


if __name__ == "__main__":
    main()
