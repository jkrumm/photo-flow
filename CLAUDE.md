# Photo-Flow Technical Reference (AI Agent Optimized)

## Project Overview
Personal CLI tool for managing Fuji X-T4 camera photos/videos with a staging workflow for JPG photography with RAW backups, plus a gallery sync for high-rated images.

**Important**: This is a personal tool designed to run only on the developer's local machine, not production software intended for distribution.

**Key Principle**: Safety-first architecture - no data loss tolerance for irreplaceable photos.

---

## Quick Reference: File Operations Matrix

| Operation | Source | Destination | Delete Original | Hash Verify | Filter |
|-----------|--------|-------------|-----------------|-------------|--------|
| Import Videos | Camera/*.MOV | SSD_PATH | ✅ Yes | ✅ Yes | System files |
| Import Photos | Camera/*.JPG | STAGING_PATH | ✅ Yes | ✅ Yes | System files + already in Final |
| Import RAWs | Camera/*.RAF | RAWS_PATH (SSD) | ✅ Yes | ✅ Yes | System files |
| Finalize | STAGING/*.JPG (+ .photo-edit) | FINAL_PATH (move, full quality) | ✅ Yes | ✅ Yes | None |
| Gallery Sync | FINAL/*.JPG (rating ≥ 4) | GALLERY_PATH/images | ❌ No | ✅ Yes | Rating-based |
| Backup (final) | FINAL/* (JPG + .photo-edit) | homelab SSD (rclone, 30d trash) | ❌ No | rclone | System files |
| Backup (staging, opt-in) | STAGING/* (JPG + .photo-edit) | homelab SSD (rclone, **no trash**) | ❌ No | rclone | System files |
| Migrate | Any folder | Same folder (rename in-place) | ✅ Yes (atomic) | ✅ Yes | Already renamed |

---

## Timestamp-Based File Naming

### Problem
Fuji X-T4 uses DSCF numbering (0001-9999) which wraps after 10,000 photos. With all photos in a single Final folder, filename collisions occur.

### Solution
Files are renamed at import time to format: `YYYY-MM-DD_HH-MM-SS_<original_base>.<ext>`
- Example: `2026-01-28_10-29-15_DSCF1234.JPG`
- With collision (burst photos): `2026-01-28_10-29-15-2_DSCF1234.JPG`

### Key Design Decisions

1. **Rename at Import Time** (not Finalize)
   - All file types (JPG, RAF, MOV) get consistent naming immediately
   - `DateTimeOriginal` is immutable - editing/cropping doesn't affect it
   - JPG and RAF share identical timestamps → correlation preserved

2. **Original Base Preserved**
   - `DSCF0430` stays in filename after timestamp prefix
   - RAW-JPG correlation: `extract_original_base()` extracts `DSCF0430` from both formats
   - Cleanup operations use original base for matching

3. **Collision Handling**
   - Counter suffix: `-2`, `-3`, etc. before the original base
   - Example: `2026-01-28_10-29-15-2_DSCF1234.JPG`

### Module: `timestamp_renamer.py`

```python
is_already_renamed(filename: str) -> bool
  # Regex: ^\d{4}-\d{2}-\d{2}_\d{2}-\d{2}-\d{2}(-\d+)?_

extract_original_base(filename: str) -> str
  # "2026-01-28_10-29-15_DSCF0430.JPG" → "DSCF0430"

correlation_base(filename: str) -> str
  # Like extract_original_base, but also strips Photomator's duplicate suffix (_2, _3, …):
  # "2026-03-03_17-36-33_DSCF0770_2.jpg" → "DSCF0770"
  # USE THIS for JPG↔RAW orphan matching — a mismatch deletes an irreplaceable RAW.

get_timestamp_from_exif(file_path: Path) -> Optional[datetime]
  # Uses exiftool -DateTimeOriginal (reliable, survives edits)

generate_timestamped_filename(file_path: Path, existing_names: set) -> tuple[str, str]
  # Returns (new_filename, error_message)
  # Handles collisions with counter suffix
```

---

## System Architecture

### File Paths (Hardcoded in config.py)
```python
CAMERA_PATH = Path("/Volumes/Fuji X-T4/DCIM")
STAGING_PATH = Path("/Users/johannes.krumm/Pictures/Staging")
RAWS_PATH = Path("/Volumes/EXT/Bilder/RAWs")
FINAL_PATH = Path("/Users/johannes.krumm/Pictures/Final")
SSD_PATH = Path("/Volumes/EXT/Videos/Videos")
GALLERY_PATH = Path("/Users/johannes.krumm/SourceRoot/photo-flow/photo_gallery/src")
```

### Remote Destinations

**Homelab Backup** (in config.py):
- User: `jkrumm`
- Host: `100.85.139.104` (Tailscale IP)
- Path: `/home/jkrumm/ssd/SSD/Bilder/Fuji`
- Method: rsync via Tailscale (encrypted mesh network)
- Exclusions: System files (`.DS_Store`, `._*`, `Thumbs.db`, etc.) are filtered out

**Gallery Sync** (in config.py — `GALLERY_REMOTE_USER`/`HOST`/`PATH`):
- User: `jkrumm`
- Host: `100.97.220.54` (VPS Tailscale IP)
- Path: `/home/jkrumm/photo-gallery-dist` (served by nginx — see `vps/apps/photo-gallery/`)
- Method: rsync over Tailscale (aes128-gcm, no compression) after npm build
- Public URL: `https://photos.jkrumm.com`

### Technology Stack

**CLI core:**
- **Python 3.9+** with venv/pipx
- **Click 8.1.8+** - CLI framework
- **Rich 13.7.0+** - Terminal output and formatting
- **Pillow 11.2.1+** - Image processing
- **piexif 1.1.3+** - EXIF read/write
- **defusedxml 0.7.1+** - Secure XML parsing
- **External**: exiftool (metadata), rsync (backup), npm/Node.js (gallery)

**Control panel (v0.4.0+):**
- **FastAPI 0.115+ / uvicorn / sse-starlette** — API server (`photo_flow/api/`)
- **SQLite** (stdlib) — metadata index at `~/.photoflow/index.db`
- **Vite 8 + React 19 + TanStack Router/Query + Mantine 9** — SPA (`control_panel/web/`)
- **framer-motion** — animated pipeline hero
- **visx** (`@argo/charts` vendored) — analytics charts with Blueprint token system
- Port: `127.0.0.1:7717` (localhost only, never exposed)

### Control Panel Architecture

```
photo_flow/ (Python core — unchanged)
  PhotoWorkflow · FileManager · MetadataExtractor · config · immich
       │ imports directly (no shell-out)
       ├── cli.py (Click / RichReporter)
       ├── photo_flow/api/ (FastAPI — QueueReporter → SSE)
       │     app.py · routes_status · routes_ops · routes_jobs · routes_analytics
       │     jobs.py (asyncio.to_thread + single-flight Lock)
       │     Serves static control_panel/web/dist/ + SPA fallback
       └── photo_flow/index/ (SQLite metadata cache at ~/.photoflow/index.db)

control_panel/web/       Vite React SPA (build → dist/ served by FastAPI)
control_panel/launchd/   LaunchAgent plist (KeepAlive, RunAtLoad, localhost:7717)
```

**Event seam:** `ProgressReporter` protocol — `RichReporter` for CLI (Rich bars unchanged),
`QueueReporter` for API (pushes structured events onto an asyncio.Queue drained by SSE).

**Job lifecycle:** `POST /ops/{name}?dry_run=true` → preview dict → UI confirm modal →
`POST /ops/{name}` → SSE stream at `GET /events/{job_id}` → terminal result at `GET /jobs/{job_id}`.

**Serving:** `photoflow serve` runs uvicorn; FastAPI mounts the built SPA at `/` with a catch-all
SPA fallback after all `/api`-prefixed routes are registered. Dev: Vite on port 7718 proxies
`/health`, `/status`, `/ops`, `/jobs`, `/events`, `/analytics`, `/index`, `/backup`, `/gallery` to 7717.

---

## Core Components

### 1. File Manager (`file_manager.py`)

**Functions:**
```python
is_valid_image_file(file_path: Path) -> bool
  # Filters: .DS_Store, ._* (macOS forks), Thumbs.db (Windows)
  # Returns: True if valid image/video file

scan_for_images(directory: Path, extension: str = '.JPG') -> List[Path]
  # Case-insensitive extension matching
  # Recursive scan with is_valid_image_file filter
```

**FileManager Class:**
```python
class FileManager:
    _hash_cache = {}  # Class-level cache: {(path, size, mtime, partial): hash}

    @staticmethod
    scan_camera_files() -> Dict[str, List[Path]]
      # Scans: /Volumes/Fuji X-T4/DCIM/*/* (all subfolders)
      # Returns: {'.JPG': [...], '.RAF': [...], '.MOV': [...]}

    @classmethod
    is_duplicate(src: Path, dst: Path) -> tuple[bool, str]
      # Step 1: Size comparison (fast check)
      # Step 2: Hash comparison (MD5, cached)
      # Partial hashing: Files >10MB = first 1MB + last 1MB only
      # Returns: (is_identical, error_message)

    @classmethod
    safe_copy(src: Path, dst: Path) -> tuple[bool, str]
      # Step 1: Skip if destination exists and is identical
      # Step 2: shutil.copy2() with metadata preservation
      # Step 3: Hash verification of copy
      # Step 4: Create parent directories automatically
      # Returns: (success, error_message)

    @classmethod
    get_file_hash(file_path: Path, partial: bool = True) -> tuple[str, str]
      # Algorithm: MD5
      # Partial mode (files >10MB): first 1MB + last 1MB
      # Cache key: (path, size, mtime, partial_flag)
      # Returns: (hash_string, error_message)
