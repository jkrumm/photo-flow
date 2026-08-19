"""
Tests for the durable job record (`api/job_store.py`) and the startup sweep.

The gap being closed: `JobManager._jobs` is in-memory, so a crash / logout /
`make reload` mid-backup vaporised the job with no trace — it did not even fail. These
tests pin the three things that fix that: every transition is mirrored to SQLite, a job
left `running` by a dead process comes back as `interrupted` (and corrects
`last_run.json`), and a terminal record notifies exactly once even if nobody was
watching when it finished.

The `isolated_job_store` autouse fixture in conftest.py points the store at a per-test
database, so none of this touches the live `~/.photoflow/index.db`.
"""
import asyncio
import json
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.testclient import TestClient

from photo_flow.api import job_store
from photo_flow.api.jobs import JobManager, sweep_interrupted_jobs
from photo_flow.api.routes_jobs import router as jobs_router


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _drain(job, timeout: float = 3.0):
    """Await a job's terminal 'done' event via a fresh subscription."""

    async def _wait():
        q = await job.subscribe()
        for _ in range(30):
            ev = await asyncio.wait_for(q.get(), timeout=timeout)
            if ev.get("type") == "done":
                return ev
        raise AssertionError("no terminal event")

    return _wait()


def _run_job(op: str, fn, **kwargs):
    """Enqueue one job on a fresh manager and run it to completion."""

    async def _inner():
        mgr = JobManager()
        job = await mgr.enqueue(op, fn, **kwargs)
        await _drain(job)
        return job

    return asyncio.run(_inner())


def _seed(status: str, op: str = "backup:final", job_id: str = "seeded") -> None:
    """Write a row directly, simulating what a dead process left behind."""
    job_store.record_queued(job_id, op, seq=1)
    if status == "running":
        job_store.record_started(job_id)


def _history_app() -> FastAPI:
    """Minimal app carrying the real jobs router (no manager state needed)."""
    app = FastAPI()
    app.include_router(jobs_router)
    return app


# ---------------------------------------------------------------------------
# Lifecycle mirroring
# ---------------------------------------------------------------------------


def test_successful_job_is_recorded():
    """A completed job leaves a durable row with timestamps and its result."""
    job = _run_job("import", lambda reporter: {"photos": 7})
    assert job.status == "done"

    rows = job_store.list_history()
    assert len(rows) == 1
    row = rows[0]
    assert row["job_id"] == job.job_id
    assert row["op"] == "import"
    assert row["status"] == "done"
    assert row["result"] == {"photos": 7}
    assert row["error"] is None
    assert row["queued_at"] is not None
    assert row["started_at"] is not None
    assert row["finished_at"] is not None
    assert row["announced"] is False


def test_failed_job_records_error():
    """A raising fn is recorded as failed with its message, not lost."""

    def _bad(reporter):
        raise RuntimeError("rclone exploded")

    job = _run_job("backup:final", _bad)
    assert job.status == "failed"

    row = job_store.list_history()[0]
    assert row["status"] == "failed"
    assert row["error"] == "rclone exploded"
    assert row["op"] == "backup:final", "the full op (with source) must survive"


def test_needs_confirm_is_recorded():
    """A destructive job parked for re-confirmation is visible in the record."""

    async def _revalidator():
        return {"orphaned": 9}

    job = _run_job(
        "cleanup",
        lambda reporter: {"deleted": 9},
        destructive_preview={"orphaned": 2},
        revalidator=_revalidator,
    )
    assert job.status == "needs_confirm"
    assert job_store.list_history()[0]["status"] == "needs_confirm"


def test_history_is_ordered_newest_first():
    """Ordering is insertion order, so the most recent job leads the list."""

    async def _inner():
        mgr = JobManager()
        for name in ("import", "finalize", "cleanup"):
            job = await mgr.enqueue(name, lambda reporter: {"ok": True})
            await _drain(job)

    asyncio.run(_inner())
    assert [r["op"] for r in job_store.list_history()] == ["cleanup", "finalize", "import"]


