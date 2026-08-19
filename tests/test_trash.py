"""
Tests for photo_flow.trash — the soft-delete path for culled photos.

Everything runs against tmp_path roots: config.CULL_ROOTS and config.TRASH_PATH are
monkeypatched, so no real Final/Staging folder is ever touched. The module reads both
through the config module at call time precisely so this is possible.

The safety-critical invariants under test:
  * the .photo-edit sidecar travels with its JPG (it is the only copy that exists)
  * a restore round-trips both files and refuses an occupied original path
  * purge honours the retention window and keys off trashed_at, NEVER file mtime
  * a path outside the cull roots is rejected before anything moves
  * dry_run mutates nothing on disk or in the database
  * an entry directory whose row was lost is adopted, listed and restorable again —
    and when its origin cannot be reconstructed it is surfaced but never deleted
  * a failed rollback is reported as a failed rollback, never as "restored in place"
"""

import os
import sqlite3
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from photo_flow import config, trash as trash_mod
from photo_flow.index.db import get_db


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture()
def roots(tmp_path, monkeypatch):
    """Point CULL_ROOTS and TRASH_PATH at tmp_path and return the three directories."""
    final_dir = tmp_path / "Final"
    staging_dir = tmp_path / "Staging"
    trash_dir = tmp_path / ".photoflow-trash"
    final_dir.mkdir()
    staging_dir.mkdir()

    monkeypatch.setattr(config, "CULL_ROOTS", {"final": final_dir, "staging": staging_dir})
    monkeypatch.setattr(config, "TRASH_PATH", trash_dir)

    return {"final": final_dir, "staging": staging_dir, "trash": trash_dir}


@pytest.fixture()
def conn(tmp_path):
    connection = get_db(tmp_path / "index.db")
    yield connection
    connection.close()


def _make_photo(directory: Path, name: str, with_sidecar: bool = False) -> Path:
    """Create a fixture JPG (and optionally its .photo-edit sidecar) with real bytes."""
    jpg = directory / name
    jpg.write_bytes(b"jpeg-bytes" * 10)
    if with_sidecar:
        jpg.with_suffix(config.EDIT_SIDECAR_SUFFIX).write_bytes(b"edit-history" * 10)
    return jpg


def _trash_rows(conn: sqlite3.Connection) -> list:
    return conn.execute("SELECT * FROM trash ORDER BY id").fetchall()


class _InsertFailingConnection:
    """Connection proxy that blows up on the `trash` INSERT (sqlite3.Connection is immutable)."""

    def __init__(self, real: sqlite3.Connection) -> None:
        self._real = real

    def execute(self, sql: str, *args, **kwargs):
        if sql.strip().upper().startswith("INSERT INTO TRASH"):
            raise sqlite3.OperationalError("simulated insert failure")
        return self._real.execute(sql, *args, **kwargs)

    def __getattr__(self, name: str):
        return getattr(self._real, name)


def _entry_key(days_ago: float = 0.0) -> str:
    """Build a directory name in the on-disk entry-key format, optionally backdated."""
    when = datetime.now(timezone.utc) - timedelta(days=days_ago)
    return f"{when.strftime('%Y%m%dT%H%M%S')}-0badc0de"


def _orphan_entry(trash_root: Path, days_ago: float = 0.0) -> Path:
    """Create a trash entry directory on disk that no `trash` row references."""
    entry_dir = trash_root / _entry_key(days_ago)
    entry_dir.mkdir(parents=True)
    return entry_dir


def _index_photo(conn: sqlite3.Connection, path: Path, rating=None) -> None:
    """Seed a `photos` row so the orphan sweep can reconstruct an original path."""
    conn.execute(
        "INSERT INTO photos (path, filename, size, mtime, rating, in_final, published, indexed_at)"
        " VALUES (?, ?, ?, ?, ?, 1, 0, ?)",
        (str(path.resolve()), path.name, 100, 1000.0, rating, "2026-01-01T00:00:00+00:00"),
    )
    conn.commit()


