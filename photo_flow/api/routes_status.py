"""
Status endpoints for the photo-flow control panel.

Three tiers:
- GET /status        — cheap, pollable every 2–3s: device mounts + cached staging count.
  Must return in well under 100ms.
- GET /status/pending — expensive: walks the camera SD card via scan_camera_files().
  Only polled on demand or when camera is present.
- GET /status/pipeline — full pipeline snapshot for the hero UI: all fields in one call.
  Expensive fields (camera scan, orphaned-RAW count) are TTL-cached at ~5 s.
"""
from __future__ import annotations

import asyncio
import json
import time
from pathlib import Path
from typing import Dict

from fastapi import APIRouter

from photo_flow.config import CAMERA_PATH, RAWS_PATH, SSD_PATH, STAGING_PATH
from photo_flow.file_manager import FileManager, scan_for_images
from photo_flow.index.db import get_db
from photo_flow.timestamp_renamer import extract_original_base, is_already_renamed
from photo_flow.workflow import compute_raw_keep_bases

router = APIRouter()

# ---------------------------------------------------------------------------
# Shared cache TTL (seconds)
# ---------------------------------------------------------------------------

_CACHE_TTL = 5.0

# ---------------------------------------------------------------------------
# Staging count cache — refreshed every _CACHE_TTL to avoid redundant globs.
# ---------------------------------------------------------------------------

_staging_cache: Dict[str, object] = {"count": 0, "ts": 0.0}

_file_manager = FileManager()


def _get_staging_count() -> int:
    now = time.monotonic()
    if now - float(_staging_cache["ts"]) > _CACHE_TTL:
        count = len(scan_for_images(STAGING_PATH, ".JPG")) if STAGING_PATH.exists() else 0
        _staging_cache["count"] = count
        _staging_cache["ts"] = now
    return int(_staging_cache["count"])


# ---------------------------------------------------------------------------
# Pipeline-snapshot helpers (all TTL-cached)
# ---------------------------------------------------------------------------

# Pending-scan cache (expensive: walks the SD card via scan_camera_files).
_pending_cache: Dict[str, object] = {"photos": 0, "videos": 0, "raws": 0, "ts": 0.0}
# Orphaned-RAW cache (expensive: walks FINAL_PATH + STAGING_PATH + RAWS_PATH).
_orphaned_cache: Dict[str, object] = {"count": 0, "ts": 0.0}

# Path to the last-run state file (written by the job manager after each terminal job).
_LAST_RUN_PATH = Path.home() / ".photoflow" / "last_run.json"
_LAST_RUN_KEYS = ("import", "finalize", "sync-gallery", "backup", "cleanup")


def _get_pending_counts() -> Dict[str, int]:
    """Scan camera SD card for pending file counts; returns zeros when disconnected.

    Cached for _CACHE_TTL seconds (including the disconnected case) so rapid polling
    of /status/pipeline never re-walks the SD card on every request.
    """
    now = time.monotonic()
    if now - float(_pending_cache["ts"]) > _CACHE_TTL:
        result: Dict[str, int] = {"photos": 0, "videos": 0, "raws": 0}
        if CAMERA_PATH.exists():
            try:
                files = _file_manager.scan_camera_files()
                result = {
                    "photos": len(files.get(".JPG", [])),
                    "videos": len(files.get(".MOV", [])),
                    "raws": len(files.get(".RAF", [])),
                }
            except Exception:
                pass  # Leave zeros on scan error
        _pending_cache.update({**result, "ts": now})
    return {
        "photos": int(_pending_cache["photos"]),
        "videos": int(_pending_cache["videos"]),
        "raws": int(_pending_cache["raws"]),
    }


def _get_final_stats() -> Dict[str, int]:
    """Query the SQLite index for final_count and unpublished_high_rated.

    `unpublished_high_rated` = photos with rating>=4 not yet synced to the gallery
    (published=0 means the file is absent from GALLERY_PATH/images/ at last index).
    Returns zeros when the index doesn't exist yet or any query error occurs.
    """
    try:
        conn = get_db()
        try:
            row = conn.execute(
                """
                SELECT
                    COUNT(*) AS final_count,
                    COALESCE(SUM(CASE WHEN rating >= 4 AND published = 0 THEN 1 ELSE 0 END), 0)
                        AS unpublished_high_rated
                FROM photos
                WHERE in_final = 1
                """
            ).fetchone()
            return {
                "final_count": row["final_count"] or 0,
                "unpublished_high_rated": row["unpublished_high_rated"] or 0,
            }
        finally:
            conn.close()
    except Exception:
        return {"final_count": 0, "unpublished_high_rated": 0}


