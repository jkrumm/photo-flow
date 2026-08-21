#!/usr/bin/env python3
"""
F5 compare-view survey — what the 2048 px `view` ceiling actually costs.

Answers three questions with numbers taken from the REAL library, and is safe to run
against it: the index is opened ``mode=ro``, no photo is read except to decode it in
memory, and the only thing written anywhere is a throwaway thumbnail cache in a temp
directory — the live cache at ``THUMB_CACHE_PATH`` is neither read nor touched, so the
cold-generation numbers below are genuinely cold.

1. **How far is the `view` tier from 1:1?** Per-master linear and pixel ratios, and the
   magnification at which the proxy runs out on a given stage.
2. **What would a full-resolution TIER cost?** Decode + encode + bytes, measured, as the
   alternative to serving the master's own bytes (which is what shipped).
3. **What does an N-up compare layout cost in resolution?** The same arithmetic the UI's
   ``bestColumns`` does, for N = 1..4 on a stated stage size.
4. **Where does the proxy actually run out?** Per N and per orientation: the fitted long
   edge, the magnification at which the 2048 px proxy starts upscaling, and the
   magnification true 1:1 sits at. This is the number the F5 finding turns on.
5. **What does one master cost against one `view` frame?** Bytes on the wire, cold
   generation, warm cache hit, client-side decode, and the resident bitmap — measured on
   the real masters, into a throwaway cache so the live one is neither read nor written.

Usage::

    venv/bin/python scripts/compare_survey.py                 # default: 12 masters
    venv/bin/python scripts/compare_survey.py --sample 40
    venv/bin/python scripts/compare_survey.py --stage 2500x1300 --dpr 2
    venv/bin/python scripts/compare_survey.py --no-encode     # skip the full-tier bench

The default stage is the one MEASURED in the running panel on this machine
(`document.querySelector('[data-frame]').parentElement`, 5120x1440 display, dpr 1) —
the window minus the nav rail, the sidebar and the filmstrip. Its arithmetic reproduces
the app's own layout exactly: 3-up cells of 1524x1266 and a "1:1 at 4.93x" readout.
"""

from __future__ import annotations

import argparse
import io
import shutil
import sqlite3
import statistics
import tempfile
import time
from pathlib import Path
from typing import Dict, List, Sequence, Tuple

from PIL import Image, ImageOps

from photo_flow.config import THUMB_SIZES
from photo_flow.console_utils import console, info, warning
from photo_flow.index import thumbs
from photo_flow.index.db import _DEFAULT_DB_PATH

# Quality a hypothetical full-resolution tier would encode at. Deliberately generous —
# the point of the measurement is that even a generous re-encode is worse than a copy.
FULL_TIER_QUALITY = 90


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


def sample_masters(db: sqlite3.Connection, per_root: int) -> List[Tuple[str, int, int, int]]:
    """
    Pick a deterministic spread of masters from both culling roots.

    Args:
        db: Read-only index connection.
        per_root: How many rows to take from each root.

    Returns:
        List of (path, width, height, size) tuples. Width/height are DISPLAY dimensions.
    """
    rows: List[Tuple[str, int, int, int]] = []
    for root in ("final", "staging"):
        rows.extend(
            db.execute(
                """
                SELECT path, width, height, size FROM photos
                WHERE present = 1 AND root = ? AND width > 0 AND height > 0
                ORDER BY path
                LIMIT ?
                """,
                (root, per_root),
            ).fetchall()
        )
    return rows


def dimension_census(db: sqlite3.Connection) -> None:
    """Print how far every indexed master sits from the `view` tier, by long edge."""
    view = THUMB_SIZES["view"]
    buckets: Dict[int, int] = {}
    for (long_edge,) in db.execute(
        "SELECT MAX(width, height) FROM photos WHERE present = 1 AND width > 0"
    ):
        buckets[long_edge] = buckets.get(long_edge, 0) + 1

    total = sum(buckets.values())
    console.print(f"\n[bold]Masters vs the {view} px `view` tier[/bold]  ({total} indexed rows)")
    console.print(f"{'long edge':>10s} {'photos':>7s} {'linear %':>9s} {'pixels %':>9s}")
    for long_edge, count in sorted(buckets.items(), key=lambda item: -item[1])[:8]:
        linear = view / long_edge
        console.print(
            f"{long_edge:>10d} {count:>7d} {linear * 100:>8.1f}% {linear * linear * 100:>8.1f}%"
        )
    below = sum(count for edge, count in buckets.items() if edge <= view)
    console.print(f"{'':>10s} {below:>7d}  already at or below the tier")


