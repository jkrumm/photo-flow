"""
Image processing utilities for the Photo-Flow application.

This module provides functionality for applying effects to images and preserving metadata.
"""

from pathlib import Path
from PIL import Image, ImageEnhance
import piexif
import subprocess
import tempfile
import shutil

from photo_flow.config import CLARITY_ADJUSTMENT


class ImageProcessor:
    """
    Handles image processing for the Photo-Flow application.
    
    This class provides methods for applying effects to images and preserving metadata.
    """
    
    @staticmethod
    def apply_clarity_effect(jpg_path: Path) -> Path:
        """
        Apply a clarity effect to a JPG image.
        
        Args:
            jpg_path (Path): Path to the JPG file
            
        Returns:
            Path: Path to the processed image
        """
        try:
            # Open the image
            img = Image.open(jpg_path)
            
            # Extract EXIF data before processing
            exif_data = None
            if 'exif' in img.info:
                exif_data = img.info['exif']
            
            # Apply clarity effect (using contrast enhancement as a simple approximation)
            enhancer = ImageEnhance.Contrast(img)
            processed_img = enhancer.enhance(1.0 + CLARITY_ADJUSTMENT / 10.0)
            
            # Create output path (same as input for now)
            output_path = jpg_path
            
            # Save processed image
            if exif_data:
                processed_img.save(output_path, 'JPEG', exif=exif_data)
            else:
                processed_img.save(output_path, 'JPEG')
            
            return output_path
        except Exception as e:
            print(f"Error processing image {jpg_path}: {e}")
            return jpg_path
    
    @staticmethod
    def preserve_exif(original: Path, processed: Path) -> None:
        """
        Copy EXIF data from original image to processed image.

        Args:
            original (Path): Path to the original image
            processed (Path): Path to the processed image
        """
        try:
            # Extract EXIF from original
            exif_dict = piexif.load(str(original))

            # Write EXIF to processed image
            exif_bytes = piexif.dump(exif_dict)
            piexif.insert(exif_bytes, str(processed))
        except Exception as e:
            print(f"Error preserving EXIF data: {e}")

    @staticmethod
    def compress_jpeg_safe(input_path: Path, output_path: Path = None, max_width: int = 5200,
                          max_height: int = 3467, quality: int = 92) -> tuple[bool, str]:
        """
        Safely compress and resize a JPEG image while preserving ALL metadata.

        SAFETY-FIRST APPROACH:
        1. Creates compressed version in temporary file
        2. Copies ALL metadata using exiftool
        3. Verifies compressed file integrity
        4. Only replaces original after successful verification
        5. Never leaves original in corrupted state

        Uses exiftool for metadata preservation to ensure zero metadata loss.
        Resizes to fit within max_width × max_height while maintaining aspect ratio.

        Args:
            input_path (Path): Path to input JPEG file
            output_path (Path): Path to save compressed file (defaults to overwriting input)
            max_width (int): Maximum width in pixels (default: 5200, 83% of X-T4 native)
            max_height (int): Maximum height in pixels (default: 3467, 83% of X-T4 native)
            quality (int): JPEG quality 1-100 (default: 92, optimal quality/size balance)

        Returns:
            tuple[bool, str]: (success, error_message)
        """
        if output_path is None:
            output_path = input_path

        # Check if exiftool is available
        if not shutil.which("exiftool"):
            return False, "exiftool not found. Install with: brew install exiftool"

        try:
            # Create temporary file for compressed version
            with tempfile.NamedTemporaryFile(suffix='.jpg', delete=False) as tmp:
                tmp_path = Path(tmp.name)

            # Open and process image
            with Image.open(input_path) as img:
                original_width, original_height = img.size

                # Calculate new dimensions maintaining aspect ratio
                aspect_ratio = original_width / original_height
                new_width, new_height = original_width, original_height

                if original_width > max_width or original_height > max_height:
                    # Image needs resizing
                    if aspect_ratio > (max_width / max_height):
                        # Width is the limiting factor
                        new_width = max_width
                        new_height = int(max_width / aspect_ratio)
                    else:
                        # Height is the limiting factor
                        new_height = max_height
                        new_width = int(max_height * aspect_ratio)

                    # Resize using high-quality Lanczos resampling
                    img = img.resize((new_width, new_height), Image.Resampling.LANCZOS)

                # Save compressed image to temporary file (without metadata initially)
                # Using subsampling=0 for 4:4:4 chroma (no color information loss)
                # Using progressive=True for better web loading and compression
                img.save(tmp_path, 'JPEG',
                        quality=quality,
                        optimize=True,
                        progressive=True,
                        subsampling=0)  # 4:4:4 chroma - preserves full color information

            # Use exiftool to copy ALL metadata from original to compressed version
            # This preserves EXIF, IPTC, XMP, and all other metadata types
            result = subprocess.run(
                ['exiftool', '-TagsFromFile', str(input_path), '-all:all',
                 '-overwrite_original', str(tmp_path)],
                capture_output=True,
                text=True
            )

            if result.returncode != 0:
                # Clean up temp file on metadata copy failure
                tmp_path.unlink(missing_ok=True)
                return False, f"exiftool failed to copy metadata: {result.stderr.strip()}"

            # Verify the compressed file can be opened (basic integrity check)
            try:
                with Image.open(tmp_path) as test_img:
                    test_img.verify()
            except Exception as e:
                tmp_path.unlink(missing_ok=True)
                return False, f"Compressed image verification failed: {e}"

            # SAFETY: Only replace original after everything succeeded
            # Create backup suffix in case something goes wrong during the replace
            backup_path = output_path.with_suffix(output_path.suffix + '.backup')

            try:
                # If we're overwriting the original, create a backup first
                if output_path == input_path:
                    shutil.copy2(input_path, backup_path)

                # Replace original with compressed version
                tmp_path.replace(output_path)

                # Remove backup if successful
                if backup_path.exists():
                    backup_path.unlink()

                return True, ""

            except Exception as e:
                # Restore from backup if replace failed
                if backup_path.exists() and output_path == input_path:
                    backup_path.replace(input_path)
                # Clean up temp file
                tmp_path.unlink(missing_ok=True)
                return False, f"Failed to replace original file: {e}"

        except Exception as e:
            # Clean up temp file on any other error
            if 'tmp_path' in locals():
                tmp_path.unlink(missing_ok=True)
            return False, f"Error compressing JPEG {input_path}: {e}"