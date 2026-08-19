"""
Tests for the culling API — photo_flow/api/routes_photos.py (prefix /api/photos).

Strategy
--------
One autouse fixture relocates every path the router can reach:

  * ``routes_photos.CULL_ROOTS`` / ``TRASH_PATH``  — the request-path allowlist
  * ``config.CULL_ROOTS`` / ``config.TRASH_PATH``  — read at call time by photo_flow.trash
  * ``thumbs.THUMB_CACHE_PATH``                    — the derived thumbnail cache
  * ``index.db._DEFAULT_DB_PATH``                  — every ``get_db()`` with no explicit path
  * ``indexer.FINAL_PATH`` / ``STAGING_PATH`` / ``GALLERY_PATH`` — reindex_paths' default roots

So no test can touch ~/Pictures, ~/.photoflow, or the real index.

exiftool is never actually invoked: ``subprocess.run`` is monkeypatched inside the
write tests, which is also how the "one process per batch" contract is asserted.

The safety-critical assertions:
  * a path outside the cull roots is rejected with 400 *before* anything is read,
    written or moved — including the classic ``../../.ssh`` traversal
  * ``present = 0`` rows (trashed / finalized away) never appear in a listing
    unless ``include_trashed=true`` asks for them, and never carry metadata the
    index cannot actually back
  * a trash round-trip restores the JPG and its .photo-edit sidecar
"""

from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Any, Dict, List, Optional

import pytest
from fastapi.testclient import TestClient
from PIL import Image

import photo_flow.api.routes_photos as photos_mod
from photo_flow import config
from photo_flow.api.app import create_app
from photo_flow.index import db as db_mod
from photo_flow.index import indexer as indexer_mod
from photo_flow.index import thumbs as thumbs_mod
from photo_flow.index.db import get_db


# ---------------------------------------------------------------------------
# Environment isolation
# ---------------------------------------------------------------------------

@pytest.fixture()
def env(tmp_path, monkeypatch) -> Dict[str, Path]:
    """Relocate every filesystem and database path the router can reach."""
    final_dir = tmp_path / "Final"
    staging_dir = tmp_path / "Staging"
    gallery_dir = tmp_path / "Gallery"
    trash_dir = tmp_path / ".photoflow-trash"
    final_dir.mkdir()
    staging_dir.mkdir()
    (gallery_dir / "images").mkdir(parents=True)

    roots = {"final": final_dir, "staging": staging_dir}
    monkeypatch.setattr(photos_mod, "CULL_ROOTS", roots)
    monkeypatch.setattr(photos_mod, "TRASH_PATH", trash_dir)
    monkeypatch.setattr(config, "CULL_ROOTS", roots)
    monkeypatch.setattr(config, "TRASH_PATH", trash_dir)
    monkeypatch.setattr(thumbs_mod, "THUMB_CACHE_PATH", tmp_path / "thumbs")
    monkeypatch.setattr(db_mod, "_DEFAULT_DB_PATH", tmp_path / "index.db")
    monkeypatch.setattr(indexer_mod, "FINAL_PATH", final_dir)
    monkeypatch.setattr(indexer_mod, "STAGING_PATH", staging_dir)
    monkeypatch.setattr(indexer_mod, "GALLERY_PATH", gallery_dir)

    return {
        "final": final_dir,
        "staging": staging_dir,
        "gallery": gallery_dir,
        "trash": trash_dir,
        "db": tmp_path / "index.db",
        "outside": tmp_path,
    }


@pytest.fixture()
def client(env):
    with TestClient(create_app()) as test_client:
        yield test_client


def _make_jpeg(directory: Path, name: str, size: tuple = (240, 160)) -> Path:
    """Write a real (small) JPEG so thumbnailing and stat() have something to chew on."""
    path = directory / name
    Image.new("RGB", size, (70, 110, 150)).save(path, "JPEG", quality=85)
    return path


_INSERT_SQL = """
    INSERT INTO photos
        (path, filename, size, mtime, date_taken, rating, iso, aperture_f, shutter_s,
         focal_mm, camera_model, camera_make, lens_model, label, dimensions, width,
         height, orientation, has_sidecar, root, present, in_final, published, indexed_at)
    VALUES
        (:path, :filename, :size, :mtime, :date_taken, :rating, :iso, :aperture_f, :shutter_s,
         :focal_mm, :camera_model, :camera_make, :lens_model, :label, :dimensions, :width,
         :height, :orientation, :has_sidecar, :root, :present, :in_final, :published, :indexed_at)
"""


def _seed(env: Dict[str, Path], **overrides: Any) -> Dict[str, Any]:
    """Insert one `photos` row, defaulting every column to a plausible Fuji frame."""
    root = overrides.pop("root", "final")
    filename = overrides.pop("filename", "photo.JPG")
    present = overrides.pop("present", 1)
    row: Dict[str, Any] = {
        "path": str(env[root] / filename),
        "filename": filename,
        "size": 8_000_000,
        "mtime": 1_760_000_000.0,
        "date_taken": "2026-01-10T09:00:00Z",
        "rating": 3,
        "iso": 400,
        "aperture_f": 2.8,
        "shutter_s": 0.004,
        "focal_mm": 35.0,
        "camera_model": "X-T4",
        "camera_make": "FUJIFILM",
        "lens_model": "XF16-55mmF2.8 R LM WR",
        "label": "",
        "dimensions": "6240x4160",
        "width": 6240,
        "height": 4160,
        "orientation": "landscape",
        "has_sidecar": 0,
        "root": root,
        "present": present,
        "in_final": 1 if (root == "final" and present) else 0,
        "published": 0,
        "indexed_at": "2026-01-10T10:00:00+00:00",
    }
    row.update(overrides)

    conn = get_db(env["db"])
    try:
        conn.execute(_INSERT_SQL, row)
        conn.commit()
    finally:
        conn.close()
    return row


@pytest.fixture()
def library(env) -> Dict[str, Any]:
    """
    Seed a small, deliberately varied library.

    f1  final   5★ Red   landscape  ISO 200   f/2.8  35mm  X-T4  sidecar
    f2  final   4★       portrait   ISO 800   f/4.0  56mm  X-T4
    f3  final   0★ Blue  landscape  ISO 3200  f/1.4  23mm  X-T3
    s1  staging 3★       square     ISO 640   f/2.0  16mm  X-T4  sidecar
    gone final  5★       landscape  — present=0, must never surface
    """
    _seed(env, filename="f1.JPG", rating=5, label="Red", iso=200, aperture_f=2.8,
          focal_mm=35.0, orientation="landscape", has_sidecar=1,
          date_taken="2026-01-10T09:00:00Z")
    _seed(env, filename="f2.JPG", rating=4, iso=800, aperture_f=4.0, focal_mm=56.0,
          orientation="portrait", date_taken="2026-02-11T09:00:00Z")
    _seed(env, filename="f3.JPG", rating=0, label="Blue", iso=3200, aperture_f=1.4,
          focal_mm=23.0, camera_model="X-T3", lens_model="XF23mmF1.4 R",
          orientation="landscape", date_taken="2026-03-12T09:00:00Z")
    _seed(env, root="staging", filename="s1.JPG", rating=3, iso=640, aperture_f=2.0,
          focal_mm=16.0, orientation="square", has_sidecar=1,
          date_taken="2026-04-13T09:00:00Z")
    _seed(env, filename="gone.JPG", rating=5, present=0, iso=100,
          date_taken="2026-05-14T09:00:00Z")
    return env


