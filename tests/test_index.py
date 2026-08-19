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
    derive_orientation,
    parse_aperture,
    parse_shutter,
    parse_focal,
    reindex,
    reindex_paths,
)


@pytest.fixture(autouse=True)
def _isolate_staging(monkeypatch, tmp_path):
    """
    Point the indexer's STAGING_PATH at an empty temp dir for every test.

    Without this, a reindex() call that only overrides final_path would fall back
    to the real ~/Pictures/Staging folder.
    """
    from photo_flow.index import indexer as idx_mod

    empty = tmp_path / "StagingIsolated"
    empty.mkdir(exist_ok=True)
    monkeypatch.setattr(idx_mod, "STAGING_PATH", empty)
    return empty


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
        assert result == {
            "indexed": 0, "updated": 0, "skipped": 0, "removed": 0, "staging_indexed": 0,
        }

    def test_nonexistent_final_path_returns_zero_counts(self, db_conn, tmp_path, gallery_dir):
        missing = tmp_path / "DoesNotExist"
        result = reindex(conn=db_conn, final_path=missing, gallery_path=gallery_dir)
        assert result == {
            "indexed": 0, "updated": 0, "skipped": 0, "removed": 0, "staging_indexed": 0,
        }

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


# ---------------------------------------------------------------------------
# Schema v2 — migration, culling columns, trash table
# ---------------------------------------------------------------------------

_V1_SCHEMA = """
    CREATE TABLE photos (
        path         TEXT PRIMARY KEY,
        filename     TEXT NOT NULL,
        size         INTEGER NOT NULL,
        mtime        REAL NOT NULL,
        date_taken   TEXT,
        rating       INTEGER,
        iso          INTEGER,
        aperture_f   REAL,
        shutter_s    REAL,
        focal_mm     REAL,
        latitude     REAL,
        longitude    REAL,
        camera_model TEXT,
        dimensions   TEXT,
        in_final     INTEGER NOT NULL DEFAULT 1,
        published    INTEGER NOT NULL DEFAULT 0,
        indexed_at   TEXT NOT NULL
    );
"""

_V2_COLUMNS = {
    "root", "present", "width", "height", "orientation",
    "lens_model", "camera_make", "label", "has_sidecar",
}


def _make_v1_db(db_path: Path) -> None:
    """Create a pre-migration (schema v1) database holding two rows."""
    conn = sqlite3.connect(str(db_path))
    conn.executescript(_V1_SCHEMA)
    conn.executemany(
        "INSERT INTO photos (path, filename, size, mtime, rating, in_final, published, indexed_at)"
        " VALUES (?, ?, ?, ?, ?, ?, ?, '2026-01-01T00:00:00+00:00')",
        [
            ("/Final/live.JPG", "live.JPG", 100, 1000.0, 5, 1, 1),
            ("/Final/gone.JPG", "gone.JPG", 200, 2000.0, 2, 0, 0),
        ],
    )
    conn.commit()
    conn.close()


