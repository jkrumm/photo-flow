# Photo-Flow

A CLI tool for managing Fuji X-T4 camera photos/videos with a staging workflow for JPG photography with RAW backups.

## Features

- Import photos and videos from Fuji X-T4 camera
- Apply clarity effect to JPG images
- Manage workflow with staging and final folders
- Backup RAW files
- Copy videos to external SSD
- Automatically clean up unused RAW files during finalization
- Sync high-rated photos to a gallery with intelligent file handling

## Quick Start

### 1. Install pipx (one-time setup)
```bash
brew install pipx
pipx ensurepath
source ~/.zshrc
```
### 2. Install Photo-Flow
```bash
git clone https://github.com/yourusername/photo-flow.git
cd photo-flow
pipx install -e .
```
### 3. Start using it anywhere!
```bash
photoflow status
photoflow import
photoflow finalize
photoflow sync-gallery
```
That's it! No virtual environments to activate, works from any directory. ✨

## Commands

### `photoflow status`
Check the current status of your workflow:
- Camera connection status
- SSD connection status
- Pending files on camera
- Files in staging area

### `photoflow import`
Import files from your camera:
- JPG files → Staging folder (with clarity effect)
- RAW files → RAWs backup folder
- Video files → External SSD

Add `--dry-run` to preview without copying files.

**Example:**
```bash
photoflow import --dry-run
photoflow import
```
### `photoflow finalize`
Move approved photos from staging to final folder:
- Moves photos to Final folder
- Copies them back to camera for viewing
- Removes orphaned RAW files (no matching JPG in Final)
- Cleans up RAW files from camera

Add `--dry-run` to preview without moving files.

**Example:**
```bash
photoflow finalize --dry-run
photoflow finalize
```
### `photoflow sync-gallery`
Sync high-rated photos (4+ stars) to your gallery:
- Copies new high-rated images to gallery
- Updates changed images (using fast hash comparison)
- Removes images no longer rated 4+
- Generates metadata JSON
- Builds and syncs gallery to remote server

Add `--dry-run` to preview without syncing files.

**Example:**
```
bash
photoflow sync-gallery --dry-run
photoflow sync-gallery
```
## Configuration

Edit paths in `photo_flow/config.py` to match your system:
```python
CAMERA_PATH = Path("/Volumes/Fuji X-T4/DCIM/102_FUJI")
STAGING_PATH = Path("/Users/johannes.krumm/Pictures/Staging")
RAWS_PATH = Path("/Users/johannes.krumm/Pictures/RAWs")
FINAL_PATH = Path("/Users/johannes.krumm/Pictures/Final")
SSD_PATH = Path("/Volumes/EXT/Videos/Videos")
```
## File Structure
```
Camera:
/Volumes/Fuji X-T4/DCIM/102_FUJI/
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
## Maintenance

### Update after code changes
Since it's installed in editable mode (`-e`), your changes are reflected immediately. No need to reinstall!

### Upgrade Photo-Flow
```bash
pipx upgrade photo-flow
```
### Uninstall
```bash
pipx uninstall photo-flow
```

## For Developers

If you prefer working in a virtual environment:
```bash
python3 -m venv venv
source venv/bin/activate
pip install -e .
```
Note: You'll need to activate the venv each time before using commands.

## License

This project is licensed under the terms of the license included in the repository.