```

**Hash Caching Strategy:**
- **Cache storage**: Class-level dict `_hash_cache`
- **Cache key**: `(path_string, file_size, modification_time, partial_flag)`
- **Partial hashing**: Files >10MB → read first 1MB + last 1MB (performance optimization)
- **Full hashing**: Files ≤10MB → read entire file

---

### 2. Image Processor (`image_processor.py`)

> **Not used by the pipeline since v0.3.4.** Finalize no longer re-compresses (full-quality
> masters). This module is retained for reference / possible opt-in compression, but no command
> currently calls it.

```python
class ImageProcessor:
    @staticmethod
    compress_jpeg_safe(input_path: Path, output_path: Path = None,
                       max_width: int = 5200, max_height: int = 3467,
                       quality: int = 92) -> tuple[bool, str]
```

**Compression Process (Atomic with Backup):**
1. **Create temp file**: Compressed image with Pillow (Lanczos resampling)
2. **Copy metadata**: exiftool copies ALL metadata (EXIF, IPTC, XMP, ratings)
3. **Verify integrity**: PIL Image.open().verify() on compressed file
4. **Create backup**: `.backup` file of original (safety net)
5. **Atomic replace**: temp_file.replace(input_path) - OS-level atomic operation
6. **Cleanup**: Remove backup on success, restore on failure

**Parameters:**
- `max_width`: 5200 pixels (default, 83% of X-T4 native 6240)
- `max_height`: 3467 pixels (default, 83% of X-T4 native 4160)
- `quality`: 92 (JPEG quality, 0-100, optimal balance)
- `subsampling`: 0 (4:4:4 chroma - full color preservation)
- `progressive`: True (better web loading)
- Aspect ratio: Always maintained
- Resampling: Lanczos (high quality)

**Metadata Preservation:**
- ⚠️ **Critical**: Must use exiftool (NOT piexif or Pillow alone)
- Preserves: EXIF, IPTC, XMP, ratings, keywords, GPS, all camera settings
- Command: `exiftool -TagsFromFile {original} -all:all -overwrite_original {compressed}`

---

### 3. Metadata Extractor (`metadata_extractor.py`)

```python
class MetadataExtractor:
    @staticmethod
    extract_metadata(image_path: Path) -> Dict[str, Any]
```

**Extracted Fields:**

| Category | Fields | Default Values |
|----------|--------|----------------|
| **Basic** | filename, file_size (MB) | Required |
| **Image** | width, height | From PIL |
| **XMP** | rating (0-5), title, description | 0, "", "" |
| **EXIF** | camera_make, camera_model, iso, aperture (f-number), shutter_speed (exposure_time), focal_length (mm), date_taken (ISO format) | "" or None |
| **GPS** | latitude, longitude (decimal degrees) | None, None |

**XMP Rating Extraction:**
- **Critical fix**: Handles XMP Description as both dict AND list of dicts (Pillow returns either format)
- Searches multiple namespaces: `xmp`, `photoshop`, Adobe RDF structures
- Iterates through all Description blocks to find Rating, title, description fields
- Rating range: 0-5 (integer)
- Default if missing: 0
- Used by: Gallery sync filter (rating ≥ 4)

**GPS Conversion:**
- Input: EXIF format (degrees, minutes, seconds as tuples)
- Output: Decimal degrees (float)
- References: N/S (latitude), E/W (longitude)
- Formula: `degrees + minutes/60 + seconds/3600` (negative for S/W)

**JSON Generation:**
```python
@staticmethod
generate_metadata_json(images_metadata: list, output_path: Path) -> bool
```
- Output format: `{"generated_at": "ISO timestamp", "total_images": int, "images": [...]}`
- Creates parent directories automatically
- Used by: Gallery sync for high-rated images only

---

### 4. Workflow Manager (`workflow.py`)

**StatusReport Dataclass:**
```python
@dataclass
StatusReport:
    camera_connected: bool
    ssd_connected: bool
    pending_videos: int
    pending_photos: int      # Excludes JPGs already in Final
    pending_raws: int
    staging_files: int
