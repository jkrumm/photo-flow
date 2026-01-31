"""
Timestamp-based file renaming for Photo-Flow.

This module provides functionality to rename files using their EXIF DateTimeOriginal
timestamp, preventing filename collisions when Fuji X-T4's DSCF counter wraps.

Format: YYYY-MM-DD_HH-MM-SS_<original_base>.<ext>
Example: 2026-01-28_10-29-15_DSCF1234.JPG
"""

import re
import subprocess
from datetime import datetime
from pathlib import Path
from typing import Optional, Set

# Regex pattern to detect already-renamed files
# Matches: YYYY-MM-DD_HH-MM-SS_ with optional counter suffix before underscore
TIMESTAMP_PREFIX_PATTERN = re.compile(r'^\d{4}-\d{2}-\d{2}_\d{2}-\d{2}-\d{2}(-\d+)?_')


def is_already_renamed(filename: str) -> bool:
    """
    Check if a file has already been renamed to timestamp format.

    Args:
        filename: The filename to check (not full path)

    Returns:
        True if filename matches timestamp format, False otherwise

    Examples:
        >>> is_already_renamed("2026-01-28_10-29-15_DSCF1234.JPG")
        True
        >>> is_already_renamed("2026-01-28_10-29-15-2_DSCF1234.JPG")  # With counter
        True
        >>> is_already_renamed("DSCF1234.JPG")
        False
    """
    return bool(TIMESTAMP_PREFIX_PATTERN.match(filename))


def extract_original_base(filename: str) -> str:
    """
    Extract the original filename base (e.g., DSCF0430) from a potentially renamed file.

    For correlation between JPG and RAF files after timestamp renaming.

    Args:
        filename: The filename (with or without timestamp prefix)

    Returns:
        The original base without extension (e.g., "DSCF0430")

    Examples:
        >>> extract_original_base("2026-01-28_10-29-15_DSCF0430.JPG")
        'DSCF0430'
        >>> extract_original_base("2026-01-28_10-29-15-2_DSCF0430.RAF")  # With counter
        'DSCF0430'
        >>> extract_original_base("DSCF0430.JPG")
        'DSCF0430'
    """
    # Get stem (filename without extension)
    stem = Path(filename).stem

    # If it has timestamp prefix, extract the part after it
    match = TIMESTAMP_PREFIX_PATTERN.match(filename)
    if match:
        # Return everything after the timestamp prefix, but without extension
        prefix_end = match.end()
        return Path(filename[prefix_end:]).stem

    # No timestamp prefix, just return the stem
    return stem


def get_timestamp_from_exif(file_path: Path) -> Optional[datetime]:
    """
    Extract DateTimeOriginal from file's EXIF metadata using exiftool.

    Uses exiftool for reliable metadata extraction that survives edits.

    Args:
        file_path: Path to the image/video file

    Returns:
        datetime object if DateTimeOriginal found, None otherwise
    """
    try:
        # Use exiftool to get DateTimeOriginal
        result = subprocess.run(
            ["exiftool", "-DateTimeOriginal", "-s3", str(file_path)],
            capture_output=True,
            text=True,
            timeout=10
        )

        if result.returncode != 0 or not result.stdout.strip():
            # Try CreateDate as fallback (commonly used in MOV files)
            result = subprocess.run(
                ["exiftool", "-CreateDate", "-s3", str(file_path)],
                capture_output=True,
                text=True,
                timeout=10
            )

        if result.returncode != 0 or not result.stdout.strip():
            return None

        # Parse the date string (format: "2026:01:28 10:29:15")
        date_str = result.stdout.strip()

        # Handle various date formats exiftool might return
        for fmt in [
            "%Y:%m:%d %H:%M:%S",      # Standard EXIF format
            "%Y-%m-%d %H:%M:%S",      # Alternative format
            "%Y:%m:%d %H:%M:%S%z",    # With timezone
            "%Y-%m-%d %H:%M:%S%z",    # Alternative with timezone
        ]:
            try:
                return datetime.strptime(date_str[:19], fmt[:17])  # Truncate to handle timezones
            except ValueError:
                continue

        return None

    except subprocess.TimeoutExpired:
        return None
    except FileNotFoundError:
        # exiftool not installed
        return None
    except Exception:
        return None


def generate_timestamped_filename(
    file_path: Path,
    existing_names: Set[str]
) -> tuple[str, str]:
    """
    Generate a timestamped filename for a file, handling collisions.

    Format: YYYY-MM-DD_HH-MM-SS_<original_base>.<ext>
    With collision handling: YYYY-MM-DD_HH-MM-SS-2_<original_base>.<ext>

    Args:
        file_path: Path to the file to rename
        existing_names: Set of filenames already in the destination folder
                       (used for collision detection)

    Returns:
        tuple of (new_filename, error_message)
        - new_filename: The generated filename, or original if timestamp unavailable
        - error_message: Empty string on success, error description otherwise
    """
    original_name = file_path.name
    original_stem = file_path.stem
    extension = file_path.suffix

    # Skip if already renamed
    if is_already_renamed(original_name):
        return original_name, ""

    # Get timestamp from EXIF
    timestamp = get_timestamp_from_exif(file_path)

    if timestamp is None:
        # Return original name if no timestamp available
        return original_name, f"No EXIF timestamp found for {original_name}"

    # Format timestamp: YYYY-MM-DD_HH-MM-SS
    ts_str = timestamp.strftime("%Y-%m-%d_%H-%M-%S")

    # Generate base filename
    base_filename = f"{ts_str}_{original_stem}{extension}"

    # Check for collision
    if base_filename not in existing_names:
        return base_filename, ""

    # Handle collision with counter suffix
    counter = 2
    while True:
        collision_filename = f"{ts_str}-{counter}_{original_stem}{extension}"
        if collision_filename not in existing_names:
            return collision_filename, ""
        counter += 1

        # Safety limit (extremely unlikely to hit)
        if counter > 1000:
            return original_name, f"Too many collisions for {original_name}"
