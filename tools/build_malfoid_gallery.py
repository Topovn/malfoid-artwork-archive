#!/usr/bin/env python3
"""Build the Malfoid Artwork Archive index and thumbnails.

Scans the top level of the Malfoid folder plus its _LostTweets subfolder.
Every other subfolder is ignored.
Writes data/manifest.json and one WebP thumbnail per work into thumbs/.
Thumbnails are only regenerated when the source file is newer, so reruns are cheap.

Usage:
    python3 tools/build_malfoid_gallery.py [--src ~/Downloads/twitter_media_harvest/Malfoid]
"""

from __future__ import annotations

import argparse
import csv
import json
import os
import re
import subprocess
import sys
from concurrent.futures import ProcessPoolExecutor
from datetime import datetime
from pathlib import Path

from PIL import Image, ImageOps

REPO = Path(__file__).resolve().parent.parent
SITE = REPO / "docs"  # the only folder GitHub Pages publishes
THUMBS = SITE / "thumbs"
DATA = REPO / "data"  # working caches (likes, followers, handles, releases); never published
SITE_DATA = SITE / "data"

THUMB_WIDTH = 400
THUMB_QUALITY = 72
LOST_DIR = "_LostTweets"
IMAGE_EXTS = {"jpg", "jpeg", "png", "webp", "gif"}
VIDEO_EXTS = {"mp4", "mov", "webm"}
ANIMATED_EXTS = {"gif"} | VIDEO_EXTS
TWITTER_EPOCH_MS = 1288834974657

# handle-tweetId-NN.ext, handle-tweetId.ext, or a bare handle.ext
NAME_RE = re.compile(r"^(?P<handle>.+?)(?:-(?P<tweet>\d{15,20}))?(?:-(?P<idx>\d{1,3}))?$")


def parse_name(stem: str) -> tuple[str, str, int]:
    m = NAME_RE.match(stem)
    handle, tweet, idx = m.group("handle"), m.group("tweet") or "", m.group("idx")
    return handle, tweet, int(idx) if idx else 1


def posted_ms(tweet_id: str) -> int:
    return (int(tweet_id) >> 22) + TWITTER_EPOCH_MS if tweet_id else 0


def added_ms(path: Path) -> int:
    st = path.stat()
    return int(getattr(st, "st_birthtime", st.st_mtime) * 1000)


def read_csv(path: Path) -> list[dict]:
    if not path.exists():
        return []
    with path.open(newline="", encoding="utf-8-sig") as fh:
        return list(csv.DictReader(fh))


def video_seconds(src: str) -> float | None:
    """Video length in seconds, or None if ffprobe is missing or can't tell."""
    try:
        r = subprocess.run(
            ["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", src],
            capture_output=True, text=True,
        )
        return float(r.stdout.strip())
    except (OSError, ValueError):
        return None


