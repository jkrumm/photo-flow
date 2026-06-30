"""
Tests for JobManager, QueueReporter, and the SSE + job-status endpoints.

Strategy:
- JobManager logic tested via asyncio.run() (no pytest-asyncio dependency).
- HTTP-layer tests (GET /jobs, GET /events) use TestClient with a minimal test
  app that adds a POST /test/start route to enqueue dummy jobs.
"""
import asyncio
import json
import time
from pathlib import Path

import pytest
from fastapi import FastAPI, Request
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

        job = await request.app.state.job_manager.enqueue("test", fn)
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

        job = await mgr.enqueue("test", fn)
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


def test_job_manager_queues_second_job_not_rejects():
    """Starting a second job while one runs ENQUEUES it (no RuntimeError, no 409)."""

    async def _inner():
        mgr = JobManager()

        ready = asyncio.Event()

        def slow(reporter):
            ready.set()   # signal that first job is running
            time.sleep(0.2)
            return {"ok": True}

        job1 = await mgr.enqueue("slow", slow)
        # Wait until the first job is actually running before enqueueing the second.
        await asyncio.wait_for(ready.wait(), timeout=3.0)

        assert mgr.is_running
        # Second enqueue must succeed — no exception.
        job2 = await mgr.enqueue("another", slow)
        assert job2.status == "queued"

        # Both finish eventually.
        q1 = await job1.subscribe()
        for _ in range(20):
            ev = await asyncio.wait_for(q1.get(), timeout=5.0)
            if ev.get("type") == "done":
                break
        assert job1.status == "done"

        q2 = await job2.subscribe()
        for _ in range(20):
            ev = await asyncio.wait_for(q2.get(), timeout=5.0)
            if ev.get("type") == "done":
                break
        assert job2.status == "done"

        assert not mgr.is_running

    asyncio.run(_inner())


def test_job_events_replayed_after_completion():
    """Subscribe after job completes still returns all events via history replay."""

    async def _inner():
        mgr = JobManager()

        def fn(reporter):
            reporter.task("work", 1)
            reporter.advance()
            return {"ok": True}

        job = await mgr.enqueue("test", fn)

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

        job = await mgr.enqueue("test", bad_fn)
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


def test_cancel_queued_job():
    """Cancelling a queued job sets cancel_event; worker skips it."""

    async def _inner():
        mgr = JobManager()

        blocking = asyncio.Event()

        def blocker(reporter):
            # Block until we explicitly release
            while not reporter.is_cancelled():
                time.sleep(0.05)
            return {"ok": True}

        job1 = await mgr.enqueue("block", blocker)
        # Wait until job1 is running
        for _ in range(40):
            if mgr.is_running:
                break
            await asyncio.sleep(0.05)

        job2 = await mgr.enqueue("second", lambda reporter: {"ok": True})
        assert job2.status == "queued"

        # Cancel job2 while it's still queued
        assert mgr.cancel(job2.job_id)

        # Cancel job1 so the blocker stops
        assert mgr.cancel(job1.job_id)

        # job2 should end up cancelled without running
        q2 = await job2.subscribe()
        for _ in range(20):
            ev = await asyncio.wait_for(q2.get(), timeout=5.0)
            if ev.get("type") == "done":
                break

        assert job2.status == "cancelled"

    asyncio.run(_inner())


def test_queue_order_respected():
    """Jobs complete in FIFO order: first enqueued runs first."""

    async def _inner():
        order = []
        mgr = JobManager()

        def make_fn(name: str):
            def fn(reporter):
                order.append(name)
                return {"name": name}
            return fn

        j1 = await mgr.enqueue("op1", make_fn("a"))
        j2 = await mgr.enqueue("op2", make_fn("b"))
        j3 = await mgr.enqueue("op3", make_fn("c"))

        # Wait for all to finish
        for job in (j1, j2, j3):
            q = await job.subscribe()
            for _ in range(20):
                ev = await asyncio.wait_for(q.get(), timeout=5.0)
                if ev.get("type") == "done":
                    break

        assert order == ["a", "b", "c"]

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
    assert data["status"] in ("running", "done", "queued")


