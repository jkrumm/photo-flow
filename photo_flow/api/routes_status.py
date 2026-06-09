"""
Status endpoints for the photo-flow control panel.

Two tiers:
- GET /status  — cheap, pollable every 2–3s: device mounts + cached staging count.
  Must return in well under 100ms.
- GET /status/pending — expensive: walks the camera SD card via scan_camera_files().
  Only polled on demand or when camera is present.
"""
from __future__ import annotations

import asyncio
import time
from typing import Dict

from fastapi import APIRouter

from photo_flow.config import CAMERA_PATH, SSD_PATH, STAGING_PATH
from photo_flow.file_manager import FileManager, scan_for_images

router = APIRouter()

# Staging count cache — refreshed every 5 s to avoid redundant globs on hot polling.
_staging_cache: Dict[str, object] = {"count": 0, "ts": 0.0}
_CACHE_TTL = 5.0

_file_manager = FileManager()


def _get_staging_count() -> int:
    now = time.monotonic()
    if now - float(_staging_cache["ts"]) > _CACHE_TTL:
        count = len(scan_for_images(STAGING_PATH, ".JPG")) if STAGING_PATH.exists() else 0
        _staging_cache["count"] = count
        _staging_cache["ts"] = now
    return int(_staging_cache["count"])


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