def test_store_failure_does_not_break_the_job(monkeypatch):
    """An unwritable job store must never fail or stall the actual photo operation."""
    job_store.set_db_path(Path("/nonexistent-dir/definitely-not-here.db"))
    try:
        job = _run_job("finalize", lambda reporter: {"moved": 4})
    finally:
        job_store.set_db_path(None)

    assert job.status == "done"
    assert job.result == {"moved": 4}


def test_history_is_pruned_to_the_cap(monkeypatch):
    """The table is bounded — old records are dropped, newest kept."""
    monkeypatch.setattr(job_store, "_MAX_HISTORY_ROWS", 3)

    async def _inner():
        mgr = JobManager()
        for i in range(6):
            job = await mgr.enqueue(f"op{i}", lambda reporter: {"ok": True})
            await _drain(job)

    asyncio.run(_inner())
    rows = job_store.list_history(limit=100)
    assert len(rows) == 3
    assert [r["op"] for r in rows] == ["op5", "op4", "op3"]


# ---------------------------------------------------------------------------
# Startup reconciliation
# ---------------------------------------------------------------------------


def test_running_job_becomes_interrupted():
    """A job the dead process was RUNNING comes back as interrupted, not missing."""
    _seed("running", op="backup:raws")

    records = job_store.reconcile_interrupted()
    assert len(records) == 1
    assert records[0]["status"] == "interrupted"
    assert records[0]["was_running"] is True
    assert "while this job was running" in records[0]["error"]

    row = job_store.list_history()[0]
    assert row["status"] == "interrupted"
    assert row["finished_at"] is not None


def test_queued_job_becomes_interrupted_but_reads_differently():
    """A job that never started is also reconciled — with distinguishable wording."""
    _seed("queued", op="cleanup")

    records = job_store.reconcile_interrupted()
    assert len(records) == 1
    assert records[0]["was_running"] is False
    assert "before this job started" in records[0]["error"]


def test_reconcile_leaves_terminal_records_alone():
    """Completed jobs are not re-flagged by a later restart."""
    _run_job("import", lambda reporter: {"photos": 1})
    assert job_store.reconcile_interrupted() == []
    assert job_store.list_history()[0]["status"] == "done"


def test_reconcile_is_idempotent():
    """A second startup finds nothing left to reconcile."""
    _seed("running")
    assert len(job_store.reconcile_interrupted()) == 1
    assert job_store.reconcile_interrupted() == []


def test_sweep_corrects_last_run_for_a_running_job(tmp_path, monkeypatch):
    """last_run.json must stop implying the last backup succeeded.

    Without this the advisor reads a backup that died 80 % through as a clean run
    from whenever the PREVIOUS one finished.
    """
    import photo_flow.api.jobs as jobs_mod

    last_run = tmp_path / "last_run.json"
    last_run.write_text(json.dumps({"backup": {"ts": "2026-08-01T10:00:00+00:00", "ok": True}}))
    monkeypatch.setattr(jobs_mod, "_LAST_RUN_PATH", last_run)

    _seed("running", op="backup:final")
    sweep_interrupted_jobs()

    entry = json.loads(last_run.read_text())["backup"]
    assert entry["ok"] is False
    assert entry["interrupted"] is True
    assert entry["ts"] != "2026-08-01T10:00:00+00:00"


def test_sweep_leaves_last_run_alone_for_a_queued_job(tmp_path, monkeypatch):
    """A job that never started changed nothing about the last RUN — don't rewrite it."""
    import photo_flow.api.jobs as jobs_mod

    last_run = tmp_path / "last_run.json"
    original = {"cleanup": {"ts": "2026-08-01T10:00:00+00:00", "ok": True}}
    last_run.write_text(json.dumps(original))
    monkeypatch.setattr(jobs_mod, "_LAST_RUN_PATH", last_run)

    _seed("queued", op="cleanup")
    sweep_interrupted_jobs()

    assert json.loads(last_run.read_text()) == original


