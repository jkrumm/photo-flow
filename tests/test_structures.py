"""
Tests for library structures — the Stage F3 experiment.

THE CLAIM UNDER TEST: every candidate way of arranging a photo library (flat, YYYY/MM,
album, event) can be navigated as a saved QUERY, so none of them has to become a second
on-disk layout. `GET /api/photos/structure` returns each layout as a list of groups, and
the property that makes the claim true is:

    for every group: the group's own `query` resolves to exactly the group's photos.

That equivalence is asserted per kind below, over a seeded library whose shape is known.
If it ever breaks, a "folder" in this app has stopped meaning what it says.

The other half of the experiment is where a structure CANNOT be a query, and the event
kind is the one that shows it: the group's bounds are an ordinary date range, but the
BOUNDARIES are computed. `TestEventGrain` pins what that costs — the grain is a
parameter, not a fact — and `TestEventStability` pins the one property that makes it
survivable: narrowing can only ever SPLIT an event, never merge or straddle two.

Isolation is the contract of tests/test_photos_api.py — the config file, both culling
roots, the trash, the thumbnail cache and the index are all relocated into tmp_path, so
nothing here touches the real library, the real index or ~/Pictures/photoflow.toml.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, List

import pytest
from fastapi.testclient import TestClient

import photo_flow.api.routes_photos as photos_mod
from photo_flow import config
from photo_flow.api.app import create_app
from photo_flow.index import db as db_mod
from photo_flow.index import indexer as indexer_mod
from photo_flow.index import thumbs as thumbs_mod
from photo_flow.index.db import get_db
from photo_flow.index.indexer import pack_keywords


# ---------------------------------------------------------------------------
# Environment isolation
# ---------------------------------------------------------------------------


@pytest.fixture()
def env(tmp_path, monkeypatch) -> Dict[str, Path]:
    """Relocate the config file, the culling roots and the index into tmp_path."""
    final_dir = tmp_path / "Final"
    staging_dir = tmp_path / "Staging"
    gallery_dir = tmp_path / "Gallery"
    trash_dir = tmp_path / ".photoflow-trash"
    final_dir.mkdir()
    staging_dir.mkdir()
    (gallery_dir / "images").mkdir(parents=True)

    roots = {"final": final_dir, "staging": staging_dir}
    monkeypatch.setattr(config, "LIBRARY_CONFIG_PATH", tmp_path / "photoflow.toml")
    monkeypatch.setattr(config, "CULL_ROOTS", roots)
    monkeypatch.setattr(config, "TRASH_PATH", trash_dir)
    monkeypatch.setattr(photos_mod, "CULL_ROOTS", roots)
    monkeypatch.setattr(photos_mod, "TRASH_PATH", trash_dir)
    monkeypatch.setattr(thumbs_mod, "THUMB_CACHE_PATH", tmp_path / "thumbs")
    monkeypatch.setattr(db_mod, "_DEFAULT_DB_PATH", tmp_path / "index.db")
    monkeypatch.setattr(indexer_mod, "FINAL_PATH", final_dir)
    monkeypatch.setattr(indexer_mod, "STAGING_PATH", staging_dir)
    monkeypatch.setattr(indexer_mod, "GALLERY_PATH", gallery_dir)

    return {
        "final": final_dir,
        "staging": staging_dir,
        "db": tmp_path / "index.db",
        "toml": tmp_path / "photoflow.toml",
        "tmp": tmp_path,
    }


@pytest.fixture()
def client(env):
    with TestClient(create_app()) as test_client:
        yield test_client


_INSERT_SQL = """
    INSERT INTO photos
        (path, filename, size, mtime, date_taken, rating, iso, aperture_f, shutter_s,
         focal_mm, camera_model, camera_make, lens_model, label, keywords, dimensions,
         width, height, orientation, has_sidecar, root, present, in_final, published,
         indexed_at)
    VALUES
        (:path, :filename, :size, :mtime, :date_taken, :rating, :iso, :aperture_f,
         :shutter_s, :focal_mm, :camera_model, :camera_make, :lens_model, :label,
         :keywords, :dimensions, :width, :height, :orientation, :has_sidecar, :root,
         :present, :in_final, :published, :indexed_at)
