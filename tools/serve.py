#!/usr/bin/env python3
"""Serve the gallery locally with My Pick editing turned on.

Static hosting (GitHub Pages) can't save anything, so My Pick is edited here and published by committing
data/picks.json. This server serves the gallery folder like `python3 -m http.server` and adds:

    GET /api/picks   the current list
    PUT /api/picks   replace the list, body {"picks": ["<filename>", ...]}

Responses for data/picks.json carry an X-Malfoid-Edit header, which tells the page to show the heart buttons.
It listens on 127.0.0.1 only, refuses requests addressed to any other host name (DNS rebinding),
and rejects cross-site writes, so only the owner's own browser can edit.

Usage:
    python3 tools/serve.py [--port 8770]
    then open http://127.0.0.1:8770/ and tap the heart on any work.

Publishing: commit and push data/picks.json. On the live site the list is read-only.
"""

from __future__ import annotations

import argparse
import json
import os
import tempfile
from functools import partial
from http import HTTPStatus
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SITE = ROOT / "docs"  # serve only the published site, never tools/ or the working caches
PICKS = SITE / "data" / "picks.json"
MAX_PICKS = 20000
MAX_BODY = 4 * 1024 * 1024
EDIT_PATHS = {"/data/picks.json", "/api/picks"}


def read_picks() -> list[str]:
    try:
        data = json.loads(PICKS.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return []
    picks = data.get("picks", []) if isinstance(data, dict) else []
    return [p for p in picks if isinstance(p, str)]


def write_picks(picks: list[str]) -> None:
    """Write atomically, so a crash never leaves a half-written file."""
    PICKS.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=PICKS.parent, prefix=".picks.", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump({"picks": picks}, fh, indent=1, ensure_ascii=False)
            fh.write("\n")
        os.chmod(tmp, 0o644)  # mkstemp creates 0600; keep the file readable like the rest of the site
        os.replace(tmp, PICKS)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise


def validate(body: object) -> list[str]:
    if not isinstance(body, dict) or not isinstance(body.get("picks"), list):
        raise ValueError('expected {"picks": [...]}')
    seen: set[str] = set()
    out = []
    for p in body["picks"]:
        if not isinstance(p, str) or not 0 < len(p) <= 300 or "/" in p or "\\" in p:
            raise ValueError(f"not a gallery filename: {p!r}")
        if p not in seen:
            seen.add(p)
            out.append(p)
    if len(out) > MAX_PICKS:
        raise ValueError("too many picks")
    return out


class Handler(SimpleHTTPRequestHandler):
    def _path(self) -> str:
        return self.path.split("?", 1)[0].split("#", 1)[0]

    def _allowed_hosts(self) -> set[str]:
        port = self.server.server_address[1]
        return {f"127.0.0.1:{port}", f"localhost:{port}"}

    def send_head(self):
        # Never serve hidden paths such as /.git, even to the owner.
        if any(part.startswith(".") for part in self._path().split("/") if part):
            self.send_error(HTTPStatus.NOT_FOUND)
            return None
        return super().send_head()

    def _host_ok(self) -> bool:
        """Refuse requests addressed to any other name. A DNS-rebinding page reaches this server
        under its own domain, so its Host header gives it away, for reads as well as writes."""
        if self.headers.get("Host") in self._allowed_hosts():
            return True
        self.send_error(HTTPStatus.FORBIDDEN, "unexpected Host")
        return False

    def do_HEAD(self) -> None:
        if self._host_ok():
            super().do_HEAD()

    def end_headers(self) -> None:
        if self._path() in EDIT_PATHS:
            self.send_header("X-Malfoid-Edit", "1")
            self.send_header("Cache-Control", "no-store")
        super().end_headers()

    def _json(self, status: HTTPStatus, payload: object) -> None:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self) -> None:
        if not self._host_ok():
            return
        if self._path() == "/api/picks":
            self._json(HTTPStatus.OK, {"picks": read_picks()})
        else:
            super().do_GET()

    def do_PUT(self) -> None:
        if self._path() != "/api/picks":
            self.send_error(HTTPStatus.NOT_FOUND)
            return
        if not self._host_ok():
            return
        # Refuse writes started by other websites open in the same browser.
        origin = self.headers.get("Origin")
        if origin and origin not in {f"http://{h}" for h in self._allowed_hosts()}:
            self.send_error(HTTPStatus.FORBIDDEN, "cross-site request")
            return
        if not (self.headers.get("Content-Type") or "").startswith("application/json"):
            self.send_error(HTTPStatus.UNSUPPORTED_MEDIA_TYPE)
            return
        length = int(self.headers.get("Content-Length") or 0)
        if length > MAX_BODY:
            self.send_error(HTTPStatus.REQUEST_ENTITY_TOO_LARGE)
            return
        try:
            picks = validate(json.loads(self.rfile.read(length) or b"null"))
        except (ValueError, json.JSONDecodeError) as exc:
            self._json(HTTPStatus.BAD_REQUEST, {"error": str(exc)})
            return
        write_picks(picks)
        self._json(HTTPStatus.OK, {"picks": picks})

    def log_message(self, fmt: str, *args) -> None:
        # Keep the console readable: only API calls and errors.
        if self._path().startswith("/api/") or (len(args) > 1 and str(args[1])[:1] in "45"):
            super().log_message(fmt, *args)


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--port", type=int, default=8770)
    args = ap.parse_args()
    server = ThreadingHTTPServer(("127.0.0.1", args.port), partial(Handler, directory=str(SITE)))
    print(f"Editing My Pick at http://127.0.0.1:{args.port}/  (Ctrl+C to stop)", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
