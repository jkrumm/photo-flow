"""
Tests for the narrowing model — how a saved collection (SCOPE) composes with the
ad-hoc filters (REFINE), and what the screen is told about the result.

THE PRECEDENCE TABLE. Every row below has a test, named after it.

    #  Situation                                       Winner   Effect
    1  neither layer sets the dimension                —        dimension stays open
    2  only SCOPE sets it                              scope    passes through
    3  only REFINE sets it                             refine   passes through
    4  both set it, same field                         REFINE   scope's value reported as overridden
    5  both set it, different fields of one dimension  REFINE   scope's whole dimension is dropped
    6  both set a RANGE dimension (min vs max)         REFINE   ditto — min and max are one axis
    7  they set different dimensions                   —        intersection (AND)
    8  scope spans both roots, rail picks one          refine   narrows inside the scope
    9  scope pins a root, rail picks the other         REFINE   the other root, scope's other dims kept
   10  scope opens rejects, refine sets a rating       REFINE   reject-hiding default returns
   11  a facet leaves its own dimension open           —        BOTH layers' clauses for it are dropped
   12  the scope id names nothing                      —        404, never a silent whole library
   13  sort / order                                    —        not owned by a scope at all
   14  destructive ops (reject purge)                  —        deliberately unscoped

Rows 5 and 6 are the load-bearing ones: the merge unit is the `_clauses` DIMENSION, not
the field, which is why `rating_min` + `rating` cannot produce the empty set and why the
facet contract (row 11) survives having a scope above it.

Isolation is the same contract as tests/test_photos_api.py — every path the store, the
router and the indexer can reach is relocated into tmp_path, so no test touches the real
library, the real index or ~/Pictures/photoflow.toml.
"""

from __future__ import annotations

import inspect
from pathlib import Path
from typing import Any, Dict

import pytest
from fastapi.testclient import TestClient

