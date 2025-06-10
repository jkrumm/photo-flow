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

    def _process_files(self, files: List[Path], destination: Path,
                       file_type: str, progress_callback=None, dry_run=False,
                       delete_original=True) -> Dict[str, int]:
        """
        Generic method to process a list of files with consistent progress reporting and error handling.

        Args:
            files: List of source files to process
            destination: Destination directory
            file_type: Human-readable file type for progress messages
            progress_callback: Optional callback for progress updates
            dry_run: If True, only count files without copying
            delete_original: If True, delete source file after successful copy

        Returns:
            Dict with 'processed', 'skipped', 'errors' counts
        """
        stats = {'processed': 0, 'skipped': 0, 'errors': 0}

        for i, file_path in enumerate(files):
            if progress_callback and i % 10 == 0:
                progress_callback(f"Processing {file_type} {i + 1}/{len(files)}: {file_path.name}")

            if dry_run:
                stats['processed'] += 1
                continue

            dst_path = destination / file_path.name

            # Check if destination already exists and is identical
            if dst_path.exists() and self.file_manager.is_duplicate(file_path, dst_path):
                stats['skipped'] += 1
                if progress_callback:
                    progress_callback(f"Skipping {file_type} {file_path.name} (already exists)")
            elif self.file_manager.safe_copy(file_path, dst_path):
                stats['processed'] += 1
                if delete_original:
                    try:
                        file_path.unlink()
                    except Exception:
                        stats['errors'] += 1
            else:
                stats['errors'] += 1

        return stats

    def _merge_stats(self, *stat_dicts) -> Dict[str, int]:
        """Merge multiple statistics dictionaries."""
        merged = {'processed': 0, 'skipped': 0, 'errors': 0}
        for stats in stat_dicts:
            for key in merged:
                if key in stats:
                    merged[key] += stats[key]
        return merged

    def import_from_camera(self, dry_run: bool = False, progress_callback=None) -> Dict[str, int]:
        """
        Import files from the camera to the appropriate locations.
        Excludes files that are already in the Final folder to avoid re-staging finalized photos.
        """
        # Scan camera for files
        if progress_callback:
            progress_callback("Scanning camera for files...")
        files = self.file_manager.scan_camera_files()

        # Get finalized files to exclude from staging (keep this pre-filtering!)
        final_jpg_bases = set()
        if FINAL_PATH.exists():
            if progress_callback:
                progress_callback("Checking for files already in Final folder...")
            final_jpgs = list(FINAL_PATH.glob('*.JPG'))
            final_jpg_bases = {jpg_file.stem for jpg_file in final_jpgs}

        # Filter out finalized files
        jpg_files = [jpg for jpg in files.get('.JPG', []) if jpg.stem not in final_jpg_bases]
        raf_files = [raf for raf in files.get('.RAF', []) if raf.stem not in final_jpg_bases]
        mov_files = files.get('.MOV', [])

        # Count skipped finalized files
        skipped_finalized = (len(files.get('.JPG', [])) - len(jpg_files) +
                             len(files.get('.RAF', [])) - len(raf_files))

        # Calculate total files for progress tracking
        total_files = len(mov_files) + len(jpg_files) + len(raf_files)
        files_processed = 0

        if progress_callback:
            progress_callback(f"Found {len(mov_files)} videos, {len(jpg_files)} photos, {len(raf_files)} RAW files")
            if skipped_finalized > 0:
                progress_callback(f"Skipping {skipped_finalized} files already in Final folder")
            if total_files > 0:
                progress_callback(f"Starting import of {total_files} files (0% complete)")

        # Create a wrapper for the progress callback to show overall progress
        def progress_wrapper(message):
            nonlocal files_processed
            if progress_callback:
                if "Processing" in message and ":" in message:
                    # Only increment once per file, not per message
                    if files_processed < total_files:
                        files_processed += 1
                        percent = int((files_processed / total_files) * 100)
                        progress_callback(f"{message} ({percent}% complete)")
                else:
                    progress_callback(message)

        # Process each file type
        if progress_callback and mov_files:
            progress_callback(f"Processing {len(mov_files)} videos...")
        mov_stats = self._process_files(mov_files, SSD_PATH, "video",
                                        progress_wrapper if not dry_run else progress_callback, dry_run)

        if progress_callback and jpg_files:
            progress_callback(f"Processing {len(jpg_files)} photos...")
        jpg_stats = self._process_files(jpg_files, STAGING_PATH, "photo",
                                        progress_wrapper if not dry_run else progress_callback, dry_run)

        if progress_callback and raf_files:
            progress_callback(f"Processing {len(raf_files)} RAW files...")
        raf_stats = self._process_files(raf_files, RAWS_PATH, "RAW file",
                                        progress_wrapper if not dry_run else progress_callback, dry_run)

        if progress_callback and not dry_run:
            progress_callback("Import completed successfully!")

        return {
            'videos': mov_stats['processed'],
            'photos': jpg_stats['processed'],
            'raws': raf_stats['processed'],
            'skipped': mov_stats['skipped'] + jpg_stats['skipped'] + raf_stats['skipped'] + skipped_finalized,
            'errors': mov_stats['errors'] + jpg_stats['errors'] + raf_stats['errors']
        }

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
            'skipped': 0,
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

        if progress_callback:
            progress_callback(f"Found {len(staging_files)} photos in staging")

        # Create final directory if it doesn't exist
        if not dry_run:
            FINAL_PATH.mkdir(parents=True, exist_ok=True)

        # Calculate total operations for progress tracking
        total_operations = len(staging_files)

        # Add camera copy operations if camera is connected
        final_files = []
        if CAMERA_PATH.exists():
            final_files = list(FINAL_PATH.glob('*.JPG'))
            total_operations += len(final_files)

        # Initialize progress counter
        operations_completed = 0

        if progress_callback and total_operations > 0:
            progress_callback(f"Starting finalization of {total_operations} operations (0% complete)")

        # Create a wrapper for the progress callback to show overall progress
        def progress_wrapper(message):
            nonlocal operations_completed
            if progress_callback:
                if "Processing" in message and ":" in message:
                    # Only increment if we haven't reached total operations
                    if operations_completed < total_operations:
                        operations_completed += 1
                        percent_complete = int((operations_completed / total_operations) * 100)
                        progress_callback(f"{message} ({percent_complete}% complete)")
                    else:
                        progress_callback(message)
                else:
                    progress_callback(message)

        # Process each file in staging
        if staging_files and progress_callback:
            progress_callback(f"Moving {len(staging_files)} photos to final folder...")

        staging_stats = self._process_files(staging_files, FINAL_PATH, "photo",
                                            progress_wrapper if not dry_run else progress_callback, dry_run)
        stats['moved'] = staging_stats['processed']
        stats['skipped'] += staging_stats['skipped']
        stats['errors'] += staging_stats['errors']

        # After moving all files to final, copy them back to camera
        if CAMERA_PATH.exists():
            # Get final files AFTER moving staging files
            final_files = list(FINAL_PATH.glob('*.JPG'))  # Move this line here
            if final_files and progress_callback:
                progress_callback(f"Copying {len(final_files)} photos back to camera...")

            camera_stats = self._process_files(final_files, CAMERA_PATH, "photo",
                                               progress_wrapper if not dry_run else progress_callback,
                                               dry_run, delete_original=False)
            stats['copied_to_camera'] = camera_stats['processed']
            stats['skipped'] += camera_stats['skipped']
            stats['errors'] += camera_stats['errors']
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

            if not dry_run and orphaned_raws:
                if progress_callback:
                    progress_callback(f"Deleting {len(orphaned_raws)} orphaned RAW files...")

                # Create a dummy destination - not actually used for deletions
                dummy_dest = RAWS_PATH

                # Process orphaned RAWs with the generic method
                # The _process_files method will call unlink() on the original files
                orphaned_stats = self._process_files(orphaned_raws, dummy_dest, "orphaned RAW",
                                                     progress_wrapper if not dry_run else progress_callback,
                                                     dry_run)

                stats['deleted_raws'] = orphaned_stats['processed']
                stats['errors'] += orphaned_stats['errors']

        if progress_callback:
            progress_callback("Finalization completed successfully!")

        return stats

    def cleanup_unused_raws(self, dry_run: bool = False, progress_callback=None) -> Dict[str, int]:
        """
        Clean up unused RAW files that don't have corresponding JPGs in the final folder.

        Args:
            dry_run (bool): If True, only simulate the cleanup without deleting files
            progress_callback (callable): Optional callback function for progress updates

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

        if progress_callback:
            progress_callback("Scanning for orphaned RAW files...")

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

        if progress_callback:
            progress_callback(f"Found {len(orphaned_raws)} orphaned RAW files")

        # If not dry run, delete the orphaned RAWs
        if not dry_run and orphaned_raws:
            if progress_callback:
                progress_callback(f"Deleting {len(orphaned_raws)} orphaned RAW files...")

            # Use the generic process_files method for RAW deletion
            # Create a dummy destination path (not actually used)
            dummy_dest = RAWS_PATH

            orphaned_stats = self._process_files(orphaned_raws, dummy_dest, "orphaned RAW",
                                                 progress_callback, dry_run)

            stats['deleted'] = orphaned_stats['processed']
            stats['errors'] = orphaned_stats['errors']

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

            # For JPGs, we need to exclude those that are already in the Final folder
            # (these are the ones that were copied back to the camera during finalization)
            camera_jpgs = files.get('.JPG', [])

            # Get a list of JPGs in the Final folder (if it exists)
            final_jpg_bases = set()
            if FINAL_PATH.exists():
                final_jpgs = list(FINAL_PATH.glob('*.JPG'))
                final_jpg_bases = {jpg_file.stem for jpg_file in final_jpgs}

            # Filter out JPGs that are already in the Final folder
            pending_jpgs = [jpg for jpg in camera_jpgs if jpg.stem not in final_jpg_bases]
            report.pending_photos = len(pending_jpgs)

            report.pending_raws = len(files.get('.RAF', []))

        return report
