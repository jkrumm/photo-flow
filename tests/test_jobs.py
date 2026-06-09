"""
Tests for JobManager, QueueReporter, and the SSE + job-status endpoints.

Strategy:
- JobManager logic tested via asyncio.run() (no pytest-asyncio dependency).
- HTTP-layer tests (GET /jobs, GET /events) use TestClient with a minimal test
  app that adds a POST /test/start route to start dummy jobs.
"""
import asyncio
import json
import time

import pytest
from fastapi import FastAPI, HTTPException, Request
from fastapi.testclient import TestClient

from photo_flow.api.jobs import Job, JobManager, QueueReporter
from photo_flow.api.routes_jobs import router as jobs_router
from photo_flow.progress import ProgressReporter


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def make_test_app(mgr: JobManager) -> FastAPI:
    """Minimal app wired with the real jobs router + a test start endpoint."""
    from contextlib import asynccontextmanager

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        app.state.job_manager = mgr
        yield

    app = FastAPI(lifespan=lifespan)
    app.include_router(jobs_router)

    @app.post("/test/start")
    async def test_start(request: Request, slow: bool = False) -> dict:
        def fn(reporter):
            reporter.task("test task", 3)
            reporter.advance()
            reporter.log("info", "step 1")
            reporter.advance()
            reporter.log("info", "step 2")
            reporter.advance()
            reporter.event("file_done", {"filename": "test.jpg"})
            if slow:
                time.sleep(0.3)
            return {"steps": 3}

        try:
            job = await request.app.state.job_manager.start("test", fn)
        except RuntimeError:
            raise HTTPException(status_code=409, detail="Job already running")
        return {"job_id": job.job_id}

    return app


# ---------------------------------------------------------------------------
# Unit tests (asyncio.run — no pytest-asyncio needed)
# ---------------------------------------------------------------------------

def test_queue_reporter_satisfies_protocol():
    """QueueReporter satisfies the ProgressReporter structural protocol."""
    loop = asyncio.new_event_loop()
    job = Job(job_id="x", op="test")
    reporter = QueueReporter(job, loop)
    assert isinstance(reporter, ProgressReporter)
    loop.close()


def test_job_manager_produces_ordered_events():
    """A dummy fn driving QueueReporter produces events in order, last is 'done'."""

    async def _inner():
        mgr = JobManager()

        def fn(reporter):
            reporter.task("work", 2)
            reporter.advance()
            reporter.log("info", "halfway")
            reporter.advance()
            reporter.event("file_done", {"filename": "a.jpg"})
            return {"processed": 2}

        job = await mgr.start("test", fn)
        q = await job.subscribe()

        received = []
        for _ in range(20):
            event = await asyncio.wait_for(q.get(), timeout=3.0)
            received.append(event)
            if event.get("type") == "done":
                break

        assert received[-1]["type"] == "done"
        assert received[-1]["result"] == {"processed": 2}
        assert received[-1]["error"] is None

        types = [e["type"] for e in received]
        assert "task" in types
        assert "advance" in types
        assert "log" in types
        assert "file_done" in types
        assert "done" in types

    asyncio.run(_inner())


def test_job_manager_single_flight():
    """Starting a second job while one is running raises RuntimeError."""

    async def _inner():
        mgr = JobManager()

        def slow(reporter):
            time.sleep(0.3)
            return {"ok": True}

        job1 = await mgr.start("slow", slow)
        assert mgr.is_running

        with pytest.raises(RuntimeError, match="already running"):
            await mgr.start("another", slow)

        # Wait for job1 to finish
        q = await job1.subscribe()
        for _ in range(20):
            ev = await asyncio.wait_for(q.get(), timeout=5.0)
            if ev.get("type") == "done":
                break

        assert not mgr.is_running
        assert job1.status == "done"

    asyncio.run(_inner())


