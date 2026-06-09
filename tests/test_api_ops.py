"""
Tests for the operation endpoints: POST /ops/* and GET /backup/availability.

Strategy:
- All tests monkeypatch _workflow (the module-level PhotoWorkflow instance in routes_ops)
  so no real filesystem, camera, or rclone operations occur.
- Test 1: dry_run=true returns the result dict with the expected keys.
- Test 2: dry_run=false returns job_id (202); a second concurrent op returns 409.
- Test 3: backup source param routes to the correct workflow method only.
"""
import time
from pathlib import Path
from unittest.mock import MagicMock, call

import pytest
from fastapi.testclient import TestClient

import photo_flow.api.routes_ops as routes_ops_module
from photo_flow.api.app import create_app


# ---------------------------------------------------------------------------
# Shared fixtures
# ---------------------------------------------------------------------------

_FINALIZE_RESULT = {
    "moved": 3,
    "edits_moved": 1,
    "orphaned_raws": 2,
    "deleted_raws": 2,
    "deleted_camera_raws": 0,
    "skipped": 0,
    "errors": 0,
}

_IMPORT_RESULT = {
    "videos": 2,
    "photos": 5,
    "raws": 5,
    "skipped": 1,
    "errors": 0,
}

_CLEANUP_RESULT = {
    "orphaned": 4,
    "deleted": 4,
    "errors": 0,
}

_SYNC_GALLERY_RESULT = {
    "scanned": 100,
    "synced": 10,
    "removed": 2,
    "skipped": 0,
    "unchanged": 88,
    "errors": 0,
    "json_updated": True,
    "total_in_gallery": 96,
    "build_successful": None,
    "sync_successful": None,
}

_BACKUP_FINAL_RESULT = {
    "source": "final",
    "scanned": 500,
    "sync_successful": True,
    "connection_method": "tailscale",
    "trash_path": "/some/trash/path",
    "errors": 0,
}

_BACKUP_RAWS_RESULT = {
    "source": "raws",
    "scanned": 200,
    "sync_successful": True,
    "connection_method": "tailscale",
    "trash_path": "/some/trash/path",
    "errors": 0,
}

_BACKUP_VIDEOS_RESULT = {
    "source": "videos",
    "scanned": 50,
    "sync_successful": True,
    "connection_method": "tailscale",
    "trash_path": "/some/trash/path",
    "errors": 0,
}


def _make_mock_workflow(**overrides) -> MagicMock:
    """Build a MagicMock wired with the standard return values."""
    mock = MagicMock()
    mock.import_from_camera.return_value = _IMPORT_RESULT
    mock.finalize_staging.return_value = _FINALIZE_RESULT
    mock.cleanup_unused_raws.return_value = _CLEANUP_RESULT
    mock.sync_gallery.return_value = _SYNC_GALLERY_RESULT
    mock.backup_final_to_homelab.return_value = _BACKUP_FINAL_RESULT
    mock.backup_raws_to_homelab.return_value = _BACKUP_RAWS_RESULT
    mock.backup_videos_to_homelab.return_value = _BACKUP_VIDEOS_RESULT
    mock.get_backup_availability.return_value = {
        "final": {"available": True, "local_count": 500, "path": Path("/tmp/final"),
                  "remote_path": Path("/remote/final"), "extension": "*.JPG"},
        "raws": {"available": False, "local_count": 0, "path": Path("/tmp/raws"),
                 "remote_path": Path("/remote/raws"), "extension": "*.RAF",
                 "requires": "External SSD"},
        "videos": {"available": False, "local_count": 0, "path": Path("/tmp/videos"),
                   "remote_path": Path("/remote/videos"), "extension": "*.MOV",
                   "requires": "External SSD"},
    }
    for k, v in overrides.items():
        setattr(mock, k, v)
    return mock


# ---------------------------------------------------------------------------
# Test 1: dry_run=true returns expected keys (no real filesystem access)
# ---------------------------------------------------------------------------

