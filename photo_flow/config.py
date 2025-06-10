"""
Configuration settings for the Photo-Flow application.

This module contains hardcoded paths and settings used throughout the application.
"""

from pathlib import Path

# File paths
CAMERA_PATH = Path("/Volumes/Fuji X-T4/DCIM/102_FUJI")
STAGING_PATH = Path("/Users/johannes.krumm/Pictures/Staging")
RAWS_PATH = Path("/Users/johannes.krumm/Pictures/RAWs")
FINAL_PATH = Path("/Users/johannes.krumm/Pictures/Final")
SSD_PATH = Path("/Volumes/EXT/Videos/Videos")

# Image processing settings
CLARITY_ADJUSTMENT = -3

# File extensions to process
EXTENSIONS = {'.JPG', '.RAF', '.MOV'}