```

**PhotoWorkflow Class:**

#### `import_from_camera(dry_run=False, progress_callback=None) -> Dict[str, int]`
**Process:**
1. Scan camera: `FileManager.scan_camera_files()`
2. **Smart filtering**: Exclude JPGs already in FINAL_PATH (prevents re-importing finalized photos)
3. Route files:
   - MOV → SSD_PATH (MOVE: delete original after verify)
   - JPG → STAGING_PATH (MOVE: delete original after verify)
   - RAF → RAWS_PATH (MOVE: delete original after verify)
4. Duplicate detection: Skip if hash matches destination
5. **Output**: Rich Progress bar with file count and completion percentage

**Returns:**
```python
{
    'videos': int,      # MOV files copied
    'photos': int,      # JPG files copied
    'raws': int,        # RAF files copied
    'skipped': int,     # Duplicates skipped
    'errors': int       # Failed operations
}
```

#### `finalize_staging(dry_run=False, progress_callback=None) -> Dict[str, int]`
**Process (4 steps with separate Rich Progress bars):**
1. **Atomic Move (full quality, no re-compression)**: For each Staging JPG (one at a time):
   - Copy Staging JPG → Final byte-for-byte (safe_copy with hash verify) — **no re-encoding**
   - Move the matching `.photo-edit` sidecar (Photomator edit history) alongside its JPG, if present
   - **Duplicate JPGs still hand over their sidecar**: when the JPG is already in Final and the
     hash matches, the JPG is skipped but the sidecar is moved anyway — skipping it strands the
     edit history in Staging forever
   - Delete from Staging (only after the verified copy)
   - **Interrupt-safe**: Remaining files stay in Staging, retry processes them
   - **Why no compression**: Photomator bakes its edits and embeds the star rating into the
     JPG itself, so the Staging JPG is already the finished full-quality master. The web
     gallery downscales on demand (Astro + sharp), so Final never needs to be small.
   - **Output**: Progress bar for move operations
2. **Sidecar reconciliation** (`_reconcile_staging_sidecars`, "Step 1b"): the loop above iterates
   JPGs, so a sidecar whose JPG already left Staging (interrupted earlier run) is unreachable by
   it. This sweep moves such a sidecar to Final if its JPG is there, and **leaves + warns** if the
   JPG exists nowhere — irreplaceable history is never auto-deleted. `._*` AppleDouble forks are
   ignored. Runs on the "no photos in staging" early return too, and is skipped when cancelled.
3. **Delete camera RAWs**: Matching RAFs for finalized JPGs (if camera connected)
   - **Output**: Info messages for each deletion
4. **Cleanup orphaned RAWs**: Local RAFs whose base matches no JPG in **Final OR Staging**
   (`compute_raw_keep_bases`, Photomator-suffix tolerant). Staging is included so RAWs for
   photos still awaiting finalize are never deleted.
   - **Output**: Info messages for cleanup operations

**Returns:**
```python
{
    'moved': int,                # JPGs moved from staging to Final
    'edits_moved': int,          # .photo-edit sidecars moved alongside their JPGs
    'orphaned_raws': int,        # Local RAWs found without Final JPG
    'deleted_raws': int,         # Local orphaned RAWs deleted
    'deleted_camera_raws': int,  # Camera RAWs deleted
    'skipped': int,
    'errors': int
}
```

#### `cleanup_unused_raws(dry_run=False, progress_callback=None) -> Dict[str, int]`
**Process:**
1. Build the keep-set: bases of every JPG in **Final AND Staging** (`compute_raw_keep_bases`,
   Photomator-suffix tolerant via `correlation_base`)
2. Scan RAWs folder for RAFs
3. Identify orphaned RAWs (base matches no Final/Staging JPG). **Including Staging is critical** —
   a RAW whose JPG is still awaiting finalize is NOT an orphan; deleting it is irreversible loss.
4. **Always preview first** (shows list)
5. **Confirmation required** (unless dry-run)
6. Delete orphaned RAWs
   - **Output**: Rich Progress bar for deletion operations

**Returns:**
```python
{
    'orphaned': int,    # RAWs without matching JPG
    'deleted': int,     # RAWs actually deleted
    'errors': int
}
```

#### `get_status() -> StatusReport`
**Checks:**
- Camera: `/Volumes/Fuji X-T4/DCIM` exists
- SSD: `/Volumes/EXT` exists
- Pending camera files: **Excludes JPGs already in Final** (smart filtering)
- Staging count: Files waiting for finalization

#### `sync_gallery(dry_run=False, progress_callback=None) -> Dict[str, int]`
**Process (8 steps with Rich output):**
1. Scan Final folder for all JPGs
2. Extract metadata for each image
   - **Output**: Rich Progress bar for metadata extraction
3. **Filter**: rating ≥ 4 only
4. Copy high-rated images to `GALLERY_PATH/images/` (skip if unchanged by hash)
   - **Output**: Rich Progress bar for file operations
5. Remove gallery images no longer rated 4+
6. Generate `metadata.json` with high-rated images only
7. **Build**: `npm run build` in photo_gallery/ (uses .nvmrc Node version)
   - **Output**: Rich status spinner during build
8. **Sync**: rsync dist/ to remote server
   - **Output**: Rich status spinner during sync

**Logging**: Uses Python's `logging` module with `logger.debug()` for debug output and `logger.error()` for errors

**Remote Destination (config.py):**
- `GALLERY_REMOTE_USER` / `GALLERY_REMOTE_HOST` / `GALLERY_REMOTE_PATH`
- Resolves to: `jkrumm@100.97.220.54:/home/jkrumm/photo-gallery-dist`
- The VPS serves that directory via nginx (`vps/apps/photo-gallery/compose.yml`) behind Traefik at `https://photos.jkrumm.com`

**Returns:**
```python
{
    'scanned': int,             # Total JPGs scanned in Final
    'synced': int,              # High-rated images copied to gallery
    'removed': int,             # Gallery images removed (rating dropped)
    'unchanged': int,           # Images skipped (already in gallery, unchanged)
    'json_updated': bool,       # metadata.json generated
    'total_in_gallery': int,    # Total images in gallery after sync
    'build_successful': bool,   # npm build succeeded
    'sync_successful': bool,    # rsync to remote succeeded
    'errors': int
}
```

#### `backup_final_to_homelab(dry_run=False, progress_callback=None) -> Dict[str, int]`
**Process (Tailscale Connection):**
1. **Safety check**: Verify Final folder has sufficient files (prevents accidental wipe)
   - **Output**: Warning messages if folder appears empty/unmounted
2. **Connect via Tailscale**: SSH to homelab Tailscale IP (timeout: 5s)
   - **Output**: Info message about connection attempt
3. Rsync flags: `-a --partial --whole-file --progress`
4. **Exclusions**: Filters out system files (`.DS_Store`, `._*`, `Thumbs.db`, `.Spotlight-V100`, `.Trashes`, `.fseventsd`)
5. SSH cipher: `aes128-gcm@openssh.com` (fast, no compression)
   - **Output**: Rich Progress bar + success/error messages

**SSH Configuration:**
- `ssh -T -c aes128-gcm@openssh.com -o Compression=no -o ConnectTimeout=5`

**Returns:**
```python
{
    'scanned': int,                     # Files in Final folder
    'sync_successful': bool,            # Rsync succeeded
    'connection_method': str,           # 'tailscale'
    'errors': int
}
```