class TestDryRunReturnsExpectedKeys:
    """POST /ops/* ?dry_run=true returns the result dict with the correct keys."""

    def _client_with_mock(self, monkeypatch) -> TestClient:
        mock = _make_mock_workflow()
        monkeypatch.setattr(routes_ops_module, "_workflow", mock)
        return TestClient(create_app())

    def test_finalize_dry_run_keys(self, monkeypatch):
        with self._client_with_mock(monkeypatch) as client:
            resp = client.post("/ops/finalize?dry_run=true")
        assert resp.status_code == 200
        data = resp.json()
        for key in ("moved", "edits_moved", "orphaned_raws", "deleted_raws",
                    "deleted_camera_raws", "skipped", "errors"):
            assert key in data, f"Missing key: {key}"
        assert data["moved"] == 3
        assert data["errors"] == 0

    def test_import_dry_run_keys(self, monkeypatch):
        with self._client_with_mock(monkeypatch) as client:
            resp = client.post("/ops/import?dry_run=true")
        assert resp.status_code == 200
        data = resp.json()
        for key in ("videos", "photos", "raws", "skipped", "errors"):
            assert key in data

    def test_cleanup_dry_run_keys(self, monkeypatch):
        with self._client_with_mock(monkeypatch) as client:
            resp = client.post("/ops/cleanup?dry_run=true")
        assert resp.status_code == 200
        data = resp.json()
        for key in ("orphaned", "deleted", "errors"):
            assert key in data
        assert data["orphaned"] == 4

    def test_sync_gallery_dry_run_keys(self, monkeypatch):
        with self._client_with_mock(monkeypatch) as client:
            resp = client.post("/ops/sync-gallery?dry_run=true")
        assert resp.status_code == 200
        data = resp.json()
        for key in ("scanned", "synced", "removed", "unchanged", "errors",
                    "json_updated", "total_in_gallery"):
            assert key in data

    def test_backup_dry_run_keys(self, monkeypatch):
        with self._client_with_mock(monkeypatch) as client:
            resp = client.post("/ops/backup?source=final&dry_run=true")
        assert resp.status_code == 200
        data = resp.json()
        for key in ("source", "scanned", "sync_successful", "errors"):
            assert key in data
        assert data["source"] == "final"

    def test_backup_all_dry_run_keys(self, monkeypatch):
        with self._client_with_mock(monkeypatch) as client:
            resp = client.post("/ops/backup?source=all&dry_run=true")
        assert resp.status_code == 200
        data = resp.json()
        for key in ("sources", "total_scanned", "all_successful", "errors"):
            assert key in data
        assert data["total_scanned"] == 750  # 500 + 200 + 50


# ---------------------------------------------------------------------------
# Test 2: real run returns job_id (202); second concurrent run returns 409
# ---------------------------------------------------------------------------

class TestRealRunJobFlow:
    """POST /ops/* without dry_run starts a job and returns 202; concurrent → 409."""

    def test_finalize_returns_job_id(self, monkeypatch):
        mock = _make_mock_workflow()
        monkeypatch.setattr(routes_ops_module, "_workflow", mock)
        with TestClient(create_app()) as client:
            resp = client.post("/ops/finalize")
        assert resp.status_code == 202
        data = resp.json()
        assert "job_id" in data
        assert data["job_id"]  # non-empty string

    def test_import_returns_job_id(self, monkeypatch):
        mock = _make_mock_workflow()
        monkeypatch.setattr(routes_ops_module, "_workflow", mock)
        with TestClient(create_app()) as client:
            resp = client.post("/ops/import")
        assert resp.status_code == 202
        assert "job_id" in resp.json()

    def test_concurrent_real_op_returns_409(self, monkeypatch):
        """Starting a second mutating op while one is running returns 409."""
        mock = _make_mock_workflow()

        # Make finalize_staging slow so the background task is still running
        # when the second request arrives.
        def slow_finalize(dry_run=False, reporter=None, progress_callback=None):
            time.sleep(0.5)
            return _FINALIZE_RESULT

        mock.finalize_staging = slow_finalize
        monkeypatch.setattr(routes_ops_module, "_workflow", mock)

        with TestClient(create_app()) as client:
            resp1 = client.post("/ops/finalize")
            assert resp1.status_code == 202, "First request should succeed"

            # _running is True immediately after start() — before any await.
            resp2 = client.post("/ops/finalize")
            assert resp2.status_code == 409, "Second concurrent request should be rejected"

    def test_different_ops_also_conflict(self, monkeypatch):
        """Single-flight lock applies across all op types."""
        mock = _make_mock_workflow()

        def slow_import(dry_run=False, reporter=None, progress_callback=None):
            time.sleep(0.5)
            return _IMPORT_RESULT

        mock.import_from_camera = slow_import
        monkeypatch.setattr(routes_ops_module, "_workflow", mock)

        with TestClient(create_app()) as client:
            resp1 = client.post("/ops/import")
            assert resp1.status_code == 202

            # Finalize should also be rejected while import is running.
            resp2 = client.post("/ops/finalize")
            assert resp2.status_code == 409