def make_thumb(job: tuple[str, str, str]) -> tuple[str, int, int, float | None, str]:
    """Return (thumb name, width, height, video seconds or None, error) for one source file."""
    src, dst, kind = job
    src_p, dst_p = Path(src), Path(dst)
    seconds = video_seconds(src) if kind == "video" else None
    try:
        if not (dst_p.exists() and dst_p.stat().st_mtime >= src_p.stat().st_mtime):
            tmp = dst_p.with_name(dst_p.name + ".tmp")
            if kind == "video":
                # ffmpeg builds often lack libwebp, so grab a PNG frame and let Pillow encode it.
                frame = dst_p.with_name(dst_p.name + ".frame.png")
                for seek in ("0.5", "0"):  # very short clips have no frame at 0.5s
                    r = subprocess.run(
                        ["ffmpeg", "-v", "error", "-y", "-ss", seek, "-i", src, "-frames:v", "1", str(frame)],
                        capture_output=True, text=True,
                    )
                    if r.returncode == 0 and frame.exists():
                        break
                else:
                    return dst_p.name, 0, 0, seconds, r.stderr.strip()[:200]
                src = str(frame)
            try:
                with Image.open(src) as im:
                    im = ImageOps.exif_transpose(im)
                    im = im.convert("RGBA" if im.mode in ("RGBA", "LA", "P") else "RGB")
                    if im.width > THUMB_WIDTH:
                        im = im.resize((THUMB_WIDTH, round(im.height * THUMB_WIDTH / im.width)), Image.LANCZOS)
                    im.save(tmp, "WEBP", quality=THUMB_QUALITY, method=5)
            finally:
                if kind == "video":
                    Path(src).unlink(missing_ok=True)
            os.replace(tmp, dst_p)
        with Image.open(dst_p) as im:
            return dst_p.name, im.width, im.height, seconds, ""
    except Exception as exc:  # noqa: BLE001 - report and keep going
        return dst_p.name, 0, 0, seconds, str(exc)[:200]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--src", default=str(Path.home() / "Downloads/twitter_media_harvest/Malfoid"))
    args = ap.parse_args()
    src = Path(args.src).expanduser()
    if not src.is_dir():
        sys.exit(f"Source folder not found: {src}")

    lost_meta = {row["file"]: row for row in read_csv(src / LOST_DIR / f"{LOST_DIR}.csv")}
    danbooru = {}
    for row in read_csv(src / "Malfoid non-Twitter sources.csv"):
        m = re.search(r"/status/(\d+)", row.get("Source", ""))
        if m and row.get("Danbooru"):
            danbooru[m.group(1)] = row["Danbooru"]

    # Works the owner keeps off the site (wrong attribution and the like); the source files stay put.
    exclude_path = DATA / "exclude.json"
    exclude = json.loads(exclude_path.read_text()) if exclude_path.exists() else {}
    excluded_handles = {h.lower() for h in exclude.get("handles", [])}
    excluded_files = set(exclude.get("files", []))

    files: list[tuple[Path, bool]] = []
    for folder, lost in ((src, False), (src / LOST_DIR, True)):
        if not folder.is_dir():
            continue
        for p in sorted(folder.iterdir()):
            ext = p.suffix.lower().lstrip(".")
            if p.name in excluded_files or parse_name(p.stem)[0].lower() in excluded_handles:
                continue
            if p.is_file() and not p.name.startswith(".") and ext in IMAGE_EXTS | VIDEO_EXTS:
                files.append((p, lost))

    THUMBS.mkdir(parents=True, exist_ok=True)
    DATA.mkdir(parents=True, exist_ok=True)

    jobs = []
    for p, _ in files:
        kind = "video" if p.suffix.lower().lstrip(".") in VIDEO_EXTS else "image"
        jobs.append((str(p), str(THUMBS / f"{p.name}.webp"), kind))

    print(f"Processing {len(jobs)} files with {os.cpu_count()} workers...", flush=True)
    results = {}
    errors = []
    with ProcessPoolExecutor() as pool:
        for i, (name, w, h, seconds, err) in enumerate(pool.map(make_thumb, jobs, chunksize=16), 1):
            results[name] = (w, h, seconds)
            if err:
                errors.append((name, err))
            if i % 500 == 0:
                print(f"  {i}/{len(jobs)}", flush=True)

    # Drop thumbnails whose source no longer exists.
    wanted = {Path(j[1]).name for j in jobs}
    for t in THUMBS.glob("*.webp"):
        if t.name not in wanted:
            t.unlink()

    # Full-size originals uploaded by tools/upload_originals.py, as "tag/asset name".
    rel_path = DATA / "releases.json"
    rel = json.loads(rel_path.read_text()) if rel_path.exists() else {}
    released = rel.get("assets", {})
    # Approximate like counts from tools/fetch_likes.py.
    likes_path = DATA / "likes.json"
    likes = json.loads(likes_path.read_text()) if likes_path.exists() else {}
    # Current handles from tools/trace_handles.py, keyed by lowercased saved handle.
    handles_path = DATA / "handles.json"
    handles = json.loads(handles_path.read_text()) if handles_path.exists() else {}
    # Manual renames for handles the tracer can't resolve, and known posting years for undated works.
    aliases_path, dates_path = DATA / "aliases.json", DATA / "dates.json"
    aliases = {k.lower(): v for k, v in (json.loads(aliases_path.read_text()) if aliases_path.exists() else {}).get("handles", {}).items()}
    years = (json.loads(dates_path.read_text()) if dates_path.exists() else {}).get("files", {})

    # Approximate follower counts from tools/fetch_followers.py, keyed by lowercased handle.
    followers_path = DATA / "followers.json"
    followers = json.loads(followers_path.read_text()) if followers_path.exists() else {}

    items = []
    for p, lost in files:
        ext = p.suffix.lower().lstrip(".")
        w, h, seconds = results.get(f"{p.name}.webp", (0, 0, None))
        if not w:
            continue
        handle, tweet, idx = parse_name(p.stem)
        meta = lost_meta.get(p.name, {})
        if meta.get("artist_handle"):
            handle = meta["artist_handle"]
        item = {
            "f": p.name,
            "a": handle,
            "t": tweet,
            "i": idx,
            "x": ext,
            "k": "anim" if ext in ANIMATED_EXTS else "art",
            "w": w,
            "h": h,
            "p": posted_ms(tweet),
            "d": added_ms(p),
        }
        if seconds is not None:
            item["s"] = round(seconds)  # video length, used to keep long videos off the home shelves
        if lost:
            item["l"] = meta.get("reason") or "Tweet no longer available"
            # Moving a file into _LostTweets resets its timestamps, so prefer the logged download time.
            try:
                item["d"] = int(datetime.strptime(meta["downloaded"], "%Y-%m-%d %H:%M").timestamp() * 1000)
            except (KeyError, ValueError):
                pass
        # Show renamed artists under their current handle; "o" keeps the saved one for search.
        # Two saved handles of one account resolve to the same current handle, so they merge.
        current = aliases.get(handle.lower()) or handles.get(handle.lower(), {}).get("current")
        if current and current != handle:
            item["a"] = current
            if current.lower() != handle.lower():
                item["o"] = handle
        if not item["p"] and p.name in years:
            item["y"] = int(years[p.name])  # posting year only; no exact date is known
        if tweet in danbooru:
            item["b"] = danbooru[tweet]
        if tweet in likes and likes[tweet].get("likes") is not None:
            item["v"] = likes[tweet]["likes"]
        if p.name in released:
            item["u"] = f"{released[p.name]['tag']}/{released[p.name]['name']}"
        items.append(item)

    manifest = {"generated": max((it["d"] for it in items), default=0), "count": len(items), "items": items}
    artist_keys = {it["a"].lower() for it in items}
    manifest["followers"] = {
        k: v["followers"]
        for k, v in sorted(followers.items())
        if k in artist_keys and v.get("followers") is not None
    }
    if rel.get("repo"):
        manifest["releaseBase"] = f"https://github.com/{rel['repo']}/releases/download/"
    SITE_DATA.mkdir(parents=True, exist_ok=True)
    (SITE_DATA / "manifest.json").write_text(json.dumps(manifest, separators=(",", ":"), ensure_ascii=False))

    print(f"Wrote {len(items)} works ({sum(1 for i in items if 'l' in i)} lost tweets).")
    if errors:
        print(f"{len(errors)} files failed:")
        for name, err in errors:
            print(f"  {name}: {err}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
