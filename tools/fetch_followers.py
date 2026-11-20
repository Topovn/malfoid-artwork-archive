#!/usr/bin/env python3
"""Fetch approximate follower counts for every artist in the gallery.

X's own no-login endpoints no longer expose follower counts, so this uses the public FxTwitter API
(https://github.com/FxEmbed/FxEmbed), which needs no login.
Requests are paced gently and retried on rate limits.
Results are cached in data/followers.json, keyed by lowercased handle, and artists fetched recently are skipped,
so reruns are cheap.
Run tools/build_malfoid_gallery.py afterwards to put the counts into the manifest.

Usage:
    python3 tools/fetch_followers.py                   # fetch artists never fetched or older than 30 days
    python3 tools/fetch_followers.py --max-age-days 0  # refetch everything
"""

from __future__ import annotations

import argparse
import json
import re
import ssl
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
MANIFEST = ROOT / "docs" / "data" / "manifest.json"
FOLLOWERS = ROOT / "data" / "followers.json"
ENDPOINT = "https://api.fxtwitter.com/2/profile/{handle}"
WORKERS = 2
MIN_INTERVAL = 0.3  # seconds between requests across all workers
HANDLE_RE = re.compile(r"^[A-Za-z0-9_]{1,15}$")

try:  # python.org builds on macOS ship without system certificates
    import certifi

    SSL_CONTEXT = ssl.create_default_context(cafile=certifi.where())
except ImportError:
    SSL_CONTEXT = ssl.create_default_context()

_lock = threading.Lock()
_next_slot = 0.0


def pace() -> None:
    global _next_slot
    with _lock:
        now = time.monotonic()
        wait = _next_slot - now
        _next_slot = max(now, _next_slot) + MIN_INTERVAL
    if wait > 0:
        time.sleep(wait)


def fetch(handle: str) -> tuple[str, int | None, str]:
    """Return (lowercased handle, followers or None if the account is gone, status)."""
    key = handle.lower()
    backoff = 5.0
    for _ in range(6):
        pace()
        url = ENDPOINT.format(handle=urllib.parse.quote(handle))
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0", "Accept": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=20, context=SSL_CONTEXT) as r:
                data = json.load(r)
            user = data.get("user") or {}
            if data.get("code") != 200 or "followers" not in user:
                return key, None, "gone"
            return key, int(user["followers"]), "ok"
        except urllib.error.HTTPError as e:
            if e.code in (401, 403, 404):  # missing, suspended or protected
                return key, None, "gone"
            if e.code in (429, 500, 502, 503, 504):
                time.sleep(backoff)
                backoff *= 2
                continue
            return key, None, f"http {e.code}"
        except urllib.error.URLError as e:
            if isinstance(e.reason, ssl.SSLError):
                raise SystemExit(f"TLS error talking to FxTwitter: {e.reason}. Install certifi (pip install certifi).")
            time.sleep(backoff)
            backoff *= 2
        except (TimeoutError, json.JSONDecodeError):
            time.sleep(backoff)
            backoff *= 2
    return key, None, "failed"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--max-age-days", type=float, default=30)
    args = ap.parse_args()

    items = json.loads(MANIFEST.read_text())["items"]
    cache: dict[str, dict] = json.loads(FOLLOWERS.read_text()) if FOLLOWERS.exists() else {}
    cutoff = time.time() - args.max_age_days * 86400
    # One spelling per account; skip names that can't be X handles (files saved without one).
    handles = {}
    for it in items:
        if HANDLE_RE.match(it["a"]):
            handles.setdefault(it["a"].lower(), it["a"])
    todo = [
        h for k, h in sorted(handles.items())
        if k not in cache or cache[k]["at"] < cutoff or cache[k].get("status") == "failed"
    ]
    print(f"{len(handles)} artists, {len(handles) - len(todo)} cached, {len(todo)} to fetch.", flush=True)

    counts: dict[str, int] = {}
    done = 0
    with ThreadPoolExecutor(WORKERS) as pool:
        for key, followers, status in pool.map(fetch, todo):
            # Keep the last known count if an account has since disappeared.
            prev = cache.get(key, {}).get("followers")
            cache[key] = {"followers": followers if followers is not None else prev, "status": status, "at": int(time.time())}
            counts[status] = counts.get(status, 0) + 1
            done += 1
            if done % 250 == 0:
                FOLLOWERS.write_text(json.dumps(cache, separators=(",", ":"), sort_keys=True))
                print(f"  {done}/{len(todo)} {counts}", flush=True)
    FOLLOWERS.write_text(json.dumps(cache, separators=(",", ":"), sort_keys=True))
    print(f"Done: {counts}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