class TestSchemaV2:
    def test_columns_added_to_fresh_db(self, tmp_path):
        conn = get_db(tmp_path / "fresh.db")
        cols = {r["name"] for r in conn.execute("PRAGMA table_info(photos)")}
        assert _V2_COLUMNS.issubset(cols)
        conn.close()

    def test_trash_table_created(self, tmp_path):
        conn = get_db(tmp_path / "fresh.db")
        row = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='trash'"
        ).fetchone()
        assert row is not None
        cols = {r["name"] for r in conn.execute("PRAGMA table_info(trash)")}
        assert cols == {
            "id", "original_path", "root", "trashed_path", "sidecar_original_path",
            "sidecar_trashed_path", "filename", "size", "rating", "trashed_at",
        }
        conn.close()

    def test_new_indexes_exist(self, tmp_path):
        conn = get_db(tmp_path / "fresh.db")
        names = {r["name"] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='index'")}
        assert {"idx_date_taken", "idx_rating", "idx_root_present", "idx_iso", "idx_focal_mm"} <= names
        conn.close()

    def test_migration_preserves_existing_rows(self, tmp_path):
        db_path = tmp_path / "legacy.db"
        _make_v1_db(db_path)

        conn = get_db(db_path)
        rows = {r["filename"]: r for r in conn.execute("SELECT * FROM photos")}
        assert len(rows) == 2
        assert rows["live.JPG"]["rating"] == 5
        assert rows["gone.JPG"]["size"] == 200
        conn.close()

    def test_migration_backfills_root_and_present(self, tmp_path):
        db_path = tmp_path / "legacy.db"
        _make_v1_db(db_path)

        conn = get_db(db_path)
        rows = {r["filename"]: r for r in conn.execute("SELECT * FROM photos")}
        # Pre-v2 only Final was ever indexed
        assert rows["live.JPG"]["root"] == "final"
        assert rows["gone.JPG"]["root"] == "final"
        # present mirrors the v1 meaning of in_final
        assert rows["live.JPG"]["present"] == 1
        assert rows["gone.JPG"]["present"] == 0
        assert rows["gone.JPG"]["in_final"] == 0
        conn.close()

    def test_migration_is_idempotent(self, tmp_path):
        db_path = tmp_path / "legacy.db"
        _make_v1_db(db_path)

        conn = get_db(db_path)
        conn.close()
        # Second open runs init_db again; must not raise or clobber the backfill
        conn = get_db(db_path)
        init_db(conn)
        rows = {r["filename"]: r for r in conn.execute("SELECT * FROM photos")}
        assert len(rows) == 2
        assert rows["gone.JPG"]["present"] == 0
        conn.close()

    def test_migration_does_not_reset_a_manual_present_flag(self, tmp_path):
        """The in_final→present backfill runs only in the pass that adds the column."""
        db_path = tmp_path / "legacy.db"
        _make_v1_db(db_path)

        conn = get_db(db_path)
        conn.execute("UPDATE photos SET present = 1 WHERE filename = 'gone.JPG'")
        conn.commit()
        init_db(conn)
        row = conn.execute("SELECT present FROM photos WHERE filename = 'gone.JPG'").fetchone()
        assert row["present"] == 1
        conn.close()


class TestDeriveOrientation:
    @pytest.mark.parametrize("width,height,expected", [
        (6240, 4160, "landscape"),
        (4160, 6240, "portrait"),
        (2000, 2000, "square"),
    ])
    def test_valid(self, width, height, expected):
        assert derive_orientation(width, height) == expected

    @pytest.mark.parametrize("width,height", [(None, 100), (100, None), (0, 100), (None, None)])
    def test_missing_returns_none(self, width, height):
        assert derive_orientation(width, height) is None


# ---------------------------------------------------------------------------
# Two-root reindex
# ---------------------------------------------------------------------------

@pytest.fixture()
def staging_dir(tmp_path):
    d = tmp_path / "Staging"
    d.mkdir()
    return d


class TestReindexRoots:
    def _patch_extract(self, monkeypatch):
        from photo_flow.metadata_extractor import MetadataExtractor

        def fake_extract(path: Path) -> Dict[str, Any]:
            return _fake_metadata(path.name)

        monkeypatch.setattr(MetadataExtractor, "extract_metadata", staticmethod(fake_extract))

    def test_staging_indexed_with_in_final_zero(self, monkeypatch, db_conn, final_dir, staging_dir, gallery_dir):
        self._patch_extract(monkeypatch)
        _make_fixture_jpg(final_dir, "f.JPG")
        _make_fixture_jpg(staging_dir, "s1.JPG")
        _make_fixture_jpg(staging_dir, "s2.JPG")

        result = reindex(
            conn=db_conn, final_path=final_dir,
            staging_path=staging_dir, gallery_path=gallery_dir,
        )

        assert result["indexed"] == 3
        assert result["staging_indexed"] == 2

        rows = {r["filename"]: r for r in db_conn.execute("SELECT * FROM photos")}
        assert rows["f.JPG"]["root"] == "final"
        assert rows["f.JPG"]["in_final"] == 1
        for name in ("s1.JPG", "s2.JPG"):
            assert rows[name]["root"] == "staging"
            assert rows[name]["in_final"] == 0
            assert rows[name]["present"] == 1

    def test_in_final_invariant_holds_for_every_row(self, monkeypatch, db_conn, final_dir, staging_dir, gallery_dir):
        """in_final == 1 iff root == 'final' AND present == 1 — analytics depends on it."""
        self._patch_extract(monkeypatch)
        _make_fixture_jpg(final_dir, "keep.JPG")
        gone = _make_fixture_jpg(final_dir, "gone.JPG")
        _make_fixture_jpg(staging_dir, "s.JPG")

        reindex(conn=db_conn, final_path=final_dir, staging_path=staging_dir, gallery_path=gallery_dir)
        gone.unlink()
        reindex(conn=db_conn, final_path=final_dir, staging_path=staging_dir, gallery_path=gallery_dir)

        violations = db_conn.execute("""
            SELECT COUNT(*) AS n FROM photos
            WHERE in_final != (CASE WHEN root = 'final' AND present = 1 THEN 1 ELSE 0 END)
        """).fetchone()
        assert violations["n"] == 0

    def test_analytics_query_sees_only_final(self, monkeypatch, db_conn, final_dir, staging_dir, gallery_dir):
        self._patch_extract(monkeypatch)
        _make_fixture_jpg(final_dir, "f.JPG")
        _make_fixture_jpg(staging_dir, "s.JPG")

        reindex(conn=db_conn, final_path=final_dir, staging_path=staging_dir, gallery_path=gallery_dir)

        names = {r["filename"] for r in db_conn.execute("SELECT filename FROM photos WHERE in_final = 1")}
        assert names == {"f.JPG"}

    def test_missing_staging_dir_does_not_mark_rows_absent(self, monkeypatch, db_conn, final_dir, staging_dir, gallery_dir, tmp_path):
        self._patch_extract(monkeypatch)
        _make_fixture_jpg(staging_dir, "s.JPG")
        reindex(conn=db_conn, final_path=final_dir, staging_path=staging_dir, gallery_path=gallery_dir)

        # Staging folder becomes unreachable (unmounted / renamed) — not "all deleted"
        result = reindex(
            conn=db_conn, final_path=final_dir,
            staging_path=tmp_path / "NotThere", gallery_path=gallery_dir,
        )
        assert result["removed"] == 0
        row = db_conn.execute("SELECT present FROM photos WHERE filename = 's.JPG'").fetchone()
        assert row["present"] == 1

    def test_finalize_move_flips_both_rows(self, monkeypatch, db_conn, final_dir, staging_dir, gallery_dir):
        """A JPG moving Staging → Final leaves the staging row absent and a live final row."""
        self._patch_extract(monkeypatch)
        src = _make_fixture_jpg(staging_dir, "moved.JPG")
        reindex(conn=db_conn, final_path=final_dir, staging_path=staging_dir, gallery_path=gallery_dir)

        src.rename(final_dir / "moved.JPG")
        reindex(conn=db_conn, final_path=final_dir, staging_path=staging_dir, gallery_path=gallery_dir)

        rows = {r["path"]: r for r in db_conn.execute("SELECT * FROM photos")}
        staged = rows[str(staging_dir / "moved.JPG")]
        finalized = rows[str(final_dir / "moved.JPG")]
        assert staged["present"] == 0 and staged["in_final"] == 0
        assert finalized["present"] == 1 and finalized["in_final"] == 1

    def test_culling_columns_populated(self, monkeypatch, db_conn, final_dir, staging_dir, gallery_dir):
        self._patch_extract(monkeypatch)
        _make_fixture_jpg(final_dir, "shot.JPG")

        reindex(conn=db_conn, final_path=final_dir, staging_path=staging_dir, gallery_path=gallery_dir)

        row = db_conn.execute("SELECT * FROM photos WHERE filename = 'shot.JPG'").fetchone()
        assert row["width"] == 6240
        assert row["height"] == 4160
        assert row["orientation"] == "landscape"
        assert row["dimensions"] == "6240x4160"
        assert row["has_sidecar"] == 0

    def test_has_sidecar_detected(self, monkeypatch, db_conn, final_dir, staging_dir, gallery_dir):
        from photo_flow.config import EDIT_SIDECAR_SUFFIX

        self._patch_extract(monkeypatch)
        _make_fixture_jpg(final_dir, "edited.JPG")
        (final_dir / f"edited{EDIT_SIDECAR_SUFFIX}").write_bytes(b"")

        reindex(conn=db_conn, final_path=final_dir, staging_path=staging_dir, gallery_path=gallery_dir)

        row = db_conn.execute("SELECT has_sidecar FROM photos WHERE filename = 'edited.JPG'").fetchone()
        assert row["has_sidecar"] == 1

    def test_label_and_lens_stored(self, monkeypatch, db_conn, final_dir, staging_dir, gallery_dir):
        from photo_flow.metadata_extractor import MetadataExtractor

        def fake_extract(path: Path) -> Dict[str, Any]:
            meta = _fake_metadata(path.name)
            meta["label"] = "Red"
            meta["lens_model"] = "XF16-55mmF2.8 R LM WR"
            meta["camera_make"] = "FUJIFILM"
            return meta

        monkeypatch.setattr(MetadataExtractor, "extract_metadata", staticmethod(fake_extract))
        _make_fixture_jpg(final_dir, "labelled.JPG")

        reindex(conn=db_conn, final_path=final_dir, staging_path=staging_dir, gallery_path=gallery_dir)

        row = db_conn.execute("SELECT * FROM photos WHERE filename = 'labelled.JPG'").fetchone()
        assert row["label"] == "Red"
        assert row["lens_model"] == "XF16-55mmF2.8 R LM WR"
        assert row["camera_make"] == "FUJIFILM"

    def test_missing_label_and_lens_default_to_empty(self, monkeypatch, db_conn, final_dir, staging_dir, gallery_dir):
        self._patch_extract(monkeypatch)
        _make_fixture_jpg(final_dir, "plain.JPG")

        reindex(conn=db_conn, final_path=final_dir, staging_path=staging_dir, gallery_path=gallery_dir)

        row = db_conn.execute("SELECT label, lens_model FROM photos WHERE filename = 'plain.JPG'").fetchone()
        assert row["label"] == ""
        assert row["lens_model"] == ""


class TestReindexPaths:
    def _patch_rating(self, monkeypatch, rating: int):
        from photo_flow.metadata_extractor import MetadataExtractor

        def fake_extract(path: Path) -> Dict[str, Any]:
            meta = _fake_metadata(path.name)
            meta["rating"] = rating
            return meta

        monkeypatch.setattr(MetadataExtractor, "extract_metadata", staticmethod(fake_extract))

    def test_rereads_without_mtime_change(self, monkeypatch, db_conn, final_dir, staging_dir, gallery_dir):
        self._patch_rating(monkeypatch, 3)
        p = _make_fixture_jpg(final_dir, "rated.JPG")
        reindex(conn=db_conn, final_path=final_dir, staging_path=staging_dir, gallery_path=gallery_dir)

        # Rating written by exiftool would change mtime in reality; here it must
        # be re-read regardless of the incremental skip.
        self._patch_rating(monkeypatch, 5)
        roots = {"final": final_dir, "staging": staging_dir}
        result = reindex_paths([p], conn=db_conn, gallery_path=gallery_dir, roots=roots)

        assert result["updated"] == 1
        row = db_conn.execute("SELECT rating FROM photos WHERE filename = 'rated.JPG'").fetchone()
        assert row["rating"] == 5

    def test_ignores_paths_outside_roots(self, monkeypatch, db_conn, tmp_path, final_dir, staging_dir, gallery_dir):
        self._patch_rating(monkeypatch, 1)
        outside = tmp_path / "elsewhere.JPG"
        outside.write_bytes(b"")

        roots = {"final": final_dir, "staging": staging_dir}
        result = reindex_paths([outside], conn=db_conn, gallery_path=gallery_dir, roots=roots)

        assert result == {"indexed": 0, "updated": 0, "removed": 0, "skipped": 1}
        assert db_conn.execute("SELECT COUNT(*) AS n FROM photos").fetchone()["n"] == 0

    def test_vanished_file_marked_absent(self, monkeypatch, db_conn, final_dir, staging_dir, gallery_dir):
        self._patch_rating(monkeypatch, 3)
        p = _make_fixture_jpg(final_dir, "trashed.JPG")
        reindex(conn=db_conn, final_path=final_dir, staging_path=staging_dir, gallery_path=gallery_dir)

        p.unlink()
        roots = {"final": final_dir, "staging": staging_dir}
        result = reindex_paths([p], conn=db_conn, gallery_path=gallery_dir, roots=roots)

        assert result["removed"] == 1
        row = db_conn.execute("SELECT present, in_final FROM photos WHERE filename = 'trashed.JPG'").fetchone()
        assert row["present"] == 0
        assert row["in_final"] == 0

    def test_empty_input_is_a_noop(self, db_conn, gallery_dir):
        result = reindex_paths([], conn=db_conn, gallery_path=gallery_dir)
        assert result == {"indexed": 0, "updated": 0, "removed": 0, "skipped": 0}