def _backdate(conn: sqlite3.Connection, entry_id: int, days: float) -> None:
    """Rewrite an entry's trashed_at to `days` in the past."""
    when = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()
    conn.execute("UPDATE trash SET trashed_at = ? WHERE id = ?", (when, entry_id))
    conn.commit()


# ---------------------------------------------------------------------------
# trash_photos
# ---------------------------------------------------------------------------

class TestTrashPhotos:
    def test_moves_photo_and_records_entry(self, roots, conn):
        jpg = _make_photo(roots["final"], "a.JPG")

        result = trash_mod.trash_photos([jpg], conn=conn)

        assert result["trashed"] == 1
        assert result["errors"] == 0
        assert not jpg.exists()

        rows = _trash_rows(conn)
        assert len(rows) == 1
        assert rows[0]["filename"] == "a.JPG"
        assert rows[0]["root"] == "final"
        assert Path(rows[0]["trashed_path"]).is_file()
        # Layout: TRASH_PATH / <entry-key> / <original filename>
        assert Path(rows[0]["trashed_path"]).parent.parent == roots["trash"]

    def test_sidecar_travels_with_the_jpg(self, roots, conn):
        jpg = _make_photo(roots["final"], "b.JPG", with_sidecar=True)
        sidecar = jpg.with_suffix(config.EDIT_SIDECAR_SUFFIX)

        result = trash_mod.trash_photos([jpg], conn=conn)

        assert result["trashed"] == 1
        assert not jpg.exists()
        assert not sidecar.exists(), "the sidecar must never be left behind in Final"

        row = _trash_rows(conn)[0]
        assert row["sidecar_original_path"] == str(sidecar.resolve())
        trashed_sidecar = Path(row["sidecar_trashed_path"])
        assert trashed_sidecar.is_file()
        assert trashed_sidecar.read_bytes() == b"edit-history" * 10
        # Both files land in the same entry directory.
        assert trashed_sidecar.parent == Path(row["trashed_path"]).parent

    def test_staging_photo_is_accepted(self, roots, conn):
        jpg = _make_photo(roots["staging"], "s.JPG")

        result = trash_mod.trash_photos([jpg], conn=conn)

        assert result["trashed"] == 1
        assert _trash_rows(conn)[0]["root"] == "staging"

    def test_path_outside_cull_roots_is_rejected(self, roots, conn, tmp_path):
        outsider = tmp_path / "elsewhere.JPG"
        outsider.write_bytes(b"nope")

        result = trash_mod.trash_photos([outsider], conn=conn)

        assert result["trashed"] == 0
        assert result["errors"] == 1
        assert "outside the cull roots" in result["messages"][0]
        assert outsider.exists(), "a rejected path must not be touched"
        assert _trash_rows(conn) == []

    def test_traversal_out_of_a_root_is_rejected(self, roots, conn, tmp_path):
        secret = tmp_path / "secret.JPG"
        secret.write_bytes(b"nope")
        traversal = roots["final"] / ".." / "secret.JPG"

        result = trash_mod.trash_photos([traversal], conn=conn)

        assert result["errors"] == 1
        assert secret.exists()

    def test_missing_file_is_an_error_not_a_crash(self, roots, conn):
        result = trash_mod.trash_photos([roots["final"] / "ghost.JPG"], conn=conn)

        assert result["trashed"] == 0
        assert result["errors"] == 1
        assert _trash_rows(conn) == []

    def test_dry_run_mutates_nothing(self, roots, conn):
        jpg = _make_photo(roots["final"], "dry.JPG", with_sidecar=True)
        sidecar = jpg.with_suffix(config.EDIT_SIDECAR_SUFFIX)

        result = trash_mod.trash_photos([jpg], dry_run=True, conn=conn)

        assert result["trashed"] == 0
        assert result["errors"] == 0
        assert len(result["entries"]) == 1
        assert result["entries"][0]["id"] is None
        assert result["entries"][0]["filename"] == "dry.JPG"

        assert jpg.exists()
        assert sidecar.exists()
        assert not roots["trash"].exists()
        assert _trash_rows(conn) == []

    def test_index_row_marked_absent(self, roots, conn):
        jpg = _make_photo(roots["final"], "indexed.JPG")
        conn.execute(
            "INSERT INTO photos (path, filename, size, mtime, rating, in_final, published, indexed_at)"
            " VALUES (?, ?, ?, ?, ?, 1, 0, ?)",
            (str(jpg.resolve()), "indexed.JPG", 100, 1000.0, 5, "2026-01-01T00:00:00+00:00"),
        )
        conn.commit()

        result = trash_mod.trash_photos([jpg], conn=conn)

        assert result["entries"][0]["rating"] == 5
        row = conn.execute("SELECT * FROM photos WHERE filename = 'indexed.JPG'").fetchone()
        assert row["in_final"] == 0
        if "present" in row.keys():
            assert row["present"] == 0

    def test_same_filename_twice_does_not_collide(self, roots, conn):
        first = _make_photo(roots["final"], "dup.JPG")
        trash_mod.trash_photos([first], conn=conn)
        second = _make_photo(roots["final"], "dup.JPG")
        trash_mod.trash_photos([second], conn=conn)

        rows = _trash_rows(conn)
        assert len(rows) == 2
        assert rows[0]["trashed_path"] != rows[1]["trashed_path"]
        assert all(Path(r["trashed_path"]).is_file() for r in rows)

    def test_raw_is_left_alone(self, roots, conn):
        jpg = _make_photo(roots["final"], "withraw.JPG")
        raw = roots["final"] / "withraw.RAF"
        raw.write_bytes(b"raw-bytes")

        trash_mod.trash_photos([jpg], conn=conn)

        assert raw.exists(), "trashing a JPG must never touch its RAW"

    def test_failed_insert_moves_files_back(self, roots, conn):
        jpg = _make_photo(roots["final"], "rollback.JPG", with_sidecar=True)
        sidecar = jpg.with_suffix(config.EDIT_SIDECAR_SUFFIX)

        result = trash_mod.trash_photos([jpg], conn=_InsertFailingConnection(conn))

        assert result["trashed"] == 0
        assert result["errors"] == 1
        assert jpg.exists(), "a lost row must never mean a lost photo"
        assert sidecar.exists(), "a lost row must never mean a lost edit history"


