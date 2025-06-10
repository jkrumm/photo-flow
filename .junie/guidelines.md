# Technical Design Document - Python CLI Photo-Flow Tool (Revised)

## Project Overview
Personal CLI tool for managing Fuji X-T4 camera photos/videos with staging workflow for JPG photography with RAW backups.

## System Architecture

### File Paths (Hardcoded)
```python
CAMERA_PATH = "/Volumes/Fuji X-T4/DCIM/102_FUJI"
STAGING_PATH = "/Users/johannes.krumm/Pictures/Staging"
RAWS_PATH = "/Users/johannes.krumm/Pictures/RAWs"
FINAL_PATH = "/Users/johannes.krumm/Pictures/Final"
SSD_PATH = "/Volumes/EXT/Videos/Videos"
```

### Technology Stack
- **Python 3.9** with venv
- **Click** - CLI framework
- **Pillow** - Image processing (clarity effect)
- **shutil** - File operations (copy with metadata)
- **pathlib** - Modern path handling
- **hashlib** - File duplicate detection

## Core Components

### 1. File Manager (`file_manager.py`)
```python
class FileManager:
    def scan_camera_files() -> Dict[str, List[Path]]
    def is_duplicate(src: Path, dst: Path) -> bool
    def safe_copy(src: Path, dst: Path) -> bool
    def get_file_hash(file_path: Path) -> str
```

### 2. Image Processor (`image_processor.py`)
```python
class ImageProcessor:
    def apply_clarity_effect(jpg_path: Path) -> Path
    def preserve_exif(original: Path, processed: Path)
```

### 3. Workflow Manager (`workflow.py`)
```python
class PhotoWorkflow:
    def import_from_camera()
    def finalize_staging()
    def cleanup_unused_raws()
    def get_status() -> StatusReport
```

### 4. CLI Interface (`cli.py`)
```python
@click.group()
def photoflow():
    pass

@photoflow.command()
def status():
    pass

@photoflow.command()
def import_files():
    pass
```

## File Structure Examples
```
Camera:
/Volumes/Fuji X-T4/DCIM/102_FUJI/
├── DSCF0430.JPG
├── DSCF0430.RAF
└── DSCF1451.MOV

Local:
Pictures/
├── Staging/          # DSCF0430.JPG (with clarity effect)
├── Final/           # Final approved photos → upload to immich
└── RAWs/           # DSCF0430.RAF (backup)

External SSD:
/Volumes/EXT/Videos/Videos/
└── DSCF1451.MOV
```

## Detailed Workflow Logic

### Import Phase
```
1. Scan /Volumes/Fuji X-T4/DCIM/102_FUJI for files
2. Categorize by extension:
   - .MOV → Copy to SSD
   - .JPG → Copy to Staging (apply clarity effect)
   - .RAF → Copy to RAWs
3. Check duplicates (hash comparison)
4. Copy files (preserve originals)
5. Report results
```

### Status Check
```
Status Report:
- Camera connected: Yes/No
- SSD connected: Yes/No  
- Pending videos: X .MOV files
- Pending photos: X .JPG, X .RAF files
- Staging status: Empty/X files ready for review
```

### Finalization Phase
```
1. Copy staging JPGs to Final folder
2. Copy Final JPGs back to camera
3. Clear staging folder
4. Ready for immich upload (manual)
```

### Cleanup Phase
```
1. Scan Final folder for JPGs (DSCF*.JPG)
2. Find corresponding RAWs in RAWs folder (same DSCF number)
3. List orphaned RAWs (no matching Final JPG)
4. Confirm deletion
5. Delete orphaned RAWs
```

## Safety Mechanisms
1. **Copy-first approach** - Never delete source until copy verified
2. **Hash verification** - Confirm file integrity after copy
3. **Dry-run mode** - Preview all operations
4. **Confirmation prompts** - For destructive operations (RAW cleanup)

## CLI Commands

```bash
photoflow status                    # Check what's pending
photoflow import                    # Import from camera
photoflow finalize                  # Staging → Final + back to camera
photoflow cleanup                   # Remove unused RAWs
photoflow import --dry-run          # Preview import
photoflow cleanup --dry-run         # Preview RAW cleanup
```

## Error Handling
- **Path validation** - Check camera/SSD mounted
- **Disk space** - Verify sufficient space
- **Permission checks** - Ensure write access
- **Clear error messages** - Simple problem descriptions

## Configuration (Hardcoded)
```python
# config.py
CAMERA_PATH = Path("/Volumes/Fuji X-T4/DCIM/102_FUJI")
STAGING_PATH = Path("/Users/johannes.krumm/Pictures/Staging")
RAWS_PATH = Path("/Users/johannes.krumm/Pictures/RAWs")
FINAL_PATH = Path("/Users/johannes.krumm/Pictures/Final")
SSD_PATH = Path("/Volumes/EXT/Videos/Videos")

CLARITY_ADJUSTMENT = -3
EXTENSIONS = {'.JPG', '.RAF', '.MOV'}
```

Simple, focused tool that does exactly what you need for your JPG workflow + immich upload pipeline.