def _get_orphaned_raws() -> int:
    """Count local RAFs with no matching JPG in Final∪Staging (data-loss-safe keep-set).

    Uses compute_raw_keep_bases() — the same Photomator-suffix-tolerant, Staging-aware
    logic as cleanup_unused_raws — and is TTL-cached to avoid re-walking the volumes
    on every /status/pipeline poll.  Returns 0 when RAWS_PATH is not mounted.
    """
    now = time.monotonic()
    if now - float(_orphaned_cache["ts"]) > _CACHE_TTL:
        count = 0
        if RAWS_PATH.exists():
            try:
                keep_bases = compute_raw_keep_bases()
                raf_files = (
                    list(RAWS_PATH.glob("*.RAF")) + list(RAWS_PATH.glob("*.raf"))
                )
                for raf in raf_files:
                    base = (
                        extract_original_base(raf.name)
                        if is_already_renamed(raf.name)
                        else raf.stem
                    )
                    if base not in keep_bases:
                        count += 1
            except Exception:
                pass  # Leave count as 0 on scan error
        _orphaned_cache.update({"count": count, "ts": now})
    return int(_orphaned_cache["count"])


def _read_last_runs() -> dict:
    """Read the last terminal-run state for each op from ~/.photoflow/last_run.json.

    Returns a dict mapping op keys to {"ts": ISO8601, "ok": bool, "interrupted": bool}
    or null. `interrupted` is written by the startup sweep for a job the previous
    process was still running: the advisor has to be able to tell "backed up two hours
    ago" from "started a backup two hours ago and the server died mid-transfer".
    Never raises — returns nulls for all keys on any read/parse error.
    """
    try:
        data = json.loads(_LAST_RUN_PATH.read_text())
        return {
            k: {
                "ts": data[k]["ts"],
                "ok": data[k]["ok"],
                "interrupted": bool(data[k].get("interrupted", False)),
            }
            if k in data
            else None
            for k in _LAST_RUN_KEYS
        }
    except Exception:
        return {k: None for k in _LAST_RUN_KEYS}


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------


@router.get("/status")
async def get_status() -> dict:
    """Cheap status — device mounts + cached staging count. Returns in <100 ms."""
    staging = await asyncio.to_thread(_get_staging_count)
    return {
        "camera_connected": CAMERA_PATH.exists(),
        "ssd_connected": SSD_PATH.exists(),
        "staging_files": staging,
    }


@router.get("/status/pending")
async def get_pending() -> dict:
    """Expensive status — walks camera SD card for pending file counts."""
    files = await asyncio.to_thread(_file_manager.scan_camera_files)
    return {
        "pending_videos": len(files.get(".MOV", [])),
        "pending_photos": len(files.get(".JPG", [])),
        "pending_raws": len(files.get(".RAF", [])),
    }


@router.get("/status/pipeline")
async def get_pipeline() -> dict:
    """Full pipeline snapshot for the pipeline hero — all fields in one call.

    Cheap fields (mount checks, staging count) are re-evaluated each call.
    Expensive fields (pending camera scan, orphaned-RAW count) are TTL-cached at
    ~5 s so rapid polling does not re-walk the SD card or RAWs volume on every
    request.  No SSH/remote probe in this path — backup connectivity lives in
    GET /backup/availability.
    """
    staging, pending, final_stats, orphaned, last_runs = await asyncio.gather(
        asyncio.to_thread(_get_staging_count),
        asyncio.to_thread(_get_pending_counts),
        asyncio.to_thread(_get_final_stats),
        asyncio.to_thread(_get_orphaned_raws),
        asyncio.to_thread(_read_last_runs),
    )
    return {
        "camera_connected": CAMERA_PATH.exists(),
        "ssd_connected": SSD_PATH.exists(),
        "staging_files": staging,
        "pending_photos": pending["photos"],
        "pending_videos": pending["videos"],
        "pending_raws": pending["raws"],
        "final_count": final_stats["final_count"],
        "unpublished_high_rated": final_stats["unpublished_high_rated"],
        "orphaned_raws": orphaned,
        "last_runs": last_runs,
    }
