"""
A backup that did not sync must report `failed`, not `done`.

Every backup path signals failure by RETURNING `sync_successful: False` rather than
raising, and the job runner marks any function that returns normally as `done`. So a
partial or wholly-failed backup was recorded `done`, announced as complete, and credited
to last_run.json as a fresh backup.

Observed live: the 2026-08-22 `backup:all` ran 5 seconds, returned
`{"all_successful": false, "errors": 2}` — RAWs and Videos never ran, because the
external SSD was unmounted — and sits in the durable job history as `done`.

These tests pin the three outcomes that must stay distinguishable: done, failed,
and cancelled.
"""
import time

import pytest

import photo_flow.api.routes_ops as routes_ops
from photo_flow.progress import NullReporter


class _CancelledReporter(NullReporter):
    def is_cancelled(self) -> bool:
        return True


def _ok(source: str) -> dict:
    return {"source": source, "scanned": 10, "sync_successful": True, "errors": 0}


def _bad(source: str) -> dict:
    return {"source": source, "scanned": 0, "sync_successful": False, "errors": 1}


@pytest.fixture
def stub(monkeypatch):
    """Replace the module-level workflow so nothing touches rclone or the network."""

    class _Workflow:
        def __init__(self):
            self.outcomes = {"final": _ok, "raws": _ok, "videos": _ok, "staging": _ok}

        def _run(self, source, dry_run, reporter):
            return self.outcomes[source](source)

        def backup_final_to_homelab(self, dry_run=False, reporter=None):
            return self._run("final", dry_run, reporter)

        def backup_raws_to_homelab(self, dry_run=False, reporter=None):
            return self._run("raws", dry_run, reporter)

        def backup_videos_to_homelab(self, dry_run=False, reporter=None):
            return self._run("videos", dry_run, reporter)

        def backup_staging_to_homelab(self, dry_run=False, reporter=None):
            return self._run("staging", dry_run, reporter)

    wf = _Workflow()
    monkeypatch.setattr(routes_ops, "_workflow", wf)
    return wf


def test_a_successful_single_source_backup_returns_normally(stub):
    result = routes_ops._run_backup_checked("final", reporter=NullReporter())
    assert result["sync_successful"] is True


def test_a_failed_single_source_backup_raises(stub):
    """`backup:raws` with the SSD unmounted returns sync_successful False — that is a failure."""
    stub.outcomes["raws"] = _bad
    with pytest.raises(routes_ops.BackupIncomplete) as exc:
        routes_ops._run_backup_checked("raws", reporter=NullReporter())
    assert "raws" in str(exc.value)
    # The partial result survives the failure, so the job still reports what happened.
    assert exc.value.result["sync_successful"] is False


def test_backup_all_raises_and_names_every_source_that_did_not_sync(stub):
    """The 2026-08-22 shape: final synced, raws and videos did not."""
    stub.outcomes["raws"] = _bad
    stub.outcomes["videos"] = _bad
    with pytest.raises(routes_ops.BackupIncomplete) as exc:
        routes_ops._run_backup_checked("all", reporter=NullReporter())

    message = str(exc.value)
    assert "raws" in message and "videos" in message
    assert "final" not in message.split(":", 1)[1]
    result = exc.value.result
    assert result["all_successful"] is False
    assert result["failed_sources"] == ["raws", "videos"]
    # What DID sync is still reported.
    assert result["sources"] == ["final", "raws", "videos"]


def test_backup_all_returns_normally_when_every_source_synced(stub):
    result = routes_ops._run_backup_checked("all", reporter=NullReporter())
    assert result["all_successful"] is True
    assert result["failed_sources"] == []


def test_a_cancelled_backup_is_not_reported_as_failed(stub):
    """
    Cancelling also yields sync_successful False. That is `cancelled`, which the job
    runner decides from the cancel event — raising here would relabel it `failed`.
    """
    stub.outcomes["final"] = _bad
    result = routes_ops._run_backup_checked("final", reporter=_CancelledReporter())
    assert result["sync_successful"] is False


def test_the_dry_run_preview_never_raises(stub):
    """The preview returns its dict to the caller synchronously; a raise would 500 it."""
    stub.outcomes["raws"] = _bad
    result = routes_ops._run_backup("all", dry_run=True, reporter=NullReporter())
    assert result["all_successful"] is False
    assert result["failed_sources"] == ["raws"]


# ---------------------------------------------------------------------------
# End to end through the job runner — the helper raising is only half the fix;
# what was broken is the STATUS the job ends up with.
# ---------------------------------------------------------------------------

def test_a_failed_backup_job_ends_up_failed_not_done(stub, monkeypatch):
    from fastapi.testclient import TestClient
    from photo_flow.api.app import create_app

    stub.outcomes["raws"] = _bad
    stub.outcomes["videos"] = _bad

    with TestClient(create_app()) as client:
        job_id = client.post("/ops/backup?source=all").json()["job_id"]
        for _ in range(200):
            body = client.get(f"/jobs/{job_id}").json()
            if body["status"] in ("done", "failed", "cancelled"):
                break
            time.sleep(0.05)

    assert body["status"] == "failed", "a backup that skipped two sources reported success"
    assert "raws" in body["error"] and "videos" in body["error"]
    # The partial result rides along, so the history still says what synced.
    assert body["result"]["failed_sources"] == ["raws", "videos"]


def test_a_fully_successful_backup_job_is_done(stub):
    from fastapi.testclient import TestClient
    from photo_flow.api.app import create_app

    with TestClient(create_app()) as client:
        job_id = client.post("/ops/backup?source=all").json()["job_id"]
        for _ in range(200):
            body = client.get(f"/jobs/{job_id}").json()
            if body["status"] in ("done", "failed", "cancelled"):
                break
            time.sleep(0.05)

    assert body["status"] == "done"
    assert body["result"]["all_successful"] is True
