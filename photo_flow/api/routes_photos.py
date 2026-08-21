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
GET  /api/photos/structure     — one candidate library layout (flat | month | album |
                                 event) as a list of groups that are each a query
GET  /api/photos/thumb         — cached JPEG thumbnail (grid | view tier)
GET  /api/photos/original      — the master's own bytes, for true 1:1 inspection
GET  /api/photos/meta          — one row plus live file sizes and full exiftool dump
POST /api/photos/rating        — batched XMP rating write-back (returns each path's prior value)
POST /api/photos/rating/undo   — restore each path to a rating it held before an earlier write
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
import calendar
import hashlib
import inspect
import json
import logging
import math
import re
import sqlite3
import subprocess
import time
from dataclasses import dataclass, field
from dataclasses import fields as dataclass_fields
from datetime import datetime
from pathlib import Path
from typing import (
    Any,
    Dict,
    List,
    Literal,
    Mapping,
    Optional,
    Sequence,
    Tuple,
    Union,
    get_args,
    get_origin,
    get_type_hints,
)

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

from photo_flow import collections as collections_store
from photo_flow import raw_link
from photo_flow import trash as trash_module
from photo_flow.config import (
    CULL_ROOTS,
    EDIT_SIDECAR_SUFFIX,
    INSTALL,
    RAWS_PATH,
    THUMB_SIZES,
    TRASH_PATH,
)
from photo_flow.index import thumbs
from photo_flow.index.db import get_db
from photo_flow.index.indexer import reindex_paths, unpack_keywords

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
    "path, filename, root, rating, label, keywords, orientation, width, height, date_taken, "
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
    # `XMP-dc:subject`, the flat tag set (decision 0004). An OR-set like camera/lens: a
    # photo matches if it carries ANY of the named tags. See `_clauses` for why this is
    # a plain dimension and not a separate "album" concept.
    keyword: List[str] = field(default_factory=list)


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
    keyword: Optional[List[str]] = Query(default=None),
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
        keyword=list(keyword or []),
    )


# ---------------------------------------------------------------------------
# The filter vocabulary, as data
#
# Saved collections (`photo_flow/collections.py`) store a query as a plain mapping and
# must speak EXACTLY this language — a second, drifting filter vocabulary would be the
# fastest way to make a saved query mean one thing in the URL and another in the file.
# So the spec is DERIVED from `PhotoFilters` rather than restated: adding a field to the
# dataclass makes it savable, and there is no second list to forget.
# ---------------------------------------------------------------------------


def _field_kind(annotation: Any) -> Tuple[str, bool]:
    """
    Reduce one `PhotoFilters` annotation to (scalar kind, is_list).

    Args:
        annotation: The resolved type annotation.

    Returns:
        Tuple of the scalar type name ('str' | 'int' | 'float' | 'bool') and whether the
        field holds a list of them.
    """
    origin = get_origin(annotation)
    if origin is list or origin is List:
        (inner,) = get_args(annotation)
        return _field_kind(inner)[0], True
    if origin is Union:  # Optional[X]
        for arg in get_args(annotation):
            if arg is not type(None):
                return _field_kind(arg)[0], False
    return getattr(annotation, "__name__", str(annotation)), False


def _build_query_spec() -> Dict[str, Tuple[str, bool]]:
    """Map every `PhotoFilters` field to its (scalar kind, is_list) pair."""
    hints = get_type_hints(PhotoFilters)
    return {f.name: _field_kind(hints[f.name]) for f in dataclass_fields(PhotoFilters)}


QUERY_SPEC: Dict[str, Tuple[str, bool]] = _build_query_spec()

# The two fields whose FastAPI signature is a Literal but whose dataclass annotation is a
# bare str. Restated here because a stored query is not routed through that signature.
QUERY_ENUMS: Dict[str, Tuple[str, ...]] = {
    "root": ("final", "staging"),
    "orientation": ("landscape", "portrait", "square"),
}


def _build_query_bounds() -> Dict[str, Tuple[Optional[float], Optional[float]]]:
    """
    Read the numeric bounds straight off the FastAPI dependency's own signature.

    ``Query(ge=..., le=...)`` stores its constraints as ``annotated_types`` markers in
    ``FieldInfo.metadata``. Deriving them here rather than restating them is the same
    tripwire as :data:`QUERY_SPEC`: the URL gate and the saved-query gate cannot drift
    into accepting different value ranges for the same filter, which they did — a stored
    ``rating_min = 99`` was a 200 that resolved to a permanently empty collection while
    ``?rating_min=99`` was a 422.

    Returns:
        Mapping of field name → (minimum, maximum); either bound may be None.
    """
    out: Dict[str, Tuple[Optional[float], Optional[float]]] = {}
    for name, param in inspect.signature(photo_filters).parameters.items():
        low: Optional[float] = None
        high: Optional[float] = None
        for marker in getattr(param.default, "metadata", ()):
            low = getattr(marker, "ge", None) if low is None else low
            high = getattr(marker, "le", None) if high is None else high
        if low is not None or high is not None:
            out[name] = (low, high)
    return out


QUERY_BOUNDS: Dict[str, Tuple[Optional[float], Optional[float]]] = _build_query_bounds()

# Bounds on the ITEMS of a list filter. Not derivable from the dependency signature:
# pydantic applies a `Query(ge=…)` to the list itself, not to its elements, so the one
# place these can live is here. Kept to the dimensions that actually have a closed range —
# `rating` is the reject value plus five stars and nothing else.
QUERY_ITEM_BOUNDS: Dict[str, Tuple[Optional[float], Optional[float]]] = {
    "rating": (REJECTED, 5),
}


def _check_bounds(key: str, value: Any, bounds: Dict[str, Tuple[Optional[float], Optional[float]]]) -> None:
    """
    Reject a numeric value outside its declared range.

    Args:
        key: Field name, for the error message.
        value: The already-coerced value.
        bounds: Either :data:`QUERY_BOUNDS` or :data:`QUERY_ITEM_BOUNDS`.

    Raises:
        ValueError: The value is out of range.
    """
    limits = bounds.get(key)
    if limits is None or isinstance(value, (str, bool)):
        return
    low, high = limits
    if low is not None and value < low:
        raise ValueError(f"{key} must be at least {low:g}")
    if high is not None and value > high:
        raise ValueError(f"{key} must be at most {high:g}")


def _coerce(key: str, kind: str, value: Any) -> Any:
    """
    Coerce one stored value to the kind its filter field declares.

    Args:
        key: Field name, for the error message.
        kind: 'str' | 'int' | 'float' | 'bool'.
        value: The stored value.

    Returns:
        The coerced value.

    Raises:
        ValueError: The value cannot be that kind.
    """
    # bool first, everywhere: it is an int subclass, so `isinstance(True, int)` is True and
    # a stray `true` would otherwise silently become ISO 1.
    if kind == "bool":
        if isinstance(value, bool):
            return value
        raise ValueError(f"{key} must be true or false")
    if isinstance(value, bool):
        raise ValueError(f"{key} must be a {kind}, not a boolean")
    if kind == "str":
        if isinstance(value, str):
            return value
        raise ValueError(f"{key} must be a string")
    if kind == "int":
        if isinstance(value, int):
            return value
        raise ValueError(f"{key} must be an integer")
    if kind == "float":
        if isinstance(value, (int, float)):
            return float(value)
        raise ValueError(f"{key} must be a number")
    raise ValueError(f"{key} has an unsupported type")


