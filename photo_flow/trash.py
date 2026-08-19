"""
Soft-delete ("trash") for culled photos.

A culled photo is **never unlinked** — it is *moved* into ``TRASH_PATH`` and recorded in
the ``trash`` table of the metadata index, so it can be restored until it is explicitly
purged. Because ``TRASH_PATH`` lives under ``~/Pictures`` (same filesystem as Final and
Staging), the move is an atomic ``rename`` — instant, no copy, no partial state.

Safety contract (all of it load-bearing — this module deletes irreplaceable photos):

1. **Move, never unlink.** ``trash_photos`` only ever relocates files. Nothing is removed
   from disk until ``purge`` runs, and ``purge`` refuses anything inside the retention
   window.
2. **The sidecar travels.** ``<stem>.photo-edit`` is the *only* copy of Photomator's
   re-editable edit history — there is no second copy anywhere. It is moved into the same
   trash entry as its JPG, restored with it, and never deleted independently.
3. **Move first, record second.** The DB row is inserted only after every file has landed
   in the trash entry. If the insert fails, the files are moved back to their original
   locations and the operation is reported as an error — a lost row must never mean a lost
   photo. The move-back is best-effort, so it reports whether it worked and the caller says
   so honestly: claiming "restored in place" over a file still sitting in the trash is how a
   photo gets written off.
3b. **Orphaned entries are adopted, never reaped.** Steps can still be lost between the
   rename and the commit (SIGKILL, a failed insert whose rollback also failed). The files
   then sit in a dot-prefixed directory nothing lists, while Final no longer holds them —
   and the next backup retires the homelab copy into remote trash. ``adopt_orphan_entries``
   sweeps ``TRASH_PATH`` for entry directories no row references and writes the missing row,
   reconstructing ``original_path`` from the index when the filename resolves unambiguously.
   When it does not, the entry is still recorded — with ``UNKNOWN_ORIGIN_PREFIX`` in
   ``original_path`` — so it is visible in the CLI and the panel. Such an entry is never
   restored automatically and **never purged**: the files are irreplaceable and only a human
   can say where they belong. The sweep runs at the top of ``list_trash`` and ``trash_stats``.
4. **Retention keys off ``trashed_at``, never file mtime.** A JPG/RAW keeps its
   capture-time mtime forever, so an mtime sweep would nuke a freshly-trashed folder of
   two-year-old photos on the spot. That exact bug was fixed once already in the rclone
   backup prune (v0.4.2); it is not coming back here.
5. **Roots are validated.** Every incoming path is resolved and must live under one of
   ``config.CULL_ROOTS``. Anything else is rejected before a single byte moves.

RAW policy
----------
Trashing a JPG deliberately leaves its ``.RAF`` alone, and ``trash_photos`` must never
touch a RAW itself. For the promised restore to be worth anything, the RAW has to outlive
the trash entry — so ``compute_raw_keep_bases`` (workflow.py) unions the bases of every
JPG **inside ``TRASH_PATH``** into its keep-set, alongside Final and Staging.

That union is load-bearing, not defensive: ``finalize_staging`` step 4 unlinks orphaned
RAWs with no preview and no confirmation, so without it the first ``photoflow finalize``
after a culling session would silently destroy the RAWs of every still-restorable photo.
A trashed photo's RAW becomes an orphan only once ``purge`` removes the entry from disk —
at which point the JPG is gone for good and the RAW has nothing left to pair with.

Entry layout on disk::

    TRASH_PATH / <entry-key> / <original filename>
    TRASH_PATH / <entry-key> / <original stem>.photo-edit

``<entry-key>`` is a generated ``<UTC timestamp>-<8 hex>`` string rather than the DB row
id: the id only exists after the insert, and the insert only happens after the move
(rule 3 above). The key is unique per call, so re-trashing the same filename never
collides.
"""

import logging
import shutil
import sqlite3
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Set, Tuple

from photo_flow import config
from photo_flow.config import EDIT_SIDECAR_SUFFIX, TRASH_RETENTION_DAYS
from photo_flow.index.db import get_db

logger = logging.getLogger(__name__)

# Written into `original_path` when an adopted orphan's origin cannot be reconstructed.
# Deliberately not a valid path: restore refuses such an entry with a message pointing at
# the trash directory, and purge never deletes one.
UNKNOWN_ORIGIN_PREFIX = "<unknown-origin>"
UNKNOWN_ROOT = "unknown"


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _now_utc() -> datetime:
    return datetime.now(timezone.utc)


