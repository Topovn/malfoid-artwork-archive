#!/usr/bin/env python3
"""Serve the gallery locally with My Pick and Comic tag editing turned on.

Static hosting (GitHub Pages) can't save anything, so My Pick is edited here and published by committing
data/picks.json. This server serves the gallery folder like `python3 -m http.server` and adds:

    GET /api/picks   the current list
    PUT /api/picks   replace the list, body {"picks": ["<filename>", ...]}
    GET /api/comics  the owner's Comic corrections, {"comic": [...], "art": [...]}
    PUT /api/comics  replace them (stored in data/comics.json, applied to the manifests by the next build)
    GET /api/hmarks  main-site works the owner marked H, {"marks": [...]}
    PUT /api/hmarks  replace them (stored in data/private/h_marks.json; tools/move_h_marked.py moves them to Malfoid H/)

Responses for data/picks.json and /api/comics carry an X-Malfoid-Edit header, which tells the page
to show the heart buttons and the Comic tag toggle.

It also shows the full copy of each site (see tools/site_paths.py): the manifest and post texts come from
data/private/site/, and thumbnails missing from docs/ are looked up there, so the hidden artists' works appear
here, marked Hidden, while the published site never has them. Those responses carry an X-Malfoid-Admin header,
which shows the page's admin toggle; with ?view=visitor they come from docs/ instead, so the owner can see the
site exactly as visitors do.
It listens on 127.0.0.1 only, refuses requests addressed to any other host name (DNS rebinding),
and rejects cross-site writes, so only the owner's own browser can edit.

Usage:
    python3 tools/serve.py [--port 8770]
    then open http://127.0.0.1:8770/ and tap the heart on any work.

Publishing: commit and push data/picks.json; rebuild for Comic corrections. On the live site both are read-only.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import tempfile
import urllib.parse
from functools import partial
from http import HTTPStatus
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from site_paths import FULL_SITES  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
SITE = ROOT / "docs"  # serve only the published site, never tools/ or the working caches
FULL = FULL_SITES["main"]  # the full copy, laid out like docs/ (the second site in its own subfolder)
FULL_FILES = {"/data/manifest.json", "/data/texts.json", "/h/data/manifest.json", "/h/data/texts.json"}
FULL_FALLBACK = ("/thumbs/", "/h/thumbs/")
PICKS = SITE / "data" / "picks.json"
COMICS = ROOT / "data" / "comics.json"  # the owner's Comic corrections; private, applied by the next build
HMARKS = ROOT / "data" / "private" / "h_marks.json"  # works marked for the 18+ site; never published
MAX_PICKS = 20000
MAX_BODY = 4 * 1024 * 1024
EDIT_PATHS = {"/data/picks.json", "/api/picks", "/api/comics", "/api/hmarks"}
API_PATHS = ("/api/picks", "/api/comics", "/api/hmarks")


def read_picks() -> list[str]:
    try:
        data = json.loads(PICKS.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return []
    picks = data.get("picks", []) if isinstance(data, dict) else []
    return [p for p in picks if isinstance(p, str)]


def write_json(path: Path, data: dict) -> None:
    """Write atomically, so a crash never leaves a half-written file."""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=f".{path.stem}.", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(data, fh, indent=1, ensure_ascii=False)
            fh.write("\n")
        os.chmod(tmp, 0o644)  # mkstemp creates 0600; keep the file readable like the rest of the site
        os.replace(tmp, path)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise


def filenames(items: object, what: str) -> list[str]:
    if not isinstance(items, list):
        raise ValueError(f"expected a list of {what}")
    seen: set[str] = set()
    out = []
    for p in items:
        if not isinstance(p, str) or not 0 < len(p) <= 300 or "/" in p or "\\" in p:
            raise ValueError(f"not a gallery filename: {p!r}")
        if p not in seen:
            seen.add(p)
            out.append(p)
    if len(out) > MAX_PICKS:
        raise ValueError(f"too many {what}")
    return out


def validate(body: object) -> list[str]:
    if not isinstance(body, dict):
        raise ValueError('expected {"picks": [...]}')
    return filenames(body.get("picks"), "picks")


def read_hmarks() -> list[str]:
    try:
        data = json.loads(HMARKS.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return []
    marks = data.get("marks", []) if isinstance(data, dict) else []
    return [m for m in marks if isinstance(m, str)]


def validate_hmarks(body: object) -> dict:
    if not isinstance(body, dict):
        raise ValueError('expected {"marks": [...]}')
    return {"marks": sorted(filenames(body.get("marks"), "marks"))}


def read_comics() -> dict:
    """{"comic": [...], "art": [...]}: works the owner marked as comics, and as not comics."""
    try:
        data = json.loads(COMICS.read_text(encoding="utf-8"))
    except FileNotFoundError:
        data = {}
    return {k: [f for f in data.get(k, []) if isinstance(f, str)] for k in ("comic", "art")}


def validate_comics(body: object) -> dict:
    if not isinstance(body, dict):
        raise ValueError('expected {"comic": [...], "art": [...]}')
    out = {k: sorted(filenames(body.get(k, []), k)) for k in ("comic", "art")}
    if set(out["comic"]) & set(out["art"]):
        raise ValueError("a work can't be marked both comic and art")
    return out


RANGE_RE = re.compile(r"^bytes=(\d*)-(\d*)$")


class Handler(SimpleHTTPRequestHandler):
    _range: tuple[int, int] | None = None  # (start, length) of the part being sent
    _ranges_ok = False  # the response is a plain file, so Accept-Ranges is advertised

    def _path(self) -> str:
        return self.path.split("?", 1)[0].split("#", 1)[0]

    def translate_path(self, path: str) -> str:
        """Map the manifests and texts to the full copy, and fall back to it for thumbnails docs/ lacks."""
        local = super().translate_path(path)
        rel = urllib.parse.unquote(path.split("?", 1)[0].split("#", 1)[0])
        query = urllib.parse.parse_qs(urllib.parse.urlsplit(path).query)
        if rel in FULL_FILES and query.get("view") == ["visitor"]:
            return local  # the admin toggle is off: the published copy
        if rel in FULL_FILES or (rel.startswith(FULL_FALLBACK) and not os.path.exists(local)):
            full = (FULL / rel.lstrip("/")).resolve()
            if full.is_relative_to(FULL.resolve()) and full.is_file():
                return str(full)
        return local

    def _allowed_hosts(self) -> set[str]:
        port = self.server.server_address[1]
        return {f"127.0.0.1:{port}", f"localhost:{port}"}

    def send_head(self):
        # Never serve hidden paths such as /.git, even to the owner.
        if any(part.startswith(".") for part in self._path().split("/") if part):
            self.send_error(HTTPStatus.NOT_FOUND)
            return None
        self._range = None
        if self._path() in FULL_FILES:
            # One URL serves the full or the published copy, and both are written in the same second, so the date
            # can't tell them apart: always send the file instead of a 304 that keeps the other copy in the browser.
            del self.headers["If-Modified-Since"]
        path = self.translate_path(self.path)
        self._ranges_ok = os.path.isfile(path)
        m = RANGE_RE.match(self.headers.get("Range", "").strip())
        if not (self._ranges_ok and m and any(m.groups())):
            return super().send_head()
        return self._send_part(path, m)

    def _send_part(self, path: str, m: re.Match):
        """Answer a single byte-range request with 206, so videos can seek (a plain 200 can't)."""
        f = open(path, "rb")
        size = os.fstat(f.fileno()).st_size
        first, last = m.groups()
        if first:
            start, end = int(first), min(int(last), size - 1) if last else size - 1
        else:  # "bytes=-N": the last N bytes
            start, end = max(0, size - int(last)), size - 1
        if start >= size or start > end:
            f.close()
            self.send_response(HTTPStatus.REQUESTED_RANGE_NOT_SATISFIABLE)
            self.send_header("Content-Range", f"bytes */{size}")
            self.send_header("Content-Length", "0")
            self.end_headers()
            return None
        self.send_response(HTTPStatus.PARTIAL_CONTENT)
        self.send_header("Content-Type", self.guess_type(path))
        self.send_header("Content-Range", f"bytes {start}-{end}/{size}")
        self.send_header("Content-Length", str(end - start + 1))
        self.send_header("Last-Modified", self.date_time_string(int(os.fstat(f.fileno()).st_mtime)))
        self.end_headers()
        f.seek(start)
        self._range = (start, end - start + 1)
        return f

    def copyfile(self, source, outputfile) -> None:
        if self._range is None:
            return super().copyfile(source, outputfile)
        remaining = self._range[1]
        while remaining > 0:
            chunk = source.read(min(64 * 1024, remaining))
            if not chunk:
                break
            outputfile.write(chunk)
            remaining -= len(chunk)

    def _host_ok(self) -> bool:
        """Refuse requests addressed to any other name. A DNS-rebinding page reaches this server
        under its own domain, so its Host header gives it away, for reads as well as writes."""
        if self.headers.get("Host") in self._allowed_hosts():
            return True
        self.send_error(HTTPStatus.FORBIDDEN, "unexpected Host")
        return False

    def _slashes_ok(self) -> bool:
        """Send an address with repeated slashes (such as http://127.0.0.1:8770//) to its clean form.
        Python folds them when serving, so the page would load, but the page's relative URLs would then
        resolve to //data/..., //api/... and so on, all through the session."""
        target = self.requestline.split(" ")[1] if self.requestline.count(" ") >= 2 else self.path
        path, sep, query = target.partition("?")
        if "//" not in path:
            return True
        self.send_response(HTTPStatus.MOVED_PERMANENTLY)
        # One leading slash only: a Location starting with // would point at another host.
        self.send_header("Location", re.sub(r"/{2,}", "/", path) + sep + query)
        self.send_header("Content-Length", "0")
        self.end_headers()
        return False

    def do_HEAD(self) -> None:
        if self._host_ok() and self._slashes_ok():
            super().do_HEAD()

    def end_headers(self) -> None:
        if self._ranges_ok:
            self.send_header("Accept-Ranges", "bytes")
        if self._path() in FULL_FILES:
            self.send_header("X-Malfoid-Admin", "1")
            self.send_header("Cache-Control", "no-store")
        elif self._path() in EDIT_PATHS:
            self.send_header("X-Malfoid-Edit", "1")
            self.send_header("Cache-Control", "no-store")
        else:
            # A local preview should show edits on a plain reload: the browser revalidates every file
            # (a 304 when unchanged) instead of guessing it is still fresh from Last-Modified.
            self.send_header("Cache-Control", "no-cache")
        super().end_headers()

    def _json(self, status: HTTPStatus, payload: object) -> None:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self) -> None:
        if not (self._host_ok() and self._slashes_ok()):
            return
        if self._path() == "/api/picks":
            self._json(HTTPStatus.OK, {"picks": read_picks()})
        elif self._path() == "/api/comics":
            self._json(HTTPStatus.OK, read_comics())
        elif self._path() == "/api/hmarks":
            self._json(HTTPStatus.OK, {"marks": read_hmarks()})
        else:
            super().do_GET()

    def do_PUT(self) -> None:
        if self._path() not in API_PATHS:
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
        target, check = {
            "/api/picks": (PICKS, lambda b: {"picks": validate(b)}),
            "/api/comics": (COMICS, validate_comics),
            "/api/hmarks": (HMARKS, validate_hmarks),
        }[self._path()]
        try:
            data = check(json.loads(self.rfile.read(length) or b"null"))
        except (ValueError, json.JSONDecodeError) as exc:
            self._json(HTTPStatus.BAD_REQUEST, {"error": str(exc)})
            return
        write_json(target, data)
        self._json(HTTPStatus.OK, data)

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
