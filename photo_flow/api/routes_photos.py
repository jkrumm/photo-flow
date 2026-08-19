"""
Culling endpoints for the control panel's "Photos" screen.

Everything here lives under the ``/api/photos`` prefix. That prefix is deliberate:
the SPA owns the client-side route ``/photos``, and ``app.py``'s catch-all would
shadow a bare ``/photos`` API route. The dev proxy forwards ``/api`` only, so the
SPA route and the API never collide.

Surface
-------
GET  /api/photos               — filtered, sorted, paged rows from the index
GET  /api/photos/facets        — counts/ranges/histograms per filter dimension
                                 (self-dimension open)
GET  /api/photos/thumb         — cached JPEG thumbnail (grid | view tier)
GET  /api/photos/meta          — one row plus live file sizes and full exiftool dump
POST /api/photos/rating        — batched XMP rating write-back
POST /api/photos/label         — batched XMP colour-label write-back
POST /api/photos/trash         — soft-delete (move to trash, sidecar travels)
GET  /api/photos/trash         — trash listing + stats
POST /api/photos/trash/restore — restore trash entries by id
POST /api/photos/trash/purge   — permanently delete entries past retention
POST /api/photos/warm          — pre-generate thumbnails for a batch of paths

Path safety
-----------
Every endpoint that accepts a filesystem path routes it through
:func:`_resolve_in_roots`, which resolves the path and requires it to sit *inside*
one of ``config.CULL_ROOTS`` (plus ``config.TRASH_PATH`` where a trashed file is
legitimately addressed). The service binds to localhost only, but a traversal that
serves ``~/.ssh/id_ed25519`` is not acceptable at any bind address.

Metadata write-back
-------------------
Ratings and labels are written with **exiftool**, never piexif/Pillow — see
CLAUDE.md "Safety Mechanisms §4". One subprocess handles a whole batch of files
(exiftool accepts many paths per invocation, roughly 50x faster than one process
per file), and the exit code plus stderr are both checked before anything is
reported as written. A successful write is followed by
:func:`photo_flow.index.indexer.reindex_paths` so the index agrees with the file
before the UI refetches.

The write intentionally lets exiftool update the file mtime (no ``-P``): the
thumbnail cache is keyed on mtime, so the stale preview is invalidated for free,
and a later full ``reindex()`` self-heals the row should the targeted reindex
above ever fail.

Blocking work
-------------
Every filesystem, SQLite and subprocess call is wrapped in
``await asyncio.to_thread(...)``, matching the other routers — the event loop is
shared with the SSE job stream and must never stall. That includes path
validation: ``Path.resolve()`` is a filesystem call, so a 500-path batch is
hundreds of them and goes through :func:`_resolve_batch_async`. The permitted
roots themselves are memoised (:data:`_ROOTS_CACHE`) so they are resolved once
rather than once per candidate path.
"""
from __future__ import annotations

import asyncio
import json
import logging
import math
import re
import sqlite3
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Literal, Optional, Sequence, Tuple

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

from photo_flow import trash as trash_module
from photo_flow.config import CULL_ROOTS, EDIT_SIDECAR_SUFFIX, THUMB_SIZES, TRASH_PATH
from photo_flow.index import thumbs
from photo_flow.index.db import get_db
from photo_flow.index.indexer import reindex_paths

logger = logging.getLogger(__name__)

router = APIRouter()

# The XMP spec's reject value for `xmp:Rating`, and the reason the rating column is
# signed rather than a 0–5 enum. -1 is a *third state*, not a low star count: unrated (0
# / absent), rated (1–5) and rejected (-1) are independent answers to "have I judged this
# yet?". Written straight into the JPEG like any other star, so it survives without us.
REJECTED = -1

# Guard rails on batch endpoints — a runaway client must not pin a CPU core.
MAX_WRITE_PATHS = 500
MAX_WARM_PATHS = 200
_EXIFTOOL_CHUNK = 200
_EXIFTOOL_WRITE_TIMEOUT = 60
_EXIFTOOL_READ_TIMEOUT = 5

# Column whitelist for ORDER BY — never interpolate a client string into SQL.
_SORT_COLUMNS: Dict[str, str] = {
    "date_taken": "date_taken",
    "filename": "filename",
    "rating": "rating",
    "iso": "iso",
    "focal_mm": "focal_mm",
}

# Columns making up a PhotoRow, in the order the model declares them.
_ROW_COLUMNS = (
    "path, filename, root, rating, label, orientation, width, height, date_taken, "
    "iso, aperture_f, shutter_s, focal_mm, camera_model, lens_model, has_sidecar, size, mtime"
)

# Buckets per range-slider histogram. 24 is a compromise: fine enough that the
# 16 mm and f/11 spikes in this library stay visible, coarse enough that a 3 px
# bar is still clickable behind a slider track.
_HISTOGRAM_BUCKETS = 24

_UPDATED_RE = re.compile(r"(\d+)\s+image files? updated")
_FAILED_RE = re.compile(r"(\d+)\s+files? weren't updated due to errors")


# ---------------------------------------------------------------------------
# Path safety
# ---------------------------------------------------------------------------


_ROOTS_CACHE: Dict[Tuple[Any, ...], Tuple[Path, ...]] = {}


def _allowed_roots(include_trash: bool = False) -> Tuple[Path, ...]:
    """
    Resolve the directories a request may address.

    Memoised on the configured root values: they are static config, and re-resolving them
    per candidate path turned a 500-path batch into ~1500 `resolve()` syscalls on the event
    loop. The cache is keyed on the raw values rather than computed once at import so a test
    that monkeypatches CULL_ROOTS/TRASH_PATH still gets its own roots.

    Args:
        include_trash: Also permit paths inside TRASH_PATH (restore previews).

    Returns:
        Tuple of resolved root directories.
    """
    key = (tuple(str(r) for r in CULL_ROOTS.values()), str(TRASH_PATH), include_trash)
    cached = _ROOTS_CACHE.get(key)
    if cached is not None:
        return cached

    roots: List[Path] = []
    for raw_root in CULL_ROOTS.values():
        try:
            roots.append(Path(raw_root).expanduser().resolve())
        except (OSError, RuntimeError):
            continue
    if include_trash:
        try:
            roots.append(Path(TRASH_PATH).expanduser().resolve())
        except (OSError, RuntimeError):
            pass

    resolved_roots = tuple(roots)
    _ROOTS_CACHE[key] = resolved_roots
    return resolved_roots


def _resolve_in_roots(raw: str, *, include_trash: bool = False) -> Path:
    """
    Resolve a client-supplied path and require it to live under a permitted root.

    Args:
        raw: Path string from a query parameter or request body.
        include_trash: Also accept paths inside TRASH_PATH. Read-only surfaces set
            this so the trash drawer can preview what it is about to restore; no
            mutating endpoint ever does.

    Returns:
        The resolved absolute path.

    Raises:
        HTTPException: 400 when the path is malformed or outside every root.
    """
    try:
        resolved = Path(raw).expanduser().resolve()
    except (OSError, RuntimeError, TypeError, ValueError) as exc:
        raise HTTPException(status_code=400, detail=f"Invalid path: {exc}") from exc

    for root in _allowed_roots(include_trash):
        if root in resolved.parents:
            return resolved

    raise HTTPException(
        status_code=400,
        detail="Path is outside the permitted photo roots (Final/Staging)",
    )


