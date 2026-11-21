#!/usr/bin/env python3
"""Fetch the post text of every live tweet in the gallery, on both sites.

Uses the public FxTwitter API, which needs no login, sees sensitive (age-restricted) tweets,
and returns clean text: entities decoded, t.co links expanded, media links dropped and long posts in full.
When FxTwitter fails on a tweet, X's public embed endpoint fills in, except for long posts it cuts short.
Lost tweets are skipped, since the site never shows their text.
A tweet's text barely changes after posting, so each tweet is fetched once and cached in data/texts.json;
only tweets never fetched or whose last fetch failed are fetched again.
Run tools/build_malfoid_gallery.py (and with --audience adult) afterwards to publish the texts.

Usage:
    python3 tools/fetch_texts.py            # fetch tweets never fetched or that failed last time
    python3 tools/fetch_texts.py --refetch  # refetch everything
"""

from __future__ import annotations

import argparse
import html
import json
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from fetch_likes import ENDPOINT as EMBED, FALLBACK as ENDPOINT, MANIFESTS, WORKERS, get_json  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
TEXTS = ROOT / "data" / "texts.json"


def embed_text(data: dict) -> str | None:
    """The post text from X's embed endpoint, cleaned like FxTwitter's, or None if it can't be trusted.

    That endpoint HTML-escapes the text, shortens links to t.co, appends media links,
    keeps the leading @mentions of a reply and cuts long posts short.
    """
    if data.get("__typename") != "Tweet" or not isinstance(data.get("text"), str) or data.get("note_tweet"):
        return None  # a tombstone, or a long post this endpoint truncates
    text = data["text"][(data.get("display_text_range") or [0])[0]:]  # drop a reply's leading @mentions
    entities = data.get("entities") or {}
    for url in entities.get("urls") or []:
        text = text.replace(url["url"], url.get("expanded_url") or url["url"])
    for media in entities.get("media") or []:
        text = text.replace(media["url"], "")
    return html.unescape(text).strip()


def fetch(tweet_id: str) -> tuple[str, str | None, str]:
    """Return (id, text or None if unknown, status)."""
    code, data = get_json(ENDPOINT.format(id=tweet_id))
    tweet = (data or {}).get("tweet") or {}
    if isinstance(tweet.get("text"), str):
        return tweet_id, tweet["text"].strip(), "ok"
    if code in (401, 404):  # deleted, suspended or protected
        return tweet_id, None, "gone"
    # FxTwitter fails on the odd tweet; X's embed endpoint can still serve most of them.
    _, data = get_json(EMBED.format(id=tweet_id))
    text = embed_text(data or {})
    if text is not None:
        return tweet_id, text, "ok"
    return tweet_id, None, "failed" if code == 0 else f"http {code}"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--refetch", action="store_true", help="refetch every tweet, not only new and failed ones")
    args = ap.parse_args()

    items = [it for m in MANIFESTS if m.exists() for it in json.loads(m.read_text())["items"]]
    cache: dict[str, dict] = json.loads(TEXTS.read_text()) if TEXTS.exists() else {}
    ids = sorted({it["t"] for it in items if it["t"] and "l" not in it})
    todo = [i for i in ids if args.refetch or cache.get(i, {}).get("status") not in ("ok", "gone")]
    print(f"{len(ids)} tweets, {len(ids) - len(todo)} cached, {len(todo)} to fetch.", flush=True)

    counts: dict[str, int] = {}
    done = 0
    with ThreadPoolExecutor(WORKERS) as pool:
        for tweet_id, text, status in pool.map(fetch, todo):
            # Keep the last known text if a tweet has since disappeared.
            prev = cache.get(tweet_id, {}).get("text")
            cache[tweet_id] = {"text": text if text is not None else prev, "status": status, "at": int(time.time())}
            counts[status] = counts.get(status, 0) + 1
            done += 1
            if done % 250 == 0:
                TEXTS.write_text(json.dumps(cache, separators=(",", ":"), sort_keys=True, ensure_ascii=False))
                print(f"  {done}/{len(todo)} {counts}", flush=True)
    TEXTS.write_text(json.dumps(cache, separators=(",", ":"), sort_keys=True, ensure_ascii=False))
    print(f"Done: {counts}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
