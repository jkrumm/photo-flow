"""
Tests for photo_flow.index — schema, parsers, and incremental reindex.

The indexer is tested against monkeypatched extract_metadata and a temp-dir
of zero-byte fixture files; no real Final folder is touched.
"""

import os
import sqlite3
import tempfile
import time
from pathlib import Path
from typing import Dict, Any
from unittest.mock import patch, MagicMock

import pytest

from photo_flow.index.db import get_db, init_db
from photo_flow.index.indexer import (
    parse_aperture,
    parse_shutter,
    parse_focal,
    reindex,
)


# ---------------------------------------------------------------------------
# Parser tests
# ---------------------------------------------------------------------------

class TestParseAperture:
    @pytest.mark.parametrize("value,expected", [
        ("f/1.4", 1.4),
        ("f/2.8", 2.8),
        ("f/5.6", 5.6),
        ("f/22.0", 22.0),
    ])
    def test_valid(self, value, expected):
        assert parse_aperture(value) == pytest.approx(expected)

    @pytest.mark.parametrize("value", ["", None, "abc", "f/", "f/bad"])
    def test_malformed_returns_none(self, value):
        assert parse_aperture(value) is None


class TestParseShutter:
    @pytest.mark.parametrize("value,expected", [
        ("1/250", 1 / 250),
        ("1/4000", 1 / 4000),
        ("1/1", 1.0),
        ("1.5", 1.5),
        ("2.0", 2.0),
        ("0.5", 0.5),
        ("1/60", 1 / 60),
    ])
    def test_valid(self, value, expected):
        assert parse_shutter(value) == pytest.approx(expected)

    @pytest.mark.parametrize("value", ["", None, "abc", "1/0", "bad/250"])
    def test_malformed_returns_none(self, value):
        assert parse_shutter(value) is None

    def test_fraction_precision(self):
        result = parse_shutter("1/250")
        assert result is not None
        assert abs(result - 0.004) < 1e-9


class TestParseFocal:
    @pytest.mark.parametrize("value,expected", [
        ("35mm", 35.0),
        ("100mm", 100.0),
        ("18mm", 18.0),
        ("200mm", 200.0),
        ("50mm", 50.0),
    ])
    def test_valid(self, value, expected):
        assert parse_focal(value) == pytest.approx(expected)

    @pytest.mark.parametrize("value", ["", None, "mm", "badmm", "abc"])
    def test_malformed_returns_none(self, value):
        assert parse_focal(value) is None


# ---------------------------------------------------------------------------
# DB / schema tests
# ---------------------------------------------------------------------------

class TestSchema:
    def test_init_creates_table(self, tmp_path):
        db_path = tmp_path / "test.db"
        conn = get_db(db_path)
        cursor = conn.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='photos'")
        assert cursor.fetchone() is not None
        conn.close()

    def test_nullable_exif_columns(self, tmp_path):
        db_path = tmp_path / "test.db"
        conn = get_db(db_path)
        conn.execute("""
            INSERT INTO photos
                (path, filename, size, mtime, in_final, published, indexed_at)
            VALUES
                ('/fake/img.JPG', 'img.JPG', 100, 1000.0, 1, 0, '2026-01-01T00:00:00+00:00')
        """)
        conn.commit()
        row = conn.execute("SELECT * FROM photos WHERE path = '/fake/img.JPG'").fetchone()
        assert row["date_taken"] is None
        assert row["rating"] is None
        assert row["iso"] is None
        assert row["aperture_f"] is None
        assert row["shutter_s"] is None
        assert row["focal_mm"] is None
        assert row["latitude"] is None
        assert row["longitude"] is None
        conn.close()

    def test_indexes_exist(self, tmp_path):
        db_path = tmp_path / "test.db"
        conn = get_db(db_path)
        rows = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='index'"
        ).fetchall()
        index_names = {r["name"] for r in rows}
        assert "idx_date_taken" in index_names
        assert "idx_rating" in index_names
        conn.close()


# ---------------------------------------------------------------------------
# Reindex tests
# ---------------------------------------------------------------------------

def _make_fixture_jpg(directory: Path, name: str) -> Path:
    """Write a zero-byte file as a fixture JPG (content doesn't matter — we mock extract_metadata)."""
    p = directory / name
    p.write_bytes(b"")
    return p


def _fake_metadata(filename: str) -> Dict[str, Any]:
    """Return a minimal extract_metadata-shaped dict for a fixture file."""
    return {
        "filename": filename,
        "file_size": 0,
        "rating": 3,
        "iso": 400,
        "aperture": "f/2.8",
        "shutter_speed": "1/250",
        "focal_length": "35mm",
        "camera_model": "X-T4",
        "dimensions": "6240x4160",
        "date_taken": "2026-01-15T10:30:00Z",
    }


@pytest.fixture()
def db_conn(tmp_path):
    db_path = tmp_path / "index.db"
    conn = get_db(db_path)
    yield conn
    conn.close()


@pytest.fixture()
def final_dir(tmp_path):
    d = tmp_path / "Final"
    d.mkdir()
    return d


@pytest.fixture()
def gallery_dir(tmp_path):
    d = tmp_path / "Gallery"
    (d / "images").mkdir(parents=True)
    return d


