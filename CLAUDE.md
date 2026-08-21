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

### File Paths (config.py, resolved from `library_config` — v0.4.16+)
```python
CAMERA_PATH  = Path("/Volumes/Fuji X-T4/DCIM")                              # roots.camera
STAGING_PATH = Path("/Users/johannes.krumm/Pictures/Staging")               # roots.staging
RAWS_PATH    = Path("/Volumes/EXT/Bilder/RAWs")                             # roots.raws
FINAL_PATH   = Path("/Users/johannes.krumm/Pictures/Final")                 # roots.final
SSD_PATH     = Path("/Volumes/EXT/Videos/Videos")                           # roots.videos
GALLERY_PATH = Path(".../photo-flow/photo_gallery/src")                     # roots.gallery
TRASH_PATH   = Path("/Users/johannes.krumm/Pictures/.photoflow-trash")      # roots.trash
```
The values above are the DEFAULTS, byte-identical to the literals that used to be hardcoded.
They now come from `photo_flow/library_config.py`, which reads two optional TOML files:

| File | Owns | Rule |
|-|-|-|
| `~/.photoflow/config.toml` | `[library] root` · `[roots]` · `[[cameras]]` | Machine facts. Wrong on any other computer. Found at a FIXED path — it is what says where the library is, so it cannot live inside it. |
| `<library root>/photoflow.toml` | `[stage]` · `[layout]` · `[naming]` · `[[collections]]` | Facts about the photographs. Travels with a copied library; survives `rm index.db`. |

**Strictness follows danger.** The install file names directories, so a bad one is FATAL:
`photo_flow.config` raises `LibraryConfigError` at import and `cli.py` turns it into one
line + exit 2 (the LaunchAgent runs `photoflow serve`, so a broken file stops the daemon
rather than pointing it at a guessed tree). All-or-nothing — never half-applied. The
library file cannot point an operation anywhere, so it degrades to defaults and reports,
matching `collections.py`'s tolerant-read/refused-write contract on the SAME file.

Refusals (each one a safety test, `tests/test_library_config.py`, 65 functions / 122 cases):
a root guarded by **containment, not a blocklist** (v0.4.19) — a container directory (`/`,
`/Users`, `/Volumes`, `/home`, `/mnt`, `/media`, `/net`, `/System/Volumes`) or any direct
child of one, AND anything at any depth inside a system or credential tree (`/System`,
`/Library`, `/usr`, `/private`, `~/Library`, `~/.ssh`, `~/.gnupg`, `~/.aws`, …) — checked on
the fully resolved (symlinks, `~`, `..`) path, casefolded per component, over the ASSEMBLED
roots (including one derived from `library.root` or a camera profile), never just the
configured ones · two roots naming the same directory · Staging inside Final or vice versa ·
trash inside a culling root or vice versa · unknown/misspelled key · unknown enum value · a
camera `volume` that is a path · duplicate camera id · a `naming.template` without `{base}`,
with a separator, or with an unknown placeholder. `library.root` is checked by the same
guard minus the containment clause (`library_root_refusal`), so a library sitting at the top
of a dedicated disk (`library.root = "/Volumes/Photos"`) stays expressible.

**The three axes of 0003 are CONFIGURATION, NOT BEHAVIOUR.** `[stage] mode = folders |
in-place`, `[layout] mode = flat | year-month | album | as-is`, `[naming] template/apply`.
Nothing reads a non-default value; restage/relayout move irreplaceable files and are a
later stage. `ORGANISATION.unimplemented` names every axis set away from its default, and
`photoflow config show` + `GET /api/config` print it — a setting that silently does nothing
is the same class of failure as a path that silently points elsewhere.

CLI: `photoflow config show | check | init` (`init` writes a commented file holding exactly
the current defaults and REFUSES to overwrite). API: `GET /api/config`, read-only — a
mis-clicked roots table is a restore from the homelab, not an undo.

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
- **Vite 8 + React 19 + TanStack Router/Query** — SPA (`control_panel/web/`)
- **basalt-ui 1.13** over **Mantine 9.5** — theme, `BasaltShell`, visx charts, `--vx-*` tokens,
  notifications, ⌘K command palette. **Not a component grab-bag: it owns the identity.** Read
  `control_panel/web/DESIGN.md` and `control_panel/web/.claude/rules/basalt-*.md` before touching UI.
- **motion** (`motion/react`, never raw `framer-motion`) — animated pipeline hero
- Port: `127.0.0.1:7717` (localhost only, never exposed)

**basalt-ui house rules that bite (all mechanically enforced by `npm run lint`):**
- No raw `#hex` / `rgb()` / `rgba()` anywhere in `src/` — colors come from `VX.*`
  (`basalt-ui/tokens`), `alpha(token, a)`, or the app's series map `src/lib/series.ts` (the one
  guard-exempt file, and it uses basalt's own `p(BP.*)` families rather than literals).
- Never `withBorder` on a `Card`/`Paper`, never an inline `border`/`borderRadius`/`boxShadow`/
  `backgroundColor` — depth is `shadow-card` (panels) / `shadow-raised` (controls), ring baked in.
- `@visx/*` may only be imported inside a `charts/` dir; everywhere else compose `basalt-ui/charts`.
- Motion timings come from `MOTION_DURATION` / `MOTION_SPRING` / `MOTION_EASE_STANDARD`.
- After a basalt-ui upgrade run `bunx basalt-ui sync` to refresh the managed rules + CLAUDE.md block.
  `.claude/` is gitignored in this repo, so those rules are **regenerated, not committed** —
  `.basalt/manifest.json` is the committed record of what version they came from.

### Control Panel Architecture

```
photo_flow/ (Python core — unchanged)
  PhotoWorkflow · FileManager · MetadataExtractor · config · immich
       │ imports directly (no shell-out)
       ├── cli.py (Click / RichReporter)
       ├── photo_flow/api/ (FastAPI — QueueReporter → SSE)
       │     app.py · routes_status · routes_ops · routes_jobs · routes_analytics
       │     routes_photos (culling + facets + narrowing + structure) · routes_collections
       │     routes_config (read-only `GET /api/config`) · library_config.py (roots/editors/axes)
       │     collections.py → ~/Pictures/photoflow.toml (saved queries; NOT in the index)
       │     jobs.py (asyncio.to_thread + single-flight Lock)
       │     job_store.py (durable job records — outlive a restart; v0.4.10)
       │     Serves static control_panel/web/dist/ + SPA fallback
       └── photo_flow/index/ (SQLite metadata cache at ~/.photoflow/index.db)

control_panel/web/       Vite React SPA (build → dist/ served by FastAPI)
  src/main.tsx           BasaltProvider → BasaltOverlays (⌘K) → QueryClient → Router
  src/routes/__root.tsx  BasaltShell (sidebar/mobile-nav/breadcrumbs/globalActions)
  src/lib/structure.ts   the four candidate library layouts, client side (F3)
  src/lib/narrowing.ts   types only — the precedence RULE lives server-side, never twice
  src/lib/series.ts      the app's series dictionary — the only place a color is declared
  src/lib/commands.ts    ⌘K registry: navigation + view toggles ONLY, never a pipeline op
  DESIGN.md              app-level design law (deltas over the shipped basalt-* rules)
control_panel/launchd/   LaunchAgent plist (KeepAlive, RunAtLoad, localhost:7717)
```

**Event seam:** `ProgressReporter` protocol — `RichReporter` for CLI (Rich bars unchanged),
`QueueReporter` for API (pushes structured events onto an asyncio.Queue drained by SSE).

**Job lifecycle:** `POST /ops/{name}?dry_run=true` → preview dict → UI confirm modal →
`POST /ops/{name}` → SSE stream at `GET /events/{job_id}` → terminal result at `GET /jobs/{job_id}`.
Every transition is also mirrored to the `jobs` table, so `GET /jobs/history` still answers after a
restart that empties `GET /jobs` — and marks whatever was in flight `interrupted` (v0.4.10).

**Serving:** `photoflow serve` runs uvicorn; FastAPI mounts the built SPA at `/` with a catch-all
SPA fallback after all `/api`-prefixed routes are registered. Dev: Vite on port 7718 proxies
`/api` (photos, collections, config), `/health`, `/status`, `/ops`, `/jobs`, `/events`,
`/analytics`, `/index`, `/backup`, `/gallery` to 7717.

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

1. **Single camera support**: still Fuji-only. v0.4.16 added `[[cameras]]` to
   `~/.photoflow/config.toml` (volume, DCIM subdir, per-type extensions) and `EXTENSIONS` is
   the union across profiles — but **the profile is accepted, validated and inert**. Two
   hardcodes defeat it: `file_manager.scan_camera_files()` globs `CAMERA_PATH/*_*` (a Fuji
   DCIM folder convention — a Sony `100MSDCF` card scans to zero files), and
   `workflow.import_from_camera()` routes on the literals `.JPG` / `.RAF` / `.MOV`, so an
   `.ARW` found by the scan is silently dropped. `CAMERA_PATH` is also bound to `cameras[0]`
   at import and never follows `INSTALL.active_camera()`, so `config show` can name one body
   while `import` reads another. Do not treat this row as done.
