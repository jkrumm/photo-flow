"""
Metadata extraction utilities for the Photo-Flow application.

This module provides functionality for extracting comprehensive metadata from images,
including XMP, EXIF, and file information.
"""

from pathlib import Path
from datetime import datetime
from typing import Dict, Any, Optional, Union
import os
import json

from PIL import Image
import piexif

from photo_flow.file_manager import is_valid_image_file

class MetadataExtractor:
    """
    Handles metadata extraction for the Photo-Flow application.

    This class provides methods for extracting comprehensive metadata from images,
    including XMP, EXIF, and file information.
    """

    @staticmethod
    def extract_metadata(image_path: Path) -> Dict[str, Any]:
        """
        Extract comprehensive metadata from an image.

        Args:
            image_path (Path): Path to the image file

        Returns:
            Dict[str, Any]: Dictionary containing all extracted metadata
        """
        metadata = {
            "filename": image_path.name,
            "file_size": os.path.getsize(image_path),
        }

        # Skip macOS resource fork files and other system files
        if not is_valid_image_file(image_path):
            # Set default values for required fields
            metadata["rating"] = 0
            metadata["title"] = ""
            metadata["description"] = ""
            return metadata

        try:
            # Open the image
            img = Image.open(image_path)

            # Add image dimensions
            metadata["dimensions"] = f"{img.width}x{img.height}"

            # Extract XMP metadata
            xmp_data = MetadataExtractor._extract_xmp_metadata(img)
            if xmp_data:
                metadata.update(xmp_data)

            # Extract EXIF metadata
            exif_data = MetadataExtractor._extract_exif_metadata(img, image_path)
            if exif_data:
                metadata.update(exif_data)

        except Exception as e:
            # Only print errors for files that should be valid images
            if is_valid_image_file(image_path):
                print(f"Error extracting metadata from {image_path}: {e}")

        return metadata

    @staticmethod
    def _extract_xmp_metadata(img: Image.Image) -> Dict[str, Any]:
        """
        Extract XMP metadata from an image.

        Args:
            img (Image.Image): PIL Image object

        Returns:
            Dict[str, Any]: Dictionary containing XMP metadata
        """
        metadata = {}

        try:
            # Get XMP data using Pillow's getxmp method
            xmp_data = img.getxmp()

            if xmp_data:
                # Extract Adobe Bridge Rating (0-5 stars)
                rating = None

                # Check if there's a direct Description object with Rating
                if 'xmpmeta' in xmp_data and 'RDF' in xmp_data['xmpmeta'] and 'Description' in xmp_data['xmpmeta']['RDF']:
                    description = xmp_data['xmpmeta']['RDF']['Description']

                    # Description can be either a dict or a list of dicts
                    descriptions_to_check = [description] if isinstance(description, dict) else description

                    # Search through all description blocks for Rating
                    for desc_block in descriptions_to_check:
                        if isinstance(desc_block, dict) and 'Rating' in desc_block:
                            try:
                                rating = int(desc_block['Rating'])
                                break
                            except (ValueError, TypeError):
                                continue

                # If not found, try the old method with different namespaces
                if rating is None:
                    for namespace in ['xmp', 'http://ns.adobe.com/xap/1.0/']:
                        if namespace in xmp_data and 'Rating' in xmp_data[namespace]:
                            try:
                                rating = int(xmp_data[namespace]['Rating'])
                                break
                            except (ValueError, TypeError):
                                continue

                # Set the rating (0 if not found or invalid)
                metadata["rating"] = rating if rating is not None else 0

                # Extract title and description from the Description object
                if 'xmpmeta' in xmp_data and 'RDF' in xmp_data['xmpmeta'] and 'Description' in xmp_data['xmpmeta']['RDF']:
                    description = xmp_data['xmpmeta']['RDF']['Description']

                    # Description can be either a dict or a list of dicts
                    descriptions_to_check = [description] if isinstance(description, dict) else description

                    # Search through all description blocks for title and description
                    for desc_block in descriptions_to_check:
                        if not isinstance(desc_block, dict):
                            continue

                        # Extract title
                        if 'title' in desc_block and "title" not in metadata:
                            title = desc_block['title']
                            if isinstance(title, dict) and 'x-default' in title:
                                metadata["title"] = title['x-default']
                            else:
                                metadata["title"] = str(title)

                        # Extract description
                        if 'description' in desc_block and "description" not in metadata:
                            desc = desc_block['description']
                            if isinstance(desc, dict) and 'x-default' in desc:
                                metadata["description"] = desc['x-default']
                            else:
                                metadata["description"] = str(desc)

            # If title/description not found in Description, try the old method
            if "title" not in metadata or "description" not in metadata:
                for namespace in ['dc', 'http://purl.org/dc/elements/1.1/']:
                    if namespace in xmp_data:
                        if 'title' in xmp_data[namespace] and "title" not in metadata:
                            title = xmp_data[namespace]['title']
                            if isinstance(title, dict) and 'x-default' in title:
                                metadata["title"] = title['x-default']
                            else:
                                metadata["title"] = str(title)

                        if 'description' in xmp_data[namespace] and "description" not in metadata:
                            desc = xmp_data[namespace]['description']
                            if isinstance(desc, dict) and 'x-default' in desc:
                                metadata["description"] = desc['x-default']
                            else:
                                metadata["description"] = str(desc)

        except Exception as e:
            print(f"Error extracting XMP metadata: {e}")
            # Set default values for required fields
            metadata["rating"] = 0

        # Ensure title and description have defaults if not found
        if "title" not in metadata:
            metadata["title"] = ""
        if "description" not in metadata:
            metadata["description"] = ""

        return metadata

    @staticmethod
    def _extract_exif_metadata(img: Image.Image, image_path: Path) -> Dict[str, Any]:
        """
        Extract EXIF metadata from an image.

        Args:
            img (Image.Image): PIL Image object
            image_path (Path): Path to the image file

        Returns:
            Dict[str, Any]: Dictionary containing EXIF metadata
        """
        metadata = {}

        try:
            # Try to get EXIF data
            exif_data = None
            if 'exif' in img.info:
                exif_data = piexif.load(img.info['exif'])
            else:
                try:
                    exif_data = piexif.load(str(image_path))
                except:
                    pass

            if exif_data:
                # Extract camera information
                if piexif.ImageIFD.Make in exif_data.get('0th', {}):
                    metadata["camera_make"] = exif_data['0th'][piexif.ImageIFD.Make].decode('utf-8', errors='ignore').strip('\x00')

                if piexif.ImageIFD.Model in exif_data.get('0th', {}):
                    metadata["camera_model"] = exif_data['0th'][piexif.ImageIFD.Model].decode('utf-8', errors='ignore').strip('\x00')

                # Extract camera settings
                if piexif.ExifIFD.ISOSpeedRatings in exif_data.get('Exif', {}):
                    metadata["iso"] = exif_data['Exif'][piexif.ExifIFD.ISOSpeedRatings]

                if piexif.ExifIFD.FNumber in exif_data.get('Exif', {}):
                    fnumber = exif_data['Exif'][piexif.ExifIFD.FNumber]
                    if isinstance(fnumber, tuple) and len(fnumber) == 2 and fnumber[1] != 0:
                        aperture = fnumber[0] / fnumber[1]
                        metadata["aperture"] = f"f/{aperture:.1f}"

                if piexif.ExifIFD.ExposureTime in exif_data.get('Exif', {}):
                    exposure = exif_data['Exif'][piexif.ExifIFD.ExposureTime]
                    if isinstance(exposure, tuple) and len(exposure) == 2 and exposure[1] != 0:
                        if exposure[0] >= exposure[1]:
                            shutter = f"{exposure[0] / exposure[1]:.1f}"
                        else:
                            shutter = f"1/{exposure[1] / exposure[0]:.0f}"
                        metadata["shutter_speed"] = shutter

                if piexif.ExifIFD.FocalLength in exif_data.get('Exif', {}):
                    focal = exif_data['Exif'][piexif.ExifIFD.FocalLength]
                    if isinstance(focal, tuple) and len(focal) == 2 and focal[1] != 0:
                        metadata["focal_length"] = f"{focal[0] / focal[1]:.0f}mm"

                # Extract GPS information
                if 'GPS' in exif_data and exif_data['GPS']:
                    lat = MetadataExtractor._convert_gps_coords(
                        exif_data['GPS'].get(piexif.GPSIFD.GPSLatitude),
                        exif_data['GPS'].get(piexif.GPSIFD.GPSLatitudeRef)
                    )

                    lon = MetadataExtractor._convert_gps_coords(
                        exif_data['GPS'].get(piexif.GPSIFD.GPSLongitude),
                        exif_data['GPS'].get(piexif.GPSIFD.GPSLongitudeRef)
                    )

                    if lat is not None and lon is not None:
                        metadata["latitude"] = lat
                        metadata["longitude"] = lon

                # Extract date/time
                if piexif.ExifIFD.DateTimeOriginal in exif_data.get('Exif', {}):
                    date_str = exif_data['Exif'][piexif.ExifIFD.DateTimeOriginal].decode('utf-8', errors='ignore')
                    try:
                        # Convert from "YYYY:MM:DD HH:MM:SS" to ISO format
                        date_obj = datetime.strptime(date_str, '%Y:%m:%d %H:%M:%S')
                        metadata["date_taken"] = date_obj.isoformat() + 'Z'
                    except:
                        # If parsing fails, store the original string
                        metadata["date_taken"] = date_str

        except Exception as e:
            print(f"Error extracting EXIF metadata: {e}")

        return metadata

    @staticmethod
    def _convert_gps_coords(coords_tuple, ref) -> Optional[float]:
        """
        Convert GPS coordinates from EXIF format to decimal degrees.

        Args:
            coords_tuple: Tuple of GPS coordinates (degrees, minutes, seconds)
            ref: Reference (N, S, E, W)

        Returns:
            Optional[float]: Decimal degrees or None if conversion fails
        """
        if not coords_tuple or not ref:
            return None

        try:
            degrees = coords_tuple[0][0] / coords_tuple[0][1]
            minutes = coords_tuple[1][0] / coords_tuple[1][1]
            seconds = coords_tuple[2][0] / coords_tuple[2][1]

            decimal = degrees + minutes / 60.0 + seconds / 3600.0

            # If south or west, negate the value
            if ref.decode('utf-8') in ['S', 'W']:
                decimal = -decimal

            return round(decimal, 6)
        except:
            return None

    @staticmethod
    def generate_metadata_json(images_metadata: list, output_path: Path) -> bool:
        """
        Generate a JSON file containing metadata for all images.

        Args:
            images_metadata (list): List of metadata dictionaries for each image
            output_path (Path): Path to save the JSON file

        Returns:
            bool: True if successful, False otherwise
        """
        try:
            metadata_json = {
                "generated_at": datetime.utcnow().isoformat() + "Z",
                "total_images": len(images_metadata),
                "images": images_metadata
            }

            # Create parent directory if it doesn't exist
            output_path.parent.mkdir(parents=True, exist_ok=True)

            # Write JSON to file
            with open(output_path, 'w', encoding='utf-8') as f:
                json.dump(metadata_json, f, indent=2)

            return True
        except Exception as e:
            print(f"Error generating metadata JSON: {e}")
            return False