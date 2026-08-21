#!/usr/bin/env python3
"""
Stage F3 survey — measure the four candidate library layouts against the REAL library.

    venv/bin/python scripts/structure_survey.py [--db PATH]

READ-ONLY BY CONSTRUCTION. The index is opened with `mode=ro` through a `file:` URI, so
sqlite refuses a write at the driver level rather than relying on this script's good
intentions. No photograph is opened, moved, renamed or written; nothing is inserted into
the index. It exists because the numbers in
`shutterflow/docs/decisions/0003-library-config-two-axes.md` are the deliverable of that
stage, and a measurement with no way to re-run it is an assertion.

What it reports, per structure (flat, YYYY/MM, album, event at several gaps):

* group count, and the size distribution (max / min / median / singletons)
* how many photos fall OUTSIDE every group — the number a directory layout hides by
  making you invent a `Misc/`
* the cost of computing it, which is the whole content of "does this need to be physical"
* for `event` specifically: how well a purely derived boundary reproduces the human's own
  hand-written album tags, which is what settles 0003's first open question
"""

from __future__ import annotations

import argparse
import re
import sqlite3
import statistics
import time
from collections import Counter
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Sequence, Tuple

DEFAULT_DB = Path.home() / ".photoflow" / "index.db"
ISO = "%Y-%m-%dT%H:%M:%SZ"

GAPS: List[Tuple[int, str]] = [
    (600, "10 min"),
    (3600, "1 h"),
    (8 * 3600, "8 h"),
    (86400, "1 day"),
    (2 * 86400, "2 days"),
    (3 * 86400, "3 days"),
    (7 * 86400, "7 days"),
]


def open_readonly(path: Path) -> sqlite3.Connection:
    """
    Open the index read-only.

    Args:
        path: Index database path.

    Returns:
        A connection that cannot write, whatever this script tries.
    """
    conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    return conn


def describe(name: str, sizes: Sequence[int], universe: int, outside: int) -> None:
    """Print one structure's size distribution and coverage."""
    if not sizes:
        print(f"{name:<22} no groups")
        return
    ordered = sorted(sizes, reverse=True)
    print(
        f"{name:<22} {len(ordered):>4} groups   "
        f"max {ordered[0]:>5}  min {ordered[-1]:>4}  median {statistics.median(ordered):>7}  "
        f"singletons {sum(1 for s in ordered if s == 1):>4}  outside {outside:>5}  "
        f"of {universe}"
    )


def cluster(seconds: Sequence[float], gap: int) -> List[Tuple[int, int]]:
    """
    Time-gap clustering: a new event starts wherever the silence exceeds `gap`.

    Args:
        seconds: Capture times, ascending.
        gap: Seconds of silence that end an event.

    Returns:
        List of (first index, last index) pairs.
    """
    if not seconds:
        return []
    out: List[Tuple[int, int]] = []
    start = 0
    for i in range(1, len(seconds)):
        if seconds[i] - seconds[i - 1] > gap:
            out.append((start, i - 1))
            start = i
    out.append((start, len(seconds) - 1))
    return out


def main() -> None:
    """Run the survey and print it."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", type=Path, default=DEFAULT_DB)
    parser.add_argument(
        "--where",
        default="present = 1",
        help="SQL predicate selecting the universe (default: every present row).",
    )
    args = parser.parse_args()

    conn = open_readonly(args.db)
    rows = conn.execute(
        f"SELECT root, filename, date_taken, keywords FROM photos "
        f"WHERE {args.where} ORDER BY date_taken"
    ).fetchall()
    universe = len(rows)
    print(f"index {args.db}  ·  universe `{args.where}`  ·  {universe} rows\n")

    # ---- flat -------------------------------------------------------------
    roots = Counter(r["root"] for r in rows)
    describe("flat (root)", list(roots.values()), universe, 0)

    # ---- YYYY/MM ----------------------------------------------------------
    months = Counter(r["date_taken"][:7] for r in rows if (r["date_taken"] or ""))
    undated = sum(1 for r in rows if not (r["date_taken"] or ""))
    describe("YYYY/MM", list(months.values()), universe, undated)
    describe("YYYY", list(Counter(k[:4] for k in months.elements()).values()), universe, undated)

    # ---- album ------------------------------------------------------------
    tags: Counter = Counter()
    untagged = 0
    for row in rows:
        names = [k for k in (row["keywords"] or "").split("|") if k]
        if names:
            tags.update(names)
        else:
            untagged += 1
    describe("album (dc:subject)", list(tags.values()), universe, untagged)
    for tag, count in tags.most_common():
        print(f"{'':<22}   {tag:<26} {count:>5}")

    # ---- event ------------------------------------------------------------
    # `dated` is the parallel row list for `stamps`/`seconds`. Keeping it is not
    # bookkeeping for its own sake: the cluster bounds below index into the DATED
    # sequence, and indexing the full `rows` with them silently shifts every lookup by
    # the number of undated rows (which sort first). This library has none, so the bug
    # is invisible here and wrong everywhere else.
    dated = [r for r in rows if (r["date_taken"] or "")]
    stamps = [r["date_taken"] for r in dated]
    seconds = [datetime.strptime(s, ISO).timestamp() for s in stamps]
    print()
    for gap, label in GAPS:
        started = time.perf_counter()
        groups = cluster(seconds, gap)
        elapsed = (time.perf_counter() - started) * 1000
        describe(f"event ({label})", [b - a + 1 for a, b in groups], universe, undated)
        print(f"{'':<22}   clustering itself: {elapsed:.2f} ms")

    # ---- does a derived boundary reproduce the human's own albums? ---------
    print("\nDerived event boundary vs the human's own album tags")
    print("(minimal set of clusters covering each tag, and what that set drags in)")
    for gap, label in GAPS:
        groups = cluster(seconds, gap)
        print(f"  gap {label} — {len(groups)} events")
        for tag, total in tags.most_common():
            needle = f"|{tag}|"
            hit = {
                index
                for index, (a, b) in enumerate(groups)
                # Pipe-sentinelled, the same match `_clauses` runs (`LIKE '%|tag|%'`).
                # A bare substring test over the packed column over-counts a tag that
                # is a prefix of another — the shape 0004 reserves `|` for.
                if any(needle in (dated[i]["keywords"] or "") for i in range(a, b + 1))
            }
            covered = sum(groups[i][1] - groups[i][0] + 1 for i in hit)
            print(
                f"     {tag:<26} {total:>5} → {len(hit):>3} cluster(s), {covered:>5} photos, "
                f"precision {total / covered:.2f}"
            )

    # ---- is YYYY/MM adding anything the filename does not already carry? ---
    # `timestamp_renamer` names every imported file `YYYY-MM-DD_HH-MM-SS_<base>`, so a
    # month tree may be re-deriving what a sorted `ls` already shows. This measures
    # whether the two actually agree.
    exact = differ = untimestamped = 0
    for row in rows:
        match = re.match(r"^(\d{4}-\d{2}-\d{2})_(\d{2})-(\d{2})-(\d{2})(?:-\d+)?_", row["filename"])
        if match is None:
            untimestamped += 1
            continue
        stamp = f"{match.group(1)}T{match.group(2)}:{match.group(3)}:{match.group(4)}Z"
        if (row["date_taken"] or "") == stamp:
            exact += 1
        else:
            differ += 1
    print(
        f"\nFilename stamp vs date_taken: {exact} exact, {differ} differ, "
        f"{untimestamped} not timestamp-named"
    )
    conn.close()


if __name__ == "__main__":
    main()