def _resolve_batch(paths: Sequence[str], limit: int) -> List[Path]:
    """
    Validate a batch of client-supplied paths. Blocking — call via `_resolve_batch_async`.

    Args:
        paths: Raw path strings.
        limit: Maximum accepted batch size.

    Returns:
        Resolved paths in the order supplied.

    Raises:
        HTTPException: 400 on an empty/oversized batch or an out-of-root path.
    """
    if not paths:
        raise HTTPException(status_code=400, detail="No paths supplied")
    if len(paths) > limit:
        raise HTTPException(
            status_code=400, detail=f"Too many paths: {len(paths)} (max {limit})"
        )
    return [_resolve_in_roots(p) for p in paths]


async def _resolve_batch_async(paths: Sequence[str], limit: int) -> List[Path]:
    """
    Resolve a whole batch off the event loop.

    `Path.resolve()` hits the filesystem per path, so a 500-path rating write is 500 stat
    walks — enough to stall the SSE job stream sharing this loop. The HTTPException raised
    inside the thread propagates unchanged.
    """
    return await asyncio.to_thread(_resolve_batch, paths, limit)


# ---------------------------------------------------------------------------
# Filter model
# ---------------------------------------------------------------------------


@dataclass
class PhotoFilters:
    """The full filter set shared by the list and facet endpoints."""

    root: Optional[str] = None
    rating: List[int] = field(default_factory=list)
    rating_min: Optional[int] = None
    include_rejected: bool = False
    label: List[str] = field(default_factory=list)
    orientation: Optional[str] = None
    iso_min: Optional[int] = None
    iso_max: Optional[int] = None
    aperture_min: Optional[float] = None
    aperture_max: Optional[float] = None
    shutter_min: Optional[float] = None
    shutter_max: Optional[float] = None
    focal_min: Optional[float] = None
    focal_max: Optional[float] = None
    camera_model: List[str] = field(default_factory=list)
    lens_model: List[str] = field(default_factory=list)
    date_from: Optional[str] = None
    date_to: Optional[str] = None
    q: Optional[str] = None
    has_sidecar: Optional[bool] = None


def photo_filters(
    root: Optional[Literal["final", "staging"]] = Query(default=None),
    rating: Optional[List[int]] = Query(default=None),
    rating_min: Optional[int] = Query(default=None, ge=0, le=5),
    include_rejected: bool = Query(default=False),
    label: Optional[List[str]] = Query(default=None),
    orientation: Optional[Literal["landscape", "portrait", "square"]] = Query(default=None),
    iso_min: Optional[int] = Query(default=None),
    iso_max: Optional[int] = Query(default=None),
    aperture_min: Optional[float] = Query(default=None),
    aperture_max: Optional[float] = Query(default=None),
    shutter_min: Optional[float] = Query(default=None),
    shutter_max: Optional[float] = Query(default=None),
    focal_min: Optional[float] = Query(default=None),
    focal_max: Optional[float] = Query(default=None),
    camera_model: Optional[List[str]] = Query(default=None),
    lens_model: Optional[List[str]] = Query(default=None),
    date_from: Optional[str] = Query(default=None),
    date_to: Optional[str] = Query(default=None),
    q: Optional[str] = Query(default=None),
    has_sidecar: Optional[bool] = Query(default=None),
) -> PhotoFilters:
    """FastAPI dependency assembling the query string into a :class:`PhotoFilters`."""
    return PhotoFilters(
        root=root,
        rating=list(rating or []),
        rating_min=rating_min,
        include_rejected=include_rejected,
        label=list(label or []),
        orientation=orientation,
        iso_min=iso_min,
        iso_max=iso_max,
        aperture_min=aperture_min,
        aperture_max=aperture_max,
        shutter_min=shutter_min,
        shutter_max=shutter_max,
        focal_min=focal_min,
        focal_max=focal_max,
        camera_model=list(camera_model or []),
        lens_model=list(lens_model or []),
        date_from=date_from,
        date_to=date_to,
        q=q,
        has_sidecar=has_sidecar,
    )


# ---------------------------------------------------------------------------
# WHERE-clause composition
#
# One dimension per filter control. Building the clauses as a dimension -> parts
# map is what makes faceting a single code path: a facet re-runs the same builder
# with its own dimension dropped, so the option the user is currently looking at
# is never narrowed by itself.
# ---------------------------------------------------------------------------

Clause = Tuple[List[str], List[Any]]


def _in_clause(column: str, values: Sequence[Any]) -> Clause:
    """Build an ``IN (...)`` fragment with generated placeholders (never interpolation)."""
    placeholders = ",".join("?" for _ in values)
    return [f"{column} IN ({placeholders})"], list(values)


_BARE_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


def _inclusive_date_to(value: Optional[str]) -> Optional[str]:
    """
    Make a bare ``YYYY-MM-DD`` upper bound cover the whole of that day.

    ``date_taken`` is stored as ``YYYY-MM-DDTHH:MM:SSZ`` and compared as a string, so
    ``date_taken <= '2025-08-31'`` is lexically false for every photo shot on the 31st —
    the last day of any range silently vanished from the listing while the date rail's
    own count still included it. Anything that already carries a time component is
    passed through untouched.

    Args:
        value: The raw ``date_to`` filter value, or None.

    Returns:
        The value with an end-of-day time appended when it was a bare date; otherwise
        the value unchanged.
    """
    if value is not None and _BARE_DATE_RE.match(value):
        return f"{value}T23:59:59Z"
    return value


