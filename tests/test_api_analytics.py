"""
Tests for analytics endpoints and /index/refresh.

Strategy:
- Seed a temp SQLite index with known rows via get_db().
- Monkeypatch `_open_conn` in `routes_analytics` to use the temp DB.
- Monkeypatch config path constants in `routes_analytics` for storage tests.
- Monkeypatch `_run_reindex` in `routes_analytics` to test the refresh endpoint
  without touching the real Final folder.
- Use FastAPI TestClient; never touch the real ~/.photoflow/index.db.
"""
from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Any, Dict, List
from unittest.mock import MagicMock

import pytest
from fastapi.testclient import TestClient

import photo_flow.api.routes_analytics as analytics_mod
from photo_flow.api.app import create_app
from photo_flow.index.db import get_db


# ---------------------------------------------------------------------------
# Seed data
# ---------------------------------------------------------------------------

# 5 in_final photos + 1 excluded (in_final=0)
# Jan 2026: a (rating=5, GPS), b (rating=4) → both high_rated
# Feb 2026: c (rating=3), d (rating=2, GPS) → both other
# Mar 2026: e (rating=0) → other
# Excluded: f (in_final=0, should never appear in analytics)
_SEED_ROWS: List[Dict[str, Any]] = [
    {
        "path": "/f/a.JPG",
        "filename": "a.JPG",
        "size": 1_000_000,
        "mtime": 1000.0,
        "date_taken": "2026-01-10T09:00:00Z",
        "rating": 5,
        "iso": 800,
        "aperture_f": 2.8,
        "shutter_s": 1 / 250,
        "focal_mm": 35.0,
        "latitude": 48.13,
        "longitude": 11.58,
        "in_final": 1,
        "published": 1,
    },
    {
        "path": "/f/b.JPG",
        "filename": "b.JPG",
        "size": 2_000_000,
        "mtime": 1001.0,
        "date_taken": "2026-01-15T10:00:00Z",
        "rating": 4,
        "iso": 400,
        "aperture_f": 2.8,
        "shutter_s": 1 / 500,
        "focal_mm": 35.0,
        "latitude": None,
        "longitude": None,
        "in_final": 1,
        "published": 1,
    },
    {
        "path": "/f/c.JPG",
        "filename": "c.JPG",
        "size": 1_500_000,
        "mtime": 1002.0,
        "date_taken": "2026-02-05T11:00:00Z",
        "rating": 3,
        "iso": 200,
        "aperture_f": 5.6,
        "shutter_s": 1 / 125,
        "focal_mm": 50.0,
        "latitude": None,
        "longitude": None,
        "in_final": 1,
        "published": 0,
    },
    {
        "path": "/f/d.JPG",
        "filename": "d.JPG",
        "size": 3_000_000,
        "mtime": 1003.0,
        "date_taken": "2026-02-20T12:00:00Z",
        "rating": 2,
        "iso": 1600,
        "aperture_f": 4.0,
        "shutter_s": 1 / 60,
        "focal_mm": 50.0,
        "latitude": 48.14,
        "longitude": 11.59,
        "in_final": 1,
        "published": 0,
    },
    {
        "path": "/f/e.JPG",
        "filename": "e.JPG",
        "size": 500_000,
        "mtime": 1004.0,
        "date_taken": "2026-03-01T08:00:00Z",
        "rating": 0,
        "iso": 3200,
        "aperture_f": 8.0,
        "shutter_s": 1 / 30,
        "focal_mm": 100.0,
        "latitude": None,
        "longitude": None,
        "in_final": 1,
        "published": 0,
    },
    # Excluded: in_final=0
    {
        "path": "/f/f.JPG",
        "filename": "f.JPG",
        "size": 100_000,
        "mtime": 1005.0,
        "date_taken": "2026-01-01T00:00:00Z",
        "rating": 5,
        "iso": 100,
        "aperture_f": 1.4,
        "shutter_s": 1 / 4000,
        "focal_mm": 35.0,
        "latitude": 50.0,
        "longitude": 8.0,
        "in_final": 0,
        "published": 0,
    },
]

_INSERT_SQL = """
    INSERT INTO photos
        (path, filename, size, mtime, date_taken, rating, iso,
         aperture_f, shutter_s, focal_mm, latitude, longitude,
         in_final, published, indexed_at)
    VALUES
        (:path, :filename, :size, :mtime, :date_taken, :rating, :iso,
         :aperture_f, :shutter_s, :focal_mm, :latitude, :longitude,
         :in_final, :published, '2026-01-01T00:00:00+00:00')
"""


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def seeded_db_path(tmp_path) -> Path:
    """Create a temp DB seeded with test rows. Returns the DB path."""
    db_path = tmp_path / "analytics_test.db"
    conn = get_db(db_path)
    for row in _SEED_ROWS:
        conn.execute(_INSERT_SQL, row)
    conn.commit()
    conn.close()
    return db_path