def test_second_op_enqueued_not_409():
    """A second POST /test/start while a slow job runs returns 200 (queued), not 409."""
    mgr = JobManager()
    with TestClient(make_test_app(mgr)) as client:
        # Start a slow job (slow=True → sleeps 0.3 s in thread)
        resp1 = client.post("/test/start?slow=true")
        assert resp1.status_code == 200

        # Second enqueue must also succeed (queued)
        resp2 = client.post("/test/start")
        assert resp2.status_code == 200
        data2 = resp2.json()
        assert "job_id" in data2


def test_list_jobs_endpoint():
    """GET /jobs returns a list of jobs."""
    mgr = JobManager()
    with TestClient(make_test_app(mgr)) as client:
        resp = client.post("/test/start")
        assert resp.status_code == 200
        time.sleep(0.1)
        list_resp = client.get("/jobs")
    assert list_resp.status_code == 200
    data = list_resp.json()
    assert "jobs" in data
    assert isinstance(data["jobs"], list)
    assert len(data["jobs"]) >= 1


def test_move_job_endpoint_nonexistent():
    """POST /jobs/{id}/move on a non-existent job returns moved=false."""
    mgr = JobManager()
    with TestClient(make_test_app(mgr)) as client:
        resp = client.post("/jobs/nonexistent/move", json={"direction": "up"})
    assert resp.status_code == 200
    assert resp.json()["moved"] is False


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


# ---------------------------------------------------------------------------
# last_run.json persistence tests
# ---------------------------------------------------------------------------

def test_last_run_persisted_on_done(tmp_path, monkeypatch):
    """A successful job writes its terminal state to _LAST_RUN_PATH."""
    import photo_flow.api.jobs as jobs_mod
    last_run_file = tmp_path / "last_run.json"
    monkeypatch.setattr(jobs_mod, "_LAST_RUN_PATH", last_run_file)

    async def _inner():
        mgr = JobManager()
        job = await mgr.enqueue("import", lambda reporter: {"photos": 3, "videos": 0})
        q = await job.subscribe()
        for _ in range(20):
            ev = await asyncio.wait_for(q.get(), timeout=3.0)
            if ev.get("type") == "done":
                break
        assert job.status == "done"

    asyncio.run(_inner())

    assert last_run_file.exists(), "last_run.json must be created after a done job"
    data = json.loads(last_run_file.read_text())
    assert "import" in data
    assert data["import"]["ok"] is True
    assert "ts" in data["import"]
    assert "counts" in data["import"]
    assert data["import"]["counts"]["photos"] == 3


def test_last_run_persisted_on_failed(tmp_path, monkeypatch):
    """A failed job records ok=false in last_run.json."""
    import photo_flow.api.jobs as jobs_mod
    last_run_file = tmp_path / "last_run.json"
    monkeypatch.setattr(jobs_mod, "_LAST_RUN_PATH", last_run_file)

    async def _inner():
        def _bad_fn(reporter):
            raise RuntimeError("boom")

        mgr = JobManager()
        job = await mgr.enqueue("finalize", _bad_fn)
        q = await job.subscribe()
        for _ in range(10):
            ev = await asyncio.wait_for(q.get(), timeout=3.0)
            if ev.get("type") == "done":
                break
        assert job.status == "failed"

    asyncio.run(_inner())

    assert last_run_file.exists()
    data = json.loads(last_run_file.read_text())
    assert "finalize" in data
    assert data["finalize"]["ok"] is False


def test_last_run_backup_op_mapped(tmp_path, monkeypatch):
    """backup:final and backup:all ops both map to the 'backup' key in last_run.json."""
    import photo_flow.api.jobs as jobs_mod
    last_run_file = tmp_path / "last_run.json"
    monkeypatch.setattr(jobs_mod, "_LAST_RUN_PATH", last_run_file)

    async def _inner():
        mgr = JobManager()
        job = await mgr.enqueue("backup:final", lambda reporter: {"sync_successful": True})
        q = await job.subscribe()
        for _ in range(20):
            ev = await asyncio.wait_for(q.get(), timeout=3.0)
            if ev.get("type") == "done":
                break
        assert job.status == "done"

    asyncio.run(_inner())

    data = json.loads(last_run_file.read_text())
    assert "backup" in data, "backup:final must map to 'backup' key"
    assert "backup:final" not in data, "Raw op name must not appear as key"