2. **No progress persistence**: Interrupted operations start from beginning
3. **Undo is a culling-screen feature, not a pipeline one**: trash (`photoflow trash restore`,
   ⌘Z) and rating writes (`POST /api/photos/rating/undo`, ⌘Z) are reversible. The pipeline verbs
   — import, finalize, backup, sync-gallery — have no inverse; dry-run remains the only way to
   preview them before committing.
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

**Version**: 0.4.19
**Last Updated**: August 2026
**Purpose**: Optimized for AI coding agents (Claude Code, Cursor, etc.)

---

## Recent Changes

### v0.4.19 - Undoable Ratings, a Hardened Root Guard, and the RAW Hand-off (August 2026)

**Stage F7** closes the F1–F7 chain (`shutterflow/docs/decisions/0003`, `0005`). The other two
items below are data-loss defects an adversarial review found in F5/F6's and F4's own work,
fixed in the same pass — item 1 is the most consequential change in this release.

1. **Rating writes are now undoable, and a broadcast past 20 frames is gated — a real
   data-loss defect.** In the contact sheet (v0.4.18), shift-selecting a range and pressing a
   number key ran `exiftool -overwrite_original -XMP-xmp:Rating=N` in place across every
   master in the selection with **no inverse**: `⌘Z` was wired to `undoLastTrash` only.
   Ratings are the entire output of a cull pass and Photomator is their single source of
   truth for them, so one keystroke over a 500-frame range destroyed unrecoverable work.
   Fixed on both sides:
   - `POST /api/photos/rating` now reads and returns every touched path's prior rating
     **unconditionally** (`RatingWriteResult.previous`, via `_current_ratings`) — not gated on
     batch size, so the client can always build the exact inverse rather than only above a
     size guess.
   - `POST /api/photos/rating/undo` (new) restores per-path values, paths grouped by their
     target rating (at most 7 exiftool calls — the whole -1..5 range — regardless of how many
     photos are in the batch). A group whose write fails reports its own paths in
     `failed_paths` rather than guessing file-by-file: an undo would rather over-report a
     failure than tell the caller it landed when it didn't.
   - In the SPA, `lastActionRef` generalised from a trash-only ref to `UndoableAction`
     (`trash | rating`), so `⌘Z` inverts whichever happened last. `RATING_CONFIRM_THRESHOLD =
     20` (`routes/photos.tsx`) gates any write past that size behind a confirm modal naming
     both the count and how many of the selected frames currently carry a **different**
     rating (the number actually overwritten, not merely reconfirmed) — the modal names `⌘Z`
     as the secondary safeguard, since the load-bearing one is that the write is undoable at
     all.
   - `TestRatingUndo` (7 cases) plus `RatingWriteResult.previous` coverage in
     `tests/test_photos_api.py`.
2. **The library-config root guard now refuses by containment, not a two-entry blocklist —
   also found by adversarial review, also a real gap.** F4's guard (v0.4.16) refused only `/`
   and the bare `$HOME`; `/Users`, `/Volumes` and `/System` all loaded clean, and
   `sync_gallery` rsyncs every rating≥4 JPG under `FINAL_PATH` to a **public** host — so a
   hand-edited `~/.photoflow/config.toml` pointing a root at `/Users` turned a routine publish
   into exfiltration of the whole account. `library_config.py`'s `_refusal()` now refuses two
   shapes: a container (`/`, `/Users`, `/Volumes`, `/home`, `/mnt`, `/media`, `/net`,
   `/System/Volumes`) and any direct child of one, AND anything at any depth inside a system
   or credential tree (`/System`, `/Library`, `/usr`, `/private`, `~/Library`, `~/.ssh`,
   `~/.gnupg`, `~/.aws`, `~/.config`, `~/.local`). Four properties make it hold: paths are
   fully **resolved** (`~`, `..`, symlinks) before comparison — a guard that checks one path
   while the operation walks another is not a guard — and compared **casefolded per
   component**, since macOS's default filesystem is case-insensitive and `realpath` does not
   normalise case; the guard runs over the **assembled** roots, not only the configured ones,
   so a root derived from `library.root` or built from a camera profile (`volume = "EXT"`,
   `dcim = "."`, which `import` would empty since it deletes originals) is refused too, naming
   where it came from; and refusal is **fatal** — no partial application, no silent fallback
   to a default, because a fallback means the operation believes it ran against the configured
   tree when it ran against another. `library_root_refusal()` stays the looser, second tier so
   `library.root = "/Volumes/Photos"` (a library at the top of a dedicated disk) keeps
   working. `tests/test_library_config.py` now carries 65 test functions (122 parametrized
   cases), including an assertion that the live install's own seven configured roots still
   pass. Findings in `shutterflow/docs/decisions/0003` § "The root guard, resolved:
   containment, not a blocklist".
3. **Stage F7 — the RAW hand-off.** `photo_flow/raw_link.py` (new): `find_raw(jpg_path,
   raws_root)` correlates a JPG to its RAF via `timestamp_renamer.correlation_base`
   (Photomator `_2`/`_3`-tolerant), never `extract_original_base` — the same mismatch class
   that has previously exposed irreplaceable RAWs elsewhere in this codebase — and
   distinguishes `found` / `no_raw` / `unmounted` / `missing` via
   `library_config.root_availability`. `POST /api/photos/open-in-editor` gained
   `target: "jpeg" | "raw"`; for `raw` it still takes the JPG's own path and derives the RAF
   server-side, launching that instead of the JPG. **`RAWS_PATH` is deliberately NOT in
   `config.CULL_ROOTS`/`_allowed_roots`** — widening the shared allowlist would make an
   irreplaceable RAF newly writable and trashable through endpoints (`rating`, `trash`) that
   have no reason to address one, so the hand-over gets its own narrow, read-only resolver
   instead. The now-unreachable `_RAW_SUFFIXES` constant is gone. **UI:** the sidebar Tools
   section and the stage right-click menu render one button per configured editor per kind
   (JPEG editors, then a RAW section); `E` and the single-editor case still resolve to the
   server default with no picker. Tests: `tests/test_raw_link.py` (6) + `TestOpenInEditorRaw`
   (7). **NOT verified live** — `/Volumes/EXT` is unmounted, so no real RAF was ever opened by
   a real RAW developer; only a monkeypatched `open` and a monkeypatched root were exercised.
4. **Four falsified numbers, corrected where they were written**, found by the same
   adversarial pass: `photos_original`'s full-tier cost docstring (real: 365–812 ms, median
   630; 3.07–9.51 MB; 26 MP portrait masters 766–812 ms and 9.0–9.5 MB — both upper bounds had
   been understated ~30%); `photo-viewer.tsx`'s master-request range ("4.6–21.7 MB" → real
   0.43–21.7 MB, median 7.3); `MAX_COMPARE = 4`'s rationale in `photo-compare.tsx` (had
   claimed the layout "stops improving after four" when the marginal-cost table shows no
   break there — cap kept at 4, rationale replaced with the honest one: a burst you cannot
   hold in your head is not a comparison, plus a ~415 MB worst-case bitmap budget, which
   itself corrected a "~352 MB" figure that was 4x the MEDIAN master rather than the worst
   case); and shutterflow `0010`'s portrait-frame count ("470 of 2364 Final frames are
   portrait" → really **512**, `SELECT orientation, COUNT(*) FROM photos WHERE in_final=1` →
   landscape 1852, portrait 512).
5. **An `Enter` binding stole a keystroke.** `['Enter', ...]` closed the contact sheet
   unconditionally, and `whenIdle`'s guard bails only on INPUT/TEXTAREA/SELECT and roles
   slider/dialog/separator — `BUTTON` is in neither, so tabbing to a sidebar folder or
   collection row and pressing `Enter` both activated the row and ejected you from the sheet.
   Fixed with a `whenIdleNotButton` wrapper applied only to that one binding
   (`routes/photos.tsx`).