"""


def _seed(env: Dict[str, Path], **overrides: Any) -> Dict[str, Any]:
    """Insert one `photos` row, defaulting every column to a plausible Fuji frame."""
    root = overrides.pop("root", "final")
    filename = overrides.pop("filename", "photo.JPG")
    keywords = overrides.pop("keywords", [])
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
        "keywords": pack_keywords(keywords),
        "dimensions": "6240x4160",
        "width": 6240,
        "height": 4160,
        "orientation": "landscape",
        "has_sidecar": 0,
        "root": root,
        "present": 1,
        "in_final": 1 if root == "final" else 0,
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
def library(env) -> Dict[str, Path]:
    """
    A library with a shape every structure can be checked against.

    Three shoots, deliberately arranged so the grain matters:

      a1 a2 a3   2026-01-10  09:00 / 09:05 / 09:20   final    tag: Trip
      b1 b2      2026-01-10  20:00 / 20:10           final    (same DAY, different shoot)
      c1         2026-01-25  09:00                   final    tag: Trip, Print
      d1 d2      2026-03-02  09:00 / 09:01           staging  tag: Print
      u1         (no capture date)                   final
    """
    for name, stamp, tags in [
        ("a1.JPG", "2026-01-10T09:00:00Z", ["Trip"]),
        ("a2.JPG", "2026-01-10T09:05:00Z", ["Trip"]),
        ("a3.JPG", "2026-01-10T09:20:00Z", ["Trip"]),
        ("b1.JPG", "2026-01-10T20:00:00Z", []),
        ("b2.JPG", "2026-01-10T20:10:00Z", []),
        ("c1.JPG", "2026-01-25T09:00:00Z", ["Trip", "Print"]),
    ]:
        _seed(env, filename=name, date_taken=stamp, keywords=tags)
    _seed(env, root="staging", filename="d1.JPG", date_taken="2026-03-02T09:00:00Z",
          keywords=["Print"])
    _seed(env, root="staging", filename="d2.JPG", date_taken="2026-03-02T09:01:00Z",
          keywords=["Print"])
    _seed(env, filename="u1.JPG", date_taken="")
    return env


def _structure(client, **params: Any) -> Dict[str, Any]:
    response = client.get("/api/photos/structure", params=params)
    assert response.status_code == 200, response.text
    return response.json()


def _resolve(client, query: Dict[str, Any], **extra: Any) -> List[str]:
    """Run a group's own query through the ordinary list endpoint."""
    params: Dict[str, Any] = {"limit": 500, **extra}
    for key, value in query.items():
        params[key] = value
    response = client.get("/api/photos", params=params)
    assert response.status_code == 200, response.text
    return sorted(item["filename"] for item in response.json()["items"])


# ---------------------------------------------------------------------------
# THE property: a group IS its query
# ---------------------------------------------------------------------------


class TestGroupsAreQueries:
    @pytest.mark.parametrize("kind", ["flat", "month", "album", "event"])
    def test_every_group_resolves_to_exactly_its_own_rows(self, client, library, kind):
        """
        The whole Stage F3 claim in one assertion. If a group's count and its query's
        result ever disagree, a "folder" in this app has stopped meaning what it says.
        """
        data = _structure(client, kind=kind)
        assert data["groups"], f"{kind} produced no groups"
        for group in data["groups"]:
            resolved = _resolve(client, group["query"])
            assert len(resolved) == group["count"], f"{kind}/{group['key']}: {resolved}"

    def test_a_group_query_is_storable_as_a_collection_verbatim(self, client, library):
        """
        A structure group and a saved collection speak the same language — which is what
        makes "keep this event" a rename rather than a feature.
        """
        group = _structure(client, kind="event", gap_seconds=3600)["groups"][0]
        created = client.post(
            "/api/collections", json={"name": "That shoot", "query": group["query"]}
        )
        assert created.status_code == 200, created.text
        listed = client.get(f"/api/collections/{created.json()['id']}/photos", params={"limit": 500})
        assert listed.status_code == 200
        names = sorted(item["filename"] for item in listed.json()["items"])
        assert names == _resolve(client, group["query"])


# ---------------------------------------------------------------------------
# Coverage — what a layout cannot place
# ---------------------------------------------------------------------------


class TestCoverage:
    def test_flat_places_everything(self, client, library):
        data = _structure(client, kind="flat")
        assert {g["key"] for g in data["groups"]} == {"final", "staging"}
        assert data["ungrouped"] == 0

    def test_month_cannot_place_an_undated_photo(self, client, library):
        """`u1` has no capture date. A directory layout would hide this in `Misc/`."""
        data = _structure(client, kind="month")
        assert data["ungrouped"] == 1
        assert sum(g["count"] for g in data["groups"]) == data["total"] - 1

    def test_album_leaves_untagged_photos_homeless(self, client, library):
        """
        The measurement that decides whether albums can BE the layout: only what somebody
        tagged is in one. Here 3 of 9 rows carry no tag.
        """
        data = _structure(client, kind="album")
        assert {g["key"]: g["count"] for g in data["groups"]} == {"Trip": 4, "Print": 3}
        assert data["ungrouped"] == 3

    def test_album_counts_do_not_sum_to_the_total(self, client, library):
        """A photo with two tags is in two groups. Overlap is a property, not a bug."""
        data = _structure(client, kind="album")
        assert sum(g["count"] for g in data["groups"]) > data["total"] - data["ungrouped"]

    def test_event_places_every_dated_photo_exactly_once(self, client, library):
        data = _structure(client, kind="event", gap_seconds=3600)
        assert sum(g["count"] for g in data["groups"]) + data["ungrouped"] == data["total"]
        assert data["ungrouped"] == 1


