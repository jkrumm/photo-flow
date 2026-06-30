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
  2. POST /ops/{name}               → enqueues a Job via JobManager, returns
     {"job_id": …, "status": "queued", "position": N} as 202

Jobs are FIFO-queued — never rejected 409 for "already running".
For destructive ops (cleanup / finalize) the confirmed dry-run preview is sent
in the request body so the worker can re-validate before executing.
"""
from __future__ import annotations

import asyncio
from typing import Any, Dict, List, Literal, Optional

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from photo_flow.index.indexer import reindex as _run_reindex
from photo_flow.progress import NullReporter
from photo_flow.workflow import PhotoWorkflow

router = APIRouter()

_workflow = PhotoWorkflow()


def _with_reindex(fn):
    """Wrap a job function to trigger an incremental reindex on successful completion.

    The reindex runs in the same worker thread immediately after the op finishes.
    Errors in the reindex are swallowed so they never fail the job itself.
    """
    def wrapped(reporter):
        result = fn(reporter)
        try:
            _run_reindex()
        except Exception:
            pass
        return result
    return wrapped

# ---------------------------------------------------------------------------
# Response models — referenced in OpenAPI so Group 8 can generate TS types
# ---------------------------------------------------------------------------


class JobEnqueued(BaseModel):
    job_id: str
    status: str
    position: int


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


class DestructiveJobBody(BaseModel):
    """Optional request body for destructive ops.

    `approved_preview` is the dry-run result dict the user confirmed.  The
    worker re-runs the dry-run at dispatch time and compares destructive counts;
    if the new count is larger than what the user approved, the job is parked in
    `needs_confirm` so the user can re-confirm with the updated numbers.
    """
    approved_preview: Optional[Dict[str, Any]] = None


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

BackupSource = Literal["final", "raws", "videos", "all"]

_JOB_RESPONSES: dict = {
    202: {"model": JobEnqueued, "description": "Job enqueued"},
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


async def _enqueue_job(
    request: Request,
    op_name: str,
    fn: Any,
    *,
    destructive_preview: Optional[Dict[str, Any]] = None,
    revalidator: Any = None,
) -> JSONResponse:
    """Enqueue a mutating job; returns job_id + status + position (202)."""
    job = await request.app.state.job_manager.enqueue(
        op_name,
        fn,
        destructive_preview=destructive_preview,
        revalidator=revalidator,
    )
    return JSONResponse(
        {"job_id": job.job_id, "status": job.status, "position": job.position},
        status_code=202,
    )


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
    - `dry_run=false` enqueues a background job and returns `{"job_id": ..., "status": "queued", "position": N}` (202).
    """
    if dry_run:
        result = _workflow.import_from_camera(dry_run=True, reporter=NullReporter())
        return _serialize(result)
    return await _enqueue_job(
        request,
        "import",
        lambda reporter: _workflow.import_from_camera(dry_run=False, reporter=reporter),
    )


@router.post(
    "/ops/finalize",
    responses={200: {"model": FinalizeResult, "description": "Dry-run preview"}, **_JOB_RESPONSES},
)
async def op_finalize(
    request: Request,
    dry_run: bool = False,
    body: Optional[DestructiveJobBody] = None,
):
    """
    Move staging JPGs to Final at full quality; carry .photo-edit sidecars; clean orphaned RAWs.

    - `dry_run=true` returns a preview dict synchronously (no files moved).
    - `dry_run=false` enqueues a background job.
      Send `{"approved_preview": <dry-run result>}` as the JSON body so the worker can
      re-validate the orphaned-RAW count at dispatch time (data-loss guard).
    """
    if dry_run:
        result = _workflow.finalize_staging(dry_run=True, reporter=NullReporter())
        return _serialize(result)

    approved_preview = body.approved_preview if body is not None else None

    async def _revalidator() -> Dict[str, Any]:
        return await asyncio.to_thread(  # type: ignore[return-value]
            _workflow.finalize_staging, dry_run=True, reporter=NullReporter()
        )

    return await _enqueue_job(
        request,
        "finalize",
        _with_reindex(lambda reporter: _workflow.finalize_staging(dry_run=False, reporter=reporter)),
        destructive_preview=approved_preview if approved_preview is not None else {},
        revalidator=_revalidator,
    )


@router.post(
    "/ops/cleanup",
    responses={200: {"model": CleanupResult, "description": "Dry-run preview"}, **_JOB_RESPONSES},
)
async def op_cleanup(
    request: Request,
    dry_run: bool = False,
    body: Optional[DestructiveJobBody] = None,
):
    """
    Delete orphaned RAW files (RAFs without a matching Final JPG).

    - `dry_run=true` returns the orphan count synchronously (no deletions).
    - `dry_run=false` enqueues a background job.
      Send `{"approved_preview": <dry-run result>}` as the JSON body so the worker can
      re-validate the orphan count at dispatch time (data-loss guard).
    """
    if dry_run:
        result = _workflow.cleanup_unused_raws(dry_run=True, reporter=NullReporter())
        return _serialize(result)

    approved_preview = body.approved_preview if body is not None else None

    async def _revalidator() -> Dict[str, Any]:
        return await asyncio.to_thread(  # type: ignore[return-value]
            _workflow.cleanup_unused_raws, dry_run=True, reporter=NullReporter()
        )

    return await _enqueue_job(
        request,
        "cleanup",
        lambda reporter: _workflow.cleanup_unused_raws(dry_run=False, reporter=reporter),
        destructive_preview=approved_preview if approved_preview is not None else {},
        revalidator=_revalidator,
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
    - `dry_run=false` enqueues a background job.
    """
    if dry_run:
        result = _workflow.sync_gallery(dry_run=True, reporter=NullReporter())
        return _serialize(result)
    return await _enqueue_job(
        request,
        "sync-gallery",
        _with_reindex(lambda reporter: _workflow.sync_gallery(dry_run=False, reporter=reporter)),
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
    - `dry_run=false` enqueues a background job.
    """
    if dry_run:
        result = _run_backup(source, dry_run=True, reporter=NullReporter())
        return _serialize(result)
    # Bind source in the closure explicitly to avoid any late-binding confusion.
    _source = source
    return await _enqueue_job(
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


class GallerySyncStatusResponse(BaseModel):
    target: int          # rating>=4 Final photos that should be published
    current: int         # image files currently in the gallery folder
    pending: int         # symmetric diff (to publish + to remove); 0 == in sync
    up_to_date: bool


@router.get("/gallery/status", response_model=GallerySyncStatusResponse)
async def gallery_status() -> GallerySyncStatusResponse:
    """
    Cheap gallery freshness signal for the UI: compares rating>=4 Final filenames (index) to the
    gallery images folder. Near-instant (one index query + one dir glob, no hashing/build).
    """
    raw = await asyncio.to_thread(_workflow.get_gallery_sync_status)
    return GallerySyncStatusResponse(**raw)
