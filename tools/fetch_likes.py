#!/usr/bin/env python3
"""Fetch approximate like counts for every tweet in the gallery.

Uses X's public embed endpoint (the one that powers embedded tweets), which needs no login.
It is undocumented, so requests are paced gently and retried on rate limits.
Results are cached in data/likes.json, and tweets fetched recently are skipped, so reruns are cheap.
Run tools/build_malfoid_gallery.py afterwards to put the counts into the manifest.

Usage:
    python3 tools/fetch_likes.py                  # fetch tweets never fetched or older than 7 days
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
MANIFEST = ROOT / "docs" / "data" / "manifest.json"
LIKES = ROOT / "data" / "likes.json"
ENDPOINT = "https://cdn.syndication.twimg.com/tweet-result?id={id}&lang=en&token=0"
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


def fetch(tweet_id: str) -> tuple[str, int | None, str]:
    """Return (id, likes or None if the tweet is gone, status)."""
    backoff = 5.0
    for _ in range(6):
        pace()
        req = urllib.request.Request(ENDPOINT.format(id=tweet_id), headers={"User-Agent": "Mozilla/5.0"})
        try:
            with urllib.request.urlopen(req, timeout=20, context=SSL_CONTEXT) as r:
                data = json.load(r)
            if data.get("__typename") == "TweetTombstone" or "favorite_count" not in data:
                return tweet_id, None, "gone"
            return tweet_id, int(data["favorite_count"]), "ok"
        except urllib.error.HTTPError as e:
            if e.code == 404:
                return tweet_id, None, "gone"
            if e.code in (429, 500, 502, 503, 504):
                time.sleep(backoff)
                backoff *= 2
                continue
            return tweet_id, None, f"http {e.code}"
        except urllib.error.URLError as e:
            if isinstance(e.reason, ssl.SSLError):
                raise SystemExit(f"TLS error talking to X: {e.reason}. Install certifi (pip install certifi).")
            time.sleep(backoff)
            backoff *= 2
        except (TimeoutError, json.JSONDecodeError):
            time.sleep(backoff)
            backoff *= 2
    return tweet_id, None, "failed"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--max-age-days", type=float, default=7)
    args = ap.parse_args()

    items = json.loads(MANIFEST.read_text())["items"]
    cache: dict[str, dict] = json.loads(LIKES.read_text()) if LIKES.exists() else {}
    cutoff = time.time() - args.max_age_days * 86400
    ids = sorted({it["t"] for it in items if it["t"]})
    todo = [i for i in ids if i not in cache or cache[i]["at"] < cutoff or cache[i].get("status") == "failed"]
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
