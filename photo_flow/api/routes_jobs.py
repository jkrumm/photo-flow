"""
Job status and SSE streaming endpoints.

GET  /jobs              — list of queued + running + recent terminal jobs
GET  /jobs/active       — currently running job (or null), for reload re-attach
GET  /jobs/stream       — SSE of manager-level lifecycle events (job_queued, job_started, …)
GET  /jobs/{job_id}     — current job state (status, result, error)
POST /jobs/{job_id}/cancel — request cooperative cancellation (running or queued)
POST /jobs/{job_id}/move   — move a queued job one slot {up|down}
GET  /events/{job_id}   — SSE stream of per-job progress events until terminal "done" event
"""
from __future__ import annotations

import asyncio
import json
from typing import Literal

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel
from sse_starlette.sse import EventSourceResponse

router = APIRouter()


@router.get("/jobs")
async def list_jobs(request: Request) -> dict:
    """List of queued + running + needs_confirm + recent terminal jobs.

    Order: queued (ascending by position) → running → needs_confirm → recent terminal (last 10).
    """
    jobs = request.app.state.job_manager.get_jobs()
    return {"jobs": [j.to_dict() for j in jobs]}


@router.get("/jobs/active")
async def get_active_job(request: Request) -> dict:
    """The job currently running (or null), so a reloaded/reopened page can re-attach.

    Jobs run server-side in the always-on service — closing the tab or reloading does
    not stop them. The UI polls this on load and resubscribes to the SSE stream, which
    replays the event history so progress resumes seamlessly.
    """
    job = request.app.state.job_manager.current_job
    if job is None:
        return {"active": None}
    return {"active": {"job_id": job.job_id, "op": job.op, "status": job.status}}


@router.get("/jobs/stream")
async def stream_manager(request: Request) -> EventSourceResponse:
    """Manager-level lifecycle events via SSE.

    Emits: job_queued | job_started | job_finished | job_needs_confirm | queue_reordered

    Unlike /events/{job_id} (which replays history), this stream is live-only. The
    client uses it to drive queue-panel updates and auto-subscribe to new running jobs.
    A 30 s keepalive comment prevents transparent proxies from closing the connection.
    """
    mgr = request.app.state.job_manager

    async def generator():
        q = await mgr.subscribe_manager()
        try:
            while True:
                try:
                    event = await asyncio.wait_for(q.get(), timeout=30)
                    yield {"data": json.dumps(event)}
                except asyncio.TimeoutError:
                    yield {"comment": "keepalive"}
        finally:
            mgr.unsubscribe_manager(q)

    return EventSourceResponse(generator())


@router.get("/jobs/{job_id}")
async def get_job(job_id: str, request: Request) -> dict:
    job = request.app.state.job_manager.get_job(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="Job not found")
    return job.to_dict()


@router.post("/jobs/{job_id}/cancel")
async def cancel_job(job_id: str, request: Request) -> dict:
    """Request cooperative cancellation of a running or queued job.

    Running → the workflow stops at its next per-file checkpoint; already-copied
    files stay (each file is copy-then-delete atomic), so the state is always consistent.
    Queued  → the worker skips the job at dequeue time.
    needs_confirm → immediately marks the job cancelled.
    """
    job = request.app.state.job_manager.get_job(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="Job not found")
    cancelled = request.app.state.job_manager.cancel(job_id)
    if not cancelled:
        raise HTTPException(
            status_code=409,
            detail=f"Job cannot be cancelled (status: {job.status})",
        )
    return {"cancelled": True, "job_id": job_id}


class MoveBody(BaseModel):
    direction: Literal["up", "down"]


@router.post("/jobs/{job_id}/move")
async def move_job(job_id: str, body: MoveBody, request: Request) -> dict:
    """Move a QUEUED job one position up or down in the queue.

    No-op for running or terminal jobs (returns moved=false). The reorder is
    synchronous and safe — the drain+refill of the asyncio.Queue has no yield
    points so the single worker cannot interleave mid-reorder.
    """
    moved = request.app.state.job_manager.reorder_job(job_id, body.direction)
    return {"moved": moved, "job_id": job_id}


@router.get("/events/{job_id}")
async def stream_events(job_id: str, request: Request) -> EventSourceResponse:
    """Stream job progress events via SSE until the terminal 'done' event.

    Past events are replayed on subscribe so clients that connect after job
    completion still receive the full event history.
    """
    job = request.app.state.job_manager.get_job(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="Job not found")

    async def generator():
        q = await job.subscribe()
        try:
            while True:
                event = await q.get()
                yield {"data": json.dumps(event)}
                if event.get("type") == "done":
                    break
        finally:
            job.unsubscribe(q)

    return EventSourceResponse(generator())
