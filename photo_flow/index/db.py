"""
SQLite connection and schema management for the photo-flow metadata index.

Schema history
--------------
v1  `photos` — one row per Final JPG, feeding the analytics screens.
v2  `photos` gains the culling columns (root/present/dimensions/lens/label/sidecar)
    and a `trash` table backing the soft-delete of culled photos.
v3  two independent additions, shipped together:
    * a `jobs` table — the durable record of every control-panel operation, so a
      server restart no longer vaporises an in-flight job (see `api/job_store.py`);
    * `photos.keywords` — the flat `XMP-dc:subject` tag set (`_PHOTOS_V3_COLUMNS`).

Every `photos` migration is additive only: it reads ``PRAGMA table_info(photos)`` and
issues ``ALTER TABLE ... ADD COLUMN`` for whatever is missing. The live database holds
thousands of rows, so nothing is ever dropped or recreated — and rebuilding from zero is
not an available migration path, because the same file holds the `trash` table and a
trash row is what makes a restore possible. ``_migrate_photos_v2`` applies BOTH the v2
and the v3 column lists despite its name (it is the `photos` column migrator; renaming it
would break nothing but was left alone rather than churn a live-database code path).
A new TABLE needs only ``CREATE TABLE IF NOT EXISTS``; any LATER column on `jobs` must
use the same PRAGMA-guarded idiom.

`in_final` invariant
--------------------
Every analytics query filters ``WHERE in_final = 1``. That column keeps its v1
meaning exactly: ``in_final = 1`` iff ``root = 'final' AND present = 1``. Staging
rows are therefore always written with ``in_final = 0``, and the analytics surface
is unchanged by the arrival of a second root.
"""

import sqlite3
from pathlib import Path
from typing import List, Optional, Tuple

_DEFAULT_DB_PATH = Path.home() / ".photoflow" / "index.db"

# Columns added in schema v2, as (name, DDL fragment). Applied with
# ALTER TABLE ADD COLUMN only when PRAGMA table_info says they are missing.
# SQLite requires a non-NULL constant default for a NOT NULL added column —
# the defaults below are also the correct backfill for pre-v2 rows, which were
# all present Final photos.
_PHOTOS_V2_COLUMNS: List[Tuple[str, str]] = [
    ("root", "TEXT NOT NULL DEFAULT 'final'"),
    ("present", "INTEGER NOT NULL DEFAULT 1"),
    ("width", "INTEGER"),
    ("height", "INTEGER"),
    ("orientation", "TEXT"),
    ("lens_model", "TEXT"),
    ("camera_make", "TEXT"),
    ("label", "TEXT"),
    ("has_sidecar", "INTEGER NOT NULL DEFAULT 0"),
]

# Columns added in schema v3, same PRAGMA-guarded idiom. `keywords` holds the flat
# `XMP-dc:subject` set, pipe-delimited AND pipe-sentinelled (`|a|b|`), so an exact tag
# match is one `LIKE '%|a|%'` with no split and no join table. `|` is safe as the
# delimiter because decision 0004 already reserves it as the hierarchy separator in
# `lr:hierarchicalSubject` — it cannot occur inside a single flat tag.
# NULL means "not indexed since v3"; the empty sentinel `|` means "indexed, no keywords".
_PHOTOS_V3_COLUMNS: List[Tuple[str, str]] = [
    ("keywords", "TEXT"),
]


def get_db(path: Optional[Path] = None, timeout: Optional[float] = None) -> sqlite3.Connection:
    """
    Open (or create) the index database and return a connection.

    The caller is responsible for closing the connection.
    Row factory is set to sqlite3.Row for dict-like access.

    Args:
        path: Override for the default ``~/.photoflow/index.db`` (used in tests).
        timeout: Lock-wait timeout in seconds. Defaults to sqlite3's own 5 s. The
            job store passes a short one because it writes from the event-loop
            thread, where a five-second block behind a long indexer transaction
            would stall every SSE stream in the panel.

    Returns:
        An open sqlite3.Connection with the current schema applied.
    """
    db_path = path or _DEFAULT_DB_PATH
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = (
        sqlite3.connect(str(db_path))
        if timeout is None
        else sqlite3.connect(str(db_path), timeout=timeout)
    )
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    init_db(conn)
    return conn


def _existing_columns(conn: sqlite3.Connection, table: str) -> set:
    """Return the set of column names currently defined on ``table``."""
    return {row["name"] for row in conn.execute(f"PRAGMA table_info({table})")}


def _migrate_photos_v2(conn: sqlite3.Connection) -> None:
    """
    Add the schema-v2 and -v3 culling columns to `photos` if they are missing.

    Idempotent and non-destructive: existing rows keep every value they had and
    pick up the column defaults ('final' / present=1), which is precisely right —
    before v2 only the Final folder was ever indexed. The one exception is rows
    previously flagged ``in_final = 0`` (their file had vanished); those are
    backfilled to ``present = 0`` in the same pass, and only in the pass that
    creates the column, so a later manual edit is never overwritten.
    """
    existing = _existing_columns(conn, "photos")
    added_present = False

    for name, ddl in _PHOTOS_V2_COLUMNS + _PHOTOS_V3_COLUMNS:
        if name in existing:
            continue
        conn.execute(f"ALTER TABLE photos ADD COLUMN {name} {ddl}")
        if name == "present":
            added_present = True

    if added_present:
        # Pre-v2, in_final=0 could only mean "file no longer on disk".
        conn.execute("UPDATE photos SET present = 0 WHERE in_final = 0")


def init_db(conn: sqlite3.Connection) -> None:
    """
    Create tables and indexes if they don't exist yet, then apply pending migrations.

    Safe to call on every connection open: every statement is guarded by
    ``IF NOT EXISTS`` or a ``PRAGMA table_info`` check.
    """
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS photos (
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

        CREATE TABLE IF NOT EXISTS trash (
            id                    INTEGER PRIMARY KEY AUTOINCREMENT,
            original_path         TEXT NOT NULL,
            root                  TEXT NOT NULL,
            trashed_path          TEXT NOT NULL,
            sidecar_original_path TEXT,
            sidecar_trashed_path  TEXT,
            filename              TEXT NOT NULL,
            size                  INTEGER NOT NULL,
            rating                INTEGER,
            trashed_at            TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS jobs (
            job_id       TEXT PRIMARY KEY,
            op           TEXT NOT NULL,
            status       TEXT NOT NULL,
            seq          INTEGER NOT NULL DEFAULT 0,
            queued_at    TEXT NOT NULL,
            started_at   TEXT,
            finished_at  TEXT,
            result       TEXT,
            error        TEXT,
            announced    INTEGER NOT NULL DEFAULT 0
        );
    """)

    _migrate_photos_v2(conn)

    conn.executescript("""
        CREATE INDEX IF NOT EXISTS idx_date_taken   ON photos (date_taken);
        CREATE INDEX IF NOT EXISTS idx_rating       ON photos (rating);
        CREATE INDEX IF NOT EXISTS idx_root_present ON photos (root, present);
        CREATE INDEX IF NOT EXISTS idx_iso          ON photos (iso);
        CREATE INDEX IF NOT EXISTS idx_focal_mm     ON photos (focal_mm);
        CREATE INDEX IF NOT EXISTS idx_trash_at     ON trash (trashed_at);
        CREATE INDEX IF NOT EXISTS idx_jobs_seq     ON jobs (seq);
        CREATE INDEX IF NOT EXISTS idx_jobs_status  ON jobs (status);
    """)
    conn.commit()