# ---------------------------------------------------------------------------
# Test 3: backup source param routes to the correct workflow method
# ---------------------------------------------------------------------------

class TestBackupSourceRouting:
    """POST /ops/backup?source=X calls only the appropriate backup method."""

    def _client(self, monkeypatch) -> tuple[TestClient, MagicMock]:
        mock = _make_mock_workflow()
        monkeypatch.setattr(routes_ops_module, "_workflow", mock)
        return TestClient(create_app()), mock

    def test_source_final_calls_final_only(self, monkeypatch):
        client, mock = self._client(monkeypatch)
        with client:
            resp = client.post("/ops/backup?source=final&dry_run=true")
        assert resp.status_code == 200
        mock.backup_final_to_homelab.assert_called_once()
        mock.backup_raws_to_homelab.assert_not_called()
        mock.backup_videos_to_homelab.assert_not_called()

    def test_source_raws_calls_raws_only(self, monkeypatch):
        client, mock = self._client(monkeypatch)
        with client:
            resp = client.post("/ops/backup?source=raws&dry_run=true")
        assert resp.status_code == 200
        mock.backup_raws_to_homelab.assert_called_once()
        mock.backup_final_to_homelab.assert_not_called()
        mock.backup_videos_to_homelab.assert_not_called()

    def test_source_videos_calls_videos_only(self, monkeypatch):
        client, mock = self._client(monkeypatch)
        with client:
            resp = client.post("/ops/backup?source=videos&dry_run=true")
        assert resp.status_code == 200
        mock.backup_videos_to_homelab.assert_called_once()
        mock.backup_final_to_homelab.assert_not_called()
        mock.backup_raws_to_homelab.assert_not_called()

    def test_source_all_calls_all_three_in_order(self, monkeypatch):
        client, mock = self._client(monkeypatch)
        with client:
            resp = client.post("/ops/backup?source=all&dry_run=true")
        assert resp.status_code == 200
        mock.backup_final_to_homelab.assert_called_once()
        mock.backup_raws_to_homelab.assert_called_once()
        mock.backup_videos_to_homelab.assert_called_once()
        # Verify call order via call_args_list on the parent mock
        method_calls = [c[0] for c in mock.method_calls]
        final_idx = next(i for i, n in enumerate(method_calls) if n == "backup_final_to_homelab")
        raws_idx = next(i for i, n in enumerate(method_calls) if n == "backup_raws_to_homelab")
        videos_idx = next(i for i, n in enumerate(method_calls) if n == "backup_videos_to_homelab")
        assert final_idx < raws_idx < videos_idx, "Backup order must be final → raws → videos"

    def test_default_source_is_all(self, monkeypatch):
        """Omitting source param defaults to 'all'."""
        client, mock = self._client(monkeypatch)
        with client:
            resp = client.post("/ops/backup?dry_run=true")
        assert resp.status_code == 200
        mock.backup_final_to_homelab.assert_called_once()
        mock.backup_raws_to_homelab.assert_called_once()
        mock.backup_videos_to_homelab.assert_called_once()

    def test_invalid_source_rejected(self, monkeypatch):
        client, mock = self._client(monkeypatch)
        with client:
            resp = client.post("/ops/backup?source=invalid&dry_run=true")
        assert resp.status_code == 422  # FastAPI validation error


# ---------------------------------------------------------------------------
# Test 4: backup availability endpoint
# ---------------------------------------------------------------------------

class TestBackupAvailability:
    def test_returns_availability_shape(self, monkeypatch):
        mock = _make_mock_workflow()
        monkeypatch.setattr(routes_ops_module, "_workflow", mock)
        with TestClient(create_app()) as client:
            resp = client.get("/backup/availability")
        assert resp.status_code == 200
        data = resp.json()
        assert "final" in data
        assert "raws" in data
        assert "videos" in data
        assert data["final"]["available"] is True
        assert data["final"]["local_count"] == 500

    def test_paths_serialized_as_strings(self, monkeypatch):
        """Path objects in availability dict must be serialized to strings."""
        mock = _make_mock_workflow()
        monkeypatch.setattr(routes_ops_module, "_workflow", mock)
        with TestClient(create_app()) as client:
            resp = client.get("/backup/availability")
        data = resp.json()
        # Path objects must become strings, not dicts or None
        assert isinstance(data["final"].get("path"), str)
