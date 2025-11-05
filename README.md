# Photo-Flow

A personal CLI tool for managing Fuji X-T4 camera photos/videos with a staging workflow for JPG photography with RAW backups, plus an optional gallery sync for high-rated images.

> **Note**: This is a personal tool designed for local use on a single developer's machine, not production software intended for distribution or multi-user environments.

## Features

- Import photos and videos from Fuji X-T4 camera
- Manage workflow with staging and final folders for JPGs
- Backup RAW (.RAF) files
- Copy videos (.MOV) to external SSD
- Sync high-rated photos (rating ≥ 4) to a gallery with intelligent file handling
- Hash-based duplicate detection and post-copy verification
- Dry-run mode for import, cleanup, and sync-gallery
- Confirmation prompts for destructive actions
- **Safety-first architecture**: copy-first approach, atomic operations, automatic backups
- **Complete metadata preservation**: All EXIF, IPTC, XMP data preserved during compression
- **Smart connectivity**: Automatic IPv4/IPv6 fallback for backups when traveling
- **Beautiful CLI output**: Rich-formatted terminal output with color-coded status, progress indicators, and structured summaries
- Case-insensitive handling for .JPG/.RAF

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
photoflow cleanup
photoflow sync-gallery
photoflow backup
```
That's it! No virtual environments to activate, works from any directory. ✨

## Commands

### `photoflow status`
Check the current status of your workflow:
- Camera connection status (color-coded: green ✓ / red ✗)
- SSD connection status (color-coded: green ✓ / red ✗)
- Pending files on camera (displayed in formatted table)
- Files in staging area

**Output**: Rich-formatted tables with color-coded status indicators

### `photoflow import`
Import files from your camera:
- JPG files → Staging folder
- RAW files → RAWs backup folder
- Video files → External SSD

Add `--dry-run` to preview without copying files.

**Example:**
```bash
photoflow import --dry-run
photoflow import
```

**Output**: Progress updates, success/error indicators (✓/✗), and structured summary

### `photoflow finalize`
Move and compress approved photos from staging to final folder:
- **Atomically processes each photo**: compress → copy to Final → delete from Staging
  - **Compresses** (resize to ≤5200×3467, quality 92, 4:4:4 chroma, preserves ALL metadata)
  - **Interrupt-safe**: Ctrl+C leaves remaining files in Staging for retry
  - **Guarantees**: Every file in Final is compressed
- Copies compressed photos back to camera for viewing
- Removes orphaned RAW files (no matching JPG in Final)
- Cleans up RAW files from camera

Add `--dry-run` to preview without moving files.

**Example:**
```bash
photoflow finalize --dry-run
photoflow finalize
```

**Output**: Progress updates for compression, success indicators, detailed summary

### `photoflow cleanup`
Remove unused RAW files that don’t have corresponding JPGs in the Final folder.

- Always previews first; asks for confirmation before deleting
- Use `--dry-run` to see only the preview and skip deletion

**Example:**
```bash
photoflow cleanup --dry-run
photoflow cleanup
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
```bash
photoflow sync-gallery --dry-run
photoflow sync-gallery
```

### `photoflow backup`
Backup the Final folder to your homelab via rsync:
- **Automatic connectivity fallback**: Tries direct IPv6 first, falls back to ProxyJump via VPS for IPv4-only networks
- **Smart filtering**: Excludes system files (`.DS_Store`, `._*`, `Thumbs.db`, etc.) - only backs up your photos
- Syncs the contents of Final/ to the remote directory
- Uses rsync for safe, interruptible transfers (--partial)
- Keeps remote in sync (uses --delete)

Add `--dry-run` to preview without sending data.

**Example:**
```bash
photoflow backup --dry-run
photoflow backup
```

## Configuration

Edit paths in `photo_flow/config.py` to match your system:
```python
from pathlib import Path

CAMERA_PATH = Path("/Volumes/Fuji X-T4/DCIM")
STAGING_PATH = Path("/Users/johannes.krumm/Pictures/Staging")
RAWS_PATH = Path("/Users/johannes.krumm/Pictures/RAWs")
FINAL_PATH = Path("/Users/johannes.krumm/Pictures/Final")
SSD_PATH = Path("/Volumes/EXT/Videos/Videos")
GALLERY_PATH = Path("/Users/johannes.krumm/SourceRoot/photo-flow/photo_gallery/src")

# Remote backup (homelab) - automatic IPv4/IPv6 fallback
HOMELAB_USER = "jkrumm"
HOMELAB_HOST = "homelab.jkrumm.com"
HOMELAB_DEST_PATH = Path("/home/jkrumm/ssd/SSD/Bilder/Fuji")
HOMELAB_JUMP_HOST = "5.75.178.196"  # VPS IPv4 for ProxyJump fallback
HOMELAB_JUMP_USER = "jkrumm"
RSYNC_FLAGS = ["-av", "--delete", "--partial", "--whole-file", "--progress"]
RSYNC_EXCLUDE_PATTERNS = [".DS_Store", "._*", "Thumbs.db", ".Spotlight-V100", ".Trashes", ".fseventsd"]
RSYNC_SSH_BASE = "ssh -T -c aes128-gcm@openssh.com -o Compression=no -o ConnectTimeout=5"
RSYNC_SSH_JUMP = f"{RSYNC_SSH_BASE} -J {HOMELAB_JUMP_USER}@{HOMELAB_JUMP_HOST}"

EXTENSIONS = {'.JPG', '.RAF', '.MOV'}
```

## Output Examples

The tool provides beautiful, color-coded terminal output:

```
Photo-Flow Status Report

┏━━━━━━━━━━━━━━┳━━━━━━━━━━━━━━━━━┓
┃ Component    ┃ Status          ┃
┡━━━━━━━━━━━━━━╇━━━━━━━━━━━━━━━━━┩
│ Camera       │ ✓ Connected     │
│ External SSD │ ✓ Connected     │
└──────────────┴─────────────────┘

Pending files on camera:
┌────────────────────┬───────┐
│ Videos (.MOV)      │    45 │
│ Photos (.JPG)      │   120 │
│ RAW files (.RAF)   │   120 │
└────────────────────┴───────┘

Staging status: 12 files ready for review
```

All commands provide:
- ✓ **Success indicators** in green
- ✗ **Error indicators** in red
- **Structured summaries** of results
- **Progress updates** during long operations

### Prerequisites
- **exiftool** for metadata preservation: `brew install exiftool`
- **rsync** for backups: usually pre-installed on macOS
- Node.js and npm for gallery build/sync (optional, respects `.nvmrc` if present)

## File Structure
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
└── src/images/       # Synced high-rated JPGs
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