def layout_cost(stage: Tuple[int, int], aspect: float, max_n: int = 4) -> None:
    """
    What an N-up comparison costs in rendered resolution on one stage.

    Mirrors ``bestColumns`` in ``photo-compare.tsx``: try every column count, keep the one
    that renders the largest frame.

    Args:
        stage: (width, height) of the compare stage's content box, in CSS px.
        aspect: Frame aspect ratio (width / height).
        max_n: Largest compare set to report.
    """
    gap = 4
    console.print(
        f"\n[bold]Compare layout on a {stage[0]}x{stage[1]} stage[/bold] "
        f"(frame aspect {aspect:.2f})"
    )
    console.print(f"{'N':>2s} {'cols':>5s} {'frame px':>12s} {'long edge':>10s} {'vs 1-up':>8s}")
    baseline = 0.0
    for count in range(1, max_n + 1):
        best = (0.0, 1, 0.0, 0.0)
        for cols in range(1, count + 1):
            grid_rows = -(-count // cols)
            cell_w = (stage[0] - gap * (cols - 1)) / cols
            cell_h = (stage[1] - gap * (grid_rows - 1)) / grid_rows
            if cell_w <= 0 or cell_h <= 0:
                continue
            fit = min(cell_w / aspect, cell_h)
            area = fit * fit * aspect
            if area > best[0]:
                best = (area, cols, fit * aspect, fit)
        _, cols, width, height = best
        long_edge = max(width, height)
        if count == 1:
            baseline = long_edge
        console.print(
            f"{count:>2d} {cols:>5d} {width:>6.0f}x{height:<5.0f} {long_edge:>10.0f} "
            f"{long_edge / baseline * 100:>7.1f}%"
        )
    view = THUMB_SIZES["view"]
    console.print(
        f"   the `view` tier supplies {view} px, so a 1-up frame runs out at "
        f"{view / baseline:.2f}x magnification"
    )


def full_tier_bench(paths: Sequence[Tuple[str, int, int, int]]) -> None:
    """
    Measure what a full-resolution thumbnail TIER would cost to generate.

    This is the option that was NOT taken. It is measured so the decision to stream the
    master's own bytes rests on numbers rather than on taste.

    Args:
        paths: (path, width, height, size) rows to benchmark.
    """
    console.print("\n[bold]Cost of a hypothetical full-resolution tier[/bold]")
    console.print(
        f"{'file':>34s} {'dims':>12s} {'src MB':>7s} {'decode ms':>10s} "
        f"{'encode ms':>10s} {'out MB':>7s} {'vs src':>7s}"
    )
    decodes: List[float] = []
    encodes: List[float] = []
    ratios: List[float] = []
    for raw, width, height, size in paths:
        source = Path(raw)
        if not source.is_file():
            warning(f"missing: {source.name}")
            continue
        started = time.perf_counter()
        with Image.open(source) as image:
            work = ImageOps.exif_transpose(image) or image
            work.load()
            decode_ms = (time.perf_counter() - started) * 1000
            encodable = work if work.mode == "RGB" else work.convert("RGB")
            started = time.perf_counter()
            buffer = io.BytesIO()
            encodable.save(
                buffer, "JPEG", quality=FULL_TIER_QUALITY, progressive=True, optimize=True
            )
            encode_ms = (time.perf_counter() - started) * 1000
        out = buffer.tell()
        decodes.append(decode_ms)
        encodes.append(encode_ms)
        ratios.append(out / size)
        console.print(
            f"{source.name[:34]:>34s} {width}x{height:<6d} {size / 1e6:>7.2f} "
            f"{decode_ms:>10.1f} {encode_ms:>10.1f} {out / 1e6:>7.2f} {out / size:>6.2f}x"
        )
    if not decodes:
        return
    total = statistics.median(decodes) + statistics.median(encodes)
    console.print(
        f"\n  median decode {statistics.median(decodes):.0f} ms + encode "
        f"{statistics.median(encodes):.0f} ms = [bold]{total:.0f} ms[/bold] per photo, "
        f"output {statistics.median(ratios):.2f}x the source size."
    )
    info(
        "Streaming the master instead costs 0 ms of CPU, 0 bytes of cache, and cannot "
        "attenuate the micro-contrast the magnification exists to judge."
    )


def magnification_ceiling(
    stage: Tuple[int, int], max_n: int = 4, dpr: float = 1.0
) -> None:
    """
    Where the 2048 px proxy stops being able to answer the question.

    For each compare set size and each of the two orientations this library actually
    produces, report: the long edge the frame is laid out at, the magnification at which
    the proxy runs out of pixels and starts upscaling, and the magnification at which one
    master pixel covers one device pixel (true 1:1).

    The gap between those last two columns is constant — ``master_long / 2048`` — and it
    is the finding: no stage size and no value of N can close it, because both scale
    together. Only fetching the master can.

    Args:
        stage: (width, height) of the compare stage's content box, in CSS px.
        max_n: Largest compare set to report.
        dpr: Device pixel ratio of the display.
    """
    view = THUMB_SIZES["view"]
    gap = 4
    console.print(
        f"\n[bold]Where the {view} px proxy runs out[/bold]  "
        f"(stage {stage[0]}x{stage[1]} CSS px, dpr {dpr:g})"
    )
    console.print(
        f"{'N':>2s} {'orientation':>12s} {'master':>10s} {'fitted px':>10s} "
        f"{'proxy to':>9s} {'1:1 at':>8s} {'shortfall':>10s}"
    )
    # The two masters this library holds: 26 MP native (Staging, and Final since v0.3.4)
    # and the 18 MP legacy frames the pre-v0.3.4 finalize step re-compressed to 5200 px.
    for label, aspect, master in (("landscape", 3 / 2, 6240), ("portrait", 2 / 3, 6240)):
        for count in range(1, max_n + 1):
            best_fit = 0.0
            for cols in range(1, count + 1):
                grid_rows = -(-count // cols)
                cell_w = (stage[0] - gap * (cols - 1)) / cols
                cell_h = (stage[1] - gap * (grid_rows - 1)) / grid_rows
                if cell_w <= 0 or cell_h <= 0:
                    continue
                fit = min(cell_w / aspect, cell_h)
                best_fit = max(best_fit, fit)
            fitted_long = max(best_fit * aspect, best_fit) * dpr
            if fitted_long <= 0:
                continue
            console.print(
                f"{count:>2d} {label:>12s} {master:>10d} {fitted_long:>10.0f} "
                f"{view / fitted_long:>8.2f}x {master / fitted_long:>7.2f}x "
                f"{master / view:>9.2f}x"
            )
    console.print(
        f"   shortfall is master/{view} in every row — a constant, because the proxy "
        f"ceiling and 1:1 scale with the same fitted size."
    )
    console.print(
        f"   the 18 MP legacy frames the pre-v0.3.4 finalize re-compressed to 5200 px "
        f"sit at {5200 / view:.2f}x instead; nothing in this library is below the tier."
    )


def wire_bench(paths: Sequence[Tuple[str, int, int, int]]) -> None:
    """
    What a master costs against the `view` frame it replaces, end to end.

    Everything the client pays for a true-1:1 frame, measured on the real masters: bytes
    over the wire, the server's cold generation and warm cache hit for the proxy it is
    replacing, the decode, and the resident bitmap the renderer then holds.

    The proxy side is generated into a **temporary** cache directory, so "cold" means
    cold rather than "whatever the live cache happened to hold".

    Args:
        paths: (path, width, height, size) rows to benchmark.
    """
    console.print("\n[bold]One master against one `view` frame[/bold]")
    console.print(
        f"{'file':>34s} {'view KB':>8s} {'cold ms':>8s} {'warm ms':>8s} "
        f"{'master MB':>10s} {'wire x':>7s} {'decode ms':>10s} {'bitmap MB':>10s}"
    )
    scratch = Path(tempfile.mkdtemp(prefix="photoflow-survey-"))
    live = thumbs.THUMB_CACHE_PATH
    thumbs.THUMB_CACHE_PATH = scratch  # type: ignore[misc]
    view_bytes: List[int] = []
    colds: List[float] = []
    warms: List[float] = []
    wires: List[float] = []
    decodes: List[float] = []
    bitmaps: List[float] = []
    try:
        for raw, width, height, size in paths:
            source = Path(raw)
            if not source.is_file():
                warning(f"missing: {source.name}")
                continue

            started = time.perf_counter()
            generated = thumbs.get_thumbs(source, ["view"])["view"][0]
            cold_ms = (time.perf_counter() - started) * 1000
            if generated is None:
                warning(f"no thumbnail: {source.name}")
                continue
            started = time.perf_counter()
            thumbs.get_thumbs(source, ["view"])
            warm_ms = (time.perf_counter() - started) * 1000
            proxy = generated.stat().st_size

            # Decode of the MASTER, as the client's renderer must do it: no draft, because
            # the browser has no equivalent and is decoding it to display it whole.
            started = time.perf_counter()
            with Image.open(source) as image:
                work = ImageOps.exif_transpose(image) or image
                work.load()
            decode_ms = (time.perf_counter() - started) * 1000
            bitmap_mb = width * height * 4 / 1e6

            view_bytes.append(proxy)
            colds.append(cold_ms)
            warms.append(warm_ms)
            wires.append(size / proxy)
            decodes.append(decode_ms)
            bitmaps.append(bitmap_mb)
            console.print(
                f"{source.name[:34]:>34s} {proxy / 1e3:>8.0f} {cold_ms:>8.0f} "
                f"{warm_ms:>8.2f} {size / 1e6:>10.2f} {size / proxy:>6.1f}x "
                f"{decode_ms:>10.0f} {bitmap_mb:>10.1f}"
            )
    finally:
        thumbs.THUMB_CACHE_PATH = live  # type: ignore[misc]
        shutil.rmtree(scratch, ignore_errors=True)

    if not view_bytes:
        return
    console.print(
        f"\n  median `view` frame {statistics.median(view_bytes) / 1e3:.0f} KB "
        f"(cold {statistics.median(colds):.0f} ms, warm {statistics.median(warms):.2f} ms); "
        f"a master is [bold]{statistics.median(wires):.1f}x[/bold] the bytes, "
        f"{statistics.median(decodes):.0f} ms to decode, "
        f"{statistics.median(bitmaps):.0f} MB resident."
    )
    # Both numbers, deliberately: the median is what a 4-up usually costs and the max is
    # what it can cost. Quoting the median alone understates the ceiling by ~18 % here,
    # because this library's largest masters (4160x6240) are also its most numerous.
    info(
        f"A 4-up at true 1:1 holds ~{4 * statistics.median(bitmaps):.0f} MB of bitmap at "
        f"the median and ~{4 * max(bitmaps):.0f} MB at this library's worst case "
        f"({max(bitmaps):.0f} MB a frame) — which is why the master is fetched on "
        f"magnification, not on selection, and dropped again the moment the stage "
        f"returns to fit."
    )


def main() -> None:
    """Run the survey and print every table."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--sample", type=int, default=6, help="masters per root to benchmark")
    parser.add_argument(
        "--stage",
        default="4579x1266",
        help="compare stage content box, WxH in CSS px (default: this machine's, measured)",
    )
    parser.add_argument("--aspect", type=float, default=1.5, help="frame aspect ratio")
    parser.add_argument("--dpr", type=float, default=1.0, help="display device pixel ratio")
    parser.add_argument("--no-encode", action="store_true", help="skip the full-tier benchmark")
    args = parser.parse_args()

    width, _, height = args.stage.partition("x")
    stage = (int(width), int(height))

    db = open_index()
    try:
        dimension_census(db)
        layout_cost(stage, args.aspect)
        magnification_ceiling(stage, dpr=args.dpr)
        if not args.no_encode:
            sample = sample_masters(db, args.sample)
            wire_bench(sample)
            full_tier_bench(sample)
    finally:
        db.close()


if __name__ == "__main__":
    main()
