#!/usr/bin/env python3
"""Find each work's Danbooru post and rating, on both sites.

Matching is per file, so every image of a multi-image tweet links to its own post:

1. By checksum: Danbooru stores each upload's MD5, and an upload taken straight from Twitter has the same bytes as the
   file Twitter Media Harvest saved. Every run looks up every file this way, 100 per request, which also refreshes
   ratings and drops posts that were deleted or banned since.
2. By tweet: files still unmatched have their tweet searched by source. Each image is then paired with the candidate
   post whose preview looks the same (a difference hash of both pictures); a video is paired only when the tweet
   has one unmatched video and one candidate. Tweets are searched again about once a month, since Danbooru keeps
   adding older works.
3. By the owner's list: "Malfoid non-Twitter sources.csv" in the Malfoid folder names posts for tweets that are gone.

Results are cached in data/danbooru.json, keyed by filename. Run tools/build_malfoid_gallery.py (and with
--audience adult) afterwards: the build links each work to its post ("b") and gives the owner's full copy the
rating ("r"), which only the admin view shows.

Usage:
    python3 tools/fetch_danbooru.py                # checksums for everything, tweets not searched in about a month
    python3 tools/fetch_danbooru.py --recheck-all  # search every unmatched tweet again
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import io
import json
import os
import re
import ssl
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import zlib
from pathlib import Path

from PIL import Image, ImageOps

sys.path.insert(0, str(Path(__file__).resolve().parent))
from site_paths import full_manifests  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
CACHE = ROOT / "data" / "danbooru.json"
HARVEST = Path.home() / "Downloads/twitter_media_harvest"
MAIN_SRC = HARVEST / "Malfoid"
ADULT_SRC = HARVEST / "Malfoid H"
LOST_DIR = "_LostTweets"
SOURCES_CSV = MAIN_SRC / "Malfoid non-Twitter sources.csv"
API = "https://danbooru.donmai.us/posts.json"
FIELDS = "id,md5,rating,file_ext,is_deleted,is_banned,preview_file_url"
BATCH = 100  # checksums or post IDs per request
MIN_INTERVAL = 0.5  # seconds between requests
RECHECK_DAYS = 30  # an unmatched tweet is searched again after this many days, plus a per-tweet spread of up to as many
HASH_MAX_DISTANCE = 10  # of 64 bits: previews this close to the local image are the same picture
IMAGE_EXTS = {"jpg", "jpeg", "png", "webp", "gif"}
RATINGS = {"g", "s", "q", "e"}

try:  # python.org builds on macOS ship without system certificates
    import certifi

    SSL_CONTEXT = ssl.create_default_context(cafile=certifi.where())
except ImportError:
    SSL_CONTEXT = ssl.create_default_context()

_last_request = 0.0


def get(url: str, raw: bool = False):
    """GET url, paced, retrying rate limits and network errors. Returns parsed JSON (or bytes when raw), else None."""
    global _last_request
    backoff = 5.0
    for _ in range(6):
        wait = _last_request + MIN_INTERVAL - time.monotonic()
        if wait > 0:
            time.sleep(wait)
        _last_request = time.monotonic()
        req = urllib.request.Request(url, headers={"User-Agent": "archive-tools/1.0"})
        try:
            with urllib.request.urlopen(req, timeout=30, context=SSL_CONTEXT) as r:
                body = r.read()
                return body if raw else json.loads(body)
        except urllib.error.HTTPError as e:
            if e.code in (429, 500, 502, 503, 504):
                time.sleep(backoff)
                backoff *= 2
                continue
            return None
        except urllib.error.URLError as e:
            if isinstance(e.reason, ssl.SSLError):
                raise SystemExit(f"TLS error: {e.reason}. Install certifi (pip install certifi).")
            time.sleep(backoff)
            backoff *= 2
        except (TimeoutError, json.JSONDecodeError):
            time.sleep(backoff)
            backoff *= 2
    return None


def search(tags: str, limit: int) -> list[dict] | None:
    """Live posts (neither deleted nor banned) matching the tags, or None if Danbooru couldn't be reached."""
    url = f"{API}?{urllib.parse.urlencode({'tags': tags + ' status:any', 'limit': limit, 'only': FIELDS})}"
    data = get(url)
    if not isinstance(data, list):
        return None
    return [p for p in data if not p.get("is_deleted") and not p.get("is_banned") and p.get("rating") in RATINGS]


def source_path(it: dict, audience: str) -> Path:
    if audience == "adult":
        return ADULT_SRC / it["f"]
    return MAIN_SRC / LOST_DIR / it["f"] if "l" in it else MAIN_SRC / it["f"]


