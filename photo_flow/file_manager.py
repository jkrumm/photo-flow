"""
File management utilities for the Photo-Flow application.

This module provides functionality for scanning, copying, and verifying files.
"""

import hashlib
import shutil
from pathlib import Path
from typing import Dict, List

from photo_flow.config import CAMERA_PATH, EXTENSIONS


def is_valid_image_file(file_path: Path) -> bool:
    """
    Check if a file is a valid image file (not a system/metadata file).

    Args:
        file_path (Path): Path to the file

    Returns:
        bool: True if the file is a valid image file, False otherwise
    """
    filename = file_path.name
    return not (
        filename.startswith('._') or          # macOS resource forks
        filename.startswith('.DS_Store') or   # macOS metadata
        filename.startswith('Thumbs.db')      # Windows thumbnails
    )


def scan_for_images(directory: Path, extension: str = '.JPG') -> List[Path]:
    """
    Scan a directory for image files with case-insensitive extension matching.

    Args:
        directory (Path): Directory to scan
        extension (str): File extension to look for (default: '.JPG')

    Returns:
        List[Path]: List of valid image files
    """
    # Remove the dot if present to normalize the extension
    if extension.startswith('.'):
        extension = extension[1:]

    # Get both uppercase and lowercase versions
    upper_ext = f'*.{extension.upper()}'
    lower_ext = f'*.{extension.lower()}'

    # Scan for both uppercase and lowercase extensions
    upper_files = [f for f in directory.glob(upper_ext) if is_valid_image_file(f)]
    lower_files = [f for f in directory.glob(lower_ext) if is_valid_image_file(f)]

    # Combine the results
    return upper_files + lower_files


class FileManager:
    """
    Handles file operations for the Photo-Flow application.

    This class provides methods for scanning camera files, checking for duplicates,
    safely copying files, and generating file hashes for verification.
    """

    @staticmethod
    def scan_camera_files() -> Dict[str, List[Path]]:
        """
        Scan the camera directory for files and categorize them by extension.

        Returns:
            Dict[str, List[Path]]: Dictionary with extensions as keys and lists of file paths as values.
        """
        result = {ext: [] for ext in EXTENSIONS}

        if not CAMERA_PATH.exists():
            return result

        for file_path in CAMERA_PATH.glob('*'):
            # Skip macOS resource fork files and other system files
            if not is_valid_image_file(file_path):
                continue

            ext = file_path.suffix.upper()
            if ext in EXTENSIONS:
                result[ext].append(file_path)

        return result

    @staticmethod
    def is_duplicate(src: Path, dst: Path) -> bool:
        """
        Check if a file is a duplicate by comparing file hashes.

        Args:
            src (Path): Source file path
            dst (Path): Destination file path

        Returns:
            bool: True if files are identical, False otherwise
        """
        if not dst.exists():
            return False

        src_hash = FileManager.get_file_hash(src)
        dst_hash = FileManager.get_file_hash(dst)

        return src_hash == dst_hash

    @staticmethod
    def safe_copy(src: Path, dst: Path) -> bool:
        """
        Safely copy a file, preserving metadata.
        First checks if the destination file already exists and is identical to the source.

        Args:
            src (Path): Source file path
            dst (Path): Destination file path

        Returns:
            bool: True if copy was successful or file already exists, False otherwise
        """
        try:
            # Create destination directory if it doesn't exist
            dst.parent.mkdir(parents=True, exist_ok=True)

            # Check if destination file already exists and is identical
            if dst.exists() and FileManager.is_duplicate(src, dst):
                # File already exists and is identical, no need to copy
                return True

            # Copy file with metadata
            shutil.copy2(src, dst)

            # Verify copy was successful
            return FileManager.is_duplicate(src, dst)
        except Exception:
            return False

    @staticmethod
    def get_file_hash(file_path: Path) -> str:
        """
        Generate a hash for a file.

        Args:
            file_path (Path): Path to the file

        Returns:
            str: Hexadecimal hash string
        """
        hash_md5 = hashlib.md5()

        try:
            with open(file_path, "rb") as f:
                for chunk in iter(lambda: f.read(4096), b""):
                    hash_md5.update(chunk)
            return hash_md5.hexdigest()
        except Exception:
            return ""