def _names(payload: Dict[str, Any]) -> set:
    return {item["filename"] for item in payload["items"]}


class _ExiftoolRecorder:
    """Stand-in for subprocess.run that records every exiftool invocation."""

    def __init__(self, returncode: int = 0, stdout: Optional[str] = None) -> None:
        self.calls: List[List[str]] = []
        self._returncode = returncode
        self._stdout = stdout

    def __call__(self, cmd, **kwargs):
        self.calls.append(list(cmd))
        file_count = sum(1 for arg in cmd if not arg.startswith("-") and arg != "exiftool")
        stdout = self._stdout
        if stdout is None:
            stdout = f"    {file_count} image files updated\n"
        return subprocess.CompletedProcess(
            args=list(cmd), returncode=self._returncode, stdout=stdout, stderr=""
        )


# ---------------------------------------------------------------------------
# GET /api/photos — filtering
# ---------------------------------------------------------------------------

class TestList:
    def test_returns_every_present_row(self, client, library):
        data = client.get("/api/photos").json()

        assert data["total"] == 4
        assert _names(data) == {"f1.JPG", "f2.JPG", "f3.JPG", "s1.JPG"}
        assert "gone.JPG" not in _names(data), "an absent file is history, not a photo"

    def test_row_shape(self, client, library):
        item = next(
            i for i in client.get("/api/photos").json()["items"] if i["filename"] == "f1.JPG"
        )

        assert item["root"] == "final"
        assert item["rating"] == 5
        assert item["label"] == "Red"
        assert item["orientation"] == "landscape"
        assert item["width"] == 6240 and item["height"] == 4160
        assert item["camera_model"] == "X-T4"
        assert item["lens_model"] == "XF16-55mmF2.8 R LM WR"
        assert item["has_sidecar"] is True
        assert item["size"] == 8_000_000

    def test_root_filter(self, client, library):
        assert _names(client.get("/api/photos", params={"root": "staging"}).json()) == {"s1.JPG"}
        assert _names(client.get("/api/photos", params={"root": "final"}).json()) == {
            "f1.JPG", "f2.JPG", "f3.JPG"
        }

    def test_rating_set_is_an_or(self, client, library):
        data = client.get("/api/photos", params=[("rating", 5), ("rating", 0)]).json()

        assert _names(data) == {"f1.JPG", "f3.JPG"}

    def test_rating_min(self, client, library):
        data = client.get("/api/photos", params={"rating_min": 4}).json()

        assert _names(data) == {"f1.JPG", "f2.JPG"}

    def test_orientation(self, client, library):
        assert _names(client.get("/api/photos", params={"orientation": "portrait"}).json()) == {
            "f2.JPG"
        }
        assert _names(client.get("/api/photos", params={"orientation": "square"}).json()) == {
            "s1.JPG"
        }

    def test_iso_range(self, client, library):
        data = client.get("/api/photos", params={"iso_min": 500, "iso_max": 1000}).json()

        assert _names(data) == {"f2.JPG", "s1.JPG"}

    def test_aperture_and_focal_ranges(self, client, library):
        assert _names(client.get("/api/photos", params={"aperture_max": 2.0}).json()) == {
            "f3.JPG", "s1.JPG"
        }
        assert _names(client.get("/api/photos", params={"focal_min": 35}).json()) == {
            "f1.JPG", "f2.JPG"
        }

    def test_camera_and_lens(self, client, library):
        assert _names(client.get("/api/photos", params={"camera_model": "X-T3"}).json()) == {
            "f3.JPG"
        }
        assert _names(
            client.get("/api/photos", params={"lens_model": "XF23mmF1.4 R"}).json()
        ) == {"f3.JPG"}

    def test_label_filter(self, client, library):
        assert _names(client.get("/api/photos", params={"label": "Red"}).json()) == {"f1.JPG"}

    def test_date_range(self, client, library):
        data = client.get(
            "/api/photos", params={"date_from": "2026-02-01", "date_to": "2026-03-31"}
        ).json()

        assert _names(data) == {"f2.JPG", "f3.JPG"}

    def test_bare_date_to_includes_the_whole_last_day(self, client, env):
        """`date_taken` is 'YYYY-MM-DDTHH:MM:SSZ' and compared as a string.

        A bare `date_to` of '2026-08-31' sorts *before* every photo taken on the 31st, so
        the last day of every range silently vanished from the listing while the date
        rail's own header still counted it. Verified on the live index: August 2025
        returned 2 of 14 photos.
        """
        _seed(env, filename="early.JPG", date_taken="2026-08-01T06:00:00Z")
        _seed(env, filename="midnight.JPG", date_taken="2026-08-31T00:00:00Z")
        _seed(env, filename="lastlight.JPG", date_taken="2026-08-31T21:47:03Z")
        _seed(env, filename="september.JPG", date_taken="2026-09-01T08:00:00Z")

        data = client.get(
            "/api/photos", params={"date_from": "2026-08-01", "date_to": "2026-08-31"}
        ).json()

        assert _names(data) == {"early.JPG", "midnight.JPG", "lastlight.JPG"}

    def test_explicit_date_to_timestamp_is_respected(self, client, env):
        """The rail now sends '…T23:59:59Z' itself — a value with a time must pass through."""
        _seed(env, filename="before.JPG", date_taken="2026-08-31T21:47:03Z")
        _seed(env, filename="after.JPG", date_taken="2026-09-01T08:00:00Z")

        data = client.get(
            "/api/photos",
            params={"date_from": "2026-08-01", "date_to": "2026-08-31T23:59:59Z"},
        ).json()

        assert _names(data) == {"before.JPG"}

    def test_filename_search(self, client, library):
        assert _names(client.get("/api/photos", params={"q": "s1"}).json()) == {"s1.JPG"}
        assert _names(client.get("/api/photos", params={"q": "f"}).json()) == {
            "f1.JPG", "f2.JPG", "f3.JPG"
        }

    def test_filename_search_treats_wildcards_literally(self, client, env):
        _seed(env, filename="a_b.JPG")
        _seed(env, filename="axb.JPG")

        data = client.get("/api/photos", params={"q": "a_b"}).json()

        assert _names(data) == {"a_b.JPG"}, "'_' must not act as a LIKE wildcard"

    def test_has_sidecar(self, client, library):
        assert _names(client.get("/api/photos", params={"has_sidecar": True}).json()) == {
            "f1.JPG", "s1.JPG"
        }
        assert _names(client.get("/api/photos", params={"has_sidecar": False}).json()) == {
            "f2.JPG", "f3.JPG"
        }

    def test_filters_compose(self, client, library):
        data = client.get(
            "/api/photos", params={"root": "final", "rating_min": 4, "orientation": "landscape"}
        ).json()

        assert _names(data) == {"f1.JPG"}

    def test_sort_and_order(self, client, library):
        ascending = client.get("/api/photos", params={"sort": "iso", "order": "asc"}).json()
        descending = client.get("/api/photos", params={"sort": "iso", "order": "desc"}).json()

        assert [i["iso"] for i in ascending["items"]] == [200, 640, 800, 3200]
        assert [i["iso"] for i in descending["items"]] == [3200, 800, 640, 200]

    def test_paging_keeps_the_unpaged_total(self, client, library):
        page = client.get(
            "/api/photos", params={"sort": "filename", "limit": 2, "offset": 2}
        ).json()

        assert page["total"] == 4
        assert page["limit"] == 2 and page["offset"] == 2
        assert len(page["items"]) == 2

    def test_unknown_sort_is_rejected(self, client, library):
        assert client.get("/api/photos", params={"sort": "size; DROP TABLE photos"}).status_code == 422

    def test_limit_bounds(self, client, library):
        assert client.get("/api/photos", params={"limit": 0}).status_code == 422
        assert client.get("/api/photos", params={"limit": 10001}).status_code == 422


