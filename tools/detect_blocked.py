#!/usr/bin/env python3
"""Find works that may belong on the block list, for the owner to review.

Every work of both sites (Malfoid, its _LostTweets, and Malfoid H) is scanned for two signs:
  - a trans pride flag (light blue, pink, white, pink, light blue stripes, as on a pin or badge), found by
    walking each pixel column and row of the image for that stripe sequence;
  - a blocked word ("Galfoy") anywhere in the art, read with Apple's Vision text recognition.
Videos are checked on a few frames. The results are cached per file in data/private/blocklist_scan.json, so
reruns only scan new or changed works.

Nothing is blocked automatically. The run writes a review page (data/private/blocklist_review.html) with every
flagged work, the match boxed, grouped by artist. Block an artist with --block, and clear works reviewed as fine
with --clear so they stop showing up. Both are kept in data/private/blocklist.json; the build leaves the works of
every artist listed there out of both sites entirely (see tools/site_paths.py).

Usage:
    python3 tools/detect_blocked.py                     # scan new works, then write and open the review page
    python3 tools/detect_blocked.py --block HANDLE ...  # add artists to the block list
    python3 tools/detect_blocked.py --clear FILE ...    # mark flagged works as reviewed and fine
    python3 tools/detect_blocked.py --test IMAGE ...    # check single images without touching the cache
"""

from __future__ import annotations

import argparse
import html
import json
import os
import re
import subprocess
import sys
import tempfile
from concurrent.futures import ProcessPoolExecutor, as_completed
from pathlib import Path

import numpy as np
from PIL import Image, ImageOps

sys.path.insert(0, str(Path(__file__).resolve().parent))
from build_malfoid_gallery import ADULT_SRC, IMAGE_EXTS, LOST_DIR, MAIN_SRC, VIDEO_EXTS, work_handles  # noqa: E402
from site_paths import BLOCKLIST, PRIVATE, blocked_handles, read_blocklist, write_blocklist  # noqa: E402

SCAN = PRIVATE / "blocklist_scan.json"
REVIEW = PRIVATE / "blocklist_review.html"
FRAMES = PRIVATE / "blocklist_frames"  # stills of flagged videos, for the review page
SOURCES = [MAIN_SRC, MAIN_SRC / LOST_DIR, ADULT_SRC]
VERSION = 1  # bump when the detection changes, so every work is scanned again

# Blocked words, with the letters text recognition commonly confuses (G/C/6, L/I/1, O/0). Kept that strict,
# because "Malfoy", in countless works, is a single letter away from "Galfoy".
WORDS = re.compile(r"(?<![a-z])[gc6][a4][li1|]f[o0]y", re.I)
SCAN_SIDE = 1200  # the stripe scan runs on the image fitted inside this many pixels
VIDEO_POINTS = (0.1, 0.5, 0.9)  # fractions of a video's length to check

# Stripe colours in OpenCV-style HSV (hue 0-179, saturation and value 0-255), loose enough for shading.
BLUE, PINK, WHITE, OTHER = 1, 2, 3, 0
FLAG = (BLUE, PINK, WHITE, PINK, BLUE)
MIN_STRIPE = 3  # pixels, at scan size
MAX_STRIPE_RATIO = 3.0  # longest stripe over shortest along one line
MIN_LINES = 4  # neighbouring columns (or rows) that must cross the whole flag


def classify(rgb: np.ndarray) -> np.ndarray:
    """Label each pixel BLUE, PINK, WHITE or OTHER."""
    import cv2

    hsv = cv2.cvtColor(rgb, cv2.COLOR_RGB2HSV)
    h, s, v = (hsv[..., i].astype(np.int16) for i in range(3))
    out = np.zeros(h.shape, np.uint8)
    out[(h >= 88) & (h <= 108) & (s >= 50) & (s <= 235) & (v >= 130)] = BLUE
    out[((h >= 160) | (h <= 6)) & (s >= 30) & (s <= 170) & (v >= 130)] = PINK
    out[(s <= 40) & (v >= 165)] = WHITE
    return out