# ---------------------------------------------------------------------------
# The facet contract — a structure does not collapse when you click it
# ---------------------------------------------------------------------------


class TestSelfDimensionStaysOpen:
    def test_selecting_a_month_does_not_collapse_the_month_list(self, client, library):
        full = _structure(client, kind="month")
        assert len(full["groups"]) >= 2
        inside = _structure(client, kind="month", date_from="2026-01-01",
                            date_to="2026-01-31T23:59:59Z")
        assert [g["key"] for g in inside["groups"]] == [g["key"] for g in full["groups"]]

    def test_selecting_a_tag_does_not_collapse_the_album_list(self, client, library):
        full = _structure(client, kind="album")
        inside = _structure(client, kind="album", keyword="Trip")
        assert [g["key"] for g in inside["groups"]] == [g["key"] for g in full["groups"]]

    def test_another_dimension_does_narrow_the_structure(self, client, library):
        """Only the structure's OWN dimension is held open — everything else applies."""
        assert _structure(client, kind="album", root="staging")["groups"] == [
            {"key": "Print", "label": "Print", "sublabel": "tag", "count": 2,
             "query": {"keyword": ["Print"]}}
        ]


# ---------------------------------------------------------------------------
# Event grain — the parameter that has no single right value
# ---------------------------------------------------------------------------


class TestEventGrain:
    def test_a_short_gap_splits_one_calendar_day_into_two_shoots(self, client, library):
        """
        `a*` at 09:00 and `b*` at 20:00 are the same DAY and not the same shoot. A YYYY/MM
        layout cannot express that difference at all; an hour-grained event can.
        """
        data = _structure(client, kind="event", gap_seconds=3600)
        january = [g for g in data["groups"] if g["key"].startswith("2026-01-10")]
        assert len(january) == 2
        assert sorted(g["count"] for g in january) == [2, 3]

    def test_a_long_gap_merges_them(self, client, library):
        data = _structure(client, kind="event", gap_seconds=3 * 86400)
        january = [g for g in data["groups"] if g["key"].startswith("2026-01-10")]
        assert len(january) == 1
        assert january[0]["count"] == 5

    def test_the_grain_changes_the_answer_monotonically(self, client, library):
        """More silence, fewer events. Never the other way round."""
        counts = [
            len(_structure(client, kind="event", gap_seconds=gap)["groups"])
            for gap in (600, 3600, 86400, 3 * 86400, 30 * 86400)
        ]
        assert counts == sorted(counts, reverse=True)

    def test_bounds_are_second_precise_not_whole_days(self, client, library):
        """
        The group's query is the cluster's own first and last capture time. A whole-day
        range would silently swallow the other shoot that day.
        """
        data = _structure(client, kind="event", gap_seconds=3600)
        morning = next(g for g in data["groups"] if g["query"]["date_from"].startswith("2026-01-10T09"))
        assert morning["query"] == {
            "date_from": "2026-01-10T09:00:00Z",
            "date_to": "2026-01-10T09:20:00Z",
        }
        assert _resolve(client, morning["query"]) == ["a1.JPG", "a2.JPG", "a3.JPG"]

    def test_the_gap_is_bounded(self, client, library):
        assert client.get("/api/photos/structure",
                          params={"kind": "event", "gap_seconds": 1}).status_code == 422
        assert client.get("/api/photos/structure",
                          params={"kind": "event", "gap_seconds": 10 ** 9}).status_code == 422

    def test_gap_is_reported_only_for_events(self, client, library):
        assert _structure(client, kind="event", gap_seconds=3600)["gap_seconds"] == 3600
        assert _structure(client, kind="month")["gap_seconds"] is None


class TestEventStability:
    def test_narrowing_can_only_split_an_event_never_straddle_two(self, client, library):
        """
        The property that makes a derived boundary survivable: removing photos can only
        WIDEN a gap, so a narrowed event tree is strictly finer. Every event under a
        narrowing sits entirely inside exactly one event of the unnarrowed tree — measured
        on the real library too (43/43 and 42/42 at two different rating floors).
        """
        base = _structure(client, kind="event", gap_seconds=3600)["groups"]
        spans = [(g["query"]["date_from"], g["query"]["date_to"]) for g in base]
        narrowed = _structure(client, kind="event", gap_seconds=3600, root="final")["groups"]
        assert narrowed
        for group in narrowed:
            parents = [
                s
                for s in spans
                if s[0] <= group["query"]["date_from"] and group["query"]["date_to"] <= s[1]
            ]
            assert len(parents) == 1, f"{group['key']} straddles {parents}"

    def test_the_boundary_key_does_move_when_the_set_does(self, client, library):
        """
        The honest counterpart: a group's KEY is its first photo's timestamp, so culling
        the first frame renames the event. A saved query keeps working; a UI selection
        keyed on the group does not. Nothing here can hide that, so it is asserted.
        """
        base = {g["key"] for g in _structure(client, kind="event", gap_seconds=3600)["groups"]}
        narrowed = {
            g["key"]
            for g in _structure(
                client, kind="event", gap_seconds=3600, q="a2"
            )["groups"]
        }
        assert narrowed and not (narrowed <= base)


