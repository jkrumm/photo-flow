"""
Configuration settings for the Photo-Flow application.

This module contains hardcoded paths and settings used throughout the application.
"""

from pathlib import Path

# File paths
CAMERA_PATH = Path("/Volumes/Fuji X-T4/DCIM")
STAGING_PATH = Path("/Users/johannes.krumm/Pictures/Staging")
RAWS_PATH = Path("/Users/johannes.krumm/Pictures/RAWs")
FINAL_PATH = Path("/Users/johannes.krumm/Pictures/Final")
SSD_PATH = Path("/Volumes/EXT/Videos/Videos")
GALLERY_PATH = Path("/Users/johannes.krumm/SourceRoot/photo-flow/photo_gallery/src")

# Remote backup (homelab) settings
HOMELAB_USER = "jkrumm"
HOMELAB_HOST = "homelab.jkrumm.com"
HOMELAB_DEST_PATH = Path("/home/jkrumm/ssd/SSD/Bilder/Fuji")
# Jump host for IPv4-only networks (auto-fallback if direct IPv6 fails)
HOMELAB_JUMP_HOST = "5.75.178.196"  # VPS IPv4 address
HOMELAB_JUMP_USER = "jkrumm"
# Default rsync flags optimized for speed and safety over SSH
# -a archive, -v verbose, --delete keep remote in sync, --partial resume partial transfers, --whole-file avoids delta CPU overhead for new/changed files
RSYNC_FLAGS = ["-av", "--delete", "--partial", "--whole-file", "--progress"]
# Use a faster SSH configuration: disable SSH stream compression and prefer a fast cipher
# Note: You can change this if your environment prefers a different cipher.
RSYNC_SSH_BASE = "ssh -T -c aes128-gcm@openssh.com -o Compression=no -o ConnectTimeout=5"
# SSH with ProxyJump for IPv4-only networks
RSYNC_SSH_JUMP = f"{RSYNC_SSH_BASE} -J {HOMELAB_JUMP_USER}@{HOMELAB_JUMP_HOST}"

# Image processing settings
CLARITY_ADJUSTMENT = -3

# File extensions to process
EXTENSIONS = {'.JPG', '.RAF', '.MOV'}
