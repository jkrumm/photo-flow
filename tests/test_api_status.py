"""
Tests for GET /status and GET /status/pending.

/status must return the three cheap fields without doing a camera scan.
"""
from fastapi.testclient import TestClient

from photo_flow.api.app import create_app


def test_status_shape():
    """GET /status returns camera_connected, ssd_connected, staging_files."""
    with TestClient(create_app()) as client:
        resp = client.get("/status")
    assert resp.status_code == 200
    data = resp.json()
    assert "camera_connected" in data
    assert "ssd_connected" in data
    assert "staging_files" in data


def test_status_field_types():
    """Status fields have the correct types."""
    with TestClient(create_app()) as client:
        data = client.get("/status").json()
    assert isinstance(data["camera_connected"], bool)
    assert isinstance(data["ssd_connected"], bool)
    assert isinstance(data["staging_files"], int)
    assert data["staging_files"] >= 0


def test_status_no_camera_scan(monkeypatch):
    """GET /status must NOT call scan_camera_files (that belongs in /status/pending)."""
    scanned = []

    import photo_flow.api.routes_status as rs
    original = rs._file_manager.scan_camera_files

    def spy(*args, **kwargs):
        scanned.append(True)
        return original(*args, **kwargs)

    monkeypatch.setattr(rs._file_manager, "scan_camera_files", spy)
    with TestClient(create_app()) as client:
        client.get("/status")
    assert scanned == [], "GET /status must not call scan_camera_files"


def test_status_pending_shape():
    """GET /status/pending returns the three pending count fields."""
    with TestClient(create_app()) as client:
        resp = client.get("/status/pending")
    assert resp.status_code == 200
    data = resp.json()
    assert "pending_videos" in data
    assert "pending_photos" in data
    assert "pending_raws" in data
    for key in ("pending_videos", "pending_photos", "pending_raws"):
        assert isinstance(data[key], int)
        assert data[key] >= 0


# ---------------------------------------------------------------------------
# GET /status/pipeline — full pipeline snapshot
# ---------------------------------------------------------------------------

_PIPELINE_REQUIRED_FIELDS = (
    "camera_connected",
    "ssd_connected",
    "staging_files",
    "pending_photos",
    "pending_videos",
    "pending_raws",
    "final_count",
    "unpublished_high_rated",
    "orphaned_raws",
    "last_runs",
)

_LAST_RUN_KEYS = ("import", "finalize", "sync-gallery", "backup", "cleanup")


def test_pipeline_shape():
    """GET /status/pipeline returns all required top-level fields."""
    with TestClient(create_app()) as client:
        resp = client.get("/status/pipeline")
    assert resp.status_code == 200
    data = resp.json()
    for field in _PIPELINE_REQUIRED_FIELDS:
        assert field in data, f"Missing field: {field}"


def test_pipeline_field_types():
    """GET /status/pipeline fields have the correct types."""
    with TestClient(create_app()) as client:
        data = client.get("/status/pipeline").json()
    assert isinstance(data["camera_connected"], bool)
    assert isinstance(data["ssd_connected"], bool)
    for int_field in (
        "staging_files",
        "pending_photos",
        "pending_videos",
        "pending_raws",
        "final_count",
        "unpublished_high_rated",
        "orphaned_raws",
    ):
        assert isinstance(data[int_field], int), f"{int_field} must be int"
        assert data[int_field] >= 0, f"{int_field} must be non-negative"


def test_pipeline_last_runs_shape():
    """last_runs contains exactly the expected op keys; each entry is null or {ts, ok}."""
    with TestClient(create_app()) as client:
        data = client.get("/status/pipeline").json()
    lr = data["last_runs"]
    assert isinstance(lr, dict)
    for key in _LAST_RUN_KEYS:
        assert key in lr, f"Missing last_runs key: {key}"
        entry = lr[key]
        assert entry is None or (
            isinstance(entry, dict) and "ts" in entry and "ok" in entry
        ), f"last_runs[{key!r}] has unexpected shape: {entry!r}"


def test_pipeline_no_camera_scan(monkeypatch):
    """GET /status/pipeline must not call scan_camera_files when camera absent."""
    scanned = []

    import photo_flow.api.routes_status as rs

    original = rs._file_manager.scan_camera_files

    def spy(*args, **kwargs):
        scanned.append(True)
        return original(*args, **kwargs)

    # Force the pending cache to be stale so _get_pending_counts re-evaluates.
    rs._pending_cache["ts"] = 0.0

    monkeypatch.setattr(rs._file_manager, "scan_camera_files", spy)
    # CAMERA_PATH almost certainly doesn't exist in CI, so the scan is skipped.
    with TestClient(create_app()) as client:
        client.get("/status/pipeline")

    # If camera is not present the spy must never be called.
    from photo_flow.config import CAMERA_PATH
    if not CAMERA_PATH.exists():
        assert scanned == [], "scan_camera_files must not be called when camera absent"
