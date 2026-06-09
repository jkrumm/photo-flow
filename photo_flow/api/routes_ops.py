"""
Operation endpoints for the photo-flow control panel.

POST /ops/import         — import files from camera to staging/RAWs/SSD
POST /ops/finalize       — move staging JPGs to Final; clean orphaned RAWs
POST /ops/cleanup        — delete orphaned RAW files (no matching Final JPG)
POST /ops/sync-gallery   — sync rating≥4 images to gallery + build + deploy
POST /ops/backup         — rclone backup to homelab (source: final|raws|videos|all)
GET  /backup/availability — check what's available and compare with remote

Confirmation flow:
  1. POST /ops/{name}?dry_run=true  → synchronous preview (NullReporter, returns result dict as 200)
  2. POST /ops/{name}               → starts a Job via JobManager, returns {"job_id": …} as 202

The single-flight lock (JobManager) prevents concurrent mutating runs.
"""
from __future__ import annotations

import asyncio
from typing import Any, Dict, List, Literal, Optional

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from photo_flow.progress import NullReporter
from photo_flow.workflow import PhotoWorkflow

router = APIRouter()

_workflow = PhotoWorkflow()

# ---------------------------------------------------------------------------
# Response models — referenced in OpenAPI so Group 8 can generate TS types
# ---------------------------------------------------------------------------


class JobStarted(BaseModel):
    job_id: str


class ImportResult(BaseModel):
    videos: int
    photos: int
    raws: int
    skipped: int
    errors: int


class FinalizeResult(BaseModel):
    moved: int
    edits_moved: int
    orphaned_raws: int
    deleted_raws: int
    deleted_camera_raws: int
    skipped: int
    errors: int


class CleanupResult(BaseModel):
    orphaned: int
    deleted: int
    errors: int


class SyncGalleryResult(BaseModel):
    scanned: int
    synced: int
    removed: int
    skipped: int
    unchanged: int
    errors: int
    json_updated: bool
    total_in_gallery: int
    build_successful: Optional[bool] = None
    sync_successful: Optional[bool] = None


class BackupResult(BaseModel):
    source: str
    scanned: int
    sync_successful: bool
    connection_method: Optional[str] = None
    trash_path: Optional[str] = None
    errors: int
    immich_scan_triggered: Optional[bool] = None


class BackupAllResult(BaseModel):
    sources: List[str]
    total_scanned: int
    all_successful: bool
    errors: int


class BackupSourceInfo(BaseModel):
    available: bool
    local_count: int
    path: Optional[str] = None
    remote_path: Optional[str] = None
    extension: Optional[str] = None
    remote_count: Optional[int] = None
    needs_sync: Optional[int] = None
    requires: Optional[str] = None


class BackupAvailabilityResponse(BaseModel):
    final: BackupSourceInfo
    raws: BackupSourceInfo
    videos: BackupSourceInfo
    connection: Optional[str] = None


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

BackupSource = Literal["final", "raws", "videos", "all"]

_409_DETAIL = "A job is already running"
_JOB_RESPONSES: dict = {
    202: {"model": JobStarted, "description": "Job started"},
    409: {"description": _409_DETAIL},
}