# ---------------------------------------------------------------------------
# restore
# ---------------------------------------------------------------------------

class TestRestore:
    def test_round_trips_photo_and_sidecar(self, roots, conn):
        jpg = _make_photo(roots["final"], "r.JPG", with_sidecar=True)
        sidecar = jpg.with_suffix(config.EDIT_SIDECAR_SUFFIX)
        original_bytes = jpg.read_bytes()

        trashed = trash_mod.trash_photos([jpg], conn=conn)
        entry_id = trashed["entries"][0]["id"]

        result = trash_mod.restore([entry_id], conn=conn)

        assert result["restored"] == 1
        assert result["errors"] == 0
        assert jpg.is_file()
        assert jpg.read_bytes() == original_bytes
        assert sidecar.is_file()
        assert _trash_rows(conn) == []

    def test_refuses_when_original_is_occupied(self, roots, conn):
        jpg = _make_photo(roots["final"], "occupied.JPG")
        trashed = trash_mod.trash_photos([jpg], conn=conn)
        entry_id = trashed["entries"][0]["id"]

        # Something else takes the original path back.
        squatter = _make_photo(roots["final"], "occupied.JPG")
        squatter.write_bytes(b"different-content")

        result = trash_mod.restore([entry_id], conn=conn)

        assert result["restored"] == 0
        assert result["errors"] == 1
        assert "occupied" in result["messages"][0]
        assert squatter.read_bytes() == b"different-content", "the squatter must not be overwritten"
        assert len(_trash_rows(conn)) == 1, "the entry stays in the trash"

    def test_refuses_when_sidecar_path_is_occupied(self, roots, conn):
        jpg = _make_photo(roots["final"], "sc.JPG", with_sidecar=True)
        trashed = trash_mod.trash_photos([jpg], conn=conn)
        entry_id = trashed["entries"][0]["id"]

        squatter = roots["final"] / f"sc{config.EDIT_SIDECAR_SUFFIX}"
        squatter.write_bytes(b"other-history")

        result = trash_mod.restore([entry_id], conn=conn)

        assert result["restored"] == 0
        assert result["errors"] == 1
        assert not jpg.exists(), "the JPG must not be restored without its edit history"
        assert squatter.read_bytes() == b"other-history"

    def test_unknown_id_is_an_error(self, roots, conn):
        result = trash_mod.restore([4242], conn=conn)
        assert result["restored"] == 0
        assert result["errors"] == 1

    def test_restore_marks_index_present(self, roots, conn):
        jpg = _make_photo(roots["final"], "back.JPG")
        conn.execute(
            "INSERT INTO photos (path, filename, size, mtime, in_final, published, indexed_at)"
            " VALUES (?, ?, ?, ?, 1, 0, ?)",
            (str(jpg.resolve()), "back.JPG", 100, 1000.0, "2026-01-01T00:00:00+00:00"),
        )
        conn.commit()

        entry_id = trash_mod.trash_photos([jpg], conn=conn)["entries"][0]["id"]
        trash_mod.restore([entry_id], conn=conn)

        row = conn.execute("SELECT * FROM photos WHERE filename = 'back.JPG'").fetchone()
        assert row["in_final"] == 1
        if "present" in row.keys():
            assert row["present"] == 1