import photo_flow.api.routes_photos as photos_mod
from photo_flow import collections as store
from photo_flow import config
from photo_flow.api.app import create_app
from photo_flow.api.routes_photos import (
    DIMENSION_LABELS,
    QUERY_DIMENSIONS,
    QUERY_SPEC,
    VIEW_FIELDS,
    PhotoFilters,
    _clauses,
    _where,
    compose,
    set_dimensions,
    set_fields,
)
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
    """Relocate the config file, the culling roots and the index."""
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
    f5  final   5*  ISO  200  200mm  keywords: 25 Segeln
    f4  final   4*  ISO  800   35mm  keywords: 25 Segeln, Insta Post Segel 25
    f0  final   0*  ISO 3200   16mm
    fx  final  -1*  ISO  400   35mm   (rejected)
    s3  staging 3*  ISO  640   90mm  keywords: 25 Segeln
    s4  staging 4*  ISO  100  200mm
    """
    _seed(env, filename="f5.JPG", rating=5, iso=200, focal_mm=200.0, keywords=["25 Segeln"])
    _seed(
        env,
        filename="f4.JPG",
        rating=4,
        iso=800,
        focal_mm=35.0,
        keywords=["25 Segeln", "Insta Post Segel 25"],
    )
    _seed(env, filename="f0.JPG", rating=0, iso=3200, focal_mm=16.0)
    _seed(env, filename="fx.JPG", rating=-1, iso=400, focal_mm=35.0)
    _seed(env, root="staging", filename="s3.JPG", rating=3, iso=640, focal_mm=90.0,
          keywords=["25 Segeln"])
    _seed(env, root="staging", filename="s4.JPG", rating=4, iso=100, focal_mm=200.0)
    return env


def _names(payload: Dict[str, Any]) -> set:
    return {item["filename"] for item in payload["items"]}


def _scope(client, name: str, query: Dict[str, Any]) -> str:
    response = client.post("/api/collections", json={"name": name, "query": query})
    assert response.status_code == 200, response.text
    return response.json()["id"]


# ---------------------------------------------------------------------------
# The vocabulary <-> dimension mapping is derived, not restated
# ---------------------------------------------------------------------------


class TestDimensionMap:
    def test_every_filter_field_belongs_to_a_dimension(self):
        """A field with no dimension would silently become a layer of its own."""
        assert set(QUERY_DIMENSIONS) | set(VIEW_FIELDS) == set(QUERY_SPEC)

    def test_view_fields_are_not_dimensions(self):
        """
        A view flag WIDENS, so it must never own a dimension — that is what made
        "Show rejected" delete a collection's rating filter.
        """
        assert set(QUERY_DIMENSIONS) & set(VIEW_FIELDS) == set()
        assert set_dimensions(PhotoFilters(include_rejected=True)) == {}

    def test_every_dimension_is_one_the_clause_builder_emits(self):
        """
        The tripwire against drift: the merge unit and the facet unit must be the same
        set of names, or `exclude=` would leave a clause the merge thought it replaced.
        """
        everything = PhotoFilters(
            root="final",
            rating=[5],
            label=["Red"],
            orientation="portrait",
            iso_min=100,
            aperture_min=1.4,
            shutter_min=0.001,
            focal_min=16.0,
            camera_model=["X-T4"],
            lens_model=["XF27mmF2.8 R WR"],
            date_from="2026-01-01",
            q="DSCF",
            has_sidecar=True,
            keyword=["25 Segeln"],
        )
        assert set(_clauses(everything)) == set(QUERY_DIMENSIONS.values())

    def test_every_dimension_has_a_human_label(self):
        """A dimension the readout cannot name is a dimension nobody can clear."""
        assert set(DIMENSION_LABELS) == set(QUERY_DIMENSIONS.values())

    def test_set_fields_reads_fields_not_clauses(self):
        """
        `_clauses` emits a `rating` entry for the default reject-hiding rule. Mistaking
        that for a narrowed dimension would let an empty scope shadow a real refinement.
        """
        assert set_fields(PhotoFilters()) == {}
        assert "rating" in _clauses(PhotoFilters())
        assert set_dimensions(PhotoFilters(rating_min=4)) == {"rating": {"rating_min": 4}}


# ---------------------------------------------------------------------------
# The precedence table, row by row
# ---------------------------------------------------------------------------


class TestPrecedence:
    def test_row_1_neither_layer_sets_a_dimension(self):
        result = compose(PhotoFilters(rating_min=4), PhotoFilters(iso_min=800))
        assert "orientation" not in result.sources
        assert result.effective.orientation is None

    def test_row_2_only_scope_sets_it(self):
        result = compose(PhotoFilters(rating_min=4), PhotoFilters())
        assert result.effective.rating_min == 4
        assert result.sources["rating"] == "scope"
        assert result.overridden == {}

    def test_row_3_only_refine_sets_it(self):
        result = compose(PhotoFilters(), PhotoFilters(rating_min=4))
        assert result.effective.rating_min == 4
        assert result.sources["rating"] == "refine"

    def test_row_3_no_scope_at_all_is_the_refinement_verbatim(self):
        result = compose(None, PhotoFilters(root="staging", iso_min=800))
        assert result.effective == PhotoFilters(root="staging", iso_min=800)
        assert result.sources == {"root": "refine", "iso": "refine"}

    def test_row_4_both_set_the_same_field_refine_wins(self):
        result = compose(PhotoFilters(rating_min=4), PhotoFilters(rating_min=2))
        assert result.effective.rating_min == 2
        assert result.sources["rating"] == "refine"
        assert result.overridden["rating"] == {"rating_min": 4}

    def test_row_5_different_fields_of_one_dimension_refine_takes_the_whole_dimension(self):
        """
        THE case the model exists for: scope rating_min=4, refine rating=[0]. Keeping
        both would AND to the empty set and make the rating facet a liar.
        """
        result = compose(PhotoFilters(rating_min=4), PhotoFilters(rating=[0]))
        assert result.effective.rating == [0]
        assert result.effective.rating_min is None
        assert result.overridden["rating"] == {"rating_min": 4}

    def test_row_6_a_range_dimension_is_one_axis_not_two(self):
        result = compose(PhotoFilters(focal_min=200.0), PhotoFilters(focal_max=100.0))
        assert result.effective.focal_max == 100.0
        assert result.effective.focal_min is None
        assert result.overridden["focal"] == {"focal_min": 200.0}

    def test_row_7_different_dimensions_intersect(self):
        result = compose(PhotoFilters(rating_min=4), PhotoFilters(iso_min=800))
        assert result.effective.rating_min == 4
        assert result.effective.iso_min == 800
        assert result.sources == {"rating": "scope", "iso": "refine"}
        assert result.overridden == {}

    def test_row_8_scope_spans_both_roots_and_the_rail_picks_one(self):
        result = compose(PhotoFilters(rating_min=4), PhotoFilters(root="staging"))
        assert result.effective.root == "staging"
        assert result.effective.rating_min == 4
        assert result.overridden == {}

    def test_row_9_scope_pins_a_root_and_the_rail_picks_the_other(self):
        result = compose(
            PhotoFilters(root="final", rating_min=4), PhotoFilters(root="staging")
        )
        assert result.effective.root == "staging"
        assert result.effective.rating_min == 4
        assert result.overridden["root"] == {"root": "final"}

    def test_row_10_a_view_flag_composes_by_or_and_owns_no_dimension(self):
        """
        `include_rejected` WIDENS, so it is not a dimension and no layer can lose it.
        Either layer asking is enough, and the rating dimension is unaffected either way.
        """
        from_scope = compose(PhotoFilters(include_rejected=True), PhotoFilters(rating=[5]))
        assert from_scope.effective.include_rejected is True
        assert from_scope.effective.rating == [5]
        assert from_scope.overridden == {}

        from_refine = compose(PhotoFilters(rating_min=4), PhotoFilters(include_rejected=True))
        assert from_refine.effective.include_rejected is True
        # THE BUG THIS ROW EXISTS FOR: the scope's rating filter must survive a view
        # toggle. It did not — `include_rejected` used to own the rating dimension, so
        # flipping "Show rejected" inside a collection deleted the filter that defined it.
        assert from_refine.effective.rating_min == 4
        assert from_refine.overridden == {}
        assert from_refine.sources["rating"] == "scope"

        neither = compose(PhotoFilters(rating_min=4), PhotoFilters())
        assert neither.effective.include_rejected is False

    def test_row_11_a_facet_drops_both_layers_clauses_for_its_own_dimension(self):
        """
        The facet contract, stated as SQL: `_where(effective, exclude=D)` must contain no
        clause from either layer for D, or the option list would offer a value the scope
        silently forbids.
        """
        effective = compose(PhotoFilters(rating_min=4), PhotoFilters(root="final")).effective
        where, params = _where(effective, exclude="rating")
        assert "rating" not in where
        assert 4 not in params
        assert "root = ?" in where

    def test_composition_is_associative_over_disjoint_dimensions(self):
        """Order of the scope's own keys cannot change the answer — it is a set merge."""
        left = compose(PhotoFilters(root="final", iso_min=800), PhotoFilters(rating=[5]))
        right = compose(PhotoFilters(iso_min=800, root="final"), PhotoFilters(rating=[5]))
        assert left.effective == right.effective


