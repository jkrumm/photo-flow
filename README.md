# Photo-Flow

A CLI tool for managing Fuji X-T4 camera photos/videos with a staging workflow for JPG photography with RAW backups.

## Features

- Import photos and videos from Fuji X-T4 camera
- Apply clarity effect to JPG images
- Manage workflow with staging and final folders
- Backup RAW files
- Copy videos to external SSD
- Clean up unused RAW files

## Installation

### Prerequisites

- Python 3.9 or higher
- Fuji X-T4 camera
- External SSD for videos (optional)

### Install from source

1. Clone the repository:
   ```bash
   git clone https://github.com/yourusername/photo-flow.git
   cd photo-flow
   ```

2. Create and activate a virtual environment (recommended):
   ```bash
   python -m venv venv
   source venv/bin/activate  # On Windows: venv\Scripts\activate
   ```

3. Install the package:
   ```bash
   pip install -e .
   ```

## Usage

### Check Status

```bash
photoflow status
```

This command shows:
- Camera connection status
- SSD connection status
- Pending files on camera
- Files in staging area

### Import Files

```bash
photoflow import
```

This imports:
- JPG files to the staging folder (with clarity effect)
- RAW files to the RAW backup folder
- Video files to the external SSD

To preview without copying:

```bash
photoflow import --dry-run
```

### Finalize Staging

```bash
photoflow finalize
```

This moves approved photos from staging to the final folder.

### Clean Up Unused RAWs

```bash
photoflow cleanup
```

This removes RAW files that don't have corresponding JPGs in the final folder.

## File Structure

```
Camera:
/Volumes/Fuji X-T4/DCIM/100_FUJI/
├── DSCF0430.JPG
├── DSCF0430.RAF
└── DSCF1451.MOV

Local:
Pictures/
├── Staging/          # DSCF0430.JPG (with clarity effect)
├── Final/            # Final approved photos → upload to immich
└── RAWs/             # DSCF0430.RAF (backup)

External SSD:
/Volumes/EXT/Videos/Videos/
└── DSCF1451.MOV
```

## Configuration

The application uses hardcoded paths in `photo_flow/config.py`. Modify these to match your system:

```python
CAMERA_PATH = Path("/Volumes/Fuji X-T4/DCIM/100_FUJI")
STAGING_PATH = Path("/Users/johannes.krumm/Pictures/Staging")
RAWS_PATH = Path("/Users/johannes.krumm/Pictures/RAWs")
FINAL_PATH = Path("/Users/johannes.krumm/Pictures/Final")
SSD_PATH = Path("/Volumes/EXT/Videos/Videos")
```

## License

This project is licensed under the terms of the license included in the repository.
