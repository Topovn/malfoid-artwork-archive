#!/usr/bin/env python3
"""Build the anonymized early-art set for the Timeline view.

Reads the works listed in data/early_years.json from Malfoid/_Early Arts.
The handles in those filenames belong to reposters, not the artists, so nothing identifying is published:
files get neutral names like 2013-1, are converted to sRGB and re-encoded with no metadata,
and docs/data/early.json carries only years and labels.
These works never enter the main manifest, the gallery, the shelves or the artist list.

Usage:
    python3 tools/build_early.py [--src ~/Downloads/twitter_media_harvest/Malfoid/_Early\\ Arts]
"""

from __future__ import annotations

import argparse
import io
import json
import sys
from pathlib import Path

from PIL import Image, ImageCms, ImageOps

REPO = Path(__file__).resolve().parent.parent
MAPPING = REPO / "data" / "early_years.json"  # private: source filenames and reposter wording
OUT_DIR = REPO / "docs" / "early"
OUT_JSON = REPO / "docs" / "data" / "early.json"
THUMB_WIDTH = 400
FULL_MAX_WIDTH = 1600
QUALITY = {"thumb": 72, "full": 85}
SRGB = ImageCms.createProfile("sRGB")


def clean_rgb(path: Path) -> Image.Image:
    """Open, apply orientation, convert to sRGB, and return a fresh image carrying no metadata."""
    with Image.open(path) as im:
        im = ImageOps.exif_transpose(im)
        icc = im.info.get("icc_profile")
        im = im.convert("RGB")
        if icc:
            src = ImageCms.ImageCmsProfile(io.BytesIO(icc))
            im = ImageCms.profileToProfile(im, src, SRGB, outputMode="RGB")
        fresh = Image.new("RGB", im.size)
        fresh.paste(im)
        return fresh


def save_webp(im: Image.Image, width: int, dest: Path, quality: int) -> tuple[int, int]:
    if im.width > width:
        im = im.resize((width, round(im.height * width / im.width)), Image.LANCZOS)
    tmp = dest.with_name(dest.name + ".tmp")
    im.save(tmp, "WEBP", quality=quality, method=6)  # no exif/icc/xmp arguments, so none are written
    tmp.replace(dest)
    return im.width, im.height


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--src", default=str(Path.home() / "Downloads/twitter_media_harvest/Malfoid/_Early Arts"))
    args = ap.parse_args()
    src = Path(args.src).expanduser()
    works = json.loads(MAPPING.read_text())["works"]

    missing = [f for f in works if not (src / f).is_file()]
    if missing:
        sys.exit(f"Missing in {src}: {missing}")

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    entries, counters, wanted = [], {}, set()
    for f in sorted(works, key=lambda f: (works[f]["year"], f)):
        meta = works[f]
        counters[meta["year"]] = counters.get(meta["year"], 0) + 1
        wid = f"{meta['year']}-{counters[meta['year']]}"
        im = clean_rgb(src / f)
        thumb, full = OUT_DIR / f"{wid}.webp", OUT_DIR / f"{wid}-full.webp"
        w, h = save_webp(im, THUMB_WIDTH, thumb, QUALITY["thumb"])
        fw, fh = save_webp(im, FULL_MAX_WIDTH, full, QUALITY["full"])
        wanted |= {thumb.name, full.name}
        entries.append({
            "id": wid, "year": meta["year"], "label": meta["label"],
            "thumb": f"early/{thumb.name}", "full": f"early/{full.name}",
            "w": w, "h": h, "fw": fw, "fh": fh,
        })

    for stale in OUT_DIR.iterdir():
        if stale.name not in wanted:
            stale.unlink()
    OUT_JSON.write_text(json.dumps({"early": entries}, indent=1))
    print(f"Wrote {len(entries)} early works to {OUT_DIR.relative_to(REPO)} and {OUT_JSON.relative_to(REPO)}.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