def _clauses(filters: PhotoFilters) -> Dict[str, Clause]:
    """
    Translate the filter set into per-dimension SQL fragments and bound parameters.

    Args:
        filters: The active filter set.

    Returns:
        Mapping of dimension name → (SQL fragments, bound parameters). Dimensions
        with no active filter are omitted.
    """
    out: Dict[str, Clause] = {}

    if filters.root:
        out["root"] = (["root = ?"], [filters.root])

    rating_parts: List[str] = []
    rating_params: List[Any] = []
    if filters.rating:
        frags, params = _in_clause("COALESCE(rating, 0)", filters.rating)
        rating_parts += frags
        rating_params += params
    if filters.rating_min is not None:
        rating_parts.append("COALESCE(rating, 0) >= ?")
        rating_params.append(filters.rating_min)
    # A reject (`xmp:Rating` = -1) is a judgement, not a deletion: the file stays exactly
    # where it is and stays selectable. It is simply out of the default result set, the
    # same way a trashed row is — otherwise culling never visibly shortens the pass.
    #
    # This lives in the *rating* dimension on purpose. The facet endpoint recomputes each
    # dimension with its own filter excluded, so `exclude="rating"` drops this clause too
    # and the rating facet can report a truthful `-1` count while every other facet stays
    # reject-free. Asking for rejects explicitly (`rating=-1`) also wins over the default.
    if not filters.include_rejected and REJECTED not in filters.rating:
        rating_parts.append("COALESCE(rating, 0) >= 0")
    if rating_parts:
        out["rating"] = (rating_parts, rating_params)

    if filters.label:
        out["label"] = _in_clause("COALESCE(label, '')", filters.label)

    if filters.orientation:
        out["orientation"] = (["orientation = ?"], [filters.orientation])

    for dim, column, low, high in (
        ("iso", "iso", filters.iso_min, filters.iso_max),
        ("aperture", "aperture_f", filters.aperture_min, filters.aperture_max),
        ("shutter", "shutter_s", filters.shutter_min, filters.shutter_max),
        ("focal", "focal_mm", filters.focal_min, filters.focal_max),
        ("date", "date_taken", filters.date_from, _inclusive_date_to(filters.date_to)),
    ):
        parts: List[str] = []
        params: List[Any] = []
        if low is not None:
            parts.append(f"{column} >= ?")
            params.append(low)
        if high is not None:
            parts.append(f"{column} <= ?")
            params.append(high)
        if parts:
            out[dim] = (parts, params)

    if filters.camera_model:
        out["camera_model"] = _in_clause("COALESCE(camera_model, '')", filters.camera_model)

    if filters.lens_model:
        out["lens_model"] = _in_clause("COALESCE(lens_model, '')", filters.lens_model)

    if filters.q:
        out["q"] = (["filename LIKE ? ESCAPE '\\'"], [f"%{_escape_like(filters.q)}%"])

    if filters.has_sidecar is not None:
        out["has_sidecar"] = (["has_sidecar = ?"], [1 if filters.has_sidecar else 0])

    return out


def _escape_like(value: str) -> str:
    """Escape SQL LIKE wildcards so a filename search for '_' is literal."""
    return value.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


def _where_parts(
    filters: PhotoFilters, exclude: Optional[str] = None
) -> Tuple[List[str], List[Any]]:
    """
    Flatten the per-dimension clauses into one list, minus the excluded dimension.

    Deliberately free of ``present = 1``: the fragments name only PhotoRow columns,
    so the exact same list also filters the trash branch of the listing (whose rows
    are projected into the PhotoRow shape before the filter is applied).

    Args:
        filters: The active filter set.
        exclude: Dimension to leave open — used by faceting.

    Returns:
        Tuple of (SQL fragments, bound parameters).
    """
    parts: List[str] = []
    params: List[Any] = []
    for dim, (frags, dim_params) in _clauses(filters).items():
        if dim == exclude:
            continue
        parts.extend(frags)
        params.extend(dim_params)
    return parts, params


def _where(filters: PhotoFilters, exclude: Optional[str] = None) -> Tuple[str, List[Any]]:
    """
    Compose the WHERE clause for a filter set over the `photos` table.

    ``present = 1`` is always applied: a row whose file has vanished (trashed,
    finalized away, deleted outside the tool) is history, not a browsable photo.

    Args:
        filters: The active filter set.
        exclude: Dimension to leave open — used by faceting.

    Returns:
        Tuple of (SQL fragment without the WHERE keyword, bound parameters).
    """
    parts, params = _where_parts(filters, exclude)
    return " AND ".join(["present = 1", *parts]), params


# ---------------------------------------------------------------------------
# Response models
# ---------------------------------------------------------------------------


class PhotoRow(BaseModel):
    path: str
    filename: str
    root: str
    rating: Optional[int] = None
    label: str = ""
    orientation: Optional[str] = None
    width: Optional[int] = None
    height: Optional[int] = None
    date_taken: Optional[str] = None
    iso: Optional[int] = None
    aperture_f: Optional[float] = None
    shutter_s: Optional[float] = None
    focal_mm: Optional[float] = None
    camera_model: Optional[str] = None
    lens_model: str = ""
    has_sidecar: bool = False
    size: int = 0
    mtime: float = 0.0
    # Set only when `include_trashed=true` surfaces a soft-deleted row. `path` then
    # points at the file's location inside TRASH_PATH (the only place it still
    # exists), and `trash_id` is what /trash/restore takes.
    trashed: bool = False
    trash_id: Optional[int] = None


class PhotoListResponse(BaseModel):
    total: int
    offset: int
    limit: int
    items: List[PhotoRow]


class FacetValue(BaseModel):
    value: str
    count: int


class RangeFacet(BaseModel):
    min: Optional[float] = None
    max: Optional[float] = None


class DateFacet(BaseModel):
    min: Optional[str] = None
    max: Optional[str] = None


class HistogramBin(BaseModel):
    """One bucket of a range-slider histogram. Bounds are in the dimension's own units."""

    lo: float
    hi: float
    count: int


class Histograms(BaseModel):
    """
    Distribution behind each range slider's track.

    Bucket bounds are explicit rather than implied by an index, because the four
    dimensions are not bucketed on the same scale — see :data:`_HISTOGRAM_SPECS`.
    Every bucket is returned, including empty ones: a histogram that silently drops
    its holes lies about where the library is thin.
    """

    iso: List[HistogramBin]
    aperture: List[HistogramBin]
    shutter: List[HistogramBin]
    focal: List[HistogramBin]


class FacetsResponse(BaseModel):
    count: int
    ratings: Dict[str, int]
    labels: List[FacetValue]
    orientations: Dict[str, int]
    camera_models: List[FacetValue]
    lens_models: List[FacetValue]
    iso: RangeFacet
    focal: RangeFacet
    aperture: RangeFacet
    shutter: RangeFacet
    date: DateFacet
    histograms: Histograms


class PhotoMetaResponse(PhotoRow):
    file_size: Optional[int] = None
    sidecar_size: Optional[int] = None
    exif_extra: Dict[str, Any] = Field(default_factory=dict)


class WriteResult(BaseModel):
    updated: int
    errors: int
    messages: List[str]


class RatingRequest(BaseModel):
    paths: List[str]
    rating: int = Field(ge=REJECTED, le=5)


class LabelRequest(BaseModel):
    paths: List[str]
    label: str = ""


class TrashRequest(BaseModel):
    paths: List[str]
    dry_run: bool = False


class TrashEntryModel(BaseModel):
    id: Optional[int] = None
    filename: str
    original_path: str
    rating: Optional[int] = None


class TrashResult(BaseModel):
    trashed: int
    entries: List[TrashEntryModel]
    errors: int
    messages: List[str]


class RejectSummary(BaseModel):
    count: int


class TrashStats(BaseModel):
    count: int
    bytes: int
    oldest_iso: Optional[str] = None
    purgeable: int
    # Adopted orphans whose original location could not be reconstructed. Never purged.
    unrecoverable: int = 0