---

### 5. Console Utilities (`console_utils.py`)

**Purpose**: Centralized Rich console helpers for consistent terminal output

**Functions:**
```python
success(message: str) -> None
  # Green checkmark + message

error(message: str) -> None
  # Red X + message

warning(message: str) -> None
  # Yellow ! + message

info(message: str) -> None
  # Plain message

show_status(message: str, spinner: str = "dots")
  # Context manager for status spinner

create_progress() -> Progress
  # Rich Progress instance with standard columns

print_summary(title: str, stats: dict) -> None
  # Formatted summary of operation results
```

**Console Instance**: Single `console` object used throughout app for output

**Logging Configuration**: Python logging set to ERROR level (silences debug/info)

---

### 6. CLI Interface (`cli.py`)

**Command Group:**
```python
@click.group()
def photoflow()
```

**Commands:**

| Command | Flag | Description | Output Style |
|---------|------|-------------|--------------|
| `photoflow status` | - | Check workflow status | Rich tables with color-coded status |
| `photoflow import` | `--dry-run` | Import from camera (with timestamp rename) | Progress updates + success/error + summary |
| `photoflow finalize` | `--dry-run` | Staging → Final → Camera → Cleanup | Progress updates + summary |
| `photoflow cleanup` | `--dry-run` | Delete orphaned RAWs | Preview + confirmation + summary |
| `photoflow sync-gallery` | `--dry-run` | High-rated photos to gallery | Progress updates + build/sync status + summary |
| `photoflow backup` | `--dry-run` | Backup Final to homelab | Status updates + connection method + summary |

**Output Features:**
- **Rich Progress bars**: All file operations show real-time progress with completion percentage
- **Status spinners**: Long-running operations (npm build, rsync) show animated spinners
- **Color-coded messages**: Success (green ✓), error (red ✗), warning (yellow !), info (plain)
- **Structured summaries**: All commands end with formatted result tables
- **Clean output**: Progress updates in-place (no terminal flooding)
- **Consistent styling**: Uniform Rich formatting throughout all commands

---

## Implementation Details

### File Extension Handling
- **Case-insensitive**: .JPG/.jpg, .RAF/.raf, .MOV/.mov all accepted
- **Extensions set**: `{'.JPG', '.RAF', '.MOV'}` in config.py
- **Comparison**: Always uses `.upper()` normalization

### System File Filtering

**Location 1: file_manager.py:is_valid_image_file**
**Excluded from local operations (import, scan):**
- macOS: `.DS_Store`, `._*` (AppleDouble resource forks)
- Windows: `Thumbs.db`
- Implementation: Basename check with startswith/equals

**Location 2: config.py:RSYNC_EXCLUDE_PATTERNS**
**Excluded from rsync backup:**
- `.DS_Store` - macOS folder view settings
- `._*` - macOS AppleDouble resource forks (extended attributes)
- `Thumbs.db` - Windows thumbnail cache
- `.Spotlight-V100` - macOS Spotlight index
- `.Trashes` - macOS trash folder
- `.fseventsd` - macOS filesystem events
- Implementation: rsync `--exclude` flags per pattern

**Why exclude these files:**
1. Not portable across operating systems
2. Not part of actual photo data
3. Automatically regenerated by OS
4. Can be large and slow down transfers (especially `._*` resource forks)

**Metadata preservation:**
- ✅ **EXIF/IPTC/XMP data is embedded in JPG files** (via exiftool during compression)
- ✅ **Adobe Bridge ratings are embedded in JPG files** (no XMP sidecars needed)
- ✅ All important metadata travels with the JPG file itself

### Smart Import Filtering
**Location**: workflow.py:import_from_camera()
**Logic**: Content-based duplicate detection using file hashes
**Reason**: Prevents re-importing photos that already exist in destination folders
**Implementation**: Hash comparison for files with matching original base names

### Atomic Finalize Workflow (Critical Architecture)
**Location**: workflow.py:finalize_staging()
**Pattern**: Verified copy-then-delete per file (full quality, no re-compression)

**Flow for each Staging file:**
```python
1. safe_copy(staging_file → Final) with hash verify   # byte-for-byte, no re-encode
2. If a matching <stem>.photo-edit sidecar exists: safe_copy it → Final, then delete it from Staging
   (_move_sidecar — same verified copy-then-delete contract; also runs on the duplicate-skip path)
3. Delete staging JPG (only after step 1 verified)

After the loop: _reconcile_staging_sidecars() sweeps sidecars the JPG-driven loop cannot reach.
```

**Architecture guarantees:**
- ✅ **Atomic per-file**: Each file fully processed or stays in Staging
- ✅ **Interrupt-safe**: Ctrl+C at any point leaves consistent state
- ✅ **Idempotent**: Re-running processes remaining Staging files
- ✅ **No quality loss**: Final JPGs are byte-identical to the Photomator-edited masters
- ✅ **Edit history preserved**: `.photo-edit` sidecars travel with their JPG (re-editable in
  Photomator), on every path — normal move, duplicate skip, and the post-loop sweep. A sidecar is
  never deleted by photo-flow; the only copy of an edit history has no second source.
- ✅ **Retry-friendly**: Failed files stay in Staging for next run

**Why no compression (changed in v0.3.4):**
- Photomator (with "modify originals" on) bakes its pixel edits AND embeds the star rating
  into the JPG itself. The Staging JPG is already the finished, full-quality master.
- Re-encoding it (the old 5200×3467 / Q92 step) only added generation loss and would orphan
  the `.photo-edit` history. Storage is cheap (homelab + external SSD); irreplaceable quality is not.
- The only consumer that needs small images is the web gallery, which downscales on demand at
  build time (Astro `<Image>` + sharp). Immich generates its own thumbnails.

### Delete Behavior Specifics
- **Import videos**: ✅ Deleted from camera after successful copy (MOVE operation)
- **Import photos**: ✅ Deleted from camera after successful copy (MOVE operation)
- **Import RAWs**: ✅ Deleted from camera after successful copy (MOVE operation)
- **Finalize**: ✅ Deleted from staging after compress+copy to Final (atomic)
- **Finalize RAWs**: ✅ Matching RAWs deleted from camera (legacy - usually already deleted during import)

---

## Common AI Agent Modification Scenarios

### 1. Adding New File Type Support
**Files to modify:**
1. `config.py`: Add extension to `EXTENSIONS` set
   ```python
   EXTENSIONS = {'.JPG', '.RAF', '.MOV', '.PNG'}  # Added .PNG
   ```
2. `file_manager.py:scan_camera_files()`: Update categorization logic (line ~45)
3. `workflow.py:import_from_camera()`: Add routing logic for new type
4. `cli.py:import_cmd()`: Update CLI help text and results display

### 2. Changing Compression Settings
**Location**: `image_processor.py:compress_jpeg_safe()`
**Parameters:**
- `max_width=5200` - Maximum width in pixels (83% of X-T4 native)
- `max_height=3467` - Maximum height in pixels (83% of X-T4 native)
- `quality=92` - JPEG quality (0-100, optimal balance for quality>size)
- `subsampling=0` - 4:4:4 chroma sampling (full color preservation)
- `progressive=True` - Progressive JPEG for better web loading