def _now_iso() -> str:
    return _now_utc().isoformat()


def _parse_iso(value: str) -> Optional[datetime]:
    """
    Parse an ISO8601 timestamp written by this module into an aware UTC datetime.

    Args:
        value: ISO8601 string, with or without an explicit offset

    Returns:
        Timezone-aware datetime in UTC, or None if the string is unparseable
    """
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except (ValueError, AttributeError):
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _has_column(conn: sqlite3.Connection, table: str, column: str) -> bool:
    """Return True if `table` has `column` (used to stay compatible with schema v1)."""
    try:
        rows = conn.execute(f"PRAGMA table_info({table})").fetchall()
    except sqlite3.Error:
        return False
    return any(row[1] == column for row in rows)


def _resolve_in_cull_roots(raw: Any) -> Tuple[Optional[Path], Optional[str], str]:
    """
    Resolve a path and verify it lives under one of the configured cull roots.

    Args:
        raw: Path or string supplied by a caller (CLI argument or HTTP body)

    Returns:
        Tuple of (resolved path, root key, error message). On rejection the path and
        root key are None and the error message explains why.
    """
    try:
        resolved = Path(raw).expanduser().resolve()
    except (OSError, RuntimeError, TypeError) as e:
        return None, None, f"Invalid path {raw!r}: {e}"

    for root_key, root_path in config.CULL_ROOTS.items():
        try:
            root_resolved = Path(root_path).expanduser().resolve()
        except (OSError, RuntimeError):
            continue
        if resolved == root_resolved:
            continue
        if root_resolved in resolved.parents:
            return resolved, root_key, ""

    return None, None, f"Path is outside the cull roots (Final/Staging): {resolved}"


def _move_file(src: Path, dst: Path) -> None:
    """
    Move a single file, preferring an atomic same-filesystem rename.

    Args:
        src: Existing source file
        dst: Destination path; its parent must already exist

    Raises:
        OSError: If neither the rename nor the cross-device fallback succeeds
    """
    try:
        src.rename(dst)
    except OSError:
        # Cross-device (EXDEV) or similar — fall back to copy+unlink.
        shutil.move(str(src), str(dst))


def _dir_bytes(directory: Path) -> int:
    """Sum the on-disk size of every file inside `directory` (0 when it is gone)."""
    if not directory.exists():
        return 0
    total = 0
    for child in directory.rglob("*"):
        try:
            if child.is_file():
                total += child.stat().st_size
        except OSError:
            continue
    return total


def _prune_entry_dir(directory: Path) -> None:
    """Remove a now-empty trash entry directory; never touches a directory with content."""
    try:
        if directory.is_dir() and not any(directory.iterdir()):
            directory.rmdir()
    except OSError as e:
        logger.debug(f"Could not remove trash entry dir {directory}: {e}")


def _lookup_rating(conn: sqlite3.Connection, path: Path) -> Optional[int]:
    """Read the indexed rating for a photo, or None when it is not indexed."""
    try:
        row = conn.execute("SELECT rating FROM photos WHERE path = ?", (str(path),)).fetchone()
    except sqlite3.Error:
        return None
    if row is None:
        return None
    return row["rating"] if isinstance(row, sqlite3.Row) else row[0]


def _mark_index_absent(conn: sqlite3.Connection, path: Path) -> None:
    """Flag the index row for a trashed photo as gone (present=0, in_final=0)."""
    sets = ["in_final = 0"]
    if _has_column(conn, "photos", "present"):
        sets.append("present = 0")
    try:
        conn.execute(f"UPDATE photos SET {', '.join(sets)} WHERE path = ?", (str(path),))
    except sqlite3.Error as e:
        logger.debug(f"Could not mark index row absent for {path}: {e}")


def _mark_index_present(conn: sqlite3.Connection, path: Path, root: str) -> None:
    """Flag the index row for a restored photo as back (present=1, in_final per root)."""
    sets = [f"in_final = {1 if root == 'final' else 0}"]
    if _has_column(conn, "photos", "present"):
        sets.append("present = 1")
    try:
        conn.execute(f"UPDATE photos SET {', '.join(sets)} WHERE path = ?", (str(path),))
    except sqlite3.Error as e:
        logger.debug(f"Could not mark index row present for {path}: {e}")


