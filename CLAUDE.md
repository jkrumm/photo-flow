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
| Import Photos | Camera/*.JPG | STAGING_PATH | ❌ No | ✅ Yes | System files + already in Final |
| Import RAWs | Camera/*.RAF | RAWS_PATH | ❌ No | ✅ Yes | System files |
| Finalize | STAGING/*.JPG | FINAL_PATH | ✅ Yes | ✅ Yes | None |
| Compress | FINAL/*.JPG | FINAL/*.JPG (in-place, 5200×3467, Q92, 4:4:4) | ❌ No (backup created) | ✅ Yes | None |
| Copy to Camera | FINAL/*.JPG (compressed) | Camera/first subfolder | ❌ No | ✅ Yes | None |
| Gallery Sync | FINAL/*.JPG (rating ≥ 4) | GALLERY_PATH/images | ❌ No | ✅ Yes | Rating-based |

---

## System Architecture

### File Paths (Hardcoded in config.py)
```python
CAMERA_PATH = Path("/Volumes/Fuji X-T4/DCIM")
STAGING_PATH = Path("/Users/johannes.krumm/Pictures/Staging")
RAWS_PATH = Path("/Users/johannes.krumm/Pictures/RAWs")
FINAL_PATH = Path("/Users/johannes.krumm/Pictures/Final")
SSD_PATH = Path("/Volumes/EXT/Videos/Videos")
GALLERY_PATH = Path("/Users/johannes.krumm/SourceRoot/photo-flow/photo_gallery/src")
```

### Remote Destinations

**Homelab Backup** (in config.py):
- User: `jkrumm`
- Host: `homelab.jkrumm.com`
- Path: `/home/jkrumm/ssd/SSD/Bilder/Fuji`
- Jump Host: `5.75.178.196` (VPS for IPv4 fallback)
- Method: rsync with automatic IPv6/IPv4 fallback

**Gallery Sync** (⚠️ HARDCODED in workflow.py:683 - NOT in config.py):
- User: `jkrumm`
- Host: `5.75.178.196`
- Path: `/home/jkrumm/sideproject-docker-stack/photo_gallery`
- Method: rsync after npm build

### Technology Stack
- **Python 3.9+** with venv/pipx
- **Click 8.1.8+** - CLI framework
- **Rich 13.7.0+** - Terminal output and formatting
- **Pillow 11.2.1+** - Image processing
- **piexif 1.1.3+** - EXIF read/write
- **defusedxml 0.7.1+** - Secure XML parsing
- **External**: exiftool (metadata), rsync (backup), npm/Node.js (gallery)

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
- Searches multiple namespaces: `xmp`, `photoshop`, Adobe RDF structures
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
   - MOV → SSD_PATH (delete original after verify)
   - JPG → STAGING_PATH (keep original on camera)
   - RAF → RAWS_PATH (keep original on camera)
4. Duplicate detection: Skip if hash matches destination
5. Progress callback: Percentage completion

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
**Process (5 steps):**
1. **Atomic Compress+Move**: For each Staging JPG (one at a time):
   - Compress to temp file (5200×3467, quality 92, 4:4:4 chroma, preserve metadata)
   - Copy temp → Final (safe_copy with hash verify)
   - Delete from Staging (only if copy succeeded)
   - **Interrupt-safe**: Remaining files stay in Staging, retry processes them
2. **Copy to camera**: Compressed JPGs → first available DCIM subfolder
3. **Delete camera RAWs**: Matching RAFs for finalized JPGs
4. **Cleanup orphaned RAWs**: Local RAFs without matching Final JPG

**Returns:**
```python
{
    'moved': int,                # JPGs moved from staging
    'compressed': int,           # JPGs compressed
    'copied_to_camera': int,     # Compressed JPGs copied back
    'orphaned_raws': int,        # Local RAWs found without Final JPG
    'deleted_raws': int,         # Local orphaned RAWs deleted
    'deleted_camera_raws': int,  # Camera RAWs deleted
    'skipped': int,
    'errors': int
}
```

#### `cleanup_unused_raws(dry_run=False, progress_callback=None) -> Dict[str, int]`
**Process:**
1. Scan Final folder for JPGs (DSCF*.JPG)
2. Find corresponding RAWs in RAWs folder (same DSCF number)
3. Identify orphaned RAWs (no matching Final JPG)
4. **Always preview first** (shows list)
5. **Confirmation required** (unless dry-run)
6. Delete orphaned RAWs

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
**Process (8 steps):**
1. Scan Final folder for all JPGs
2. Extract metadata for each image
3. **Filter**: rating ≥ 4 only
4. Copy high-rated images to `GALLERY_PATH/images/` (skip if unchanged by hash)
5. Remove gallery images no longer rated 4+
6. Generate `metadata.json` with high-rated images only
7. **Build**: `npm run build` in photo_gallery/ (uses .nvmrc Node version)
8. **Sync**: rsync dist/ to remote server

**Logging**: Uses Python's `logging` module with `logger.debug()` for debug output and `logger.error()` for errors

**Remote Destination (HARDCODED - NOT in config.py):**
- Location: workflow.py:~815
- Value: `jkrumm@5.75.178.196:/home/jkrumm/sideproject-docker-stack/photo_gallery`
- TODO: Move to config.py

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
**Process (IPv4/IPv6 Automatic Fallback):**
1. **Try direct SSH**: IPv6 connection (timeout: 5s)
2. **Fallback to ProxyJump**: IPv4 via VPS if direct fails
3. Rsync flags: `-av --delete --partial --whole-file --progress`
4. SSH cipher: `aes128-gcm@openssh.com` (fast, no compression)

**SSH Configurations:**
- Direct: `ssh -T -c aes128-gcm@openssh.com -o Compression=no -o ConnectTimeout=5`
- ProxyJump: `ssh -T -c aes128-gcm@openssh.com -o Compression=no -o ConnectTimeout=5 -J jkrumm@5.75.178.196`

**Returns:**
```python
{
    'scanned': int,                     # Files in Final folder
    'sync_successful': bool,            # Rsync succeeded
    'connection_method': str,           # 'direct' or 'proxyjump'
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
| `photoflow import` | `--dry-run` | Import from camera | Progress updates + success/error + summary |
| `photoflow finalize` | `--dry-run` | Staging → Final → Camera → Cleanup | Progress updates + summary |
| `photoflow cleanup` | `--dry-run` | Delete orphaned RAWs | Preview + confirmation + summary |
| `photoflow sync-gallery` | `--dry-run` | High-rated photos to gallery | Progress updates + build/sync status + summary |
| `photoflow backup` | `--dry-run` | Backup Final to homelab | Status updates + connection method + summary |

**Output Features:**
- Rich-formatted progress messages (all operations)
- Color-coded success (green ✓) / error (red ✗) messages
- Structured summaries using `print_summary()`
- Rich tables for status display
- Consistent styling throughout

---

## Implementation Details

### File Extension Handling
- **Case-insensitive**: .JPG/.jpg, .RAF/.raf, .MOV/.mov all accepted
- **Extensions set**: `{'.JPG', '.RAF', '.MOV'}` in config.py
- **Comparison**: Always uses `.upper()` normalization

### System File Filtering (file_manager.py:is_valid_image_file)
**Excluded files:**
- macOS: `.DS_Store`, `._*` (AppleDouble resource forks)
- Windows: `Thumbs.db`
- Implementation: Basename check with startswith/equals

### Smart Import Filtering
**Location**: workflow.py:import_from_camera()
**Logic**: Before importing JPGs from camera, check if they exist in FINAL_PATH
**Reason**: Prevents re-importing finalized photos that were copied back to camera
**Implementation**: Set comprehension with Final folder JPG names

### Atomic Finalize Workflow (Critical Architecture)
**Location**: workflow.py:finalize_staging() lines ~262-336
**Pattern**: Compress-then-copy per file

**Flow for each Staging file:**
```python
1. Compress staging_file → temp_file (5200×3467, Q92, 4:4:4)
2. safe_copy(temp_file → Final) with hash verify
3. Delete from Staging (only if step 2 succeeded)
4. Cleanup temp_file
```

**Architecture guarantees:**
- ✅ **Atomic per-file**: Each file fully processed or stays in Staging
- ✅ **Interrupt-safe**: Ctrl+C at any point leaves consistent state
- ✅ **Idempotent**: Re-running processes remaining Staging files
- ✅ **No uncompressed files in Final**: Every Final file is guaranteed compressed
- ✅ **Retry-friendly**: Failed files stay in Staging for next run

**Why this matters:**
- Old approach: Move all → Compress all (could leave uncompressed files in Final)
- New approach: Compress → Move (atomic unit, never uncompressed in Final)

### Delete Behavior Specifics
- **Import videos**: ✅ Deleted from camera after successful copy
- **Import photos**: ❌ NOT deleted from camera
- **Import RAWs**: ❌ NOT deleted from camera
- **Finalize**: ✅ Deleted from staging after compress+copy to Final (atomic)
- **Finalize RAWs**: ✅ Matching RAWs deleted from camera after JPG finalized

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

### 4. Moving Hardcoded Gallery Remote to Config
**Current**: workflow.py:683 (hardcoded string)
**Target**: config.py (new constants)
**Steps:**
1. Add to config.py:
   ```python
   GALLERY_REMOTE_USER = "jkrumm"
   GALLERY_REMOTE_HOST = "5.75.178.196"
   GALLERY_REMOTE_PATH = Path("/home/jkrumm/sideproject-docker-stack/photo_gallery")
   ```
2. Update workflow.py:683: Use config constants
3. Test: `photoflow sync-gallery --dry-run`

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
| "rsync timeout" | Network connectivity | Automatically tries ProxyJump fallback | workflow.py:711 |
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
    │   Compressed JPGs        │
    │   (5200×3467, q=92, 4:4:4)│
    └────┬─────────────────┬───┘
         │                 │
         │                 │ sync-gallery (rating ≥ 4)
         │                 │
         ▼                 ▼
    ┌──────────┐    ┌─────────────────┐
    │  Camera  │    │  photo_gallery/ │
    │  (copy   │    │  ├── src/images │
    │   back)  │    │  └── dist/      │
    └──────────┘    └────────┬────────┘
                             │ npm build + rsync
                             ▼
                    ┌──────────────────┐
                    │  Remote Server   │
                    │  Gallery Deploy  │
                    └──────────────────┘

    ┌──────────────────────────────────┐
    │   Final/ → Homelab Backup        │
    │   (rsync with IPv6/IPv4 fallback)│
    └──────────────────────────────────┘
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
├── Camera: DSCF0430.JPG, DSCF0430.RAF (kept)
├── Camera: DSCF1451.MOV (deleted)
├── Staging: DSCF0430.JPG
├── RAWs: DSCF0430.RAF
└── SSD: DSCF1451.MOV

↓ photoflow finalize

State 3: FINALIZED
├── Camera: DSCF0430.JPG (compressed copy)
├── Camera: DSCF0430.RAF (deleted)
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

1. **Hardcoded gallery remote**: workflow.py:~815 (should move to config.py for consistency)
2. **Single camera support**: Hardcoded to Fuji X-T4 volume name
3. **No progress persistence**: Interrupted operations start from beginning
4. **No undo mechanism**: Operations are permanent (dry-run recommended)
5. **Hash algorithm**: MD5 is fast but not cryptographically secure (sufficient for duplicate detection)
6. **Personal tool**: Designed for single-user local execution, not production deployment

---

## Future Improvements (Optional)

1. Move gallery remote destination to config.py
2. Add progress persistence for resumable operations
3. Support multiple camera models (configurable volume names)
4. Add undo/rollback mechanism for operations
5. Unit tests for safety mechanisms
6. Integration tests for full workflow
7. Performance metrics logging
8. Add --verbose CLI flag for enhanced debugging output

---

**Version**: 0.1.0
**Last Updated**: Based on codebase analysis January 2025
**Purpose**: Optimized for AI coding agents (Claude Code, Cursor, etc.)