@pytest.fixture()
def client(seeded_db_path, monkeypatch) -> TestClient:
    """TestClient with _open_conn patched to use the seeded temp DB."""
    monkeypatch.setattr(
        analytics_mod,
        "_open_conn",
        lambda: get_db(seeded_db_path),
    )
    return TestClient(create_app())


# ---------------------------------------------------------------------------
# /analytics/over-time
# ---------------------------------------------------------------------------


class TestOverTime:
    def test_month_bucket_returns_three_buckets(self, client):
        resp = client.get("/analytics/over-time?bucket=month")
        assert resp.status_code == 200
        data = resp.json()
        xs = [d["x"] for d in data]
        assert "2026-01" in xs
        assert "2026-02" in xs
        assert "2026-03" in xs
        assert len(data) == 3

    def test_month_totals(self, client):
        resp = client.get("/analytics/over-time?bucket=month")
        by_month = {d["x"]: d for d in resp.json()}
        # Jan: a(5) + b(4) = 2 total, 2 high_rated, 0 other
        assert by_month["2026-01"]["total"] == 2
        assert by_month["2026-01"]["high_rated"] == 2
        assert by_month["2026-01"]["other"] == 0
        # Feb: c(3) + d(2) = 2 total, 0 high_rated, 2 other
        assert by_month["2026-02"]["total"] == 2
        assert by_month["2026-02"]["high_rated"] == 0
        assert by_month["2026-02"]["other"] == 2
        # Mar: e(0) = 1 total, 0 high_rated, 1 other
        assert by_month["2026-03"]["total"] == 1
        assert by_month["2026-03"]["high_rated"] == 0
        assert by_month["2026-03"]["other"] == 1

    def test_year_bucket(self, client):
        resp = client.get("/analytics/over-time?bucket=year")
        assert resp.status_code == 200
        data = resp.json()
        assert len(data) == 1
        assert data[0]["x"] == "2026"
        assert data[0]["total"] == 5  # all 5 in_final photos

    def test_day_bucket(self, client):
        resp = client.get("/analytics/over-time?bucket=day")
        assert resp.status_code == 200
        data = resp.json()
        # 5 unique dates
        assert len(data) == 5
        assert all(d["total"] == 1 for d in data)

    def test_excluded_photo_not_counted(self, client):
        resp = client.get("/analytics/over-time?bucket=year")
        data = resp.json()
        # Only 5 in_final rows; the in_final=0 row is excluded
        assert data[0]["total"] == 5

    def test_invalid_bucket_returns_422(self, client):
        resp = client.get("/analytics/over-time?bucket=hour")
        assert resp.status_code == 422


# ---------------------------------------------------------------------------
# /analytics/ratings
# ---------------------------------------------------------------------------


class TestRatings:
    def test_histogram_contains_all_in_final_ratings(self, client):
        resp = client.get("/analytics/ratings")
        assert resp.status_code == 200
        data = resp.json()
        histogram = {r["rating"]: r["count"] for r in data["histogram"]}
        assert histogram[0] == 1
        assert histogram[2] == 1
        assert histogram[3] == 1
        assert histogram[4] == 1
        assert histogram[5] == 1

    def test_totals(self, client):
        resp = client.get("/analytics/ratings")
        data = resp.json()
        assert data["total_final"] == 5
        assert data["total_published"] == 2  # a + b

    def test_excluded_photo_not_in_histogram(self, client):
        resp = client.get("/analytics/ratings")
        data = resp.json()
        total = sum(r["count"] for r in data["histogram"])
        assert total == 5  # 5 in_final rows, not 6


# ---------------------------------------------------------------------------
# /analytics/settings
# ---------------------------------------------------------------------------


