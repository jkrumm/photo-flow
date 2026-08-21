"""
Tests for saved collections — the TOML store and the /api/collections router.

Isolation contract (same shape as tests/test_photos_api.py)
-----------------------------------------------------------
One fixture relocates every path the store and the router can reach:

  * ``config.LIBRARY_CONFIG_PATH`` — the TOML file the store reads and writes
  * ``config.CULL_ROOTS`` / ``TRASH_PATH`` and their ``routes_photos`` aliases
  * ``index.db._DEFAULT_DB_PATH`` — every ``get_db()`` with no explicit path
  * ``indexer.FINAL_PATH`` / ``STAGING_PATH`` / ``GALLERY_PATH``

So no test can reach ~/Pictures/photoflow.toml, the real library, or the real index.

The load-bearing assertions:
  * a collection survives the index being **deleted and rebuilt from the files** — the
    0002 requirement, proved rather than assumed
  * resolving a collection returns rows and moves nothing
  * a stored query speaks exactly ``PhotoFilters``' vocabulary, mechanically
  * a hand-mangled file degrades (skip the bad entry) instead of taking the API down
"""

from __future__ import annotations

from dataclasses import fields as dataclass_fields
from pathlib import Path
from typing import Any, Dict

import pytest
from fastapi.testclient import TestClient
from PIL import Image

import photo_flow.api.routes_photos as photos_mod
from photo_flow import collections as store
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
        "trash": trash_dir,
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
def library(env) -> Dict[str, Path]:
    """
    f1 final 5★  ISO 200   f2 final 4★ ISO 800   f3 final 0★ ISO 3200   s1 staging 3★
    """
    _seed(env, filename="f1.JPG", rating=5, iso=200, date_taken="2026-01-10T09:00:00Z")
    _seed(env, filename="f2.JPG", rating=4, iso=800, date_taken="2026-02-11T09:00:00Z")
    _seed(env, filename="f3.JPG", rating=0, iso=3200, date_taken="2026-03-12T09:00:00Z")
    _seed(env, root="staging", filename="s1.JPG", rating=3, iso=640,
          date_taken="2026-04-13T09:00:00Z")
    return env


def _names(payload: Dict[str, Any]) -> set:
    return {item["filename"] for item in payload["items"]}


# ---------------------------------------------------------------------------
# The store
# ---------------------------------------------------------------------------


