#!/usr/bin/env python3
"""
F6 contact-sheet survey — what a full-page raster grid costs, and what it can show.

Answers, with numbers from the REAL library, the three questions the F6 finding turns on.
Safe to run against it: the index is opened ``mode=ro``, no photo is written, and every
cold-generation measurement renders into a throwaway cache in a temp directory, so the
live cache at ``THUMB_CACHE_PATH`` is neither read nor touched by the benchmark.

1. **Density.** How many frames a sheet holds at each density stop, on real window sizes,
   and how large the photograph inside a cell actually is. This is the "at what cell size
   does a photograph stop being judgeable" question reduced to the numbers a judgement can
   be made against — the judgement itself is a human one and is recorded in `0003`.
2. **Cache.** What the `grid` tier costs to hold for the whole library, against the `view`
   tier the single-frame viewer needs. The two differ by more than an order of magnitude,
   which is the reason a sheet over 3 800 rows is cheap at all.
3. **Generation.** Cold and warm cost of filling one viewport, single-threaded and through
   ``warm_tiers``' own 4-worker pool — i.e. what the sheet's prewarm actually buys.

Usage::

    venv/bin/python scripts/grid_survey.py                    # default: 48 masters
    venv/bin/python scripts/grid_survey.py --sample 24
    venv/bin/python scripts/grid_survey.py --no-generate      # skip the decode bench

The stage sizes are MEASURED in the running panel, not guessed: the grid's scroll viewport
reports 979x905 CSS px inside a 1512x945 window with the sidebar open, which is the
laptop case. The others scale that same chrome (a 206 px nav rail, a ~312 px sidebar, the
40 px header) up to the wider windows.
"""

from __future__ import annotations

import argparse
import shutil
import sqlite3
import statistics
import tempfile
import time
from pathlib import Path
from typing import Dict, List, Sequence, Tuple

from photo_flow.config import THUMB_CACHE_PATH, THUMB_SIZES
from photo_flow.console_utils import console, info
from photo_flow.index import thumbs
from photo_flow.index.db import _DEFAULT_DB_PATH

# Mirrors `photo-grid.tsx`. Kept in one place here so the arithmetic below is the
# component's arithmetic and not a second model of it.
GAP = 6
PAD = 8
CELL_ASPECT = 1.5
DENSITY_STEPS = (96, 128, 160, 200, 256, 320)

# (label, grid viewport width, grid viewport height) — the sheet's own scroll box, i.e.
# the window minus the nav rail, the sidebar, the header and the shell's gutter.
STAGES: Tuple[Tuple[str, int, int], ...] = (
    ("14in 1512x945 (measured)", 979, 905),
    ("1920x1080", 1387, 1040),
    ("2560x1440", 2027, 1400),
    ("5120x1400 ultrawide", 4587, 1360),
)


def open_index() -> sqlite3.Connection:
    """
    Open the live index read-only.

    Returns:
        A read-only connection to the index database.

    Raises:
        SystemExit: If the index does not exist.
    """
    if not _DEFAULT_DB_PATH.is_file():
        raise SystemExit(f"No index at {_DEFAULT_DB_PATH} — run `photoflow index refresh` first.")
    return sqlite3.connect(f"file:{_DEFAULT_DB_PATH}?mode=ro", uri=True)