def file_md5(path: Path) -> str:
    h = hashlib.md5()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def dhash(im: Image.Image) -> int:
    """64-bit difference hash: which neighbouring pixels get brighter, on a 9x8 grayscale copy."""
    im = ImageOps.exif_transpose(im).convert("L").resize((9, 8), Image.LANCZOS)
    px = im.tobytes()  # one byte per pixel, row by row
    bits = 0
    for row in range(8):
        for col in range(8):
            bits = (bits << 1) | (px[row * 9 + col] < px[row * 9 + col + 1])
    return bits


def local_hash(path: Path) -> int | None:
    try:
        with Image.open(path) as im:
            im.draft("L", (64, 64))  # JPEGs decode at a fraction of their size
            return dhash(im)
    except Exception:  # noqa: BLE001 - an unreadable file just can't be matched by picture
        return None


def preview_hash(url: str | None) -> int | None:
    if not url or not url.startswith("https://"):
        return None
    body = get(url, raw=True)
    if not body:
        return None
    try:
        with Image.open(io.BytesIO(body)) as im:
            return dhash(im)
    except Exception:  # noqa: BLE001
        return None


def tweet_due(tweet: str, checked: int | None, now: float, recheck_all: bool) -> bool:
    if recheck_all or checked is None:
        return True
    spread = zlib.crc32(tweet.encode()) % RECHECK_DAYS  # so a first full pass doesn't come due all on one day
    return now - checked > (RECHECK_DAYS + spread) * 86400


