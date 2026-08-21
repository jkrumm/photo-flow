"""
Incremental metadata indexer for the photo-flow SQLite index.

Scans the JPGs of both cull roots — FINAL_PATH (root='final') and STAGING_PATH
(root='staging') — and upserts a row only when (path, size, mtime) changed.
Rows whose backing file has vanished are marked present=0 (never deleted, so the
trash/analytics history survives).

published=1 when the file is present in GALLERY_PATH/images/ (actual sync state).

`in_final` invariant
--------------------
`in_final = 1` iff `root = 'final' AND present = 1`. Every analytics query filters
on it, so Staging rows — always written with in_final=0 — stay invisible to the
analytics surface.
"""

import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional, Dict, Any, Sequence, Tuple

from photo_flow.config import (
    EDIT_SIDECAR_SUFFIX,
    FINAL_PATH,
    GALLERY_PATH,
    STAGING_PATH,
)
from photo_flow.file_manager import is_valid_image_file, scan_for_images
from photo_flow.metadata_extractor import MetadataExtractor
from photo_flow.index.db import get_db

ROOT_FINAL = "final"
ROOT_STAGING = "staging"


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


def derive_orientation(width: Optional[int], height: Optional[int]) -> Optional[str]:
    """
    Derive 'landscape' / 'portrait' / 'square' from pixel dimensions.

    Args:
        width: Image width in pixels, or None when unknown.
        height: Image height in pixels, or None when unknown.

    Returns:
        The orientation string, or None when either dimension is missing/invalid.
    """
    if not width or not height:
        return None
    if width > height:
        return "landscape"
    if width < height:
        return "portrait"
    return "square"


# ---------------------------------------------------------------------------
# Indexer
# ---------------------------------------------------------------------------

def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _gallery_images_set(gallery_path: Optional[Path] = None) -> set:
    """Return a set of filenames currently present in <gallery_path>/images/."""
    gallery_images = (gallery_path or GALLERY_PATH) / "images"
    if not gallery_images.exists():
        return set()
    return {p.name for p in gallery_images.iterdir() if p.suffix.upper() == ".JPG"}


def _has_sidecar(path: Path) -> int:
    """Return 1 when Photomator's <stem>.photo-edit history sits next to the JPG."""
    try:
        return 1 if path.with_suffix(EDIT_SIDECAR_SUFFIX).exists() else 0
    except (OSError, ValueError):
        return 0


def _dimensions_from_meta(meta: Dict[str, Any]) -> Tuple[Optional[int], Optional[int]]:
    """
    Pull (width, height) out of an extract_metadata dict.

    Prefers the numeric width/height keys and falls back to parsing the legacy
    "WxH" `dimensions` string, so rows written by an older extractor still get
    an orientation.
    """
    width = meta.get("width")
    height = meta.get("height")
    if isinstance(width, int) and isinstance(height, int):
        return width, height

    dims = meta.get("dimensions")
    if isinstance(dims, str) and "x" in dims:
        raw_w, _, raw_h = dims.partition("x")
        try:
            return int(raw_w), int(raw_h)
        except ValueError:
            return None, None
    return None, None


# The pipe used to both separate and bracket the stored keyword set, so an exact tag
# match is `keywords LIKE '%|tag|%'` — no split, no join table, no ambiguity between
# "Segeln" and "25 Segeln". Safe as a delimiter because decision 0004 reserves `|` as
# the hierarchy separator in `lr:hierarchicalSubject`, so it cannot occur inside a flat
# `dc:subject` tag; one appearing anyway is folded to `/` rather than allowed to split
# a tag in half.
KEYWORD_SEP = "|"


def pack_keywords(values: Any) -> str:
    """
    Render a keyword list into the sentinelled column form.

    Args:
        values: The extracted `dc:subject` items, or None.

    Returns:
        ``"|"`` for no keywords (which still means "indexed, none found" — distinct from
        the NULL of a row not touched since schema v3), else ``"|a|b|"``.
    """
    if not values:
        return KEYWORD_SEP
    cleaned = [str(v).replace(KEYWORD_SEP, "/").strip() for v in values]
    kept = [v for v in cleaned if v]
    if not kept:
        return KEYWORD_SEP
    return KEYWORD_SEP + KEYWORD_SEP.join(kept) + KEYWORD_SEP


def unpack_keywords(packed: Optional[str]) -> list:
    """
    Read the column form back into a list.

    Args:
        packed: The stored value, or None for a row predating schema v3.

    Returns:
        The keywords, in stored order.
    """
    if not packed:
        return []
    return [part for part in packed.split(KEYWORD_SEP) if part]