# ---------------------------------------------------------------------------
# The same rules, over HTTP and over real rows
# ---------------------------------------------------------------------------


class TestPrecedenceOverHttp:
    def test_row_2_a_scope_alone_narrows(self, client, library):
        cid = _scope(client, "Keepers", {"root": "final", "rating_min": 4})
        payload = client.get("/api/photos", params={"collection": cid}).json()
        assert _names(payload) == {"f4.JPG", "f5.JPG"}

    def test_row_5_refining_the_rating_replaces_the_scope_rating(self, client, library):
        cid = _scope(client, "Keepers", {"root": "final", "rating_min": 4})
        payload = client.get("/api/photos", params={"collection": cid, "rating": 0}).json()
        # Not empty: the scope's root survives, its rating does not.
        assert _names(payload) == {"f0.JPG"}

    def test_row_7_refining_another_dimension_intersects(self, client, library):
        cid = _scope(client, "Keepers", {"rating_min": 4})
        payload = client.get(
            "/api/photos", params={"collection": cid, "root": "staging"}
        ).json()
        assert _names(payload) == {"s4.JPG"}

    def test_row_9_the_rail_overrides_the_scope_root(self, client, library):
        cid = _scope(client, "Final keepers", {"root": "final", "rating_min": 4})
        payload = client.get(
            "/api/photos", params={"collection": cid, "root": "staging"}
        ).json()
        assert _names(payload) == {"s4.JPG"}

    def test_row_10_a_reject_scope_survives_until_the_rating_is_refined(self, client, library):
        cid = _scope(client, "Rejects", {"rating": [-1]})
        assert _names(client.get("/api/photos", params={"collection": cid}).json()) == {
            "fx.JPG"
        }
        refined = client.get("/api/photos", params={"collection": cid, "rating": 5}).json()
        assert _names(refined) == {"f5.JPG"}

    def test_row_10_show_rejected_does_not_dissolve_the_scope(self, client, library):
        """
        The exact request the sidebar's "Show rejected" switch makes inside a collection.
        Before the view-flag split this returned the whole Final root.
        """
        cid = _scope(client, "Keepers", {"root": "final", "rating_min": 4})
        payload = client.get(
            "/api/photos", params={"collection": cid, "include_rejected": "true"}
        ).json()
        assert _names(payload) == {"f4.JPG", "f5.JPG"}

    def test_row_10_the_readout_describes_the_set_the_list_returns(self, client, library):
        """
        The narrowing card and the filmstrip must never be looking at two different sets:
        the card is the one surface whose job is to explain the other.
        """
        cid = _scope(client, "Everything final", {"root": "final"})
        params = {"collection": cid, "include_rejected": "true"}
        listing = client.get("/api/photos", params=params).json()
        readout = client.get("/api/photos/narrowing", params=params).json()
        assert readout["total"] == listing["total"] == 4
        assert readout["library_total"] == 6

    def test_row_11_the_rating_facet_counts_what_the_scope_forbids(self, client, library):
        """
        Inside a `rating_min = 4` scope, the rating facet must still report the 0-star
        bucket — because clicking it is legal and yields rows (row 5). A facet that hid
        it would be offering only options that change nothing.
        """
        cid = _scope(client, "Keepers", {"root": "final", "rating_min": 4})
        facets = client.get("/api/photos/facets", params={"collection": cid}).json()
        assert facets["count"] == 2
        assert facets["ratings"]["0"] == 1
        assert facets["ratings"]["5"] == 1
        # Every other dimension stays scoped: the staging rows are not in the ISO range.
        assert facets["iso"]["max"] == 800

    def test_row_12_an_unknown_scope_is_a_404_not_the_whole_library(self, client, library):
        for path in ("/api/photos", "/api/photos/facets", "/api/photos/narrowing"):
            response = client.get(path, params={"collection": "no-such-thing"})
            assert response.status_code == 404, path

    def test_row_13_sort_is_not_owned_by_a_scope(self, client, library):
        cid = _scope(client, "Keepers", {"root": "final", "rating_min": 4})
        ascending = client.get(
            "/api/photos", params={"collection": cid, "sort": "rating", "order": "asc"}
        ).json()
        descending = client.get(
            "/api/photos", params={"collection": cid, "sort": "rating", "order": "desc"}
        ).json()
        assert [i["filename"] for i in ascending["items"]] == ["f4.JPG", "f5.JPG"]
        assert [i["filename"] for i in descending["items"]] == ["f5.JPG", "f4.JPG"]

    def test_row_14_the_reject_purge_is_deliberately_unscoped(self, client, library, env):
        """
        A destructive batch is NOT composed. It re-reads the rejects from the index and
        takes only `root` — so what it moves is always something the user can enumerate,
        never the residue of a scope they may have half-forgotten they were inside.
        """
        (env["final"] / "fx.JPG").write_bytes(b"not really a jpeg")
        cid = _scope(client, "Keepers", {"root": "final", "rating_min": 4})

        # The endpoint has no `collection` parameter, so passing one changes nothing —
        # which is the assertion: a destructive batch cannot be silently re-aimed by the
        # view the user happens to be in.
        assert "collection" not in inspect.signature(photos_mod.photos_purge_rejects).parameters
        scoped = client.post(
            "/api/photos/rejects/purge", params={"dry_run": True, "collection": cid}
        )
        unscoped = client.post("/api/photos/rejects/purge", params={"dry_run": True})
        assert scoped.status_code == unscoped.status_code == 200
        # `trashed` is 0 on a dry run by contract; `entries` is the preview. The scope said
        # rating >= 4 and the preview still holds the one rejected frame, unchanged by it.
        names = [e["filename"] for e in scoped.json()["entries"]]
        assert names == [e["filename"] for e in unscoped.json()["entries"]] == ["fx.JPG"]

    def test_a_scope_plus_a_refinement_equals_the_merged_query(self, client, library):
        """One code path: composing must be indistinguishable from asking directly."""
        cid = _scope(client, "Keepers", {"root": "final", "rating_min": 4})
        composed = client.get(
            "/api/photos", params={"collection": cid, "iso_min": 500}
        ).json()
        direct = client.get(
            "/api/photos", params={"root": "final", "rating_min": 4, "iso_min": 500}
        ).json()
        assert [i["path"] for i in composed["items"]] == [i["path"] for i in direct["items"]]
        assert composed["total"] == direct["total"] == 1


