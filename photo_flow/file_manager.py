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
    # Class-level cache for file hashes to avoid recomputing
    _hash_cache = {}

    @staticmethod
    def scan_camera_files() -> Dict[str, List[Path]]:
        """
        Scan all folders in the camera DCIM directory for files and categorize them by extension.

        Returns:
            Dict[str, List[Path]]: Dictionary with extensions as keys and lists of file paths as values.
        """
        result = {ext: [] for ext in EXTENSIONS}

        if not CAMERA_PATH.exists():
            return result

        # Look for all folders in the DCIM directory (like 102_FUJI, 103_FUJI, etc.)
        for folder in CAMERA_PATH.glob('*_*'):
            if not folder.is_dir():
                continue

            # Scan each folder for files
            for file_path in folder.glob('*'):
                # Skip macOS resource fork files and other system files
                if not is_valid_image_file(file_path):
                    continue

                ext = file_path.suffix.upper()
                if ext in EXTENSIONS:
                    result[ext].append(file_path)

        return result

    @classmethod
    def is_duplicate(cls, src: Path, dst: Path) -> tuple[bool, str]:
        """
        Check if a file is a duplicate by comparing file sizes first, then hashes if needed.

        Args:
            src (Path): Source file path
            dst (Path): Destination file path

        Returns:
            tuple[bool, str]: (is_duplicate, error_message) - is_duplicate is True if files are identical,
                             False otherwise. error_message contains details if an error occurred,
                             empty string otherwise.
        """
        if not dst.exists():
            return False, ""

        # Quick check: if file sizes differ, files cannot be identical
        try:
            if src.stat().st_size != dst.stat().st_size:
                return False, ""
        except Exception as e:
            return False, f"Error comparing file sizes: {str(e)}"

        # If file sizes match, compare hashes for definitive check
        src_hash, src_error = cls.get_file_hash(src)
        if src_error:
            return False, src_error

        dst_hash, dst_error = cls.get_file_hash(dst)
        if dst_error:
            return False, dst_error

        return src_hash == dst_hash, ""

    @classmethod
    def safe_copy(cls, src: Path, dst: Path) -> tuple[bool, str]:
        """
        Safely copy a file, preserving metadata.
        First checks if the destination file already exists and is identical to the source.

        Args:
            src (Path): Source file path
            dst (Path): Destination file path

        Returns:
            tuple[bool, str]: (success, error_message) - success is True if copy was successful 
                             or file already exists, False otherwise. error_message contains 
                             details if an error occurred, empty string otherwise.
        """
        try:
            # Create destination directory if it doesn't exist
            dst.parent.mkdir(parents=True, exist_ok=True)

            # Check if destination file already exists and is identical
            if dst.exists():
                is_dup, error = cls.is_duplicate(src, dst)
                if error:
                    return False, error
                if is_dup:
                    # File already exists and is identical, no need to copy
                    return True, ""

            # Copy file with metadata
            shutil.copy2(src, dst)

            # Verify copy was successful
            is_dup, error = cls.is_duplicate(src, dst)
            if error:
                return False, error
            if is_dup:
                return True, ""
            else:
                return False, f"Verification failed: copied file does not match source for {src}"
        except Exception as e:
            return False, f"Error copying {src} to {dst}: {str(e)}"

    @classmethod
    def get_file_hash(cls, file_path: Path, partial: bool = True) -> tuple[str, str]:
        """
        Generate a hash for a file.

        When partial=True (default), only reads the first and last 1MB of the file
        for large files (>10MB), which is much faster but still reliable for
        detecting most differences.

        Args:
            file_path (Path): Path to the file
            partial (bool): Whether to use partial hashing for large files

        Returns:
            tuple[str, str]: (hash_string, error_message) - hash_string is the hexadecimal hash,
                            error_message contains details if an error occurred, empty string otherwise.
        """
        try:
            # Get file stats for cache key and size check
            file_stat = file_path.stat()
            file_size = file_stat.st_size

            # Create a cache key based on file path, size, modification time, and partial flag
            cache_key = (str(file_path), file_size, file_stat.st_mtime, partial)

            # Check if hash is in cache
            if cache_key in cls._hash_cache:
                return cls._hash_cache[cache_key], ""

            # Not in cache, compute hash
            hash_md5 = hashlib.md5()

            # For small files or when partial=False, hash the entire file
            if not partial or file_size <= 10 * 1024 * 1024:  # 10MB threshold
                with open(file_path, "rb") as f:
                    for chunk in iter(lambda: f.read(4096), b""):
                        hash_md5.update(chunk)
                result = hash_md5.hexdigest()
                cls._hash_cache[cache_key] = result
                return result, ""

            # For large files, hash only the first and last 1MB
            with open(file_path, "rb") as f:
                # Hash first 1MB
                for _ in range(256):  # 256 * 4KB = 1MB
                    chunk = f.read(4096)
                    if not chunk:
                        break
                    hash_md5.update(chunk)

                # Move to 1MB before the end of file
                f.seek(max(file_size - 1024 * 1024, 0))

                # Hash last 1MB
                for chunk in iter(lambda: f.read(4096), b""):
                    hash_md5.update(chunk)

            result = hash_md5.hexdigest()
            cls._hash_cache[cache_key] = result
            return result, ""
        except Exception as e:
            return "", f"Error generating hash for {file_path}: {str(e)}"