def test_last_run_write_failure_does_not_break_job(tmp_path, monkeypatch):
    """A last_run write failure never propagates to the job result."""
    import photo_flow.api.jobs as jobs_mod
    # Point to a non-writable path to trigger a write failure.
    monkeypatch.setattr(jobs_mod, "_LAST_RUN_PATH", Path("/nonexistent-dir/last_run.json"))

    async def _inner():
        mgr = JobManager()
        job = await mgr.enqueue("import", lambda reporter: {"ok": True})
        q = await job.subscribe()
        for _ in range(20):
            ev = await asyncio.wait_for(q.get(), timeout=3.0)
            if ev.get("type") == "done":
                break
        # Job must still succeed even if the write fails
        assert job.status == "done"
        assert job.result == {"ok": True}

    asyncio.run(_inner())


# ---------------------------------------------------------------------------
# Revalidation / destructive-op guard tests (FIX 1)
# ---------------------------------------------------------------------------

def test_revalidator_exception_fails_closed():
    """If the revalidator raises, the job is parked as needs_confirm (fail closed), not run."""

    async def _inner():
        mgr = JobManager()

        def _bad_revalidator():
            raise RuntimeError("exiftool not found")

        # Even though the fn would happily complete, the failing revalidator must block it.
        job = await mgr.enqueue(
            "cleanup",
            lambda reporter: {"orphaned": 2, "deleted": 2, "errors": 0},
            destructive_preview={"orphaned": 2},
            revalidator=_bad_revalidator,
        )
        q = await job.subscribe()
        for _ in range(20):
            ev = await asyncio.wait_for(q.get(), timeout=3.0)
            if ev.get("type") == "done":
                break

        assert job.status == "needs_confirm", (
            f"Expected needs_confirm when revalidator raises, got {job.status!r}"
        )

    asyncio.run(_inner())


def test_no_approved_preview_with_orphans_needs_confirm():
    """Destructive op enqueued with no approved preview (empty dict) and a non-zero fresh
    orphan count must park as needs_confirm — never run ungated."""

    async def _inner():
        mgr = JobManager()

        # Fresh dry-run reveals 3 orphans but the user approved nothing (empty dict → count 0).
        async def _revalidator():
            return {"orphaned": 3}

        deleted: list = []

        def _fn(reporter):
            deleted.append(True)
            return {"orphaned": 3, "deleted": 3, "errors": 0}

        job = await mgr.enqueue(
            "cleanup",
            _fn,
            destructive_preview={},   # no prior approval
            revalidator=_revalidator,
        )
        q = await job.subscribe()
        for _ in range(20):
            ev = await asyncio.wait_for(q.get(), timeout=3.0)
            if ev.get("type") == "done":
                break

        assert job.status == "needs_confirm", (
            f"Expected needs_confirm when no approved preview and orphans exist, got {job.status!r}"
        )
        assert not deleted, "Job fn must not run when parked in needs_confirm"

    asyncio.run(_inner())


def test_destructive_op_with_no_approved_preview_and_zero_orphans_runs():
    """Destructive op with no approved preview is allowed to run when the fresh count is 0
    (nothing to delete — safe, no data-loss risk)."""

    async def _inner():
        mgr = JobManager()

        async def _revalidator():
            return {"orphaned": 0}

        ran: list = []

        def _fn(reporter):
            ran.append(True)
            return {"orphaned": 0, "deleted": 0, "errors": 0}

        job = await mgr.enqueue(
            "cleanup",
            _fn,
            destructive_preview={},
            revalidator=_revalidator,
        )
        q = await job.subscribe()
        for _ in range(20):
            ev = await asyncio.wait_for(q.get(), timeout=3.0)
            if ev.get("type") == "done":
                break

        assert job.status == "done", (
            f"Expected done when nothing to delete, got {job.status!r}"
        )
        assert ran, "Job fn must run when there is nothing to delete"

    asyncio.run(_inner())