def _serialize(value: Any) -> Any:
    """Recursively convert non-JSON-serializable values (Path, etc.) to strings."""
    if hasattr(value, "__fspath__"):
        return str(value)
    if isinstance(value, dict):
        return {k: _serialize(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_serialize(i) for i in value]
    return value


def _run_backup(source: BackupSource, dry_run: bool, reporter: Any) -> Dict[str, Any]:
    """Dispatch to the right backup method(s). For 'all', run final→raws→videos sequentially."""
    if source == "final":
        return _workflow.backup_final_to_homelab(dry_run=dry_run, reporter=reporter)
    elif source == "raws":
        return _workflow.backup_raws_to_homelab(dry_run=dry_run, reporter=reporter)
    elif source == "videos":
        return _workflow.backup_videos_to_homelab(dry_run=dry_run, reporter=reporter)
    else:  # "all"
        results = []
        for method in (
            _workflow.backup_final_to_homelab,
            _workflow.backup_raws_to_homelab,
            _workflow.backup_videos_to_homelab,
        ):
            results.append(method(dry_run=dry_run, reporter=reporter))
        return {
            "sources": [r.get("source", "unknown") for r in results],
            "total_scanned": sum(r.get("scanned", 0) for r in results),
            "all_successful": all(r.get("sync_successful", False) for r in results),
            "errors": sum(r.get("errors", 0) for r in results),
        }


async def _start_job(
    request: Request,
    op_name: str,
    fn: Any,
) -> JSONResponse:
    """Start a mutating job; map RuntimeError → 409."""
    try:
        job = await request.app.state.job_manager.start(op_name, fn)
    except RuntimeError as exc:
        raise HTTPException(status_code=409, detail=str(exc))
    return JSONResponse({"job_id": job.job_id}, status_code=202)


# ---------------------------------------------------------------------------
# Operation endpoints
# ---------------------------------------------------------------------------


@router.post(
    "/ops/import",
    responses={200: {"model": ImportResult, "description": "Dry-run preview"}, **_JOB_RESPONSES},
)
async def op_import(request: Request, dry_run: bool = False):
    """
    Import files from camera to staging/RAWs/SSD.

    - `dry_run=true` returns a preview dict synchronously (no files moved).
    - `dry_run=false` starts a background job and returns `{"job_id": ...}` (202).
    """
    if dry_run:
        result = _workflow.import_from_camera(dry_run=True, reporter=NullReporter())
        return _serialize(result)
    return await _start_job(
        request,
        "import",
        lambda reporter: _workflow.import_from_camera(dry_run=False, reporter=reporter),
    )


@router.post(
    "/ops/finalize",
    responses={200: {"model": FinalizeResult, "description": "Dry-run preview"}, **_JOB_RESPONSES},
)
async def op_finalize(request: Request, dry_run: bool = False):
    """
    Move staging JPGs to Final at full quality; carry .photo-edit sidecars; clean orphaned RAWs.

    - `dry_run=true` returns a preview dict synchronously (no files moved).
    - `dry_run=false` starts a background job and returns `{"job_id": ...}` (202).
    """
    if dry_run:
        result = _workflow.finalize_staging(dry_run=True, reporter=NullReporter())
        return _serialize(result)
    return await _start_job(
        request,
        "finalize",
        lambda reporter: _workflow.finalize_staging(dry_run=False, reporter=reporter),
    )


@router.post(
    "/ops/cleanup",
    responses={200: {"model": CleanupResult, "description": "Dry-run preview"}, **_JOB_RESPONSES},
)
async def op_cleanup(request: Request, dry_run: bool = False):
    """
    Delete orphaned RAW files (RAFs without a matching Final JPG).

    - `dry_run=true` returns the orphan count synchronously (no deletions).
    - `dry_run=false` starts a background job and returns `{"job_id": ...}` (202).
    """
    if dry_run:
        result = _workflow.cleanup_unused_raws(dry_run=True, reporter=NullReporter())
        return _serialize(result)
    return await _start_job(
        request,
        "cleanup",
        lambda reporter: _workflow.cleanup_unused_raws(dry_run=False, reporter=reporter),
    )


@router.post(
    "/ops/sync-gallery",
    responses={
        200: {"model": SyncGalleryResult, "description": "Dry-run preview"},
        **_JOB_RESPONSES,
    },
)
async def op_sync_gallery(request: Request, dry_run: bool = False):
    """
    Sync rating≥4 images to gallery, build with npm, and rsync to the remote server.

    - `dry_run=true` returns a preview dict synchronously (no files copied, no build/sync).
    - `dry_run=false` starts a background job and returns `{"job_id": ...}` (202).
    """
    if dry_run:
        result = _workflow.sync_gallery(dry_run=True, reporter=NullReporter())
        return _serialize(result)
    return await _start_job(
        request,
        "sync-gallery",
        lambda reporter: _workflow.sync_gallery(dry_run=False, reporter=reporter),
    )


@router.post(
    "/ops/backup",
    responses={
        200: {"description": "Dry-run preview (BackupResult or BackupAllResult)"},
        **_JOB_RESPONSES,
    },
)
async def op_backup(
    request: Request,
    source: BackupSource = "all",
    dry_run: bool = False,
):
    """
    Backup to homelab via rclone over Tailscale.

    - `source`: `final` | `raws` | `videos` | `all` (default: `all`).
      `all` runs final → raws → videos sequentially within one job.
    - `dry_run=true` returns a preview dict synchronously (no remote changes).
    - `dry_run=false` starts a background job and returns `{"job_id": ...}` (202).
    """
    if dry_run:
        result = _run_backup(source, dry_run=True, reporter=NullReporter())
        return _serialize(result)
    # Bind source in the closure explicitly to avoid any late-binding confusion.
    _source = source
    return await _start_job(
        request,
        f"backup:{_source}",
        lambda reporter, s=_source: _run_backup(s, dry_run=False, reporter=reporter),
    )


# ---------------------------------------------------------------------------
# Backup availability (not under /ops prefix — lives at /backup/availability)
# ---------------------------------------------------------------------------


@router.get("/backup/availability", response_model=BackupAvailabilityResponse)
async def backup_availability(check_remote: bool = False) -> BackupAvailabilityResponse:
    """
    Check which backup sources (final/raws/videos) are available locally, and optionally
    compare with remote file counts via SSH.

    - `check_remote=false` (default): near-instant, path-existence checks only.
    - `check_remote=true`: SSH to homelab for remote counts (may take a few seconds).
    """
    raw = await asyncio.to_thread(_workflow.get_backup_availability, check_remote)
    connection = raw.pop("_connection", None)
    serialized = _serialize(raw)
    serialized["connection"] = connection
    return BackupAvailabilityResponse(**{
        "final": BackupSourceInfo(**serialized.get("final", {})),
        "raws": BackupSourceInfo(**serialized.get("raws", {})),
        "videos": BackupSourceInfo(**serialized.get("videos", {})),
        "connection": connection,
    })