def test_job_events_replayed_after_completion():
    """Subscribe after job completes still returns all events via history replay."""

    async def _inner():
        mgr = JobManager()

        def fn(reporter):
            reporter.task("work", 1)
            reporter.advance()
            return {"ok": True}

        job = await mgr.start("test", fn)

        # Wait for completion by draining via a first subscriber
        q1 = await job.subscribe()
        for _ in range(20):
            ev = await asyncio.wait_for(q1.get(), timeout=3.0)
            if ev.get("type") == "done":
                break

        assert job.status == "done"

        # Subscribe AFTER job is done — should get all events from replay
        q2 = await job.subscribe()
        replayed = []
        while not q2.empty():
            replayed.append(q2.get_nowait())

        assert replayed[-1]["type"] == "done"
        types = [e["type"] for e in replayed]
        assert "task" in types

    asyncio.run(_inner())


def test_failed_job_status():
    """If fn raises, job.status is 'failed' and done event has an error."""

    async def _inner():
        mgr = JobManager()

        def bad_fn(reporter):
            raise ValueError("something broke")

        job = await mgr.start("test", bad_fn)
        q = await job.subscribe()
        for _ in range(10):
            ev = await asyncio.wait_for(q.get(), timeout=3.0)
            if ev.get("type") == "done":
                break

        assert job.status == "failed"
        assert job.error == "something broke"
        assert ev["error"] == "something broke"
        assert not mgr.is_running

    asyncio.run(_inner())


# ---------------------------------------------------------------------------
# HTTP-level tests via TestClient
# ---------------------------------------------------------------------------

def test_get_job_not_found():
    mgr = JobManager()
    with TestClient(make_test_app(mgr)) as client:
        resp = client.get("/jobs/nonexistent-id")
    assert resp.status_code == 404


def test_get_job_after_start():
    mgr = JobManager()
    with TestClient(make_test_app(mgr)) as client:
        resp = client.post("/test/start")
        assert resp.status_code == 200
        job_id = resp.json()["job_id"]

        # Give the background task time to finish
        time.sleep(0.1)

        job_resp = client.get(f"/jobs/{job_id}")
    assert job_resp.status_code == 200
    data = job_resp.json()
    assert data["job_id"] == job_id
    assert data["op"] == "test"
    assert data["status"] in ("running", "done")


def test_single_flight_http_409():
    """A second POST /test/start while a slow job runs returns 409."""
    mgr = JobManager()
    with TestClient(make_test_app(mgr)) as client:
        # Start a slow job (slow=True → sleeps 0.3 s in thread)
        resp1 = client.post("/test/start?slow=true")
        assert resp1.status_code == 200

        # _running is True immediately after start() returns
        resp2 = client.post("/test/start")
    assert resp2.status_code == 409


def test_sse_events_endpoint():
    """GET /events/{job_id} streams all events ending with 'done'."""
    mgr = JobManager()
    with TestClient(make_test_app(mgr)) as client:
        # Start a fast job
        resp = client.post("/test/start")
        assert resp.status_code == 200
        job_id = resp.json()["job_id"]

        # Wait for job to complete so events are fully buffered
        time.sleep(0.1)

        events = []
        with client.stream("GET", f"/events/{job_id}") as response:
            assert response.status_code == 200
            for line in response.iter_lines():
                line = line.strip()
                if not line.startswith("data:"):
                    continue
                event = json.loads(line[len("data:"):].strip())
                events.append(event)
                if event.get("type") == "done":
                    break

    assert events, "Should have received at least one event"
    assert events[-1]["type"] == "done"
    assert events[-1]["result"] == {"steps": 3}

    types = {e["type"] for e in events}
    assert "task" in types
    assert "advance" in types
    assert "log" in types
    assert "file_done" in types


def test_events_not_found():
    mgr = JobManager()
    with TestClient(make_test_app(mgr)) as client:
        resp = client.get("/events/nonexistent-id")
    assert resp.status_code == 404
