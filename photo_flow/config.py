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

# Gallery deployment (VPS) — static Astro site rsynced to a host directory
# served by nginx (vps repo: apps/photo-gallery/). Public at photos.jkrumm.com.
GALLERY_REMOTE_USER = "jkrumm"
GALLERY_REMOTE_HOST = "100.97.220.54"  # VPS Tailscale IP (matches ~/.ssh/config "vps")
GALLERY_REMOTE_PATH = Path("/home/jkrumm/photo-gallery-dist")

# Remote backup (homelab) settings
HOMELAB_USER = "jkrumm"
HOMELAB_HOST = "100.85.139.104"  # Tailscale IP
# SSD backup path (for Final JPEGs - fast access)
HOMELAB_SSD_FINAL_PATH = Path("/home/jkrumm/ssd/SSD/Bilder/Fuji")
# HDD backup paths (for large files - RAWs and Videos)
HOMELAB_HDD_RAWS_PATH = Path("/mnt/hdd/fuji/RAWs")
HOMELAB_HDD_VIDEOS_PATH = Path("/mnt/hdd/fuji/Videos")
# Trash folders for deleted files (must be on the same filesystem as the destination)
HOMELAB_SSD_TRASH_PATH = Path("/home/jkrumm/ssd/SSD/Bilder/.trash")  # Same SSD as Final
HOMELAB_HDD_TRASH_PATH = Path("/mnt/hdd/fuji/.trash")                 # Same HDD as RAWs/Videos
# Legacy alias - kept for any direct references
HOMELAB_TRASH_PATH = HOMELAB_HDD_TRASH_PATH
# Legacy alias for backwards compatibility
HOMELAB_DEST_PATH = HOMELAB_SSD_FINAL_PATH
# Exclude system files from backup (macOS resource forks, Windows thumbnails, etc.)
# These files are not portable and not part of the actual photo data
RSYNC_EXCLUDE_PATTERNS = [
    ".DS_Store",      # macOS folder view settings
    "._*",            # macOS AppleDouble resource forks (extended attributes)
    "Thumbs.db",      # Windows thumbnail cache
    ".Spotlight-V100", # macOS Spotlight index
    ".Trashes",       # macOS trash folder
    ".fseventsd",     # macOS filesystem events
]
# rclone parallel transfer settings
# Connection via Tailscale (encrypted mesh network, no port exposure needed)
RCLONE_TRANSFERS = 8           # Parallel file transfers (optimal for 5–100MB files over LAN)
RCLONE_SSH_CIPHER = "aes128-gcm@openssh.com"
RCLONE_SFTP_CONCURRENCY = 64  # Concurrent SFTP requests per transfer (speeds up large files)
# SSH options list for direct SSH calls (e.g. remote file count check)
HOMELAB_SSH_OPTS = ["-T", "-c", "aes128-gcm@openssh.com", "-o", "Compression=no", "-o", "ConnectTimeout=5"]

# Image processing settings
CLARITY_ADJUSTMENT = -3

# File extensions to process
EXTENSIONS = {'.JPG', '.RAF', '.MOV'}