# ---------------------------------------------------------------------------
# list_trash / trash_stats
# ---------------------------------------------------------------------------

class TestListAndStats:
    def test_list_reports_age_and_purgeable(self, roots, conn):
        jpg = _make_photo(roots["final"], "old.JPG")
        entry_id = trash_mod.trash_photos([jpg], conn=conn)["entries"][0]["id"]
        _backdate(conn, entry_id, days=45)

        entries = trash_mod.list_trash(conn=conn)

        assert len(entries) == 1
        assert entries[0]["purgeable"] is True
        assert entries[0]["age_days"] > 44
        assert entries[0]["exists"] is True

    def test_stats_counts_bytes_and_purgeable(self, roots, conn):
        fresh = _make_photo(roots["final"], "fresh.JPG")
        stale = _make_photo(roots["final"], "stale.JPG")
        trash_mod.trash_photos([fresh], conn=conn)
        stale_id = trash_mod.trash_photos([stale], conn=conn)["entries"][0]["id"]
        _backdate(conn, stale_id, days=90)

        stats = trash_mod.trash_stats(conn=conn)

        assert stats["count"] == 2
        assert stats["purgeable"] == 1
        assert stats["bytes"] == 2 * len(b"jpeg-bytes" * 10)
        assert stats["oldest_iso"] is not None

    def test_empty_trash(self, roots, conn):
        assert trash_mod.list_trash(conn=conn) == []
        assert trash_mod.trash_stats(conn=conn)["count"] == 0


# ---------------------------------------------------------------------------
# purge
# ---------------------------------------------------------------------------

