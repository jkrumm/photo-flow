"""
Saved collections — CRUD over the TOML store, plus resolution into a result set.

Everything here lives under ``/api/collections``. The ``/api`` prefix is the same
requirement as the photos router: ``app.py``'s SPA catch-all is registered last and
would otherwise shadow a bare path.

Surface
-------
GET    /api/collections                  — every saved collection (optionally with counts)
POST   /api/collections                  — save the current query under a name
PATCH  /api/collections/{id}             — rename and/or replace the query
DELETE /api/collections/{id}             — forget the query (never a photo)
GET    /api/collections/{id}/photos      — resolve into the ordinary photo list

What a collection is NOT
------------------------
It is not a place. Resolving one runs the same ``SELECT`` the culling screen already
runs, with the stored filters applied — no file is moved, copied, linked or renamed, and
deleting a collection deletes a query. That is the property being prototyped: whether a
saved query can stand in for a physical folder without a second on-disk layout existing.

Vocabulary
----------
A stored query speaks exactly ``routes_photos.PhotoFilters`` — validated here through
:func:`~photo_flow.api.routes_photos.filters_from_mapping`, whose spec is derived from
that dataclass. Create/update validate **strictly** (a bad key from the SPA is a bug and
gets a 400); resolution validates **leniently** (the same value may have arrived from a
hand-edited file, and dropping one filter beats refusing to open the collection).
"""
from __future__ import annotations

import asyncio
import logging
import sqlite3
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, Field

from photo_flow import collections as store
from photo_flow.api.routes_photos import (
    PhotoListResponse,
    PhotoRow,
    _open_conn,
    _query_list,
    _where,
    compose,
    filters_from_mapping,
    scope_filters,
    set_fields,
)

logger = logging.getLogger(__name__)

router = APIRouter()

# A count per collection is one COUNT(*) over the indexed 3 800-row table — 0.15 ms
# measured on the real library — so the sidebar can afford to show them all. The cap
# guards against a hand-edited file with a thousand entries; past it, counts are simply
# not computed and every row renders "—".
MAX_COUNTED = 100


class CollectionModel(BaseModel):
    """One saved query as the SPA sees it."""

    id: str
    name: str
    query: Dict[str, Any] = Field(default_factory=dict)
    created_at: str = ""
    updated_at: str = ""
    # Present only when the listing was asked for counts; null otherwise, which the UI
    # renders as "—" rather than as zero. A collection matching nothing is a real 0.
    count: Optional[int] = None


class CollectionListResponse(BaseModel):
    items: List[CollectionModel]
    # The file the collections live in — surfaced so the UI can say where they are, and
    # so "the index is a cache, this is not" is visible rather than merely documented.
    path: str


class CollectionCreate(BaseModel):
    name: str
    query: Dict[str, Any] = Field(default_factory=dict)
    # The collection the client was looking THROUGH when it saved. Present so the server
    # composes, rather than the client shipping a pre-merged query it derived from its own
    # copy of the precedence rule — the rule has one implementation, in `routes_photos`.
    scope: Optional[str] = None


class CollectionUpdate(BaseModel):
    name: Optional[str] = None
    query: Optional[Dict[str, Any]] = None
    scope: Optional[str] = None


class CollectionDeleted(BaseModel):
    deleted: bool


def _validate(query: Dict[str, Any]) -> None:
    """
    Reject a query the filter vocabulary cannot express.

    Args:
        query: The client-supplied filter mapping.

    Raises:
        HTTPException: 400 with the offending field named.
    """
    try:
        filters_from_mapping(query, strict=True)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


def _compose_query(query: Dict[str, Any], scope_id: Optional[str]) -> Dict[str, Any]:
    """
    Flatten "the collection I was inside, plus what I narrowed on top" into one query.

    A saved collection is a complete answer, never a delta on another one: a stored
    reference would make the saved set change under you when the parent is edited, and
    would need its own cycle rule the moment someone saved a scope of a scope.

    Args:
        query: The ad-hoc (refine) filter mapping from the client.
        scope_id: The active collection's id, or None.

    Returns:
        The composed mapping, holding only the dimensions that actually narrow.

    Raises:
        HTTPException: 400 for a bad query, 404 for an unknown scope.
    """
    _validate(query)
    if scope_id is None:
        return dict(query)
    composed = compose(scope_filters(scope_id), filters_from_mapping(query, strict=True))
    return set_fields(composed.effective)


def _count(conn: sqlite3.Connection, collection: store.Collection) -> Optional[int]:
    """
    How many present photos the collection resolves to.

    Args:
        conn: An open index connection, shared across the whole listing.
        collection: The saved query.

    Returns:
        The row count, or None when the query could not be run — a missing count renders
        as "—", which is honest; a 0 would claim the collection is empty.
    """
    filters = filters_from_mapping(store.clean_query(collection.query), strict=False)
    where, params = _where(filters)
    try:
        return conn.execute(
            f"SELECT COUNT(*) AS n FROM photos WHERE {where}", params
        ).fetchone()["n"]
    except sqlite3.Error as exc:
        logger.debug("Collection count unavailable for %r: %s", collection.id, exc)
        return None


