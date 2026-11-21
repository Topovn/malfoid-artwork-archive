"""Where each site's files live, shared by the build and the tools around it.

Each site has two copies:
- the public copy in docs/ (the second site in a subfolder), which GitHub Pages publishes, and
- the full copy in data/private/site/, laid out the same, which is gitignored and served only by tools/serve.py.

The full copy holds everything the public one does plus the works of hidden artists: artists whose X bio forbids
reposting or using their art (see tools/fetch_bios.py). Their works are marked "z" in the full manifest, and their
thumbnails live only in the full copy, so visitors never see them. The site's numbers still count them (the manifests'
"totals"). The public copy keeps the shared files (code, styles, icons, My Pick, early works); the full copy holds just
the manifest, post texts and hidden thumbnails, and tools/serve.py fills in the rest from docs/.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data"
PRIVATE = DATA / "private"
BIO_FLAGS = PRIVATE / "bio_flags.json"

PUBLIC_SITES = {"main": ROOT / "docs", "adult": ROOT / "docs" / "h"}
FULL_SITES = {"main": PRIVATE / "site", "adult": PRIVATE / "site" / "h"}


def manifest_path(audience: str, full: bool = False) -> Path:
    return (FULL_SITES if full else PUBLIC_SITES)[audience] / "data" / "manifest.json"


def full_manifests() -> tuple[Path, ...]:
    """Each site's full manifest, or its public one before the first build that wrote a full copy."""
    return tuple(
        manifest_path(a, full=True) if manifest_path(a, full=True).exists() else manifest_path(a)
        for a in PUBLIC_SITES
    )


def hidden_files() -> set[str]:
    """Filenames of the works by hidden artists, as marked in the full manifests."""
    return {
        it["f"]
        for audience in FULL_SITES
        if (path := manifest_path(audience, full=True)).exists()
        for it in json.loads(path.read_text())["items"]
        if it.get("z")
    }


def hidden_handles() -> set[str]:
    """Lowercased handles of the artists kept off the public sites.

    Refuses to go on without the review file, so a build on a machine without it can never publish them.
    """
    if not BIO_FLAGS.exists():
        sys.exit(f"{BIO_FLAGS.relative_to(ROOT)} is missing, so hidden artists can't be kept off the public sites. "
                 "Run tools/fetch_bios.py and review its bios first.")
    return {k.lower() for k, v in json.loads(BIO_FLAGS.read_text()).items() if v.get("flag")}