def line_hits(labels: np.ndarray) -> list[tuple[int, int, int]]:
    """(line, start, end) of every full flag crossing along each row of `labels`."""
    hits = []
    for i, row in enumerate(labels):
        change = np.flatnonzero(np.diff(row)) + 1
        starts = np.concatenate(([0], change))
        ends = np.concatenate((change, [len(row)]))
        # Drop the thin runs anti-aliasing leaves between stripes, then merge what touches.
        runs: list[list[int]] = []
        for a, b in zip(starts, ends):
            lab = int(row[a])
            if b - a < MIN_STRIPE and lab != OTHER:
                continue
            if b - a <= 2:
                continue
            if runs and runs[-1][0] == lab:
                runs[-1][2] = b
            else:
                runs.append([lab, a, b])
        for k in range(len(runs) - len(FLAG) + 1):
            window = runs[k:k + len(FLAG)]
            if tuple(r[0] for r in window) != FLAG:
                continue
            if any(window[j + 1][1] - window[j][2] > 2 for j in range(len(FLAG) - 1)):
                continue  # something else sits between two stripes
            sizes = [r[2] - r[1] for r in window]
            if min(sizes) >= MIN_STRIPE and max(sizes) / min(sizes) <= MAX_STRIPE_RATIO:
                hits.append((i, window[0][1], window[-1][2]))
    return hits


def group(hits: list[tuple[int, int, int]]) -> list[tuple[int, int, int, int]]:
    """Boxes (line0, line1, start, end) of runs of neighbouring lines that cross the same flag."""
    boxes: list[list[int]] = []
    for line, a, b in sorted(hits):
        for box in boxes:
            if line - box[1] <= 2 and a < box[3] and b > box[2]:
                box[1], box[2], box[3] = line, min(box[2], a), max(box[3], b)
                box[4] += 1
                break
        else:
            boxes.append([line, line, a, b, 1])
    return [(l0, l1, a, b) for l0, l1, a, b, n in boxes if n >= MIN_LINES]


def find_flags(img: Image.Image) -> list[list[float]]:
    """Boxes [x0, y0, x1, y1] (fractions of the image) around trans flags."""
    img = ImageOps.exif_transpose(img).convert("RGB")
    img.thumbnail((SCAN_SIDE, SCAN_SIDE))
    labels = classify(np.asarray(img))
    hgt, wid = labels.shape
    boxes = []
    for x0, x1, y0, y1 in group(line_hits(labels.T)):  # columns: horizontal stripes
        boxes.append([x0 / wid, y0 / hgt, (x1 + 1) / wid, y1 / hgt])
    for y0, y1, x0, x1 in group(line_hits(labels)):  # rows: vertical stripes
        boxes.append([x0 / wid, y0 / hgt, x1 / wid, (y1 + 1) / hgt])
    return [[round(c, 4) for c in b] for b in boxes]


def read_text(path: Path, detect_language: bool = False) -> list[tuple[str, list[float]]]:
    """Every line of text Vision finds, with its box [x0, y0, x1, y1] as fractions from the top left.

    By default the text is read as English; detect_language lets Vision pick the script per line instead, which reads
    Japanese, Chinese and Korean but can misread stylised English.
    """
    import Vision
    from Foundation import NSURL

    req = Vision.VNRecognizeTextRequest.alloc().init()
    req.setRecognitionLevel_(Vision.VNRequestTextRecognitionLevelAccurate)
    req.setUsesLanguageCorrection_(False)
    if detect_language:
        req.setAutomaticallyDetectsLanguage_(True)
    handler = Vision.VNImageRequestHandler.alloc().initWithURL_options_(NSURL.fileURLWithPath_(str(path)), None)
    ok, _ = handler.performRequests_error_([req], None)
    lines = []
    for obs in (req.results() or []) if ok else []:
        cand = obs.topCandidates_(1)
        if not cand:
            continue
        r = obs.boundingBox()  # normalised, origin at the bottom left
        x, y, w, h = r.origin.x, r.origin.y, r.size.width, r.size.height
        lines.append((str(cand[0].string()), [round(x, 4), round(1 - y - h, 4), round(x + w, 4), round(1 - y, 4)]))
    return lines


