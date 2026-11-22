#!/usr/bin/env python3
"""Move the works marked H in the owner's admin view from Malfoid/ to Malfoid H/, then rebuild both sites.

The H button under each card's heart (shown only by tools/serve.py) saves its marks in data/private/h_marks.json.
This moves each marked file from the top level of Malfoid/ or from Malfoid/_LostTweets/ to the top level of
Malfoid H/, which is where the 18+ build reads. A lost tweet stays a lost tweet there, since the 18+ build also reads
the main folder's lost-tweet log. Originals on GitHub Releases are shared by both sites, so nothing is uploaded again.
Moved works leave the marks file; a mark whose file can't be moved stays, and the reason is reported.

Usage:
    python3 tools/move_h_marked.py --dry-run   # show what would move
    python3 tools/move_h_marked.py             # move, then rebuild both sites
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
HMARKS = ROOT / "data" / "private" / "h_marks.json"
HARVEST = Path.home() / "Downloads/twitter_media_harvest"
MAIN_SRC = HARVEST / "Malfoid"
ADULT_SRC = HARVEST / "Malfoid H"
LOST_DIR = "_LostTweets"
BUILD = ROOT / "tools" / "build_malfoid_gallery.py"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    marks = json.loads(HMARKS.read_text()).get("marks", []) if HMARKS.exists() else []
    if not marks:
        print("No works are marked H.")
        return 0
    if not ADULT_SRC.is_dir():
        sys.exit(f"{ADULT_SRC} not found.")

    moved, kept = [], []
    for name in marks:
        if "/" in name or name.startswith("."):
            kept.append((name, "not a gallery filename"))
            continue
        dest = ADULT_SRC / name
        sources = [p for p in (MAIN_SRC / name, MAIN_SRC / LOST_DIR / name) if p.is_file()]
        if not sources:
            if dest.is_file():
                moved.append((name, "already in Malfoid H/"))
            else:
                kept.append((name, "not found in Malfoid/ or Malfoid/_LostTweets/"))
            continue
        if len(sources) > 1:
            kept.append((name, "in both Malfoid/ and Malfoid/_LostTweets/"))
            continue
        if dest.exists():
            kept.append((name, "a file with this name is already in Malfoid H/"))
            continue
        src = sources[0]
        where = f"{LOST_DIR}/" if src.parent.name == LOST_DIR else ""
        if not args.dry_run:
            os.rename(src, dest)  # same disk: keeps the file's dates, so it stays "added" when it was
        moved.append((name, f"from Malfoid/{where}"))

    verb = "Would move" if args.dry_run else "Moved"
    print(f"{verb} {len(moved)} of {len(marks)} marked works to Malfoid H/:")
    for name, how in moved:
        print(f"  {name}  ({how})")
    if kept:
        print(f"Kept {len(kept)} marks:")
        for name, why in kept:
            print(f"  {name}: {why}")
    if args.dry_run or not moved:
        return 0

    done = {name for name, _ in moved}
    HMARKS.write_text(json.dumps({"marks": [m for m in marks if m not in done]}, indent=1) + "\n")
    for extra in ([], ["--audience", "adult"]):
        print(f"Rebuilding {'the 18+ site' if extra else 'the main site'}...", flush=True)
        subprocess.run([sys.executable, str(BUILD), *extra], check=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