# ---------------------------------------------------------------------------
# Composition, guards, and the cardinal rule
# ---------------------------------------------------------------------------


class TestStructureUnderAScope:
    def test_a_structure_is_computed_inside_the_active_collection(self, client, library):
        created = client.post(
            "/api/collections", json={"name": "Staging", "query": {"root": "staging"}}
        )
        assert created.status_code == 200, created.text
        data = _structure(client, kind="month", collection=created.json()["id"])
        assert [g["key"] for g in data["groups"]] == ["2026-03"]

    def test_an_unknown_collection_is_a_404_not_the_whole_library(self, client, library):
        response = client.get(
            "/api/photos/structure", params={"kind": "month", "collection": "nope"}
        )
        assert response.status_code == 404

    def test_an_unknown_kind_is_refused(self, client, library):
        assert client.get(
            "/api/photos/structure", params={"kind": "shoebox"}
        ).status_code == 422


class TestStructuresMoveNothing:
    def test_no_file_is_touched_by_any_structure(self, client, library, env):
        """
        The cardinal rule of this stage: a structure is a query, never a second on-disk
        layout. Files are seeded on disk and their mtimes compared across every kind.
        """
        for name in ["a1.JPG", "b1.JPG", "c1.JPG"]:
            (env["final"] / name).write_bytes(b"jpeg")
        before = {p.name: p.stat().st_mtime_ns for p in env["final"].glob("*.JPG")}
        listing = sorted(p.name for p in env["tmp"].rglob("*") if p.is_file())

        for kind in ["flat", "month", "album", "event"]:
            _structure(client, kind=kind)

        assert {p.name: p.stat().st_mtime_ns for p in env["final"].glob("*.JPG")} == before
        assert sorted(p.name for p in env["tmp"].rglob("*") if p.is_file()) == listing


class TestMalformedCaptureStamps:
    """
    A `date_taken` the extractor could not parse must not reach a month group.

    `metadata_extractor` stores the RAW EXIF string when `strptime` fails, so `2026:03:03
    17:36:33` and the blank-date placeholder `    :  :     :  :  ` are both reachable
    index states. `_structure_event` already counted such a row as ungrouped; `month` did
    not, and produced either a 500 or — worse — a group whose own query resolves to zero
    rows, which quietly falsifies the invariant every other test here asserts.
    """

    def test_blank_placeholder_does_not_crash_the_month_structure(self, client, env):
        _seed(env, filename="ok.JPG", date_taken="2026-03-03T17:36:33Z")
        _seed(env, filename="blank.JPG", date_taken="    :  :     :  :  ")

        data = _structure(client, kind="month")

        assert [g["key"] for g in data["groups"]] == ["2026-03"]
        assert data["ungrouped"] == 1

    def test_raw_exif_stamp_is_ungrouped_not_a_dead_group(self, client, env):
        _seed(env, filename="ok.JPG", date_taken="2026-03-03T17:36:33Z")
        _seed(env, filename="raw.JPG", date_taken="2026:03:03 17:36:33")

        data = _structure(client, kind="month")

        assert [g["key"] for g in data["groups"]] == ["2026-03"]
        assert data["ungrouped"] == 1
        # The invariant: what is left in a group still resolves to exactly that group.
        for group in data["groups"]:
            listed = client.get("/api/photos", params={**group["query"], "limit": 1})
            assert listed.json()["total"] == group["count"]

    def test_an_impossible_month_number_is_ungrouped(self, client, env):
        _seed(env, filename="bad.JPG", date_taken="2026-13-01T09:00:00Z")

        data = _structure(client, kind="month")

        assert data["groups"] == []
        assert data["ungrouped"] == 1

    def test_every_other_kind_still_places_or_excuses_the_row(self, client, env):
        _seed(env, filename="raw.JPG", date_taken="2026:03:03 17:36:33", keywords=["Trip"])

        assert _structure(client, kind="flat")["ungrouped"] == 0
        assert _structure(client, kind="album")["ungrouped"] == 0
        assert _structure(client, kind="event")["ungrouped"] == 1
