#!/usr/bin/env python3
"""Trace every artist handle to the account's current handle and permanent user ID.

Artists sometimes rename their X account, so files saved under an old handle would otherwise
show up as a separate artist with no follower count.
Each handle is resolved through one of its tweets on X's public embed endpoint (no login),
which reports the account's current handle and its user ID; the ID never changes.
Files are never renamed: the build shows works under the current handle and keeps the old one searchable.
Results are cached in data/handles.json, keyed by lowercased saved handle, and recent lookups are skipped.

Usage:
    python3 tools/trace_handles.py                    # trace handles never traced or older than 30 days
    python3 tools/trace_handles.py --max-age-days 0   # retrace everything
Then run tools/fetch_followers.py and tools/build_malfoid_gallery.py.
"""

from __future__ import annotations

import argparse
import json
import ssl
import sys
import threading
import time
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from site_paths import full_manifests  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
# Both sites share the cache: the main site and the 18+ page at docs/h/.
# The full manifests, so hidden artists (see tools/site_paths.py) stay current in the owner's view too.
MANIFESTS = full_manifests()
HANDLES = ROOT / "data" / "handles.json"
ENDPOINT = "https://cdn.syndication.twimg.com/tweet-result?id={id}&lang=en&token=0"
WORKERS = 4
MIN_INTERVAL = 0.15  # seconds between requests across all workers
TWEETS_PER_HANDLE = 3  # tweets tried per handle, newest first, in case some were deleted

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


def tweet_user(tweet_id: str) -> tuple[dict | None, str]:
    """Return (user, status) for one tweet; status is ok, gone or failed."""
    backoff = 5.0
    for _ in range(6):
        pace()
        req = urllib.request.Request(ENDPOINT.format(id=tweet_id), headers={"User-Agent": "Mozilla/5.0"})
        try:
            with urllib.request.urlopen(req, timeout=20, context=SSL_CONTEXT) as r:
                data = json.load(r)
            user = data.get("user") or {}
            if data.get("__typename") == "TweetTombstone" or not user.get("id_str"):
                return None, "gone"
            return user, "ok"
        except urllib.error.HTTPError as e:
            if e.code == 404:
                return None, "gone"
            if e.code in (429, 500, 502, 503, 504):
                time.sleep(backoff)
                backoff *= 2
                continue
            return None, f"http {e.code}"
        except urllib.error.URLError as e:
            if isinstance(e.reason, ssl.SSLError):
                raise SystemExit(f"TLS error talking to X: {e.reason}. Install certifi (pip install certifi).")
            time.sleep(backoff)
            backoff *= 2
        except (TimeoutError, json.JSONDecodeError):
            time.sleep(backoff)
            backoff *= 2
    return None, "failed"


def trace(job: tuple[str, list[str]]) -> tuple[str, dict]:
    key, tweet_ids = job
    status = "gone"
    for tweet_id in tweet_ids:
        user, status = tweet_user(tweet_id)
        if user:
            return key, {"id": user["id_str"], "current": user["screen_name"], "status": "ok", "at": int(time.time())}
        if status == "failed":
            break
    return key, {"status": status, "at": int(time.time())}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--max-age-days", type=float, default=30)
    args = ap.parse_args()

    items = [it for m in MANIFESTS if m.exists() for it in json.loads(m.read_text())["items"]]
    cache: dict[str, dict] = json.loads(HANDLES.read_text()) if HANDLES.exists() else {}
    cutoff = time.time() - args.max_age_days * 86400

    # Tweets per saved handle, newest first. "o" is the saved handle when the build already renamed it.
    tweets: dict[str, list[str]] = {}
    for it in items:
        if it["t"]:
            tweets.setdefault((it.get("o") or it["a"]).lower(), []).append(it["t"])
    jobs = []
    for key, ids in sorted(tweets.items()):
        entry = cache.get(key)
        if entry and entry["at"] >= cutoff and entry.get("status") != "failed":
            continue
        jobs.append((key, sorted(set(ids), key=int, reverse=True)[:TWEETS_PER_HANDLE]))
    print(f"{len(tweets)} handles, {len(tweets) - len(jobs)} cached, {len(jobs)} to trace.", flush=True)

    counts: dict[str, int] = {}
    with ThreadPoolExecutor(WORKERS) as pool:
        for done, (key, entry) in enumerate(pool.map(trace, jobs), 1):
            # A handle whose tweets are all gone keeps its last known account.
            if entry["status"] != "ok" and cache.get(key, {}).get("id"):
                entry = {**cache[key], "status": entry["status"], "at": entry["at"]}
            cache[key] = entry
            counts[entry["status"]] = counts.get(entry["status"], 0) + 1
            if done % 250 == 0:
                HANDLES.write_text(json.dumps(cache, indent=1, sort_keys=True))
                print(f"  {done}/{len(jobs)} {counts}", flush=True)
    HANDLES.write_text(json.dumps(cache, indent=1, sort_keys=True))

    renamed = sum(1 for k, v in cache.items() if v.get("current") and v["current"].lower() != k)
    ids = [v["id"] for v in cache.values() if v.get("id")]
    print(f"Done: {counts}. {renamed} handles changed; {len(ids) - len(set(ids))} saved handles share an account with another.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
