#!/usr/bin/env python3
"""Hide works whose image forbids using or reposting it (a commission watermark, say) from visitors.

Every work of both sites (Malfoid, its _LostTweets, and Malfoid H) and every work waiting in the Workspace sorting
folders (General, Suggestive, Questionable, Explicit, ElysiaLover) is read with Apple's Vision text recognition,
twice: as English, and with the script detected per line, which reads Japanese, Chinese and Korean (and can misread
stylised English, so both readings count). Videos are checked on a few frames.

A work is hidden when its text says
  - "do not use" ("do. not. use", "don't use", "dont use"), unless it only bans AI training ("do not use my art for
    AI training"), which the sites don't hide, as with artists' bios; or
  - not to repost it: "do not reupload / re-upload / repost / reprint / redistribute", "no reposting", "reposting is
    prohibited", 無断転載禁止, 禁止转载, 무단 전재 금지 and the like, unless it allows reposting with credit
    ("do not repost without credit").
Hidden works are listed in data/private/hidden_works.json, so the build keeps them off the public sites the way it keeps
off works by hidden artists: visitors never see them, the owner's full copy (tools/serve.py) shows them marked Hidden.
The text read from each file is cached in data/private/image_notices_scan.json, so reruns only read new or changed
works. A work wrongly flagged can be kept visible with --keep; it stays out of the hidden list on later scans.

Usage:
    python3 tools/detect_image_notices.py                  # read new works and hide every one with such a notice
    python3 tools/detect_image_notices.py --keep FILE ...  # a flagged work is fine: show it again, and keep it shown
    python3 tools/detect_image_notices.py --test IMAGE ... # print what single images say, without touching anything
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import tempfile
import unicodedata
from concurrent.futures import ProcessPoolExecutor, as_completed
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from build_malfoid_gallery import ADULT_SRC, HARVEST_ROOT, IMAGE_EXTS, LOST_DIR, MAIN_SRC, VIDEO_EXTS  # noqa: E402
from detect_blocked import read_text, video_frames  # noqa: E402
from site_paths import HIDDEN_WORKS, PRIVATE, read_hidden_works, write_hidden_works  # noqa: E402

SCAN = PRIVATE / "image_notices_scan.json"
SORTING = HARVEST_ROOT / "Workspace"
SOURCES = [MAIN_SRC, MAIN_SRC / LOST_DIR, ADULT_SRC] + [
    SORTING / f for f in ("General", "Suggestive", "Questionable", "Explicit", "ElysiaLover")]
VERSION = 2  # bump when the reading changes, so every work is read again
READINGS = (False, True)  # read_text's detect_language: as English, then with the script detected

# Text recognition confuses O with 0 and AI with Al, so the patterns allow both.
DO_NOT_USE = re.compile(r"(?<![a-z])d[o0]\W{0,4}n(?:[o0]|['’])?t\W{0,4}use(?![a-z])", re.I)
# "Do not use (my art) for AI (training)" only bans AI training.
AI_ONLY = re.compile(r"^\W*(?:my\s+\w+\W*)?for\W*(?:a[il1]|generative)(?![a-z])", re.I)
NO_REPOST = re.compile("|".join([
    # do not / don't / not / no / never + reupload, repost, reprint, redistribute ("hot" is how "not" is often misread)
    r"(?<![a-z])(?:d[o0]\W{0,4}n['’]?[o0]?t|[nh][o0]t|n[o0]|never)\W{0,4}"
    r"(?:re\W?up\W?l[o0]ad|re\W?p[o0]st|re\W?print|redistribut)",
    # reposting is (strictly) not allowed / prohibited / forbidden
    r"(?<![a-z])re\W?(?:up\W?l[o0]ad|p[o0]st|print)\w{0,3}\W{0,4}(?:is\W{0,3})?(?:strictly\W{0,3})?"
    r"(?:not\W{0,3}allowed|prohibited|forbidden|banned)",
    # Japanese and Chinese: unauthorised reprint (無断転載), reprint prohibited (転載禁止, 禁止转载), no reposting (请勿搬运)
    r"(?:無断|无断|無斷)\W{0,3}(?:転[載裁戴]|转载|轉載|転用|使用|複製|利用)",
    r"(?:転[載裁戴]|转载|轉載)\W{0,3}(?:禁止|厳禁|严禁|嚴禁|不可|ng)",
    r"(?:禁止|严禁|嚴禁|请勿|請勿|不准)\W{0,4}(?:転[載裁戴]|转载|轉載|搬运|搬運|二传|二傳)",
    # Korean: unauthorised reprint, use or theft; reupload or reprint forbidden
    r"무단\s*(?:전재|도용|사용|배포|복제|재업)", r"재업\w{0,4}\s*금지", r"전재\s*금지",
]), re.I)
# "Do not repost without credit" allows reposting with credit.
WITH_CREDIT = re.compile(r"^\W*(?:\w+\W+){0,3}?(?:without|w/?o)\W+(?:\w+\W+)?credit", re.I)
CHECKS = (("do not use", DO_NOT_USE, AI_ONLY), ("no reposting", NO_REPOST, WITH_CREDIT))
REASONS = {reason for reason, _, _ in CHECKS}


def read_file(path: Path) -> list[list[str]]:
    """Each reading of one work, as its lines of text; for a video, the lines of a few frames."""
    if path.suffix.lower().lstrip(".") not in VIDEO_EXTS:
        return [[text for text, _ in read_text(path, d)] for d in READINGS]
    with tempfile.TemporaryDirectory() as tmp:
        frames = video_frames(path, Path(tmp))
        return [[text for frame in frames for text, _ in read_text(frame, d)] for d in READINGS]


def find_notice(readings: list[list[str]]) -> tuple[str, str]:
    """(reason, the text around it) for the first notice any reading shows, or ("", "").

    Each reading's lines are joined in reading order, so a phrase or its ending can run onto the next line.
    """
    for lines in readings:
        text = unicodedata.normalize("NFKC", " ".join(lines))
        for reason, pattern, unless in CHECKS:
            for m in pattern.finditer(text):
                if unless.match(text[m.end():]):
                    continue
                start, end = text.rfind(" ", 0, max(0, m.start() - 60)) + 1, text.find(" ", m.end() + 60)
                return reason, text[start:end if end > 0 else len(text)].strip()
    return "", ""


def read_job(path_str: str) -> tuple[str, dict | None]:
    path = Path(path_str)
    try:
        st = path.stat()
        res = {"readings": read_file(path)}
    except FileNotFoundError:
        return path_str, None  # moved away mid-scan
    except Exception as e:  # a broken file must not stop the whole scan
        res = {"readings": [], "error": f"{type(e).__name__}: {e}"[:200]}
    return path_str, {**res, "v": VERSION, "size": st.st_size, "mtime": int(st.st_mtime)}


def all_works() -> list[Path]:
    exts = IMAGE_EXTS | VIDEO_EXTS
    return [p for folder in SOURCES if folder.is_dir() for p in sorted(folder.iterdir())
            if p.is_file() and not p.name.startswith(".") and p.suffix.lower().lstrip(".") in exts]


def scan(works: list[Path]) -> dict:
    cache = json.loads(SCAN.read_text()) if SCAN.exists() else {}
    todo = []
    for p in works:
        old = cache.get(str(p))
        st = p.stat()
        if not old or old.get("v") != VERSION or old.get("size") != st.st_size or old.get("mtime") != int(st.st_mtime):
            todo.append(str(p))
    print(f"Works: {len(works)}, to read: {len(todo)}", flush=True)

    def save() -> None:
        tmp = SCAN.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(cache, indent=1, sort_keys=True, ensure_ascii=False))
        os.replace(tmp, SCAN)

    if todo:
        with ProcessPoolExecutor(max_workers=max(1, (os.cpu_count() or 4) - 1)) as pool:
            for n, fut in enumerate(as_completed(pool.submit(read_job, p) for p in todo), 1):
                key, res = fut.result()
                if res is not None:
                    cache[key] = res
                if n % 200 == 0 or n == len(todo):
                    print(f"  read {n}/{len(todo)}", flush=True)
                    save()
    live = {str(p) for p in works}
    return {k: v for k, v in cache.items() if k in live}


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--keep", nargs="+", metavar="FILE", help="flagged works that are fine: show them again")
    ap.add_argument("--test", nargs="+", metavar="IMAGE", help="print what single images say")
    args = ap.parse_args()

    if args.test:
        for f in args.test:
            readings = read_file(Path(f).expanduser())
            reason, text = find_notice(readings)
            print(f, "->", f"{reason}: {text}" if reason else "no notice", json.dumps(readings, ensure_ascii=False))
        return 0
    record = read_hidden_works()
    if args.keep:
        names = {Path(f).name for f in args.keep}
        record["kept"] = sorted(set(record["kept"]) | names)
        for n in names:
            record["files"].pop(n, None)
        write_hidden_works(record)
        print(f"Kept visible: {', '.join(sorted(names))}. Rebuild both sites to show them again.")
        return 0

    results = scan(all_works())
    found = {}
    for key, res in results.items():
        reason, text = find_notice(res.get("readings", []))
        name = Path(key).name
        if reason and name not in record["kept"]:
            found[name] = {"reason": reason, "text": text}
    new = sorted(n for n in found if n not in record["files"])
    gone = sorted(n for n, v in record["files"].items() if v.get("reason") in REASONS and n not in found)
    # This scan's findings replace its earlier ones (a file moved between folders keeps its name).
    record["files"] = {n: v for n, v in record["files"].items() if v.get("reason") not in REASONS} | found
    write_hidden_works(record)
    errors = sorted(Path(k).name for k, v in results.items() if v.get("error"))
    counts = {r: sum(1 for v in record["files"].values() if v.get("reason") == r) for r in sorted(REASONS)}
    print("Hidden for their notice:", ", ".join(f'{n} "{r}"' for r, n in counts.items()), f"({len(new)} new)")
    for n in new:
        print(f"  {n}  [{found[n]['reason']}]  \"{found[n]['text']}\"")
    if gone:
        print(f"No longer hidden ({len(gone)}):", *gone, sep="\n  ")
    if errors:
        print(f"Could not read {len(errors)} files:", *errors, sep="\n  ")
    if new or gone:
        print(f"Recorded in {HIDDEN_WORKS.relative_to(HIDDEN_WORKS.parents[2])}; rebuild both sites to apply.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
