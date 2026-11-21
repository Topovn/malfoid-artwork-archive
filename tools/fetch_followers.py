#!/usr/bin/env python3
"""Fetch approximate follower counts for every artist in the gallery, on the main site and the 18+ page.

X's own no-login endpoints no longer expose follower counts, so this uses the public FxTwitter API
(https://github.com/FxEmbed/FxEmbed), which needs no login.
Requests are paced gently and retried on rate limits.
Results are cached in data/followers.json, keyed by lowercased handle, and artists fetched recently are skipped,
so reruns are cheap. Artists without a count are always retried.

When an artist's profile can't be fetched, the account may have been renamed: its current handle is traced through
the artist's newest tweets (as tools/trace_handles.py does) and recorded in data/handles.json, and the count is
fetched under the new handle. The site then shows the new handle; files keep the name they were saved under.
A rename can't be found once all of an artist's tweets are deleted; data/aliases.json takes those by hand.
Run tools/build_malfoid_gallery.py (and with --audience adult) afterwards to put the counts and handles into the manifests.

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

sys.path.insert(0, str(Path(__file__).resolve().parent))
from trace_handles import HANDLES, TWEETS_PER_HANDLE, trace  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
# Both sites share the caches: the main site and the 18+ page at docs/h/.
MANIFESTS = (ROOT / "docs" / "data" / "manifest.json", ROOT / "docs" / "h" / "data" / "manifest.json")
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

    items = [it for m in MANIFESTS if m.exists() for it in json.loads(m.read_text())["items"]]
    cache: dict[str, dict] = json.loads(FOLLOWERS.read_text()) if FOLLOWERS.exists() else {}
    cutoff = time.time() - args.max_age_days * 86400
    # One spelling per account; skip names that can't be X handles (files saved without one).
    handles = {}
    for it in items:
        if HANDLE_RE.match(it["a"]):
            handles.setdefault(it["a"].lower(), it["a"])
    todo = [
        h for k, h in sorted(handles.items())
        if k not in cache or cache[k]["at"] < cutoff or cache[k].get("status") != "ok"
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

    missing = sorted(k for k in handles if cache.get(k, {}).get("status") != "ok")
    if missing:
        resolve_renames(missing, items, cache)
        FOLLOWERS.write_text(json.dumps(cache, separators=(",", ":"), sort_keys=True))
    return 0


def resolve_renames(missing: list[str], items: list[dict], cache: dict[str, dict]) -> None:
    """Trace artists whose profile lookup failed to their current handle, then fetch the count under it."""
    traced: dict[str, dict] = json.loads(HANDLES.read_text()) if HANDLES.exists() else {}
    # Saved handles and newest tweets behind each shown handle ("o" is the saved handle after a rename).
    tweets: dict[str, dict[str, list[str]]] = {}
    for it in items:
        if it["t"] and it["a"].lower() in missing:
            saved = (it.get("o") or it["a"]).lower()
            tweets.setdefault(it["a"].lower(), {}).setdefault(saved, []).append(it["t"])
    jobs = [(saved, sorted(set(ids), key=int, reverse=True)[:TWEETS_PER_HANDLE])
            for shown in sorted(tweets) for saved, ids in sorted(tweets[shown].items())]
    print(f"Tracing {len(missing)} artists without a count through their tweets...", flush=True)

    current: dict[str, str] = {}  # shown handle -> account's current handle
    with ThreadPoolExecutor(WORKERS) as pool:
        for saved, entry in pool.map(trace, jobs):
            if entry["status"] == "ok":
                traced[saved] = entry
                shown = next(k for k, v in tweets.items() if saved in v)
                current[shown] = entry["current"]
            elif traced.get(saved, {}).get("id"):
                traced[saved] = {**traced[saved], "status": entry["status"], "at": entry["at"]}
            else:
                traced[saved] = entry
    HANDLES.write_text(json.dumps(traced, indent=1, sort_keys=True))

    # Renamed accounts get their count under the new handle; unchanged ones that still exist are tried once more,
    # since a failed lookup is sometimes transient.
    retry = sorted({h for h in current.values()})
    found: dict[str, int] = {}
    with ThreadPoolExecutor(WORKERS) as pool:
        for key, followers, status in pool.map(fetch, retry):
            cache[key] = {"followers": followers if followers is not None else cache.get(key, {}).get("followers"),
                          "status": status, "at": int(time.time())}
            if status == "ok":
                found[key] = followers
    renamed = {old: new for old, new in current.items() if new.lower() != old}
    for old, new in sorted(renamed.items()):
        print(f"  renamed: @{old} -> @{new} ({found.get(new.lower(), 'no count')} followers)")
    still = [k for k in missing if k not in current or current[k].lower() not in found]
    print(f"Renames: {len(renamed)}. Counts found on retry: {len(found)}. Still without a count: {len(still)}.")


if __name__ == "__main__":
    sys.exit(main())
