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
# Optional safety mirror of Staging. Deliberately a SIBLING of the Fuji folder, not inside it:
# Immich mounts .../Bilder/Fuji read-only as an external library, and unfinalized, unrated
# photos have no business showing up there.
HOMELAB_SSD_STAGING_PATH = Path("/home/jkrumm/ssd/SSD/Bilder/Staging")
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
# These files are not portable and not part of the actual photo data.
# NEVER add EDIT_SIDECAR_SUFFIX here — the .photo-edit sidecars are the only copy of
# Photomator's re-editable edit history and must ride along to the homelab.
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

# ---------------------------------------------------------------------------
# Culling view (control panel "Photos" screen)
# ---------------------------------------------------------------------------
# Roots the culling browser may read. Nothing outside this map is servable —
# routes_photos.py resolves every incoming path against it and 400s on a miss.
CULL_ROOTS = {
    "final": FINAL_PATH,
    "staging": STAGING_PATH,
}

# The external editor the culling view hands a photo to.
#
# A macOS application NAME, resolved by Launch Services (`open -a`), not a path: the
# bundle moves between ~/Applications and /Applications depending on how it was installed
# and a hardcoded path would break on the other one. Shutterflow lives in
# ~/SourceRoot/shutterflow; `make install` there puts the bundle where this can find it.
#
# Nothing in the pipeline depends on this. If the app is absent the endpoint reports that
# and no photo is touched — the editor is a convenience on top of the library, never a
# step in it.
EXTERNAL_EDITOR_APP = "Shutterflow"

# Derived thumbnail/preview cache. Disposable: safe to delete, regenerates on demand.
THUMB_CACHE_PATH = Path.home() / ".photoflow" / "thumbs"
# Long-edge pixels per tier. `grid` feeds the filmstrip, `view` the big viewer.
THUMB_SIZES = {"grid": 320, "view": 2048}
THUMB_QUALITY = {"grid": 78, "view": 86}

# Soft-delete staging area for culled photos. Deliberately under ~/Pictures so a move
# from Final or Staging is a same-filesystem rename (atomic, instant, no copy).
# Leading dot keeps it out of Photos/Photomator library scans.
TRASH_PATH = Path.home() / "Pictures" / ".photoflow-trash"
# Days a trashed file is retained before `photoflow trash purge` may delete it.
TRASH_RETENTION_DAYS = 30

# Image processing settings
CLARITY_ADJUSTMENT = -3

# File extensions to process
EXTENSIONS = {'.JPG', '.RAF', '.MOV'}

# Photomator's re-editable edit history, stored as <jpg-stem>.photo-edit next to the JPG.
# Irreplaceable (there is no second copy) and ~17 MB each, so it travels with its JPG
# through finalize and is included in the Final backup.
EDIT_SIDECAR_SUFFIX = '.photo-edit'