# ---------------------------------------------------------------------------
# The narrowing readout
# ---------------------------------------------------------------------------


class TestNarrowingReadout:
    def test_an_unscoped_unfiltered_library_narrows_on_nothing(self, client, library):
        payload = client.get("/api/photos/narrowing").json()
        assert payload["dimensions"] == []
        assert payload["scope_id"] is None
        # 6 rows, one rejected and therefore hidden by default.
        assert payload["total"] == payload["library_total"] == 5

    def test_it_names_the_layer_behind_every_dimension(self, client, library):
        cid = _scope(client, "Keepers", {"root": "final", "rating_min": 4})
        payload = client.get(
            "/api/photos/narrowing", params={"collection": cid, "iso_min": 500}
        ).json()
        by_dim = {d["dimension"]: d for d in payload["dimensions"]}
        assert by_dim["root"]["source"] == "scope"
        assert by_dim["rating"]["source"] == "scope"
        assert by_dim["iso"]["source"] == "refine"
        assert by_dim["iso"]["label"] == "ISO"
        assert payload["scope_name"] == "Keepers"

    def test_without_is_what_clearing_that_dimension_would_show(self, client, library):
        """The 'what would happen' number, and it must match asking for it directly."""
        payload = client.get("/api/photos/narrowing", params={"rating_min": 4}).json()
        (rating,) = payload["dimensions"]
        assert payload["total"] == 3
        assert rating["without"] == 5
        direct = client.get("/api/photos").json()["total"]
        assert rating["without"] == direct

    def test_without_reinstates_the_defaults_a_facet_deliberately_drops(self, client, library):
        """
        The one place the readout and the facet must NOT use the same computation.
        `exclude="rating"` drops the reject-hiding clause too — correct for a facet that
        reports the -1 bucket, and a lie in a readout that means "what if I cleared this".
        """
        payload = client.get("/api/photos/narrowing", params={"rating_min": 4}).json()
        (rating,) = payload["dimensions"]
        assert rating["without"] == 5
        # 6 rows exist; the rejected one stays hidden when the rating filter is cleared.
        facets = client.get("/api/photos/facets", params={"rating_min": 4}).json()
        assert facets["ratings"]["-1"] == 1

    def test_an_override_is_reported_rather_than_swallowed(self, client, library):
        cid = _scope(client, "Keepers", {"root": "final", "rating_min": 4})
        payload = client.get(
            "/api/photos/narrowing", params={"collection": cid, "rating": 0}
        ).json()
        by_dim = {d["dimension"]: d for d in payload["dimensions"]}
        assert by_dim["rating"]["overrides"] is True
        assert by_dim["rating"]["source"] == "refine"
        assert by_dim["rating"]["scope_fields"] == {"rating_min": 4}
        assert by_dim["rating"]["fields"] == {"rating": [0]}

    def test_scope_total_is_the_scope_alone(self, client, library):
        cid = _scope(client, "Keepers", {"root": "final", "rating_min": 4})
        payload = client.get(
            "/api/photos/narrowing", params={"collection": cid, "iso_min": 500}
        ).json()
        assert payload["scope_total"] == 2
        assert payload["total"] == 1
        assert payload["library_total"] == 5

    def test_the_readout_agrees_with_the_list_it_describes(self, client, library):
        cid = _scope(client, "Keepers", {"rating_min": 4})
        params = {"collection": cid, "root": "final"}
        narrowing = client.get("/api/photos/narrowing", params=params).json()
        listing = client.get("/api/photos", params=params).json()
        assert narrowing["total"] == listing["total"]