def is_unrecoverable(original_path: Any) -> bool:
    """True for an adopted orphan whose original location could not be reconstructed."""
    return str(original_path).startswith(UNKNOWN_ORIGIN_PREFIX)


def _open(conn: Optional[sqlite3.Connection]) -> Tuple[sqlite3.Connection, bool]:
    """
    Return (connection, close_on_exit), opening the default index DB when needed.

    The `trash` table itself is created by photo_flow.index.db.init_db, which get_db()
    always runs — this module never issues DDL of its own.
    """
    if conn is not None:
        return conn, False
    return get_db(), True


# ---------------------------------------------------------------------------
# Orphan adoption (safety net for a move that outlived its row)
# ---------------------------------------------------------------------------

def _entry_dir_timestamp(name: str) -> Optional[datetime]:
    """
    Recover when an entry directory was created from its own name.

    Args:
        name: Directory name as produced by `_entry_key` ("20260810T131415-1a2b3c4d")

    Returns:
        Aware UTC datetime, or None when the name does not carry a parseable stamp.
    """
    stamp = name.split("-", 1)[0]
    try:
        parsed = datetime.strptime(stamp, "%Y%m%dT%H%M%S")
    except ValueError:
        return None
    return parsed.replace(tzinfo=timezone.utc)


def _referenced_entry_dirs(conn: sqlite3.Connection) -> Set[str]:
    """Collect every trash entry directory the `trash` table already points at."""
    dirs: Set[str] = set()
    try:
        rows = conn.execute("SELECT trashed_path, sidecar_trashed_path FROM trash").fetchall()
    except sqlite3.Error as e:
        logger.error(f"Could not read trash rows for the orphan sweep: {e}")
        return dirs
    for row in rows:
        for value in (row["trashed_path"], row["sidecar_trashed_path"]):
            if value:
                dirs.add(str(Path(value).parent))
    return dirs


def _derive_origin(
    conn: sqlite3.Connection,
    filename: str,
) -> Tuple[Optional[str], Optional[str]]:
    """
    Reconstruct where an orphaned file came from, using the index as the only evidence.

    Two roots can hold the same filename, so an ambiguous match is treated as *not*
    derivable — guessing would restore a photo into the wrong folder.

    Args:
        conn: Open index connection
        filename: Name of the orphaned file inside its trash entry directory

    Returns:
        Tuple of (original path, root key), or (None, None) when it cannot be derived.
    """
    try:
        rows = conn.execute("SELECT path FROM photos WHERE filename = ?", (filename,)).fetchall()
    except sqlite3.Error:
        return None, None

    candidates: Set[Tuple[str, str]] = set()
    for row in rows:
        raw_path = row["path"] if isinstance(row, sqlite3.Row) else row[0]
        resolved, root_key, _ = _resolve_in_cull_roots(raw_path)
        if resolved is not None and root_key is not None and resolved.name == filename:
            candidates.add((str(resolved), root_key))

    if len(candidates) == 1:
        return candidates.pop()
    return None, None


