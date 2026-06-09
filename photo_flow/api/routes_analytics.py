"""
Analytics endpoints for the photo-flow control panel.

All read queries run against the SQLite metadata index (~/.photoflow/index.db).
For stages outside the index (Staging, RAWs, Videos), storage sizes come from a
quick filesystem glob — bounded and fast.

Endpoints:
  GET /analytics/summary     — headline tiles (totals, avg rating, date range)
  GET /analytics/over-time   — photos-over-time with rating-band overlay
  GET /analytics/ratings     — rating histogram + Final-total vs published
  GET /analytics/settings    — ISO / aperture / focal / shutter distributions
  GET /analytics/storage     — byte + file counts per pipeline stage
  GET /analytics/map         — GPS coordinates for map visualisation
  POST /index/refresh        — trigger incremental reindex of the Final folder
"""
from __future__ import annotations

import asyncio
import sqlite3
from typing import Any, Dict, List, Literal, Optional

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel

from photo_flow.config import FINAL_PATH, RAWS_PATH, STAGING_PATH, SSD_PATH
from photo_flow.index.db import get_db
from photo_flow.index.indexer import reindex

router = APIRouter()

# Single lock — prevents concurrent /index/refresh calls.
# Lazy-initialised so it's always bound to the running event loop.
_refresh_lock: Optional[asyncio.Lock] = None


def _get_refresh_lock() -> asyncio.Lock:
    global _refresh_lock
    if _refresh_lock is None:
        _refresh_lock = asyncio.Lock()
    return _refresh_lock


# ---------------------------------------------------------------------------
# DB helper — extracted so tests can monkeypatch cleanly
# ---------------------------------------------------------------------------


def _open_conn() -> sqlite3.Connection:
    """Open a connection to the default index DB. Caller must close."""
    return get_db()


# ---------------------------------------------------------------------------
# Pydantic response models
# ---------------------------------------------------------------------------


class BucketPoint(BaseModel):
    x: str
    total: int
    high_rated: int
    other: int


class RatingsResponse(BaseModel):
    histogram: List[Dict[str, Any]]
    total_final: int
    total_published: int


class SettingsResponse(BaseModel):
    iso: List[Dict[str, Any]]
    aperture: List[Dict[str, Any]]
    focal: List[Dict[str, Any]]
    shutter: List[Dict[str, Any]]


class StageStorage(BaseModel):
    count: int
    bytes: int
    available: bool


class StorageResponse(BaseModel):
    final: StageStorage
    staging: StageStorage
    raws: StageStorage
    videos: StageStorage


class MapPoint(BaseModel):
    lat: float
    lng: float
    filename: str
    date_taken: Optional[str] = None


class SummaryResponse(BaseModel):
    total_photos: int
    total_published: int
    this_month_count: int
    avg_rating: Optional[float]
    earliest_date: Optional[str]
    latest_date: Optional[str]


class RefreshResponse(BaseModel):
    indexed: int
    updated: int
    skipped: int
    removed: int


# ---------------------------------------------------------------------------
# Bucket format map
# ---------------------------------------------------------------------------

_BUCKET_FMTS: Dict[str, str] = {
    "day":   "%Y-%m-%d",
    "week":  "%Y-W%W",
    "month": "%Y-%m",
    "year":  "%Y",
}


def _shutter_label(s: float) -> str:
    """Human-readable shutter label: 0.004 → '1/250', 1.5 → '1.5s'."""
    if s <= 0:
        return "?"
    if s >= 1.0:
        return f"{s:.1f}s"
    denominator = round(1.0 / s)
    return f"1/{denominator}"


# ---------------------------------------------------------------------------
# Synchronous DB query helpers (run via asyncio.to_thread)
# ---------------------------------------------------------------------------