6. **Two duplicated constant sets pinned** (`tests/test_client_constants.py`):
   `THUMB_LONG_EDGE` against `config.THUMB_SIZES` (decides when a 0.43–21.7 MB master is
   fetched, so drift silently desynchronises the compare stage's fetch threshold) and
   `scripts/grid_survey.py`'s `GAP`/`PAD`/`CELL_ASPECT`/`DENSITY_STEPS` against
   `photo-grid.tsx`.

### v0.4.18 - The Contact Sheet: a Set-Shaped Surface, and the Ring That Did Not Survive It (August 2026)

**Stage F6** — a virtualized full-page grid over the whole result set. The findings are the
deliverable and live in `shutterflow/docs/decisions/0010-contact-sheet-role-and-density.md`;
this is what the code does. **Frontend only — no Python change.**

1. **`components/photos/photo-grid.tsx`** (new) windows the result set with the same arithmetic
   the filmstrip uses — uniform cells absolutely positioned in a track of the full extent, so
   "what is visible" is pure arithmetic against `scrollTop` with no measurement pass and no
   virtualization dependency. `rows.length` is up to 3 797; the DOM holds ~32. `g` toggles it,
   `ArrowUp`/`ArrowDown` move by `gridColumns` (a row, not a frame), density is a slider over
   `DENSITY_STEPS` (96…320 px target cell width, persisted at `photos-grid-density`).
   - **Cells are `object-fit: contain`, not `cover`.** The strip crops because a strip cell is a
     locator; a sheet cell is being *judged*, and a crop hides where the subject sits and whether
     the frame is level. Portrait frames letterbox and pay ~33 % of the cell — that is the price
     of not lying about composition, and it is why density is adjustable.
   - **The window is NOT widened to contain the focus.** The strip does that harmlessly because it
     re-centres on every step; a sheet scrolls independently of its focus, so the same rule makes
     the window the whole *span* between them. Measured: a fling to the bottom with the focus on
     row 0 mounted **all 3 515** cells and prewarmed the entire result set. The focused cell is
     mounted separately instead, which is all `scrollIntoView` ever needed.
   - A cell that outgrows `THUMB_SIZES['grid']` by more than `UPGRADE_RATIO` (1.25) asks for
     `view` instead. Self-limiting: big cells and many cells are mutually exclusive.
2. **The viewer's 1-D prewarm ring is now gated on `gridOpen`, and this was a real defect.**
   `warmGridWindow` reshaped the *sheet's* prefetch correctly (keyed on the visible window, one
   tier, debounced 220 ms — a 20 s full traversal fires **one** warm POST), but the ring was left
   running underneath it. Measured with the sheet open at 236 px cells, per settled step: a
   **second** warm POST for **15 paths at BOTH tiers**, plus up to four `view` decodes
   (2.1 MB encoded over two keystrokes, ~4.8 MB resident each) into an LRU capped at 24 — i.e. up
   to **~116 MB** of full-size bitmaps held for a screen whose entire visible content is 4.7 MB of
   320 px thumbnails. All the ring legitimately owes the sheet is the ONE frame `Enter` opens, and
   warming it server-side is enough (the viewer paints `grid` instantly and upgrades), so the
   upgrade costs a **3 ms** warm fetch instead of a **321 ms** cold generation. After: 1 path, 0
   decodes. The viewer's own ring is unchanged the moment the sheet closes — verified both ways.
   **The rule for anything after this: a prefetch ring is keyed to a traversal order, and a grid
   has two. Reuse the endpoint, the tiers and the cache; do not reuse the ring.**
3. **Selection is F5's marked set, not a second model.** The grid owns no selection state — every
   gesture is reported upward with its modifiers. The cap is `MAX_MARKED = MAX_WRITE_PATHS`,
   because the reason to mark forty frames is to write to them and a selection larger than the
   endpoint accepts is one whose whole purpose 413s. **Same set, different verb:** in the viewer a
   star applies to the focused frame even while comparing (broadcasting across a comparison would
   say "these are equally good"); in the sheet broadcasting *is* the point. Rejecting in the sheet
   does not advance the focus — the frames are all still on screen.
4. **Measured over the real 3 515-row result set** (3 797 present, minus 282 rejected by the
   default view), 979x905 viewport: a full 20 s traversal ran **960 frames at p50 20.8 ms / max
   31.6 ms with zero frames over 32 ms** — and an *idle* rAF loop on the same machine also measures
   20.8 ms, so the scroll adds nothing measurable. A hard fling passes 588 cells and fires **146**
   requests (a fling skips ~75 % of what it flies over). Renderer RSS peaks at 312 MB from a
   176.7 MB baseline and *falls* to 201 MB on a second pass — Chrome's own image cache, reclaimed,
   not a leak. Whole-library `grid` cache: **42.7 MB** (10.4 KB a photo) against 1 241 MB for
   `view`, which is the only reason a sheet over every photograph is reasonable to build.

New persisted keys: `photos-grid-open`, `photos-grid-density`.

### v0.4.17 - Compare: One Shared Transform, and 1:1 Means the Master (August 2026)

**Stage F5** — findings in `shutterflow/docs/decisions/0009-compare-and-true-resolution.md`.

1. **`GET /api/photos/original`** streams the master's own bytes — no cache, no re-encode —
   behind an explicit magnification. **A full-resolution tier was costed and rejected**: median
   620 ms of CPU per photo (186 ms decode + 435 ms encode) to produce a file 0.70x the source,
   which is a re-encode of exactly the micro-contrast the magnification exists to judge.
   **1:1 bites unconditionally here** — the shortest long edge in the library is 3 278 px and
   **0 of 3 797** photographs are at or below the 2048 px `view` tier, so every magnification past
   fit was magnifying a proxy. Resolved through `_resolve_in_roots` and additionally suffix-gated
   to `{.jpg,.jpeg}`, which is what refuses a `.photo-edit` — the only copy of an edit history and
   a file that must never reach a byte streamer.
2. **`components/photos/photo-compare.tsx`** — N frames, one shared pan/zoom transform, always
   shared rather than on a modifier: two frames at different magnifications of different regions
   do not answer "which of these". `c` marks, `p` picks (keep this, reject the others), `MAX_COMPARE`
   is 4. **2-up is free** (cells are height-limited, so each resolves to the size a single frame
   would); the cost begins at three.
3. **Two interaction defects fixed, both worth not re-deriving.** The wheel handler read the
   *rendered* scale, so every event arriving before the next commit recomputed from the same stale
   value: 6 notches paced one-per-render reached 3.00x, the same 6 dispatched in one task reached
   **1.20x — a single notch**. A continuous gesture must read a ref and compose. And the
   `trueResolution` master swap re-laid the viewer's `<img>` out at the bitmap's intrinsic size, a
   **2.54–3.05x** geometry jump mid-inspection with scroll offsets not rebased; the box is now the
   *display* geometry, which is what the compare stage always did.

New persisted key: `photos-true-resolution` (default on). New hotkeys: `c`, `p`, `shift+Arrow/J/K`,
`Escape`.

### v0.4.16 - The Config Model: Two Files, Three Axes, and Validation as a Safety Feature (August 2026)

**Stage F4** — the durable serialisation of what F1–F3 settled. `photo_flow/library_config.py`
(new) plus `photo_flow/api/routes_config.py` (new) and a `photoflow config` CLI group.
See "File Paths" above for the schema and the refusal list; what follows is the reasoning.

1. **TWO files, and the split is a bootstrap fact plus a portability rule.** F1 put the
   collection file next to the library and expected F4's roots to join it there. They
   cannot: the roots table is what says where the library IS, so reading it from inside
   the library needs the answer first. Something must be findable at a fixed path.
   The rule that decides any future setting: **if the value would be wrong after copying
   the library to another computer, it belongs to the install; otherwise to the library.**
   A mount point and a volume name fail that test; a layout, a filename template and a
   saved query pass it.
2. **Strictness follows danger, and the two halves therefore fail differently.** A bad
   install file is fatal and all-or-nothing — a half-read roots table is the one outcome
   that could point `finalize` at a directory nobody named. A bad library file degrades
   and reports, because nothing in it can point an operation anywhere AND because
   `collections.py` already degrades on the same file; one file may not have two
   contradictory failure modes.
3. **No writer for the library file, and only a template writer for the install file.**
   `config init` refuses to overwrite. F1 lost a `[library]` table to a writer that
   re-emitted its own model; the fix is not a better writer, it is not having one. The
   three axes were hand-added to the real `~/Pictures/photoflow.toml` and survived a live
   collection create+delete **byte-identically** (md5 unchanged), which is F2's lossless
   region writer doing exactly what it claimed with the tables it was claimed for.
4. **`naming.template` is the serialisation of `timestamp_renamer`, not a new idea.**
   `"%Y-%m-%d_%H-%M-%S{n}_{base}"` — strftime for the stamp, `{n}` for the collision
   counter (empty, then -2, -3 …) exactly where `generate_timestamped_filename` puts it,
   `{base}` for the camera's own stem. A test renders it and asserts
   `2026-01-28_10-29-15_DSCF1234` / `2026-01-28_10-29-15-2_DSCF1234`.
5. **`layout.mode = "as-is"` is 0003's open "no opinion" option, modelled.** It costs one
   enum member and is the only on-ramp for a library whose owner does not want a tool
   rearranging it.
6. **The proof that defaults reproduce today's behaviour is the suite itself**: 627 tests
   pass with no config file, and again with `~/.photoflow/config.toml` present holding the
   rendered defaults — a subprocess diff of every resolved constant is identical either way.

**F3-verify defects fixed in the same pass:** `_structure_month` had no malformed-timestamp
guard where `_structure_event` did, so a `date_taken` the extractor could not parse (it
stores the raw EXIF string) either 500'd the Month structure or produced a group whose own
query resolves to zero rows — the invariant the whole endpoint exists to demonstrate.
`scripts/structure_survey.py` indexed cluster bounds into the full row list while the
clusters were built from the DATED subset (correct here only because this library has zero
undated rows), and matched tags by plain substring rather than the pipe-sentinelled test
`_clauses` uses. The structure query fired on every filter change even with the section
closed; it is now `enabled: structureOpen` (verified live: the request disappears from the
network log when the section is shut). `PhotoFolders`' docstring still described the
deleted date tree.

### v0.4.15 - Library Structures: Four Candidate Layouts, All of Them Queries (August 2026)

**Prototype work for Shutterflow** (Stage F3; findings in
`~/SourceRoot/shutterflow/docs/decisions/0003-library-config-two-axes.md`). The question is which
way of arranging a photo library actually gets navigated by, and whether any of them has to be a
directory. Nothing here moves a file.