def _list_with_counts(with_counts: bool) -> List[Dict[str, Any]]:
    """
    Load every collection, optionally resolving each to a count. Blocking.

    ONE connection for the whole listing, not one per collection. The COUNT itself is
    ~0.15 ms against the real 3 800-row index; opening a connection is ~6 ms, because
    `get_db()` re-applies the schema migration on every open. Eleven collections was
    therefore 68 ms of connection setup and 2 ms of counting.
    """
    items = store.load()
    if not (with_counts and items):
        return [{**item.to_dict(), "count": None} for item in items]
    if len(items) > MAX_COUNTED:
        return [{**item.to_dict(), "count": None} for item in items]

    try:
        conn = _open_conn()
    except sqlite3.Error as exc:  # a broken index is not this route's fault
        logger.debug("Collection counts unavailable: %s", exc)
        return [{**item.to_dict(), "count": None} for item in items]
    try:
        return [{**item.to_dict(), "count": _count(conn, item)} for item in items]
    finally:
        conn.close()


@router.get("/api/collections", response_model=CollectionListResponse)
async def collections_list(
    with_counts: bool = Query(default=False),
) -> CollectionListResponse:
    """
    Every saved collection, in file order.

    `with_counts=true` resolves each one against the index. That is a separate opt-in
    because the counts are the only part of this response that depends on the index at
    all — the collections themselves are read from the file and answer even if the
    database has just been deleted.
    """
    items = await asyncio.to_thread(_list_with_counts, with_counts)
    return CollectionListResponse(
        items=[CollectionModel(**item) for item in items],
        path=str(store.config_path()),
    )


@router.post("/api/collections", response_model=CollectionModel)
async def collections_create(body: CollectionCreate) -> CollectionModel:
    """
    Save the current view under a name.

    `scope` names the collection the view was seen through; the stored query is the
    composed result, so the new collection stands on its own.
    """
    query = await asyncio.to_thread(_compose_query, body.query, body.scope)
    try:
        created = await asyncio.to_thread(store.create, body.name, query)
    except store.StoreUnreadable as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except store.CollectionError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except OSError as exc:
        raise HTTPException(status_code=500, detail=f"Could not write collections: {exc}") from exc
    return CollectionModel(**created.to_dict())


@router.patch("/api/collections/{collection_id}", response_model=CollectionModel)
async def collections_update(collection_id: str, body: CollectionUpdate) -> CollectionModel:
    """
    Rename a collection and/or replace its query.

    The id is stable across a rename: it is what the UI's selection and any bookmark hold.
    """
    query = (
        None
        if body.query is None
        else await asyncio.to_thread(_compose_query, body.query, body.scope)
    )
    try:
        updated = await asyncio.to_thread(
            store.update, collection_id, name=body.name, query=query
        )
    except store.StoreUnreadable as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except store.CollectionError as exc:
        # An unknown id and a blank name both arrive here; only the first is a 404.
        status = 404 if "No collection" in str(exc) else 400
        raise HTTPException(status_code=status, detail=str(exc)) from exc
    except OSError as exc:
        raise HTTPException(status_code=500, detail=f"Could not write collections: {exc}") from exc
    return CollectionModel(**updated.to_dict())


@router.delete("/api/collections/{collection_id}", response_model=CollectionDeleted)
async def collections_delete(collection_id: str) -> CollectionDeleted:
    """Forget a saved query. No photograph is read, moved or deleted."""
    try:
        removed = await asyncio.to_thread(store.delete, collection_id)
    except store.StoreUnreadable as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except OSError as exc:
        raise HTTPException(status_code=500, detail=f"Could not write collections: {exc}") from exc
    if not removed:
        raise HTTPException(status_code=404, detail=f"No collection with id {collection_id!r}")
    return CollectionDeleted(deleted=True)


@router.get("/api/collections/{collection_id}/photos", response_model=PhotoListResponse)
async def collections_photos(
    collection_id: str,
    sort: str = Query(default="date_taken"),
    order: str = Query(default="asc"),
    limit: int = Query(default=2000, ge=1, le=10000),
    offset: int = Query(default=0, ge=0),
) -> PhotoListResponse:
    """
    Resolve a collection into a result set.

    Identical to ``GET /api/photos`` with the collection's filters applied — the same
    builder, the same query, the same rows. Sort and order are NOT stored on a
    collection: they are how you look at a set, not which set it is, so the caller keeps
    whatever ordering it was already using.
    """
    collection = await asyncio.to_thread(store.get, collection_id)
    if collection is None:
        raise HTTPException(status_code=404, detail=f"No collection with id {collection_id!r}")
    if sort not in ("date_taken", "filename", "rating", "iso", "focal_mm"):
        raise HTTPException(status_code=400, detail=f"Unknown sort: {sort}")
    if order not in ("asc", "desc"):
        raise HTTPException(status_code=400, detail=f"Unknown order: {order}")

    filters = filters_from_mapping(store.clean_query(collection.query), strict=False)
    data = await asyncio.to_thread(_query_list, filters, sort, order, limit, offset, False)
    return PhotoListResponse(
        total=data["total"],
        offset=data["offset"],
        limit=data["limit"],
        items=[PhotoRow(**item) for item in data["items"]],
    )