def word_hits(lines: list[tuple[str, list[float]]]) -> list[dict]:
    """Lines that contain a blocked word."""
    return [{"text": text, "box": box} for text, box in lines if WORDS.search(text)]


def scan_still(path: Path) -> dict:
    with Image.open(path) as img:
        img.seek(0)
        flags = find_flags(img.copy())
    return {"flags": flags, "words": word_hits(read_text(path))}


def video_frames(path: Path, out: Path) -> list[Path]:
    r = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", str(path)],
                       capture_output=True, text=True)
    try:
        length = float(r.stdout.strip())
    except ValueError:
        length = 0.0
    frames = []
    for i, at in enumerate(VIDEO_POINTS):
        frame = out / f"{i}.png"
        subprocess.run(["ffmpeg", "-v", "error", "-y", "-ss", f"{length * at:.2f}", "-i", str(path),
                        "-frames:v", "1", str(frame)], capture_output=True)
        if frame.exists():
            frames.append(frame)
    return frames


def scan_file(path: Path) -> dict:
    """Scan one work; videos report the first frame with a match (its index in "frame")."""
    if path.suffix.lower().lstrip(".") not in VIDEO_EXTS:
        return scan_still(path)
    with tempfile.TemporaryDirectory() as tmp:
        frames = video_frames(path, Path(tmp))
        for i, frame in enumerate(frames):
            res = scan_still(frame)
            if res["flags"] or res["words"]:
                FRAMES.mkdir(parents=True, exist_ok=True)
                still = FRAMES / f"{path.name}.jpg"
                Image.open(frame).convert("RGB").save(still, quality=85)
                return {**res, "frame": str(still)}
        return {"flags": [], "words": [], **({} if frames else {"error": "no frames"})}


def scan_job(path_str: str) -> tuple[str, dict | None]:
    """Scan one work for the cache; None when the owner moved it away mid-scan."""
    path = Path(path_str)
    try:
        st = path.stat()
        res = scan_file(path)
    except FileNotFoundError:
        return path_str, None
    except Exception as e:  # a broken file must not stop the whole scan
        res = {"flags": [], "words": [], "error": f"{type(e).__name__}: {e}"[:200]}
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
        try:
            st = p.stat()
        except FileNotFoundError:
            continue
        if not old or old.get("v") != VERSION or old.get("size") != st.st_size or old.get("mtime") != int(st.st_mtime):
            todo.append(str(p))
    print(f"Works: {len(works)}, to scan: {len(todo)}", flush=True)
    if todo:
        with ProcessPoolExecutor(max_workers=max(1, (os.cpu_count() or 4) - 1)) as pool:
            for n, fut in enumerate(as_completed(pool.submit(scan_job, p) for p in todo), 1):
                key, res = fut.result()
                if res is not None:
                    cache[key] = res
                if n % 200 == 0 or n == len(todo):
                    print(f"  scanned {n}/{len(todo)}", flush=True)
                    tmp = SCAN.with_suffix(".json.tmp")
                    tmp.write_text(json.dumps(cache, indent=1, sort_keys=True))
                    os.replace(tmp, SCAN)
    live = {str(p) for p in works if p.exists()}
    return {k: v for k, v in cache.items() if k in live}


def review(results: dict) -> list[tuple[str, Path, dict]]:
    """Flagged works not yet cleared and not by an artist already blocked: (handle, path, result)."""
    blocked, cleared = blocked_handles(), set(read_blocklist().get("cleared", []))
    out = []
    for key, res in results.items():
        p = Path(key)
        if not (res["flags"] or res["words"]) or p.name in cleared:
            continue
        handles = work_handles(p.name)
        if blocked & {h.lower() for h in handles if h}:
            continue
        out.append((handles[1] or handles[0], p, res))
    return sorted(out, key=lambda t: (t[0].lower(), t[1].name))