1. **`GET /api/photos/structure?kind=flat|month|album|event`** (`routes_photos.py`) returns one
   candidate layout as a list of groups, and **every group carries the `PhotoFilters` mapping that
   resolves to exactly its own photos** — the same mapping `POST /api/collections` stores. So a
   folder is a `WHERE` clause and keeping one is a saved collection. Verified live: **71 of 71
   groups** across all four kinds resolve to their own count against the real 3 797-row index.
   - Each kind is computed with **its own dimension left open** (`STRUCTURE_DIMENSION`), the same
     facet contract the option lists use — clicking March must not collapse the month list.
   - `event` is a time-gap clustering over `date_taken`, `gap_seconds` = 2 days by default.
     `MAX_EVENT_SCAN_ROWS` (250 000) makes a future library of a different order of magnitude fail
     loudly rather than silently make the sidebar slow.
   - `ungrouped` is the load-bearing number: `album` cannot place **2 555 of 3 797** photos. A
     directory layout hides that by making you invent a `Misc/`.
2. **UI: a `Structure` sidebar section** (`photo-structure.tsx`) — a four-way switch, the group
   list as ordinary `FolderRow`s, and the three measurements on one line (groups · unplaced · ms).
   The rows are deliberately the SAME component the two real directories use: if a derived group
   and a physical folder are indistinguishable in use, the layout was never load-bearing.
   New persisted keys `photos-section-structure`, `photos-structure-kind`,
   `photos-structure-event-gap`. A group is a REFINE on **one** dimension
   (`STRUCTURE_KIND_FIELDS`), so picking July keeps the folder you were in.
3. **The client-derived date tree is gone from `photo-folders.tsx`.** It grouped at most
   `DEFAULT_PHOTO_LIMIT` (2 000) LOADED rows against a 3 797-row library, so the oldest months
   were silently missing. Months now come from the server over the whole set. `monthKey`/`yearKey`
   are deleted.
4. **`include_rejected` is no longer a dimension** (`VIEW_FIELDS` in `routes_photos.py`). It
   WIDENS, and a widening flag has nothing for a precedence rule to arbitrate — mapping it to
   `rating` made the sidebar's "Show rejected" switch DELETE a collection's rating filter (inside
   Keepers: 172 rows → 2 364, every Final photo). View fields now compose by **OR** after the
   dimension merge, and the funnel's three steps all apply them so they stay comparable. The
   facets and the narrowing readout finally receive the flag too, so the card describes the set on
   screen rather than the pre-toggle one.
5. **A scope-sourced narrowing chip no longer carries a dead ✕.** `clearDimension` resets fields in
   the REFINE layer, where a scope-supplied dimension was never set — inside a collection that was
   every chip on the card. Scope chips now show a `scope` badge and say so in the tooltip.
6. **`~/Pictures/photoflow.toml` is never overwritten when it cannot be parsed.** `read()` degraded
   to empty on a syntax error and the next write then replaced the user's whole document with the
   collections region. `Store.unreadable` + `StoreUnreadable` → the read still degrades (the
   culling screen must not die over a typo) but the write refuses, surfaced as a **409**.
7. **`scripts/structure_survey.py`** (new) — the F3 measurements, re-runnable, index opened
   `mode=ro`. A measurement with no way to re-run it is an assertion.

**Two findings worth carrying:** a time-gap clustering reproduces five of this library's seven
hand-written album tags *exactly, with zero extra photos* — but never the two that are selections
inside a trip (precision 0.03–0.12 at every grain). And the filename stamp equals `date_taken` on
**3 797 of 3 797** rows, so a `YYYY/MM` directory tree would encode nothing the filename does not.
451 → 564 tests.

### v0.4.14 - The Narrowing Model: Scope + Refine, and Keywords as a Dimension (August 2026)

Saved collections (v0.4.13-era prototype work) previously *replaced* the filter state. Now a
collection is a **layer** you narrow inside, which forces a precedence rule.

**The rule:** REFINE overrides SCOPE on a shared `_clauses` DIMENSION, and intersects with it on
every other. Two layers, no third. The folder rail and the date tree are **not** a layer — they
are dimensions (`root`, `date`), because a "folder" here is a column, not a place. Full table +
rationale: `photo_flow/api/routes_photos.py` § "The narrowing model", and every row has a test in
`tests/test_narrowing.py`. Findings live in `shutterflow/docs/decisions/0003`.

1. **`compose(scope, refine)`** (`routes_photos.py`) merges into ONE effective `PhotoFilters`, so
   `_where(effective, exclude=D)` is already `(scope\D) AND (refine\D)` and every existing
   endpoint kept working unchanged. `QUERY_DIMENSIONS` maps each filter field to its dimension;
   a test asserts it covers `PhotoFilters` exactly and emits only dimensions `_clauses` produces.
2. **`?collection=<id>` on `/api/photos` and `/api/photos/facets`.** The SERVER composes — the
   client names the scope only. An unknown id is a **404**, never a silent whole library.
3. **`GET /api/photos/narrowing`** — per active dimension: the layer that supplied it, whether it
   overrode a scope clause, and `without` (rows if that dimension were cleared). `without` uses
   `without_dimension()` (reset the fields), **not** the facet's `exclude=`: `exclude` also drops
   the default reject-hiding clause, which is right for a facet and a lie in a readout.
4. **Keywords — schema v3.** `photos.keywords` holds the flat `XMP-dc:subject` set,
   pipe-sentinelled (`|a|b|`), so an exact tag match is one `LIKE '%|a|%'`. `|` is safe because
   decision 0004 reserves it as the hierarchy separator. NULL = not yet backfilled, and
   `_reindex_root` re-reads such a row even when unchanged (the backfill has to ride the
   incremental pass — rebuilding the DB would take the `trash` table with it). New `keyword`
   filter (OR-set), facet, and `PhotoRow.keywords`.
5. **UI:** new **Narrowing** sidebar card (funnel + one removable chip per dimension with its
   `−N` cost, amber where it overrode the collection), `col=` search param, a `Keywords`
   MultiSelect in Filters. New persisted key `photos-section-narrowing`.
6. **`splitRootCounts` is gone.** It derived the second root count by subtraction, valid only
   while nothing narrowed the library above the rail; under a scope it reported Staging 0 where
   the answer was 11. Each root count — and "All" — is now an explicit facet query.
7. **Collections store is now lossless** (`collections.py`). `_split_document` preserves every
   line outside the `[[collections]]` region verbatim (other tables, comments), entries it cannot
   model are quarantined rather than dropped, and query values are stored raw and cleaned at the
   API boundary. The previous writer re-emitted only its own model, so one rename deleted every
   other table in the file.
8. **`QUERY_BOUNDS` is derived from the FastAPI dependency's own signature** — a stored
   `rating_min = 99` was a 200 while `?rating_min=99` was a 422.

533 tests (was 483).


### v0.4.13 - The Editor Hand-off: Open in Shutterflow (August 2026)