class TestStore:
    def test_no_file_is_an_empty_list_not_an_error(self, env):
        assert not env["toml"].exists()
        assert store.load() == []

    def test_create_writes_readable_toml(self, env):
        created = store.create("Keepers", {"root": "final", "rating_min": 4})

        text = env["toml"].read_text()
        assert "[[collections]]" in text
        assert 'name = "Keepers"' in text
        assert "[collections.query]" in text
        assert "rating_min = 4" in text

        (loaded,) = store.load()
        assert loaded.id == created.id == "keepers"
        assert loaded.query == {"root": "final", "rating_min": 4}
        assert loaded.created_at and loaded.updated_at

    def test_every_value_kind_round_trips(self, env):
        query = {
            "root": "staging",
            "rating": [4, 5],
            "aperture_max": 2.8,
            "has_sidecar": True,
            "include_rejected": False,
            "label": ["Red", "Blue"],
            "q": 'quote " and \\ backslash',
        }
        store.create("Everything", query)
        (loaded,) = store.load()
        assert loaded.query == query
        # bool must not degrade to 1/0 — TOML has both, and `has_sidecar = 1` is a
        # different filter from `has_sidecar = true`.
        assert loaded.query["has_sidecar"] is True
        assert loaded.query["include_rejected"] is False

    def test_ids_are_unique_and_stable_across_a_rename(self, env):
        first = store.create("Keepers", {})
        second = store.create("Keepers", {})
        assert (first.id, second.id) == ("keepers", "keepers-2")

        renamed = store.update(first.id, name="Portfolio")
        assert renamed.id == "keepers"
        assert renamed.name == "Portfolio"

    def test_a_name_with_no_ascii_still_gets_an_id(self, env):
        created = store.create("北海道", {})
        assert created.id == "collection"

    def test_blank_and_overlong_names_are_refused(self, env):
        with pytest.raises(store.CollectionError):
            store.create("   ", {})
        with pytest.raises(store.CollectionError):
            store.create("x" * (store.MAX_NAME_LENGTH + 1), {})
        assert store.load() == []

    def test_update_replaces_the_query_wholesale(self, env):
        created = store.create("Keepers", {"rating_min": 4, "root": "final"})
        updated = store.update(created.id, query={"rating_min": 5})
        assert updated.query == {"rating_min": 5}
        assert store.load()[0].query == {"rating_min": 5}

    def test_update_of_an_unknown_id_raises(self, env):
        with pytest.raises(store.CollectionError):
            store.update("nope", name="x")

    def test_delete_removes_only_that_collection(self, env):
        a = store.create("A", {})
        store.create("B", {})
        assert store.delete(a.id) is True
        assert [c.name for c in store.load()] == ["B"]
        assert store.delete(a.id) is False

    def test_a_malformed_file_reads_as_empty_rather_than_raising(self, env):
        env["toml"].write_text("[[collections]\nthis is not toml")
        assert store.load() == []

    def test_a_malformed_file_is_never_overwritten(self, env):
        """
        The one data-loss path this store could have: reading degrades to empty, so a
        write would have replaced the user's whole document with this module's region.
        Reproduces the F2 review case — comments, a foreign table and a real collection.
        """
        original = (
            "# my hand notes\n\n"
            '[library]\nroots = ["Final"]\n\n'
            '[[collections]]\nid = "keepers"\nname = "Keepers"\n\n'
            "[oops\n"
        )
        env["toml"].write_text(original)
        assert store.load() == []
        with pytest.raises(store.StoreUnreadable):
            store.create("New one", {"rating_min": 4})
        assert env["toml"].read_text() == original

    def test_an_unreadable_file_is_never_overwritten(self, env, monkeypatch):
        """Same rule for an OSError — nothing was read, so nothing may be written."""
        env["toml"].write_text('[[collections]]\nname = "Keepers"\n')
        original = env["toml"].read_text()
        monkeypatch.setattr(
            store.Path, "read_bytes", lambda _self: (_ for _ in ()).throw(OSError("nope"))
        )
        assert store.load() == []
        with pytest.raises(store.StoreUnreadable):
            store.save_all([])
        monkeypatch.undo()
        assert env["toml"].read_text() == original

    def test_a_nameless_entry_is_skipped_not_fatal(self, env):
        env["toml"].write_text(
            '[[collections]]\nid = "broken"\n\n'
            '[[collections]]\nid = "good"\nname = "Good"\n'
        )
        assert [c.id for c in store.load()] == ["good"]

    def test_unrepresentable_query_values_are_hidden_from_the_api_not_deleted(self, env):
        """
        A value the filter vocabulary cannot express is cleaned at the API boundary and
        kept on the record — so an unrelated rename does not quietly delete it.
        """
        env["toml"].write_text(
            '[[collections]]\nname = "Weird"\n\n'
            "[collections.query]\nrating_min = 4\nnested = { a = 1 }\n"
        )
        (loaded,) = store.load()
        assert loaded.query == {"rating_min": 4, "nested": {"a": 1}}
        assert loaded.to_dict()["query"] == {"rating_min": 4}

    def test_a_write_never_leaves_a_partial_file(self, env, monkeypatch):
        store.create("Keepers", {"rating_min": 4})
        before = env["toml"].read_text()

        def boom(*_args, **_kwargs):
            raise OSError("disk full")

        monkeypatch.setattr(store.os, "replace", boom)
        with pytest.raises(OSError):
            store.create("Second", {})

        assert env["toml"].read_text() == before
        # And no temp file was left behind next to it.
        assert [p.name for p in env["tmp"].glob(".photoflow.toml.*")] == []


# ---------------------------------------------------------------------------
# Vocabulary — one filter language, mechanically
# ---------------------------------------------------------------------------