# ---------------------------------------------------------------------------
# GET /api/photos/facets
# ---------------------------------------------------------------------------

class TestFacets:
    def test_unfiltered(self, client, library):
        data = client.get("/api/photos/facets").json()

        assert data["count"] == 4
        assert data["ratings"] == {"-1": 0, "0": 1, "1": 0, "2": 0, "3": 1, "4": 1, "5": 1}
        assert data["orientations"] == {"landscape": 2, "portrait": 1, "square": 1}
        assert {f["value"]: f["count"] for f in data["camera_models"]} == {"X-T4": 3, "X-T3": 1}
        assert {f["value"] for f in data["labels"]} == {"Red", "Blue"}
        assert data["iso"] == {"min": 200, "max": 3200}
        assert data["focal"] == {"min": 16.0, "max": 56.0}
        assert data["aperture"] == {"min": 1.4, "max": 4.0}

    def test_absent_rows_are_excluded(self, client, library):
        """gone.JPG has ISO 100 and 5★ — neither may leak into a facet."""
        data = client.get("/api/photos/facets").json()

        assert data["iso"]["min"] == 200
        assert data["ratings"]["5"] == 1

    def test_rating_facet_ignores_the_rating_filter(self, client, library):
        data = client.get("/api/photos/facets", params={"rating": 5}).json()

        assert data["count"] == 1, "the headline count DOES apply every filter"
        assert data["ratings"] == {"-1": 0, "0": 1, "1": 0, "2": 0, "3": 1, "4": 1, "5": 1}, (
            "the rating facet must stay open, or the chips collapse to the one you picked"
        )

    def test_other_facets_narrow_under_a_rating_filter(self, client, library):
        data = client.get("/api/photos/facets", params={"rating": 5}).json()

        assert {f["value"] for f in data["camera_models"]} == {"X-T4"}
        assert data["orientations"] == {"landscape": 1, "portrait": 0, "square": 0}
        assert data["iso"] == {"min": 200, "max": 200}

    def test_orientation_facet_ignores_the_orientation_filter(self, client, library):
        data = client.get("/api/photos/facets", params={"orientation": "landscape"}).json()

        assert data["count"] == 2
        assert data["orientations"] == {"landscape": 2, "portrait": 1, "square": 1}
        assert data["ratings"]["4"] == 0, "the rating facet DOES narrow under an orientation filter"

    def test_range_facet_ignores_its_own_bounds(self, client, library):
        data = client.get("/api/photos/facets", params={"iso_min": 700, "iso_max": 900}).json()

        assert data["count"] == 1
        assert data["iso"] == {"min": 200, "max": 3200}, "the ISO slider must keep its full track"
        assert data["focal"] == {"min": 56.0, "max": 56.0}

    def test_camera_facet_ignores_the_camera_filter(self, client, library):
        data = client.get("/api/photos/facets", params={"camera_model": "X-T3"}).json()

        assert data["count"] == 1
        assert {f["value"]: f["count"] for f in data["camera_models"]} == {"X-T4": 3, "X-T3": 1}
        assert {f["value"] for f in data["lens_models"]} == {"XF23mmF1.4 R"}

    def test_empty_library(self, client, env):
        data = client.get("/api/photos/facets").json()

        assert data["count"] == 0
        assert data["labels"] == []
        assert data["iso"] == {"min": None, "max": None}


# ---------------------------------------------------------------------------
# GET /api/photos/facets — the range-slider histograms
# ---------------------------------------------------------------------------

def _bins(client, dimension: str, **params) -> List[Dict[str, Any]]:
    return client.get("/api/photos/facets", params=params).json()["histograms"][dimension]


def _ratios(bins: List[Dict[str, Any]]) -> List[float]:
    return [b["hi"] / b["lo"] for b in bins]


def _widths(bins: List[Dict[str, Any]]) -> List[float]:
    return [b["hi"] - b["lo"] for b in bins]


