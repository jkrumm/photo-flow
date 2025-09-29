# Technical Design Document - Python CLI Photo-Flow Tool (Revised)

## Project Overview
Personal CLI tool for managing Fuji X-T4 camera photos/videos with a staging workflow for JPG photography with RAW backups, plus a gallery sync for high-rated images.

## System Architecture

### File Paths (Hardcoded)
```python
CAMERA_PATH = "/Volumes/Fuji X-T4/DCIM"
STAGING_PATH = "/Users/johannes.krumm/Pictures/Staging"
RAWS_PATH = "/Users/johannes.krumm/Pictures/RAWs"
FINAL_PATH = "/Users/johannes.krumm/Pictures/Final"
SSD_PATH = "/Volumes/EXT/Videos/Videos"
GALLERY_PATH = "/Users/johannes.krumm/SourceRoot/photo-flow/photo_gallery/src"
```

### Technology Stack
- **Python 3.9** with venv
- **Click** - CLI framework
- **Pillow** - Image metadata access (no clarity processing)
- **piexif** - EXIF read/write
- **shutil** - File operations (copy with metadata)
- **pathlib** - Modern path handling
- **hashlib** - File duplicate detection
- External: **npm** for building the static gallery, **rsync** for remote sync

## Core Components

### 1. File Manager (`file_manager.py`)
```python
class FileManager:
    def scan_camera_files() -> Dict[str, List[Path]]
    def is_duplicate(src: Path, dst: Path) -> bool
    def safe_copy(src: Path, dst: Path) -> bool
    def get_file_hash(file_path: Path) -> str
```

### 2. Metadata Extractor (`metadata_extractor.py`)
```python
class MetadataExtractor:
    def extract_metadata(image_path: Path) -> Dict[str, Any]
    def generate_metadata_json(items: List[Dict[str, Any]], json_path: Path) -> bool
```

### 3. Workflow Manager (`workflow.py`)
```python
class PhotoWorkflow:
    def import_from_camera()
    def finalize_staging()
    def cleanup_unused_raws()
    def get_status() -> StatusReport
    def sync_gallery()
```

### 4. CLI Interface (`cli.py`)
```python
@click.group()
def photoflow():
    pass

@photoflow.command()
def status():
    pass

@photoflow.command(name='import')
def import_cmd():
    pass

@photoflow.command()
def finalize():
    pass

@photoflow.command()
def cleanup():
    pass

@photoflow.command(name='sync-gallery')
def sync_gallery():
    pass
```

## File Structure Examples
```
Camera:
/Volumes/Fuji X-T4/DCIM/
├── 102_FUJI/
│   ├── DSCF0430.JPG
│   ├── DSCF0430.RAF
│   └── DSCF1451.MOV

Local:
Pictures/
├── Staging/          # DSCF0430.JPG
├── Final/            # Final approved photos → upload to immich
└── RAWs/             # DSCF0430.RAF (backup)

External SSD:
/Volumes/EXT/Videos/Videos/
└── DSCF1451.MOV

Gallery workspace:
/Users/johannes.krumm/SourceRoot/photo-flow/photo_gallery/
└── src/images/ (synced high‑rated JPGs)
```

## Detailed Workflow Logic

### Import Phase
```
1. Scan /Volumes/Fuji X-T4/DCIM for files (all subfolders like 102_FUJI)
2. Categorize by extension:
   - .MOV → Copy to SSD
   - .JPG → Copy to Staging
   - .RAF → Copy to RAWs
3. Check duplicates (hash comparison)
4. Copy files (preserve originals), then optionally delete originals after verified copy
5. Report results
```

### Status Check
```
Status Report:
- Camera connected: Yes/No
- SSD connected: Yes/No  
- Pending videos: X .MOV files
- Pending photos: X .JPG, X .RAF files (excluding JPGs already in Final)
- Staging status: Empty/X files ready for review
```

### Finalization Phase
```
1. Move staging JPGs to Final folder
2. Copy Final JPGs back to camera (to the first available DCIM subfolder)
3. Clear staging folder
4. Optionally delete matching RAWs from camera for finalized images
```

### Cleanup Phase
```
1. Scan Final folder for JPGs (DSCF*.JPG)
2. Find corresponding RAWs in RAWs folder (same DSCF number)
3. List orphaned RAWs (no matching Final JPG)
4. Confirm deletion
5. Delete orphaned RAWs
```

### Gallery Sync Phase
```
1. Scan Final folder for JPGs
2. Extract metadata; filter images with rating >= 4
3. Copy qualifying images to GALLERY_PATH/images (skip unchanged)
4. Remove images from gallery that no longer qualify
5. Generate metadata.json for high‑rated images
6. Build static gallery (npm run build) and rsync dist/ to remote
```

## Safety Mechanisms
1. **Copy-first approach** - Never delete source until copy verified
2. **Hash verification** - Confirm file integrity after copy
3. **Dry-run mode** - Preview all operations
4. **Confirmation prompts** - For destructive operations (RAW cleanup)

## CLI Commands

```bash
photoflow status                        # Check what's pending
photoflow import                        # Import from camera
photoflow finalize                      # Staging → Final + back to camera
photoflow cleanup                       # Remove unused RAWs
photoflow sync-gallery                  # Sync high-rated photos to gallery
photoflow import --dry-run              # Preview import
photoflow cleanup --dry-run             # Preview RAW cleanup
photoflow sync-gallery --dry-run        # Preview gallery sync
```

## Error Handling
- **Path validation** - Check camera/SSD/gallery paths mounted/exist
- **Disk space** - Verify sufficient space
- **Permission checks** - Ensure write access
- **Clear error messages** - Simple problem descriptions

## Configuration (Hardcoded)
```python
# config.py
CAMERA_PATH = Path("/Volumes/Fuji X-T4/DCIM")
STAGING_PATH = Path("/Users/johannes.krumm/Pictures/Staging")
RAWS_PATH = Path("/Users/johannes.krumm/Pictures/RAWs")
FINAL_PATH = Path("/Users/johannes.krumm/Pictures/Final")
SSD_PATH = Path("/Volumes/EXT/Videos/Videos")
GALLERY_PATH = Path("/Users/johannes.krumm/SourceRoot/photo-flow/photo_gallery/src")

EXTENSIONS = {'.JPG', '.RAF', '.MOV'}
```

Simple, focused tool that does exactly what you need for your JPG workflow + gallery/immich pipeline.