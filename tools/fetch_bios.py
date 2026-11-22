#!/usr/bin/env python3
"""Fetch every gallery artist's X bio and export a private CSV marking bios that forbid reproduction or use.

Bios come from the same FxTwitter profile endpoint as tools/fetch_followers.py and are cached in
data/private/bios.json. Everything under data/private/ is gitignored and never published.

A bio is flagged when it forbids unauthorized reproduction, reposting or use of the artist's work (AI training
included), in any language. Bios are first screened with keyword patterns, then each candidate is reviewed by hand
and the decision recorded in data/private/bio_flags.json as {handle: {"flag": bool, "kind": str, "translation": str}}.
Kinds: "forbids" (reposting, reproduction or use without permission is forbidden; flagged), "ai-only" (only AI
training or AI use is forbidden), "credit" (reposting is allowed with credit or for non-commercial use), "none",
"owner" (hidden by the owner's choice, whatever the bio says; flagged).
Reviewed decisions win over the keyword screen; unreviewed candidates are listed at the end of a run.

The CSV (data/private/artist_bios.csv) has one row per artist:
    handle, raw bio, English translation (flagged bios only), "X" when flagged.

Usage:
    python3 tools/fetch_bios.py                   # fetch artists never fetched or older than 30 days, then export
    python3 tools/fetch_bios.py --max-age-days 0  # refetch everything
    python3 tools/fetch_bios.py --export-only     # rebuild the CSV from the cache
"""

from __future__ import annotations

import argparse
import csv
import json
import re
import sys
import time
import unicodedata
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from fetch_followers import HANDLE_RE, MANIFESTS, WORKERS, fetch_profile  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
PRIVATE = ROOT / "data" / "private"
BIOS = PRIVATE / "bios.json"
FLAGS = PRIVATE / "bio_flags.json"
CSV_OUT = PRIVATE / "artist_bios.csv"

# Screening patterns, matched against the NFKC-normalised, casefolded bio. Broad on purpose: false positives are
# weeded out in review, so a missed notice is the worse failure.
SCREEN = re.compile("|".join([
    # Japanese
    r"無断", r"転載", r"転用", r"複製", r"二次配布", r"再配布", r"自作発言", r"使用禁止", r"利用禁止", r"加工禁止",
    r"ai学習", r"aiへの", r"ai利用", r"ai使用", r"学習禁止", r"ご遠慮", r"不正利用", r"保存", r"利用", r"使用",
    # Chinese
    r"无断", r"無斷", r"转载", r"轉載", r"搬运", r"搬運", r"盗用", r"盜用", r"二改", r"二传", r"二傳", r"商用",
    r"未经", r"未經", r"授权", r"授權", r"禁止", r"严禁", r"嚴禁", r"请勿", r"請勿", r"勿", r"喂ai",
    # Korean
    r"무단", r"재업", r"도용", r"2차\s*가공", r"가공", r"전재", r"금지", r"사용\s*불가",
    # English
    r"re-?post", r"re-?upload", r"re-?print", r"unauthori[sz]ed", r"without (?:my )?(?:permission|consent|credit)",
    r"\bdo ?n[o']?t (?:use|edit|trace|steal|copy|take|share|feed|train)", r"\bno (?:use|editing|tracing|ai|copy)",
    r"not allowed", r"prohibit", r"forbid", r"\bai\b.{0,20}(?:train|learn|use|feed|input)", r"\bdnr\b",
    r"\bdni\b", r"steal", r"permission", r"\bdub", r"🔁\s*[❌✖✕×🚫]",
    # Spanish / Portuguese / French / German / Italian
    r"resubir", r"prohibid", r"proibid", r"sin (?:permiso|autorizaci)", r"sem (?:permiss|autoriza)",
    r"sans (?:autorisation|permission)", r"interdit", r"ohne (?:erlaubnis|genehmigung)", r"verboten", r"vietat",
    # Russian / Ukrainian
    r"без разрешени", r"запрещ", r"заборон",
    # Thai / Vietnamese / Indonesian / Malay / Tagalog
    r"ห้าม", r"không được", r"\bcấm\b", r"dilarang", r"tanpa izin", r"\bbawal\b",
]))


def normalise(text: str) -> str:
    return unicodedata.normalize("NFKC", text).casefold()


def load(path: Path) -> dict:
    return json.loads(path.read_text()) if path.exists() else {}


def save(path: Path, data: dict) -> None:
    path.write_text(json.dumps(data, ensure_ascii=False, indent=1, sort_keys=True))


def fetch_all(handles: dict[str, str], cache: dict[str, dict], max_age_days: float) -> None:
    cutoff = time.time() - max_age_days * 86400
    todo = [h for k, h in sorted(handles.items())
            if k not in cache or cache[k]["at"] < cutoff or cache[k].get("status") != "ok"]
    print(f"{len(handles)} artists, {len(handles) - len(todo)} cached, {len(todo)} to fetch.", flush=True)
    counts: dict[str, int] = {}
    with ThreadPoolExecutor(WORKERS) as pool:
        for done, (key, user, status) in enumerate(pool.map(fetch_profile, todo), 1):
            # Keep the last known bio if an account has since disappeared.
            bio = user.get("description", "") if user else cache.get(key, {}).get("bio", "")
            cache[key] = {"handle": user["screen_name"] if user else handles[key], "bio": bio,
                          "status": status, "at": int(time.time())}
            counts[status] = counts.get(status, 0) + 1
            if done % 250 == 0:
                save(BIOS, cache)
                print(f"  {done}/{len(todo)} {counts}", flush=True)
    save(BIOS, cache)
    print(f"Fetched: {counts}")


def export(handles: dict[str, str], cache: dict[str, dict]) -> None:
    flags = load(FLAGS)
    unreviewed = []
    flagged = 0
    with CSV_OUT.open("w", newline="", encoding="utf-8-sig") as f:  # BOM so spreadsheet apps read it as UTF-8
        w = csv.writer(f)
        w.writerow(["Artist handle", "Bio (raw)", "Bio (translated)", "No unauthorized use"])
        for key in sorted(handles):
            entry = cache.get(key, {})
            bio = entry.get("bio", "")
            review = flags.get(key)
            if review is None and bio and SCREEN.search(normalise(bio)):
                unreviewed.append(key)
            flag = bool(review and review["flag"])
            flagged += flag
            w.writerow([entry.get("handle", handles[key]), bio, review["translation"] if flag else "",
                        "X" if flag else ""])
    print(f"Wrote {CSV_OUT.relative_to(ROOT)}: {len(handles)} artists, {flagged} flagged.")
    if unreviewed:
        print(f"{len(unreviewed)} screened bios still need review in {FLAGS.relative_to(ROOT)}:")
        for key in unreviewed:
            print(f"  @{key}: {cache[key]['bio']!r}")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--max-age-days", type=float, default=30)
    ap.add_argument("--export-only", action="store_true")
    args = ap.parse_args()

    PRIVATE.mkdir(parents=True, exist_ok=True)
    handles: dict[str, str] = {}
    for m in MANIFESTS:
        if m.exists():
            for it in json.loads(m.read_text())["items"]:
                if HANDLE_RE.match(it["a"]):
                    handles.setdefault(it["a"].lower(), it["a"])
    cache = load(BIOS)
    if not args.export_only:
        fetch_all(handles, cache, args.max_age_days)
    export(handles, cache)
    return 0


if __name__ == "__main__":
    sys.exit(main())