class TestVocabulary:
    def test_query_spec_covers_exactly_the_photo_filters_dataclass(self):
        """
        The spec is derived, not restated — this pins that it stays derived.

        If someone reintroduces a hand-written list and forgets a field, a filter becomes
        unsavable while still being usable in the URL, and a saved query silently means
        something different from the view it was saved from.
        """
        assert set(photos_mod.QUERY_SPEC) == {
            f.name for f in dataclass_fields(photos_mod.PhotoFilters)
        }

    def test_strict_rejects_an_unknown_key(self):
        with pytest.raises(ValueError, match="Unknown filter"):
            photos_mod.filters_from_mapping({"colour": "red"}, strict=True)

    def test_lenient_drops_it_instead(self):
        filters = photos_mod.filters_from_mapping({"colour": "red", "rating_min": 4}, strict=False)
        assert filters.rating_min == 4

    def test_a_boolean_never_becomes_an_int(self):
        with pytest.raises(ValueError):
            photos_mod.filters_from_mapping({"iso_min": True}, strict=True)

    def test_enum_fields_are_checked(self):
        with pytest.raises(ValueError, match="root"):
            photos_mod.filters_from_mapping({"root": "archive"}, strict=True)

    def test_lists_and_scalars_are_not_interchangeable(self):
        with pytest.raises(ValueError, match="must be a list"):
            photos_mod.filters_from_mapping({"rating": 5}, strict=True)


# ---------------------------------------------------------------------------
# The API
# ---------------------------------------------------------------------------


class TestApi:
    def test_empty_listing_names_the_file(self, client, env):
        payload = client.get("/api/collections").json()
        assert payload["items"] == []
        assert payload["path"] == str(env["toml"])

    def test_create_list_rename_delete(self, client, library):
        created = client.post(
            "/api/collections", json={"name": "Keepers", "query": {"rating_min": 4}}
        ).json()
        assert created["id"] == "keepers"

        listed = client.get("/api/collections").json()["items"]
        assert [c["name"] for c in listed] == ["Keepers"]
        assert listed[0]["count"] is None  # not asked for

        renamed = client.patch("/api/collections/keepers", json={"name": "Portfolio"}).json()
        assert renamed["name"] == "Portfolio" and renamed["id"] == "keepers"

        assert client.delete("/api/collections/keepers").json() == {"deleted": True}
        assert client.get("/api/collections").json()["items"] == []

    def test_counts_are_opt_in_and_correct(self, client, library):
        client.post("/api/collections", json={"name": "Keepers", "query": {"rating_min": 4}})
        client.post("/api/collections", json={"name": "Staging", "query": {"root": "staging"}})

        items = client.get("/api/collections", params={"with_counts": "true"}).json()["items"]
        by_name = {c["name"]: c["count"] for c in items}
        assert by_name == {"Keepers": 2, "Staging": 1}

    def test_a_malformed_file_is_a_409_not_a_truncation(self, client, env):
        """The API surface of the refusal: the button fails loudly, the file survives."""
        original = '# notes\n\n[library]\nroots = ["Final"]\n\n[oops\n'
        env["toml"].write_text(original)
        response = client.post("/api/collections", json={"name": "X", "query": {}})
        assert response.status_code == 409
        assert "could not be parsed" in response.json()["detail"]
        assert env["toml"].read_text() == original

    def test_a_bad_query_is_a_400_and_nothing_is_stored(self, client, env):
        response = client.post(
            "/api/collections", json={"name": "Bad", "query": {"not_a_filter": 1}}
        )
        assert response.status_code == 400
        assert "not_a_filter" in response.json()["detail"]
        assert store.load() == []

    def test_a_blank_name_is_a_400(self, client, env):
        assert client.post("/api/collections", json={"name": "  ", "query": {}}).status_code == 400

    def test_unknown_ids_are_404(self, client, env):
        assert client.patch("/api/collections/nope", json={"name": "x"}).status_code == 404
        assert client.delete("/api/collections/nope").status_code == 404
        assert client.get("/api/collections/nope/photos").status_code == 404

    def test_resolve_returns_the_same_rows_as_the_equivalent_filter(self, client, library):
        client.post("/api/collections", json={"name": "Keepers", "query": {"rating_min": 4}})

        resolved = client.get("/api/collections/keepers/photos").json()
        direct = client.get("/api/photos", params={"rating_min": 4}).json()

        assert _names(resolved) == _names(direct) == {"f1.JPG", "f2.JPG"}
        assert resolved["total"] == direct["total"] == 2

    def test_resolve_honours_sort_and_order_without_storing_them(self, client, library):
        client.post("/api/collections", json={"name": "All final", "query": {"root": "final"}})
        descending = client.get(
            "/api/collections/all-final/photos", params={"sort": "rating", "order": "desc"}
        ).json()
        assert [i["rating"] for i in descending["items"]] == [5, 4, 0]
        # …and the stored query is still only the filter.
        assert store.get("all-final").query == {"root": "final"}

    def test_resolve_rejects_an_unwhitelisted_sort(self, client, library):
        client.post("/api/collections", json={"name": "All", "query": {}})
        assert client.get(
            "/api/collections/all/photos", params={"sort": "path); DROP TABLE photos--"}
        ).status_code == 400

    def test_resolve_skips_a_hand_written_filter_it_cannot_use(self, client, library, env):
        """A typo in the file narrows the result set by less, never breaks the screen."""
        env["toml"].write_text(
            '[[collections]]\nid = "typo"\nname = "Typo"\n\n'
            '[collections.query]\nrating_min = 4\ncamrea_model = "X-T4"\n'
        )
        payload = client.get("/api/collections/typo/photos").json()
        assert _names(payload) == {"f1.JPG", "f2.JPG"}

    def test_resolving_moves_nothing(self, client, library, env):
        """The whole premise: a collection is a query, not a place."""
        for name in ("f1.JPG", "f2.JPG", "f3.JPG"):
            Image.new("RGB", (24, 16), (70, 110, 150)).save(env["final"] / name, "JPEG")
        before = sorted(p.name for p in env["final"].iterdir())

        client.post("/api/collections", json={"name": "Keepers", "query": {"rating_min": 4}})
        client.get("/api/collections/keepers/photos")
        client.delete("/api/collections/keepers")

        assert sorted(p.name for p in env["final"].iterdir()) == before


