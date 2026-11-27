#!/usr/bin/env python3
"""Build the main site's Emotes page data from the emotes folder inside Malfoid/.

Writes docs/data/emotes.json, one still WebP thumbnail per GIF into docs/emotes/ and one animated WebP preview per GIF
into docs/emotes/anim/ (the page plays these, served by GitHub Pages), and drops the files of removed GIFs. The page lists emotes in the order of the existing emotes.json (the artist's Discord order); new GIFs
are added at the end in download order. The full GIFs go to GitHub Releases with `tools/upload_originals.py --audience emotes`, which reruns
this build so the page links to them.

Usage:
    python3 tools/build_emotes.py
"""

from __future__ import annotations

import json
import os
import sys
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

from PIL import Image, ImageSequence

sys.path.insert(0, str(Path(__file__).resolve().parent))
from build_malfoid_gallery import DATA, DEFAULT_SITE, MAIN_SRC, THUMB_QUALITY  # noqa: E402

EMOTES_DIR = "_Emotes by u:CuteAssTigerENVtuber"
EMOTES_SRC = MAIN_SRC / EMOTES_DIR
MANIFEST = DEFAULT_SITE / "data" / "emotes.json"
THUMBS = DEFAULT_SITE / "emotes"
PREVIEWS = THUMBS / "anim"
THUMB_SIZE = 320
# Previews keep the GIF's size and every frame: scaling down saves almost nothing on this flat art.
PREVIEW_QUALITY = 60
# Some emotes start almost empty (the character slides in). Their first frame is replaced by the fullest one.
MOSTLY_EMPTY = 0.6


def emote_files() -> list[Path]:
    listed = json.loads(MANIFEST.read_text())["items"] if MANIFEST.exists() else []
    rank = {item["f"]: i for i, item in enumerate(listed)}
    files = (p for p in EMOTES_SRC.iterdir() if p.suffix.lower() == ".gif" and not p.name.startswith("."))
    return sorted(files, key=lambda p: (rank.get(p.name, len(rank)), p.stat().st_birthtime, p.name))


def coverage(frame: Image.Image) -> int:
    """How many pixels of the frame show anything."""
    return sum(frame.getchannel("A").histogram()[128:])


def still_frame(src: Path) -> Image.Image:
    with Image.open(src) as im:
        frames = [f.convert("RGBA") for f in ImageSequence.Iterator(im)]
    fullest = max(frames, key=coverage)
    return frames[0] if coverage(frames[0]) >= MOSTLY_EMPTY * coverage(fullest) else fullest


def is_current(dst: Path, src: Path) -> bool:
    return dst.exists() and dst.stat().st_mtime >= src.stat().st_mtime


def save_webp(im: Image.Image, dst: Path, **options) -> None:
    tmp = dst.with_name(dst.name + ".tmp")
    im.save(tmp, "WEBP", **options)
    os.replace(tmp, dst)


def make_still(src: Path) -> None:
    dst = THUMBS / f"{src.stem}.webp"
    if is_current(dst, src):
        return
    im = still_frame(src)
    im.thumbnail((THUMB_SIZE, THUMB_SIZE), Image.LANCZOS)
    save_webp(im, dst, quality=THUMB_QUALITY, method=5)


def make_preview(src: Path) -> None:
    dst = PREVIEWS / f"{src.stem}.webp"
    if is_current(dst, src):
        return
    frames, durations = [], []
    with Image.open(src) as im:
        for frame in ImageSequence.Iterator(im):
            frames.append(frame.convert("RGBA"))
            durations.append(frame.info["duration"])
    save_webp(frames[0], dst, save_all=True, append_images=frames[1:], duration=durations, loop=0,
              quality=PREVIEW_QUALITY, method=4)


def main() -> int:
    if not EMOTES_SRC.is_dir():
        sys.exit(f"Emotes folder not found: {EMOTES_SRC}")
    files = emote_files()
    PREVIEWS.mkdir(parents=True, exist_ok=True)
    with ProcessPoolExecutor() as pool:
        list(pool.map(make_still, files))
        list(pool.map(make_preview, files))

    keep = {f"{p.stem}.webp" for p in files}
    for old in (*THUMBS.glob("*.webp"), *PREVIEWS.glob("*.webp")):
        if old.name not in keep:
            old.unlink()

    releases = DATA / "releases.json"
    rel = json.loads(releases.read_text()) if releases.exists() else {}
    items = []
    for p in files:
        item = {"f": p.name}
        if entry := rel.get("assets", {}).get(p.name):
            item["u"] = f"{entry['tag']}/{entry['name']}"
        items.append(item)
    data = {"folder": EMOTES_DIR, "items": items}
    if rel.get("repo"):
        data["releaseBase"] = f"https://github.com/{rel['repo']}/releases/download/"
    tmp = MANIFEST.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(data, separators=(",", ":")))
    os.replace(tmp, MANIFEST)
    print(f"{len(items)} emotes, {sum('u' in it for it in items)} with originals on Releases.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