class TrashListEntry(BaseModel):
    id: int
    original_path: str
    root: str
    trashed_path: str
    sidecar_original_path: Optional[str] = None
    sidecar_trashed_path: Optional[str] = None
    filename: str
    size: int
    rating: Optional[int] = None
    trashed_at: str
    age_days: float
    purgeable: bool
    exists: bool
    # True for an entry adopted by the orphan sweep with no known original path: it can
    # only be recovered by hand, and purge refuses it at any age.
    unrecoverable: bool = False


class TrashListResponse(BaseModel):
    entries: List[TrashListEntry]
    stats: TrashStats


class RestoreRequest(BaseModel):
    ids: List[int]


class RestoreResult(BaseModel):
    restored: int
    errors: int
    messages: List[str]


class PurgeRequest(BaseModel):
    days: Optional[int] = None
    dry_run: bool = False


class PurgeResult(BaseModel):
    purged: int
    bytes: int
    errors: int


class WarmRequest(BaseModel):
    paths: List[str]
    # Warming both tiers costs ~1.3x one tier, not 2x (one decode serves both), so
    # the default leaves the filmstrip AND the viewer hot after a single pass.
    tiers: List[Literal["grid", "view"]] = Field(default_factory=lambda: ["grid", "view"])
    # Legacy single-tier form. When present it wins, so an older client keeps its
    # exact previous behaviour instead of silently doubling its work.
    tier: Optional[Literal["grid", "view"]] = None


class WarmResult(BaseModel):
    generated: int


# ---------------------------------------------------------------------------
# Synchronous DB helpers (all invoked through asyncio.to_thread)
# ---------------------------------------------------------------------------


def _open_conn() -> sqlite3.Connection:
    """Open a connection to the default index DB. Caller must close."""
    return get_db()


def _row_to_dict(row: sqlite3.Row) -> Dict[str, Any]:
    """Normalise a `photos` (or projected `trash`) row into the PhotoRow shape."""
    data = dict(row)
    return {
        "path": data["path"],
        "filename": data["filename"],
        "root": data.get("root") or "final",
        "rating": data.get("rating"),
        "label": data.get("label") or "",
        "orientation": data.get("orientation"),
        "width": data.get("width"),
        "height": data.get("height"),
        "date_taken": data.get("date_taken"),
        "iso": data.get("iso"),
        "aperture_f": data.get("aperture_f"),
        "shutter_s": data.get("shutter_s"),
        "focal_mm": data.get("focal_mm"),
        "camera_model": data.get("camera_model"),
        "lens_model": data.get("lens_model") or "",
        "has_sidecar": bool(data.get("has_sidecar")),
        "size": data.get("size") or 0,
        "mtime": data.get("mtime") or 0.0,
        "trashed": bool(data.get("trashed")),
        "trash_id": data.get("trash_id"),
    }


# A trash entry projected into the PhotoRow column shape, in `_ROW_COLUMNS` order.
#
# The LEFT JOIN is what makes filtering and sorting honest: soft-deleting a photo
# only flips its `photos` row to present = 0, so the full EXIF is still there under
# the ORIGINAL path and the trashed row can be placed at its true position in a
# date_taken sort. An entry the join cannot resolve (an orphan adopted with no known
# origin) keeps NULLs, which is exactly right — a NULL fails every range comparison,
# so an active ISO/date/lens filter drops it rather than parading an unverifiable
# row through a narrowed view.
_TRASH_ROW_SELECT = """
    SELECT
        t.trashed_path             AS path,
        t.filename                 AS filename,
        COALESCE(p.root, t.root)   AS root,
        COALESCE(p.rating, t.rating) AS rating,
        COALESCE(p.label, '')      AS label,
        p.orientation              AS orientation,
        p.width                    AS width,
        p.height                   AS height,
        p.date_taken               AS date_taken,
        p.iso                      AS iso,
        p.aperture_f               AS aperture_f,
        p.shutter_s                AS shutter_s,
        p.focal_mm                 AS focal_mm,
        p.camera_model             AS camera_model,
        COALESCE(p.lens_model, '') AS lens_model,
        CASE WHEN t.sidecar_trashed_path IS NOT NULL THEN 1 ELSE 0 END AS has_sidecar,
        t.size                     AS size,
        COALESCE(p.mtime, 0.0)     AS mtime,
        1                          AS trashed,
        t.id                       AS trash_id
    FROM trash t
    LEFT JOIN photos p ON p.path = t.original_path
"""


def _list_source_sql(filters: PhotoFilters, include_trashed: bool) -> Tuple[str, List[Any]]:
    """
    Build the row source for the listing: live rows, optionally unioned with trash.

    Both branches expose the identical column list, so one ORDER BY / LIMIT wraps
    them and a trashed photo lands in its natural sort position rather than being
    appended as a second list.

    Args:
        filters: Active filter set.
        include_trashed: Merge soft-deleted rows into the source.

    Returns:
        Tuple of (parenthesised SQL sub-select, bound parameters).
    """
    parts, params = _where_parts(filters)
    live_where = " AND ".join(["present = 1", *parts])
    live = f"SELECT {_ROW_COLUMNS}, 0 AS trashed, NULL AS trash_id FROM photos WHERE {live_where}"
    if not include_trashed:
        return f"({live})", list(params)

    trash_where = " AND ".join(parts) if parts else "1 = 1"
    trashed = f"SELECT * FROM ({_TRASH_ROW_SELECT}) WHERE {trash_where}"
    return f"({live} UNION ALL {trashed})", [*params, *params]


def _query_list(
    filters: PhotoFilters,
    sort: str,
    order: str,
    limit: int,
    offset: int,
    include_trashed: bool = False,
) -> Dict[str, Any]:
    """
    Run the filtered/sorted/paged list query.

    Args:
        filters: Active filter set.
        sort: Whitelisted sort key (see ``_SORT_COLUMNS``).
        order: 'asc' or 'desc'.
        limit: Page size.
        offset: Page offset.
        include_trashed: Merge soft-deleted rows into the result.

    Returns:
        Dict with keys total, offset, limit, items (list of PhotoRow dicts).
    """
    source, params = _list_source_sql(filters, include_trashed)
    column = _SORT_COLUMNS[sort]
    direction = "DESC" if order == "desc" else "ASC"

    conn = _open_conn()
    try:
        total = conn.execute(
            f"SELECT COUNT(*) AS n FROM {source}", params
        ).fetchone()["n"]
        rows = conn.execute(
            f"""
            SELECT * FROM {source}
            ORDER BY {column} {direction}, path {direction}
            LIMIT ? OFFSET ?
            """,
            [*params, limit, offset],
        ).fetchall()
        return {
            "total": total,
            "offset": offset,
            "limit": limit,
            "items": [_row_to_dict(r) for r in rows],
        }
    finally:
        conn.close()