**⚠️ Critical**: Always use exiftool for metadata preservation
**Test command**: `photoflow finalize --dry-run`

### 3. Modifying Gallery Rating Filter
**Location**: `workflow.py:sync_gallery()` line ~593
**Current filter**: `if rating >= 4:`
**To change threshold**: Modify comparison value (e.g., `>= 3` for 3+ stars)

### 4. Changing the Gallery Remote Destination
**Location**: `config.py` — `GALLERY_REMOTE_USER` / `GALLERY_REMOTE_HOST` / `GALLERY_REMOTE_PATH`
**To repoint** (e.g., new server or path):
1. Update the constants in `config.py`
2. Ensure the destination directory exists on the remote
3. If on a new server, ensure the host serves it via nginx + Traefik (see `vps/apps/photo-gallery/`)
4. Test: `photoflow sync-gallery --dry-run`

### 5. Changing Hash Algorithm
**Location**: `file_manager.py:get_file_hash()`
**Current**: MD5 (line ~110)
**To change**: Replace `hashlib.md5()` with `hashlib.sha256()` or other
**⚠️ Impact**: Breaks hash cache, forces re-computation

---

## Error Handling & Troubleshooting

### Common Error Patterns

| Error Message | Cause | Solution | File Location |
|---------------|-------|----------|---------------|
| "exiftool not found" | Missing dependency | `brew install exiftool` | image_processor.py:75 |
| "Camera not connected" | Volume not mounted | Check `/Volumes/Fuji X-T4/DCIM` exists | workflow.py:513 |
| "SSD not connected" | Volume not mounted | Check `/Volumes/EXT` exists | workflow.py:514 |
| "Hash mismatch" | Copy verification failed | Auto-retry mechanism (already implemented) | file_manager.py:93 |
| "npm build failed" | Wrong Node version | Check `.nvmrc` in photo_gallery/, install correct version | workflow.py:665 |
| "rsync timeout" | Network connectivity | Check Tailscale connection (`tailscale status`) | workflow.py |
| "Permission denied" | Write access issue | Check directory permissions with `ls -la` | Various |
| "Disk space full" | Insufficient space | Free up space on target drive | Various |

### Dependency Validation
**Location**: Check at operation start
**Required tools:**
- `exiftool` - Metadata preservation (compress, finalize)
- `rsync` - Remote sync (backup, gallery sync)
- `npm` - Gallery build (sync-gallery)
- Node.js - Gallery build (uses .nvmrc version via nvm)

### Pre-flight Checks
**Performed by each command:**
1. Path validation: Check source/destination exist
2. Disk space: Verify sufficient space for operation
3. Permission checks: Test write access to destination
4. Tool availability: Verify external dependencies exist

---

## Code Quality Standards

### Output & Logging System

**Architecture**: Hybrid Rich + Python logging approach

**Rich Console** (User-Facing Output):
- **Location**: `console_utils.py`
- **Purpose**: All user-visible output
- **Usage**:
```python
from photo_flow.console_utils import console, success, error, info

info("Processing files...")
success("Operation completed successfully!")
error("Failed to process file")
console.print("[cyan]Status update[/cyan]")
```

**Python Logging** (Debug/Error Tracking):
- **Location**: workflow.py, other modules
- **Configuration**: cli.py sets level to ERROR (silences debug/info)
- **Usage**:
```python
import logging
logger = logging.getLogger(__name__)

# Debug (silent unless logging reconfigured):
logger.debug(f"Existing gallery images: {len(existing_gallery_images)}")

# Error (always shown):
logger.error(f"Error during build or sync: {e}")
```

**Standard**:
- ✅ Do: Use Rich console functions for all user output
- ✅ Do: Use `logger.debug()` for diagnostic info (normally hidden)
- ✅ Do: Use `logger.error()` for error tracking
- ❌ Don't: Use `print()` for any output
- ❌ Don't: Use click.echo() (use Rich instead)

### Type Hints

**Current coverage**: Good for most functions
**Missing**: `progress_callback` parameter type hints
**Standard type**: `Optional[Callable[[str], None]]`

**Example fix**:
```python
from typing import Callable, Optional

def sync_gallery(
    dry_run: bool = False,
    progress_callback: Optional[Callable[[str], None]] = None
) -> Dict[str, int]:
```

### Return Type Patterns

**Tuple format** (success/error):
```python
tuple[bool, str]  # (success: bool, error_message: str)
```

**Dict format** (operation results):
```python
Dict[str, int]    # {'processed': 10, 'skipped': 2, 'errors': 0}
Dict[str, Any]    # Mixed types: {'success': bool, 'count': int, 'message': str}
```

**Always include**: Docstrings with return value descriptions

### Docstring Standard
```python
def function_name(param1: Type1, param2: Type2) -> ReturnType:
    """
    Brief description of what the function does.

    Args:
        param1: Description of param1
        param2: Description of param2

    Returns:
        Description of return value structure

    Raises:
        ExceptionType: When this exception occurs
    """
```

---

## Safety Mechanisms (Critical for AI Modifications)

### 1. Copy-First Verification
- **Never delete source** until copy verified with hash comparison
- **Implementation**: `safe_copy()` always verifies before returning success
- **Where**: file_manager.py:safe_copy()

### 2. Atomic File Operations
- **Use**: `temp_file.replace(target)` for atomic OS-level operations
- **Never**: Write directly to final destination
- **Where**: image_processor.py:compress_jpeg_safe()

### 3. Backup/Restore System
- **Create**: `.backup` files before in-place modifications
- **Restore**: On any failure during modification
- **Delete**: Only after successful operation
- **Where**: image_processor.py:compress_jpeg_safe()

### 4. Metadata Preservation
- **Tool**: exiftool (REQUIRED - not optional)
- **Preserves**: EXIF, IPTC, XMP, ratings, keywords, GPS, all camera data
- **Never**: Use piexif or Pillow alone for metadata preservation
- **Where**: image_processor.py:compress_jpeg_safe() line ~85

### 5. Interruptible Operations (Ctrl+C Safe)
- **Temporary files**: Auto-cleanup on interruption
- **Original files**: Never left in invalid states
- **Operations**: Can be resumed without conflicts
- **Implementation**: Context managers, try/finally blocks
- **Atomic finalize**: Each file is compress→copy→delete as single unit
  - Ctrl+C leaves remaining files in Staging
  - Re-running processes remaining files
  - **Guarantees**: Files in Final are ALWAYS compressed (no uncompressed files possible)

### 6. Hash-Based Verification
- **Algorithm**: MD5 (fast, sufficient for duplicate detection)
- **Optimization**: Partial hashing for files >10MB (first+last 1MB)
- **Cache**: Class-level dict prevents re-computation
- **Where**: file_manager.py:get_file_hash()

### 7. Dry-Run Mode
- **All commands**: Support `--dry-run` flag
- **Behavior**: Execute same code path, skip actual file operations
- **Use**: Always test with dry-run first
- **Where**: Every CLI command in cli.py

