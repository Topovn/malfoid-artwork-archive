#!/usr/bin/env python3
"""Compare the Malfoid folder with the gallery, and record when it was last synced.

The gallery is in sync when every work in the folder has a thumbnail in docs/data/manifest.json
and a full-size original on GitHub Releases (data/releases.json).
The last sync time lives in data/last_sync.json and is committed with each sync,
so the next run can say what was added to the folder since then.

Usage:
    python3 tools/sync_status.py                       # report; prints the scan time to pass to --mark
    python3 tools/sync_status.py --mark --at <ISO>     # record a finished sync that scanned at <ISO>

--mark refuses while works are still missing from the site or from Releases, so the date only moves
once a sync really finished. --force records it anyway.
The comparison itself is by filename, so a file with an old timestamp is never missed;
the date is the reference point for "added since last sync".
"""

from __future__ import annotations

import argparse
import fcntl
import json
import os
import subprocess
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from build_malfoid_gallery import DATA, DEFAULT_SITE, MAIN_SRC, added_ms, source_files  # noqa: E402
from upload_originals import LOCK, MAX_ASSET_BYTES  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
LAST_SYNC = DATA / "last_sync.json"
MANIFEST = DEFAULT_SITE / "data" / "manifest.json"  # the main site only
RELEASES = DATA / "releases.json"
SHOW = 10


def now_iso() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def last_sync() -> tuple[datetime | None, str]:
    """When the last sync scanned the folder, and where that date came from."""
    if LAST_SYNC.exists():
        return datetime.fromisoformat(json.loads(LAST_SYNC.read_text())["synced_at"]), "data/last_sync.json"
    # Before the first marked sync, the last commit of the manifest is the best estimate.
    r = subprocess.run(["git", "-C", str(ROOT), "log", "-1", "--format=%cI", "--", str(MANIFEST)],
                       capture_output=True, text=True)
    if r.stdout.strip():
        return datetime.fromisoformat(r.stdout.strip()), "last manifest commit (no sync recorded yet)"
    return None, "never synced"


def upload_running() -> bool:
    with LOCK.open("a") as fh:
        try:
            fcntl.flock(fh, fcntl.LOCK_EX | fcntl.LOCK_NB)
            fcntl.flock(fh, fcntl.LOCK_UN)
        except BlockingIOError:
            return True
    # Runs started before the lock existed don't hold it.
    return subprocess.run(["pgrep", "-f", "tools/upload_originals.py"], capture_output=True).returncode == 0


def scan(src: Path) -> dict:
    files = {p.name: p for p, _ in source_files(src)}
    on_site = {it["f"] for it in json.loads(MANIFEST.read_text())["items"]} if MANIFEST.exists() else set()
    uploaded = set(json.loads(RELEASES.read_text())["assets"]) if RELEASES.exists() else set()
    too_big = {f for f, p in files.items() if p.stat().st_size > MAX_ASSET_BYTES}
    return {
        "files": files,
        "missing_from_site": sorted(set(files) - on_site),
        "gone_from_folder": sorted(on_site - set(files)),
        "not_uploaded": sorted(set(files) - uploaded - too_big),
        "too_big": sorted(too_big),
    }


def listing(label: str, names: list[str]) -> None:
    print(f"{label}: {len(names)}")
    for n in names[:SHOW]:
        print(f"  {n}")
    if len(names) > SHOW:
        print(f"  ... and {len(names) - SHOW} more")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--src", default=str(MAIN_SRC))
    ap.add_argument("--mark", action="store_true", help="record a finished sync")
    ap.add_argument("--at", help="scan time printed by the report at the start of the sync (ISO 8601)")
    ap.add_argument("--force", action="store_true", help="with --mark, record even if works are still pending")
    args = ap.parse_args()
    src = Path(args.src).expanduser()
    if not src.is_dir():
        sys.exit(f"Source folder not found: {src}")

    scan_time = now_iso()
    s = scan(src)
    pending = bool(s["missing_from_site"] or s["gone_from_folder"] or s["not_uploaded"])

    if args.mark:
        if not args.at:
            sys.exit("--mark needs --at <scan time from the report at the start of this sync>.")
        at = datetime.fromisoformat(args.at)
        if pending and not args.force:
            listing("Not on the site yet", s["missing_from_site"])
            listing("On the site but gone from the folder", s["gone_from_folder"])
            listing("Originals not on GitHub Releases yet", s["not_uploaded"])
            sys.exit("Sync is not finished, so the date was not recorded. Use --force to record it anyway.")
        tmp = LAST_SYNC.with_suffix(".json.tmp")
        tmp.write_text(json.dumps({"synced_at": at.isoformat(timespec="seconds"), "works": len(s["files"])},
                                  indent=1) + "\n")
        os.replace(tmp, LAST_SYNC)
        print(f"Recorded sync at {at.isoformat(timespec='seconds')} ({len(s['files'])} works) in data/last_sync.json.")
        return 0

    since, origin = last_sync()
    print(f"Scan time: {scan_time}")
    print(f"Last sync: {since.isoformat(timespec='seconds') if since else 'never'} ({origin})")
    print(f"Works in folder: {len(s['files'])}")
    if since:
        cutoff = since.timestamp() * 1000
        added = sorted(n for n, p in s["files"].items() if added_ms(p) > cutoff)
        listing("Added to the folder since last sync", added)
    listing("Not on the site yet", s["missing_from_site"])
    listing("On the site but gone from the folder", s["gone_from_folder"])
    listing("Originals not on GitHub Releases yet", s["not_uploaded"])
    if s["too_big"]:
        listing("Over the 2 GiB release asset limit (stay local)", s["too_big"])
    print(f"Upload running: {'yes' if upload_running() else 'no'}")
    print("Status: " + ("sync needed" if pending else "up to date"))
    return 0


if __name__ == "__main__":
    sys.exit(main())