def _facet_values(
    conn: sqlite3.Connection,
    filters: PhotoFilters,
    dimension: str,
    expression: str,
    *,
    drop_empty: bool = True,
) -> List[Dict[str, Any]]:
    """Group-by facet for a categorical dimension, with that dimension left open."""
    where, params = _where(filters, exclude=dimension)
    extra = f" AND {expression} IS NOT NULL AND {expression} != ''" if drop_empty else ""
    rows = conn.execute(
        f"""
        SELECT {expression} AS value, COUNT(*) AS count
        FROM photos
        WHERE {where}{extra}
        GROUP BY value
        ORDER BY count DESC, value ASC
        """,
        params,
    ).fetchall()
    return [{"value": str(r["value"]), "count": r["count"]} for r in rows]


def _facet_range(
    conn: sqlite3.Connection,
    filters: PhotoFilters,
    dimension: str,
    column: str,
) -> Dict[str, Any]:
    """Min/max facet for a numeric or date dimension, with that dimension left open."""
    where, params = _where(filters, exclude=dimension)
    row = conn.execute(
        f"SELECT MIN({column}) AS lo, MAX({column}) AS hi FROM photos WHERE {where}",
        params,
    ).fetchone()
    return {"min": row["lo"], "max": row["hi"]}


@dataclass(frozen=True)
class _HistogramSpec:
    """How one range dimension is bucketed for its slider histogram."""

    dimension: str
    column: str
    log: bool


# Bucket each dimension the way it is actually read, verified against the live
# 3,119-frame index rather than assumed:
#
#   iso      log — 160..12800 is 6.3 stops and 60 % of frames sit at ISO 640.
#                  Linear buckets put 1,865 rows in bucket 0 and leave 11 of 24
#                  empty; log spreads them over 20 populated buckets.
#   shutter  log — 1/10000 s..25 s. Linear is a bar chart of one bar: 3,098 of
#                  3,119 rows land in bucket 0 (99.3 %). Log peaks at 19.5 %.
#   focal    log — 16..300 mm from a 16-80 and a 70-300. Linear buries 53.5 % in
#                  bucket 0 (the 16 mm stop of the wide zoom) and empties five
#                  buckets; log peaks at 31.8 % with none empty. The brief guessed
#                  linear would do; the measurement says otherwise.
#   aperture linear — f/2.8..f/26 is only 3.2 octaves, so both scales peak at the
#                  same 24.1 % f/11 spike. Linear stays, because a linear track is
#                  what an aperture slider draws.
_HISTOGRAM_SPECS = (
    _HistogramSpec("iso", "iso", True),
    _HistogramSpec("aperture", "aperture_f", False),
    _HistogramSpec("shutter", "shutter_s", True),
    _HistogramSpec("focal", "focal_mm", True),
)

# Resolved once per process: SQLite's math functions are a compile-time option.
_LN_FUNCTION: Optional[str] = None


def _safe_ln(value: Any) -> Optional[float]:
    """Natural log for the UDF fallback; NULL rather than an exception off-domain."""
    try:
        return math.log(value) if value is not None and value > 0 else None
    except (TypeError, ValueError):
        return None


def _ln_function(conn: sqlite3.Connection) -> str:
    """
    Name of a natural-log SQL function usable on this connection.

    ``ln()`` needs ``SQLITE_ENABLE_MATH_FUNCTIONS``, which is on in the interpreter
    this ships with but is not guaranteed. Rather than let a histogram be a build-flag
    lottery, a Python UDF stands in where the builtin is absent; the scan is a few
    thousand rows, so the callback cost is noise.

    Args:
        conn: Connection the histogram query will run on.

    Returns:
        The SQL function name to call with one argument.
    """
    global _LN_FUNCTION
    if _LN_FUNCTION is None:
        try:
            conn.execute("SELECT ln(2.0)").fetchone()
            _LN_FUNCTION = "ln"
        except sqlite3.Error:
            _LN_FUNCTION = "pf_ln"
    if _LN_FUNCTION == "pf_ln":
        conn.create_function("pf_ln", 1, _safe_ln)
    return _LN_FUNCTION


def _round_sig(value: float, digits: int = 6) -> float:
    """Trim float noise off a bucket edge (1/10000 s must not print as 0.000100000002)."""
    return float(f"{value:.{digits}g}")


def _histogram(
    conn: sqlite3.Connection, filters: PhotoFilters, spec: _HistogramSpec
) -> List[Dict[str, Any]]:
    """
    Bucket one numeric dimension over the current selection.

    Like the range facet it accompanies, the dimension's own filter is left open —
    the histogram behind a slider must show the whole library's spread, not just the
    slice already selected, or dragging the handle repaints the chart under the hand
    doing the dragging.

    Args:
        conn: Open index connection.
        filters: Active filter set.
        spec: Dimension, column and scale.

    Returns:
        List of ``{lo, hi, count}`` dicts, one per bucket including empty ones.
        Empty when the selection has no usable value for this dimension.
    """
    where, params = _where(filters, exclude=spec.dimension)
    # Log bucketing needs a strictly positive domain; a 0 s exposure is nonsense
    # data anyway and would otherwise take the whole histogram out with a NULL bound.
    guard = f"{spec.column} IS NOT NULL" + (f" AND {spec.column} > 0" if spec.log else "")

    bounds = conn.execute(
        f"""
        SELECT MIN({spec.column}) AS lo, MAX({spec.column}) AS hi, COUNT(*) AS n
        FROM photos WHERE {where} AND {guard}
        """,
        params,
    ).fetchone()

    low, high, total = bounds["lo"], bounds["hi"], bounds["n"]
    if not total or low is None or high is None:
        return []
    if high <= low:
        return [{"lo": _round_sig(low), "hi": _round_sig(high), "count": total}]

    buckets = _HISTOGRAM_BUCKETS
    if spec.log:
        value_expr = f"{_ln_function(conn)}({spec.column})"
        origin, span = math.log(low), math.log(high) - math.log(low)
        edges = [low * (high / low) ** (i / buckets) for i in range(buckets + 1)]
    else:
        value_expr = spec.column
        origin, span = low, high - low
        edges = [low + span * i / buckets for i in range(buckets + 1)]

    # Clamped so the max value lands in the last bucket rather than one past it.
    bucket_expr = (
        f"MIN({buckets - 1}, MAX(0, CAST({buckets} * ({value_expr} - ?) / ? AS INTEGER)))"
    )
    rows = conn.execute(
        f"""
        SELECT {bucket_expr} AS bucket, COUNT(*) AS count
        FROM photos WHERE {where} AND {guard}
        GROUP BY bucket
        """,
        [origin, span, *params],
    ).fetchall()

    counts = [0] * buckets
    for row in rows:
        index = row["bucket"]
        if index is not None and 0 <= index < buckets:
            counts[index] = row["count"]

    return [
        {"lo": _round_sig(edges[i]), "hi": _round_sig(edges[i + 1]), "count": counts[i]}
        for i in range(buckets)
    ]


