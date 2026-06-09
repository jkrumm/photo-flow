"""
Incremental metadata indexer for the photo-flow SQLite index.

Scans FINAL_PATH JPGs and upserts rows only when (path, size, mtime) changed.
Marks rows whose backing file no longer exists as in_final=0.
published=1 when the file is present in GALLERY_PATH/images/ (actual sync state).
"""

import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional, Dict, Any

from photo_flow.config import FINAL_PATH, GALLERY_PATH
from photo_flow.file_manager import scan_for_images
from photo_flow.metadata_extractor import MetadataExtractor
from photo_flow.index.db import get_db


# ---------------------------------------------------------------------------
# EXIF string parsers
# ---------------------------------------------------------------------------

def parse_aperture(value: Optional[str]) -> Optional[float]:
    """
    Parse "f/2.8" → 2.8. Returns None on missing or malformed input.

    >>> parse_aperture("f/2.8")
    2.8
    >>> parse_aperture("f/1.4")
    1.4
    >>> parse_aperture(None) is None
    True
    """
    if not value:
        return None
    try:
        stripped = value.strip().lower().lstrip("f").lstrip("/")
        return float(stripped)
    except (ValueError, AttributeError):
        return None


def parse_shutter(value: Optional[str]) -> Optional[float]:
    """
    Parse "1/250" → 0.004 or "1.5" → 1.5. Returns None on missing/malformed.

    >>> abs(parse_shutter("1/250") - 0.004) < 1e-9
    True
    >>> parse_shutter("1.5")
    1.5
    >>> parse_shutter(None) is None
    True
    """
    if not value:
        return None
    try:
        value = value.strip()
        if "/" in value:
            numerator, denominator = value.split("/", 1)
            d = float(denominator)
            if d == 0:
                return None
            return float(numerator) / d
        return float(value)
    except (ValueError, AttributeError, ZeroDivisionError):
        return None


def parse_focal(value: Optional[str]) -> Optional[float]:
    """
    Parse "35mm" → 35.0. Returns None on missing or malformed input.

    >>> parse_focal("35mm")
    35.0
    >>> parse_focal("100mm")
    100.0
    >>> parse_focal(None) is None
    True
    """
    if not value:
        return None
    try:
        stripped = value.strip().lower().rstrip("m")  # strip "mm" or "m"
        return float(stripped)
    except (ValueError, AttributeError):
        return None


# ---------------------------------------------------------------------------
# Indexer
# ---------------------------------------------------------------------------

def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _gallery_images_set() -> set:
    """Return a set of filenames currently present in GALLERY_PATH/images/."""
    gallery_images = GALLERY_PATH / "images"
    if not gallery_images.exists():
        return set()
    return {p.name for p in gallery_images.iterdir() if p.suffix.upper() == ".JPG"}


def _build_row(path: Path, stat: Any, meta: Dict[str, Any], gallery_names: set) -> dict:
    """Build the upsert dict from a file stat + extracted metadata dict."""
    return {
        "path": str(path),
        "filename": path.name,
        "size": stat.st_size,
        "mtime": stat.st_mtime,
        "date_taken": meta.get("date_taken"),
        "rating": meta.get("rating"),
        "iso": meta.get("iso"),
        "aperture_f": parse_aperture(meta.get("aperture")),
        "shutter_s": parse_shutter(meta.get("shutter_speed")),
        "focal_mm": parse_focal(meta.get("focal_length")),
        "latitude": meta.get("latitude"),
        "longitude": meta.get("longitude"),
        "camera_model": meta.get("camera_model"),
        "dimensions": meta.get("dimensions"),
        "in_final": 1,
        "published": 1 if path.name in gallery_names else 0,
        "indexed_at": _now_iso(),
    }


