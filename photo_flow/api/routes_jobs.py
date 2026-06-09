"""
Job status and SSE streaming endpoints.

GET /jobs/{job_id}     — current job state (status, result, error)
GET /events/{job_id}   — SSE stream of progress events until terminal "done" event
"""
from __future__ import annotations

import json

from fastapi import APIRouter, HTTPException, Request
from sse_starlette.sse import EventSourceResponse

router = APIRouter()


@router.get("/jobs/{job_id}")
async def get_job(job_id: str, request: Request) -> dict:
    job = request.app.state.job_manager.get_job(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="Job not found")
    return job.to_dict()


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
