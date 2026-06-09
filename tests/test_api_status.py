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