### 8. Confirmation Prompts
- **Destructive operations**: Require explicit confirmation
- **Example**: cleanup command always previews before deletion
- **Where**: cli.py:cleanup() (preview + confirmation)

---

## Testing Checklist for AI Modifications

### After Any Code Change:

**0. Update documentation (MANDATORY):**
- [ ] Update README.md if user-facing behavior changed
- [ ] Update CLAUDE.md if internal behavior or architecture changed
- [ ] Update SAFETY.md if safety mechanisms changed
- [ ] Keep documentation in sync with code changes

**1. Run dry-run tests:**
```bash
photoflow status
photoflow import --dry-run
photoflow finalize --dry-run
photoflow cleanup --dry-run
photoflow sync-gallery --dry-run
photoflow backup --dry-run
```

**2. Verify safety mechanisms:**
- [ ] No direct file deletions (must be after verification)
- [ ] Atomic operations use .replace()
- [ ] Metadata preservation uses exiftool
- [ ] Error handling with try/except blocks
- [ ] Cleanup in finally blocks

**3. Check code quality:**
- [ ] Use logging module instead of print() statements
- [ ] Type hints on all parameters
- [ ] Docstrings on all functions
- [ ] Return types documented
- [ ] Error messages are clear and actionable

**4. Test actual operations (small batch):**
- Create test folder with 2-3 files
- Run actual command (non-dry-run)
- Verify: file integrity, metadata preservation, expected results

---

## Architecture Diagram

```
┌─────────────────────────────────────────────────────────────┐
│  Camera: /Volumes/Fuji X-T4/DCIM/*/*                        │
│  ├── DSCF0430.JPG  ┐                                        │
│  ├── DSCF0430.RAF  │  Import                                │
│  └── DSCF1451.MOV  ┘                                        │
└────────┬─────────────────────────────────────────────────────┘
         │
         │ photoflow import (hash verify, smart filter)
         │
         ├──────────────────┬───────────────────┬──────────────
         │                  │                   │
         ▼                  ▼                   ▼
    ┌─────────┐       ┌──────────┐      ┌──────────────┐
    │ Staging │       │   RAWs   │      │  SSD/Videos  │
    │  .JPG   │       │   .RAF   │      │    .MOV      │
    └────┬────┘       └─────┬────┘      └──────────────┘
         │                  │
         │ finalize         │ cleanup (orphaned RAWs)
         │ (compress)       │
         ▼                  ▼
    ┌──────────────────────────┐
    │   Final/                 │
    │   Full-quality JPGs      │
    │   (+ .photo-edit history)│
    └────┬─────────────────┬───┘
         │                 │
         │ backup          │ sync-gallery (rating ≥ 4)
         │                 │
         ▼                 ▼
    ┌──────────────┐    ┌─────────────────┐
    │   Homelab    │    │  photo_gallery/ │
    │   Backup     │    │  ├── src/images │
    │   (rsync)    │    │  └── dist/      │
    └──────────────┘    └────────┬────────┘
                                 │ npm build + rsync
                                 ▼
                        ┌──────────────────┐
                        │  Remote Server   │
                        │  Gallery Deploy  │
                        └──────────────────┘
```

---

## File Workflow States

```
State 1: ON CAMERA
├── DSCF0430.JPG (original)
├── DSCF0430.RAF (original)
└── DSCF1451.MOV (original)

↓ photoflow import

State 2: IMPORTED
├── Camera: (empty - all files moved)
├── Staging: DSCF0430.JPG
├── RAWs: DSCF0430.RAF
└── SSD: DSCF1451.MOV

↓ photoflow finalize

State 3: FINALIZED
├── Camera: (empty - files moved during import)
├── Staging: (empty)
├── RAWs: DSCF0430.RAF (kept)
├── Final: DSCF0430.JPG (compressed)
└── SSD: DSCF1451.MOV

↓ photoflow sync-gallery (if rating ≥ 4)

State 4: PUBLISHED
├── Final: DSCF0430.JPG
├── Gallery: DSCF0430.JPG (copy)
└── Remote: DSCF0430.JPG (deployed)

↓ photoflow backup

State 5: BACKED UP
├── Final: DSCF0430.JPG
└── Homelab: DSCF0430.JPG (rsync)
```

---

## Dependencies & Installation

### System Requirements
- macOS (tested on Darwin 24.6.0)
- Python 3.9+
- exiftool: `brew install exiftool`
- rsync: Pre-installed on macOS
- Node.js + npm: For gallery build (optional)
- NVM: For Node version management (.nvmrc support)

### Python Dependencies (requirements.txt / setup.py)
```
Click>=8.1.8          # CLI framework
Rich>=13.7.0          # Terminal output and formatting
Pillow>=11.2.1        # Image processing
piexif>=1.1.3         # EXIF metadata
defusedxml>=0.7.1     # Secure XML parsing
```

### Installation Methods

**Global install (pipx - recommended):**
```bash
brew install pipx
pipx ensurepath
source ~/.zshrc
git clone https://github.com/yourusername/photo-flow.git
cd photo-flow
pipx install -e .
```

**Development install (venv):**
```bash
python3 -m venv venv
source venv/bin/activate
pip install -e .
```

---

## Maintenance Notes

### Editable Install
- Changes reflected immediately (no reinstall needed)
- Location: Installed with `pipx install -e .` or `pip install -e .`

### Upgrade
```bash
pipx upgrade photo-flow
```

### Uninstall
```bash
pipx uninstall photo-flow
```

---

## Known Limitations

1. **Single camera support**: Hardcoded to Fuji X-T4 volume name
2. **No progress persistence**: Interrupted operations start from beginning
3. **No undo mechanism**: Operations are permanent (dry-run recommended)
4. **Hash algorithm**: MD5 is fast but not cryptographically secure (sufficient for duplicate detection)
5. **Personal tool**: Designed for single-user local execution, not production deployment

---

## Future Improvements (Optional)

1. Add progress persistence for resumable operations
2. Support multiple camera models (configurable volume names)
3. Add undo/rollback mechanism for operations
4. Unit tests for safety mechanisms
5. Integration tests for full workflow
6. Performance metrics logging
8. Add --verbose CLI flag for enhanced debugging output

---

**Version**: 0.4.3
**Last Updated**: August 2026
**Purpose**: Optimized for AI coding agents (Claude Code, Cursor, etc.)

---

## Recent Changes

### v0.4.3 - Sidecar Integrity: Stranded `.photo-edit` Recovery + Sidecar-Aware Freshness (August 2026)
The `.photo-edit` sidecar is the **only** copy of Photomator's re-editable edit history — no
second copy exists anywhere. Three gaps closed, plus one opt-in addition:

