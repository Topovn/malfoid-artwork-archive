#!/usr/bin/env python3
"""Delete the full-size originals of AI works and blocked artists from GitHub Releases.

Works the owner moves into 'Malfoid AI/', and works by artists on the block list (see tools/detect_blocked.py),
drop off both sites at the next build, but their originals stay on Releases, reachable by direct link.
This deletes every release asset whose local file now sits in 'Malfoid AI/' or whose artist is blocked, and
removes it from data/releases.json. Matching is by the filename the work was saved under.
Deletion cannot be undone; if a work is moved back or its artist unblocked, the next upload_originals.py run
uploads it again.

Usage:
    python3 tools/prune_originals.py --dry-run   # list what would be deleted
    python3 tools/prune_originals.py             # delete it
"""

from __future__ import annotations

import argparse
import fcntl
import json
import os
import shutil
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from build_malfoid_gallery import AI_SRC, is_blocked  # noqa: E402
from site_paths import blocked_handles  # noqa: E402
from upload_originals import DEFAULT_REPO, LOCK, RELEASES, gh  # noqa: E402


def prunable_assets() -> dict[str, dict]:
    """Release records ({local filename: {"tag", "name"}}) of works now in Malfoid AI/ or by blocked artists."""
    if not RELEASES.exists():
        return {}
    ai_files = {p.name for p in AI_SRC.iterdir() if p.is_file()} if AI_SRC.is_dir() else set()
    blocked = blocked_handles()
    assets = json.loads(RELEASES.read_text())["assets"]
    return {f: e for f, e in sorted(assets.items()) if f in ai_files or is_blocked(f, blocked)}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true", help="list the originals without deleting them")
    args = ap.parse_args()

    found = prunable_assets()
    print(f"AI or blocked works with originals on Releases: {len(found)}")
    for f, e in found.items():
        print(f"  {e['tag']}/{e['name']}")
    if args.dry_run or not found:
        return 0

    record = json.loads(RELEASES.read_text())
    repo = record.get("repo", DEFAULT_REPO)
    failed = []
    for f, e in found.items():
        r = gh("release", "delete-asset", e["tag"], e["name"], "--repo", repo, "-y", check=False)
        # An asset GitHub no longer has is as good as deleted.
        if r.returncode == 0 or "not found" in r.stderr.lower():
            record["assets"].pop(f, None)
        else:
            failed.append(f"{e['tag']}/{e['name']}: {r.stderr.strip()[:120]}")
    tmp = RELEASES.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(record, indent=1, sort_keys=True))
    os.replace(tmp, RELEASES)
    print(f"Deleted {len(found) - len(failed)} originals.")
    for line in failed:
        print(f"  FAILED {line}")
    return 1 if failed else 0


if __name__ == "__main__":
    if not shutil.which("gh"):
        sys.exit("The GitHub CLI (gh) is required.")
    lock = LOCK.open("w")
    try:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        sys.exit("An upload_originals.py run is in progress. Let it finish first.")
    sys.exit(main())
