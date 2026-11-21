#!/usr/bin/env python3
"""Upload full-size originals to GitHub Releases on the gallery repo.

A release holds a limited number of assets, so works are spread over releases named media-001, media-002, ...
Each work is assigned to a release once and keeps that slot forever, recorded in data/releases.json.
Every run compares that record with what GitHub actually holds and uploads only what is missing,
so adding new works later is one command.
Asset names are generated to be GitHub-safe, and the name GitHub reports back after upload is what gets recorded.

Usage:
    python3 tools/upload_originals.py --dry-run         # show the plan, change nothing
    python3 tools/upload_originals.py --limit 5         # upload at most 5 files (a quick test)
    python3 tools/upload_originals.py --only a.jpg b.mp4  # upload just these files
    python3 tools/upload_originals.py                   # upload everything missing
    python3 tools/upload_originals.py --audience adult  # the same for the 18+ page (Malfoid H/)

Run tools/build_malfoid_gallery.py (with the same --audience) first so the manifest lists the current works.
Both sites share the releases and data/releases.json, so a work moved between them is never uploaded twice.
The manifest is rebuilt at the end so the site points at the uploaded files.
"""

from __future__ import annotations

import argparse
import fcntl
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
MANIFESTS = {"main": ROOT / "docs" / "data" / "manifest.json", "adult": ROOT / "docs" / "h" / "data" / "manifest.json"}
SOURCES = {"main": Path.home() / "Downloads/twitter_media_harvest/Malfoid",
           "adult": Path.home() / "Downloads/twitter_media_harvest/Malfoid H"}
RELEASES = ROOT / "data" / "releases.json"
LOCK = ROOT / "data" / ".upload.lock"  # held while a run is active, so two runs never upload the same files
DEFAULT_REPO = "Topovn/malfoid-artwork-archive"
TAG_PREFIX = "media-"
ASSETS_PER_RELEASE = 900  # GitHub allows 1000; keep headroom
UPLOAD_BATCH = 5
BATCH_PAUSE = 45.0  # seconds between batches: about 400 uploads an hour, under GitHub's content-creation limit
MAX_ASSET_BYTES = 2 * 1024**3
RATE_LIMITED = re.compile(r"rate limit|secondary|abuse|HTTP 403|HTTP 429|HTTP 50[234]", re.I)


def gh(*args: str, check: bool = True, attempts: int = 12) -> subprocess.CompletedProcess:
    """Run gh, waiting out rate limits and transient server errors instead of giving up."""
    wait = 60.0
    for attempt in range(1, attempts + 1):
        r = subprocess.run(["gh", *args], capture_output=True, text=True)
        if r.returncode == 0 or not RATE_LIMITED.search(r.stderr) or attempt == attempts:
            break
        print(f"  GitHub limit hit ({r.stderr.strip().splitlines()[-1][:120]}), waiting {wait:.0f}s...", flush=True)
        time.sleep(wait)
        wait = min(wait * 2, 900)
    if check and r.returncode != 0:
        sys.exit(f"gh {' '.join(args[:3])} failed:\n{r.stderr.strip()}")
    return r


def tag_assets(repo: str, tag: str) -> dict[str, int]:
    v = gh("release", "view", tag, "--repo", repo, "--json", "assets")
    return {a["name"]: a["size"] for a in json.loads(v.stdout)["assets"]}


def safe_asset_name(item: dict) -> str:
    """A name GitHub will not rewrite: starts with a digit, only [A-Za-z0-9._-]."""
    ext = item["x"]
    # Use the handle the file was saved under ("o" when the build shows a renamed artist's current handle),
    # so asset names never change when an artist renames their account.
    saved = item.get("o") or item["a"]
    stem = f"{item['t']}-{item['i']:02d}-{saved}" if item["t"] else f"0-{Path(item['f']).stem}"
    return re.sub(r"[^A-Za-z0-9._-]", "-", stem) + "." + ext