def filters_from_mapping(query: Mapping[str, Any], *, strict: bool = True) -> PhotoFilters:
    """
    Build a :class:`PhotoFilters` from a stored collection query.

    Args:
        query: Mapping of filter field name to value (scalars, or lists for the
            multi-select dimensions).
        strict: Raise on an unknown key or an unusable value. The API sets this on
            create/update, where a bad value is a client bug and should be loud. The
            resolve path sets it False, where the same value came from a hand-edited
            file and dropping one filter beats refusing to open the collection.

    Returns:
        The filter set. Fields not present in `query` keep their defaults.

    Raises:
        ValueError: Only when `strict` — unknown key, wrong type, or an illegal enum value.
    """
    filters = PhotoFilters()
    for key, value in query.items():
        spec = QUERY_SPEC.get(key)
        if spec is None:
            if strict:
                raise ValueError(f"Unknown filter: {key}")
            logger.debug("Ignoring unknown filter %r in a saved query", key)
            continue
        kind, is_list = spec
        try:
            if is_list:
                if not isinstance(value, (list, tuple)):
                    raise ValueError(f"{key} must be a list")
                coerced: Any = [_coerce(key, kind, item) for item in value]
                for item in coerced:
                    _check_bounds(key, item, QUERY_ITEM_BOUNDS)
            else:
                coerced = _coerce(key, kind, value)
                allowed = QUERY_ENUMS.get(key)
                if allowed is not None and coerced not in allowed:
                    raise ValueError(f"{key} must be one of {', '.join(allowed)}")
                _check_bounds(key, coerced, QUERY_BOUNDS)
        except ValueError:
            if strict:
                raise
            logger.debug("Ignoring unusable filter %r in a saved query", key)
            continue
        setattr(filters, key, coerced)
    return filters


# ---------------------------------------------------------------------------
# The narrowing model — how a saved collection and the ad-hoc filters compose
#
# Three controls narrow the same list: the collections list (SCOPE), the folder /
# date rail, and the filter panel. Adding saved collections made the conflict real:
# when the scope says `rating_min = 4` and the panel says `rating = [0]`, something
# has to decide what the user sees.
#
# THE RULE, in one line:
#
#     REFINE overrides SCOPE on a shared DIMENSION, and intersects with it on
#     every other. There is no third layer.
#
# Two consequences worth stating, because they are the whole design:
#
# 1. **The folder rail is not a layer, it is a dimension.** `root` and the date tree
#    are columns like any other; a "folder" here has never been a directory (the
#    library is flat — two directories, no nesting). Giving the rail its own
#    precedence tier would invent a hierarchy the filesystem does not have, and
#    would then need its own conflict rule against both neighbours.
#
# 2. **Override, not intersection, is what makes a control inside a scope do
#    anything.** Under strict AND a refinement can only ever REMOVE rows, so every
#    control that widens is dead inside a collection: in "Keepers" (`rating >= 4`)
#    clicking the rating facet's `0` bucket would resolve to nothing, and so would
#    every other bucket below 4. Under override the bucket resolves to exactly what
#    it says. Note this is NOT the same as claiming the facet lists never offer a
#    zero-result option — they do today, unscoped, and the UI renders them (see
#    `RatingFilter`); the claim is only that the scope must not be what makes them
#    dead. And because composition produces ONE effective `PhotoFilters`,
#    `_where(effective, exclude=D)` is already `(scope minus D) AND (refine minus D)`:
#    every existing endpoint keeps working with no second code path.
#
# The merge unit is the `_clauses` DIMENSION, not the field. `rating` and
# `rating_min` are two spellings of one axis, so setting either replaces both;
# `focal_min` and `focal_max` are two ends of one axis, so the panel's range slider
# (which always emits the pair) replaces the pair. One unit, one rule, and it is
# the same unit faceting already keys on.
# ---------------------------------------------------------------------------

# Every `PhotoFilters` field, mapped to the `_clauses` dimension it contributes to.
# `test_narrowing.py` pins both directions: every field is mapped, and every mapped
# dimension is one `_clauses` can actually emit. Add a filter field without adding it
# here and the suite fails rather than the field silently becoming its own layer.
QUERY_DIMENSIONS: Dict[str, str] = {
    "root": "root",
    "rating": "rating",
    "rating_min": "rating",
    "label": "label",
    "orientation": "orientation",
    "iso_min": "iso",
    "iso_max": "iso",
    "aperture_min": "aperture",
    "aperture_max": "aperture",
    "shutter_min": "shutter",
    "shutter_max": "shutter",
    "focal_min": "focal",
    "focal_max": "focal",
    "camera_model": "camera_model",
    "lens_model": "lens_model",
    "date_from": "date",
    "date_to": "date",
    "q": "q",
    "has_sidecar": "has_sidecar",
    "keyword": "keyword",
}

# Fields that are a VIEW preference rather than a dimension, and therefore compose by OR
# across the layers instead of by precedence.
#
# `include_rejected` is the only one, and it is here because it WIDENS. Every dimension
# narrows, which is what makes "whichever layer set it wins" a coherent rule; a widening
# flag has no narrowing to lose, so treating it as a member of the `rating` dimension made
# the sidebar's "Show rejected" switch DELETE a collection's rating filter — inside
# "Keepers" (root=final, rating>=4) flipping the switch went from 172 rows to 2 364, every
# Final photo. The user asked to additionally see rejects and got the collection undone.
#
# It stays a `PhotoFilters` field (that is where `_clauses` reads it, and a collection may
# legitimately save "…, rejects included"); it is simply not a dimension anyone can own.
VIEW_FIELDS: frozenset = frozenset({"include_rejected"})

# Human labels for the narrowing readout. A dimension the user cannot name is a
# dimension they cannot clear.
DIMENSION_LABELS: Dict[str, str] = {
    "root": "Folder",
    "rating": "Rating",
    "label": "Colour label",
    "orientation": "Orientation",
    "iso": "ISO",
    "aperture": "Aperture",
    "shutter": "Shutter",
    "focal": "Focal length",
    "camera_model": "Camera",
    "lens_model": "Lens",
    "date": "Date",
    "q": "Filename",
    "has_sidecar": "Edit history",
    "keyword": "Keyword",
}

# Layer names, in precedence order (later wins a shared dimension).
LAYERS: Tuple[str, str] = ("scope", "refine")


def set_fields(filters: PhotoFilters) -> Dict[str, Any]:
    """
    The fields this layer actually narrows on — everything differing from the default.

    Read from the FIELDS, never from :func:`_clauses`: the clause builder emits a
    ``rating`` entry for the default reject-hiding rule even when the user has set no
    rating filter at all, and mistaking that for "this layer owns the rating dimension"
    would let an empty scope shadow a real one.

    Args:
        filters: One layer's filter set.

    Returns:
        Mapping of field name → value, for the narrowed fields only.
    """
    base = PhotoFilters()
    out: Dict[str, Any] = {}
    for spec in dataclass_fields(PhotoFilters):
        value = getattr(filters, spec.name)
        if value != getattr(base, spec.name):
            out[spec.name] = value
    return out


