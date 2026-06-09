# Group 6: SQLite metadata index — schema + incremental indexer

## What You're Doing

Build the SQLite index that powers analytics: a schema mirroring the sparse `extract_metadata()`
dict (most columns nullable), an incremental indexer that scans Final and only re-reads
mtime-changed files (mirroring the `FileManager._hash_cache` key), numeric re-parsing of the
formatted EXIF strings, and per-photo state flags. No API endpoints yet (Group 7).

---

## Research & Exploration First

1. Read `control_panel/PRD.md` → "SQLite metadata index".
2. Read `photo_flow/metadata_extractor.py` `extract_metadata()` — the exact dict keys and which are
   conditional. Note `aperture` (`"f/2.8"`), `shutter_speed` (`"1/250"` or `"1.5"`), `focal_length`
   (`"35mm"`) are **formatted strings** that must be re-parsed to numbers for charts. `iso`,
   `rating`, `latitude`, `longitude`, `date_taken` are already usable.
3. Read `photo_flow/file_manager.py` `_hash_cache` key shape `(path, size, mtime, partial)` and
   `config.py` `FINAL_PATH` / `GALLERY_PATH`.

---

## What to Implement

### `photo_flow/index/db.py` + `photo_flow/index/indexer.py`

- **DB location**: `~/.photoflow/index.db` (create the dir). One table `photos`:
  `path TEXT PRIMARY KEY, filename, size, mtime, date_taken, rating, iso, aperture_f REAL,
  shutter_s REAL, focal_mm REAL, latitude REAL, longitude REAL, camera_model, dimensions,
  in_final INTEGER, published INTEGER, indexed_at`. Make EXIF columns nullable. Add indexes on
  `date_taken` and `rating`.
- **Incremental `reindex()`**: scan `FINAL_PATH` JPGs; for each, if `(path,size,mtime)` is unchanged
  vs the stored row, skip; else `extract_metadata()` + parse strings → upsert. Mark rows whose file
  no longer exists `in_final=0` (or delete). Set `published=1` when the file is present in
  `GALLERY_PATH/images` (or rating≥4 — match the gallery-sync rule; confirm which by reading
  `sync_gallery`). Return counts `{indexed, updated, skipped, removed}`.
- **Parsers** (pure, well-tested): `parse_aperture("f/2.8") -> 2.8`, `parse_shutter("1/250") -> 0.004`,
  `parse_shutter("1.5") -> 1.5`, `parse_focal("35mm") -> 35.0`. Handle missing/malformed → `None`.

Keep it stdlib `sqlite3`. No ORM.

---

## Validation

```bash
venv/bin/python -m pytest -q
```
Tests (`tests/test_index.py`):
- Parsers: table-driven over good/edge/malformed inputs (`"f/1.4"`, `"1/4000"`, `"1.3"`, `""`, `None`).
- Indexer against a **temp dir of tiny fixture JPGs** (or monkeypatched `extract_metadata`):
  first run indexes N; second run with no changes skips all; touching a file's mtime re-reads it;
  removing a file marks it removed.
- Schema round-trips nullable EXIF fields.

Use temp dirs / fixtures — never point the indexer at the real `FINAL_PATH` in tests.

---

## Commit

```
feat(index): SQLite metadata index with incremental reindex + EXIF parsers
```

---

## Done

Append notes (the `published` rule you chose), then:
```
RALPH_TASK_COMPLETE: Group 6
```
