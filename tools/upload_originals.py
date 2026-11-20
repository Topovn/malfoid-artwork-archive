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

Run tools/build_malfoid_gallery.py first so data/manifest.json lists the current works.
The manifest is rebuilt at the end so the site points at the uploaded files.
"""

from __future__ import annotations

import argparse
import json
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
MANIFEST = ROOT / "data" / "manifest.json"
RELEASES = ROOT / "data" / "releases.json"
DEFAULT_REPO = "Topovn/malfoid-gallery"
TAG_PREFIX = "media-"
ASSETS_PER_RELEASE = 900  # GitHub allows 1000; keep headroom
UPLOAD_BATCH = 25
MAX_ASSET_BYTES = 2 * 1024**3


def gh(*args: str, check: bool = True) -> subprocess.CompletedProcess:
    r = subprocess.run(["gh", *args], capture_output=True, text=True)
    if check and r.returncode != 0:
        sys.exit(f"gh {' '.join(args[:3])} failed:\n{r.stderr.strip()}")
    return r


def safe_asset_name(item: dict) -> str:
    """A name GitHub will not rewrite: starts with a digit, only [A-Za-z0-9._-]."""
    ext = item["x"]
    stem = f"{item['t']}-{item['i']:02d}-{item['a']}" if item["t"] else f"0-{Path(item['f']).stem}"
    return re.sub(r"[^A-Za-z0-9._-]", "-", stem) + "." + ext


def remote_assets(repo: str) -> dict[str, dict[str, int]]:
    """Map each media-* tag to {asset name: size} as GitHub reports it."""
    r = gh("release", "list", "--repo", repo, "--limit", "1000", "--json", "tagName")
    tags = sorted(t["tagName"] for t in json.loads(r.stdout or "[]") if t["tagName"].startswith(TAG_PREFIX))
    out = {}
    for tag in tags:
        v = gh("release", "view", tag, "--repo", repo, "--json", "assets")
        out[tag] = {a["name"]: a["size"] for a in json.loads(v.stdout)["assets"]}
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--src", default=str(Path.home() / "Downloads/twitter_media_harvest/Malfoid"))
    ap.add_argument("--repo", default=DEFAULT_REPO)
    ap.add_argument("--dry-run", action="store_true", help="print the plan without uploading")
    ap.add_argument("--limit", type=int, default=0, help="upload at most this many files")
    ap.add_argument("--only", nargs="+", metavar="FILE", help="restrict the run to these local filenames (for tests)")
    args = ap.parse_args()
    src = Path(args.src).expanduser()

    items = json.loads(MANIFEST.read_text())["items"]
    record = json.loads(RELEASES.read_text()) if RELEASES.exists() else {"repo": args.repo, "assets": {}}
    if record.get("repo") != args.repo:
        sys.exit(f"data/releases.json belongs to {record.get('repo')}, not {args.repo}. Asset URLs include the repo name.")
    assets: dict[str, dict] = record["assets"]  # local filename -> {"tag", "name"}

    remote = remote_assets(args.repo)

    # Local files for every work, and their GitHub-safe names.
    local = {}
    only = set(args.only or [])
    if only - {it["f"] for it in items}:
        sys.exit(f"Not in the manifest: {sorted(only - {it['f'] for it in items})}")
    for it in items:
        if only and it["f"] not in only:
            continue
        path = src / ("_LostTweets" if "l" in it else "") / it["f"]
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
                paths = []
                for f in batch:
                    link = stage / assets[f]["name"]
                    link.unlink(missing_ok=True)
                    link.symlink_to(local[f][0])
                    paths.append(str(link))
                gh("release", "upload", tag, "--repo", args.repo, *paths)
                for p in paths:
                    Path(p).unlink()
                print(f"  {tag}: uploaded {min(start + UPLOAD_BATCH, len(files))}/{len(files)}", flush=True)

            # Record the names GitHub actually stored, in case it rewrote any.
            got = remote_assets(args.repo)[tag]
            for f in files:
                want = assets[f]["name"]
                if want in got:
                    continue
                size = local[f][0].stat().st_size
                match = [n for n, s in got.items() if s == size and n not in {a["name"] for a in assets.values()}]
                if len(match) == 1:
                    print(f"  GitHub renamed {want} -> {match[0]}")
                    assets[f]["name"] = match[0]
                else:
                    print(f"  WARNING: could not find {want} in {tag} after upload")
            RELEASES.write_text(json.dumps(record, indent=1, sort_keys=True))

    # Only keep records for assets that exist, so a failed upload is retried next run.
    final = remote_assets(args.repo)
    record["assets"] = {f: e for f, e in assets.items() if e["name"] in final.get(e["tag"], {})}
    RELEASES.write_text(json.dumps(record, indent=1, sort_keys=True))
    print(f"Recorded {len(record['assets'])} uploaded files in {RELEASES.relative_to(ROOT)}.")

    print("Rebuilding the manifest...")
    subprocess.run([sys.executable, str(ROOT / "tools" / "build_malfoid_gallery.py"), "--src", str(src)], check=True)
    return 0


if __name__ == "__main__":
    if not shutil.which("gh"):
        sys.exit("The GitHub CLI (gh) is required.")
    sys.exit(main())