def set_dimensions(filters: PhotoFilters) -> Dict[str, Dict[str, Any]]:
    """
    The same thing, grouped by dimension.

    Args:
        filters: One layer's filter set.

    Returns:
        Mapping of dimension name → {field: value} for the fields it narrows on.
    """
    out: Dict[str, Dict[str, Any]] = {}
    for name, value in set_fields(filters).items():
        if name in VIEW_FIELDS:
            continue
        out.setdefault(QUERY_DIMENSIONS[name], {})[name] = value
    return out


@dataclass
class Composition:
    """
    The result of composing a scope with a refinement.

    Attributes:
        effective: The single filter set every downstream query runs against.
        sources: Dimension → the layer that supplied it ('scope' or 'refine').
        overridden: Dimensions where refine replaced a clause the scope had set.
        scope_dimensions: What the scope asked for, per dimension, override included.
    """

    effective: PhotoFilters
    sources: Dict[str, str] = field(default_factory=dict)
    overridden: Dict[str, Dict[str, Any]] = field(default_factory=dict)
    scope_dimensions: Dict[str, Dict[str, Any]] = field(default_factory=dict)


def without_dimension(filters: PhotoFilters, dimension: str) -> PhotoFilters:
    """
    The same filter set with one dimension's fields reset to their defaults.

    NOT the same thing as ``_where(filters, exclude=dimension)``, and the difference is
    load-bearing for the ``rating`` dimension. ``exclude=`` drops the SQL clauses, which
    takes the default reject-hiding rule with them — right for a facet, whose counts are
    meant to report the ``-1`` bucket, and wrong for the narrowing readout, which answers
    "what would I see if I cleared this". Resetting the fields instead lets the defaults
    reassert themselves, so the number the readout shows is the number the user gets.

    Args:
        filters: The effective filter set.
        dimension: The dimension to clear.

    Returns:
        A new filter set with that dimension open.
    """
    base = PhotoFilters()
    out = PhotoFilters(**{f.name: getattr(filters, f.name) for f in dataclass_fields(PhotoFilters)})
    for name, owner in QUERY_DIMENSIONS.items():
        if owner == dimension:
            setattr(out, name, getattr(base, name))
    return out