class TestSettings:
    def test_iso_distribution(self, client):
        resp = client.get("/analytics/settings")
        assert resp.status_code == 200
        data = resp.json()
        iso_values = {int(r["value"]): r["count"] for r in data["iso"]}
        assert iso_values[200] == 1
        assert iso_values[400] == 1
        assert iso_values[800] == 1
        assert iso_values[1600] == 1
        assert iso_values[3200] == 1
        # Excluded row (iso=100, in_final=0) must not appear
        assert 100 not in iso_values

    def test_aperture_distribution(self, client):
        resp = client.get("/analytics/settings")
        data = resp.json()
        ap_values = {round(r["value"], 1): r["count"] for r in data["aperture"]}
        assert ap_values[2.8] == 2  # a + b both f/2.8
        assert ap_values[4.0] == 1
        assert ap_values[5.6] == 1
        assert ap_values[8.0] == 1

    def test_focal_distribution(self, client):
        resp = client.get("/analytics/settings")
        data = resp.json()
        focal_values = {round(r["value"], 0): r["count"] for r in data["focal"]}
        assert focal_values[35.0] == 2  # a + b
        assert focal_values[50.0] == 2  # c + d
        assert focal_values[100.0] == 1  # e

    def test_shutter_distribution(self, client):
        resp = client.get("/analytics/settings")
        data = resp.json()
        shutter_rows = data["shutter"]
        # Each shutter value is unique in seed data
        assert len(shutter_rows) == 5
        # Labels are present
        assert all(r["label"] != "" for r in shutter_rows)

    def test_shutter_labels_human_readable(self, client):
        resp = client.get("/analytics/settings")
        data = resp.json()
        labels = {r["label"] for r in data["shutter"]}
        # 1/250 → '1/250', 1/500 → '1/500', etc.
        assert "1/250" in labels
        assert "1/500" in labels
        assert "1/125" in labels
        assert "1/60" in labels
        assert "1/30" in labels


# ---------------------------------------------------------------------------
# /analytics/storage
# ---------------------------------------------------------------------------


class TestStorage:
    def test_final_counts_from_index(self, client, monkeypatch):
        # Make all "unavailable" paths absent so only final is relevant
        nonexistent = Path("/nonexistent_test_path_xyz")
        monkeypatch.setattr(analytics_mod, "STAGING_PATH", nonexistent)
        monkeypatch.setattr(analytics_mod, "RAWS_PATH", nonexistent)
        monkeypatch.setattr(analytics_mod, "SSD_PATH", nonexistent)
        monkeypatch.setattr(analytics_mod, "FINAL_PATH", Path("/"))  # root always exists

        resp = client.get("/analytics/storage")
        assert resp.status_code == 200
        data = resp.json()
        assert data["final"]["count"] == 5
        # Total bytes from seed: 1M + 2M + 1.5M + 3M + 0.5M = 8M
        assert data["final"]["bytes"] == 8_000_000

    def test_unavailable_stages(self, client, monkeypatch):
        nonexistent = Path("/nonexistent_test_path_xyz")
        monkeypatch.setattr(analytics_mod, "STAGING_PATH", nonexistent)
        monkeypatch.setattr(analytics_mod, "RAWS_PATH", nonexistent)
        monkeypatch.setattr(analytics_mod, "SSD_PATH", nonexistent)
        monkeypatch.setattr(analytics_mod, "FINAL_PATH", nonexistent)

        resp = client.get("/analytics/storage")
        data = resp.json()
        assert data["staging"]["available"] is False
        assert data["staging"]["count"] == 0
        assert data["staging"]["bytes"] == 0
        assert data["raws"]["available"] is False
        assert data["videos"]["available"] is False

    def test_staging_counts_files(self, client, monkeypatch, tmp_path):
        staging_dir = tmp_path / "Staging"
        staging_dir.mkdir()
        (staging_dir / "a.JPG").write_bytes(b"x" * 100)
        (staging_dir / "b.JPG").write_bytes(b"x" * 200)
        (staging_dir / "ignore.RAF").write_bytes(b"x" * 50)  # wrong ext

        nonexistent = Path("/nonexistent_test_path_xyz")
        monkeypatch.setattr(analytics_mod, "STAGING_PATH", staging_dir)
        monkeypatch.setattr(analytics_mod, "RAWS_PATH", nonexistent)
        monkeypatch.setattr(analytics_mod, "SSD_PATH", nonexistent)
        monkeypatch.setattr(analytics_mod, "FINAL_PATH", Path("/"))

        resp = client.get("/analytics/storage")
        data = resp.json()
        assert data["staging"]["available"] is True
        assert data["staging"]["count"] == 2
        assert data["staging"]["bytes"] == 300


# ---------------------------------------------------------------------------
# /analytics/map
# ---------------------------------------------------------------------------


