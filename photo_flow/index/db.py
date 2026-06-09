"""SQLite connection and schema management for the photo-flow metadata index."""

import sqlite3
from pathlib import Path
from typing import Optional

_DEFAULT_DB_PATH = Path.home() / ".photoflow" / "index.db"


def get_db(path: Optional[Path] = None) -> sqlite3.Connection:
    """
    Open (or create) the index database and return a connection.

    The caller is responsible for closing the connection.
    Row factory is set to sqlite3.Row for dict-like access.
    """
    db_path = path or _DEFAULT_DB_PATH
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    init_db(conn)
    return conn


def init_db(conn: sqlite3.Connection) -> None:
    """Create tables and indexes if they don't exist yet."""
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

        CREATE INDEX IF NOT EXISTS idx_date_taken ON photos (date_taken);
        CREATE INDEX IF NOT EXISTS idx_rating     ON photos (rating);
    """)
    conn.commit()