class TestPurge:
    def test_respects_the_age_threshold(self, roots, conn):
        fresh = _make_photo(roots["final"], "young.JPG")
        stale = _make_photo(roots["final"], "aged.JPG")
        fresh_id = trash_mod.trash_photos([fresh], conn=conn)["entries"][0]["id"]
        stale_id = trash_mod.trash_photos([stale], conn=conn)["entries"][0]["id"]
        _backdate(conn, stale_id, days=31)

        result = trash_mod.purge(older_than_days=30, conn=conn)

        assert result["purged"] == 1
        assert result["errors"] == 0

        remaining = _trash_rows(conn)
        assert len(remaining) == 1
        assert remaining[0]["id"] == fresh_id
        assert Path(remaining[0]["trashed_path"]).is_file()

    def test_ignores_file_mtime(self, roots, conn):
        """A two-year-old capture trashed 30 seconds ago must survive the purge.

        Photos keep their capture-time mtime forever. Keying retention off mtime instead of
        trashed_at is the exact bug that once shipped in the rclone backup prune.
        """
        jpg = _make_photo(roots["final"], "ancient.JPG")
        two_years_ago = time.time() - (730 * 86400)
        os.utime(jpg, (two_years_ago, two_years_ago))

        entry_id = trash_mod.trash_photos([jpg], conn=conn)["entries"][0]["id"]
        trashed_path = Path(_trash_rows(conn)[0]["trashed_path"])
        # The moved file keeps the ancient mtime; only trashed_at is fresh.
        assert trashed_path.stat().st_mtime == pytest.approx(two_years_ago, abs=2)

        result = trash_mod.purge(older_than_days=30, conn=conn)

        assert result["purged"] == 0
        assert trashed_path.is_file()
        assert [r["id"] for r in _trash_rows(conn)] == [entry_id]

    def test_dry_run_mutates_nothing(self, roots, conn):
        jpg = _make_photo(roots["final"], "purge-dry.JPG")
        entry_id = trash_mod.trash_photos([jpg], conn=conn)["entries"][0]["id"]
        _backdate(conn, entry_id, days=99)
        trashed_path = Path(_trash_rows(conn)[0]["trashed_path"])

        result = trash_mod.purge(older_than_days=30, dry_run=True, conn=conn)

        assert result["purged"] == 1
        assert result["bytes"] == len(b"jpeg-bytes" * 10)
        assert trashed_path.is_file()
        assert len(_trash_rows(conn)) == 1

    def test_purge_removes_sidecar_and_entry_dir(self, roots, conn):
        jpg = _make_photo(roots["final"], "gone.JPG", with_sidecar=True)
        entry_id = trash_mod.trash_photos([jpg], conn=conn)["entries"][0]["id"]
        row = _trash_rows(conn)[0]
        entry_dir = Path(row["trashed_path"]).parent
        _backdate(conn, entry_id, days=31)

        result = trash_mod.purge(older_than_days=30, conn=conn)

        assert result["purged"] == 1
        assert result["bytes"] == len(b"jpeg-bytes" * 10) + len(b"edit-history" * 10)
        assert not entry_dir.exists()
        assert _trash_rows(conn) == []

    def test_unparseable_timestamp_is_never_purged(self, roots, conn):
        jpg = _make_photo(roots["final"], "broken-ts.JPG")
        entry_id = trash_mod.trash_photos([jpg], conn=conn)["entries"][0]["id"]
        conn.execute("UPDATE trash SET trashed_at = 'not-a-date' WHERE id = ?", (entry_id,))
        conn.commit()

        result = trash_mod.purge(older_than_days=0, conn=conn)

        assert result["purged"] == 0
        assert result["errors"] == 1
        assert len(_trash_rows(conn)) == 1


# ---------------------------------------------------------------------------
# Orphan adoption — files in the trash whose row never landed
# ---------------------------------------------------------------------------

