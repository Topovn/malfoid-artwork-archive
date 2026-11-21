#!/usr/bin/env python3
"""Measure how comic-like each still image is, for the Comic tag.

Two signals are measured on every still image of both sites, from the originals in the harvest folders:
  - panels: framed panel outlines, full-length borders and gutters (OpenCV), and an XY-cut of the page
    along uniform gutter bands;
  - style: the comic probability from deepghs' anime image classifier (dghs-imgutils, run locally).
The raw measurements are cached per file in data/comic_features.json, so reruns only measure new works.
Run it with the project environment, which has the classifier (see tools/requirements-comics.txt).
The yes/no decision is made from them by `detected_comic()` here, and the owner's corrections in data/comics.json
("comic" and "art" lists of filenames) always win over it.

Usage:
    .venv/bin/python tools/detect_comics.py            # measure works not measured yet
    .venv/bin/python tools/detect_comics.py --report   # also list each work's verdict and signals
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from concurrent.futures import ProcessPoolExecutor, as_completed
from pathlib import Path

import numpy as np

REPO = Path(__file__).resolve().parent.parent
DATA = REPO / "data"
FEATURES = DATA / "comic_features.json"
DECISIONS = DATA / "comics.json"
HARVEST_ROOT = Path.home() / "Downloads/twitter_media_harvest"
SOURCES = [HARVEST_ROOT / "Malfoid", HARVEST_ROOT / "Malfoid" / "_LostTweets", HARVEST_ROOT / "Malfoid H"]
STILL_EXTS = {"jpg", "jpeg", "png", "webp"}
# Bump a version when its measurements change, so every work is measured again; the other set is kept.
PANEL_VERSION = 3
STYLE_VERSION = 1
STYLE_MODEL = "caformer_s36_v1.3_focal"  # the classifier's most accurate model

ANALYSIS_SIDE = 1200  # long side the panel analysis runs at


# ---------- measurement ----------

def _load_gray(path: Path) -> np.ndarray:
    from PIL import Image, ImageOps

    with Image.open(path) as im:
        im = ImageOps.exif_transpose(im)
        if im.mode in ("RGBA", "LA", "P"):
            im = im.convert("RGBA")
            bg = Image.new("RGBA", im.size, (255, 255, 255, 255))
            im = Image.alpha_composite(bg, im)
        im = im.convert("L")
        im.thumbnail((ANALYSIS_SIDE, ANALYSIS_SIDE), Image.LANCZOS)
        return np.asarray(im, dtype=np.uint8)


def _long_lines(gray: np.ndarray) -> dict:
    """Long straight lines inside the page, grouped into distinct borders.

    "full" counts borders running (nearly) the whole width or height, as between two panels that touch;
    "gutters" counts close parallel pairs, the two edges of a gutter between framed panels.
    """
    import cv2

    h, w = gray.shape
    edges = cv2.Canny(gray, 60, 160)
    short = min(h, w)
    raw = cv2.HoughLinesP(edges, 1, np.pi / 360, threshold=80, minLineLength=int(short * 0.3), maxLineGap=6)
    borders: list[tuple[str, float, float]] = []  # (orientation, offset as a fraction, length as a fraction)
    for x1, y1, x2, y2 in ([] if raw is None else raw.reshape(-1, 4)):
        dx, dy = x2 - x1, y2 - y1
        length = float(np.hypot(dx, dy))
        angle = abs(np.degrees(np.arctan2(dy, dx))) % 180
        if angle < 1.5 or angle > 178.5:
            kind, offset, span = "h", (y1 + y2) / 2 / h, length / w
        elif abs(angle - 90) < 1.5:
            kind, offset, span = "v", (x1 + x2) / 2 / w, length / h
        else:
            # Slanted panel borders: keep the long ones, keyed by angle and position.
            kind, span = "d", length / max(h, w)
            offset = round(angle / 3) * 1000 + ((x1 + x2) / 2 / w + (y1 + y2) / 2 / h)
            if span < 0.3:
                continue
        if kind != "d" and (offset < 0.03 or offset > 0.97):
            continue  # the page's own edge or a frame hugging it
        borders.append((kind, offset, span))

    # Merge segments of the same border.
    merged: list[list] = []
    for kind, offset, span in sorted(borders):
        if merged and merged[-1][0] == kind and abs(merged[-1][1] - offset) < 0.006:
            merged[-1][2] = max(merged[-1][2], span)
        else:
            merged.append([kind, offset, span])
    straight = [m for m in merged if m[0] != "d"]
    gutters = sum(1 for a, b in zip(straight, straight[1:]) if a[0] == b[0] and 0.004 <= b[1] - a[1] <= 0.06)
    # Full-length borders, with the two edges of one gutter counted once.
    full, last = 0, None
    for m in straight:
        if m[2] >= 0.9:
            if not (last and last[0] == m[0] and m[1] - last[1] <= 0.06):
                full += 1
            last = m
    return {
        "lines_h": sum(1 for m in merged if m[0] == "h"),
        "lines_v": sum(1 for m in merged if m[0] == "v"),
        "lines_d": sum(1 for m in merged if m[0] == "d"),
        "gutters": gutters,
        "full": full,
    }


def _boxes(gray: np.ndarray) -> int:
    """Framed panels: closed four-sided outlines covering 3%-90% of the page, counting only the innermost ones."""
    import cv2

    h, w = gray.shape
    edges = cv2.dilate(cv2.Canny(gray, 50, 150), np.ones((3, 3), np.uint8))
    contours, _ = cv2.findContours(edges, cv2.RETR_LIST, cv2.CHAIN_APPROX_SIMPLE)
    boxes = []
    for c in contours:
        area = cv2.contourArea(c)
        if not h * w * 0.03 <= area <= h * w * 0.9:
            continue
        approx = cv2.approxPolyDP(c, 0.02 * cv2.arcLength(c, True), True)
        if len(approx) != 4 or not cv2.isContourConvex(approx):
            continue
        (_, _), (rw, rh), _ = cv2.minAreaRect(c)
        if area / (rw * rh + 1e-6) < 0.85:
            continue
        boxes.append(cv2.boundingRect(approx))

    def inside(a, b, slack=0.02):  # a within b
        sx, sy = w * slack, h * slack
        return a[0] >= b[0] - sx and a[1] >= b[1] - sy and a[0] + a[2] <= b[0] + b[2] + sx and a[1] + a[3] <= b[1] + b[3] + sy

    # The inner and outer edge of one border make two near-identical boxes; keep one.
    uniq = []
    for b in sorted(boxes, key=lambda b: b[2] * b[3]):
        if any(inside(u, b) and u[2] * u[3] > b[2] * b[3] * 0.8 for u in uniq):
            continue
        uniq.append(b)
    # A frame around panels (or around a picture inside it) is not a panel itself.
    leaves = [b for b in uniq if not any(o is not b and inside(o, b) for o in uniq)]
    return len(leaves)


def _xy_cut(gray: np.ndarray) -> tuple[int, int]:
    """Cut the page along uniform bands that run all the way across, recursively.

    Returns (pieces, filled pieces). A filled piece has content right up to its edges, as a panel does;
    loose drawings on a sketch sheet leave blank space around themselves and count only as pieces.
    """
    h, w = gray.shape
    min_band = max(3, int(min(h, w) * 0.008))
    values, counts = np.unique(gray[:: 4, :: 4], return_counts=True)
    background = int(values[counts.argmax()])
    pieces = filled = 0

    def uniform(lines: np.ndarray) -> np.ndarray:
        return (lines.std(axis=1) < 7) & (np.ptp(lines, axis=1) < 60)

    def cut(y0: int, y1: int, x0: int, x1: int, depth: int) -> None:
        nonlocal pieces, filled
        block = gray[y0:y1, x0:x1]
        bh, bw = block.shape
        if bh < 20 or bw < 20 or block.std() < 12:
            return  # blank space, not a panel
        for axis in (0, 1):
            lines = block if axis == 0 else block.T
            flags = uniform(lines)
            runs, start = [], None
            for i, f in enumerate(np.append(flags, False)):
                if f and start is None:
                    start = i
                elif not f and start is not None:
                    if i - start >= min_band and start > 0 and i < len(flags):
                        runs.append((start, i))
                    start = None
            parts, prev = [], 0
            for a, b in runs:
                if a - prev > len(flags) * 0.08:
                    parts.append((prev, a))
                prev = b
            if len(flags) - prev > len(flags) * 0.08:
                parts.append((prev, len(flags)))
            if len(parts) >= 2 and depth < 6:
                for a, b in parts:
                    if axis == 0:
                        cut(y0 + a, y0 + b, x0, x1, depth + 1)
                    else:
                        cut(y0, y1, x0 + a, x0 + b, depth + 1)
                return
        if bh * bw < h * w * 0.015:
            return
        pieces += 1
        ring = max(2, int(min(bh, bw) * 0.03))
        edge = np.concatenate([block[:ring].ravel(), block[-ring:].ravel(), block[:, :ring].ravel(), block[:, -ring:].ravel()])
        if (np.abs(edge.astype(int) - background) > 25).mean() >= 0.5:
            filled += 1

    cut(0, h, 0, w, 0)
    return pieces, filled


def measure(job: tuple[str, dict]) -> tuple[str, dict]:
    """Measure one image, reusing whichever measurement set in `old` is still current."""
    path_str, old = job
    path = Path(path_str)
    feats = dict(old) if "error" not in old else {}
    try:
        if feats.get("pv") != PANEL_VERSION:
            gray = _load_gray(path)
            feats.update(_long_lines(gray))
            feats["boxes"] = _boxes(gray)
            feats["pieces"], feats["filled"] = _xy_cut(gray)
            feats["ar"] = round(gray.shape[0] / gray.shape[1], 3)
            feats["pv"] = PANEL_VERSION
        if feats.get("sv") != STYLE_VERSION:
            from imgutils.validate import anime_classify_score

            feats["style"] = round(float(anime_classify_score(str(path), model_name=STYLE_MODEL)["comic"]), 3)
            feats["sv"] = STYLE_VERSION
        for old_key in ("panels", "text", "tv", "v"):  # earlier measurements
            feats.pop(old_key, None)
        return path.name, feats
    except Exception as exc:  # noqa: BLE001 - report and keep going
        return path.name, {"error": str(exc)[:200]}


def current(feats: dict | None) -> bool:
    return (
        bool(feats) and "error" not in feats
        and feats.get("pv") == PANEL_VERSION and feats.get("sv") == STYLE_VERSION
    )


# ---------- decision ----------

def panel_count(feats: dict) -> int:
    """Panels the page appears to have; 1 means a single picture.

    The owner's rule: a comic has two or more panels, with or without lettering, memes included;
    a single illustration stays artwork however much dialogue it carries. So lettering never decides alone.
    """
    if "error" in feats:
        return 1
    boxes, pieces, full = feats.get("boxes", 0), feats.get("pieces", 0), feats.get("full", 0)
    straight = feats.get("gutters", 0) + full + feats.get("lines_h", 0) + feats.get("lines_v", 0) + feats.get("lines_d", 0)
    if boxes >= 2:
        return boxes  # framed panels
    if pieces >= 2 and (feats.get("filled", 0) >= 1 or straight >= 1):
        return pieces  # cut apart by gutters, with panel edges (not loose drawings on a sketch sheet)
    if full >= 2:
        return full + 1  # panels butted together along full-length borders
    return 1


def load_json(path: Path, default):
    return json.loads(path.read_text()) if path.exists() else default


def detected_comic(feats: dict | None) -> bool:
    """The detector's call. Panels and style catch different mistakes, so each needs the other's support:
    found panels need only a hint of comic style (stained glass, posters and notebook lines have none),
    and without found panels the classifier must be sure (borderless manga pages are; a single picture
    with a speech bubble usually isn't)."""
    if not feats or "error" in feats:
        return False
    style = feats.get("style")
    if style is None:  # not classified yet: panels alone
        return panel_count(feats) >= 2
    return style >= (STYLE_WITH_PANELS if panel_count(feats) >= 2 else STYLE_ALONE)


STYLE_WITH_PANELS = 0.1
STYLE_ALONE = 0.95


def is_comic(name: str, feats: dict | None, decisions: dict) -> bool:
    if name in decisions.get("comic", []):
        return True
    if name in decisions.get("art", []):
        return False
    return detected_comic(feats)


# ---------- run ----------

def _cap_threads(threads: int) -> None:
    """Worker start-up. dghs-imgutils gives every classifier session one thread per CPU core (os.cpu_count()),
    which keeps every core busy however few workers run; workers do nothing else, so cap it here."""
    os.cpu_count = lambda: threads
    try:
        import cv2

        cv2.setNumThreads(threads)
    except ImportError:
        pass


def still_files() -> list[Path]:
    files = []
    for folder in SOURCES:
        if folder.is_dir():
            files += [p for p in folder.iterdir() if p.is_file() and p.suffix[1:].lower() in STILL_EXTS]
    return sorted(files)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--report", action="store_true", help="print each work's panel count after measuring")
    ap.add_argument("--workers", type=int, default=1, help="processes measuring at once (default 1)")
    ap.add_argument("--threads", type=int, default=2, help="threads each process may use (default 2)")
    args = ap.parse_args()

    cache = load_json(FEATURES, {})
    files = still_files()
    todo = [p for p in files if not current(cache.get(p.name))]
    print(f"{len(files)} still images, {len(files) - len(todo)} measured, {len(todo)} to measure.")
    done = 0
    if todo:
        with ProcessPoolExecutor(max_workers=args.workers, initializer=_cap_threads, initargs=(args.threads,)) as pool:
            futures = [pool.submit(measure, (str(p), cache.get(p.name, {}))) for p in todo]
            for fut in as_completed(futures):
                name, feats = fut.result()
                cache[name] = feats
                done += 1
                if done % 200 == 0 or done == len(todo):
                    print(f"  {done}/{len(todo)}", flush=True)
                    tmp = FEATURES.with_suffix(".json.tmp")
                    tmp.write_text(json.dumps(cache, ensure_ascii=False, sort_keys=True, separators=(",", ":")))
                    tmp.replace(FEATURES)
    # Forget works no longer in any folder (deleted, or moved to Malfoid AI/); they are measured again if they return.
    names = {p.name for p in files}
    for gone in set(cache) - names:
        del cache[gone]
    tmp = FEATURES.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(cache, ensure_ascii=False, sort_keys=True, separators=(",", ":")))
    tmp.replace(FEATURES)
    errors = {n: f["error"] for n, f in cache.items() if "error" in f}
    for n, e in sorted(errors.items()):
        print(f"  failed: {n}: {e}")
    decisions = load_json(DECISIONS, {})
    comics = sum(is_comic(n, cache.get(n), decisions) for n in names)
    print(f"Comics: {comics} of {len(names)} still images.")
    if args.report:
        for n in sorted(names):
            f = cache.get(n, {})
            verdict = "comic" if is_comic(n, f, decisions) else "art"
            print(f"{verdict:5}  panels {panel_count(f)}  style {f.get('style', '-')}  {n}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