def write_review(items: list[tuple[str, Path, dict]]) -> None:
    def card(handle: str, p: Path, res: dict) -> str:
        src = Path(res.get("frame") or p).as_uri()
        marks = [("flag", b) for b in res["flags"]] + [("word", w["box"]) for w in res["words"]]
        boxes = "".join(
            f'<i class="{k}" style="left:{b[0]*100:.2f}%;top:{b[1]*100:.2f}%;'
            f'width:{(b[2]-b[0])*100:.2f}%;height:{(b[3]-b[1])*100:.2f}%"></i>' for k, b in marks)
        why = " · ".join(filter(None, ["trans flag" if res["flags"] else "",
                                       ", ".join(f'"{w["text"]}"' for w in res["words"])]))
        return (f'<figure><a href="{p.as_uri()}" target="_blank"><span><img src="{src}" loading="lazy">{boxes}</span></a>'
                f'<figcaption><b>{html.escape(handle)}</b><small>{html.escape(why)}</small>'
                f'<code>{html.escape(p.name)}</code></figcaption></figure>')

    page = f"""<!doctype html><meta charset="utf-8"><title>Block list review</title>
<meta name="viewport" content="width=device-width,initial-scale=1">
<style>
:root{{color-scheme:light dark;--bg:#f6f6f4;--fg:#1b1b1b;--muted:#6b6b6b;--card:#fff}}
@media (prefers-color-scheme:dark){{:root{{--bg:#151515;--fg:#eee;--muted:#999;--card:#222}}}}
body{{margin:0;padding:16px;background:var(--bg);color:var(--fg);font:14px/1.4 -apple-system,system-ui,sans-serif}}
h1{{font-size:18px;margin:0 0 16px}}
main{{display:grid;grid-template-columns:repeat(auto-fill,minmax(260px,1fr));gap:16px}}
figure{{margin:0;background:var(--card);border-radius:8px;overflow:hidden}}
a{{display:block;color:inherit}} span{{position:relative;display:block}}
img{{display:block;width:100%;height:auto}}
i{{position:absolute;border:3px solid #e5242b;border-radius:4px;box-sizing:border-box;margin:-3px;padding:3px}}
i.word{{border-color:#f39c12}}
figcaption{{padding:8px 10px;display:grid;gap:2px}}
small{{color:var(--muted)}} code{{font-size:11px;color:var(--muted);word-break:break-all;user-select:all}}
</style>
<h1>Block list review</h1>
<main>{''.join(card(*t) for t in items) or '<p>Nothing to review.</p>'}</main>
"""
    REVIEW.write_text(page, encoding="utf-8")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--block", nargs="+", metavar="HANDLE", help="add artists to the block list")
    ap.add_argument("--clear", nargs="+", metavar="FILE", help="mark flagged works as reviewed and fine")
    ap.add_argument("--test", nargs="+", metavar="IMAGE", help="scan single images and print the result")
    ap.add_argument("--no-open", action="store_true", help="don't open the review page")
    args = ap.parse_args()

    if args.test:
        for f in args.test:
            print(f, json.dumps(scan_file(Path(f).expanduser())))
        return 0
    if args.block or args.clear:
        record = read_blocklist()
        lower = {h.lower() for h in record["artists"]}
        record["artists"] += [h.lstrip("@") for h in args.block or [] if h.lstrip("@").lower() not in lower]
        record["cleared"] = sorted(set(record.get("cleared", [])) | {Path(f).name for f in args.clear or []})
        write_blocklist(record)
        print(f"{BLOCKLIST.relative_to(BLOCKLIST.parents[2])}: {len(record['artists'])} blocked artists, "
              f"{len(record['cleared'])} cleared works.")
        if args.block:
            print("Rebuild both sites to drop their works (and run tools/prune_originals.py for any on Releases).")
        return 0

    items = review(scan(all_works()))
    write_review(items)
    print(f"Flagged for review: {len(items)} works by {len({h.lower() for h, _, _ in items})} artists")
    for handle, p, res in items:
        why = "flag" if res["flags"] else ""
        why += (" + " if why and res["words"] else "") + ("word" if res["words"] else "")
        print(f"  {handle:<22} {why:<11} {p.name}")
    print(REVIEW)
    if items and not args.no_open:
        subprocess.run(["open", str(REVIEW)])
    return 0


if __name__ == "__main__":
    sys.exit(main())