The culling screen could judge a photo and never change one. Editing lives in
**Shutterflow** (`~/SourceRoot/shutterflow`, a Tauri app that crops and straightens by
writing the master's own XMP packet), and until now there was no way to get from one to the
other — the app existed only as a binary you ran from a terminal.

1. **`POST /api/photos/open-in-editor`** (`routes_photos.py`) takes one path, resolves it
   through the same `_resolve_in_roots` allowlist as every other surface, and runs
   `open -a Shutterflow <path>` as an **argument list with no shell**. `EXTERNAL_EDITOR_APP`
   in `config.py` is a macOS application *name*, not a path: Launch Services resolves it, so
   the bundle can live in `~/Applications` or `/Applications` without this caring.
   - **It is a launch, not a write.** Nothing waits for the editor, nothing invalidates a
     query on success, and photo-flow never learns what changed. Shutterflow writes the
     master in place, so the edit arrives back through the index on the next reindex — via
     mtime, exactly like a Photomator edit. An invalidation at click time would refetch the
     row in its unchanged state and prove nothing.
   - **Trashed paths are refused** (`include_trash` is not set). Handing a culled photo to
     an editor that writes to it is a way to resurrect a file the user decided against.
   - **A missing editor is `opened: false`, not a 500.** The editor is optional and the
     panel is a pipeline tool; an absent convenience is not a fault in it. `open(1)` itself
     missing (not macOS) and a 15s timeout are the same kind of answer.
2. **UI: a `Tools` section in the photos sidebar**, between Info and Cull — it is what you
   do to the photo in front of you, which is the same class of action as rating it. Plus
   **`E`** as a one-key shortcut and a **right-click menu on the photograph** (a 1x1
   absolutely-positioned anchor at the click point, since Mantine's `Menu` anchors to a node
   and a context menu has none). New persisted key `photos-section-tools`, open by default
   until the shortcut is muscle memory.
3. **Tests:** `TestOpenInEditor` (6 cases) — the launcher is monkeypatched, because what is
   worth asserting is the argument list and the refusals, not that a GUI opens. Traversal,
   a non-existent path, and a trashed path are each rejected **before** anything is
   launched; a non-zero exit and a missing `open` binary are both reported rather than
   raised. 445 → 451 tests.

**Verified live**: `POST /api/photos/open-in-editor` against the running daemon with a real
Final master opened the app; `/Users/…/.ssh/id_ed25519` returned 400 with nothing launched.

### v0.4.12 - Reject Flag: Three-State Culling, Trash Demoted to a Batch Step (August 2026)

**Prototype work for Shutterflow** (see `~/SourceRoot/shutterflow/docs/decisions/0004`) — the
findings are the deliverable; the code is how they were obtained. `x` (and `Backspace`/`Delete`,
rebound) now writes `XMP-xmp:Rating = -1` instead of moving the file to the trash.

1. **`-1` is a third cull state, not a low star count** (`REJECTED` in `routes_photos.py` and
   `lib/photos.ts`). Unrated (0 / tag absent), rated (1–5) and rejected (-1) are independent
   answers to "have I judged this yet?", which the old 0–5-only model could not express. It is the
   XMP spec's own reject value, so it is written literally — unlike rating 0, which still *clears*
   the tag. `RatingRequest` widened to `ge=-1`; the Pillow reader already passed `-1` through
   unclamped, so nothing else in the read path changed.
2. **Rejecting moves nothing.** That is the entire point: a cull pass is now a sequence of
   metadata writes, reversible by pressing the key again, and only the deliberate
   **`POST /api/photos/rejects/purge`** touches the filesystem — behind a count, a confirm modal
   and a `dry_run`. "Purge" still only means trash; `photoflow trash restore` works afterwards.
   Per-photo trash-on-keypress (`trashSelected`/`trashMutation`) is **deleted**, not hidden.
   - The purge re-reads its targets from the **index**, not from a client-supplied list, so it
     acts on the judgement as it stands at click time. Every path is still re-validated through
     `_resolve_in_roots` + `exists()` — "the DB said so" is not a reason to hand a path to a move,
     and a lagging index can then only fail to offer a purge, never direct one at the wrong file.
3. **Hide-by-default lives in the `rating` dimension of `_clauses`, deliberately.** The facet
   endpoint recomputes each dimension with its own filter excluded, so `exclude="rating"` drops
   the reject-exclusion too — which is exactly right: the rating facet reports a truthful `-1`
   count while every other facet stays reject-free. An explicit `rating=-1` also wins over the
   default, so asking for rejects is never vetoed by the view preference.
4. **A rejected frame dims but is NOT desaturated** (`photo-filmstrip.tsx`). Greyscale is correct
   for a *trashed* photo — it has left the library — and wrong for a rejected one: colour is half
   of what is being judged, and an un-reject decided against a grey thumbnail is decided blind.
   The ✕ mark, not the dimming, is what makes it unambiguous; at strip size a dim frame alone
   reads as "still loading".
5. **UI:** new **Cull** sidebar section (reject count, "Show rejected" switch, the purge button),
   open by default because it holds the only control that moves a culled photo.
   `photos-show-rejected` / `photos-section-cull` are new persisted keys.

**Verified against the live library, not just fixtures:** a real Final JPG was rejected through
the API; the file on disk read back `-1`, facets moved 3797 → 3796 with `ratings['-1'] = 1`, the
default list dropped it, `include_rejected=true` returned it, and the file was still on disk.
445 tests (was 434).

**Two things left unverified**, both needing something this session could not reach: whether
Photomator displays `-1` sanely and preserves it across a re-edit (GUI), and whether Immich reads
it (the `.env` API key is scoped to library-scan only — `asset.read` is denied).

**Bonus survey, same session:** index rating vs. a live exiftool read across all **2 364** Final
JPGs — **0 disagreements**, 27 files with no `Rating` tag at all. The Photomator-vs-us conflict
risk recorded in `0004` is prospective, not an existing mess.

### v0.4.11 - Shell Chrome: 40px Header, and the Standalone Gutter That Ate the Right Edge (August 2026)
**Frontend only — no Python change.** Three edges of the culling screen, reclaimed.

1. **The shell header is 40px** (`src/styles/shell.css`, new, imported after `native.css`). basalt's
   dense default is 48; the panel's primary screen is a photo viewer, where chrome above the image
   is space the photograph does not get. The header's tallest content box (the global-actions
   group) measures 30px, so 40 clears it with room on both sides. Mantine writes
   `--app-shell-header-{height,offset}` into an injected **unlayered `:root` block**, so the
   override needs `!important` — a plain declaration would only win on source order. Both vars move
   together: `-height` sizes the element, `-offset` is what `AppShell.Main` and this route's
   `CONTENT_HEIGHT` reserve. Scoped to `min-width: 48em`, since below it the shell stacks the
   breadcrumb row and legitimately needs its own taller value. Recorded in `DESIGN.md`.
2. **`native.css` was padding the installed app WIDER than the browser.** Its standalone rule used
   `max(var(--mantine-spacing-md), env(safe-area-inset-*))` — and `md` is 18px against the shell's
   own 13px gutter, on a Mac where every safe-area inset is 0. Invisible on a page of cards;
   very visible on `/photos`, which cancels exactly one `--app-shell-padding` to bleed, so the
   sidebar and the filmstrip stopped 5px short of the window edge in the Dock PWA and reached it in
   a browser tab. The floor is now `--app-shell-padding`, so standalone padding equals the normal
   gutter and a bleeding route cancels it exactly.
3. **The filmstrip's right-hand overshoot is gone** — both `.filmstripBleed`'s second
   `margin-right` and `photo-filmstrip.tsx`'s `RIGHT_BLEED` term. v0.4.8 added them against a
   ~7px shortfall it could only guess at ("a reserved scrollbar gutter is the likeliest culprit");
   item 2 is what it actually was. With the cause fixed, the outer column's own negative margin
   lands on the edge and both ends of the track carry a plain `EDGE`.
4. **Sidebar cards ride the window edge** — the section stack's inset is 4px (was 8), gap 6 (was
   8). Combined with item 2 the cards sit ~9px further right.

### v0.4.10 - Durable Job Records; Interrupted-Job Detection; Notification Coverage (August 2026)

Job records lived **only** in `JobManager._jobs`, an in-memory dict. A restart — crash, logout,
`make reload` — vaporised every one of them: a 22 GB backup that was 80 % done did not fail, it
stopped existing, and `last_run.json` was left implying the op had never run at all. Three gaps,
one durable record.

1. **`photo_flow/api/job_store.py`** (new) mirrors every job transition to a `jobs` table in
   `~/.photoflow/index.db` (**schema v3** — a new table, so `CREATE TABLE IF NOT EXISTS`; any
   later column on it must use the PRAGMA-guarded `ALTER TABLE` idiom of `_migrate_photos_v2`).
   Columns: op, status, seq, queued/started/finished timestamps, JSON result, error, `announced`.
   Three writes per job (admitted / started / terminal), pruned to 500 rows.
   - **Every write is best-effort** — each entry point swallows and DEBUG-logs its own failure, the
     same contract as `_persist_last_run`. The durable record is a convenience; the verified-copy
     file operations underneath are not, and a jobs-table problem must never be able to fail or
     stall one. `test_store_failure_does_not_break_the_job` pins it against an unwritable path.
   - **Events are NOT persisted.** The per-job SSE log stays in memory: large, only useful while
     someone is watching, and its terminal summary is already in `result`.
   - Writes happen on the **event-loop thread**, so `get_db()` gained a `timeout` parameter and the
     store passes 2 s. sqlite3's default is 5 s, which is 5 s of every SSE stream in the panel
     stalling behind a long indexer transaction.
   - Ordering is by **rowid** (insertion order), never `seq` — `JobManager._seq` restarts at 0 in
     each new process, so it orders within a run and lies across restarts.
2. **Startup reconciliation** (`jobs.sweep_interrupted_jobs()`, called from `app.py`'s lifespan
   *before* the manager exists). A fresh process owns no jobs, so any row still `queued` or
   `running` is by definition residue of a dead one; each becomes **`interrupted`**. The two are
   kept distinguishable through the error text, because only `running` actually touched the disk.
   - **It also corrects `last_run.json`** — but only for jobs that were *running*. Without that the
     file still holds whatever the previous successful run wrote, and the advisor reads a backup
     that died mid-transfer as "backed up 3 days ago, fine". A job that was only ever queued is
     left alone: nothing ran, so nothing about the last run changed.
   - `interrupted` is a **seventh status that no live `Job` object can reach** — it is assigned to
     rows whose process no longer exists. It exists in the persisted record and the API types only.
3. **The notification bell is now a record of what happened, not of what was watched.** Jobs run
   server-side, so one finishing with the panel shut produced no toast and no history entry.
   `announced` is the coverage flag: `GET /jobs/history?unannounced=true` feeds a catch-up sweep in
   `JobController` that replays those outcomes into the notification history on mount, then
   `POST /jobs/history/ack`s them. The live completion seam acks too, so a job watched in real time
   is never re-announced on the next reload.
   - The catch-up path is deliberately **quieter than the live one** — no chime, no OS notification.
     The event is over, several may arrive at once, and a burst of chimes for things that finished
     hours ago is noise.
   - Ack failure is swallowed on purpose: replaying the whole batch next load is worse than losing
     one bell entry.
4. **`usePipelineAdvisor` no longer buys staleness credit with a timestamp from a run that did not
   finish.** The backup rule read age only, so an interrupted run — stamped with the moment the
   sweep detected it — would have read as "backed up 2 minutes ago" and then gone quiet for three
   days. `ok === false` now forces the advice, with "last backup interrupted — the server stopped
   mid-transfer" as the detail (`last_runs.*` gained an optional `interrupted` flag,
   OPTIONAL not just nullable, per the long-daemon/fresh-SPA rule above).