class TestOrphanAdoption:
    """A trash entry with no row is invisible: nothing lists it, restore cannot reach it,
    purge skips it — while the photo is already gone from Final and the next backup retires
    the homelab copy. The sweep in list_trash/trash_stats is what closes that hole."""

    def test_orphan_is_adopted_and_restorable(self, roots, conn):
        jpg = _make_photo(roots["final"], "lost-row.JPG", with_sidecar=True)
        sidecar = jpg.with_suffix(config.EDIT_SIDECAR_SUFFIX)
        _index_photo(conn, jpg, rating=4)

        trash_mod.trash_photos([jpg], conn=conn)
        trashed_path = Path(_trash_rows(conn)[0]["trashed_path"])
        # Simulate a kill between the rename and the commit: files moved, row never written.
        conn.execute("DELETE FROM trash")
        conn.commit()
        assert trashed_path.is_file()

        entries = trash_mod.list_trash(conn=conn)

        assert len(entries) == 1, "an orphaned entry directory must surface in the listing"
        entry = entries[0]
        assert entry["unrecoverable"] is False
        assert entry["original_path"] == str(jpg.resolve())
        assert entry["root"] == "final"
        assert entry["sidecar_trashed_path"] is not None

        result = trash_mod.restore([entry["id"]], conn=conn)

        assert result["restored"] == 1
        assert result["errors"] == 0
        assert jpg.is_file(), "the adopted entry must be restorable again"
        assert sidecar.is_file()

    def test_adoption_is_idempotent(self, roots, conn):
        jpg = _make_photo(roots["final"], "once.JPG")
        _index_photo(conn, jpg)
        trash_mod.trash_photos([jpg], conn=conn)
        conn.execute("DELETE FROM trash")
        conn.commit()

        first = trash_mod.list_trash(conn=conn)
        second = trash_mod.list_trash(conn=conn)

        assert len(first) == 1
        assert len(second) == 1
        assert first[0]["id"] == second[0]["id"]

    def test_stats_also_sweep(self, roots, conn):
        jpg = _make_photo(roots["final"], "seen-by-stats.JPG")
        _index_photo(conn, jpg)
        trash_mod.trash_photos([jpg], conn=conn)
        conn.execute("DELETE FROM trash")
        conn.commit()

        stats = trash_mod.trash_stats(conn=conn)

        assert stats["count"] == 1
        assert stats["bytes"] == len(b"jpeg-bytes" * 10)
        assert stats["unrecoverable"] == 0

    def test_age_comes_from_the_entry_directory_name(self, roots, conn):
        entry_dir = _orphan_entry(roots["trash"], days_ago=40)
        (entry_dir / "aged.JPG").write_bytes(b"jpeg-bytes" * 10)
        _index_photo(conn, roots["final"] / "aged.JPG")

        entry = trash_mod.list_trash(conn=conn)[0]

        assert 39 < entry["age_days"] < 41, "retention must key off when it was trashed"
        assert entry["purgeable"] is True

    def test_unadoptable_entry_is_surfaced_but_never_deleted(self, roots, conn):
        entry_dir = _orphan_entry(roots["trash"], days_ago=400)
        stray = entry_dir / "no-idea-where-this-came-from.JPG"
        stray.write_bytes(b"jpeg-bytes" * 10)

        entries = trash_mod.list_trash(conn=conn)

        assert len(entries) == 1
        entry = entries[0]
        assert entry["unrecoverable"] is True
        assert entry["filename"] == stray.name
        assert entry["trashed_path"] == str(stray)
        assert entry["purgeable"] is False, "no known origin means no automatic deletion"

        result = trash_mod.purge(older_than_days=0, conn=conn)

        assert result["purged"] == 0
        assert stray.is_file(), "an unadoptable entry is irreplaceable — purge must not touch it"
        assert len(_trash_rows(conn)) == 1
        assert trash_mod.trash_stats(conn=conn)["unrecoverable"] == 1

    def test_ambiguous_filename_is_not_guessed(self, roots, conn):
        entry_dir = _orphan_entry(roots["trash"])
        (entry_dir / "both.JPG").write_bytes(b"jpeg-bytes" * 10)
        _index_photo(conn, roots["final"] / "both.JPG")
        _index_photo(conn, roots["staging"] / "both.JPG")

        entry = trash_mod.list_trash(conn=conn)[0]

        assert entry["unrecoverable"] is True, "two candidate roots must never be guessed between"

    def test_restore_of_an_unadoptable_entry_is_honest(self, roots, conn):
        entry_dir = _orphan_entry(roots["trash"])
        stray = entry_dir / "handmade.JPG"
        stray.write_bytes(b"jpeg-bytes" * 10)
        entry_id = trash_mod.list_trash(conn=conn)[0]["id"]

        result = trash_mod.restore([entry_id], conn=conn)

        assert result["restored"] == 0
        assert result["errors"] == 1
        assert str(entry_dir) in result["messages"][0]
        assert stray.is_file()

    def test_sidecar_only_orphan_is_surfaced(self, roots, conn):
        entry_dir = _orphan_entry(roots["trash"])
        sidecar = entry_dir / f"history{config.EDIT_SIDECAR_SUFFIX}"
        sidecar.write_bytes(b"edit-history" * 10)

        entries = trash_mod.list_trash(conn=conn)

        assert len(entries) == 1, "an orphaned edit history must never go unlisted"
        assert entries[0]["unrecoverable"] is True

        trash_mod.purge(older_than_days=0, conn=conn)

        assert sidecar.is_file(), "the only copy of an edit history is never auto-deleted"

    def test_empty_entry_directory_is_not_adopted(self, roots, conn):
        _orphan_entry(roots["trash"])

        assert trash_mod.list_trash(conn=conn) == []

    def test_appledouble_forks_are_ignored(self, roots, conn):
        entry_dir = _orphan_entry(roots["trash"])
        (entry_dir / "._ghost.JPG").write_bytes(b"resource-fork")

        assert trash_mod.list_trash(conn=conn) == []