# ---------------------------------------------------------------------------
# Keywords: a tag is a dimension, an album is a collection over it
# ---------------------------------------------------------------------------


class TestKeywords:
    def test_a_keyword_filter_is_an_or_set_like_camera_or_lens(self, client, library):
        payload = client.get("/api/photos", params={"keyword": "25 Segeln"}).json()
        assert _names(payload) == {"f5.JPG", "f4.JPG", "s3.JPG"}
        both = client.get(
            "/api/photos", params=[("keyword", "25 Segeln"), ("keyword", "Insta Post Segel 25")]
        ).json()
        assert _names(both) == {"f5.JPG", "f4.JPG", "s3.JPG"}

    def test_a_keyword_match_is_exact_not_a_substring(self, client, library):
        """`|a|b|` sentinels are what stop 'Segeln' from matching '25 Segeln'."""
        assert client.get("/api/photos", params={"keyword": "Segeln"}).json()["total"] == 0

    def test_keywords_reach_the_row(self, client, library):
        payload = client.get("/api/photos", params={"keyword": "Insta Post Segel 25"}).json()
        (row,) = payload["items"]
        assert row["keywords"] == ["25 Segeln", "Insta Post Segel 25"]

    def test_the_keyword_facet_leaves_its_own_dimension_open(self, client, library):
        payload = client.get("/api/photos/facets", params={"keyword": "Insta Post Segel 25"}).json()
        counts = {f["value"]: f["count"] for f in payload["keywords"]}
        # The other keyword is still offered, counted across everything else in view.
        assert counts == {"25 Segeln": 3, "Insta Post Segel 25": 1}

    def test_an_album_is_a_collection_over_the_keyword_dimension(self, client, library):
        """The whole tag answer, in one test: no album type, no album table, no album files."""
        cid = _scope(client, "Insta Post Segel 25", {"keyword": ["Insta Post Segel 25"]})
        payload = client.get("/api/photos", params={"collection": cid}).json()
        assert _names(payload) == {"f4.JPG"}

    def test_an_album_scope_still_refines_like_any_other(self, client, library):
        cid = _scope(client, "Sailing 2025", {"keyword": ["25 Segeln"]})
        payload = client.get(
            "/api/photos", params={"collection": cid, "root": "staging"}
        ).json()
        assert _names(payload) == {"s3.JPG"}

    def test_a_keyword_with_a_like_wildcard_is_matched_literally(self, client, env):
        _seed(env, filename="pct.JPG", keywords=["50%"])
        _seed(env, filename="other.JPG", keywords=["50 percent"])
        payload = client.get("/api/photos", params={"keyword": "50%"}).json()
        assert _names(payload) == {"pct.JPG"}