def _build_row(
    path: Path,
    stat: Any,
    meta: Dict[str, Any],
    gallery_names: set,
    root: str,
) -> dict:
    """Build the upsert dict from a file stat + extracted metadata dict."""
    width, height = _dimensions_from_meta(meta)
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
        "camera_make": meta.get("camera_make"),
        "lens_model": meta.get("lens_model") or "",
        "label": meta.get("label") or "",
        "keywords": pack_keywords(meta.get("keywords")),
        "dimensions": meta.get("dimensions"),
        "width": width,
        "height": height,
        "orientation": derive_orientation(width, height),
        "has_sidecar": _has_sidecar(path),
        "root": root,
        "present": 1,
        # INVARIANT: in_final == 1 iff root == 'final' and present == 1
        "in_final": 1 if root == ROOT_FINAL else 0,
        "published": 1 if path.name in gallery_names else 0,
        "indexed_at": _now_iso(),
    }


_UPSERT_SQL = """
    INSERT INTO photos
        (path, filename, size, mtime, date_taken, rating, iso,
         aperture_f, shutter_s, focal_mm, latitude, longitude,
         camera_model, camera_make, lens_model, label, keywords, dimensions,
         width, height, orientation, has_sidecar, root, present,
         in_final, published, indexed_at)
    VALUES
        (:path, :filename, :size, :mtime, :date_taken, :rating, :iso,
         :aperture_f, :shutter_s, :focal_mm, :latitude, :longitude,
         :camera_model, :camera_make, :lens_model, :label, :keywords, :dimensions,
         :width, :height, :orientation, :has_sidecar, :root, :present,
         :in_final, :published, :indexed_at)
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
        camera_make  = excluded.camera_make,
        lens_model   = excluded.lens_model,
        label        = excluded.label,
        keywords     = excluded.keywords,
        dimensions   = excluded.dimensions,
        width        = excluded.width,
        height       = excluded.height,
        orientation  = excluded.orientation,
        has_sidecar  = excluded.has_sidecar,
        root         = excluded.root,
        present      = excluded.present,
        in_final     = excluded.in_final,
        published    = excluded.published,
        indexed_at   = excluded.indexed_at
"""

_MARK_ABSENT_SQL = "UPDATE photos SET present = 0, in_final = 0 WHERE path = ?"


def _reindex_root(
    conn: sqlite3.Connection,
    root: str,
    directory: Path,
    gallery_names: set,
    stats: Dict[str, int],
) -> int:
    """
    Incrementally index one root and fold the counts into `stats`.

    A missing directory is a no-op — notably it does NOT mark that root's rows
    absent, because an unmounted/renamed folder must never be read as "every
    photo disappeared".

    Args:
        conn: Open connection (transaction is committed by the caller).
        root: 'final' or 'staging'.
        directory: Folder to scan.
        gallery_names: Filenames currently published to the gallery.
        stats: Mutated in place — indexed / updated / skipped / removed.

    Returns:
        Number of JPGs scanned in this root.
    """
    if not directory.exists():
        return 0

    existing: Dict[str, tuple] = {}
    for row in conn.execute(
        "SELECT path, size, mtime, keywords FROM photos WHERE root = ? AND present = 1",
        (root,),
    ):
        existing[row["path"]] = (row["size"], row["mtime"], row["keywords"])

    files = scan_for_images(directory, ".JPG")
    scanned_paths = {str(f) for f in files}

    # Mark rows whose file is no longer in this root
    for stored_path in existing:
        if stored_path not in scanned_paths:
            conn.execute(_MARK_ABSENT_SQL, (stored_path,))
            stats["removed"] += 1

    for file_path in files:
        path_str = str(file_path)
        try:
            stat = file_path.stat()
        except OSError:
            continue

        stored = existing.get(path_str)
        if stored is not None:
            stored_size, stored_mtime, stored_keywords = stored
            # Allow floating-point mtime fuzz of 1ms.
            #
            # `keywords IS NULL` forces a re-read even when the file has not changed: it is
            # the schema-v3 backfill, and it has to ride the ordinary incremental pass
            # because the alternative — rebuilding the index from zero — would take the
            # `trash` table with it, and a trash row is what makes a restore possible. A
            # file with no keywords stores the `|` sentinel, not NULL, so this self-heals
            # exactly once per row and then never fires again.
            unchanged = stored_size == stat.st_size and abs(stored_mtime - stat.st_mtime) < 0.001
            if unchanged and stored_keywords is not None:
                stats["skipped"] += 1
                continue
            meta = MetadataExtractor.extract_metadata(file_path)
            conn.execute(_UPSERT_SQL, _build_row(file_path, stat, meta, gallery_names, root))
            stats["updated"] += 1
        else:
            meta = MetadataExtractor.extract_metadata(file_path)
            conn.execute(_UPSERT_SQL, _build_row(file_path, stat, meta, gallery_names, root))
            stats["indexed"] += 1

    return len(files)


