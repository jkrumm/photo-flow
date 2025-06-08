"""
Workflow management for the Photo-Flow application.

This module provides the main workflow logic for the application.
"""

from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List

from photo_flow.config import CAMERA_PATH, STAGING_PATH, RAWS_PATH, FINAL_PATH, SSD_PATH
from photo_flow.file_manager import FileManager
from photo_flow.image_processor import ImageProcessor


@dataclass
class StatusReport:
    """Data class for storing workflow status information."""
    camera_connected: bool = False
    ssd_connected: bool = False
    pending_videos: int = 0
    pending_photos: int = 0
    pending_raws: int = 0
    staging_files: int = 0


class PhotoWorkflow:
    """
    Manages the photo workflow process.

    This class provides methods for importing files from the camera,
    finalizing the staging process, cleaning up unused RAW files,
    and checking the current status.
    """

    def __init__(self):
        """Initialize the PhotoWorkflow instance."""
        self.file_manager = FileManager()
        self.image_processor = ImageProcessor()

    def import_from_camera(self, dry_run: bool = False, progress_callback=None) -> Dict[str, int]:
        """
        Import files from the camera to the appropriate locations.

        Args:
            dry_run (bool): If True, only simulate the import without copying files
            progress_callback (callable): Optional callback function for progress updates

        Returns:
            Dict[str, int]: Statistics about the import operation
        """
        stats = {
            'videos': 0,
            'photos': 0,
            'raws': 0,
            'errors': 0
        }

        # Scan camera for files
        if progress_callback:
            progress_callback("Scanning camera for files...")
        files = self.file_manager.scan_camera_files()

        # Count files that would be imported
        mov_count = len(files.get('.MOV', []))
        jpg_count = len(files.get('.JPG', []))
        raf_count = len(files.get('.RAF', []))

        # Set initial stats for dry run mode
        stats['videos'] = mov_count
        stats['photos'] = jpg_count
        stats['raws'] = raf_count

        if progress_callback:
            progress_callback(f"Found {mov_count} videos, {jpg_count} photos, and {raf_count} RAW files")

        # If not dry run, actually import the files
        if not dry_run:
            # Reset counters for actual import
            stats['videos'] = 0
            stats['photos'] = 0
            stats['raws'] = 0

            # Process MOV files (copy to SSD)
            mov_files = files.get('.MOV', [])
            if mov_files and progress_callback:
                progress_callback(f"Copying {len(mov_files)} videos to SSD...")

            for i, mov_file in enumerate(mov_files):
                if progress_callback and i % 5 == 0:  # Update every 5 files to avoid too many updates
                    progress_callback(f"Copying video {i+1}/{len(mov_files)}: {mov_file.name}")

                dst_path = SSD_PATH / mov_file.name
                if self.file_manager.safe_copy(mov_file, dst_path):
                    # Only increment counter if copy was successful
                    stats['videos'] += 1
                    # Delete original file after successful copy
                    try:
                        mov_file.unlink()
                    except Exception:
                        stats['errors'] += 1
                else:
                    stats['errors'] += 1

            # Process JPG files (copy to Staging)
            jpg_files = files.get('.JPG', [])
            if jpg_files and progress_callback:
                progress_callback(f"Copying {len(jpg_files)} photos to Staging...")

            for i, jpg_file in enumerate(jpg_files):
                if progress_callback and i % 10 == 0:  # Update every 10 files
                    progress_callback(f"Copying photo {i+1}/{len(jpg_files)}: {jpg_file.name}")

                dst_path = STAGING_PATH / jpg_file.name

                # First copy the file
                if self.file_manager.safe_copy(jpg_file, dst_path):
                    # Skip applying clarity effect for now
                    stats['photos'] += 1
                    # Delete original file after successful copy
                    try:
                        jpg_file.unlink()
                    except Exception:
                        stats['errors'] += 1
                else:
                    stats['errors'] += 1

            # Process RAF files (copy to RAWs)
            raf_files = files.get('.RAF', [])
            if raf_files and progress_callback:
                progress_callback(f"Copying {len(raf_files)} RAW files to backup...")

            for i, raf_file in enumerate(raf_files):
                if progress_callback and i % 10 == 0:  # Update every 10 files
                    progress_callback(f"Copying RAW file {i+1}/{len(raf_files)}: {raf_file.name}")

                dst_path = RAWS_PATH / raf_file.name
                if self.file_manager.safe_copy(raf_file, dst_path):
                    stats['raws'] += 1
                    # Delete original file after successful copy
                    try:
                        raf_file.unlink()
                    except Exception:
                        stats['errors'] += 1
                else:
                    stats['errors'] += 1

            if progress_callback:
                progress_callback("Import completed successfully!")

        return stats

    def finalize_staging(self, dry_run: bool = False, progress_callback=None) -> Dict[str, int]:
        """
        Finalize the staging process by moving approved photos to the final folder,
        copying them back to the camera for viewing, and cleaning up orphaned RAW files.

        Args:
            dry_run (bool): If True, only simulate the finalization without moving files
            progress_callback (callable): Optional callback function for progress updates

        Returns:
            Dict[str, int]: Statistics about the finalization operation
        """
        stats = {
            'moved': 0,
            'copied_to_camera': 0,
            'orphaned_raws': 0,
            'deleted_raws': 0,
            'errors': 0
        }

        # Scan staging folder for JPG files
        if progress_callback:
            progress_callback("Scanning staging folder for photos...")

        if not STAGING_PATH.exists():
            if progress_callback:
                progress_callback("Staging folder not found. Nothing to finalize.")
            return stats

        staging_files = list(STAGING_PATH.glob('*.JPG'))

        # Count files that would be moved
        stats['moved'] = len(staging_files)
        stats['copied_to_camera'] = len(staging_files)

        if progress_callback:
            progress_callback(f"Found {len(staging_files)} photos in staging")

        # If not dry run, actually move the files
        if not dry_run:
            # Reset counters for actual operations
            stats['moved'] = 0
            stats['copied_to_camera'] = 0

            # Create final directory if it doesn't exist
            FINAL_PATH.mkdir(parents=True, exist_ok=True)

            # Process each file in staging
            if staging_files and progress_callback:
                progress_callback(f"Moving {len(staging_files)} photos to final folder...")

            for i, jpg_file in enumerate(staging_files):
                if progress_callback and i % 5 == 0:  # Update every 5 files
                    progress_callback(f"Moving photo {i+1}/{len(staging_files)}: {jpg_file.name}")

                dst_path = FINAL_PATH / jpg_file.name

                # First copy the file to final folder
                if self.file_manager.safe_copy(jpg_file, dst_path):
                    # Only increment counter if copy was successful
                    stats['moved'] += 1
                    # Delete original file after successful copy
                    try:
                        jpg_file.unlink()
                    except Exception:
                        stats['errors'] += 1
                else:
                    stats['errors'] += 1

            # After moving all files to final, copy them back to camera
            if CAMERA_PATH.exists():
                final_files = list(FINAL_PATH.glob('*.JPG'))
                if final_files and progress_callback:
                    progress_callback(f"Copying {len(final_files)} photos back to camera...")

                for i, jpg_file in enumerate(final_files):
                    if progress_callback and i % 5 == 0:  # Update every 5 files
                        progress_callback(f"Copying photo {i+1}/{len(final_files)} to camera: {jpg_file.name}")

                    camera_dst_path = CAMERA_PATH / jpg_file.name
                    if self.file_manager.safe_copy(jpg_file, camera_dst_path):
                        stats['copied_to_camera'] += 1
                    else:
                        stats['errors'] += 1
            elif progress_callback:
                progress_callback("Camera not connected. Skipping copy back to camera.")

            # Clean up orphaned RAW files
            if progress_callback:
                progress_callback("Checking for orphaned RAW files...")

            if RAWS_PATH.exists() and FINAL_PATH.exists():
                # Get all JPGs in the final folder
                final_jpgs = list(FINAL_PATH.glob('*.JPG'))

                # Extract base filenames (without extension) from final JPGs
                final_jpg_bases = {jpg_file.stem for jpg_file in final_jpgs}

                # Get all RAFs in the RAWs folder
                raw_files = list(RAWS_PATH.glob('*.RAF'))

                # Find orphaned RAWs (those without a corresponding JPG in final)
                orphaned_raws = [raw_file for raw_file in raw_files 
                                if raw_file.stem not in final_jpg_bases]

                # Count orphaned RAWs
                stats['orphaned_raws'] = len(orphaned_raws)

                if progress_callback:
                    progress_callback(f"Found {len(orphaned_raws)} orphaned RAW files")

                # If not dry run, delete the orphaned RAWs
                if not dry_run and orphaned_raws:
                    if progress_callback:
                        progress_callback(f"Deleting {len(orphaned_raws)} orphaned RAW files...")

                    for i, raw_file in enumerate(orphaned_raws):
                        if progress_callback and i % 10 == 0:  # Update every 10 files
                            progress_callback(f"Deleting RAW file {i+1}/{len(orphaned_raws)}: {raw_file.name}")

                        try:
                            raw_file.unlink()
                            stats['deleted_raws'] += 1
                        except Exception:
                            stats['errors'] += 1

            if progress_callback:
                progress_callback("Finalization completed successfully!")

        return stats

    def cleanup_unused_raws(self, dry_run: bool = False) -> Dict[str, int]:
        """
        Clean up unused RAW files that don't have corresponding JPGs in the final folder.

        Args:
            dry_run (bool): If True, only simulate the cleanup without deleting files

        Returns:
            Dict[str, int]: Statistics about the cleanup operation
        """
        stats = {
            'orphaned': 0,
            'deleted': 0,
            'errors': 0
        }

        # Check if RAWs and Final folders exist
        if not RAWS_PATH.exists() or not FINAL_PATH.exists():
            return stats

        # Get all JPGs in the final folder
        final_jpgs = list(FINAL_PATH.glob('*.JPG'))

        # Extract base filenames (without extension) from final JPGs
        final_jpg_bases = {jpg_file.stem for jpg_file in final_jpgs}

        # Get all RAFs in the RAWs folder
        raw_files = list(RAWS_PATH.glob('*.RAF'))

        # Find orphaned RAWs (those without a corresponding JPG in final)
        orphaned_raws = [raw_file for raw_file in raw_files 
                         if raw_file.stem not in final_jpg_bases]

        # Count orphaned RAWs
        stats['orphaned'] = len(orphaned_raws)

        # If not dry run, delete the orphaned RAWs
        if not dry_run and orphaned_raws:
            for raw_file in orphaned_raws:
                try:
                    raw_file.unlink()
                    stats['deleted'] += 1
                except Exception:
                    stats['errors'] += 1

        return stats

    def get_status(self) -> StatusReport:
        """
        Get the current status of the workflow.

        Returns:
            StatusReport: A report containing the current status
        """
        report = StatusReport()

        # Check if camera is connected
        report.camera_connected = CAMERA_PATH.exists()

        # Check if SSD is connected
        report.ssd_connected = SSD_PATH.exists()

        # Count files in staging
        if STAGING_PATH.exists():
            report.staging_files = len(list(STAGING_PATH.glob('*.JPG')))

        # Count pending files on camera (if connected)
        if report.camera_connected:
            files = self.file_manager.scan_camera_files()
            report.pending_videos = len(files.get('.MOV', []))
            report.pending_photos = len(files.get('.JPG', []))
            report.pending_raws = len(files.get('.RAF', []))

        return report