def adopt_orphan_entries(conn: Optional[sqlite3.Connection] = None) -> Dict[str, Any]:
    """
    Write the missing `trash` row for every entry directory nothing references.

    A trash entry whose row never landed (killed between the rename and the commit, or an
    insert failure whose rollback also failed) is invisible: no command lists it, restore
    cannot reach it and purge skips it — while the photo is already gone from Final, so the
    next backup retires the homelab copy too. This sweep is the same shape as
    `_reconcile_staging_sidecars` in workflow.py and exists for the same reason.

    `original_path` is reconstructed from the index when the filename resolves to exactly
    one path inside the cull roots. When it does not, the entry is recorded with
    `UNKNOWN_ORIGIN_PREFIX` so it is at least visible — and is then never restored
    automatically and never purged. Nothing here ever deletes a file.

    Args:
        conn: Open index connection; if None, the default index DB is opened and closed

    Returns:
        Dict with keys: adopted (int), unrecoverable (int — subset of adopted), errors (int),
        messages (list of str)
    """
    conn, close_on_exit = _open(conn)
    result: Dict[str, Any] = {"adopted": 0, "unrecoverable": 0, "errors": 0, "messages": []}

    try:
        trash_root = Path(config.TRASH_PATH)
        if not trash_root.is_dir():
            return result

        try:
            entry_dirs = sorted(p for p in trash_root.iterdir() if p.is_dir())
        except OSError as e:
            result["errors"] += 1
            result["messages"].append(f"Could not scan the trash directory {trash_root}: {e}")
            return result

        referenced = _referenced_entry_dirs(conn)

        for entry_dir in entry_dirs:
            if str(entry_dir) in referenced:
                continue

            try:
                files = [
                    f for f in sorted(entry_dir.iterdir())
                    if f.is_file() and not f.name.startswith("._")
                ]
            except OSError as e:
                result["errors"] += 1
                result["messages"].append(f"Could not read trash entry {entry_dir}: {e}")
                continue

            if not files:
                # Nothing to lose here — an empty leftover directory, safe to tidy away.
                _prune_entry_dir(entry_dir)
                continue

            sidecars = [f for f in files if f.name.endswith(EDIT_SIDECAR_SUFFIX)]
            photos = [f for f in files if f not in sidecars]

            primary = photos[0] if photos else sidecars[0]
            if len(photos) > 1:
                extras = ", ".join(f.name for f in photos[1:])
                result["messages"].append(
                    f"Trash entry {entry_dir.name} holds more than one photo; adopted "
                    f"{primary.name}, left alongside: {extras}"
                )

            sidecar = next((s for s in sidecars if s.stem == primary.stem), None) if photos else None
            unmatched = [s.name for s in sidecars if s is not sidecar]
            if unmatched:
                # They stay in the entry dir and travel with it; say so, because a restore
                # will not carry them back and only a human can place them.
                result["messages"].append(
                    f"Trash entry {entry_dir.name} holds edit histories with no photo beside "
                    f"them: {', '.join(unmatched)}"
                )

            original_path: Optional[str] = None
            root_key: Optional[str] = None
            if photos:
                original_path, root_key = _derive_origin(conn, primary.name)

            unrecoverable = original_path is None or root_key is None
            if unrecoverable:
                original_path = f"{UNKNOWN_ORIGIN_PREFIX}/{primary.name}"
                root_key = UNKNOWN_ROOT
                sidecar_original: Optional[str] = None
            else:
                sidecar_original = (
                    str(Path(original_path).with_suffix(EDIT_SIDECAR_SUFFIX))
                    if sidecar is not None else None
                )

            try:
                size = primary.stat().st_size
            except OSError:
                size = 0

            trashed_at = _entry_dir_timestamp(entry_dir.name) or _now_utc()
            rating = None if unrecoverable else _lookup_rating(conn, Path(original_path))

            try:
                conn.execute(
                    """
                    INSERT INTO trash
                        (original_path, root, trashed_path, sidecar_original_path,
                         sidecar_trashed_path, filename, size, rating, trashed_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        original_path,
                        root_key,
                        str(primary),
                        sidecar_original,
                        str(sidecar) if sidecar is not None else None,
                        primary.name,
                        size,
                        rating,
                        trashed_at.isoformat(),
                    ),
                )
                if not unrecoverable and not Path(original_path).exists():
                    # Only when the origin really is empty — a live file there means the
                    # index row is about something else and must not be flagged gone.
                    _mark_index_absent(conn, Path(original_path))
                conn.commit()
            except sqlite3.Error as e:
                try:
                    conn.rollback()
                except sqlite3.Error:
                    pass
                result["errors"] += 1
                result["messages"].append(
                    f"Could not adopt orphaned trash entry {entry_dir.name}: {e}"
                )
                continue

            result["adopted"] += 1
            if unrecoverable:
                result["unrecoverable"] += 1
                logger.warning(
                    f"Adopted orphaned trash entry {entry_dir.name} ({primary.name}) with no "
                    f"reconstructable original path — it will never be purged; move it by hand"
                )
            else:
                logger.warning(
                    f"Adopted orphaned trash entry {entry_dir.name} for {original_path}"
                )

        return result

    finally:
        if close_on_exit:
            conn.close()


def _sweep_orphans(conn: sqlite3.Connection) -> None:
    """Run the adoption sweep best-effort — a failure must never break a listing."""
    try:
        adopt_orphan_entries(conn=conn)
    except Exception as e:  # noqa: BLE001 - surfacing the trash is more important than this
        logger.error(f"Trash orphan sweep failed: {e}")


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def trash_photos(
    paths: Sequence[Any],
    dry_run: bool = False,
    conn: Optional[sqlite3.Connection] = None,
) -> Dict[str, Any]:
    """
    Soft-delete photos by moving them (and their .photo-edit sidecars) into the trash.

    Each path is resolved and validated against CULL_ROOTS before anything moves. Files
    are relocated first and the `trash` row is inserted afterwards; if the insert fails
    the files are moved back and the entry is reported as an error. A RAW belonging to the
    photo is deliberately left untouched (see module docstring).

    Args:
        paths: Photo paths (str or Path) inside Final or Staging
        dry_run: If True, validate and report only — no file is moved, no row written
        conn: Open index connection; if None, the default index DB is opened and closed

    Returns:
        Dict with keys:
            trashed:  number of photos actually moved (0 on dry run)
            entries:  list of {id, filename, original_path, rating} — id is None on dry run
            errors:   number of paths that could not be trashed
            messages: human-readable diagnostics, one per error or skipped path
    """
    conn, close_on_exit = _open(conn)
    result: Dict[str, Any] = {"trashed": 0, "entries": [], "errors": 0, "messages": []}

    try:
        for raw in paths:
            resolved, root_key, resolve_error = _resolve_in_cull_roots(raw)
            if resolved is None or root_key is None:
                result["errors"] += 1
                result["messages"].append(resolve_error)
                continue

            if not resolved.is_file():
                result["errors"] += 1
                result["messages"].append(f"Not a file (already gone?): {resolved}")
                continue

            sidecar_src = resolved.with_suffix(EDIT_SIDECAR_SUFFIX)
            has_sidecar = sidecar_src.is_file()
            rating = _lookup_rating(conn, resolved)

            if dry_run:
                result["entries"].append({
                    "id": None,
                    "filename": resolved.name,
                    "original_path": str(resolved),
                    "rating": rating,
                })
                continue

            try:
                size = resolved.stat().st_size
            except OSError as e:
                result["errors"] += 1
                result["messages"].append(f"Could not stat {resolved}: {e}")
                continue

            entry_dir = Path(config.TRASH_PATH) / _entry_key()
            jpg_dst = entry_dir / resolved.name
            sidecar_dst = entry_dir / sidecar_src.name if has_sidecar else None

            try:
                entry_dir.mkdir(parents=True, exist_ok=False)
            except OSError as e:
                result["errors"] += 1
                result["messages"].append(f"Could not create trash entry for {resolved.name}: {e}")
                continue

            # --- move the photo -------------------------------------------------
            try:
                _move_file(resolved, jpg_dst)
            except OSError as e:
                _prune_entry_dir(entry_dir)
                result["errors"] += 1
                result["messages"].append(f"Could not move {resolved.name} to trash: {e}")
                continue

            # --- move the sidecar (irreplaceable — it never stays behind) --------
            if has_sidecar and sidecar_dst is not None:
                try:
                    _move_file(sidecar_src, sidecar_dst)
                except OSError as e:
                    rolled_back = _rollback_moves([(jpg_dst, resolved)])
                    _prune_entry_dir(entry_dir)
                    result["errors"] += 1
                    if rolled_back:
                        result["messages"].append(
                            f"Could not move edit sidecar {sidecar_src.name} — {resolved.name} left in place: {e}"
                        )
                    else:
                        result["messages"].append(
                            f"Could not move edit sidecar {sidecar_src.name}, and {resolved.name} could NOT be "
                            f"restored — it is at {jpg_dst}: {e}"
                        )
                    continue

            # --- record the entry only once every file has landed ----------------
            try:
                cursor = conn.execute(
                    """
                    INSERT INTO trash
                        (original_path, root, trashed_path, sidecar_original_path,
                         sidecar_trashed_path, filename, size, rating, trashed_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        str(resolved),
                        root_key,
                        str(jpg_dst),
                        str(sidecar_src) if has_sidecar else None,
                        str(sidecar_dst) if sidecar_dst is not None else None,
                        resolved.name,
                        size,
                        rating,
                        _now_iso(),
                    ),
                )
                entry_id = cursor.lastrowid
                _mark_index_absent(conn, resolved)
                conn.commit()
            except sqlite3.Error as e:
                # The row is what makes the move recoverable — without it, put everything back.
                restores = [(jpg_dst, resolved)]
                if has_sidecar and sidecar_dst is not None:
                    restores.append((sidecar_dst, sidecar_src))
                rolled_back = _rollback_moves(restores)
                _prune_entry_dir(entry_dir)
                try:
                    conn.rollback()
                except sqlite3.Error:
                    pass
                result["errors"] += 1
                if rolled_back:
                    result["messages"].append(
                        f"Could not record trash entry for {resolved.name} — file restored in place: {e}"
                    )
                else:
                    # Left in the trash with no row: the adoption sweep in list_trash/trash_stats
                    # picks it up so it stays visible and reachable.
                    result["messages"].append(
                        f"Could not record trash entry for {resolved.name} and it could NOT be restored "
                        f"— it is at {entry_dir}: {e}"
                    )
                continue

            result["trashed"] += 1
            result["entries"].append({
                "id": entry_id,
                "filename": resolved.name,
                "original_path": str(resolved),
                "rating": rating,
            })

        return result

    finally:
        if close_on_exit:
            conn.close()