class TestFacetHistograms:
    def test_every_range_dimension_is_bucketed(self, client, library):
        histograms = client.get("/api/photos/facets").json()["histograms"]

        assert set(histograms) == {"iso", "aperture", "shutter", "focal"}
        for dimension in ("iso", "aperture", "focal"):
            assert len(histograms[dimension]) == 24, dimension

    def test_buckets_span_the_facet_range_without_gaps(self, client, library):
        data = client.get("/api/photos/facets").json()
        bins = data["histograms"]["iso"]

        assert bins[0]["lo"] == data["iso"]["min"] == 200
        assert bins[-1]["hi"] == data["iso"]["max"] == 3200
        for lower, upper in zip(bins, bins[1:]):
            assert lower["hi"] == upper["lo"], "a gap between buckets loses rows"

    def test_counts_add_up_to_the_selection(self, client, library):
        for dimension in ("iso", "aperture", "shutter", "focal"):
            bins = _bins(client, dimension)
            assert sum(b["count"] for b in bins) == 4, dimension

    def test_iso_is_log_spaced(self, client, library):
        """
        ISO 160..12800 is 6.3 stops and this library parks 60 % of its frames on one
        value. Linear buckets would put nearly everything in bucket 0 and leave a third
        of the track empty — the histogram would show nothing at all.
        """
        ratios = _ratios(_bins(client, "iso"))

        assert all(r == pytest.approx(ratios[0], rel=1e-4) for r in ratios)
        assert len(set(_widths(_bins(client, "iso")))) > 1, "log buckets widen, linear don't"

    def test_focal_is_log_spaced(self, client, library):
        """Two lenses spanning 16-300 mm: linear buries half the library in bucket 0."""
        ratios = _ratios(_bins(client, "focal"))

        assert all(r == pytest.approx(ratios[0], rel=1e-4) for r in ratios)

    def test_aperture_is_linear(self, client, library):
        widths = _widths(_bins(client, "aperture"))

        assert all(w == pytest.approx(widths[0], rel=1e-4) for w in widths)

    def test_the_extremes_land_inside_the_track(self, client, library):
        """Float rounding must not push the max value into a 25th bucket."""
        iso = _bins(client, "iso")

        assert iso[0]["count"] >= 1, "the minimum value belongs in the first bucket"
        assert iso[-1]["count"] >= 1, "the maximum value belongs in the last bucket"

    def test_a_single_distinct_value_collapses_to_one_bucket(self, client, library):
        """Every fixture shares one shutter speed — 24 buckets of zero would be a lie."""
        bins = _bins(client, "shutter")

        assert bins == [{"lo": 0.004, "hi": 0.004, "count": 4}]

    def test_histogram_ignores_its_own_filter(self, client, library):
        bins = _bins(client, "iso", iso_min=700, iso_max=900)

        assert bins[0]["lo"] == 200 and bins[-1]["hi"] == 3200, "the track must not shrink"
        assert sum(b["count"] for b in bins) == 4, (
            "the distribution behind a slider shows the library, not the selected slice"
        )

    def test_histogram_narrows_under_another_filter(self, client, library):
        bins = _bins(client, "iso", camera_model="X-T3")

        assert sum(b["count"] for b in bins) == 1
        assert bins[0]["lo"] == 3200 and bins[0]["hi"] == 3200

    def test_absent_rows_are_excluded(self, client, library):
        """gone.JPG is ISO 100 — it must not stretch the track down."""
        assert _bins(client, "iso")[0]["lo"] == 200

    def test_rows_without_a_value_are_skipped(self, client, env):
        _seed(env, filename="a.JPG", iso=400)
        _seed(env, filename="b.JPG", iso=None)

        bins = _bins(client, "iso")

        assert sum(b["count"] for b in bins) == 1

    def test_empty_library(self, client, env):
        histograms = client.get("/api/photos/facets").json()["histograms"]

        assert histograms == {"iso": [], "aperture": [], "shutter": [], "focal": []}

    def test_log_buckets_survive_a_sqlite_without_math_functions(
        self, client, library, monkeypatch
    ):
        """
        ``ln()`` is a SQLite compile-time option. Where it is absent the router falls
        back to a Python UDF — a branch that never runs on this machine, and would
        therefore ship broken without being pinned here.
        """
        native = _bins(client, "iso")
        monkeypatch.setattr(photos_mod, "_LN_FUNCTION", "pf_ln")

        assert _bins(client, "iso") == native

    def test_a_nonpositive_value_cannot_break_a_log_dimension(self, client, env):
        """A 0 s exposure is junk data; log() of it must not take the histogram out."""
        _seed(env, filename="ok.JPG", shutter_s=0.002)
        _seed(env, filename="junk.JPG", shutter_s=0.0)
        _seed(env, filename="fast.JPG", shutter_s=0.5)

        bins = _bins(client, "shutter")

        assert len(bins) == 24
        assert bins[0]["lo"] == 0.002
        assert sum(b["count"] for b in bins) == 2


# ---------------------------------------------------------------------------
# Path safety — the guard that keeps a localhost service from serving ~/.ssh
# ---------------------------------------------------------------------------

class TestPathSafety:
    @pytest.mark.parametrize("attack", [
        "../../.ssh/id_rsa",
        "../../../etc/passwd",
        "/etc/passwd",
        "~/.ssh/id_ed25519",
        "",
    ])
    def test_meta_rejects_out_of_root_paths(self, client, library, attack):
        assert client.get("/api/photos/meta", params={"path": attack}).status_code == 400

    @pytest.mark.parametrize("attack", ["../../.ssh/id_rsa", "/etc/passwd"])
    def test_thumb_rejects_out_of_root_paths(self, client, library, attack):
        assert client.get(
            "/api/photos/thumb", params={"path": attack, "tier": "grid"}
        ).status_code == 400

    def test_traversal_back_out_of_a_root_is_rejected(self, client, env):
        secret = env["outside"] / "secret.JPG"
        _make_jpeg(env["outside"], "secret.JPG")
        traversal = str(env["final"] / ".." / "secret.JPG")

        resp = client.get("/api/photos/thumb", params={"path": traversal, "tier": "grid"})

        assert resp.status_code == 400
        assert secret.is_file()

    def test_absolute_path_outside_the_roots_is_rejected(self, client, env):
        outsider = _make_jpeg(env["outside"], "elsewhere.JPG")

        resp = client.get("/api/photos/thumb", params={"path": str(outsider), "tier": "grid"})

        assert resp.status_code == 400

    def test_the_root_directory_itself_is_not_addressable(self, client, env):
        resp = client.get("/api/photos/meta", params={"path": str(env["final"])})

        assert resp.status_code == 400

    def test_write_endpoints_reject_out_of_root_paths(self, client, env, monkeypatch):
        recorder = _ExiftoolRecorder()
        monkeypatch.setattr(photos_mod.subprocess, "run", recorder)
        outsider = _make_jpeg(env["outside"], "victim.JPG")

        rating = client.post(
            "/api/photos/rating", json={"paths": [str(outsider)], "rating": 5}
        )
        label = client.post("/api/photos/label", json={"paths": [str(outsider)], "label": "Red"})
        trashed = client.post("/api/photos/trash", json={"paths": [str(outsider)]})
        warmed = client.post("/api/photos/warm", json={"paths": [str(outsider)], "tier": "grid"})

        assert [r.status_code for r in (rating, label, trashed, warmed)] == [400, 400, 400, 400]
        assert recorder.calls == [], "exiftool must not run on a rejected batch"
        assert outsider.is_file()

    def test_one_bad_path_rejects_the_whole_batch(self, client, env, monkeypatch):
        recorder = _ExiftoolRecorder()
        monkeypatch.setattr(photos_mod.subprocess, "run", recorder)
        good = _make_jpeg(env["final"], "good.JPG")
        bad = _make_jpeg(env["outside"], "bad.JPG")

        resp = client.post(
            "/api/photos/rating", json={"paths": [str(good), str(bad)], "rating": 3}
        )

        assert resp.status_code == 400
        assert recorder.calls == []

    def test_empty_batch_is_rejected(self, client, env):
        assert client.post("/api/photos/rating", json={"paths": [], "rating": 3}).status_code == 400

    def test_oversized_batch_is_rejected(self, client, env):
        paths = [str(env["final"] / f"p{i}.JPG") for i in range(photos_mod.MAX_WRITE_PATHS + 1)]

        resp = client.post("/api/photos/rating", json={"paths": paths, "rating": 3})

        assert resp.status_code == 400
        assert "Too many paths" in resp.json()["detail"]

    def test_warm_batch_cap(self, client, env):
        paths = [str(env["final"] / f"p{i}.JPG") for i in range(photos_mod.MAX_WARM_PATHS + 1)]

        resp = client.post("/api/photos/warm", json={"paths": paths, "tier": "grid"})

        assert resp.status_code == 400


