#!/usr/bin/env python3
"""Build a Malfoid Artwork Archive site and thumbnails.

The main build scans the top level of the Malfoid folder plus its _LostTweets subfolder,
skipping the works listed in data/audience.json. It writes the site in docs/.
The 18+ build scans only the top level of the 'Malfoid H' folder; its subfolders are ignored.
A work there counts as a lost tweet when the lost-tweet log (_LostTweets.csv) lists it.
It writes the site in docs/h/, which GitHub Pages publishes as the /h/ page of the main site.
That page shares the main site's code, styles and icons and adds only its own data and thumbnails.
Malfoid AI/ is never scanned by either build.

Writes data/manifest.json and one WebP thumbnail per work into the chosen site folder.
Thumbnails are only regenerated when the source file is newer, so reruns are cheap.

Usage:
    python3 tools/build_malfoid_gallery.py
    python3 tools/build_malfoid_gallery.py --audience adult
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
DEFAULT_SITE = REPO / "docs"  # the main site; GitHub Pages publishes this folder
ADULT_SITE = DEFAULT_SITE / "h"  # the 18+ page, published at <main site>/h/
DATA = REPO / "data"  # working caches (likes, followers, handles, releases); never published
HARVEST_ROOT = Path.home() / "Downloads/twitter_media_harvest"
MAIN_SRC = HARVEST_ROOT / "Malfoid"
ADULT_SRC = HARVEST_ROOT / "Malfoid H"
AI_SRC = HARVEST_ROOT / "Malfoid AI"
# Relative links in the main index.html that the 18+ page keeps pointing at its own folder.
OWN_PATH_PREFIXES = ("data/", "thumbs/", "media/")
MAIN_TITLE = "The Malfoid Artwork Archive"
ADULT_TITLE = "R18 Malfoid Artwork Archive"
MAIN_QUOTE = "Since when have you been collecting my artwork, Potter?"
ADULT_QUOTE = "Caught you gooning, Potter"
ASSET_REF_RE = re.compile(r'\b(href|src)="([^"#?:]+)(\?[^"]*)?"')

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


def source_files(src: Path, audience: str = "main") -> list[tuple[Path, bool]]:
    """Every work the chosen site should show, as (path, is lost tweet), in a stable order.

    The main site reads the top level and _LostTweets, skipping excluded and 18+ works.
    The adult site reads only the top level of Malfoid H/, skipping excluded works.
    The source files always stay where the owner put them.
    """
    exclude_path = DATA / "exclude.json"
    exclude = json.loads(exclude_path.read_text()) if exclude_path.exists() else {}
    excluded_handles = {h.lower() for h in exclude.get("handles", [])}
    excluded_files = set(exclude.get("files", []))

    audience_path = DATA / "audience.json"
    audience_data = json.loads(audience_path.read_text()) if audience_path.exists() else {}
    adult_handles = {h.lower() for h in audience_data.get("handles", [])}
    adult_files = set(audience_data.get("files", []))

    folders = [(src, False)] if audience == "adult" else [(src, False), (src / LOST_DIR, True)]
    files: list[tuple[Path, bool]] = []
    for folder, lost in folders:
        if not folder.is_dir():
            continue
        for p in sorted(folder.iterdir()):
            ext = p.suffix.lower().lstrip(".")
            handle = parse_name(p.stem)[0].lower()
            if p.name in excluded_files or handle in excluded_handles:
                continue
            if audience == "main" and (p.name in adult_files or handle in adult_handles):
                continue
            if p.is_file() and not p.name.startswith(".") and ext in IMAGE_EXTS | VIDEO_EXTS:
                files.append((p, lost))
    return files


def write_adult_page() -> None:
    """Write the 18+ index.html from the main one, so both pages always share one layout.

    It is the main page marked with data-site="h", with the shared code, styles and icons
    loaded from the main site one folder up. Its data, thumbnails and media stay in its own folder.
    """
    html = (DEFAULT_SITE / "index.html").read_text(encoding="utf-8")

    def shared(m: re.Match) -> str:
        attr, path, query = m.group(1), m.group(2), m.group(3) or ""
        if path.startswith(OWN_PATH_PREFIXES) or not (DEFAULT_SITE / path).is_file():
            return m.group(0)
        return f'{attr}="../{path}{query}"'

    html, marked = re.subn(r"<html\b", '<html data-site="h"', html, count=1)
    if not marked:
        sys.exit("docs/index.html has no <html> tag.")
    html = ASSET_REF_RE.sub(shared, html)
    html = html.replace(MAIN_TITLE, ADULT_TITLE)  # the tab title, the heading and the home link's label
    if MAIN_QUOTE not in html:
        sys.exit("docs/index.html no longer has the speech bubble quote; update MAIN_QUOTE.")
    html = html.replace(MAIN_QUOTE, ADULT_QUOTE)  # the speech bubble under the icon
    ADULT_SITE.mkdir(parents=True, exist_ok=True)
    (ADULT_SITE / "index.html").write_text(html, encoding="utf-8")
    # The page also asks for My Pick and the Timeline's early works; neither exists for 18+ works.
    for name, empty in (("picks.json", {"picks": []}), ("early.json", {"early": []})):
        path = ADULT_SITE / "data" / name
        if not path.exists():
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(json.dumps(empty) + "\n", encoding="utf-8")
    # A local link to the originals for previewing full-size files, like docs/media; never published.
    media = ADULT_SITE / "media"
    if not media.exists() and not media.is_symlink():
        media.symlink_to(ADULT_SRC, target_is_directory=True)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--src", help="main-build source folder (default Malfoid/)")
    ap.add_argument("--audience", choices=("main", "adult"), default="main")
    ap.add_argument("--site", help="main-build output folder (default docs/)")
    args = ap.parse_args()

    if args.audience == "adult":
        if args.src or args.site:
            sys.exit("The 18+ build always reads Malfoid H/ and writes docs/h/; --src and --site are only for the main build.")
        src = ADULT_SRC
    else:
        src = Path(args.src).expanduser() if args.src else MAIN_SRC
    if src.resolve() == AI_SRC.resolve():
        sys.exit("Malfoid AI/ is never built.")
    if args.audience == "main" and src.resolve() == ADULT_SRC.resolve():
        sys.exit("Malfoid H/ is for the 18+ build; use --audience adult.")

    site = ADULT_SITE if args.audience == "adult" else Path(args.site).expanduser() if args.site else DEFAULT_SITE
    if not site.is_absolute():
        site = REPO / site
    thumbs = site / "thumbs"
    site_data = site / "data"
    if not src.is_dir():
        sys.exit(f"Source folder not found: {src}")

    # Lost tweets moved to Malfoid H/ keep their row in the main folder's log.
    lost_logs = [MAIN_SRC, src] if args.audience == "adult" else [src]
    lost_meta = {row["file"]: row for folder in lost_logs for row in read_csv(folder / LOST_DIR / f"{LOST_DIR}.csv")}
    danbooru = {}
    for row in read_csv(src / "Malfoid non-Twitter sources.csv"):
        m = re.search(r"/status/(\d+)", row.get("Source", ""))
        if m and row.get("Danbooru"):
            danbooru[m.group(1)] = row["Danbooru"]

    files = source_files(src, args.audience)

    thumbs.mkdir(parents=True, exist_ok=True)
    DATA.mkdir(parents=True, exist_ok=True)

    jobs = []
    for p, _ in files:
        kind = "video" if p.suffix.lower().lstrip(".") in VIDEO_EXTS else "image"
        jobs.append((str(p), str(thumbs / f"{p.name}.webp"), kind))

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
    for t in thumbs.glob("*.webp"):
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
        if args.audience == "adult":
            lost = p.name in lost_meta  # the 18+ page has no _LostTweets folder
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

    # Lost tweets were mostly deleted before their likes could be fetched. Give those a hidden estimate ("e"):
    # the artist's average likes per tweet still up, counting each tweet once however many images it has.
    # The site sorts and filters by it but never shows it, since it is a guess.
    live_likes: dict[str, dict[str, int]] = {}
    for it in items:
        if "v" in it and "l" not in it and it["t"]:
            live_likes.setdefault(it["a"].lower(), {})[it["t"]] = it["v"]
    for it in items:
        counts = live_likes.get(it["a"].lower())
        if "l" in it and "v" not in it and counts:
            it["e"] = round(sum(counts.values()) / len(counts))

    manifest = {"generated": max((it["d"] for it in items), default=0), "count": len(items), "items": items}
    artist_keys = {it["a"].lower() for it in items}
    manifest["followers"] = {
        k: v["followers"]
        for k, v in sorted(followers.items())
        if k in artist_keys and v.get("followers") is not None
    }
    if rel.get("repo"):
        manifest["releaseBase"] = f"https://github.com/{rel['repo']}/releases/download/"
    site_data.mkdir(parents=True, exist_ok=True)
    (site_data / "manifest.json").write_text(json.dumps(manifest, separators=(",", ":"), ensure_ascii=False))
    # The 18+ page copies the main index.html, so either build refreshes it once it exists.
    if args.audience == "adult" or ADULT_SITE.is_dir():
        write_adult_page()

    print(f"Wrote {len(items)} {args.audience} works ({sum(1 for i in items if 'l' in i)} lost tweets) to {site}.")
    if errors:
        print(f"{len(errors)} files failed:")
        for name, err in errors:
            print(f"  {name}: {err}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