class TestMap:
    def test_returns_only_gps_photos(self, client):
        resp = client.get("/analytics/map")
        assert resp.status_code == 200
        data = resp.json()
        # Only a (48.13, 11.58) and d (48.14, 11.59) have GPS; f is excluded
        assert len(data) == 2

    def test_map_point_shape(self, client):
        resp = client.get("/analytics/map")
        data = resp.json()
        for point in data:
            assert "lat" in point
            assert "lng" in point
            assert "filename" in point
            assert "date_taken" in point

    def test_lat_lng_values(self, client):
        resp = client.get("/analytics/map")
        filenames = {p["filename"]: p for p in resp.json()}
        assert abs(filenames["a.JPG"]["lat"] - 48.13) < 0.001
        assert abs(filenames["a.JPG"]["lng"] - 11.58) < 0.001

    def test_excluded_photo_not_on_map(self, client):
        resp = client.get("/analytics/map")
        filenames = {p["filename"] for p in resp.json()}
        assert "f.JPG" not in filenames  # in_final=0


# ---------------------------------------------------------------------------
# /analytics/summary
# ---------------------------------------------------------------------------


class TestSummary:
    def test_total_photos(self, client):
        resp = client.get("/analytics/summary")
        assert resp.status_code == 200
        data = resp.json()
        assert data["total_photos"] == 5

    def test_total_published(self, client):
        resp = client.get("/analytics/summary")
        data = resp.json()
        assert data["total_published"] == 2

    def test_avg_rating(self, client):
        resp = client.get("/analytics/summary")
        data = resp.json()
        # Ratings for in_final: 5+4+3+2+0 = 14, avg = 2.8
        assert data["avg_rating"] == pytest.approx(2.8)

    def test_date_range(self, client):
        resp = client.get("/analytics/summary")
        data = resp.json()
        assert data["earliest_date"] == "2026-01-10T09:00:00Z"
        assert data["latest_date"] == "2026-03-01T08:00:00Z"

    def test_this_month_count_zero_for_old_data(self, client):
        # All seed data is in Jan–Mar 2026; current date is 2026-06 so this_month = 0
        resp = client.get("/analytics/summary")
        data = resp.json()
        assert data["this_month_count"] == 0


# ---------------------------------------------------------------------------
# POST /index/refresh
# ---------------------------------------------------------------------------


class TestIndexRefresh:
    def test_returns_reindex_counts(self, client, monkeypatch):
        fake_counts = {"indexed": 3, "updated": 1, "skipped": 10, "removed": 0}
        monkeypatch.setattr(analytics_mod, "_run_reindex", lambda: fake_counts)

        resp = client.post("/index/refresh")
        assert resp.status_code == 200
        data = resp.json()
        assert data == fake_counts

    def test_response_schema(self, client, monkeypatch):
        monkeypatch.setattr(
            analytics_mod, "_run_reindex",
            lambda: {"indexed": 0, "updated": 0, "skipped": 5, "removed": 0},
        )
        resp = client.post("/index/refresh")
        data = resp.json()
        for key in ("indexed", "updated", "skipped", "removed"):
            assert key in data
            assert isinstance(data[key], int)


# ---------------------------------------------------------------------------
# Auto-reindex wiring (routes_ops)
# ---------------------------------------------------------------------------


class TestAutoReindex:
    def test_with_reindex_wraps_fn(self):
        """_with_reindex calls the wrapped fn and then reindex."""
        import photo_flow.api.routes_ops as ops_mod

        call_log = []
        original_fn = lambda reporter: (call_log.append("fn"), {"moved": 1})[1]  # noqa
        wrapped = ops_mod._with_reindex(original_fn)

        # Monkeypatch _run_reindex in routes_ops
        ops_mod_reindex_calls = []
        original_reindex = ops_mod._run_reindex

        def fake_reindex():
            ops_mod_reindex_calls.append(True)
            return {"indexed": 0, "updated": 0, "skipped": 5, "removed": 0}

        ops_mod._run_reindex = fake_reindex
        try:
            result = wrapped(reporter=MagicMock())
        finally:
            ops_mod._run_reindex = original_reindex

        assert "fn" in call_log
        assert len(ops_mod_reindex_calls) == 1
        assert result == {"moved": 1}

    def test_with_reindex_swallows_reindex_errors(self):
        """A reindex failure must not propagate or fail the job result."""
        import photo_flow.api.routes_ops as ops_mod

        fn = lambda reporter: {"moved": 2}  # noqa
        wrapped = ops_mod._with_reindex(fn)

        original_reindex = ops_mod._run_reindex
        ops_mod._run_reindex = lambda: (_ for _ in ()).throw(RuntimeError("disk full"))
        try:
            result = wrapped(reporter=MagicMock())
        finally:
            ops_mod._run_reindex = original_reindex

        # The job result is still returned cleanly
        assert result == {"moved": 2}