# ---------------------------------------------------------------------------
# Rollback honesty
# ---------------------------------------------------------------------------

class TestRollbackReporting:
    def test_failed_rollback_is_reported_as_such(self, roots, conn, monkeypatch):
        """A rollback that fails must not be reported as "file restored in place".

        The file is still in the trash with no row behind it — the caller has to be told
        where it actually is, and the adoption sweep has to pick it up afterwards.
        """
        jpg = _make_photo(roots["final"], "stuck.JPG")
        _index_photo(conn, jpg)

        real_move = trash_mod._move_file

        def move_only_into_trash(src: Path, dst: Path) -> None:
            if roots["trash"] not in Path(dst).parents:
                raise OSError("simulated rollback failure")
            real_move(src, dst)

        monkeypatch.setattr(trash_mod, "_move_file", move_only_into_trash)

        result = trash_mod.trash_photos([jpg], conn=_InsertFailingConnection(conn))

        assert result["errors"] == 1
        message = result["messages"][0]
        assert "could NOT be restored" in message
        assert "restored in place" not in message
        assert not jpg.exists(), "the file really is still in the trash"

        # Restore only the move helper — monkeypatch.undo() would also revert the tmp_path
        # roots and point the sweep at the real ~/Pictures trash.
        monkeypatch.setattr(trash_mod, "_move_file", real_move)
        entries = trash_mod.list_trash(conn=conn)
        assert len(entries) == 1, "the stranded files must be adopted, not left invisible"
        assert entries[0]["original_path"] == str(jpg.resolve())

    def test_failed_sidecar_rollback_is_reported_as_such(self, roots, conn, monkeypatch):
        jpg = _make_photo(roots["final"], "pair.JPG", with_sidecar=True)
        real_move = trash_mod._move_file
        calls = {"n": 0}

        def fail_sidecar_and_rollback(src: Path, dst: Path) -> None:
            calls["n"] += 1
            if calls["n"] == 1:
                real_move(src, dst)  # the JPG lands in the trash
                return
            raise OSError("simulated sidecar + rollback failure")

        monkeypatch.setattr(trash_mod, "_move_file", fail_sidecar_and_rollback)

        result = trash_mod.trash_photos([jpg], conn=conn)

        assert result["errors"] == 1
        assert "could NOT be restored" in result["messages"][0]
        assert not jpg.exists()
        assert jpg.with_suffix(config.EDIT_SIDECAR_SUFFIX).is_file()