def restore(
    entry_ids: Sequence[int],
    conn: Optional[sqlite3.Connection] = None,
) -> Dict[str, Any]:
    """
    Move trashed photos (and their sidecars) back to their original locations.

    An occupied original path is never overwritten — the entry is left in the trash and
    reported instead. If the sidecar cannot be restored, the JPG move is rolled back so
    the pair stays together.

    Args:
        entry_ids: `trash.id` values to restore
        conn: Open index connection; if None, the default index DB is opened and closed

    Returns:
        Dict with keys: restored (int), errors (int), messages (list of str)
    """
    conn, close_on_exit = _open(conn)
    result: Dict[str, Any] = {"restored": 0, "errors": 0, "messages": []}

    try:
        for entry_id in entry_ids:
            row = conn.execute("SELECT * FROM trash WHERE id = ?", (entry_id,)).fetchone()
            if row is None:
                result["errors"] += 1
                result["messages"].append(f"No trash entry with id {entry_id}")
                continue

            if is_unrecoverable(row["original_path"]):
                result["errors"] += 1
                result["messages"].append(
                    f"Entry {entry_id}: this is an adopted orphan with no known original location "
                    f"— the files are at {Path(row['trashed_path']).parent} and must be moved back "
                    f"by hand"
                )
                continue

            original = Path(row["original_path"])
            trashed = Path(row["trashed_path"])

            # Re-validate: the row could predate a config change (or have been tampered with).
            _, root_key, resolve_error = _resolve_in_cull_roots(original)
            if root_key is None:
                result["errors"] += 1
                result["messages"].append(f"Entry {entry_id}: {resolve_error}")
                continue

            if not trashed.is_file():
                result["errors"] += 1
                result["messages"].append(
                    f"Entry {entry_id}: trashed file is missing ({trashed})"
                )
                continue

            if original.exists():
                result["errors"] += 1
                result["messages"].append(
                    f"Entry {entry_id}: original path is occupied, refusing to overwrite ({original})"
                )
                continue

            sidecar_trashed = Path(row["sidecar_trashed_path"]) if row["sidecar_trashed_path"] else None
            sidecar_original = Path(row["sidecar_original_path"]) if row["sidecar_original_path"] else None
            if sidecar_original is not None and sidecar_original.exists():
                result["errors"] += 1
                result["messages"].append(
                    f"Entry {entry_id}: edit sidecar path is occupied, refusing to overwrite ({sidecar_original})"
                )
                continue

            try:
                original.parent.mkdir(parents=True, exist_ok=True)
                _move_file(trashed, original)
            except OSError as e:
                result["errors"] += 1
                result["messages"].append(f"Entry {entry_id}: could not restore {row['filename']}: {e}")
                continue

            if sidecar_trashed is not None and sidecar_original is not None:
                if sidecar_trashed.is_file():
                    try:
                        _move_file(sidecar_trashed, sidecar_original)
                    except OSError as e:
                        _rollback_moves([(original, trashed)])
                        result["errors"] += 1
                        result["messages"].append(
                            f"Entry {entry_id}: could not restore edit sidecar, rolled back: {e}"
                        )
                        continue
                else:
                    result["messages"].append(
                        f"Entry {entry_id}: edit sidecar missing from trash ({sidecar_trashed})"
                    )

            try:
                conn.execute("DELETE FROM trash WHERE id = ?", (entry_id,))
                _mark_index_present(conn, original, root_key)
                conn.commit()
            except sqlite3.Error as e:
                # The files are back where they belong; a stale row is the lesser evil.
                result["messages"].append(
                    f"Entry {entry_id}: restored, but the trash row could not be cleared: {e}"
                )

            _prune_entry_dir(trashed.parent)
            result["restored"] += 1

        return result

    finally:
        if close_on_exit:
            conn.close()