# ---------------------------------------------------------------------------
# GET /api/photos/thumb
# ---------------------------------------------------------------------------

class TestThumb:
    def test_serves_an_immutable_jpeg(self, client, env):
        photo = _make_jpeg(env["final"], "t.JPG", size=(900, 600))

        resp = client.get("/api/photos/thumb", params={"path": str(photo), "tier": "grid"})

        assert resp.status_code == 200
        assert resp.headers["content-type"] == "image/jpeg"
        assert resp.headers["cache-control"] == "public, max-age=31536000, immutable"
        assert resp.headers["etag"].startswith('"')
        assert resp.content[:2] == b"\xff\xd8", "not a JPEG payload"

    def test_etag_matches_the_cache_key(self, client, env):
        photo = _make_jpeg(env["final"], "t.JPG")

        resp = client.get("/api/photos/thumb", params={"path": str(photo), "tier": "view"})

        assert resp.headers["etag"] == f'"{thumbs_mod.cache_key(photo, "view")}"'

    def test_conditional_request_returns_304(self, client, env):
        photo = _make_jpeg(env["final"], "t.JPG")
        first = client.get("/api/photos/thumb", params={"path": str(photo), "tier": "grid"})

        second = client.get(
            "/api/photos/thumb",
            params={"path": str(photo), "tier": "grid"},
            headers={"If-None-Match": first.headers["etag"]},
        )

        assert second.status_code == 304
        assert second.content == b""
        assert second.headers["cache-control"] == "public, max-age=31536000, immutable"

    def test_stale_etag_still_serves_the_image(self, client, env):
        photo = _make_jpeg(env["final"], "t.JPG")

        resp = client.get(
            "/api/photos/thumb",
            params={"path": str(photo), "tier": "grid"},
            headers={"If-None-Match": '"an-old-key"'},
        )

        assert resp.status_code == 200

    def test_cache_buster_is_ignored_server_side(self, client, env):
        photo = _make_jpeg(env["final"], "t.JPG")

        a = client.get("/api/photos/thumb", params={"path": str(photo), "tier": "grid", "v": "1"})
        b = client.get("/api/photos/thumb", params={"path": str(photo), "tier": "grid", "v": "2"})

        assert a.headers["etag"] == b.headers["etag"]

    def test_missing_source_is_404(self, client, env):
        resp = client.get(
            "/api/photos/thumb", params={"path": str(env["final"] / "ghost.JPG"), "tier": "grid"}
        )

        assert resp.status_code == 404

    def test_unknown_tier_is_rejected(self, client, env):
        photo = _make_jpeg(env["final"], "t.JPG")

        resp = client.get("/api/photos/thumb", params={"path": str(photo), "tier": "huge"})

        assert resp.status_code == 422

    def test_staging_photo_is_servable(self, client, env):
        photo = _make_jpeg(env["staging"], "s.JPG")

        assert client.get(
            "/api/photos/thumb", params={"path": str(photo), "tier": "grid"}
        ).status_code == 200


# ---------------------------------------------------------------------------
# GET /api/photos/meta
# ---------------------------------------------------------------------------

class TestMeta:
    def test_row_plus_live_sizes(self, client, env, monkeypatch):
        photo = _make_jpeg(env["final"], "m.JPG")
        sidecar = photo.with_suffix(config.EDIT_SIDECAR_SUFFIX)
        sidecar.write_bytes(b"edit-history")
        _seed(env, filename="m.JPG", has_sidecar=1, rating=4)
        monkeypatch.setattr(photos_mod, "_read_exif_extra", lambda p: {"EXIF:Make": "FUJIFILM"})

        data = client.get("/api/photos/meta", params={"path": str(photo)}).json()

        assert data["filename"] == "m.JPG"
        assert data["rating"] == 4
        assert data["file_size"] == photo.stat().st_size
        assert data["sidecar_size"] == len(b"edit-history")
        assert data["exif_extra"] == {"EXIF:Make": "FUJIFILM"}

    def test_no_sidecar_reports_null(self, client, env, monkeypatch):
        photo = _make_jpeg(env["final"], "m.JPG")
        _seed(env, filename="m.JPG")
        monkeypatch.setattr(photos_mod, "_read_exif_extra", lambda p: {})

        data = client.get("/api/photos/meta", params={"path": str(photo)}).json()

        assert data["sidecar_size"] is None

    def test_unindexed_photo_is_404(self, client, env, monkeypatch):
        photo = _make_jpeg(env["final"], "stranger.JPG")
        monkeypatch.setattr(photos_mod, "_read_exif_extra", lambda p: {})

        assert client.get(
            "/api/photos/meta", params={"path": str(photo)}
        ).status_code == 404

    def test_exif_read_failure_is_not_a_500(self, client, env, monkeypatch):
        photo = _make_jpeg(env["final"], "m.JPG")
        _seed(env, filename="m.JPG")

        def boom(cmd, **kwargs):
            raise OSError("exiftool exploded")

        monkeypatch.setattr(photos_mod.subprocess, "run", boom)

        data = client.get("/api/photos/meta", params={"path": str(photo)}).json()

        assert data["exif_extra"] == {}


# ---------------------------------------------------------------------------
# POST /api/photos/rating and /label — the exiftool batching contract
# ---------------------------------------------------------------------------