_UPSERT_SQL = """
    INSERT INTO photos
        (path, filename, size, mtime, date_taken, rating, iso,
         aperture_f, shutter_s, focal_mm, latitude, longitude,
         camera_model, dimensions, in_final, published, indexed_at)
    VALUES
        (:path, :filename, :size, :mtime, :date_taken, :rating, :iso,
         :aperture_f, :shutter_s, :focal_mm, :latitude, :longitude,
         :camera_model, :dimensions, :in_final, :published, :indexed_at)
    ON CONFLICT(path) DO UPDATE SET
        filename     = excluded.filename,
        size         = excluded.size,
        mtime        = excluded.mtime,
        date_taken   = excluded.date_taken,
        rating       = excluded.rating,
        iso          = excluded.iso,
        aperture_f   = excluded.aperture_f,
        shutter_s    = excluded.shutter_s,
        focal_mm     = excluded.focal_mm,
        latitude     = excluded.latitude,
        longitude    = excluded.longitude,
        camera_model = excluded.camera_model,
        dimensions   = excluded.dimensions,
        in_final     = excluded.in_final,
        published    = excluded.published,
        indexed_at   = excluded.indexed_at
"""


def reindex(
    conn: Optional[sqlite3.Connection] = None,
    final_path: Optional[Path] = None,
    gallery_path: Optional[Path] = None,
) -> Dict[str, int]:
    """
    Incrementally update the index for all JPGs in final_path.

    Only files whose (size, mtime) differ from the stored row are re-read via
    extract_metadata(). Rows for files that no longer exist are marked in_final=0.

    Args:
        conn: Open sqlite3 connection; if None, opens the default DB.
        final_path: Override for FINAL_PATH (used in tests).
        gallery_path: Override for GALLERY_PATH/images parent (used in tests).

    Returns:
        Dict with keys: indexed, updated, skipped, removed.
    """
    close_on_exit = conn is None
    if conn is None:
        conn = get_db()

    fp = final_path or FINAL_PATH
    gp = gallery_path or GALLERY_PATH

    try:
        stats = {"indexed": 0, "updated": 0, "skipped": 0, "removed": 0}

        if not fp.exists():
            return stats

        # Build gallery filenames set once
        gallery_images_dir = gp / "images"
        gallery_names: set = set()
        if gallery_images_dir.exists():
            gallery_names = {p.name for p in gallery_images_dir.iterdir() if p.suffix.upper() == ".JPG"}

        # Load existing rows: path → (size, mtime)
        existing: Dict[str, tuple] = {}
        for row in conn.execute("SELECT path, size, mtime FROM photos WHERE in_final = 1"):
            existing[row["path"]] = (row["size"], row["mtime"])

        # Scan Final JPGs
        final_files = scan_for_images(fp, ".JPG")
        final_paths = {str(f) for f in final_files}

        # Mark rows for files no longer present in Final
        for stored_path in list(existing.keys()):
            if stored_path not in final_paths:
                conn.execute("UPDATE photos SET in_final = 0 WHERE path = ?", (stored_path,))
                stats["removed"] += 1

        # Upsert each file
        for file_path in final_files:
            path_str = str(file_path)
            try:
                stat = file_path.stat()
            except OSError:
                continue

            stored = existing.get(path_str)
            if stored is not None:
                stored_size, stored_mtime = stored
                # Allow floating-point mtime fuzz of 1ms
                if stored_size == stat.st_size and abs(stored_mtime - stat.st_mtime) < 0.001:
                    stats["skipped"] += 1
                    continue
                # File changed — re-read metadata
                meta = MetadataExtractor.extract_metadata(file_path)
                row = _build_row(file_path, stat, meta, gallery_names)
                conn.execute(_UPSERT_SQL, row)
                stats["updated"] += 1
            else:
                # New file
                meta = MetadataExtractor.extract_metadata(file_path)
                row = _build_row(file_path, stat, meta, gallery_names)
                conn.execute(_UPSERT_SQL, row)
                stats["indexed"] += 1

        conn.commit()
        return stats

    finally:
        if close_on_exit:
            conn.close()