def _query_over_time(bucket: str) -> List[Dict[str, Any]]:
    fmt = _BUCKET_FMTS[bucket]
    sql = f"""
        SELECT
            strftime('{fmt}', date_taken) AS x,
            COUNT(*) AS total,
            COALESCE(SUM(CASE WHEN rating >= 4 THEN 1 ELSE 0 END), 0) AS high_rated,
            COALESCE(SUM(CASE WHEN rating < 4 OR rating IS NULL THEN 1 ELSE 0 END), 0) AS other
        FROM photos
        WHERE in_final = 1 AND date_taken IS NOT NULL
        GROUP BY x
        ORDER BY x
    """
    conn = _open_conn()
    try:
        return [
            {
                "x": r["x"],
                "total": r["total"],
                "high_rated": r["high_rated"],
                "other": r["other"],
            }
            for r in conn.execute(sql).fetchall()
        ]
    finally:
        conn.close()


def _query_ratings() -> Dict[str, Any]:
    conn = _open_conn()
    try:
        histogram = conn.execute(
            """
            SELECT rating, COUNT(*) AS count
            FROM photos
            WHERE in_final = 1
            GROUP BY rating
            ORDER BY rating
            """
        ).fetchall()
        totals = conn.execute(
            """
            SELECT
                COUNT(*) AS total_final,
                COALESCE(SUM(CASE WHEN published = 1 THEN 1 ELSE 0 END), 0) AS total_published
            FROM photos
            WHERE in_final = 1
            """
        ).fetchone()
        return {
            "histogram": [{"rating": r["rating"], "count": r["count"]} for r in histogram],
            "total_final": totals["total_final"] or 0,
            "total_published": totals["total_published"] or 0,
        }
    finally:
        conn.close()


def _query_settings() -> Dict[str, Any]:
    conn = _open_conn()
    try:
        iso = conn.execute(
            """
            SELECT iso AS value, COUNT(*) AS count
            FROM photos
            WHERE in_final = 1 AND iso IS NOT NULL
            GROUP BY iso ORDER BY iso
            """
        ).fetchall()
        aperture = conn.execute(
            """
            SELECT ROUND(aperture_f, 1) AS value, COUNT(*) AS count
            FROM photos
            WHERE in_final = 1 AND aperture_f IS NOT NULL
            GROUP BY value ORDER BY value
            """
        ).fetchall()
        focal = conn.execute(
            """
            SELECT ROUND(focal_mm, 0) AS value, COUNT(*) AS count
            FROM photos
            WHERE in_final = 1 AND focal_mm IS NOT NULL
            GROUP BY value ORDER BY value
            """
        ).fetchall()
        shutter = conn.execute(
            """
            SELECT shutter_s AS value, COUNT(*) AS count
            FROM photos
            WHERE in_final = 1 AND shutter_s IS NOT NULL
            GROUP BY value ORDER BY value
            """
        ).fetchall()
        return {
            "iso": [{"value": r["value"], "count": r["count"]} for r in iso],
            "aperture": [{"value": r["value"], "count": r["count"]} for r in aperture],
            "focal": [{"value": r["value"], "count": r["count"]} for r in focal],
            "shutter": [
                {
                    "value": r["value"],
                    "count": r["count"],
                    "label": _shutter_label(r["value"]) if r["value"] else "",
                }
                for r in shutter
            ],
        }
    finally:
        conn.close()


def _query_storage() -> Dict[str, Any]:
    conn = _open_conn()
    try:
        final_row = conn.execute(
            "SELECT COUNT(*) AS count, COALESCE(SUM(size), 0) AS bytes FROM photos WHERE in_final = 1"
        ).fetchone()
    finally:
        conn.close()

    def _stage(path, glob_pattern: str) -> Dict[str, Any]:
        if not path.exists():
            return {"count": 0, "bytes": 0, "available": False}
        files = [f for f in path.glob(glob_pattern) if f.is_file()]
        total_bytes = sum(f.stat().st_size for f in files)
        return {"count": len(files), "bytes": total_bytes, "available": True}

    return {
        "final": {
            "count": final_row["count"],
            "bytes": final_row["bytes"],
            "available": FINAL_PATH.exists(),
        },
        "staging": _stage(STAGING_PATH, "*.JPG"),
        "raws": _stage(RAWS_PATH, "*.RAF"),
        "videos": _stage(SSD_PATH, "*.MOV"),
    }