def list_trash(
    limit: int = 500,
    conn: Optional[sqlite3.Connection] = None,
) -> List[Dict[str, Any]]:
    """
    List trash entries, newest first.

    Runs the orphan adoption sweep first, so an entry whose row was lost between the move
    and the commit shows up here instead of sitting invisible under ~/Pictures.

    Args:
        limit: Maximum number of entries to return
        conn: Open index connection; if None, the default index DB is opened and closed

    Returns:
        List of dicts with the stored columns plus `age_days` (float), `purgeable` (bool),
        `exists` (bool — whether the trashed file is still on disk) and `unrecoverable`
        (bool — an adopted orphan whose original location is unknown).
    """
    conn, close_on_exit = _open(conn)
    try:
        _sweep_orphans(conn)
        rows = conn.execute(
            "SELECT * FROM trash ORDER BY datetime(trashed_at) DESC, id DESC LIMIT ?",
            (max(0, int(limit)),),
        ).fetchall()

        now = _now_utc()
        entries: List[Dict[str, Any]] = []
        for row in rows:
            entry = dict(row)
            trashed_at = _parse_iso(entry.get("trashed_at", ""))
            age_days = (now - trashed_at).total_seconds() / 86400.0 if trashed_at else 0.0
            unrecoverable = is_unrecoverable(entry["original_path"])
            entry["age_days"] = age_days
            # An entry with no known original location is never purged, whatever its age.
            entry["purgeable"] = age_days >= TRASH_RETENTION_DAYS and not unrecoverable
            entry["exists"] = Path(entry["trashed_path"]).is_file()
            entry["unrecoverable"] = unrecoverable
            entries.append(entry)
        return entries

    finally:
        if close_on_exit:
            conn.close()