def compose(scope: Optional[PhotoFilters], refine: PhotoFilters) -> Composition:
    """
    Combine a saved collection's query with the ad-hoc filters.

    Per dimension: whichever layer sets it wins, refine first. Dimensions neither layer
    sets stay open; dimensions only one layer sets pass through; dimensions both set
    resolve to refine's, and the scope's values are reported as `overridden` so the UI
    can say so instead of silently swallowing them.

    Args:
        scope: The active collection's filters, or None when browsing the whole library.
        refine: The ad-hoc filter state (folder rail, date tree and filter panel).

    Returns:
        The composition — one effective filter set plus the provenance behind it.
    """
    scope_dims = set_dimensions(scope) if scope is not None else {}
    refine_dims = set_dimensions(refine)

    effective = PhotoFilters()
    sources: Dict[str, str] = {}
    overridden: Dict[str, Dict[str, Any]] = {}

    for dimension, fields_ in scope_dims.items():
        if dimension in refine_dims:
            overridden[dimension] = dict(fields_)
            continue
        for name, value in fields_.items():
            setattr(effective, name, value)
        sources[dimension] = "scope"

    for dimension, fields_ in refine_dims.items():
        for name, value in fields_.items():
            setattr(effective, name, value)
        sources[dimension] = "refine"

    # View flags are not owned by a layer: either layer asking for them is enough. They
    # widen rather than narrow, so there is nothing for a precedence rule to arbitrate.
    for name in VIEW_FIELDS:
        setattr(
            effective,
            name,
            bool(getattr(refine, name)) or bool(scope is not None and getattr(scope, name)),
        )

    return Composition(
        effective=effective,
        sources=sources,
        overridden=overridden,
        scope_dimensions=scope_dims,
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

    # Keywords are the answer to "is a tag a third thing?" — no: it is a DIMENSION, and
    # an album is a collection over it. The stored form is pipe-sentinelled (`|a|b|`), so
    # an exact tag match is one LIKE against the sentinelled needle; a tag containing a
    # LIKE wildcard is escaped like any other user string.
    if filters.keyword:
        frags = " OR ".join("COALESCE(keywords, '') LIKE ? ESCAPE '\\'" for _ in filters.keyword)
        out["keyword"] = (
            [f"({frags})"],
            [f"%|{_escape_like(value)}|%" for value in filters.keyword],
        )

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
    keywords: List[str] = Field(default_factory=list)
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
    keywords: List[FacetValue]
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


class RatingWriteResult(WriteResult):
    """
    A rating write's result, plus what every touched path carried *before* the write.

    ``previous`` is keyed by the path exactly as the client sent it — the client's own
    undo write sends that same spelling back — and defaults an unindexed path to ``0``
    (unrated), the only honest guess when no prior row exists. This is what makes a
    500-photo broadcast reversible: without it, a wrong star value overwrites every
    prior judgement with nothing to restore it from.
    """

    previous: Dict[str, int] = Field(default_factory=dict)


class RatingRequest(BaseModel):
    paths: List[str]
    rating: int = Field(ge=REJECTED, le=5)


class RatingRestoreItem(BaseModel):
    """One path and the rating it should be restored to."""

    path: str
    rating: int = Field(ge=REJECTED, le=5)


class RatingRestoreRequest(BaseModel):
    """
    The inverse of a rating write: restore each path to its own prior value.

    Deliberately per-item rather than one shared ``rating`` — that is the entire point
    of an undo for a batch write, where every photo can be restored to a *different*
    number.
    """

    items: List[RatingRestoreItem]


class RatingRestoreResult(BaseModel):
    restored: int
    errors: int
    #: Paths whose restore could not be confirmed successful. Conservative: if a batch's
    #: exiftool call reports any failure, every path in that batch is listed here rather
    #: than guessing which ones actually failed — an undo must never claim success it
    #: cannot back up.
    failed_paths: List[str]
    messages: List[str]


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
        "keywords": unpack_keywords(data.get("keywords")),
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
        p.keywords                 AS keywords,
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


def _facet_keywords(conn: sqlite3.Connection, filters: PhotoFilters) -> List[Dict[str, Any]]:
    """
    Count each distinct keyword, with the keyword dimension itself left open.

    Split in Python rather than in SQL. The alternative — a recursive CTE over a
    delimited column, or a keywords table — buys nothing at this size: the column is a
    few dozen bytes on ~3 800 rows and the whole pass measures in single-digit
    milliseconds, while a join table would be a second place a tag lives and a second
    thing a reindex has to keep true.

    Args:
        conn: Open index connection.
        filters: Active filter set.

    Returns:
        ``[{value, count}]``, most used first, then alphabetical.
    """
    where, params = _where(filters, exclude="keyword")
    counts: Dict[str, int] = {}
    for row in conn.execute(f"SELECT keywords FROM photos WHERE {where}", params):
        for value in unpack_keywords(row["keywords"]):
            counts[value] = counts.get(value, 0) + 1
    return [
        {"value": value, "count": count}
        for value, count in sorted(counts.items(), key=lambda kv: (-kv[1], kv[0]))
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
            "keywords": _facet_keywords(conn, filters),
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


def _current_ratings(raw_paths: Sequence[str], resolved: Sequence[Path]) -> Dict[str, int]:
    """
    Read each path's rating currently in the index, before a write overwrites it.

    Keyed by the path exactly as the client sent it (``raw_paths``), because that is the
    spelling the client will send back on undo. Both spellings a row might be stored
    under (see :func:`_lookup_row`) are queried in one round trip; a path with no row at
    all defaults to ``0`` (unrated) — the correct target for "there was nothing to
    restore".

    Args:
        raw_paths: Path strings exactly as the client supplied them.
        resolved: The same paths after resolution, same order.

    Returns:
        ``{raw_path: rating}`` for every input path.
    """
    if not raw_paths:
        return {}
    candidates = set(raw_paths) | {str(p) for p in resolved}
    conn = _open_conn()
    try:
        placeholders = ",".join("?" for _ in candidates)
        rows = conn.execute(
            f"SELECT path, rating FROM photos WHERE path IN ({placeholders})",
            list(candidates),
        ).fetchall()
        found = {row["path"]: row["rating"] for row in rows}
    finally:
        conn.close()
    return {
        raw: found.get(raw, found.get(str(res), 0))
        for raw, res in zip(raw_paths, resolved)
    }


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


# ---------------------------------------------------------------------------
# Scope resolution
#
# The SPA sends `collection=<id>` alongside the ordinary filters and the SERVER does
# the merge. Deliberately: the precedence rule is the model, and a model implemented
# twice (once in Python for `/collections/{id}/photos`, once in TypeScript for the
# screen) is a model that drifts. The client's job is to name the scope; deciding what
# a scope plus a refinement means is this file's.
# ---------------------------------------------------------------------------


def scope_filters(collection_id: Optional[str]) -> Optional[PhotoFilters]:
    """
    Resolve a collection id into its filter set. Blocking (reads the TOML file).

    Args:
        collection_id: Saved collection id, or None for the whole library.

    Returns:
        The collection's filters, or None when there is no scope.

    Raises:
        HTTPException: 404 when the id names no saved collection. A scope that has
            silently evaporated must not degrade into "the whole library" — that would
            show the user 3 800 photos where they asked for 172 and give no clue why.
    """
    if not collection_id:
        return None
    saved = collections_store.get(collection_id)
    if saved is None:
        raise HTTPException(status_code=404, detail=f"No collection with id {collection_id!r}")
    # Lenient: the query may have been hand-edited, and dropping one unusable filter
    # beats refusing to open the collection at all.
    return filters_from_mapping(collections_store.clean_query(saved.query), strict=False)


def composed_filters(collection_id: Optional[str], refine: PhotoFilters) -> PhotoFilters:
    """
    Resolve a scope and compose it with the ad-hoc filters. Blocking.

    Args:
        collection_id: Saved collection id, or None.
        refine: The ad-hoc filter set from the query string.

    Returns:
        The single effective filter set every downstream query runs against.
    """
    return compose(scope_filters(collection_id), refine).effective


class NarrowingDimension(BaseModel):
    """One dimension that is currently narrowing the result set."""

    dimension: str
    label: str
    # Which layer supplied the effective clause. Never 'both' — the whole point of the
    # rule is that exactly one layer owns a dimension at a time.
    source: Literal["scope", "refine"]
    # The scope also set this dimension and lost. Present so the UI can SAY so; a
    # silently swallowed scope clause is how a user stops trusting a saved collection.
    overrides: bool = False
    fields: Dict[str, Any] = Field(default_factory=dict)
    scope_fields: Dict[str, Any] = Field(default_factory=dict)
    # Rows this dimension is currently excluding — `without` minus `total`. This is the
    # "what happens if I change one of them" answer, and it is computed by RESETTING the
    # dimension's fields, deliberately NOT by the facet endpoint's `exclude=`. The two
    # differ on `rating`: `exclude=` also drops the default reject-hiding clause, which is
    # right for a facet reporting the -1 bucket and a lie in a readout that promises the
    # number you will actually see. See :func:`without_dimension`.
    without: int


class NarrowingResponse(BaseModel):
    """
    What is narrowing the result set, and what each part costs.

    `total` is the composed count; `scope_total` is the scope alone; `library_total` is
    the unfiltered library under the same default rules (rejects hidden). Together they
    let the UI show the whole funnel in one row of numbers.
    """

    scope_id: Optional[str] = None
    scope_name: Optional[str] = None
    total: int
    scope_total: int
    library_total: int
    dimensions: List[NarrowingDimension] = Field(default_factory=list)


def _query_narrowing(
    saved: Optional["collections_store.Collection"],
    scope: Optional[PhotoFilters],
    refine: PhotoFilters,
) -> Dict[str, Any]:
    """
    Compute the narrowing readout. Blocking.

    One COUNT for the composed set, one for the scope alone, one for the bare library,
    and one per active dimension with that dimension excluded. Active dimensions are
    few (six is a busy screen), so this is a handful of sub-millisecond counts.

    Args:
        saved: The scope's stored record, for its name; None when unscoped.
        scope: The scope's filters, or None.
        refine: The ad-hoc filter set.

    Returns:
        Dict matching :class:`NarrowingResponse`.
    """
    composition = compose(scope, refine)
    conn = _open_conn()
    try:

        def count(filters: PhotoFilters, exclude: Optional[str] = None) -> int:
            where, params = _where(filters, exclude=exclude)
            return conn.execute(
                f"SELECT COUNT(*) AS n FROM photos WHERE {where}", params
            ).fetchone()["n"]

        # The funnel's three steps must be comparable, so the view flags apply to all of
        # them: with "show rejected" on, "library" means the library including rejects.
        def with_view(filters: PhotoFilters) -> PhotoFilters:
            out = PhotoFilters(
                **{f.name: getattr(filters, f.name) for f in dataclass_fields(PhotoFilters)}
            )
            for name in VIEW_FIELDS:
                setattr(out, name, getattr(composition.effective, name))
            return out

        total = count(composition.effective)
        dimensions = [
            {
                "dimension": dimension,
                "label": DIMENSION_LABELS.get(dimension, dimension),
                "source": source,
                "overrides": dimension in composition.overridden,
                "fields": set_dimensions(composition.effective).get(dimension, {}),
                "scope_fields": composition.overridden.get(dimension, {}),
                "without": count(without_dimension(composition.effective, dimension)),
            }
            for dimension, source in composition.sources.items()
        ]
        return {
            "scope_id": saved.id if saved is not None else None,
            "scope_name": saved.name if saved is not None else None,
            "total": total,
            "scope_total": total if scope is None else count(with_view(scope)),
            "library_total": count(with_view(PhotoFilters())),
            "dimensions": sorted(dimensions, key=lambda d: (d["source"] == "refine", d["label"])),
        }
    finally:
        conn.close()


@router.get("/api/photos/narrowing", response_model=NarrowingResponse)
async def photos_narrowing(
    filters: PhotoFilters = Depends(photo_filters),
    collection: Optional[str] = Query(default=None),
) -> NarrowingResponse:
    """
    Explain the current result set: which layer supplies each dimension, and what it costs.

    This endpoint exists so the screen can EXPRESS the precedence rule rather than merely
    obey it. `without` per dimension answers "what would happen if I cleared this one", by
    resetting that dimension's fields and re-counting — see :func:`without_dimension` for
    why that is not the facet endpoint's `exclude=`.
    """
    saved = await asyncio.to_thread(collections_store.get, collection) if collection else None
    if collection and saved is None:
        raise HTTPException(status_code=404, detail=f"No collection with id {collection!r}")
    scope = (
        filters_from_mapping(collections_store.clean_query(saved.query), strict=False)
        if saved is not None
        else None
    )
    data = await asyncio.to_thread(_query_narrowing, saved, scope, filters)
    return NarrowingResponse(
        scope_id=data["scope_id"],
        scope_name=data["scope_name"],
        total=data["total"],
        scope_total=data["scope_total"],
        library_total=data["library_total"],
        dimensions=[NarrowingDimension(**d) for d in data["dimensions"]],
    )


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
    collection: Optional[str] = Query(default=None),
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
    effective = await asyncio.to_thread(composed_filters, collection, filters)
    data = await asyncio.to_thread(
        _query_list, effective, sort, order, limit, offset, include_trashed
    )
    return PhotoListResponse(
        total=data["total"],
        offset=data["offset"],
        limit=data["limit"],
        items=[PhotoRow(**item) for item in data["items"]],
    )


# ---------------------------------------------------------------------------
# Library structures — the candidate layouts, as queries
#
# THE CLAIM UNDER TEST (Stage F3): a directory layout can be replaced by a saved
# query, so nothing has to move on disk to be navigable. Every structure below is
# therefore returned as a LIST OF QUERIES: each group carries the `PhotoFilters`
# mapping that resolves to exactly its own rows, which is the same mapping
# `POST /api/collections` stores. Clicking a group is a filter change; saving one is
# a saved collection. No group is ever a directory, and none of this reads a file.
#
# Four kinds, and what makes each different:
#
#   flat   — the two culling roots. The layout this library actually has.
#   month  — `substr(date_taken, 1, 7)`. Pure SQL, no state.
#   album  — the `dc:subject` tag set. The one structure whose membership is
#            AUTHORED rather than derived, which is why it is the only one that can
#            express a hand-picked set (see `event` below for what that costs).
#   event  — a time-gap clustering over `date_taken`. DERIVED: the boundaries are
#            computed from the data, not stored, so the group's query is a plain
#            `date_from`/`date_to` pair once the boundary is known — but the
#            BOUNDARY itself is not expressible as a query at all. That asymmetry is
#            the finding, and it is why `gap_seconds` is a request parameter rather
#            than a constant: there is no single correct grain (measured: 2 days
#            keeps every hand-tagged trip intact without merging January into one
#            26-day blob; 3 days gets each trip to exactly one cluster and does
#            merge it).
#
# Each kind is computed with ITS OWN dimension excluded, exactly like a facet. That
# is what keeps the tree stable while you navigate inside it: clicking March must
# not collapse the month list to March alone.
# ---------------------------------------------------------------------------

StructureKind = Literal["flat", "month", "album", "event"]

# The dimension each structure occupies, and therefore the one left open when its
# groups are computed.
STRUCTURE_DIMENSION: Dict[str, str] = {
    "flat": "root",
    "month": "date",
    "album": "keyword",
    "event": "date",
}

# Gap that ends an event, in seconds. 2 days by default — measured against this
# library's own hand-written album tags, it is the largest threshold that still
# separates ordinary shooting weeks (see the comment above).
DEFAULT_EVENT_GAP = 2 * 86400
MIN_EVENT_GAP = 600
MAX_EVENT_GAP = 30 * 86400

# Above this, an event scan is refused rather than run. The scan is O(n) over one
# column and measures ~20 ms at 3 800 rows, so the ceiling is generous; it exists so
# a future library of a different order of magnitude fails loudly instead of
# silently making the sidebar slow.
MAX_EVENT_SCAN_ROWS = 250_000

_ISO_STAMP = "%Y-%m-%dT%H:%M:%SZ"


class StructureGroup(BaseModel):
    """
    One group of a structure — a navigable node that is really a saved query.

    Attributes:
        key: Stable identifier within this structure (a month key, a tag, a start stamp).
        label: What the sidebar row says.
        sublabel: The one-line detail under it (a date range, a span in days).
        count: Rows in the group, under the current narrowing minus this dimension.
        query: The `PhotoFilters` mapping that resolves to exactly this group. Storable
            verbatim by `POST /api/collections` — that equivalence is the whole point.
    """

    key: str
    label: str
    sublabel: str = ""
    count: int
    query: Dict[str, Any] = Field(default_factory=dict)


class StructureResponse(BaseModel):
    """
    A candidate library layout, expressed as queries.

    `total` is the universe this structure partitions — the current narrowing with the
    structure's OWN dimension left open, which is what `ungrouped` is measured against.
    It is deliberately not the number of rows on screen: inside `root=final` the flat
    structure still lists both roots, the same way a folder rail does.

    `ungrouped` is the number of photos the structure cannot place — the measurement
    that says whether a layout actually covers the library. It is 0 for `flat` and for
    `month`/`event` on this library (every indexed photo carries `date_taken`), and
    large for `album`, which only covers what somebody tagged.
    """

    kind: StructureKind
    # Only set for `event`; the grain the boundaries were computed at.
    gap_seconds: Optional[int] = None
    total: int
    ungrouped: int
    groups: List[StructureGroup] = Field(default_factory=list)
    # Server-side wall time for the grouping, so "cheap to compute or needs a stored
    # artifact" stays a measured question rather than an argued one.
    elapsed_ms: float = 0.0


# A `YYYY-MM` bucket key, as `substr(date_taken, 1, 7)` produces it from a well-formed
# ISO stamp. `metadata_extractor` stores the RAW EXIF string when it cannot parse a
# capture time, so `2026:03` and `    :  ` are both reachable index states — see
# `_month_bounds`.
_MONTH_KEY_RE = re.compile(r"^(\d{4})-(\d{2})$")


def _month_bounds(key: str) -> Optional[Tuple[str, str]]:
    """
    Inclusive ISO bounds covering one ``YYYY-MM`` bucket.

    Returns None for a key that is not a well-formed month, which is what a row whose
    `date_taken` the extractor could not parse produces (it stores the raw EXIF string,
    so `2026:03:03 17:36:33` truncates to the key `2026:03`). Such a row is counted as
    ungrouped, exactly as `_structure_event` counts it — the alternative shipped a group
    whose own query resolves to zero rows, which quietly falsifies the one invariant this
    endpoint exists to demonstrate.

    Args:
        key: The month key.

    Returns:
        Tuple of (date_from, date_to), or None when the key is not a month. The upper
        bound carries an explicit end-of-day time: `date_taken` is compared as a string,
        so a bare date sorts before every photo taken on that day.
    """
    match = _MONTH_KEY_RE.match(key)
    if match is None:
        return None
    year, month = int(match.group(1)), int(match.group(2))
    if not 1 <= month <= 12:
        return None
    last = calendar.monthrange(year, month)[1]
    return f"{key}-01", f"{key}-{last:02d}T23:59:59Z"


def _structure_flat(conn: sqlite3.Connection, filters: PhotoFilters) -> Dict[str, Any]:
    """Group by culling root — the layout the library physically has."""
    where, params = _where(filters, exclude="root")
    rows = conn.execute(
        f"SELECT root, COUNT(*) AS n FROM photos WHERE {where} GROUP BY root", params
    ).fetchall()
    counts = {r["root"]: r["n"] for r in rows}
    groups = [
        StructureGroup(
            key=root,
            label=root.capitalize(),
            sublabel="directory",
            count=counts.get(root, 0),
            query={"root": root},
        )
        for root in CULL_ROOTS
        if counts.get(root, 0) > 0
    ]
    return {"groups": groups, "ungrouped": 0}


def _structure_month(conn: sqlite3.Connection, filters: PhotoFilters) -> Dict[str, Any]:
    """Group by calendar month — the classic `YYYY/MM` layout, as a date range each."""
    where, params = _where(filters, exclude="date")
    rows = conn.execute(
        f"SELECT substr(date_taken, 1, 7) AS k, COUNT(*) AS n FROM photos "
        f"WHERE {where} AND date_taken IS NOT NULL AND date_taken <> '' "
        f"GROUP BY k ORDER BY k DESC",
        params,
    ).fetchall()
    undated = conn.execute(
        f"SELECT COUNT(*) AS n FROM photos "
        f"WHERE {where} AND (date_taken IS NULL OR date_taken = '')",
        params,
    ).fetchone()["n"]
    groups = []
    for row in rows:
        key = row["k"]
        bounds = _month_bounds(key)
        if bounds is None:
            undated += row["n"]
            continue
        start, end = bounds
        groups.append(
            StructureGroup(
                key=key,
                label=key,
                sublabel=f"{calendar.month_abbr[int(key[5:7])]} {key[:4]}",
                count=row["n"],
                query={"date_from": start, "date_to": end},
            )
        )
    return {"groups": groups, "ungrouped": undated}


def _structure_album(conn: sqlite3.Connection, filters: PhotoFilters) -> Dict[str, Any]:
    """
    Group by `dc:subject` tag — the only structure whose membership a human authored.

    A photo carrying two tags is in two groups, so the counts deliberately do not sum
    to the total; `ungrouped` is computed as "rows with no tag at all" rather than by
    subtraction.
    """
    where, params = _where(filters, exclude="keyword")
    rows = conn.execute(
        f"SELECT keywords FROM photos WHERE {where} AND keywords IS NOT NULL", params
    ).fetchall()
    tally: Dict[str, int] = {}
    untagged = 0
    for row in rows:
        tags = unpack_keywords(row["keywords"])
        if not tags:
            untagged += 1
            continue
        for tag in tags:
            tally[tag] = tally.get(tag, 0) + 1
    # Rows whose `keywords` is NULL predate schema v3 and are also untagged as far as
    # anyone browsing can tell.
    untagged += conn.execute(
        f"SELECT COUNT(*) AS n FROM photos WHERE {where} AND keywords IS NULL", params
    ).fetchone()["n"]
    groups = [
        StructureGroup(key=tag, label=tag, sublabel="tag", count=count, query={"keyword": [tag]})
        for tag, count in sorted(tally.items(), key=lambda kv: (-kv[1], kv[0]))
    ]
    return {"groups": groups, "ungrouped": untagged}


def _structure_event(
    conn: sqlite3.Connection, filters: PhotoFilters, gap_seconds: int
) -> Dict[str, Any]:
    """
    Group by a time gap in `date_taken` — a shoot, derived rather than declared.

    One ordered pass over a single column: a new event starts wherever consecutive
    capture times are further apart than `gap_seconds`. Nothing is stored, so the
    boundaries move when the set does — which is exactly the property that decides
    whether an event can stay a query. See the module comment above.

    Args:
        conn: Open index connection.
        filters: The effective filter set (its own `date` dimension already excluded).
        gap_seconds: The silence that ends an event.

    Returns:
        Dict with `groups` and `ungrouped`.

    Raises:
        HTTPException: 413 when the scan would exceed `MAX_EVENT_SCAN_ROWS`.
    """
    where, params = _where(filters, exclude="date")
    scanned = conn.execute(
        f"SELECT COUNT(*) AS n FROM photos WHERE {where} "
        f"AND date_taken IS NOT NULL AND date_taken <> ''",
        params,
    ).fetchone()["n"]
    if scanned > MAX_EVENT_SCAN_ROWS:
        raise HTTPException(
            status_code=413,
            detail=f"Event detection scans every dated row; {scanned} is over the "
            f"{MAX_EVENT_SCAN_ROWS} limit.",
        )
    rows = conn.execute(
        f"SELECT date_taken FROM photos WHERE {where} "
        f"AND date_taken IS NOT NULL AND date_taken <> '' ORDER BY date_taken",
        params,
    ).fetchall()
    undated = conn.execute(
        f"SELECT COUNT(*) AS n FROM photos "
        f"WHERE {where} AND (date_taken IS NULL OR date_taken = '')",
        params,
    ).fetchone()["n"]

    stamps: List[str] = []
    seconds: List[float] = []
    for row in rows:
        raw = row["date_taken"]
        try:
            moment = datetime.strptime(raw, _ISO_STAMP)
        except ValueError:
            # A stamp the indexer wrote in some other shape cannot be placed on the
            # timeline. Counted as ungrouped rather than guessed at.
            undated += 1
            continue
        stamps.append(raw)
        seconds.append(moment.timestamp())

    groups: List[StructureGroup] = []
    if stamps:
        start = 0
        bounds: List[Tuple[int, int]] = []
        for i in range(1, len(stamps)):
            if seconds[i] - seconds[i - 1] > gap_seconds:
                bounds.append((start, i - 1))
                start = i
        bounds.append((start, len(stamps) - 1))
        for first, last in reversed(bounds):
            span_days = (
                datetime.strptime(stamps[last], _ISO_STAMP).date()
                - datetime.strptime(stamps[first], _ISO_STAMP).date()
            ).days + 1
            label = stamps[first][:10]
            if stamps[last][:10] != label:
                label = f"{label} → {stamps[last][:10]}"
            groups.append(
                StructureGroup(
                    key=stamps[first],
                    label=label,
                    sublabel=f"{span_days} day{'' if span_days == 1 else 's'}",
                    count=last - first + 1,
                    # Second-precision bounds, so the range is exactly this cluster and
                    # not "that day" — two shoots can share a calendar day.
                    query={"date_from": stamps[first], "date_to": stamps[last]},
                )
            )
    return {"groups": groups, "ungrouped": undated}


def _query_structure(
    filters: PhotoFilters, kind: StructureKind, gap_seconds: int
) -> Dict[str, Any]:
    """
    Compute one structure over the current narrowing. Blocking.

    Args:
        filters: The effective (composed) filter set.
        kind: Which structure to build.
        gap_seconds: Event grain; ignored by the other kinds.

    Returns:
        Dict matching :class:`StructureResponse`.
    """
    started = time.perf_counter()
    conn = _open_conn()
    try:
        where, params = _where(filters, exclude=STRUCTURE_DIMENSION[kind])
        total = conn.execute(
            f"SELECT COUNT(*) AS n FROM photos WHERE {where}", params
        ).fetchone()["n"]
        if kind == "flat":
            built = _structure_flat(conn, filters)
        elif kind == "month":
            built = _structure_month(conn, filters)
        elif kind == "album":
            built = _structure_album(conn, filters)
        else:
            built = _structure_event(conn, filters, gap_seconds)
    finally:
        conn.close()
    return {
        "kind": kind,
        "gap_seconds": gap_seconds if kind == "event" else None,
        "total": total,
        "ungrouped": built["ungrouped"],
        "groups": built["groups"],
        "elapsed_ms": round((time.perf_counter() - started) * 1000, 2),
    }


@router.get("/api/photos/structure", response_model=StructureResponse)
async def photos_structure(
    filters: PhotoFilters = Depends(photo_filters),
    collection: Optional[str] = Query(default=None),
    kind: StructureKind = Query(default="month"),
    gap_seconds: int = Query(default=DEFAULT_EVENT_GAP, ge=MIN_EVENT_GAP, le=MAX_EVENT_GAP),
) -> StructureResponse:
    """
    One candidate library layout, as a list of groups that are each a query.

    Nothing here moves, renames or reads a photograph — a "folder" is a `WHERE` clause
    and a group's `query` is the exact mapping a saved collection would store. That
    equivalence is the Stage F3 experiment: if a structure can be navigated this way it
    never needed to be a directory.

    Computed with the structure's own dimension left open (the facet contract), so
    clicking one group does not collapse the list to that group.
    """
    effective = await asyncio.to_thread(composed_filters, collection, filters)
    data = await asyncio.to_thread(_query_structure, effective, kind, gap_seconds)
    return StructureResponse(**data)


@router.get("/api/photos/facets", response_model=FacetsResponse)
async def photos_facets(
    filters: PhotoFilters = Depends(photo_filters),
    collection: Optional[str] = Query(default=None),
) -> FacetsResponse:
    """
    Counts and ranges per filter dimension for the current selection.

    Each facet applies every filter except its own, so the option list stays
    navigable: picking "5 stars" does not collapse the rating chips to a single row.
    The `histograms` block follows the same rule and gives each range slider the
    distribution to draw behind its track.
    """
    effective = await asyncio.to_thread(composed_filters, collection, filters)
    data = await asyncio.to_thread(_query_facets, effective)
    return FacetsResponse(
        count=data["count"],
        ratings=data["ratings"],
        orientations=data["orientations"],
        labels=[FacetValue(**v) for v in data["labels"]],
        camera_models=[FacetValue(**v) for v in data["camera_models"]],
        lens_models=[FacetValue(**v) for v in data["lens_models"]],
        keywords=[FacetValue(**v) for v in data["keywords"]],
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


# Suffixes `GET /api/photos/original` will stream.
#
# The root allowlist says a path is *inside the library*; it does not say the file is a
# photograph. Final and Staging also hold `.photo-edit` sidecars — a ~17 MB zip that is
# the only copy of an edit history — and this is the one endpoint that would hand a
# client an arbitrary file byte for byte. `thumb` needs no such list because Pillow
# refuses to decode anything that is not an image; streaming raw bytes has no equivalent
# refusal, so the list is the refusal.
_ORIGINAL_SUFFIXES = {".jpg", ".jpeg"}


def _original_etag(source: Path) -> str:
    """
    Content-address a master by identity, matching the thumbnail cache's key material.

    Deliberately the same ``path|mtime_ns|size`` shape as
    :func:`photo_flow.index.thumbs.cache_key`, with its own tier sentinel, so an edit
    (which changes mtime) misses here exactly as it misses there.

    Args:
        source: Resolved path to the master.

    Returns:
        Hex SHA-1 digest over (absolute path, mtime_ns, size, "original").

    Raises:
        OSError: If the file cannot be stat'ed.
    """
    stat = source.stat()
    material = f"{source}|{stat.st_mtime_ns}|{stat.st_size}|original"
    return hashlib.sha1(material.encode("utf-8")).hexdigest()


@router.get("/api/photos/original", response_class=FileResponse)
async def photos_original(
    request: Request,
    path: str = Query(...),
    v: Optional[str] = Query(default=None),
) -> Response:
    """
    Serve a master's own bytes, unmodified, for true 1:1 inspection.

    The `view` tier tops out at 2048 px on the long edge, which is 32–39 % of the linear
    resolution of the masters in this library — so "1:1" in the proxy viewer is not 1:1.
    This is the escape hatch, and it deliberately does **not** go through the thumbnail
    cache.

    **It is not a `full` tier, and that is the point.** A full-resolution tier would be a
    lossy re-encode of an already-lossy master, and the signal 1:1 exists to judge —
    per-pixel micro-contrast, i.e. whether this frame is sharper than that one — is
    exactly what JPEG quantisation attenuates first. Measured on this library by
    `scripts/compare_survey.py` (12 masters, throwaway cache, this machine), such a tier
    would also cost **365–812 ms** (median 630 ms) of CPU and **3.07–9.51 MB** of cache per
    photo to produce a file no more faithful than the one already on disk. The 26 MP
    portrait masters are the whole upper half of both ranges (766–812 ms of encode alone,
    9.0–9.5 MB out). Copying the bytes costs nothing and cannot lie.

    The price is paid on the wire and in the client's decoder: **0.43–21.7 MB** per frame
    over the 3 797 present rows (median 7.3 MB), and a 26 MP bitmap is ~104 MB resident
    once decoded. The client is therefore expected to request this only on an explicit
    zoom, never from a prewarm ring.

    Cache semantics match `thumb`: an ETag over (path, mtime, size) plus `immutable`, so
    an edited photo gets a new URL rather than a stale hit. `v` is the client-side
    cache-buster and is ignored here.

    Args:
        request: Incoming request, read for `If-None-Match`.
        path: Absolute path of the master, validated against the culling roots.
        v: Ignored cache-buster.

    Returns:
        The file itself as `image/jpeg`, or a bare 304.

    Raises:
        HTTPException: 400 outside the roots or not a JPEG, 404 when it is not a file.
    """
    source = await asyncio.to_thread(_resolve_in_roots, path, include_trash=True)
    if source.suffix.lower() not in _ORIGINAL_SUFFIXES:
        raise HTTPException(status_code=400, detail="Only JPEG masters can be served in full")
    if not await asyncio.to_thread(source.is_file):
        raise HTTPException(status_code=404, detail="Source photo not found")

    try:
        etag = await asyncio.to_thread(_original_etag, source)
    except OSError as exc:
        raise HTTPException(status_code=404, detail=f"Cannot read source photo: {exc}") from exc

    quoted = f'"{etag}"'
    cache_headers = {
        "Cache-Control": "public, max-age=31536000, immutable",
        "ETag": quoted,
    }

    if_none_match = request.headers.get("if-none-match", "")
    if quoted in [candidate.strip() for candidate in if_none_match.split(",")]:
        return Response(status_code=304, headers=cache_headers)

    return FileResponse(str(source), media_type="image/jpeg", headers=cache_headers)


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


@router.post("/api/photos/rating", response_model=RatingWriteResult)
async def photos_set_rating(body: RatingRequest) -> RatingWriteResult:
    """
    Write an XMP rating (-1 reject, 0 unrated, 1–5 stars) to a batch of photos.

    Rating 0 *clears* the tag rather than writing a literal zero, so an uncalled
    photo is indistinguishable from one that was never rated — which is what
    Photomator, Bridge and Immich all expect. -1 is written literally: it is the
    spec's reject value and the whole point is that it is NOT the absent state.

    Every touched path's *prior* rating is read before the write and returned as
    ``previous`` — unconditionally, not only above some batch-size threshold. A rating
    write is destructive by construction (exiftool overwrites the tag in place, and
    Photomator's rating is this project's single source of truth for it), so the client
    can always build the exact inverse for ⌘Z rather than only above a size guess.
    """
    paths = await _resolve_batch_async(body.paths, MAX_WRITE_PATHS)
    previous = await asyncio.to_thread(_current_ratings, body.paths, paths)
    value = "" if body.rating == 0 else str(body.rating)
    result = await asyncio.to_thread(_write_and_reindex, paths, [f"-XMP-xmp:Rating={value}"])
    return RatingWriteResult(**result, previous=previous)


@router.post("/api/photos/rating/undo", response_model=RatingRestoreResult)
async def photos_undo_rating(body: RatingRestoreRequest) -> RatingRestoreResult:
    """
    Restore each path to a rating it held before some earlier write — the inverse of
    `POST /api/photos/rating`.

    Paths are grouped by their target rating (at most 7 groups — the whole -1..5 range),
    so a 500-photo undo is still a handful of exiftool processes, not one per file. If a
    group's write reports any failure, **every path in that group** is reported failed
    rather than guessed at file-by-file: this endpoint would rather over-report a
    failure than tell the caller an undo landed when it didn't.
    """
    if not body.items:
        raise HTTPException(status_code=400, detail="No items supplied")
    if len(body.items) > MAX_WRITE_PATHS:
        raise HTTPException(
            status_code=400, detail=f"Too many paths: {len(body.items)} (max {MAX_WRITE_PATHS})"
        )

    by_rating: Dict[int, List[str]] = {}
    for item in body.items:
        by_rating.setdefault(item.rating, []).append(item.path)

    restored = 0
    errors = 0
    failed_paths: List[str] = []
    messages: List[str] = []

    for rating, raw_paths in by_rating.items():
        resolved = await _resolve_batch_async(raw_paths, MAX_WRITE_PATHS)
        value = "" if rating == 0 else str(rating)
        result = await asyncio.to_thread(
            _write_and_reindex, resolved, [f"-XMP-xmp:Rating={value}"]
        )
        if result["errors"] or result["updated"] < len(resolved):
            errors += len(raw_paths)
            failed_paths.extend(raw_paths)
        else:
            restored += result["updated"]
        messages.extend(result["messages"])

    return RatingRestoreResult(
        restored=restored, errors=errors, failed_paths=failed_paths, messages=messages
    )


class OpenInEditorRequest(BaseModel):
    path: str
    #: Which file the hand-over is FOR — the JPEG master (the default), or the RAW that
    #: correlates to it. `raw` still takes the JPG's own path in `path`, never a RAW path:
    #: RAWS_PATH is deliberately outside `CULL_ROOTS` (see config.py), so a client can
    #: never name a RAW file directly through this or any other endpoint. The correlating
    #: RAF is found server-side by :mod:`photo_flow.raw_link`.
    target: Literal["jpeg", "raw"] = "jpeg"
    #: Which configured editor to use. Omitted means "the default for this kind of file",
    #: which is the first `[[editors]]` entry that handles it — file order is the
    #: preference order.
    editor: Optional[str] = None


class OpenInEditorResult(BaseModel):
    opened: bool
    editor: str
    message: str


@router.post("/api/photos/open-in-editor", response_model=OpenInEditorResult)
async def photos_open_in_editor(body: OpenInEditorRequest) -> OpenInEditorResult:
    """
    Hand one photo — or the RAW that correlates to it — to an external application.

    This is a **launch, not a write**: photo-flow does not read the result, does not wait
    for it, and does not learn that anything changed. The application writes the file in
    place, so the next reindex picks the change up the same way it picks up a Photomator
    edit — via mtime. That is the whole integration, deliberately.

    ``target: "raw"`` still takes the JPG's own path in `body.path`. It is resolved
    through the ordinary allowlist like any other request, and the correlating RAF is then
    derived by :func:`photo_flow.raw_link.find_raw` — the one read-only door onto
    RAWS_PATH. See that module's docstring for why this is a separate resolver rather than
    a widened `_allowed_roots`.

    ``open -a NAME PATH`` is invoked as an argument list with no shell, and the path has
    already been through :func:`_resolve_in_roots` (the JPG) or :func:`raw_link.find_raw`
    (the RAF it derives), so neither the app name nor the filename can be made to mean
    something else. Trashed paths are excluded: the editor would write to a file the user
    has already culled.

    A missing application, an unreachable RAW volume, or a JPG with no correlating RAW are
    all reported, never raised as a 500 — the panel is a pipeline tool and an absent
    optional capability is not a fault in it. An install that has turned the hand-over off
    (`editors = []`) is the same kind of answer, not an error.
    """
    path = _resolve_in_roots(body.path)
    if not path.exists():
        raise HTTPException(status_code=404, detail=f"{path.name} is not on disk")

    # A JPEG master goes to a pixel editor, a RAF to a RAW developer, and on most machines
    # those are different programs — so the kind selects the candidate editor set and the
    # caller may name one within it.
    kind = body.target
    if body.editor is not None:
        profile = INSTALL.find_editor(body.editor)
        if profile is None:
            raise HTTPException(
                status_code=404, detail=f"No configured editor with id {body.editor!r}"
            )
        if not profile.handles_kind(kind):
            # Refused rather than launched: handing a RAF to a pixel editor is how you
            # get a silent no-op or a flattened export, neither of which is what was asked.
            raise HTTPException(
                status_code=400,
                detail=f"{profile.name} is not configured for {kind} files",
            )
    else:
        profile = INSTALL.default_editor(kind)
        if profile is None:
            return OpenInEditorResult(
                opened=False,
                editor="",
                message=f"No editor is configured for {kind} files.",
            )

    if kind == "raw":
        link = await asyncio.to_thread(raw_link.find_raw, path, RAWS_PATH)
        if link.state != "found" or link.path is None:
            return OpenInEditorResult(opened=False, editor="", message=link.detail)
        launch_path = link.path
    else:
        launch_path = path

    app_name = profile.app

    def _launch() -> OpenInEditorResult:
        try:
            proc = subprocess.run(
                ["open", "-a", app_name, str(launch_path)],
                capture_output=True,
                text=True,
                timeout=15,
            )
        except FileNotFoundError:
            # `open(1)` itself missing: not macOS.
            return OpenInEditorResult(
                opened=False,
                editor=app_name,
                message="`open` is not available on this platform.",
            )
        except subprocess.TimeoutExpired:
            return OpenInEditorResult(
                opened=False,
                editor=app_name,
                message=f"{app_name} did not respond within 15s.",
            )
        if proc.returncode != 0:
            detail = (proc.stderr or proc.stdout or "").strip()
            return OpenInEditorResult(
                opened=False,
                editor=app_name,
                message=detail or f"{app_name} could not be launched.",
            )
        return OpenInEditorResult(
            opened=True,
            editor=app_name,
            message=f"Opened {launch_path.name} in {app_name}.",
        )

    return await asyncio.to_thread(_launch)


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