def _query_facets(filters: PhotoFilters) -> Dict[str, Any]:
    """
    Compute every facet for the current filter set.

    Each facet is evaluated with all *other* filters applied but its own dimension
    left open, so the UI never offers an option that would yield zero rows and never
    hides the option the user just picked.

    Args:
        filters: Active filter set.

    Returns:
        Dict matching :class:`FacetsResponse`.
    """
    conn = _open_conn()
    try:
        where, params = _where(filters)
        count = conn.execute(
            f"SELECT COUNT(*) AS n FROM photos WHERE {where}", params
        ).fetchone()["n"]

        rating_where, rating_params = _where(filters, exclude="rating")
        rating_rows = conn.execute(
            f"""
            SELECT COALESCE(rating, 0) AS value, COUNT(*) AS count
            FROM photos WHERE {rating_where}
            GROUP BY value
            """,
            rating_params,
        ).fetchall()
        ratings = {str(n): 0 for n in range(REJECTED, 6)}
        for row in rating_rows:
            key = str(int(row["value"]))
            if key in ratings:
                ratings[key] = row["count"]

        orientation_rows = _facet_values(conn, filters, "orientation", "orientation")
        orientations = {name: 0 for name in ("landscape", "portrait", "square")}
        for item in orientation_rows:
            if item["value"] in orientations:
                orientations[item["value"]] = item["count"]

        return {
            "count": count,
            "ratings": ratings,
            "orientations": orientations,
            "labels": _facet_values(conn, filters, "label", "label"),
            "camera_models": _facet_values(conn, filters, "camera_model", "camera_model"),
            "lens_models": _facet_values(conn, filters, "lens_model", "lens_model"),
            "iso": _facet_range(conn, filters, "iso", "iso"),
            "focal": _facet_range(conn, filters, "focal", "focal_mm"),
            "aperture": _facet_range(conn, filters, "aperture", "aperture_f"),
            "shutter": _facet_range(conn, filters, "shutter", "shutter_s"),
            "date": _facet_range(conn, filters, "date", "date_taken"),
            "histograms": {
                spec.dimension: _histogram(conn, filters, spec)
                for spec in _HISTOGRAM_SPECS
            },
        }
    finally:
        conn.close()


def _lookup_row(raw: str, resolved: Path) -> Optional[Dict[str, Any]]:
    """
    Fetch one indexed row by exact path.

    The index stores the path as the scanner saw it (``FINAL_PATH / name``), which can
    differ in spelling from ``Path.resolve()`` when a parent is a symlink, so both
    spellings are tried. Deliberately NOT a filename fallback: two roots can hold the
    same filename, and answering with the wrong file's metadata during a cull is how a
    keeper gets deleted.

    Args:
        raw: Path exactly as the client sent it.
        resolved: The same path after resolution.

    Returns:
        PhotoRow dict, or None when the photo is not indexed.
    """
    conn = _open_conn()
    try:
        row = conn.execute(
            f"SELECT {_ROW_COLUMNS} FROM photos WHERE path = ? OR path = ? LIMIT 1",
            (raw, str(resolved)),
        ).fetchone()
        return _row_to_dict(row) if row is not None else None
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# exiftool helpers
# ---------------------------------------------------------------------------


def _chunk(items: Sequence[Path], size: int) -> List[List[Path]]:
    """Split a sequence into fixed-size chunks (keeps the exiftool argv bounded)."""
    return [list(items[i:i + size]) for i in range(0, len(items), size)]


def _write_tags(paths: Sequence[Path], tag_args: Sequence[str]) -> Dict[str, Any]:
    """
    Apply XMP tag writes to a batch of files with as few exiftool processes as possible.

    exiftool takes many paths per invocation, so a 200-photo batch is one process
    rather than 200 — the difference between "instant" and "a coffee break". The exit
    code is authoritative: a non-zero status means at least one file failed, and those
    files are reported as errors instead of being silently counted as written.

    Args:
        paths: Resolved photo paths to write.
        tag_args: exiftool tag assignments, e.g. ``["-XMP-xmp:Rating=4"]``. An empty
            value clears the tag.

    Returns:
        Dict with keys updated (int), errors (int), messages (list of str).
    """
    updated = 0
    errors = 0
    messages: List[str] = []

    for batch in _chunk(paths, _EXIFTOOL_CHUNK):
        cmd = ["exiftool", "-overwrite_original", *tag_args, *(str(p) for p in batch)]
        try:
            proc = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=_EXIFTOOL_WRITE_TIMEOUT,
            )
        except FileNotFoundError:
            errors += len(batch)
            messages.append("exiftool not found — install it with `brew install exiftool`")
            continue
        except subprocess.TimeoutExpired:
            errors += len(batch)
            messages.append(f"exiftool timed out writing {len(batch)} file(s)")
            continue

        stdout = proc.stdout or ""
        stderr = (proc.stderr or "").strip()

        match = _UPDATED_RE.search(stdout)
        batch_updated = int(match.group(1)) if match else (len(batch) if proc.returncode == 0 else 0)
        failed_match = _FAILED_RE.search(stdout)
        batch_failed = int(failed_match.group(1)) if failed_match else 0

        if proc.returncode != 0 and batch_failed == 0:
            # Non-zero without a parseable failure count: treat the whole batch as failed.
            batch_failed = max(0, len(batch) - batch_updated)

        updated += batch_updated
        errors += batch_failed
        if batch_failed or (proc.returncode != 0):
            messages.append(stderr or f"exiftool exited {proc.returncode}")
        elif stderr:
            logger.debug("exiftool stderr (non-fatal): %s", stderr)

    return {"updated": updated, "errors": errors, "messages": messages}


def _write_and_reindex(paths: Sequence[Path], tag_args: Sequence[str]) -> Dict[str, Any]:
    """Write tags, then re-read the touched files into the index so the UI agrees."""
    result = _write_tags(paths, tag_args)
    if result["updated"]:
        try:
            reindex_paths(paths)
        except Exception as exc:  # noqa: BLE001 - a stale row must not fail a good write
            logger.error("Reindex after metadata write failed: %s", exc)
            result["messages"].append(f"Metadata written, but the index refresh failed: {exc}")
    return result


def _read_exif_extra(path: Path) -> Dict[str, Any]:
    """
    Best-effort full metadata dump for the info panel.

    Args:
        path: Resolved photo path.

    Returns:
        The first exiftool JSON object, or an empty dict on any failure — this is
        a display nicety and must never turn into a 500.
    """
    try:
        proc = subprocess.run(
            ["exiftool", "-j", "-G0:1", str(path)],
            capture_output=True,
            text=True,
            timeout=_EXIFTOOL_READ_TIMEOUT,
        )
        if proc.returncode != 0 or not proc.stdout.strip():
            return {}
        parsed = json.loads(proc.stdout)
    except (OSError, subprocess.SubprocessError, json.JSONDecodeError) as exc:
        logger.debug("exiftool read failed for %s: %s", path, exc)
        return {}
    if isinstance(parsed, list) and parsed and isinstance(parsed[0], dict):
        return parsed[0]
    return {}