# ---------------------------------------------------------------------------
# Bounds — the F1 gap where the two gates accepted different value ranges
# ---------------------------------------------------------------------------


class TestBounds:
    def test_an_out_of_range_rating_min_is_refused_by_both_gates(self, client, env):
        assert client.get("/api/photos", params={"rating_min": 99}).status_code == 422
        response = client.post(
            "/api/collections", json={"name": "Impossible", "query": {"rating_min": 99}}
        )
        assert response.status_code == 400
        assert "rating_min" in response.json()["detail"]

    def test_an_out_of_range_rating_item_is_refused(self, client, env):
        response = client.post(
            "/api/collections", json={"name": "Impossible", "query": {"rating": [9]}}
        )
        assert response.status_code == 400

    def test_the_reject_value_is_inside_the_range(self, client, env):
        response = client.post(
            "/api/collections", json={"name": "Rejects", "query": {"rating": [-1]}}
        )
        assert response.status_code == 200

    def test_bounds_are_derived_from_the_dependency_signature(self):
        """
        Not restated. If someone adds `Query(ge=…)` to a filter parameter, the saved-query
        gate picks it up for free; if someone removes one, both gates loosen together.
        """
        assert photos_mod.QUERY_BOUNDS["rating_min"] == (0, 5)
        assert set(photos_mod.QUERY_BOUNDS) <= set(QUERY_SPEC)


