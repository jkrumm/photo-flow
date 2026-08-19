"""
End-to-end tests for the two-root indexer against REAL image files.

tests/test_index.py drives the indexer with a monkeypatched
``MetadataExtractor.extract_metadata``, which proves the SQL but says nothing
about whether the extractor actually produces the schema-v2 columns. These tests
close that gap: every fixture is a real JPEG carrying real EXIF (written with
piexif) and real XMP rating/label tags (written with exiftool, exactly as the
control panel's write-back does), and the assertions run over the whole chain
file → extract_metadata → reindex → SQLite.

The headline invariant has its own class: ``in_final = 1`` iff
``root = 'final' AND present = 1``. Every analytics query filters on that column,
so the arrival of a second root must be invisible to the analytics surface — and
that is asserted through the live ``/analytics/*`` endpoints, not a hand-rolled
copy of their SQL.

Nothing here touches ~/Pictures or ~/.photoflow: the roots are tmp_path dirs and
the database is a tmp_path file.
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path
from typing import Optional

import piexif
import pytest
from fastapi.testclient import TestClient
from PIL import Image

import photo_flow.api.routes_analytics as analytics_mod
from photo_flow.api.app import create_app
from photo_flow.config import EDIT_SIDECAR_SUFFIX
from photo_flow.index.db import get_db
from photo_flow.index.indexer import reindex, reindex_paths

pytestmark = pytest.mark.skipif(
    shutil.which("exiftool") is None,
    reason="exiftool is required to write the XMP rating/label fixtures",
)

_LENS = "XF16-55mmF2.8 R LM WR"


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture()
def db_conn(tmp_path):
    connection = get_db(tmp_path / "index.db")
    yield connection
    connection.close()


@pytest.fixture()
def final_dir(tmp_path):
    d = tmp_path / "Final"
    d.mkdir()
    return d


@pytest.fixture()
def staging_dir(tmp_path):
    d = tmp_path / "Staging"
    d.mkdir()
    return d


@pytest.fixture()
def gallery_dir(tmp_path):
    d = tmp_path / "Gallery"
    (d / "images").mkdir(parents=True)
    return d


def _make_photo(
    directory: Path,
    name: str,
    size: tuple = (240, 160),
    iso: int = 400,
    lens: str = _LENS,
    exif_orientation: Optional[int] = None,
) -> Path:
    """
    Write a real JPEG fixture carrying the EXIF the indexer reads.

    Args:
        directory: Folder to write into.
        name: Filename including the .JPG extension.
        size: (width, height) in pixels — the raster, which is NOT the display size
            when exif_orientation rotates the frame.
        iso: ISO speed written to ExifIFD.
        lens: LensModel string written to ExifIFD (0xA434).
        exif_orientation: EXIF Orientation tag (0x0112). 6 and 8 are the 90° cases the
            Fuji writes for portrait frames, where the raster stays landscape.

    Returns:
        Path to the written file.
    """
    path = directory / name
    exif = {
        "0th": {
            piexif.ImageIFD.Make: b"FUJIFILM",
            piexif.ImageIFD.Model: b"X-T4",
            **(
                {}
                if exif_orientation is None
                else {piexif.ImageIFD.Orientation: exif_orientation}
            ),
        },
        "Exif": {
            piexif.ExifIFD.ISOSpeedRatings: iso,
            piexif.ExifIFD.FNumber: (28, 10),
            piexif.ExifIFD.ExposureTime: (1, 250),
            piexif.ExifIFD.FocalLength: (35, 1),
            piexif.ExifIFD.LensModel: lens.encode("utf-8"),
            piexif.ExifIFD.DateTimeOriginal: b"2026:03:14 09:15:00",
        },
    }
    Image.new("RGB", size, (120, 90, 60)).save(
        path, "JPEG", quality=88, exif=piexif.dump(exif)
    )
    return path


def _write_xmp(path: Path, rating: Optional[int] = None, label: Optional[str] = None) -> None:
    """Write XMP rating/label with exiftool — the same tags the panel writes back."""
    args = ["exiftool", "-overwrite_original"]
    if rating is not None:
        args.append(f"-XMP-xmp:Rating={rating}")
    if label is not None:
        args.append(f"-XMP-xmp:Label={label}")
    args.append(str(path))
    proc = subprocess.run(args, capture_output=True, text=True, timeout=30)
    assert proc.returncode == 0, proc.stderr


def _run(db_conn, final_dir, staging_dir, gallery_dir) -> dict:
    """Reindex both roots against the temp fixtures."""
    return reindex(
        conn=db_conn,
        final_path=final_dir,
        staging_path=staging_dir,
        gallery_path=gallery_dir,
    )


def _row(db_conn, filename: str):
    return db_conn.execute(
        "SELECT * FROM photos WHERE filename = ?", (filename,)
    ).fetchone()


# ---------------------------------------------------------------------------
# Root assignment
# ---------------------------------------------------------------------------

class TestRootAssignment:
    def test_staging_photo_is_indexed_as_staging(self, db_conn, final_dir, staging_dir, gallery_dir):
        _make_photo(staging_dir, "pending.JPG")

        result = _run(db_conn, final_dir, staging_dir, gallery_dir)

        assert result["indexed"] == 1
        assert result["staging_indexed"] == 1

        row = _row(db_conn, "pending.JPG")
        assert row["root"] == "staging"
        assert row["in_final"] == 0
        assert row["present"] == 1

    def test_final_photo_keeps_in_final(self, db_conn, final_dir, staging_dir, gallery_dir):
        _make_photo(final_dir, "keeper.JPG")

        _run(db_conn, final_dir, staging_dir, gallery_dir)

        row = _row(db_conn, "keeper.JPG")
        assert row["root"] == "final"
        assert row["in_final"] == 1
        assert row["present"] == 1

    def test_same_filename_in_both_roots_yields_two_rows(self, db_conn, final_dir, staging_dir, gallery_dir):
        """Path is the primary key, so a Staging copy never overwrites the Final row."""
        _make_photo(final_dir, "twin.JPG")
        _make_photo(staging_dir, "twin.JPG")

        _run(db_conn, final_dir, staging_dir, gallery_dir)

        rows = {
            r["root"]: r
            for r in db_conn.execute("SELECT * FROM photos WHERE filename = 'twin.JPG'")
        }
        assert set(rows) == {"final", "staging"}
        assert rows["final"]["in_final"] == 1
        assert rows["staging"]["in_final"] == 0

    def test_deleted_file_flips_present_to_zero(self, db_conn, final_dir, staging_dir, gallery_dir):
        photo = _make_photo(staging_dir, "vanishing.JPG")
        _run(db_conn, final_dir, staging_dir, gallery_dir)

        photo.unlink()
        result = _run(db_conn, final_dir, staging_dir, gallery_dir)

        assert result["removed"] == 1
        row = _row(db_conn, "vanishing.JPG")
        assert row is not None, "the row is history, not garbage — it must survive"
        assert row["present"] == 0
        assert row["in_final"] == 0

    def test_deleted_final_file_flips_both_flags(self, db_conn, final_dir, staging_dir, gallery_dir):
        photo = _make_photo(final_dir, "culled.JPG")
        _run(db_conn, final_dir, staging_dir, gallery_dir)

        photo.unlink()
        _run(db_conn, final_dir, staging_dir, gallery_dir)

        row = _row(db_conn, "culled.JPG")
        assert row["present"] == 0
        assert row["in_final"] == 0
        assert row["root"] == "final", "the row still records where it lived"


# ---------------------------------------------------------------------------
# THE INVARIANT: in_final = 1 iff root='final' AND present=1
# ---------------------------------------------------------------------------

class TestInFinalInvariant:
    def _seed_mixed_library(self, db_conn, final_dir, staging_dir, gallery_dir):
        """Two Final photos (one later deleted) and two Staging photos."""
        _make_photo(final_dir, "f-keep.JPG")
        doomed = _make_photo(final_dir, "f-gone.JPG")
        _make_photo(staging_dir, "s-one.JPG")
        _make_photo(staging_dir, "s-two.JPG")

        _run(db_conn, final_dir, staging_dir, gallery_dir)
        doomed.unlink()
        _run(db_conn, final_dir, staging_dir, gallery_dir)

    def test_no_row_violates_the_invariant(self, db_conn, final_dir, staging_dir, gallery_dir):
        self._seed_mixed_library(db_conn, final_dir, staging_dir, gallery_dir)

        violations = db_conn.execute(
            """
            SELECT path FROM photos
            WHERE in_final != (CASE WHEN root = 'final' AND present = 1 THEN 1 ELSE 0 END)
            """
        ).fetchall()

        assert [r["path"] for r in violations] == []

    def test_analytics_style_query_returns_only_live_final(self, db_conn, final_dir, staging_dir, gallery_dir):
        self._seed_mixed_library(db_conn, final_dir, staging_dir, gallery_dir)

        names = {
            r["filename"]
            for r in db_conn.execute("SELECT filename FROM photos WHERE in_final = 1")
        }

        assert names == {"f-keep.JPG"}

    def test_live_analytics_endpoints_ignore_staging(
        self, db_conn, final_dir, staging_dir, gallery_dir, tmp_path, monkeypatch
    ):
        """The real /analytics/* handlers, not a copy of their SQL."""
        self._seed_mixed_library(db_conn, final_dir, staging_dir, gallery_dir)
        db_conn.commit()

        db_path = tmp_path / "index.db"
        monkeypatch.setattr(analytics_mod, "_open_conn", lambda: get_db(db_path))

        with TestClient(create_app()) as client:
            summary = client.get("/analytics/summary").json()
            ratings = client.get("/analytics/ratings").json()
            over_time = client.get("/analytics/over-time", params={"bucket": "month"}).json()
            settings = client.get("/analytics/settings").json()

        assert summary["total_photos"] == 1, "Staging must never inflate the Final totals"
        assert ratings["total_final"] == 1
        assert sum(b["count"] for b in ratings["histogram"]) == 1
        assert sum(point["total"] for point in over_time) == 1
        assert sum(b["count"] for b in settings["iso"]) == 1


# ---------------------------------------------------------------------------
# Schema-v2 columns, populated by the real extractor
# ---------------------------------------------------------------------------

class TestCullingColumns:
    def test_dimensions_and_orientation_from_a_real_file(self, db_conn, final_dir, staging_dir, gallery_dir):
        _make_photo(final_dir, "wide.JPG", size=(320, 200))
        _make_photo(final_dir, "tall.JPG", size=(200, 320))
        _make_photo(final_dir, "boxy.JPG", size=(256, 256))

        _run(db_conn, final_dir, staging_dir, gallery_dir)

        wide = _row(db_conn, "wide.JPG")
        assert (wide["width"], wide["height"]) == (320, 200)
        assert wide["orientation"] == "landscape"
        assert wide["dimensions"] == "320x200", "analytics still reads the legacy string"

        assert _row(db_conn, "tall.JPG")["orientation"] == "portrait"
        assert _row(db_conn, "boxy.JPG")["orientation"] == "square"

    @pytest.mark.parametrize("tag", [6, 8])
    def test_rotated_frames_are_indexed_at_their_display_size(
        self, db_conn, final_dir, staging_dir, gallery_dir, tag
    ):
        """A Fuji portrait frame is a landscape raster plus Orientation 6/8.

        Reading img.width/img.height straight off the raster indexed ~20% of the live
        library as landscape when it is portrait: the orientation facet under-counted
        Portrait by the same amount, the Landscape filter falsely returned them, and the
        info panel printed the frame sideways — while index/thumbs.py already applied
        exif_transpose, so the thumbnail and the row disagreed about one file.
        """
        _make_photo(final_dir, f"rotated{tag}.JPG", size=(320, 200), exif_orientation=tag)

        _run(db_conn, final_dir, staging_dir, gallery_dir)

        row = _row(db_conn, f"rotated{tag}.JPG")
        assert (row["width"], row["height"]) == (200, 320)
        assert row["dimensions"] == "200x320"
        assert row["orientation"] == "portrait"

    @pytest.mark.parametrize("tag", [1, 3])
    def test_unrotated_orientation_tags_leave_the_raster_alone(
        self, db_conn, final_dir, staging_dir, gallery_dir, tag
    ):
        """Orientation 1 (normal) and 3 (180°) do not transpose — only 5-8 do."""
        _make_photo(final_dir, f"flat{tag}.JPG", size=(320, 200), exif_orientation=tag)

        _run(db_conn, final_dir, staging_dir, gallery_dir)

        row = _row(db_conn, f"flat{tag}.JPG")
        assert (row["width"], row["height"]) == (320, 200)
        assert row["orientation"] == "landscape"

    def test_lens_and_camera_make_extracted(self, db_conn, final_dir, staging_dir, gallery_dir):
        _make_photo(final_dir, "lensed.JPG")

        _run(db_conn, final_dir, staging_dir, gallery_dir)

        row = _row(db_conn, "lensed.JPG")
        assert row["lens_model"] == _LENS
        assert row["camera_make"] == "FUJIFILM"
        assert row["camera_model"] == "X-T4"
        assert row["iso"] == 400

    def test_missing_lens_defaults_to_empty_string(self, db_conn, final_dir, staging_dir, gallery_dir):
        path = final_dir / "bare.JPG"
        Image.new("RGB", (100, 60), (10, 10, 10)).save(path, "JPEG")

        _run(db_conn, final_dir, staging_dir, gallery_dir)

        row = _row(db_conn, "bare.JPG")
        assert row["lens_model"] == ""
        assert row["label"] == ""

    def test_xmp_rating_and_label_extracted(self, db_conn, final_dir, staging_dir, gallery_dir):
        photo = _make_photo(final_dir, "rated.JPG")
        _write_xmp(photo, rating=4, label="Red")

        _run(db_conn, final_dir, staging_dir, gallery_dir)

        row = _row(db_conn, "rated.JPG")
        assert row["rating"] == 4
        assert row["label"] == "Red"

    def test_has_sidecar_detects_the_photo_edit_neighbour(self, db_conn, final_dir, staging_dir, gallery_dir):
        edited = _make_photo(staging_dir, "edited.JPG")
        edited.with_suffix(EDIT_SIDECAR_SUFFIX).write_bytes(b"photomator-history")
        _make_photo(staging_dir, "untouched.JPG")

        _run(db_conn, final_dir, staging_dir, gallery_dir)

        assert _row(db_conn, "edited.JPG")["has_sidecar"] == 1
        assert _row(db_conn, "untouched.JPG")["has_sidecar"] == 0

    def test_sidecar_is_not_indexed_as_a_photo(self, db_conn, final_dir, staging_dir, gallery_dir):
        edited = _make_photo(staging_dir, "solo.JPG")
        edited.with_suffix(EDIT_SIDECAR_SUFFIX).write_bytes(b"history")

        result = _run(db_conn, final_dir, staging_dir, gallery_dir)

        assert result["indexed"] == 1
        assert db_conn.execute("SELECT COUNT(*) AS n FROM photos").fetchone()["n"] == 1


# ---------------------------------------------------------------------------
# Targeted re-read after a write-back
# ---------------------------------------------------------------------------

class TestReindexPathsAfterWrite:
    def test_rating_write_is_picked_up(self, db_conn, final_dir, staging_dir, gallery_dir):
        photo = _make_photo(final_dir, "rerate.JPG")
        _write_xmp(photo, rating=1)
        _run(db_conn, final_dir, staging_dir, gallery_dir)
        assert _row(db_conn, "rerate.JPG")["rating"] == 1

        _write_xmp(photo, rating=5, label="Green")
        result = reindex_paths(
            [photo],
            conn=db_conn,
            gallery_path=gallery_dir,
            roots={"final": final_dir, "staging": staging_dir},
        )

        assert result["updated"] == 1
        row = _row(db_conn, "rerate.JPG")
        assert row["rating"] == 5
        assert row["label"] == "Green"

    def test_staging_path_is_reindexable(self, db_conn, final_dir, staging_dir, gallery_dir):
        photo = _make_photo(staging_dir, "fresh.JPG")

        result = reindex_paths(
            [photo],
            conn=db_conn,
            gallery_path=gallery_dir,
            roots={"final": final_dir, "staging": staging_dir},
        )

        assert result["indexed"] == 1
        row = _row(db_conn, "fresh.JPG")
        assert row["root"] == "staging"
        assert row["in_final"] == 0