# ---------------------------------------------------------------------------
# The 0002 requirement
# ---------------------------------------------------------------------------


class TestSurvivesTheIndex:
    def test_a_collection_survives_deleting_and_rebuilding_the_index(self, client, env):
        """
        `0002`: the index is a disposable cache. Prove it for collections rather than
        assuming it — delete the database outright, rebuild it from the files, and check
        the collection is still there AND still resolves.
        """
        for name in ("f1.JPG", "f2.JPG"):
            Image.new("RGB", (24, 16), (70, 110, 150)).save(env["final"] / name, "JPEG")

        indexer_mod.reindex()

        client.post("/api/collections", json={"name": "Everything", "query": {"root": "final"}})
        before = client.get("/api/collections", params={"with_counts": "true"}).json()
        assert before["items"][0]["count"] == 2

        # Nuke the cache, WAL and all.
        for suffix in ("", "-wal", "-shm"):
            Path(str(env["db"]) + suffix).unlink(missing_ok=True)
        assert not env["db"].exists()

        indexer_mod.reindex()

        after = client.get("/api/collections", params={"with_counts": "true"}).json()
        assert after["items"][0]["id"] == before["items"][0]["id"]
        assert after["items"][0]["query"] == {"root": "final"}
        assert after["items"][0]["count"] == before["items"][0]["count"] == 2
        assert _names(client.get("/api/collections/everything/photos").json()) == {
            "f1.JPG",
            "f2.JPG",
        }

    def test_collections_list_answers_with_no_index_at_all(self, client, env):
        """The list itself must not depend on the database — only the counts do."""
        store.create("Keepers", {"rating_min": 4})
        for suffix in ("", "-wal", "-shm"):
            Path(str(env["db"]) + suffix).unlink(missing_ok=True)

        payload = client.get("/api/collections").json()
        assert [c["name"] for c in payload["items"]] == ["Keepers"]