1. **Two ways a sidecar got stranded in Staging** (`finalize_staging`):
   - The **duplicate-skip path** (`final_path.exists()` + hash match) `continue`d *before* the
     sidecar block, so a JPG already in Final left its sidecar behind permanently.
   - The main loop **iterates JPGs**, so a sidecar whose JPG left Staging in an earlier
     interrupted run (JPG copied + deleted, sidecar copy failed) was never visited again.

   **Fix:** the dup-skip branch now carries the sidecar over before skipping, and a new
   **Step 1b** `_reconcile_staging_sidecars()` sweeps Staging for sidecars with no JPG beside
   them — moving them if their JPG is in Final, **leaving and reporting** them otherwise (never
   deleting: it is irreplaceable history). The sweep also runs on the "no photos in staging"
   early return, which would otherwise skip it. Shared `_move_sidecar()` keeps the
   verified-copy-then-delete contract identical to a JPG's.
2. **Backup badge no longer lies about edit history** (`get_backup_availability`): counts were
   `*.JPG`-only on both sides, so re-editing an already-backed-up photo in Photomator (which
   changes *only* the sidecar) left the panel reading "Synced" while the homelab held stale
   history. `final` and `staging` now also carry `sidecar_extension` / `sidecar_local_count` /
   `sidecar_remote_count` / `sidecar_needs_sync`, and **`needs_sync` includes the sidecar gap**
   (so `PipelineHero`'s edge button and the CLI panel become sidecar-aware for free). The
   Library card and the CLI status panel break the sidecar count out separately, since "3 behind"
   with zero new photos otherwise reads as a glitch.
   Sidecars were already *backed up* correctly — `_run_backup_rclone` syncs the whole Final dir
   and `RSYNC_EXCLUDE_PATTERNS` never listed them; `config.py` now says so explicitly so nobody
   "tidies up" by excluding them.
3. **Optional Staging mirror** (`backup_staging_to_homelab`, source `staging`): Staging is the
   one stage with no second copy — between import and finalize a JPG and its ~17 MB sidecar
   live on the laptop disk alone. Deliberately unlike the other three sources:
   - **No trash** (new `use_trash=False` on `_run_backup_rclone`, which omits `--backup-dir`).
     The mirror is transient; once finalize moves a photo into Final (which *is* trash-backed)
     the staging copy should vanish, not accumulate for 30 days.
   - **No `min_files` floor** — an empty Staging is the normal end state, not a red flag.
   - **Not part of `all`** — opt-in only, via the CLI menu's last entry or
     `POST /ops/backup?source=staging`.
   - Remote path `HOMELAB_SSD_STAGING_PATH` is a **sibling** of the Fuji folder, not inside it:
     Immich mounts `…/Bilder/Fuji` as an external library and unfinalized, unrated photos have
     no business appearing there.

**Tests:** `tests/test_finalize_sidecars.py` (7 cases: travels-with-JPG, duplicate skip, stranded
sweep with and without other staging JPGs, no-owner left alone, AppleDouble `._*` ignored,
dry-run counts only) and `tests/test_backup_availability.py` (7 cases: split local counts,
sidecar-only gap marks stale, gaps add up, non-sidecar sources untouched, unreachable → -1).

### v0.4.2 - Backup Hardening: Cancel + Trash Retention + needs_sync Fix (June 2026)
Three fixes to the rclone backup path (`workflow.py`):

1. **Cancellable backups**: the rclone streaming loop (`_run_backup_rclone`) now polls
   `reporter.is_cancelled()` each iteration (rclone `--stats=1s` → ~1-2s response), sends SIGTERM,
   escalates to SIGKILL after 10s, and reports `cancelled` (not `failed`). Previously the loop
   ignored the cancel flag and ran rclone to completion — the panel's Stop button did nothing.
2. **30-day trash retention** (`_prune_remote_trash`): rclone `--backup-dir` parks deleted/replaced
   files in timestamped `{source}_{ts}` trash folders that accumulated unbounded (was 59 GB). After
   a successful backup, the public `backup_*_to_homelab` methods prune trash folders older than 30
   days. **Retention keys off the folder-name timestamp (when trashed), NOT file mtime** — rclone
   preserves each RAW's original capture-time mtime, so an mtime sweep would delete a freshly-trashed
   folder of months-old photos immediately. HDD-trash prune (raws/videos) also clears legacy
   `final_*` folders that predate the SSD-trash split. Trash is excluded from the offsite restic→B2
   backup (mount-level + `**/.trash/**`), so it never leaves the homelab.
3. **`needs_sync` 500 fix**: `get_backup_availability(check_remote=True)` iterated the `_connection`
   meta key as if it were a source dict → 500, so the panel's per-source "Synced / N behind" badges
   were stuck on "No data". The loop now skips non-source keys. Frontend `availabilityRemote()` query
   polls `check_remote=true` on a slow (3-min) interval to drive the badges.
4. **"Up to date" UI on the pipeline edges**: backup & sync-gallery edge buttons now show a
   `Synced` / `N behind|pending` badge and disable when there's nothing to sync. Backup uses
   `needs_sync` (sum over final/raws/videos). Gallery uses a new cheap endpoint
   `GET /gallery/status` → `get_gallery_sync_status()`: set-diff of rating≥4 Final filenames (index)
   vs the `GALLERY_PATH/images` folder (`{target, current, pending, up_to_date}`) — catches swaps,
   no hashing/build. It relies on index freshness and does not detect in-place re-edits of an
   already-published photo (that still needs a full sync-gallery run).

### v0.4.1 - RAW Orphan Detection: Staging-Aware + Photomator-Tolerant (June 2026)
**Fixed two data-loss bugs in RAW orphan cleanup** (`finalize_staging` step 4 and `cleanup_unused_raws`):

1. **Staging was ignored**: orphans were computed against **Final only**. A RAW whose JPG was still
   in Staging (imported, not yet finalized/rated) counted as orphaned and would be deleted — destroying
   the RAW backup of a photo about to be kept. Audited on the live library: **566 RAWs** were exposed.
2. **Photomator duplicate suffix broke correlation**: a Photomator export `DSCF0770_2.jpg` extracts to
   base `DSCF0770_2`, which never matches RAW `DSCF0770.RAF`. **3 keepers' RAWs** were exposed.

**Fix:** new `compute_raw_keep_bases()` (workflow.py) builds the keep-set from **Final ∪ Staging**, and
new `correlation_base()` (timestamp_renamer.py) strips the trailing `_<digits>` Photomator marker. Both
orphan call sites now use them. Strictly safer — the fix never deletes a RAW the old logic kept
(validated: 569 RAWs newly protected, 0 newly orphaned).

### v0.4.0 - Control Panel (June 2026)
**Added a local-only always-on web control panel at `http://localhost:7717`:**

1. **`photo_flow/api/`** — FastAPI server (uvicorn + sse-starlette) that imports `PhotoWorkflow`
   in-process. Routers: `routes_status` (cheap poll + pending scan), `routes_ops` (dry-run preview +
   job dispatch), `routes_jobs` (SSE stream + terminal result), `routes_analytics` (SQLite queries),
   `routes_backup` (availability + multi-source trigger).
2. **`photo_flow/index/`** — SQLite metadata index at `~/.photoflow/index.db`. One row per Final JPG,
   keyed by `(path, size, mtime)`. Parses EXIF strings (`aperture "f/2.8"` → `2.8`, etc.) for charting.
   Incremental refresh; refreshed automatically after finalize/sync-gallery jobs.
3. **`control_panel/web/`** — Vite 8 + React 19 + TanStack Router/Query + Mantine 9 SPA. Four screens:
   Pipeline hero (framer-motion, live counts), Operations (dry-run confirm modals + SSE progress),
   Analytics (visx charts over the SQLite index), Library Health (backup freshness, orphaned RAWs).
   Vendored `@argo/charts` (visx) + Blueprint token system (CSS-var palette). PWA manifest.
4. **`photoflow serve`** — new CLI command; runs uvicorn on `127.0.0.1:7717`. FastAPI serves the
   built SPA static files with SPA catch-all fallback after registering all API routes.
5. **`control_panel/launchd/com.jkrumm.photoflow.plist`** — LaunchAgent (`KeepAlive`, `RunAtLoad`).
   Install: `cp … ~/Library/LaunchAgents/ && launchctl load …`.

**ProgressReporter event seam:**
- `RichReporter` wraps existing `create_progress()` / `console.*` — CLI output unchanged.
- `QueueReporter` pushes structured `{type, payload}` events onto an `asyncio.Queue` drained by SSE.
- Single-flight `asyncio.Lock` — one mutating op at a time; 409 if another job is running.

### v0.3.4 - Full-Quality Masters + Photomator/Immich Workflow (June 2026)
**Adopted Photomator for editing/rating and stopped degrading Final JPGs:**

1. **Finalize no longer re-compresses** (`workflow.py:finalize_staging`): replaced the per-file
   *compress → copy → delete* with a *verified copy → delete* at full quality. The
   `ImageProcessor`/`compress_jpeg_safe` path and the `tempfile` import are no longer used by the
   pipeline (the module is retained for reference). Stat key `compressed` → `edits_moved`.
2. **`.photo-edit` sidecars travel with their JPG**: Photomator stores its re-editable edit history
   in a `<stem>.photo-edit` file (~17 MB each). Finalize moves it alongside the JPG into Final so
   finalized photos stay non-destructively editable; backup carries them up (rclone syncs the whole
   Final dir; `.photo-edit` is not excluded — Immich ignores non-image files).
3. **Rating model**: with Photomator's "modify originals" enabled, edits are **baked into the JPG**
   and the star rating is **embedded** as `XMP-xmp:Rating`. So the existing embedded-XMP reader
   (`metadata_extractor`) and Immich both pick ratings up directly — there is **no `.xmp` sidecar**.
   Photomator is the single source of truth for ratings/edits.

**Immich integration (was undocumented):**
- `immich_client.py` triggers an Immich **external-library rescan** after a successful backup
  (`trigger_immich_scan()`, configured via `.env`: `IMMICH_URL`, `IMMICH_API_KEY`).
- Immich mounts the homelab backup folder `…/Bilder/Fuji` **read-only** as an external library
  (`homelab/docker-compose.yml`, exposed at `/mnt/media/fuji:ro`). It is a **read-only viewer**:
  it reads embedded ratings/metadata but **cannot write back** — ratings/tags created *in* Immich
  stay only in its Postgres DB and never reach the files. **Rate in Photomator, not in Immich.**
- Data flow is strictly one-way: Photomator (local, embeds rating + bakes edit) → backup push →
  Immich rescan. Nothing syncs from Immich back to local Final.

### v0.3.3 - Gallery Deployment Moved to New VPS (May 2026)
**Old `sideproject-docker-stack` was decommissioned; gallery now deploys to the new VPS stack:**

1. **Hardcoded path removed**: rsync destination at `workflow.py:~765` moved to `config.py` (`GALLERY_REMOTE_USER` / `GALLERY_REMOTE_HOST` / `GALLERY_REMOTE_PATH`)
2. **New destination**: `jkrumm@100.97.220.54:/home/jkrumm/photo-gallery-dist`
3. **New serving stack**: nginx container in `vps/apps/photo-gallery/compose.yml`, behind Traefik + cloudflared at `https://photos.jkrumm.com`
4. **rsync tuned for Tailscale**: dropped `-z`, switched to `aes128-gcm@openssh.com` cipher with `Compression=no` (matches `backup_final_to_homelab`)
5. **Known Limitation #1 fixed** — hardcoded gallery remote is gone

### v0.3.2 - RAW Storage Migration to External Drive (February 2025)
**Moves RAW storage from laptop to external SSD to free local disk space:**

1. **Changed RAWS_PATH**: `~/Pictures/RAWs` → `/Volumes/EXT/Bilder/RAWs`
2. **Added SSD connection checks**: RAW import and cleanup skip gracefully when SSD disconnected
3. **Migrated 2,890 existing RAW files** via rsync with verification

### v0.3.1 - Migration Cleanup (January 2025)
**Removed one-time migration code after successful migration of all 7,633 files:**
- Removed `photoflow migrate` CLI command
- Removed `migrate_files_to_timestamp()` method from workflow.py
- Deleted MIGRATION_TEST_PLAN.md (migration concluded 2026-01-31)

### v0.3.0 - Timestamp-Based File Renaming (January 2025)
**Prevents filename collisions when Fuji X-T4's DSCF counter wraps after 10,000 photos:**

1. **New Naming Format**: Files renamed to `YYYY-MM-DD_HH-MM-SS_<original_base>.<ext>`
   - Example: `2026-01-28_10-29-15_DSCF1234.JPG`
   - Original base preserved for RAW-JPG correlation

2. **New Module** (`timestamp_renamer.py`):
   - `is_already_renamed()`: Regex detection of timestamp prefix
   - `extract_original_base()`: Extract DSCF base from any filename format
   - `generate_timestamped_filename()`: Generate new name with collision handling
   - `get_timestamp_from_exif()`: Extract DateTimeOriginal via exiftool

3. **Modified `workflow.py`**:
   - `import_from_camera()`: Generates timestamp names at import time
   - All RAW cleanup operations use `extract_original_base()` for correlation

### v0.2.0 - Output & UX Improvements (January 2025)
**Major refactoring of CLI output from verbose callbacks to Rich Progress bars:**

1. **Fixed XMP Rating Bug** (`metadata_extractor.py`):
   - XMP Description structure can be either dict OR list of dicts
   - Now iterates through all Description blocks to find Rating/title/description
   - Fixes issue where all images appeared to have 0 rating

2. **Replaced Verbose Callbacks with Rich Progress** (all commands):
   - **import**: 60+ verbose lines → 1 clean progress bar
   - **finalize**: 100+ verbose lines → 1 progress bar (compress + move) + info messages for RAW cleanup
   - **cleanup**: Verbose callbacks → progress bar for deletions
   - **sync-gallery**: 265+ verbose lines → 2 progress bars + 2 status spinners
   - **backup**: Verbose callbacks → Rich info/warning/error messages + rsync output

3. **Benefits**:
   - ✅ Clean terminal output (no flooding)
   - ✅ Real-time progress with completion percentage
   - ✅ Consistent Rich formatting across all commands
   - ✅ Better user experience with spinners for long operations
   - ✅ No functionality changes - only output improvements
