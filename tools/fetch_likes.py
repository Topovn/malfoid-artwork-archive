#!/usr/bin/env python3
"""Fetch approximate like counts for every tweet in the gallery, on the main site and the 18+ page.

Uses X's public embed endpoint (the one that powers embedded tweets), which needs no login.
That endpoint hides sensitive (age-restricted) tweets behind an empty tombstone, so those are
looked up again through the public FxTwitter API, which still sees them.
Both are undocumented, so requests are paced gently and retried on rate limits.
Results are cached in data/likes.json, and tweets fetched recently are skipped, so reruns are cheap.
A count taken during a tweet's first week is still climbing, so those tweets are fetched again on every run
until a count is taken after their first week.
Run tools/build_malfoid_gallery.py (and with --audience adult) afterwards to put the counts into the manifests.

Usage:
    python3 tools/fetch_likes.py                  # fetch tweets never fetched, fetched young, or older than 7 days
    python3 tools/fetch_likes.py --max-age-days 0 # refetch everything
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

ROOT = Path(__file__).resolve().parent.parent
# Both sites share the caches: the main site and the 18+ page at docs/h/.
MANIFESTS = (ROOT / "docs" / "data" / "manifest.json", ROOT / "docs" / "h" / "data" / "manifest.json")
LIKES = ROOT / "data" / "likes.json"
ENDPOINT = "https://cdn.syndication.twimg.com/tweet-result?id={id}&lang=en&token=0"
FALLBACK = "https://api.fxtwitter.com/status/{id}"
SETTLE_DAYS = 7  # a count taken before the tweet is this old is refetched on every run
SNOWFLAKE_EPOCH_MS = 1288834974657  # X tweet IDs carry their creation time in milliseconds since this epoch
WORKERS = 4
MIN_INTERVAL = 0.15  # seconds between requests across all workers

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


def get_json(url: str) -> tuple[int, dict | None]:
    """GET url and return (HTTP status, parsed JSON or None). Retries rate limits and network errors."""
    backoff = 5.0
    for _ in range(6):
        pace()
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
        try:
            with urllib.request.urlopen(req, timeout=20, context=SSL_CONTEXT) as r:
                return r.status, json.load(r)
        except urllib.error.HTTPError as e:
            if e.code in (429, 500, 502, 503, 504):
                time.sleep(backoff)
                backoff *= 2
                continue
            return e.code, None
        except urllib.error.URLError as e:
            if isinstance(e.reason, ssl.SSLError):
                raise SystemExit(f"TLS error: {e.reason}. Install certifi (pip install certifi).")
            time.sleep(backoff)
            backoff *= 2
        except (TimeoutError, json.JSONDecodeError):
            time.sleep(backoff)
            backoff *= 2
    return 0, None


def fetch(tweet_id: str) -> tuple[str, int | None, str]:
    """Return (id, likes or None if the tweet is gone, status)."""
    code, data = get_json(ENDPOINT.format(id=tweet_id))
    if data is not None and "favorite_count" in data and data.get("__typename") != "TweetTombstone":
        return tweet_id, int(data["favorite_count"]), "ok"
    if code not in (200, 404):
        return tweet_id, None, "failed" if code == 0 else f"http {code}"
    # A tombstone or 404 here can also mean a live sensitive tweet; ask FxTwitter.
    code, data = get_json(FALLBACK.format(id=tweet_id))
    tweet = (data or {}).get("tweet") or {}
    if isinstance(tweet.get("likes"), int):
        return tweet_id, tweet["likes"], "ok"
    if code in (401, 404):  # deleted, suspended or protected
        return tweet_id, None, "gone"
    return tweet_id, None, "failed"


def posted_at(tweet_id: str) -> float:
    """Seconds since the Unix epoch when the tweet was posted, read from its ID."""
    return ((int(tweet_id) >> 22) + SNOWFLAKE_EPOCH_MS) / 1000


def needs_fetch(tweet_id: str, entry: dict | None, cutoff: float) -> bool:
    if entry is None or entry.get("status") != "ok" or entry["at"] < cutoff:
        return True
    return entry["at"] - posted_at(tweet_id) < SETTLE_DAYS * 86400  # taken while the count was still climbing


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--max-age-days", type=float, default=7)
    args = ap.parse_args()

    items = [it for m in MANIFESTS if m.exists() for it in json.loads(m.read_text())["items"]]
    cache: dict[str, dict] = json.loads(LIKES.read_text()) if LIKES.exists() else {}
    cutoff = time.time() - args.max_age_days * 86400
    ids = sorted({it["t"] for it in items if it["t"]})
    todo = [i for i in ids if needs_fetch(i, cache.get(i), cutoff)]
    print(f"{len(ids)} tweets, {len(ids) - len(todo)} cached, {len(todo)} to fetch.", flush=True)

    counts: dict[str, int] = {}
    done = 0
    with ThreadPoolExecutor(WORKERS) as pool:
        for tweet_id, likes, status in pool.map(fetch, todo):
            # Keep the last known count if a tweet has since disappeared.
            prev = cache.get(tweet_id, {}).get("likes")
            cache[tweet_id] = {"likes": likes if likes is not None else prev, "status": status, "at": int(time.time())}
            counts[status] = counts.get(status, 0) + 1
            done += 1
            if done % 250 == 0:
                LIKES.write_text(json.dumps(cache, separators=(",", ":"), sort_keys=True))
                print(f"  {done}/{len(todo)} {counts}", flush=True)
    LIKES.write_text(json.dumps(cache, separators=(",", ":"), sort_keys=True))
    print(f"Done: {counts}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