def reindex(
    conn: Optional[sqlite3.Connection] = None,
    final_path: Optional[Path] = None,
    gallery_path: Optional[Path] = None,
    staging_path: Optional[Path] = None,
) -> Dict[str, int]:
    """
    Incrementally update the index for all JPGs in the Final and Staging roots.

    Only files whose (size, mtime) differ from the stored row are re-read via
    extract_metadata(). Rows for files that no longer exist are marked
    present=0 / in_final=0.

    Args:
        conn: Open sqlite3 connection; if None, opens the default DB.
        final_path: Override for FINAL_PATH (used in tests).
        gallery_path: Override for GALLERY_PATH/images parent (used in tests).
        staging_path: Override for STAGING_PATH (used in tests).

    Returns:
        Dict with keys: indexed, updated, skipped, removed (totals across both
        roots) and staging_indexed (JPGs currently scanned in the Staging root).
    """
    close_on_exit = conn is None
    if conn is None:
        conn = get_db()

    fp = final_path or FINAL_PATH
    sp = staging_path or STAGING_PATH
    gp = gallery_path or GALLERY_PATH

    try:
        stats = {"indexed": 0, "updated": 0, "skipped": 0, "removed": 0, "staging_indexed": 0}

        gallery_names = _gallery_images_set(gp)

        _reindex_root(conn, ROOT_FINAL, fp, gallery_names, stats)
        stats["staging_indexed"] = _reindex_root(conn, ROOT_STAGING, sp, gallery_names, stats)

        conn.commit()
        return stats

    finally:
        if close_on_exit:
            conn.close()


def _canonical_path(path: Path, roots: Dict[str, Path]) -> Optional[Tuple[str, Path]]:
    """
    Map a path onto one of the cull roots.

    Resolves symlinks for the containment check but rebuilds the returned path
    from the configured root, so the string form matches exactly what reindex()
    stores (otherwise a re-read would insert a duplicate row).

    Args:
        path: Candidate file path.
        roots: Mapping of root name → root directory.

    Returns:
        (root_name, canonical_path), or None when the path is outside every root.
    """
    try:
        resolved = path.resolve()
    except OSError:
        return None

    for root_name, root_dir in roots.items():
        try:
            resolved_root = root_dir.resolve()
            relative = resolved.relative_to(resolved_root)
        except (OSError, ValueError):
            continue
        return root_name, root_dir / relative
    return None


def reindex_paths(
    paths: Sequence[Path],
    conn: Optional[sqlite3.Connection] = None,
    gallery_path: Optional[Path] = None,
    roots: Optional[Dict[str, Path]] = None,
) -> Dict[str, int]:
    """
    Re-read a specific set of files, bypassing the (size, mtime) skip.

    Used right after a rating/label write-back so the index agrees with the file
    before the UI refetches. Paths outside the cull roots — and system files such
    as macOS `._*` forks — are ignored rather than raising: the caller has already
    rejected traversal attempts, this is defence in depth.

    Args:
        paths: Files to re-read.
        conn: Open sqlite3 connection; if None, opens the default DB.
        gallery_path: Override for GALLERY_PATH (used in tests).
        roots: Override for CULL_ROOTS (used in tests).

    Returns:
        Dict with keys: indexed, updated, removed, skipped.
        `skipped` counts paths outside the cull roots.
    """
    close_on_exit = conn is None
    if conn is None:
        conn = get_db()

    active_roots = roots if roots is not None else {ROOT_FINAL: FINAL_PATH, ROOT_STAGING: STAGING_PATH}

    try:
        stats = {"indexed": 0, "updated": 0, "removed": 0, "skipped": 0}
        if not paths:
            return stats

        gallery_names = _gallery_images_set(gallery_path)

        for raw_path in paths:
            mapped = _canonical_path(Path(raw_path), active_roots)
            if mapped is None or not is_valid_image_file(mapped[1]):
                # Outside the cull roots, or a system file (.DS_Store, ._* fork)
                stats["skipped"] += 1
                continue
            root, file_path = mapped
            path_str = str(file_path)

            try:
                stat = file_path.stat()
            except OSError:
                conn.execute(_MARK_ABSENT_SQL, (path_str,))
                stats["removed"] += 1
                continue

            known = conn.execute(
                "SELECT 1 FROM photos WHERE path = ?", (path_str,)
            ).fetchone() is not None

            meta = MetadataExtractor.extract_metadata(file_path)
            conn.execute(_UPSERT_SQL, _build_row(file_path, stat, meta, gallery_names, root))
            stats["updated" if known else "indexed"] += 1

        conn.commit()
        return stats

    finally:
        if close_on_exit:
            conn.close()