def test_lifespan_runs_the_sweep():
    """The real app reconciles on startup, before it can accept anything new."""
    from photo_flow.api.app import create_app

    _seed("running", op="finalize")
    app = create_app()
    with TestClient(app) as client:
        assert client.get("/health").status_code == 200
        assert len(app.state.interrupted_jobs) == 1

    assert job_store.list_history()[0]["status"] == "interrupted"


# ---------------------------------------------------------------------------
# Notification coverage
# ---------------------------------------------------------------------------


def test_unannounced_lists_only_terminal_unseen_jobs():
    """The catch-up query skips in-flight jobs and anything already surfaced."""
    _run_job("import", lambda reporter: {"photos": 2})
    _seed("running", job_id="still-going")

    unseen = job_store.list_history(unannounced_only=True)
    assert [r["op"] for r in unseen] == ["import"]

    assert job_store.mark_announced([unseen[0]["job_id"]]) == 1
    assert job_store.list_history(unannounced_only=True) == []


def test_mark_announced_ignores_empty_and_unknown_ids():
    assert job_store.mark_announced([]) == 0
    assert job_store.mark_announced(["no-such-job"]) == 0


def test_interrupted_jobs_are_unannounced():
    """An interrupted job is exactly what the user needs told about on next open."""
    _seed("running", op="backup:videos")
    job_store.reconcile_interrupted()

    unseen = job_store.list_history(unannounced_only=True)
    assert len(unseen) == 1
    assert unseen[0]["status"] == "interrupted"


# ---------------------------------------------------------------------------
# HTTP layer
# ---------------------------------------------------------------------------


def test_history_endpoint():
    _run_job("finalize", lambda reporter: {"moved": 3})
    with TestClient(_history_app()) as client:
        resp = client.get("/jobs/history")
    assert resp.status_code == 200
    jobs = resp.json()["jobs"]
    assert len(jobs) == 1
    assert jobs[0]["op"] == "finalize"
    assert jobs[0]["result"] == {"moved": 3}


def test_history_endpoint_unannounced_filter_and_ack():
    _run_job("import", lambda reporter: {"photos": 5})
    with TestClient(_history_app()) as client:
        unseen = client.get("/jobs/history", params={"unannounced": True}).json()["jobs"]
        assert len(unseen) == 1

        ack = client.post("/jobs/history/ack", json={"job_ids": [unseen[0]["job_id"]]})
        assert ack.status_code == 200
        assert ack.json()["acknowledged"] == 1

        assert client.get("/jobs/history", params={"unannounced": True}).json()["jobs"] == []
        # The record itself survives the ack — only the flag changed.
        assert len(client.get("/jobs/history").json()["jobs"]) == 1


def test_history_endpoint_clamps_limit():
    """`limit` is clamped rather than trusted — this route is reachable from the SPA."""
    with TestClient(_history_app()) as client:
        assert client.get("/jobs/history", params={"limit": 100000}).status_code == 200
        assert client.get("/jobs/history", params={"limit": 0}).status_code == 200


def test_history_route_does_not_shadow_job_lookup():
    """`/jobs/history` must not be swallowed by the `/jobs/{job_id}` catch-all."""
    mgr = JobManager()

    from contextlib import asynccontextmanager

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        app.state.job_manager = mgr
        yield

    app = FastAPI(lifespan=lifespan)
    app.include_router(jobs_router)

    @app.post("/test/start")
    async def start(request: Request) -> dict:
        job = await request.app.state.job_manager.enqueue("test", lambda reporter: {"ok": True})
        return {"job_id": job.job_id}

    with TestClient(app) as client:
        job_id = client.post("/test/start").json()["job_id"]
        assert client.get("/jobs/history").json()["jobs"] is not None
        assert client.get(f"/jobs/{job_id}").json()["job_id"] == job_id