def layout(viewport: Tuple[int, int], density: int) -> Tuple[int, int, int, int]:
    """
    Resolve the sheet's layout for one viewport and density.

    Args:
        viewport: (width, height) of the scroll box, in CSS px.
        density: Target cell width in px.

    Returns:
        (columns, cell width, cell height, cells fully or partly visible).
    """
    inner = max(1, viewport[0] - PAD * 2)
    columns = max(1, (inner + GAP) // (density + GAP))
    cell_w = max(1, (inner - GAP * (columns - 1)) // columns)
    cell_h = max(1, round(cell_w / CELL_ASPECT))
    visible_rows = -(-viewport[1] // (cell_h + GAP))
    return columns, cell_w, cell_h, columns * visible_rows


def density_table(total_rows: int) -> None:
    """
    Print the density table for every stage.

    Args:
        total_rows: Rows in the result set the sheet is standing in for.
    """
    for label, width, height in STAGES:
        console.print(f"\n[bold]Sheet on a {label} viewport ({width}x{height})[/bold]")
        console.print(
            f"{'density':>8s} {'cols':>5s} {'cell':>10s} {'on screen':>10s} "
            f"{'of set':>7s} {'flicks':>7s} {'long edge':>10s}"
        )
        for density in DENSITY_STEPS:
            columns, cell_w, cell_h, visible = layout((width, height), density)
            share = visible / total_rows * 100
            flicks = -(-total_rows // max(1, visible))
            # A landscape frame fills the cell width; a portrait one fills its height. The
            # long edge of the PHOTOGRAPH is therefore the cell width for landscape and the
            # cell height for portrait — the second is what a mixed library is limited by.
            console.print(
                f"{density:>8d} {columns:>5d} {f'{cell_w}x{cell_h}':>10s} {visible:>10d} "
                f"{share:>6.1f}% {flicks:>7d} {f'{cell_w}/{cell_h}':>10s}"
            )
        console.print(
            "  long edge = landscape/portrait, i.e. how many px the photograph itself gets"
        )


def cache_footprint(db: sqlite3.Connection) -> None:
    """
    Print what each tier costs to hold for the whole library.

    Args:
        db: Read-only index connection.
    """
    (rows,) = db.execute("SELECT COUNT(*) FROM photos WHERE present = 1").fetchone()
    console.print(f"\n[bold]Cache footprint[/bold]  ({rows} present rows)")
    console.print(f"{'tier':>6s} {'px':>6s} {'files':>7s} {'total':>10s} {'per photo':>10s}")
    for tier in ("grid", "view"):
        root = THUMB_CACHE_PATH / tier
        files = list(root.rglob("*.jpg")) if root.is_dir() else []
        total = sum(path.stat().st_size for path in files)
        per = total / len(files) if files else 0
        console.print(
            f"{tier:>6s} {THUMB_SIZES[tier]:>6d} {len(files):>7d} "
            f"{total / 1e6:>9.1f}M {per / 1e3:>9.1f}K"
        )
    info(
        "The whole library's contact sheet is the `grid` row — that is the number that "
        "makes a sheet over every photograph a reasonable thing to build."
    )


def sample_masters(db: sqlite3.Connection, count: int) -> List[Path]:
    """
    Pick a deterministic spread of real masters across both roots.

    Args:
        db: Read-only index connection.
        count: How many paths to take in total.

    Returns:
        Existing master paths.
    """
    per_root = max(1, count // 2)
    picked: List[Path] = []
    for root in ("final", "staging"):
        for (path,) in db.execute(
            "SELECT path FROM photos WHERE present = 1 AND root = ? ORDER BY path LIMIT ?",
            (root, per_root),
        ):
            candidate = Path(path)
            if candidate.is_file():
                picked.append(candidate)
    return picked


def generation_bench(paths: Sequence[Path]) -> None:
    """
    Cold and warm cost of filling a viewport with `grid` thumbnails.

    Renders into a temp cache: `THUMB_CACHE_PATH` is a module global read at call time, so
    redirecting it here genuinely spares the live cache and makes "cold" mean cold.

    Args:
        paths: Master paths to generate for.
    """
    if not paths:
        return
    original = thumbs.THUMB_CACHE_PATH
    scratch = Path(tempfile.mkdtemp(prefix="pf-grid-survey-"))
    try:
        thumbs.THUMB_CACHE_PATH = scratch
        console.print(f"\n[bold]Generating {len(paths)} `grid` thumbnails[/bold]")

        started = time.perf_counter()
        for path in paths:
            thumbs.get_thumbs(path, ["grid"])
        cold_serial = time.perf_counter() - started

        started = time.perf_counter()
        for path in paths:
            thumbs.get_thumbs(path, ["grid"])
        warm_serial = time.perf_counter() - started

        shutil.rmtree(scratch, ignore_errors=True)
        scratch.mkdir(parents=True, exist_ok=True)
        started = time.perf_counter()
        generated = thumbs.warm_tiers(list(paths), ["grid"], workers=4)
        cold_pool = time.perf_counter() - started

        started = time.perf_counter()
        thumbs.warm_tiers(list(paths), ["grid"], workers=4)
        warm_pool = time.perf_counter() - started

        sizes = [entry.stat().st_size for entry in scratch.rglob("*.jpg")]
        console.print(
            f"{'':>16s} {'total':>9s} {'per frame':>10s}\n"
            f"{'cold, serial':>16s} {cold_serial * 1e3:>8.0f}ms "
            f"{cold_serial / len(paths) * 1e3:>9.1f}ms\n"
            f"{'warm, serial':>16s} {warm_serial * 1e3:>8.0f}ms "
            f"{warm_serial / len(paths) * 1e3:>9.2f}ms\n"
            f"{'cold, 4 workers':>16s} {cold_pool * 1e3:>8.0f}ms "
            f"{cold_pool / len(paths) * 1e3:>9.1f}ms\n"
            f"{'warm, 4 workers':>16s} {warm_pool * 1e3:>8.0f}ms "
            f"{warm_pool / len(paths) * 1e3:>9.2f}ms"
        )
        if sizes:
            console.print(
                f"  {generated} generated · median {statistics.median(sizes) / 1e3:.1f} KB "
                f"a frame, {sum(sizes) / 1e6:.1f} MB for the batch"
            )
    finally:
        thumbs.THUMB_CACHE_PATH = original
        shutil.rmtree(scratch, ignore_errors=True)


def main() -> None:
    """Run the survey and print every table."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--sample", type=int, default=48, help="masters for the decode bench")
    parser.add_argument(
        "--no-generate", action="store_true", help="skip the cold/warm generation bench"
    )
    args = parser.parse_args()

    db = open_index()
    try:
        (rows,) = db.execute("SELECT COUNT(*) FROM photos WHERE present = 1").fetchone()
        density_table(rows)
        cache_footprint(db)
        if not args.no_generate:
            generation_bench(sample_masters(db, args.sample))
    finally:
        db.close()


if __name__ == "__main__":
    main()