5. **UI:** `JobHistoryPanel` (collapsible, on the Pipeline screen below the hero) lists the durable
   record — op, outcome, relative time, duration, headline counts — and is the one surface that
   still shows something after a restart, where `GET /jobs` is empty by design. `JobStatusBadge` was
   extracted from `JobQueuePanel` and is shared by both, so the live queue and the history can't
   drift into two vocabularies for the same state.
6. **`tests/conftest.py`** (new) points the job store at a per-test database. Its default is the
   developer's **live** `~/.photoflow/index.db`; without this fixture every test that enqueues a job
   would append history rows to a database holding thousands of real photo rows.

**Verified live, not just under pytest:** a real `backup:final` was started, `photoflow service
restart` was issued 8 s into the transfer, and after the restart `GET /jobs` was empty (the old
behaviour, unchanged) while `GET /jobs/history` held the job as `interrupted` with its start/finish
stamps and `last_runs.backup` read `ok: false, interrupted: true`. The re-run then completed
normally (2 526 files, Immich rescan triggered) and recorded `done`. 414 → 434 tests.

### v0.4.9 - Import Reindexes; Viewer Cache Releases What It Evicts (August 2026)

1. **`import` now reindexes** (`api/routes_ops.py`, `cli.py`). It was the only mutating op not
   wrapped in `_with_reindex`. That was *correct* before v0.4.5 — the index held Final only, so an
   import touched nothing indexable — but since Staging is indexed (`root='staging'`), import is
   precisely the op that ADDS rows. Observed live: 679 freshly imported photos stayed invisible to
   `/photos` until a manual `POST /index/refresh`. Both paths now reindex on a successful non-dry
   run; the CLI's is best-effort (`try/except` + warning), because the files are already copied and
   hash-verified and a reindex failure must not report a successful import as failed. Incremental
   and cheap — **1.3 s for 679 new rows** against a 3 800-row index.
2. **The viewer's decoded-frame LRU aborts what it evicts** (`routes/photos.tsx`). Eviction was
   `lru.delete(url)`, which drops only *our* reference: an entry evicted while its request was
   still in flight held one of the browser's six connections to completion and then decoded a
   ~450 KB `view` frame nobody was waiting for. Holding an arrow key evicts exactly those — the
   ring refills faster than a cold frame lands. Measured over a 75-frame walk at 25 ms/step,
   **~36 of 75 frames were evicted mid-flight**, and clearing `src` on eviction cut bytes
   transferred **30.4 MB → 16.2 MB (-47 %)**. This is the same abort `PhotoViewer` already
   performed on its own superseded loads; the LRU simply never got it.
3. **The LRU is released on unmount.** Leaving `/photos` stranded up to `LRU_CAP` decoded frames
   until the component graph was collected. Now aborted and cleared in the effect teardown.
4. **`LRU_CAP`'s cost is documented in measured numbers.** The old comment read "a few tens of MB",
   which is the *encoded* size; a `view` frame is a ~450 KB JPEG but a 1365x2048 **bitmap** once
   decoded. Measured (Chrome 151, 32 GB): 24 held frames move renderer RSS **~116 MB**, ~4.8 MB
   resident each. The cap is unchanged — read any future change to it as tens of megabytes.

**Memory profile, measured, for anyone tuning this next.** Server: **55 MB idle**, ~138 MB peak
during a 4-worker `warm` — bounded, no leak across a 200-frame walk. Browser: baseline ~210 MB,
**+116 MB** for a full 24-frame LRU. Growth beyond the LRU is Chrome's own HTTP memory cache
holding the encoded `view` JPEGs of every frame stepped past (~450 KB each, `Cache-Control:
immutable`) — **not reachable from JS and not a leak in this code**; it is reclaimed under
pressure. Don't chase it by shrinking `LRU_CAP`.

### v0.4.8 - Culling View: Full-Bleed Stage, Edge-to-Edge Filmstrip (August 2026)
**Frontend only — no Python change.** v0.4.7 collapsed three edges of chrome into one; this
reclaims the space the *shell* was still holding around it.

1. **The route bleeds through `AppShell.Main`'s gutter** (`routes/photos.tsx`). Every other screen
   in the panel is a page of cards and wants the shell's 13px padding; this one is a viewer, where
   the same padding is dead surface on four sides framing the only thing the screen exists to show.
   Mantine pads Main with `{header,footer,navbar}-offset + --app-shell-padding`, so a negative
   margin of exactly `--app-shell-padding` cancels the gutter and **leaves the offsets** — the
   content still clears the header, the nav rail and the mobile footer. `CONTENT_HEIGHT`
   correspondingly stops subtracting the two gutters it just reclaimed. Do not "simplify" it to a
   plain `100%`: percentage height does not resolve against Main's `min-height: 100dvh`.
   - **The width stays `auto` — do not add `calc(100% + var(--app-shell-padding) * 2)`.** It is
     arithmetically identical and wrong in practice: it resolves the container width and the two
     gutters separately and adds them, so on a display running a fractional scale factor (any
     "More Space" Mac) the roundings do not cancel and the box lands short of the right edge — a
     thin dark band against the window frame, on the **right only**, because the left edge is
     fixed by the margin rather than by the sum. `width: auto` derives both edges from the
     container, so the negative margin widens the box by exactly the gutter, once.
2. **The filmstrip moved back OUT of the stage column**, below the whole viewer+sidebar row, and
   spans the **window** — `.filmstripBleed` cancels the nav rail's offset too
   (`margin-left: calc(var(--app-shell-navbar-offset) * -1)`, `z-index: 102` to clear the fixed
   navbar at 101), so it runs 0 → 100vw under the rail, which has nothing below its nav items.
   It is the timeline of the result set, not an accessory of the viewer. (This reverses the
   v0.4.7 note above; the `<Activity mode="hidden">` treatment that keeps its DOM and scroll
   state across a toggle is unchanged.)
   - **Its right margin OVERSHOOTS the gutter, deliberately.** Measured in the installed app,
     reaching the window's right edge took ~7px MORE than `--app-shell-padding` — some ancestor
     box is that much narrower than the window there (a reserved scrollbar gutter is the
     likeliest culprit: Chrome honours `scrollbar-width: thin`, which basalt sets globally, over
     `::-webkit-scrollbar`). Exact arithmetic against an already-short container can never reach
     the edge, so the strip takes a full extra `--app-shell-padding`. That is free HERE and
     nowhere else on the screen: the strip is a horizontal scroll container, so the only
     consequence is its last cell being cut a few px past the window, and `.noPageScroll` means
     the page cannot scroll to reveal it. Do NOT do the same to the stage row — its sidebar is
     anchored to that edge and would be pushed off by exactly the overshoot.
   - **The outer column therefore carries no `overflow: hidden`** — a clip there cuts exactly
     the part the bleed exists to show. The clip moved to the stage row, which is the box that
     actually has something to contain. This is the one thing to re-check if the strip ever
     stops reaching the left edge.
3. **Strip height 96 → 82** and the `+9` scrollbar reserve is gone, so the strip costs 86px instead
   of 105 and the photo takes the difference.
4. **The strip's scrollbar is macOS-style** (`type="scroll"`, `scrollHideDelay={1200}`, 10px):
   shown while the strip is moving, faded out otherwise, overlaying the frames rather than
   reserving a row. It was `type="hover"` — a permanent hairline under 3 000 frames whose thumb is
   a few px wide and unusable as a control — and briefly `type="never"`, which answered nothing.
   **The thumb is overridden to a 60% ink mix** (`.filmstripScroll`): basalt's global 25% is tuned
   for a panel, and over *photographs* at the very bottom edge of the window a 25% capsule 6px
   tall is invisible — it read as "there is no scrollbar" even though the element was rendering.
5. **The page itself is locked while the route is mounted** (`.noPageScroll` on `<html>` and
   `<body>`, added/removed by an effect). The layout is pinned to `100dvh` minus the shell
   offsets, but `dvh` resolves fractionally on a window with an odd pixel height, and half a
   pixel of overflow raises a bar — and basalt styles `::-webkit-scrollbar`, which drops Chrome
   out of macOS's overlay behaviour, so that bar is a **9px gutter** that shortens the sidebar
   and the filmstrip instead of floating over them. That is why a scrollbar showed in the
   installed PWA and not in a browser tab of the same width.