class TestReindex:
    def _patch_extract(self, monkeypatch):
        """Monkeypatch MetadataExtractor.extract_metadata to return fake data."""
        from photo_flow.index import indexer as idx_module
        from photo_flow.metadata_extractor import MetadataExtractor

        def fake_extract(path: Path) -> Dict[str, Any]:
            return _fake_metadata(path.name)

        monkeypatch.setattr(MetadataExtractor, "extract_metadata", staticmethod(fake_extract))

    def test_first_run_indexes_all_files(self, monkeypatch, db_conn, final_dir, gallery_dir):
        self._patch_extract(monkeypatch)
        for name in ["a.JPG", "b.JPG", "c.JPG"]:
            _make_fixture_jpg(final_dir, name)

        result = reindex(conn=db_conn, final_path=final_dir, gallery_path=gallery_dir)

        assert result["indexed"] == 3
        assert result["updated"] == 0
        assert result["skipped"] == 0
        assert result["removed"] == 0

    def test_second_run_with_no_changes_skips_all(self, monkeypatch, db_conn, final_dir, gallery_dir):
        self._patch_extract(monkeypatch)
        for name in ["a.JPG", "b.JPG"]:
            _make_fixture_jpg(final_dir, name)

        reindex(conn=db_conn, final_path=final_dir, gallery_path=gallery_dir)
        result = reindex(conn=db_conn, final_path=final_dir, gallery_path=gallery_dir)

        assert result["indexed"] == 0
        assert result["updated"] == 0
        assert result["skipped"] == 2
        assert result["removed"] == 0

    def test_mtime_change_triggers_reread(self, monkeypatch, db_conn, final_dir, gallery_dir):
        self._patch_extract(monkeypatch)
        p = _make_fixture_jpg(final_dir, "x.JPG")

        reindex(conn=db_conn, final_path=final_dir, gallery_path=gallery_dir)

        # Simulate mtime change (touch the file in the future)
        future_mtime = p.stat().st_mtime + 2.0
        os.utime(p, (future_mtime, future_mtime))

        result = reindex(conn=db_conn, final_path=final_dir, gallery_path=gallery_dir)

        assert result["updated"] == 1
        assert result["skipped"] == 0

    def test_removed_file_marked_in_final_zero(self, monkeypatch, db_conn, final_dir, gallery_dir):
        self._patch_extract(monkeypatch)
        p = _make_fixture_jpg(final_dir, "del.JPG")
        _make_fixture_jpg(final_dir, "keep.JPG")

        reindex(conn=db_conn, final_path=final_dir, gallery_path=gallery_dir)

        # Remove one file
        p.unlink()

        result = reindex(conn=db_conn, final_path=final_dir, gallery_path=gallery_dir)

        assert result["removed"] == 1
        # The kept file is skipped (unchanged)
        assert result["skipped"] == 1

        row = db_conn.execute(
            "SELECT in_final FROM photos WHERE filename = 'del.JPG'"
        ).fetchone()
        assert row is not None
        assert row["in_final"] == 0

    def test_published_set_for_gallery_files(self, monkeypatch, db_conn, final_dir, gallery_dir):
        self._patch_extract(monkeypatch)
        _make_fixture_jpg(final_dir, "gallery.JPG")
        _make_fixture_jpg(final_dir, "nogallery.JPG")
        # gallery.JPG exists in gallery/images/
        _make_fixture_jpg(gallery_dir / "images", "gallery.JPG")

        reindex(conn=db_conn, final_path=final_dir, gallery_path=gallery_dir)

        rows = {
            r["filename"]: r["published"]
            for r in db_conn.execute("SELECT filename, published FROM photos")
        }
        assert rows["gallery.JPG"] == 1
        assert rows["nogallery.JPG"] == 0

    def test_exif_parsed_to_numeric_columns(self, monkeypatch, db_conn, final_dir, gallery_dir):
        self._patch_extract(monkeypatch)
        _make_fixture_jpg(final_dir, "parsed.JPG")

        reindex(conn=db_conn, final_path=final_dir, gallery_path=gallery_dir)

        row = db_conn.execute(
            "SELECT aperture_f, shutter_s, focal_mm, iso FROM photos WHERE filename='parsed.JPG'"
        ).fetchone()
        assert row["aperture_f"] == pytest.approx(2.8)
        assert row["shutter_s"] == pytest.approx(1 / 250)
        assert row["focal_mm"] == pytest.approx(35.0)
        assert row["iso"] == 400

    def test_empty_final_path_returns_zero_counts(self, db_conn, final_dir, gallery_dir):
        result = reindex(conn=db_conn, final_path=final_dir, gallery_path=gallery_dir)
        assert result == {"indexed": 0, "updated": 0, "skipped": 0, "removed": 0}

    def test_nonexistent_final_path_returns_zero_counts(self, db_conn, tmp_path, gallery_dir):
        missing = tmp_path / "DoesNotExist"
        result = reindex(conn=db_conn, final_path=missing, gallery_path=gallery_dir)
        assert result == {"indexed": 0, "updated": 0, "skipped": 0, "removed": 0}

    def test_opens_default_db_when_conn_is_none(self, monkeypatch, tmp_path, final_dir, gallery_dir):
        """reindex() opens and closes its own connection when conn=None."""
        self._patch_extract(monkeypatch)
        _make_fixture_jpg(final_dir, "auto.JPG")

        db_path = tmp_path / "auto.db"

        # Monkeypatch get_db to use our tmp path
        from photo_flow.index import indexer as idx_mod
        from photo_flow.index import db as db_mod

        orig_get_db = db_mod.get_db

        def patched_get_db(path=None):
            return orig_get_db(db_path)

        monkeypatch.setattr(idx_mod, "get_db", patched_get_db)

        result = reindex(conn=None, final_path=final_dir, gallery_path=gallery_dir)
        assert result["indexed"] == 1