def purge(
    older_than_days: int = TRASH_RETENTION_DAYS,
    dry_run: bool = False,
    conn: Optional[sqlite3.Connection] = None,
) -> Dict[str, Any]:
    """
    Permanently delete trash entries older than the retention threshold.

    Age is measured from `trashed_at` — when the entry was trashed — and NEVER from file
    mtime. A photo keeps its capture-time mtime forever, so an mtime-based sweep would
    delete a folder trashed thirty seconds ago just because the photos in it are two years
    old. That exact mistake was already shipped once in the rclone backup prune (fixed in
    v0.4.2, see CLAUDE.md); this comment exists so it is not reintroduced here.

    Adopted orphans with no reconstructable original location are skipped unconditionally,
    at any age — see `adopt_orphan_entries`.

    Args:
        older_than_days: Minimum age in days; entries younger than this are never touched
        dry_run: If True, report what would be purged without deleting anything
        conn: Open index connection; if None, the default index DB is opened and closed

    Returns:
        Dict with keys: purged (int), bytes (int reclaimed, or reclaimable on dry run),
        errors (int)
    """
    conn, close_on_exit = _open(conn)
    result: Dict[str, Any] = {"purged": 0, "bytes": 0, "errors": 0}

    threshold_days = max(0, int(older_than_days))
    cutoff = _now_utc() - timedelta(days=threshold_days)

    try:
        for row in conn.execute("SELECT * FROM trash ORDER BY id").fetchall():
            if is_unrecoverable(row["original_path"]):
                # An adopted orphan we could not place: irreplaceable, with nobody left to
                # say where it belongs. Age is irrelevant — a human decides, never this loop.
                logger.warning(
                    f"Trash entry {row['id']} has no known original location; refusing to purge "
                    f"{row['trashed_path']} — move it out by hand instead"
                )
                continue

            trashed_at = _parse_iso(row["trashed_at"])
            if trashed_at is None:
                # Unparseable timestamp: treat as young. Never guess in favour of deletion.
                result["errors"] += 1
                logger.error(f"Trash entry {row['id']} has an unreadable trashed_at; skipping purge")
                continue
            if trashed_at > cutoff:
                continue

            trashed = Path(row["trashed_path"])
            entry_dir = trashed.parent
            reclaimable = _dir_bytes(entry_dir)

            if dry_run:
                result["purged"] += 1
                result["bytes"] += reclaimable
                continue

            try:
                if entry_dir.is_dir():
                    shutil.rmtree(entry_dir)
                else:
                    trashed.unlink(missing_ok=True)
            except OSError as e:
                result["errors"] += 1
                logger.error(f"Could not purge trash entry {row['id']} at {entry_dir}: {e}")
                continue

            try:
                conn.execute("DELETE FROM trash WHERE id = ?", (row["id"],))
                conn.commit()
            except sqlite3.Error as e:
                result["errors"] += 1
                logger.error(f"Purged files for trash entry {row['id']} but could not delete the row: {e}")
                continue

            result["purged"] += 1
            result["bytes"] += reclaimable

        return result

    finally:
        if close_on_exit:
            conn.close()