class TestWriteBack:
    def test_one_exiftool_process_for_the_whole_batch(self, client, env, monkeypatch):
        recorder = _ExiftoolRecorder()
        monkeypatch.setattr(photos_mod.subprocess, "run", recorder)
        photos = [_make_jpeg(env["final"], f"r{i}.JPG") for i in range(3)]

        resp = client.post(
            "/api/photos/rating", json={"paths": [str(p) for p in photos], "rating": 4}
        )

        assert resp.status_code == 200
        assert resp.json()["updated"] == 3
        assert len(recorder.calls) == 1, "one process per batch, not one per file"

        cmd = recorder.calls[0]
        assert cmd[0] == "exiftool"
        assert "-overwrite_original" in cmd
        assert "-XMP-xmp:Rating=4" in cmd
        assert [str(p) for p in photos] == cmd[-3:]

    def test_rating_zero_clears_the_tag(self, client, env, monkeypatch):
        recorder = _ExiftoolRecorder()
        monkeypatch.setattr(photos_mod.subprocess, "run", recorder)
        photo = _make_jpeg(env["final"], "clear.JPG")

        client.post("/api/photos/rating", json={"paths": [str(photo)], "rating": 0})

        assert "-XMP-xmp:Rating=" in recorder.calls[0]
        assert "-XMP-xmp:Rating=0" not in recorder.calls[0]

    def test_rating_is_bounds_checked(self, client, env):
        photo = str(env["final"] / "x.JPG")

        assert client.post("/api/photos/rating", json={"paths": [photo], "rating": 6}).status_code == 422
        # -1 is the reject value and IS accepted; -2 is the first thing below the floor.
        assert client.post("/api/photos/rating", json={"paths": [photo], "rating": -2}).status_code == 422

    def test_label_write(self, client, env, monkeypatch):
        recorder = _ExiftoolRecorder()
        monkeypatch.setattr(photos_mod.subprocess, "run", recorder)
        photos = [_make_jpeg(env["final"], f"l{i}.JPG") for i in range(2)]

        resp = client.post(
            "/api/photos/label", json={"paths": [str(p) for p in photos], "label": "Red"}
        )

        assert resp.json()["updated"] == 2
        assert len(recorder.calls) == 1
        assert "-XMP-xmp:Label=Red" in recorder.calls[0]

    def test_empty_label_clears(self, client, env, monkeypatch):
        recorder = _ExiftoolRecorder()
        monkeypatch.setattr(photos_mod.subprocess, "run", recorder)
        photo = _make_jpeg(env["final"], "l.JPG")

        client.post("/api/photos/label", json={"paths": [str(photo)], "label": ""})

        assert "-XMP-xmp:Label=" in recorder.calls[0]

    def test_index_is_refreshed_after_a_successful_write(self, client, env, monkeypatch):
        """The UI refetches straight after the write, so the row has to be current."""
        recorder = _ExiftoolRecorder()
        monkeypatch.setattr(photos_mod.subprocess, "run", recorder)
        photo = _make_jpeg(env["final"], "indexed.JPG", size=(320, 200))

        client.post("/api/photos/rating", json={"paths": [str(photo)], "rating": 2})

        data = client.get("/api/photos", params={"q": "indexed"}).json()
        assert data["total"] == 1
        assert data["items"][0]["orientation"] == "landscape"
        assert data["items"][0]["root"] == "final"

    def test_exiftool_failure_is_reported_not_swallowed(self, client, env, monkeypatch):
        recorder = _ExiftoolRecorder(returncode=1, stdout="")
        monkeypatch.setattr(photos_mod.subprocess, "run", recorder)
        photo = _make_jpeg(env["final"], "fail.JPG")

        result = client.post(
            "/api/photos/rating", json={"paths": [str(photo)], "rating": 5}
        ).json()

        assert result["updated"] == 0
        assert result["errors"] == 1
        assert result["messages"]

    def test_missing_exiftool_is_reported(self, client, env, monkeypatch):
        def missing(cmd, **kwargs):
            raise FileNotFoundError("exiftool")

        monkeypatch.setattr(photos_mod.subprocess, "run", missing)
        photo = _make_jpeg(env["final"], "noexif.JPG")

        result = client.post(
            "/api/photos/rating", json={"paths": [str(photo)], "rating": 5}
        ).json()

        assert result["updated"] == 0
        assert result["errors"] == 1
        assert "exiftool not found" in result["messages"][0]

    def test_timeout_is_reported(self, client, env, monkeypatch):
        def slow(cmd, **kwargs):
            raise subprocess.TimeoutExpired(cmd, 60)

        monkeypatch.setattr(photos_mod.subprocess, "run", slow)
        photo = _make_jpeg(env["final"], "slow.JPG")

        result = client.post(
            "/api/photos/rating", json={"paths": [str(photo)], "rating": 5}
        ).json()

        assert result["errors"] == 1
        assert "timed out" in result["messages"][0]


# ---------------------------------------------------------------------------
# Trash — the soft-delete round trip
# ---------------------------------------------------------------------------

class TestRejects:
    """
    The three-state cull model: unrated (0/absent), rated (1–5) and rejected (-1).

    The invariant worth defending here is that rejecting is *only* a metadata write.
    Nothing moves until the batch purge is called explicitly, so a mis-keyed reject
    costs one keypress to undo rather than a trip through the trash.
    """

    def test_reject_is_written_literally_not_cleared(self, client, env, monkeypatch):
        recorder = _ExiftoolRecorder()
        monkeypatch.setattr(photos_mod.subprocess, "run", recorder)
        photo = _make_jpeg(env["final"], "r.JPG")

        resp = client.post("/api/photos/rating", json={"paths": [str(photo)], "rating": -1})

        assert resp.json()["updated"] == 1
        assert "-XMP-xmp:Rating=-1" in recorder.calls[0], (
            "-1 must reach the file — clearing it would collapse reject into unrated"
        )

    def test_rejects_are_hidden_by_default(self, client, env):
        _seed(env, filename="keep.JPG", rating=4)
        _seed(env, filename="nope.JPG", rating=-1)

        assert _names(client.get("/api/photos").json()) == {"keep.JPG"}

    def test_include_rejected_brings_them_back(self, client, env):
        _seed(env, filename="keep.JPG", rating=4)
        _seed(env, filename="nope.JPG", rating=-1)

        data = client.get("/api/photos", params={"include_rejected": "true"}).json()

        assert _names(data) == {"keep.JPG", "nope.JPG"}

    def test_asking_for_rejects_explicitly_wins_over_the_default(self, client, env):
        """`rating=-1` is a deliberate request; the hide-by-default must not veto it."""
        _seed(env, filename="keep.JPG", rating=4)
        _seed(env, filename="nope.JPG", rating=-1)

        assert _names(client.get("/api/photos", params={"rating": -1}).json()) == {"nope.JPG"}

    def test_a_reject_moves_nothing(self, client, env, monkeypatch):
        monkeypatch.setattr(photos_mod.subprocess, "run", _ExiftoolRecorder())
        photo = _make_jpeg(env["final"], "still-here.JPG")

        client.post("/api/photos/rating", json={"paths": [str(photo)], "rating": -1})

        assert photo.exists(), "rejecting is a judgement, not a file operation"

    def test_reject_facet_counts_them(self, client, env):
        _seed(env, filename="a.JPG", rating=-1)
        _seed(env, filename="b.JPG", rating=-1)
        _seed(env, filename="c.JPG", rating=5)

        data = client.get("/api/photos/facets").json()

        assert data["ratings"]["-1"] == 2
        assert data["count"] == 1, "the headline count still excludes rejects"

    def test_purge_trashes_every_reject(self, client, env):
        keep = _make_jpeg(env["final"], "keep.JPG")
        gone = _make_jpeg(env["final"], "gone.JPG")
        _seed(env, filename="keep.JPG", rating=4)
        _seed(env, filename="gone.JPG", rating=-1)

        body = client.post("/api/photos/rejects/purge").json()

        assert body["trashed"] == 1
        assert body["errors"] == 0
        assert keep.exists()
        assert not gone.exists()

    def test_purge_dry_run_moves_nothing(self, client, env):
        photo = _make_jpeg(env["final"], "gone.JPG")
        _seed(env, filename="gone.JPG", rating=-1)

        client.post("/api/photos/rejects/purge", params={"dry_run": "true"})

        assert photo.exists()

    def test_purge_is_scoped_by_root(self, client, env):
        final = _make_jpeg(env["final"], "f.JPG")
        staging = _make_jpeg(env["staging"], "s.JPG")
        _seed(env, filename="f.JPG", rating=-1)
        _seed(env, root="staging", filename="s.JPG", rating=-1)

        client.post("/api/photos/rejects/purge", params={"root": "staging"})

        assert final.exists()
        assert not staging.exists()

    def test_purge_skips_rows_whose_file_is_gone(self, client, env):
        """A trash that outran its reindex must not fail the next purge."""
        _seed(env, filename="vanished.JPG", rating=-1)

        body = client.post("/api/photos/rejects/purge").json()

        assert body["trashed"] == 0
        assert body["errors"] == 0

    def test_reject_count_endpoint(self, client, env):
        _make_jpeg(env["final"], "a.JPG")
        _make_jpeg(env["final"], "b.JPG")
        _seed(env, filename="a.JPG", rating=-1)
        _seed(env, filename="b.JPG", rating=-1)
        _seed(env, filename="c.JPG", rating=5)

        assert client.get("/api/photos/rejects").json()["count"] == 2


