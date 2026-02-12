"""
Configuration settings for the Photo-Flow application.

This module contains hardcoded paths and settings used throughout the application.
"""

from pathlib import Path

# File paths
CAMERA_PATH = Path("/Volumes/Fuji X-T4/DCIM")
STAGING_PATH = Path("/Users/johannes.krumm/Pictures/Staging")
RAWS_PATH = Path("/Volumes/EXT/Bilder/RAWs")
FINAL_PATH = Path("/Users/johannes.krumm/Pictures/Final")
SSD_PATH = Path("/Volumes/EXT/Videos/Videos")
GALLERY_PATH = Path("/Users/johannes.krumm/SourceRoot/photo-flow/photo_gallery/src")

# Remote backup (homelab) settings
HOMELAB_USER = "jkrumm"
HOMELAB_HOST = "100.85.139.104"  # Tailscale IP
# SSD backup path (for Final JPEGs - fast access)
HOMELAB_SSD_FINAL_PATH = Path("/home/jkrumm/ssd/SSD/Bilder/Fuji")
# HDD backup paths (for large files - RAWs and Videos)
HOMELAB_HDD_RAWS_PATH = Path("/mnt/hdd/fuji/RAWs")
HOMELAB_HDD_VIDEOS_PATH = Path("/mnt/hdd/fuji/Videos")
# Trash folder for deleted files (instead of permanent delete)
HOMELAB_TRASH_PATH = Path("/mnt/hdd/fuji/.trash")
# Legacy alias for backwards compatibility
HOMELAB_DEST_PATH = HOMELAB_SSD_FINAL_PATH
# Default rsync flags optimized for speed and safety over SSH
# -a archive, -v verbose (shows files being transferred), --delete keep remote in sync
# --partial resume partial transfers, --whole-file avoids delta CPU overhead for new/changed files
# --progress shows transfer speed and file numbers
RSYNC_FLAGS = ["-av", "--delete", "--partial", "--whole-file", "--progress"]
# Exclude system files from rsync (macOS resource forks, Windows thumbnails, etc.)
# These files are not portable and not part of the actual photo data
RSYNC_EXCLUDE_PATTERNS = [
    ".DS_Store",      # macOS folder view settings
    "._*",            # macOS AppleDouble resource forks (extended attributes)
    "Thumbs.db",      # Windows thumbnail cache
    ".Spotlight-V100", # macOS Spotlight index
    ".Trashes",       # macOS trash folder
    ".fseventsd",     # macOS filesystem events
]
# Use a faster SSH configuration: disable SSH stream compression and prefer a fast cipher
# Connection via Tailscale (encrypted mesh network, no port exposure needed)
RSYNC_SSH_CMD = "ssh -T -c aes128-gcm@openssh.com -o Compression=no -o ConnectTimeout=5"

# Image processing settings
CLARITY_ADJUSTMENT = -3

# File extensions to process
EXTENSIONS = {'.JPG', '.RAF', '.MOV'}