def _meta_payload(raw: str, path: Path) -> Optional[Dict[str, Any]]:
    """Assemble the /meta response: indexed row + live file sizes + exiftool dump."""
    row = _lookup_row(raw, path)
    if row is None:
        return None

    try:
        file_size: Optional[int] = path.stat().st_size
    except OSError:
        file_size = None

    sidecar = path.with_suffix(EDIT_SIDECAR_SUFFIX)
    try:
        sidecar_size: Optional[int] = sidecar.stat().st_size if sidecar.is_file() else None
    except OSError:
        sidecar_size = None

    row["file_size"] = file_size
    row["sidecar_size"] = sidecar_size
    row["exif_extra"] = _read_exif_extra(path)
    return row


# ---------------------------------------------------------------------------
# Endpoints — browsing
# ---------------------------------------------------------------------------


@router.get("/api/photos", response_model=PhotoListResponse)
async def photos_list(
    filters: PhotoFilters = Depends(photo_filters),
    sort: Literal["date_taken", "filename", "rating", "iso", "focal_mm"] = Query(
        default="date_taken"
    ),
    order: Literal["asc", "desc"] = Query(default="asc"),
    limit: int = Query(default=2000, ge=1, le=10000),
    offset: int = Query(default=0, ge=0),
    include_trashed: bool = Query(default=False),
) -> PhotoListResponse:
    """
    List indexed photos from the Final and Staging roots, filtered/sorted/paged.

    Only rows whose file is still present are returned; trashed or finalized-away
    photos stay in the index as history but never appear here.

    ``include_trashed=true`` merges the soft-deleted rows back in *in their natural
    sort position* — the undo affordance for a cull, not a separate drawer. Such a
    row carries ``trashed: true``, a ``trash_id`` for /trash/restore, and a ``path``
    pointing inside TRASH_PATH (which /thumb, and only /thumb, will serve). Filters
    still apply: a trashed row is matched on the metadata its original index row
    retained, and is dropped by any filter that metadata cannot answer.
    """
    data = await asyncio.to_thread(
        _query_list, filters, sort, order, limit, offset, include_trashed
    )
    return PhotoListResponse(
        total=data["total"],
        offset=data["offset"],
        limit=data["limit"],
        items=[PhotoRow(**item) for item in data["items"]],
    )


@router.get("/api/photos/facets", response_model=FacetsResponse)
async def photos_facets(
    filters: PhotoFilters = Depends(photo_filters),
) -> FacetsResponse:
    """
    Counts and ranges per filter dimension for the current selection.

    Each facet applies every filter except its own, so the option list stays
    navigable: picking "5 stars" does not collapse the rating chips to a single row.
    The `histograms` block follows the same rule and gives each range slider the
    distribution to draw behind its track.
    """
    data = await asyncio.to_thread(_query_facets, filters)
    return FacetsResponse(
        count=data["count"],
        ratings=data["ratings"],
        orientations=data["orientations"],
        labels=[FacetValue(**v) for v in data["labels"]],
        camera_models=[FacetValue(**v) for v in data["camera_models"]],
        lens_models=[FacetValue(**v) for v in data["lens_models"]],
        iso=RangeFacet(**data["iso"]),
        focal=RangeFacet(**data["focal"]),
        aperture=RangeFacet(**data["aperture"]),
        shutter=RangeFacet(**data["shutter"]),
        date=DateFacet(**data["date"]),
        histograms=Histograms(
            **{
                dimension: [HistogramBin(**b) for b in bins]
                for dimension, bins in data["histograms"].items()
            }
        ),
    )


@router.get("/api/photos/thumb", response_class=FileResponse)
async def photos_thumb(
    request: Request,
    path: str = Query(...),
    tier: Literal["grid", "view"] = Query(default="grid"),
    v: Optional[str] = Query(default=None),
) -> Response:
    """
    Serve a cached JPEG thumbnail, generating it on a cache miss.

    The ETag is the content-addressed cache key over (path, mtime, size, tier), so
    the response is safely `immutable`: an edited or re-rated photo gets a new key
    and therefore a new URL. `v` is the client-side cache-buster and is ignored here.

    TRASH_PATH is addressable here (and only here): the trash drawer has to show what
    it is offering to restore. Reading a trashed file is harmless — nothing about this
    endpoint can move, write or delete one.
    """
    if tier not in THUMB_SIZES:
        raise HTTPException(status_code=400, detail=f"Unknown thumbnail tier: {tier}")

    source = await asyncio.to_thread(_resolve_in_roots, path, include_trash=True)
    if not await asyncio.to_thread(source.is_file):
        raise HTTPException(status_code=404, detail="Source photo not found")

    try:
        etag = await asyncio.to_thread(thumbs.cache_key, source, tier)
    except (OSError, KeyError) as exc:
        raise HTTPException(status_code=404, detail=f"Cannot read source photo: {exc}") from exc

    quoted = f'"{etag}"'
    cache_headers = {
        "Cache-Control": "public, max-age=31536000, immutable",
        "ETag": quoted,
    }

    if_none_match = request.headers.get("if-none-match", "")
    if quoted in [candidate.strip() for candidate in if_none_match.split(",")]:
        return Response(status_code=304, headers=cache_headers)

    generated, error = await asyncio.to_thread(thumbs.get_thumb, source, tier)
    if generated is None:
        raise HTTPException(status_code=500, detail=error or "Thumbnail generation failed")

    return FileResponse(str(generated), media_type="image/jpeg", headers=cache_headers)


@router.get("/api/photos/meta", response_model=PhotoMetaResponse)
async def photos_meta(path: str = Query(...)) -> PhotoMetaResponse:
    """
    Full indexed row for one photo plus live file/sidecar sizes and an exiftool dump.

    The exiftool block is best-effort: a failed or slow read yields `exif_extra: {}`
    rather than an error, so the info panel always renders.
    """
    source = await asyncio.to_thread(_resolve_in_roots, path)
    payload = await asyncio.to_thread(_meta_payload, path, source)
    if payload is None:
        raise HTTPException(status_code=404, detail="Photo is not in the index")
    return PhotoMetaResponse(**payload)


# ---------------------------------------------------------------------------
# Endpoints — metadata write-back
# ---------------------------------------------------------------------------


@router.post("/api/photos/rating", response_model=WriteResult)
async def photos_set_rating(body: RatingRequest) -> WriteResult:
    """
    Write an XMP rating (-1 reject, 0 unrated, 1–5 stars) to a batch of photos.

    Rating 0 *clears* the tag rather than writing a literal zero, so an uncalled
    photo is indistinguishable from one that was never rated — which is what
    Photomator, Bridge and Immich all expect. -1 is written literally: it is the
    spec's reject value and the whole point is that it is NOT the absent state.
    """
    paths = await _resolve_batch_async(body.paths, MAX_WRITE_PATHS)
    value = "" if body.rating == 0 else str(body.rating)
    result = await asyncio.to_thread(_write_and_reindex, paths, [f"-XMP-xmp:Rating={value}"])
    return WriteResult(**result)