class TestTrashEndpoints:
    def _trash_one(self, client, env, name: str = "cull.JPG", sidecar: bool = True):
        photo = _make_jpeg(env["final"], name)
        if sidecar:
            photo.with_suffix(config.EDIT_SIDECAR_SUFFIX).write_bytes(b"edit-history")
        _seed(env, filename=name, rating=1)
        resp = client.post("/api/photos/trash", json={"paths": [str(photo)]})
        return photo, resp

    def test_trash_moves_the_jpg_and_its_sidecar(self, client, env):
        photo, resp = self._trash_one(client, env)
        body = resp.json()

        assert resp.status_code == 200
        assert body["trashed"] == 1
        assert body["errors"] == 0
        assert body["entries"][0]["filename"] == "cull.JPG"
        assert body["entries"][0]["rating"] == 1
        assert not photo.exists()
        assert not photo.with_suffix(config.EDIT_SIDECAR_SUFFIX).exists()

    def test_trashed_photo_leaves_the_listing(self, client, env):
        self._trash_one(client, env, name="bye.JPG")

        assert client.get("/api/photos", params={"q": "bye"}).json()["total"] == 0

    def test_dry_run_moves_nothing(self, client, env):
        photo = _make_jpeg(env["final"], "safe.JPG")
        _seed(env, filename="safe.JPG")

        body = client.post(
            "/api/photos/trash", json={"paths": [str(photo)], "dry_run": True}
        ).json()

        assert body["trashed"] == 0
        assert len(body["entries"]) == 1
        assert photo.is_file()
        assert client.get("/api/photos/trash").json()["stats"]["count"] == 0

    def test_listing_reports_entries_and_stats(self, client, env):
        self._trash_one(client, env, name="listed.JPG")

        body = client.get("/api/photos/trash").json()

        assert body["stats"]["count"] == 1
        assert body["stats"]["bytes"] > 0
        assert body["stats"]["purgeable"] == 0, "a fresh entry is never purgeable"
        entry = body["entries"][0]
        assert entry["filename"] == "listed.JPG"
        assert entry["root"] == "final"
        assert entry["exists"] is True
        assert entry["age_days"] < 1

    def test_restore_round_trip(self, client, env):
        photo, resp = self._trash_one(client, env, name="undo.JPG")
        entry_id = resp.json()["entries"][0]["id"]

        restored = client.post("/api/photos/trash/restore", json={"ids": [entry_id]}).json()

        assert restored["restored"] == 1
        assert restored["errors"] == 0
        assert photo.is_file()
        assert photo.with_suffix(config.EDIT_SIDECAR_SUFFIX).read_bytes() == b"edit-history"
        assert client.get("/api/photos/trash").json()["stats"]["count"] == 0

    def test_restore_refuses_an_occupied_original(self, client, env):
        photo, resp = self._trash_one(client, env, name="taken.JPG", sidecar=False)
        entry_id = resp.json()["entries"][0]["id"]
        _make_jpeg(env["final"], "taken.JPG", size=(64, 64))
        squatter_bytes = photo.read_bytes()

        restored = client.post("/api/photos/trash/restore", json={"ids": [entry_id]}).json()

        assert restored["restored"] == 0
        assert restored["errors"] == 1
        assert photo.read_bytes() == squatter_bytes, "the squatter must not be overwritten"

    def test_restore_without_ids_is_rejected(self, client, env):
        assert client.post("/api/photos/trash/restore", json={"ids": []}).status_code == 400

    def test_trashed_file_is_still_thumbnailable(self, client, env):
        """The trash drawer has to show what it is offering to restore."""
        _, resp = self._trash_one(client, env, name="preview.JPG", sidecar=False)
        entry = client.get("/api/photos/trash").json()["entries"][0]

        thumb = client.get(
            "/api/photos/thumb", params={"path": entry["trashed_path"], "tier": "grid"}
        )

        assert thumb.status_code == 200
        assert thumb.headers["content-type"] == "image/jpeg"

    def test_purge_spares_a_fresh_entry(self, client, env):
        self._trash_one(client, env, name="young.JPG")

        result = client.post("/api/photos/trash/purge", json={}).json()

        assert result["purged"] == 0
        assert client.get("/api/photos/trash").json()["stats"]["count"] == 1

    def test_purge_dry_run_deletes_nothing(self, client, env):
        self._trash_one(client, env, name="dry.JPG")

        result = client.post(
            "/api/photos/trash/purge", json={"days": 0, "dry_run": True}
        ).json()

        assert result["purged"] == 1
        assert client.get("/api/photos/trash").json()["stats"]["count"] == 1

    def test_purge_rejects_a_negative_window(self, client, env):
        assert client.post(
            "/api/photos/trash/purge", json={"days": -1}
        ).status_code == 400


# ---------------------------------------------------------------------------
# GET /api/photos?include_trashed=true — the cull's undo affordance
# ---------------------------------------------------------------------------