def remote_assets(repo: str) -> dict[str, dict[str, int]]:
    """Map each media-* tag to {asset name: size} as GitHub reports it."""
    r = gh("release", "list", "--repo", repo, "--limit", "1000", "--json", "tagName")
    tags = sorted(t["tagName"] for t in json.loads(r.stdout or "[]") if t["tagName"].startswith(TAG_PREFIX))
    return {tag: tag_assets(repo, tag) for tag in tags}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--audience", choices=("main", "adult"), default="main")
    ap.add_argument("--src", help="main-site source folder (default Malfoid/); the 18+ page always reads Malfoid H/")
    ap.add_argument("--repo", default=DEFAULT_REPO)
    ap.add_argument("--dry-run", action="store_true", help="print the plan without uploading")
    ap.add_argument("--limit", type=int, default=0, help="upload at most this many files")
    ap.add_argument("--only", nargs="+", metavar="FILE", help="restrict the run to these local filenames (for tests)")
    args = ap.parse_args()
    if args.audience == "adult" and args.src:
        sys.exit("The 18+ page always reads Malfoid H/; --src is only for the main site.")
    src = Path(args.src).expanduser() if args.src else SOURCES[args.audience]

    items = json.loads(MANIFESTS[args.audience].read_text())["items"]
    record = json.loads(RELEASES.read_text()) if RELEASES.exists() else {"repo": args.repo, "assets": {}}
    if record.get("repo") != args.repo:
        sys.exit(f"data/releases.json belongs to {record.get('repo')}, not {args.repo}. Asset URLs include the repo name.")
    assets: dict[str, dict] = record["assets"]  # local filename -> {"tag", "name"}

    remote = remote_assets(args.repo)

    def save_confirmed() -> None:
        """Write only files GitHub actually holds, so the site never links to a missing original."""
        confirmed = {f: e for f, e in assets.items() if e["name"] in remote.get(e["tag"], {})}
        out = {"repo": record["repo"], "assets": confirmed}
        tmp = RELEASES.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(out, indent=1, sort_keys=True))
        os.replace(tmp, RELEASES)

    # Drop stale records from an interrupted run before planning.
    save_confirmed()

    # Local files for every work, and their GitHub-safe names.
    local = {}
    only = set(args.only or [])
    if only - {it["f"] for it in items}:
        sys.exit(f"Not in the manifest: {sorted(only - {it['f'] for it in items})}")
    for it in items:
        if only and it["f"] not in only:
            continue
        # Only the main site keeps lost tweets in _LostTweets/; the 18+ page has them at the top level of Malfoid H/.
        path = src / ("_LostTweets" if "l" in it and args.audience == "main" else "") / it["f"]
        if not path.exists():
            print(f"skip, not found locally: {path}")
            continue
        if path.stat().st_size > MAX_ASSET_BYTES:
            print(f"skip, over the 2 GiB asset limit: {path}")
            continue
        local[it["f"]] = (path, safe_asset_name(it))
    names = [n for _, n in local.values()]
    dupes = {n for n in names if names.count(n) > 1}
    if dupes:
        sys.exit(f"Asset name collision, fix safe_asset_name: {sorted(dupes)[:5]}")

    # Work out which files still need uploading, keeping each file's assigned release.
    counts = {tag: len(a) for tag, a in remote.items()}
    for entry in assets.values():
        counts.setdefault(entry["tag"], 0)
    pending: dict[str, list[str]] = {}

    def next_tag() -> str:
        for tag in sorted(counts):
            if counts[tag] < ASSETS_PER_RELEASE:
                return tag
        tag = f"{TAG_PREFIX}{len(counts) + 1:03d}"
        counts[tag] = 0
        return tag

    for f, (path, name) in local.items():
        entry = assets.get(f)
        if entry and entry["name"] in remote.get(entry["tag"], {}):
            continue  # already on GitHub
        if not entry:
            tag = next_tag()
            counts[tag] += 1
            entry = assets[f] = {"tag": tag, "name": name}
        pending.setdefault(entry["tag"], []).append(f)

    total = sum(len(v) for v in pending.values())
    print(f"{len(local)} works, {len(local) - total} already uploaded, {total} to upload.")
    for tag, files in sorted(pending.items()):
        print(f"  {tag}: {len(files)} files{' (new release)' if tag not in remote else ''}")
    if args.dry_run or not total:
        return 0

    budget = args.limit or total
    with tempfile.TemporaryDirectory() as tmp:
        stage = Path(tmp)
        for tag, files in sorted(pending.items()):
            if budget <= 0:
                break
            files = files[:budget]
            budget -= len(files)
            if tag not in remote:
                gh("release", "create", tag, "--repo", args.repo, "--title", f"Media {tag[len(TAG_PREFIX):]}",
                   "--notes", "Full-size originals for the gallery. Managed by tools/upload_originals.py.",
                   "--latest=false")
                remote[tag] = {}
            for start in range(0, len(files), UPLOAD_BATCH):
                batch = files[start:start + UPLOAD_BATCH]
                for attempt in range(3):
                    # After a failure, part of the batch may already be on GitHub; skip those,
                    # and skip files the owner moved or deleted since this run started.
                    todo = [f for f in batch if assets[f]["name"] not in remote[tag] and local[f][0].exists()]
                    if not todo:
                        break
                    paths = []
                    for f in todo:
                        link = stage / assets[f]["name"]
                        link.unlink(missing_ok=True)
                        link.symlink_to(local[f][0])
                        paths.append(str(link))
                    r = gh("release", "upload", tag, "--repo", args.repo, *paths, check=attempt == 2)
                    for p_ in paths:
                        Path(p_).unlink(missing_ok=True)
                    if r.returncode == 0:
                        for f in todo:
                            # The owner may move a file right after it uploads; its size here is only a placeholder.
                            remote[tag][assets[f]["name"]] = local[f][0].stat().st_size if local[f][0].exists() else 0
                        break
                    remote[tag] = tag_assets(args.repo, tag)
                save_confirmed()
                print(f"  {tag}: uploaded {min(start + UPLOAD_BATCH, len(files))}/{len(files)}", flush=True)
                time.sleep(BATCH_PAUSE)

            # Record the names GitHub actually stored, in case it rewrote any.
            got = remote[tag] = tag_assets(args.repo, tag)
            for f in files:
                want = assets[f]["name"]
                if want in got or not local[f][0].exists():
                    continue  # uploaded, or moved away mid-run (retried next run if it comes back)
                size = local[f][0].stat().st_size
                match = [n for n, s in got.items() if s == size and n not in {a["name"] for a in assets.values()}]
                if len(match) == 1:
                    print(f"  GitHub renamed {want} -> {match[0]}")
                    assets[f]["name"] = match[0]
                else:
                    print(f"  WARNING: could not find {want} in {tag} after upload")
            save_confirmed()

    # Only keep records for assets that exist, so a failed upload is retried next run.
    remote = remote_assets(args.repo)
    save_confirmed()
    print(f"Recorded {len(json.loads(RELEASES.read_text())['assets'])} uploaded files in {RELEASES.relative_to(ROOT)}.")

    print("Rebuilding the manifest...")
    build = ["--audience", "adult"] if args.audience == "adult" else ["--src", str(src)]
    subprocess.run([sys.executable, str(ROOT / "tools" / "build_malfoid_gallery.py"), *build], check=True)
    return 0


if __name__ == "__main__":
    if not shutil.which("gh"):
        sys.exit("The GitHub CLI (gh) is required.")
    lock = LOCK.open("w")
    try:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        sys.exit("Another upload_originals.py run is in progress. Let it finish first.")
    sys.exit(main())