# ---------------------------------------------------------------------------
# The store's preservation contract
# ---------------------------------------------------------------------------


class TestFilePreservation:
    def test_other_tables_and_comments_survive_a_write(self, env):
        """
        F1 shipped a writer that re-emitted only its own model, so one rename deleted
        every other table and every comment in the file — which would have made this the
        one file F4's `[library]` and `[cameras]` tables could not share.
        """
        env["toml"].write_text(
            "# my hand notes\n\n"
            '[library]\nroots = ["/Volumes/EXT/Bilder"]\n\n'
            '[cameras.xt4]\nvolume = "Fuji X-T4"\n\n'
            '[[collections]]\nid = "keepers"\nname = "Keepers"\n\n'
            "[collections.query]\nrating_min = 4\n\n"
            '[notes]\ntext = "after the region"\n'
        )
        store.update("keepers", name="Keepers 2")
        store.create("New one", {"iso_min": 1600})

        text = env["toml"].read_text()
        assert "# my hand notes" in text
        assert '[library]' in text and '"/Volumes/EXT/Bilder"' in text
        assert "[cameras.xt4]" in text
        assert '[notes]' in text and '"after the region"' in text
        assert 'name = "Keepers 2"' in text
        assert 'name = "New one"' in text

    def test_a_skipped_entry_survives_an_unrelated_write(self, env):
        """
        Tolerance that deletes on the next click is worse than raising — a raise at least
        leaves the file. A duplicate id and a nameless entry are quarantined, not dropped.
        """
        env["toml"].write_text(
            '[[collections]]\nid = "a"\nname = "First"\n\n'
            "[collections.query]\nrating_min = 4\n\n"
            '[[collections]]\nid = "a"\nname = "Shadowed by a duplicate id"\n\n'
            "[collections.query]\nfocal_min = 200\n\n"
            '[[collections]]\nid = "c"\n'
        )
        assert [c.id for c in store.load()] == ["a"]

        store.create("New one", {"iso_min": 1600})

        text = env["toml"].read_text()
        assert "Shadowed by a duplicate id" in text
        assert "focal_min = 200" in text
        assert 'id = "c"' in text

    def test_a_write_still_leaves_no_temp_residue_on_failure(self, env, monkeypatch):
        store.create("Keepers", {"rating_min": 4})
        before = env["toml"].read_text()

        def boom(*_args, **_kwargs):
            raise OSError("disk full")

        monkeypatch.setattr(store.os, "replace", boom)
        with pytest.raises(OSError):
            store.create("Second", {"rating_min": 5})

        assert env["toml"].read_text() == before
        assert list(env["tmp"].glob(".photoflow.toml.*")) == []

    def test_a_first_write_into_an_empty_directory_writes_the_header(self, env):
        store.create("Keepers", {"rating_min": 4})
        assert env["toml"].read_text().startswith("# photo-flow library configuration.")

    def test_the_written_file_is_still_valid_toml_after_a_round_trip(self, env):
        env["toml"].write_text('[library]\nroots = ["/a"]\n')
        store.create("Keepers", {"rating_min": 4, "label": ["Red"]})
        store.update("keepers", query={"rating_min": 5})
        reread = store.read()
        assert [c.query for c in reread.collections] == [{"rating_min": 5}]
        assert "[library]" in env["toml"].read_text()