class TestIncludeTrashed:
    def _cull(self, client, env, name: str, indexed: bool = True, **seed: Any) -> int:
        """Trash one photo and return its trash id. `indexed=False` skips the index row."""
        photo = _make_jpeg(env["final"], name)
        if indexed:
            _seed(env, filename=name, **seed)
        resp = client.post("/api/photos/trash", json={"paths": [str(photo)]})
        return resp.json()["entries"][0]["id"]

    def _listing(self, client, **params) -> Dict[str, Any]:
        return client.get("/api/photos", params=params).json()

    def test_off_by_default(self, client, env):
        self._cull(client, env, "bye.JPG")

        assert self._listing(client)["total"] == 0

    def test_brings_the_row_back_with_its_restore_id(self, client, env):
        trash_id = self._cull(client, env, "undo.JPG", rating=4)

        body = self._listing(client, include_trashed=True)

        assert body["total"] == 1
        item = body["items"][0]
        assert item["trashed"] is True
        assert item["trash_id"] == trash_id
        assert item["filename"] == "undo.JPG"
        assert item["rating"] == 4, "the index row's metadata survives the move"

    def test_the_listed_path_is_the_file_that_still_exists(self, client, env):
        """`path` must address the trash copy — the original is gone, and the viewer
        has to be able to render what it is offering to restore."""
        self._cull(client, env, "preview.JPG")
        entry = client.get("/api/photos/trash").json()["entries"][0]

        item = self._listing(client, include_trashed=True)["items"][0]

        assert item["path"] == entry["trashed_path"]
        assert Path(item["path"]).is_file()
        thumb = client.get("/api/photos/thumb", params={"path": item["path"]})
        assert thumb.status_code == 200

    def test_live_rows_stay_marked_untrashed(self, client, library):
        body = self._listing(client, include_trashed=True)

        assert body["total"] == 4
        assert all(item["trashed"] is False for item in body["items"])
        assert all(item["trash_id"] is None for item in body["items"])

    def test_lands_in_its_natural_sort_position(self, client, env):
        """A separate drawer would be a different feature; this one is an inline undo."""
        for name, when in (("a.JPG", "2026-01-01"), ("c.JPG", "2026-03-01")):
            _make_jpeg(env["final"], name)
            _seed(env, filename=name, date_taken=f"{when}T09:00:00Z")
        self._cull(client, env, "b.JPG", date_taken="2026-02-01T09:00:00Z")

        body = self._listing(client, include_trashed=True, sort="date_taken", order="asc")

        assert [i["filename"] for i in body["items"]] == ["a.JPG", "b.JPG", "c.JPG"]
        assert [i["trashed"] for i in body["items"]] == [False, True, False]

    def test_paging_totals_include_trashed_rows(self, client, library):
        self._cull(client, library, "extra.JPG")

        assert self._listing(client)["total"] == 4
        assert self._listing(client, include_trashed=True)["total"] == 5

    def test_filters_apply_to_a_trashed_row(self, client, env):
        self._cull(client, env, "keeper.JPG", rating=5, iso=200)
        self._cull(client, env, "reject.JPG", rating=1, iso=6400)

        matched = self._listing(client, include_trashed=True, rating_min=4)
        by_iso = self._listing(client, include_trashed=True, iso_min=1000)

        assert _names(matched) == {"keeper.JPG"}
        assert _names(by_iso) == {"reject.JPG"}

    def test_root_filter_applies_to_a_trashed_row(self, client, env):
        photo = _make_jpeg(env["staging"], "s.JPG")
        _seed(env, root="staging", filename="s.JPG")
        client.post("/api/photos/trash", json={"paths": [str(photo)]})

        assert self._listing(client, include_trashed=True, root="staging")["total"] == 1
        assert self._listing(client, include_trashed=True, root="final")["total"] == 0

    def test_an_unindexed_trash_row_is_dropped_by_a_filter_it_cannot_answer(
        self, client, env
    ):
        """
        A photo trashed with no index row (or an orphan adopted by the sweep) carries no
        EXIF. Parading it through an ISO-filtered view would be a claim we cannot back;
        the NULL fails the comparison and it drops out instead.
        """
        self._cull(client, env, "mystery.JPG", indexed=False)

        assert self._listing(client, include_trashed=True)["total"] == 1
        assert self._listing(client, include_trashed=True, iso_min=1)["total"] == 0
        assert self._listing(client, include_trashed=True, date_from="1970-01-01")["total"] == 0

    def test_a_restored_row_is_a_live_row_again(self, client, env):
        trash_id = self._cull(client, env, "back.JPG")
        client.post("/api/photos/trash/restore", json={"ids": [trash_id]})

        body = self._listing(client, include_trashed=True)

        assert body["total"] == 1
        assert body["items"][0]["trashed"] is False


# ---------------------------------------------------------------------------
# POST /api/photos/warm
# ---------------------------------------------------------------------------

class TestWarm:
    def test_generates_the_batch(self, client, env):
        photos = [_make_jpeg(env["final"], f"w{i}.JPG", size=(600, 400)) for i in range(3)]
        paths = [str(p) for p in photos]

        first = client.post("/api/photos/warm", json={"paths": paths, "tier": "grid"}).json()
        second = client.post("/api/photos/warm", json={"paths": paths, "tier": "grid"}).json()

        assert first["generated"] == 3
        assert second["generated"] == 0, "a warm cache must not be regenerated"
        assert all(thumbs_mod.thumb_path(p, "grid").is_file() for p in photos)

    def test_view_tier(self, client, env):
        photo = _make_jpeg(env["final"], "v.JPG", size=(600, 400))

        result = client.post(
            "/api/photos/warm", json={"paths": [str(photo)], "tier": "view"}
        ).json()

        assert result["generated"] == 1
        assert thumbs_mod.thumb_path(photo, "view").is_file()

    def test_unknown_tier_is_rejected(self, client, env):
        photo = _make_jpeg(env["final"], "v.JPG")

        assert client.post(
            "/api/photos/warm", json={"paths": [str(photo)], "tier": "enormous"}
        ).status_code == 422

    def test_missing_sources_are_not_fatal(self, client, env):
        good = _make_jpeg(env["final"], "here.JPG", size=(400, 300))
        ghost = env["final"] / "gone.JPG"

        result = client.post(
            "/api/photos/warm", json={"paths": [str(good), str(ghost)], "tier": "grid"}
        ).json()

        assert result["generated"] == 1

    def test_default_warms_both_tiers(self, client, env):
        """One decode serves both, so a prewarm pass should leave the viewer hot too."""
        photo = _make_jpeg(env["final"], "both.JPG", size=(600, 400))

        result = client.post("/api/photos/warm", json={"paths": [str(photo)]}).json()

        assert result["generated"] == 2
        assert thumbs_mod.thumb_path(photo, "grid").is_file()
        assert thumbs_mod.thumb_path(photo, "view").is_file()

    def test_explicit_tier_list(self, client, env):
        photo = _make_jpeg(env["final"], "one.JPG", size=(600, 400))

        result = client.post(
            "/api/photos/warm", json={"paths": [str(photo)], "tiers": ["view"]}
        ).json()

        assert result["generated"] == 1
        assert thumbs_mod.thumb_path(photo, "view").is_file()
        assert not thumbs_mod.thumb_path(photo, "grid").exists()

    def test_legacy_single_tier_field_still_wins(self, client, env):
        """An older client that sends `tier` must not silently start doing twice the work."""
        photo = _make_jpeg(env["final"], "legacy.JPG", size=(600, 400))

        result = client.post(
            "/api/photos/warm", json={"paths": [str(photo)], "tier": "grid"}
        ).json()

        assert result["generated"] == 1
        assert not thumbs_mod.thumb_path(photo, "view").exists()

    def test_unknown_tier_in_the_list_is_rejected(self, client, env):
        photo = _make_jpeg(env["final"], "bad.JPG")

        assert client.post(
            "/api/photos/warm", json={"paths": [str(photo)], "tiers": ["grid", "enormous"]}
        ).status_code == 422
