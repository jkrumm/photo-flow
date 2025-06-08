"""
Image processing utilities for the Photo-Flow application.

This module provides functionality for applying effects to images and preserving metadata.
"""

from pathlib import Path
from PIL import Image, ImageEnhance
import piexif

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