def trash_stats(conn: Optional[sqlite3.Connection] = None) -> Dict[str, Any]:
    """
    Summarise the trash for the CLI and the control panel.

    Args:
        conn: Open index connection; if None, the default index DB is opened and closed

    Returns:
        Dict with keys: count (int), bytes (int on disk), oldest_iso (str or None),
        purgeable (int — entries at or past TRASH_RETENTION_DAYS), unrecoverable (int —
        adopted orphans with no known original location, which are never purged)
    """
    conn, close_on_exit = _open(conn)
    try:
        _sweep_orphans(conn)
        rows = conn.execute("SELECT * FROM trash").fetchall()
        now = _now_utc()

        total_bytes = 0
        purgeable = 0
        unrecoverable = 0
        oldest: Optional[datetime] = None
        oldest_iso: Optional[str] = None

        for row in rows:
            total_bytes += _dir_bytes(Path(row["trashed_path"]).parent)
            is_orphan = is_unrecoverable(row["original_path"])
            if is_orphan:
                unrecoverable += 1
            trashed_at = _parse_iso(row["trashed_at"])
            if trashed_at is None:
                continue
            if not is_orphan and (now - trashed_at).total_seconds() / 86400.0 >= TRASH_RETENTION_DAYS:
                purgeable += 1
            if oldest is None or trashed_at < oldest:
                oldest = trashed_at
                oldest_iso = row["trashed_at"]

        return {
            "count": len(rows),
            "bytes": total_bytes,
            "oldest_iso": oldest_iso,
            "purgeable": purgeable,
            "unrecoverable": unrecoverable,
        }

    finally:
        if close_on_exit:
            conn.close()


# ---------------------------------------------------------------------------
# Entry keys / rollback
# ---------------------------------------------------------------------------

def _entry_key() -> str:
    """
    Build a unique directory name for one trash entry.

    Not the DB id: the row is inserted only after the files have moved, so no id exists
    yet. Sortable UTC timestamp + 8 random hex keeps entries readable on disk and free of
    collisions when the same filename is trashed twice.
    """
    return f"{_now_utc().strftime('%Y%m%dT%H%M%S')}-{uuid.uuid4().hex[:8]}"


def _rollback_moves(moves: Sequence[Tuple[Path, Path]]) -> bool:
    """
    Undo a partial trash operation by moving files back to where they came from.

    Args:
        moves: (current location, original location) pairs, applied best-effort. A failure
            here is logged rather than raised — the caller is already handling a failure —
            but it *is* reported back, because a caller that claims "restored in place" over
            a file still sitting in the trash is how a photo silently gets written off.

    Returns:
        True when every pair ended up back at its original location (or had already left the
        trash entry); False when at least one file could not be moved back.
    """
    ok = True
    for current, original in moves:
        try:
            if not current.exists():
                continue
            if original.exists():
                ok = False
                logger.error(
                    f"Rollback failed: {original} is occupied, so {current} stays in the trash entry"
                )
                continue
            _move_file(current, original)
        except OSError as e:
            ok = False
            logger.error(f"Rollback failed: {current} could not be moved back to {original}: {e}")
    return ok