@router.post("/api/photos/label", response_model=WriteResult)
async def photos_set_label(body: LabelRequest) -> WriteResult:
    """Write an XMP colour label to a batch of photos. An empty string clears it."""
    paths = await _resolve_batch_async(body.paths, MAX_WRITE_PATHS)
    result = await asyncio.to_thread(
        _write_and_reindex, paths, [f"-XMP-xmp:Label={body.label}"]
    )
    return WriteResult(**result)


def _rejected_paths(root: Optional[str]) -> List[Path]:
    """
    Every indexed, present, non-trashed photo currently flagged rejected.

    Read from the index rather than from a client-supplied list on purpose: the whole
    point of the batch step is that it acts on the *judgement*, not on whatever happened
    to be on screen when the button was pressed.

    An already-trashed photo drops out by way of `present = 0` — and, because a reindex
    can lag a trash by a moment, again on the `exists()` check below. Every path is put
    back through `_resolve_in_roots` even though it came from our own database: the index
    is a cache that a `photoflow` run on any root can fill, and "the DB said so" is not a
    reason to hand an arbitrary path to a move. A stale index can therefore only fail to
    offer a purge, never direct one at the wrong file.
    """
    clauses = ["rating = ?", "present = 1"]
    params: List[Any] = [REJECTED]
    if root:
        clauses.append("root = ?")
        params.append(root)
    with get_db() as conn:
        rows = conn.execute(
            f"SELECT path FROM photos WHERE {' AND '.join(clauses)} ORDER BY path", params
        ).fetchall()

    out: List[Path] = []
    for row in rows:
        try:
            resolved = _resolve_in_roots(row["path"])
        except HTTPException:
            logger.warning("Rejected photo outside the permitted roots: %s", row["path"])
            continue
        if resolved.exists():
            out.append(resolved)
    return out


@router.get("/api/photos/rejects", response_model=RejectSummary)
async def photos_rejects(
    root: Optional[Literal["final", "staging"]] = Query(default=None),
) -> RejectSummary:
    """Count the photos awaiting a purge, so the button can say how many it will move."""
    paths = await asyncio.to_thread(_rejected_paths, root)
    return RejectSummary(count=len(paths))


@router.post("/api/photos/rejects/purge", response_model=TrashResult)
async def photos_purge_rejects(
    root: Optional[Literal["final", "staging"]] = Query(default=None),
    dry_run: bool = Query(default=False),
) -> TrashResult:
    """
    Move every rejected photo into the trash in one deliberate step.

    This is the *only* place a cull pass touches the filesystem. Rejecting is a metadata
    write that leaves the file where it is, so a whole pass is reversible by pressing the
    key again; the irreversible-feeling part is batched here, behind a count and a
    confirmation, where it can be previewed with `dry_run` first. Even then "purge" only
    means trash — `photoflow trash restore` still works afterwards.
    """
    paths = await asyncio.to_thread(_rejected_paths, root)
    if not paths:
        return TrashResult(trashed=0, entries=[], errors=0, messages=[])
    result = await asyncio.to_thread(trash_module.trash_photos, paths, dry_run)
    return TrashResult(
        trashed=result["trashed"],
        entries=[TrashEntryModel(**e) for e in result["entries"]],
        errors=result["errors"],
        messages=result["messages"],
    )


# ---------------------------------------------------------------------------
# Endpoints — trash
# ---------------------------------------------------------------------------


@router.post("/api/photos/trash", response_model=TrashResult)
async def photos_trash(body: TrashRequest) -> TrashResult:
    """
    Soft-delete photos: move them (with their .photo-edit sidecars) into the trash.

    Nothing is unlinked — `photoflow trash restore` and the panel's undo both work
    until the entry is explicitly purged past its retention window.
    """
    paths = await _resolve_batch_async(body.paths, MAX_WRITE_PATHS)
    result = await asyncio.to_thread(trash_module.trash_photos, paths, body.dry_run)
    return TrashResult(
        trashed=result["trashed"],
        entries=[TrashEntryModel(**e) for e in result["entries"]],
        errors=result["errors"],
        messages=result["messages"],
    )


@router.get("/api/photos/trash", response_model=TrashListResponse)
async def photos_trash_list(
    limit: int = Query(default=500, ge=1, le=5000),
) -> TrashListResponse:
    """
    List trash entries (newest first) together with the aggregate trash stats.

    Not purely read-only: `list_trash` first adopts any entry directory whose row was lost
    between the move and the commit, so a stranded photo shows up here instead of sitting
    invisible under ~/Pictures. An entry flagged `unrecoverable` has no known original
    location — it can only be recovered by hand, and is never purged.
    """
    entries = await asyncio.to_thread(trash_module.list_trash, limit)
    stats = await asyncio.to_thread(trash_module.trash_stats)
    return TrashListResponse(
        entries=[TrashListEntry(**e) for e in entries],
        stats=TrashStats(**stats),
    )


@router.post("/api/photos/trash/restore", response_model=RestoreResult)
async def photos_trash_restore(body: RestoreRequest) -> RestoreResult:
    """
    Move trashed photos back to their original locations.

    An occupied original path is refused rather than overwritten.
    """
    if not body.ids:
        raise HTTPException(status_code=400, detail="No trash entry ids supplied")
    result = await asyncio.to_thread(trash_module.restore, body.ids)
    return RestoreResult(**result)


@router.post("/api/photos/trash/purge", response_model=PurgeResult)
async def photos_trash_purge(body: Optional[PurgeRequest] = None) -> PurgeResult:
    """
    Permanently delete trash entries older than the retention threshold.

    Age is measured from when the entry was trashed, never from file mtime — a photo
    keeps its capture-time mtime, so an mtime sweep would erase a folder trashed
    seconds ago. Entries inside the window are never touched.
    """
    request_body = body or PurgeRequest()
    days = request_body.days if request_body.days is not None else trash_module.TRASH_RETENTION_DAYS
    if days < 0:
        raise HTTPException(status_code=400, detail="days must be >= 0")
    result = await asyncio.to_thread(trash_module.purge, days, request_body.dry_run)
    return PurgeResult(**result)


# ---------------------------------------------------------------------------
# Endpoints — cache prewarm
# ---------------------------------------------------------------------------


@router.post("/api/photos/warm", response_model=WarmResult)
async def photos_warm(body: WarmRequest) -> WarmResult:
    """
    Pre-generate thumbnails for a batch of paths so stepping through them is instant.

    Defaults to warming both tiers, because they share one decode of the source —
    the filmstrip and the viewer come up hot for barely more than the filmstrip
    alone. Capped at 200 paths per call; already-cached entries cost nothing and
    are not counted as generated (which is counted per tier, not per path).
    """
    paths = await _resolve_batch_async(body.paths, MAX_WARM_PATHS)
    tiers = [body.tier] if body.tier is not None else body.tiers
    generated = await asyncio.to_thread(thumbs.warm_tiers, paths, tiers)
    return WarmResult(generated=generated)