def csv_posts() -> dict[str, int]:
    """Tweet ID -> Danbooru post ID from the owner's list of works found off Twitter."""
    out = {}
    if not SOURCES_CSV.exists():
        return out
    with SOURCES_CSV.open(newline="", encoding="utf-8-sig") as fh:
        for row in csv.DictReader(fh):
            tweet = re.search(r"/status/(\d+)", row.get("Source", ""))
            post = re.fullmatch(r"https://danbooru\.donmai\.us/posts/(\d+)", (row.get("Danbooru") or "").strip())
            if tweet and post:
                out[tweet.group(1)] = int(post.group(1))
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--recheck-all", action="store_true", help="search every unmatched tweet again")
    args = ap.parse_args()

    cache = json.loads(CACHE.read_text()) if CACHE.exists() else {}
    files: dict[str, dict] = cache.get("files", {})
    tweets: dict[str, int] = cache.get("tweets", {})
    now = time.time()

    def save() -> None:
        # Atomically, so a build reading the cache mid-run never sees half a file.
        tmp = CACHE.with_name(CACHE.name + ".tmp")
        tmp.write_text(json.dumps({"files": files, "tweets": tweets}, separators=(",", ":"), sort_keys=True))
        os.replace(tmp, CACHE)

    # Every work of both sites, with where its file lives.
    works: dict[str, tuple[dict, Path]] = {}
    for audience, manifest in zip(("main", "adult"), full_manifests()):
        if manifest.exists():
            for it in json.loads(manifest.read_text())["items"]:
                works[it["f"]] = (it, source_path(it, audience))
    files = {f: e for f, e in files.items() if f in works}  # works gone from both sites
    print(f"{len(works)} works.", flush=True)

    # Checksums, recomputed only for files that changed.
    hashed = 0
    for f, (it, path) in works.items():
        try:
            st = path.stat()
        except FileNotFoundError:
            print(f"  missing: {path}")
            continue
        sig = f"{st.st_size}:{int(st.st_mtime)}"
        entry = files.setdefault(f, {})
        if entry.get("sig") != sig:
            entry.update(md5=file_md5(path), sig=sig)
            hashed += 1
    print(f"Checksummed {hashed} new or changed files.", flush=True)

    # 1. By checksum, for every file: new matches, fresh ratings, and posts gone since.
    by_md5: dict[str, list[str]] = {}  # the same bytes can be saved under two names
    for f, e in files.items():
        if e.get("md5"):
            by_md5.setdefault(e["md5"], []).append(f)
    md5s = sorted(by_md5)
    found: dict[str, dict] = {}
    complete = True
    for i in range(0, len(md5s), BATCH):
        chunk = md5s[i:i + BATCH]
        posts = search("md5:" + ",".join(chunk), BATCH)
        if posts is None:
            complete = False
            print(f"  checksum batch {i // BATCH + 1} failed; keeping what those files had.")
            for m in chunk:
                for f in by_md5[m]:
                    found[f] = None
            continue
        for p in posts:
            for f in by_md5.get(p.get("md5"), []):
                found[f] = p
    for f, e in files.items():
        if f in found and found[f] is None:
            continue  # its batch failed
        if f in found:
            e.update(post=found[f]["id"], rating=found[f]["rating"], via="md5")
        elif e.get("via") == "md5":
            for k in ("post", "rating", "via"):
                e.pop(k, None)
    print(f"Checksum matches: {sum(1 for e in files.values() if e.get('via') == 'md5')}"
          + ("" if complete else " (some batches failed)"), flush=True)

    # Matches made by tweet or from the list: refresh their ratings, and drop posts deleted or banned since.
    others = {e["post"]: f for f, e in files.items() if e.get("post") and e.get("via") in ("source", "csv")}
    ids = sorted(others)
    for i in range(0, len(ids), BATCH):
        chunk = ids[i:i + BATCH]
        posts = search("id:" + ",".join(map(str, chunk)), BATCH)
        if posts is None:
            continue
        live = {p["id"]: p for p in posts}
        for pid in chunk:
            e = files[others[pid]]
            if pid in live:
                e["rating"] = live[pid]["rating"]
            else:
                for k in ("post", "rating", "via"):
                    e.pop(k, None)

    # 2. By tweet, for tweets with files still unmatched.
    taken = {e["post"] for e in files.values() if e.get("post")}
    unmatched: dict[str, list[str]] = {}
    for f, (it, _) in works.items():
        if it.get("t") and not files.get(f, {}).get("post"):
            unmatched.setdefault(it["t"], []).append(f)
    due = sorted(t for t in unmatched if tweet_due(t, tweets.get(t), now, args.recheck_all))
    print(f"{len(unmatched)} tweets have unmatched files; searching {len(due)}.", flush=True)
    by_source = 0
    for n, tweet in enumerate(due, 1):
        posts = search(f"source:*{tweet}*", 50)
        if posts is None:
            continue  # unreachable: try again next run
        tweets[tweet] = int(now)
        candidates = [p for p in posts if p["id"] not in taken]
        if candidates:
            todo = sorted(unmatched[tweet], key=lambda f: works[f][0].get("i", 1))
            stills = [f for f in todo if works[f][0]["x"].lower() in IMAGE_EXTS]
            clips = [f for f in todo if f not in stills]
            if stills:
                previews = {p["id"]: preview_hash(p.get("preview_file_url")) for p in candidates}
                for f in stills:
                    mine = local_hash(works[f][1])
                    if mine is None:
                        continue
                    scored = sorted(
                        (bin(mine ^ h).count("1"), p["id"]) for p in candidates
                        if p["id"] not in taken and (h := previews.get(p["id"])) is not None
                    )
                    if scored and scored[0][0] <= HASH_MAX_DISTANCE:
                        post = next(p for p in candidates if p["id"] == scored[0][1])
                        files[f].update(post=post["id"], rating=post["rating"], via="source")
                        taken.add(post["id"])
                        by_source += 1
            videos = [p for p in candidates if p["id"] not in taken and p.get("file_ext") in ("mp4", "webm", "zip")]
            if len(clips) == 1 and len(videos) == 1:
                files[clips[0]].update(post=videos[0]["id"], rating=videos[0]["rating"], via="source")
                taken.add(videos[0]["id"])
                by_source += 1
        if n % 200 == 0:
            save()
            print(f"  {n}/{len(due)} searched, {by_source} matched", flush=True)
    tweets = {t: at for t, at in tweets.items() if t in unmatched}  # matched tweets no longer need searching
    print(f"Tweet search matches: {by_source}", flush=True)

    # 3. The owner's list, for files nothing else matched.
    listed = csv_posts()
    want = {listed[it["t"]] for f, (it, _) in works.items()
            if it.get("t") in listed and not files.get(f, {}).get("post")}
    live: dict[int, dict] = {}
    ids = sorted(want)
    for i in range(0, len(ids), BATCH):
        posts = search("id:" + ",".join(map(str, ids[i:i + BATCH])), BATCH) or []
        live.update({p["id"]: p for p in posts})
    for f, (it, _) in works.items():
        post = live.get(listed.get(it.get("t"), -1))
        if post and not files.get(f, {}).get("post"):
            files[f].update(post=post["id"], rating=post["rating"], via="csv")

    save()
    matched = [e for e in files.values() if e.get("post")]
    counts = {r: sum(1 for e in matched if e["rating"] == r) for r in ("g", "s", "q", "e")}
    print(f"Done: {len(matched)} of {len(works)} works on Danbooru {counts}.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