def _query_map() -> List[Dict[str, Any]]:
    conn = _open_conn()
    try:
        rows = conn.execute(
            """
            SELECT latitude, longitude, filename, date_taken
            FROM photos
            WHERE in_final = 1 AND latitude IS NOT NULL AND longitude IS NOT NULL
            ORDER BY date_taken
            """
        ).fetchall()
        return [
            {
                "lat": r["latitude"],
                "lng": r["longitude"],
                "filename": r["filename"],
                "date_taken": r["date_taken"],
            }
            for r in rows
        ]
    finally:
        conn.close()


def _query_summary() -> Dict[str, Any]:
    conn = _open_conn()
    try:
        row = conn.execute(
            """
            SELECT
                COUNT(*) AS total_photos,
                COALESCE(SUM(CASE WHEN published = 1 THEN 1 ELSE 0 END), 0) AS total_published,
                AVG(CAST(rating AS REAL)) AS avg_rating,
                MIN(date_taken) AS earliest_date,
                MAX(date_taken) AS latest_date
            FROM photos
            WHERE in_final = 1
            """
        ).fetchone()
        this_month = conn.execute(
            """
            SELECT COUNT(*) AS count
            FROM photos
            WHERE in_final = 1
              AND date_taken IS NOT NULL
              AND date_taken >= strftime('%Y-%m-01', 'now')
            """
        ).fetchone()
        avg = row["avg_rating"]
        return {
            "total_photos": row["total_photos"] or 0,
            "total_published": row["total_published"] or 0,
            "this_month_count": this_month["count"] or 0,
            "avg_rating": round(avg, 2) if avg is not None else None,
            "earliest_date": row["earliest_date"],
            "latest_date": row["latest_date"],
        }
    finally:
        conn.close()


def _run_reindex() -> Dict[str, int]:
    return reindex()


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------


@router.get("/analytics/over-time", response_model=List[BucketPoint])
async def analytics_over_time(
    bucket: Literal["day", "week", "month", "year"] = Query(default="month"),
) -> List[BucketPoint]:
    """Photos-over-time bucketed by day/week/month/year with rating-band (≥4 vs <4) overlay."""
    rows = await asyncio.to_thread(_query_over_time, bucket)
    return [BucketPoint(**r) for r in rows]


@router.get("/analytics/ratings", response_model=RatingsResponse)
async def analytics_ratings() -> RatingsResponse:
    """Rating histogram (0–5 + NULL) plus Final-total and published counts."""
    data = await asyncio.to_thread(_query_ratings)
    return RatingsResponse(**data)


@router.get("/analytics/settings", response_model=SettingsResponse)
async def analytics_settings() -> SettingsResponse:
    """ISO / aperture / focal-length / shutter distributions (value → count pairs)."""
    data = await asyncio.to_thread(_query_settings)
    return SettingsResponse(**data)


@router.get("/analytics/storage", response_model=StorageResponse)
async def analytics_storage() -> StorageResponse:
    """Byte + file counts per pipeline stage: Final (from index), Staging/RAWs/Videos (glob)."""
    data = await asyncio.to_thread(_query_storage)
    return StorageResponse(**{k: StageStorage(**v) for k, v in data.items()})


@router.get("/analytics/map", response_model=List[MapPoint])
async def analytics_map() -> List[MapPoint]:
    """GPS coordinates for all Final photos that have location data."""
    rows = await asyncio.to_thread(_query_map)
    return [MapPoint(**r) for r in rows]


@router.get("/analytics/summary", response_model=SummaryResponse)
async def analytics_summary() -> SummaryResponse:
    """Headline tiles: total photos, published count, this-month count, avg rating, date range."""
    data = await asyncio.to_thread(_query_summary)
    return SummaryResponse(**data)


@router.post("/index/refresh", response_model=RefreshResponse)
async def index_refresh() -> RefreshResponse:
    """
    Trigger an incremental reindex of the Final folder.

    Only mtime-changed files are re-read. A 409 is returned if a refresh is already running.
    """
    lock = _get_refresh_lock()
    if lock.locked():
        raise HTTPException(status_code=409, detail="Refresh already running")
    async with lock:
        counts = await asyncio.to_thread(_run_reindex)
    return RefreshResponse(**counts)