6. **The sidebar is a stack of cards, not a panel.** The column carries **no background**; each
   section is its own `Paper` (panel surface + `shadow-card`'s ring + `--vx-radius-card`), so the
   chrome takes up exactly as much of the right edge as it has content and the page surface
   simply continues below the last card — no full-height slab running down to the filmstrip.
   The card is now the object, so `.sectionHeader` dropped its resting `surface-subtle` fill and
   its own radius: it tints edge-to-edge on hover only, clipped to the card's corners by
   `overflow: hidden` on the `Paper`. An open card separates the two with a hairline
   (`.sectionBody`, on the scroll container rather than the padded content box, so it holds while
   the body scrolls under it) drawn in **`--vx-divider`** — the card's ring is a 4%-white inset,
   so the opaque `--vx-surface-border` read as a hard rule across a soft box; the 6%-white mix is
   the same material as the rim — that is what makes the title row read as the card's bar instead
   of as the first line of its content. (That `overflow` does **not** eat the depth token — an
   outset shadow is painted outside the border box, so an element's own overflow never reaches
   it. Verified in the browser: the computed shadow still carries both the drop and the inset
   rim.)
7. **The track is inset at both ends** (`EDGE` = 6px), so the first and last frame are held off
   the window and their selection ring has room to draw. The trailing inset carries an extra
   `RIGHT_BLEED` term that **mirrors `.filmstripBleed`'s `margin-right`** — without it the last
   frame's breathing room falls entirely outside the window and the strip reads as padded at the
   start and cut off at the end. (A gapless, radius-less strip was tried here to close a
   right-edge sliver and **reverted** — it was not the cause, and it made adjacent frames read as
   one image.)
8. **The selection ring is neutral, and no longer clipped.** It was `VX.accent`; a saturated blue
   edge competes with the photographs for no added meaning, so it is now `alpha(VX.neutral, 0.8)`
   and brightness alone marks the position. The ring is a `box-shadow` spread — it paints *outside*
   the cell — so with the cells flush against the track the viewport clipped its top edge and the
   selection read as outlined on three sides. The track now carries a 2px inset (`RING`), added to
   both `trackWidth` and the cells' `left`/`top`.

### v0.4.7 - Culling View: One Sidebar, No Top Bar (August 2026)
**Frontend only — no Python change.** The screen had chrome on three edges (top filter bar, left
folder rail, right info panel) framing the one thing it exists to show. All three collapse into a
single right-hand `PhotoSidebar`; the top bar's ~40px goes back to the image.

1. **`components/photos/photo-sidebar.tsx`** (new) is the screen's only chrome, and it is *nothing
   but sections*: four independent collapsibles — **Folders**, **Filters**, **Info**, **View**.
   Deliberately NOT an accordion — closing one to open another is a tax on a screen you sit in for
   an hour. Each persists its own open state (`basalt:photos-section-*`) and shows a one-glance
   `summary` while closed (active root, live-filter count / result count) so the panel answers
   "anything in there?" unopened. Defaults: only **Info** open — and it has to be, because it
   carries the stars.
   - **Section headers are objects, not captions** (`.sectionHeader`): a resting `surface-subtle`
     fill, a leading icon, a bold uppercase label, a rotating chevron. They are the screen's
     primary navigation; a header that only appears on hover is a header nobody finds.
   - **A section body is capped at 44vh** (`ScrollArea.Autosize`) and scrolls inside itself.
     Uncapped, opening Filters pushed View off the panel and the section list stopped being a list.
   - **Open/close is a `motion` height-auto tween** (`MOTION_DURATION.fast` /
     `MOTION_EASE_STANDARD`), not Mantine's `Collapse`, so height, fade and chevron run on one
     curve; `useReducedMotion` renders a plain unanimated node rather than a 0-duration animation.
   - **No pinned header.** A filename heading and a portrait/landscape badge are not worth
     permanent real estate on a screen whose subject is the photograph, so the whole block is gone
     — orientation deleted outright, stars + label picker moved to the top of **Info**, and the
     filename demoted to a single truncated `File` row (full path in its tooltip). That row's
     `flex: 1 1 0` is load-bearing: inside a `ScrollArea` the content box is content-sized, so a
     `nowrap` value with an `auto` basis widens the whole panel and carries every other row's
     right-aligned value off the visible edge.
2. **The sidebar is width-resizable** by dragging its leading edge (248–560px, persisted at
   `basalt:photos-sidebar-width`). The live width is component state and only reaches localStorage
   on pointer-up — a drag is otherwise ~60 storage writes a second. The handle is
   `role="separator"` with `aria-valuenow` (a WAI-ARIA window splitter — the `jsx-a11y`
   `prefer-tag-over-role` suggestion of `<hr>` is for the decorative case and cannot take a drag),
   and arrow keys nudge it, which is why `role="separator"` had to join `KEYBOARD_OWNING_ROLES` in
   the route: otherwise nudging the width also stepped the photo selection.
3. **One floating control, top-right of the stage.** `position / total` plus the sidebar toggle, at
   40% opacity until hovered (`photos-screen.module.css`), the icon `color="gray"` — it is chrome
   pointing at chrome and carries no signal, so it never takes the accent. It anchors to the stage
   column, not the window, so it never lands on the sidebar. `i` toggles the sidebar now (was the
   info panel); `f`/`z` unchanged. (The filmstrip sat inside the stage column here too —
   **superseded by v0.4.8**, which moved it back out to run the full width.)
4. **Files:** `photo-filters.tsx` `PhotoFilters` → **`PhotoFilterPanel`**, a vertical stack — the
   Filters popover and the root `SegmentedControl` are gone (a column has the room the one-row bar
   never had; root is a folder and lives in the Folders section). `photo-info-panel.tsx`
   `PhotoInfoPanel` → **`PhotoInfo`**, now write-surface-first (stars, label, divider, rows). The
   folder rail moved out of `routes/photos.tsx` into **`photo-folders.tsx`** (with
   `splitRootCounts`), gaining an explicit "All" row. New: `sidebar-section.tsx`,
   `photos-screen.module.css`. The route drops ~230 lines.
5. **View options are Switches, not icon buttons** — filmstrip, 1:1 zoom, show-trashed, plus an
   "Open trash…" button. A labelled switch in a panel is discoverable in a way a row of unlabelled
   16px glyphs never was; the one-key shortcuts remain the fast path.

**Note:** the retired `basalt:photos-rail-open` / `photos-info-open` keys are simply orphaned, so
the sidebar comes back open once on first load after this change.

### v0.4.6 - Culling View: Layout, Density, and the Stale-PWA Fix (August 2026)
Two rounds of feedback on the v0.4.5 screen. **No Python change beyond the LaunchAgent plist.**

1. **`ProcessType: Background` was throttling the daemon** (`control_panel/launchd/*.plist`). It
   looked like the obvious choice for an always-on job, but background QoS pins the process to the
   efficiency cores on Apple Silicon (observed priority 4 vs 31). Thumbnail generation is CPU-bound
   JPEG decode and paid a **3.5x** tax: cold `view` 1041 → 305 ms, prewarm 341 → 79 ms/frame. Now
   `Interactive` + `Nice 0` + `LowPriorityIO false`. This — not any frontend code — was "the
   responsiveness of the image in view is horrible".
2. **One decode, both tiers** (`index/thumbs.py` `get_thumbs`, `POST /api/photos/warm?tiers=`).
   Decode is ~117 ms of the ~194 ms a `view` frame costs and is nearly tier-independent (progressive
   JPEG is Huffman-bound), so the second tier off a shared decode costs ~8 ms instead of ~79.
   **Every tier's box is computed from a pre-draft, orientation-corrected `base_size`**, never from
   whatever `draft()` produced — otherwise the grid tier came out 213px from the dual path and 214px
   standalone, under the same cache key, and whichever request landed first won.
3. **The image now actually fits** (`photo-viewer.tsx`). `max-height: 100%` only resolves against a
   **definite** height; the sharp frame's box had `mih="100%"` with `height: auto`, so the
   percentage computed to `none`, portrait frames laid out width-limited (~1.5x too tall) and were
   clipped by the surface's `overflow: hidden`. Measured 540 px of hidden overflow on a 3466x5200
   frame. Fit mode now sets `h`/`w`; zoom keeps `mih`/`miw` so the pan surface can still exceed the
   viewport. The placeholder layer never showed the bug — `position: absolute; inset: 0` gave it a
   definite height for free.
4. **No dividers on this screen.** Chrome (filter bar, folder rail, info panel) sits on
   `surface.panel`, the stage (viewer + filmstrip) on `surface.bg`; the surface change is the
   separator. Filmstrip cells lost their per-cell 1px ring — only the selection is outlined.
5. **The filter bar is one row at every width.** `wrap="wrap"` fell to three lines on a narrow
   window and ate a third of the screen's height. Camera / lens / label moved into the (renamed)
   **Filters** popover alongside the four EXIF ranges, the trigger shows how many are set, and the
   filename box is the only elastic control (`flex: 1 1 0; min-width: 0`).
6. **EXIF sliders draw their distribution** — `GET /api/photos/facets` gained `histograms`
   (`iso`/`aperture`/`shutter`/`focal`, 24 buckets, log-spaced for ISO and shutter or a base-ISO
   library lands entirely in bucket 0). Plain SVG, not `@visx` — it is a control backdrop, not a
   chart, and `@visx/*` may only be imported inside a `charts/` dir.
7. **Trashed photos are reviewable in place** — `include_trashed` on the list endpoint plus a strip
   toggle; culled frames render desaturated and dimmed rather than vanishing. Trashing while the
   toggle is on deliberately does NOT optimistically remove the row.
8. **The Dock PWA now picks up deploys.** `basaltAppPlugin`'s injected `registerSW.js` only
   registers — nothing reacted to the new worker claiming the page, so load N kept running the
   precached OLD bundle and only load N+1 saw the change. A browser tab self-corrects on the next
   ⌘R; an installed PWA that stays open for days never does. `main.tsx` now reloads on
   `controllerchange` (guarded against a double fire). **This is why a shipped change could look
   like it had not shipped.**
9. **Deploying is no longer a thing to remember** — `scripts/reload-if-stale.sh` + `make
   reload-if-stale`, wired to the Claude Code **`Stop` hook** in `.claude/settings.json`. Three
   tiers off one stamp file (`~/.photoflow/.last-reload`): no change → ~50 ms exit, Python/plist
   only → `make service-restart`, panel sources → full `make reload`. The stamp is touched only
   after a successful run, so a failed build retries instead of claiming to have shipped.
   `.gitignore` had to become `.claude/*` rather than `.claude/` for the `!.claude/settings.json`
   negation to bite — git never descends into an excluded *directory*. Watch find(1)'s `-o`
   precedence in that script; the first version silently never matched a `.py` edit.

### v0.4.5 - Culling View: Photos Screen, Thumbnail Cache, Soft-Delete Trash (August 2026)
Replaces Adobe Bridge for culling. New SPA route `/photos` (sidebar group "Workflow"), backed by
three new Python modules and one new router. 166 → 366 tests.

1. **Index schema v2** (`index/db.py`, `indexer.py`). Additive, idempotent
   `ALTER TABLE ADD COLUMN` guarded by a `PRAGMA table_info` check — the live DB holds thousands
   of rows and is never recreated. New columns: `root` ('final'|'staging'), `present`,
   `width`/`height`, `orientation`, `lens_model`, `camera_make`, `label`, `has_sidecar`.
   **Staging is now indexed into the same table.** The load-bearing invariant:
   `in_final = 1` ⟺ (`root`='final' AND `present`=1), so every pre-existing analytics/status
   query (all of which filter `WHERE in_final = 1`) still sees exactly the Final set. Verified
   against the live DB: 3119 rows, 2365 `in_final=1`, unchanged across the migration.
   `reindex_paths(paths)` re-reads a single file after a rating write.
2. **`width`/`height` are DISPLAY dimensions, not raster dimensions**
   (`metadata_extractor._display_size`). The X-T4 writes portrait frames as a landscape raster
   plus EXIF Orientation 6/8 — 470 of 2365 Final JPGs. Reading the raster size indexed every one
   of them as landscape, so a Portrait filter returned nothing and the thumbnail (which applies
   `exif_transpose`) disagreed with the index about the same file. The tag is read and the axes
   swapped for values 5–8. The `dimensions` "WxH" string keeps the same, now-corrected, value.
3. **Thumbnail cache** (`index/thumbs.py`, new). Content-addressed on
   `sha1(path|mtime_ns|size|tier)` under `~/.photoflow/thumbs` — an edit or a rating write
   changes mtime and therefore misses naturally; no invalidation bookkeeping. Two tiers:
   `grid` 320px (filmstrip), `view` 2048px (viewer). Measured: cold 240–270 ms, **warm 2–3 ms**,
   which is what makes arrow-key stepping instant.
   - `Image.draft()` runs before the first pixel access, and its box must be the
     **aspect-preserved** output size, not `(target, target)` — draft only reduces while BOTH
     dimensions stay ≥ the box, so a square box lets the short edge veto the reduction entirely.
   - Honest numbers: draft saves 30–37 % on wall time, not the order of magnitude one expects
     (libjpeg still Huffman-decodes every MCU, and Photomator re-saves Final JPEGs as
     progressive, where entropy decoding dominates). The real prize is peak memory — ~1 MB
     instead of ~78 MB per decode, which is what makes a 4-worker `warm()` sane.
   - `exif_transpose` after draft, before thumbnail. Atomic `os.replace` from a private sibling.
4. **Soft-delete trash** (`trash.py`, new; `trash` table; `photoflow trash list|restore|purge|stats`).
   A culled photo is **moved, never unlinked**. `TRASH_PATH` (`~/Pictures/.photoflow-trash`) is
   deliberately under `~/Pictures` so the move from Final or Staging is a same-filesystem rename.
   The `.photo-edit` sidecar travels with its JPG and is never deleted independently. Move first,
   then insert the row; a failed insert moves the files back and reports honestly when that
   move-back itself failed. An orphaned entry directory (killed between rename and commit) is
   **adopted**, never reaped. Retention keys off `trashed_at`, **never file mtime** — the same
   mistake the v0.4.2 rclone trash prune had to fix.
5. **Trashed photos protect their RAWs** (`workflow.compute_raw_keep_bases`). The keep-set is now
   Final ∪ Staging ∪ **trash**. Without this, culling 200 photos and then running `finalize`
   would unlink 200 irreplaceable RAFs — step 4 deletes orphans with no preview and no
   confirmation. `_trash_keep_bases()` reads the **filesystem**, not the `trash` table: the entry
   directory is what makes a restore possible, so files without a row still protect their RAW.
   A RAW becomes an orphan only once `purge` removes the entry, which is the intended semantics.
6. **`photo_flow/api/routes_photos.py`** (new), prefix **`/api/photos`** — the `/api` segment is
   deliberate, since the SPA owns the bare `/photos` route and `app.py`'s catch-all would
   otherwise be shadowed. Endpoints: list (every EXIF dimension filterable), facets (each facet
   computed with the *other* filters applied and its own dimension left open, so the UI never
   offers a zero-result option), thumb (immutable `Cache-Control` + ETag + 304), meta,
   rating/label write-back, trash/restore/purge, warm. Every path goes through
   `_resolve_in_roots` against `config.CULL_ROOTS` — traversal returns 400.
   Rating/label are written with **exiftool, one subprocess per batch** (never piexif/Pillow),
   then `reindex_paths()` reconciles the index. Rating 0 clears the tag.
7. **`/photos` SPA route** + `src/components/photos/*`. Viewport-filling: filter bar, folder rail,
   viewer, EXIF info panel, filmstrip. (**Superseded by v0.4.7** — the bar, the rail and the panel
   are now sections of one right-hand sidebar.) Arrow keys / j-k step, 0–5 rate, ⌫ trash-and-advance,
   ⌘Z undo, i/f/z toggles. Snappiness is the acceptance criterion: a `warm` POST for ±8
   neighbours, `img.decode()` preloads for ±3, a bounded LRU, and a viewer that holds the last
   decoded frame until the next one is ready (no white flash). Mutations are optimistic and
   invalidate only the key that is actually stale — a star keypress must not refetch the list.

**Two things to know before touching this.** Rating writes `XMP-xmp:Rating` straight into the JPG,
which is what Bridge did — but v0.3.4 made Photomator the single source of truth for ratings, so
the two can now overwrite each other. And a trashed Staging JPG leaves its RAF behind: protected
while the trash entry lives, offered to the (confirm-gated) `cleanup` once it is purged.

### v0.4.4 - Panel on basalt-ui; Staging Mirror Reaches the UI (August 2026)
**Frontend only — no Python change; all 166 tests pass untouched.**

1. **Adopted `basalt-ui@1.13.0` as a real dependency.** The panel never depended on it: it carried a
   **vendored fork** of the (pre-1.0 `@argo/charts`) chart library at `src/lib/charts/**`, plus its
   own Blueprint `theme.ts`, its own app shell, and a hand-rolled `check-hex.mjs`. All of that is
   deleted — ~25 chart files, 6 shell files, 3 CSS modules (**−1,900 lines**) — and replaced by
   `basalt-ui/charts`, `createBasaltTheme`, and `BasaltShell`. The fork had drifted: it was on
   Blueprint hues while basalt had moved to modern zinc, so chrome and charts were quietly two
   identities. `bunx basalt-ui sync` now keeps the rules current; `.basalt/manifest.json` records
   the version. **`.claude/` is gitignored here, so the `basalt-*` rules are regenerated, not committed.**
2. **Mechanical palette enforcement.** `npm run lint` = `oxlint` + `basalt-ui check-theme`. The
   migration cleared **215 findings to zero**. `src/lib/series.ts` is the ONE place a color is
   declared, and even it holds no literals — every pair is `p(BP.<family>)` against basalt's own
   palette, so a basalt retune carries photo-flow along instead of stranding copied hexes.
3. **What the framework replaced**: `BasaltProvider` + `BasaltOverlays` (⌘K palette — navigation
   and view toggles ONLY, never a pipeline op), `BasaltShell` (`__root.tsx` 167 → 86 lines),
   `createBasaltQueryClient`, `basaltViteConfig` + `basaltAppPlugin` (PWA head/manifest/icons now
   derived from the token palette rather than hand-written hexes in `index.html`),
   `createPersistedState` for UI prefs, and a typed `defineNotifications` registry so job toasts
   land in the notification bell/history. `framer-motion` → `motion/react` with `MOTION_*` tokens.
   Mantine 9.2 → 9.5.1, visx alphas → 4.0.0.
4. **Staging mirror is now reachable from the panel.** The backend has supported
   `POST /ops/backup?source=staging` since v0.4.3, but `BACKUP_SOURCES` in the UI listed only
   final/raws/videos — the feature was CLI-only in practice. It now appears in the backup menu
   **under its own "Optional" divider** with a "no trash retention" hint, so it never reads as a
   peer of the canonical set that `all` runs.
5. **The advisor stopped trusting the backup timestamp alone.** A Photomator re-edit rewrites only
   the `.photo-edit` sidecar, so a fresh `last_runs.backup` said nothing about whether the edit
   history had been carried up. `usePipelineAdvisor` now also reads `sidecar_needs_sync` and says
   "N edit histories not backed up". The Staging mirror is deliberately NOT advised — Staging
   holding files is the normal state between import and finalize, so advising it would fire on
   every import and read as noise.

**Two behaviour changes worth knowing:** UI preferences moved to `basalt:*` localStorage keys, so
the sound / desktop-notify toggles reset once; and error toasts now stay until dismissed (basalt's
`intent: 'error'` mapping) instead of auto-closing after 6s.

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
