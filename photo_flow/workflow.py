"""
Workflow management for the Photo-Flow application.

This module provides the main workflow logic for the application.
"""

from dataclasses import dataclass
from pathlib import Path
import subprocess
from typing import Dict, List

from photo_flow.config import CAMERA_PATH, STAGING_PATH, RAWS_PATH, FINAL_PATH, SSD_PATH, GALLERY_PATH
from photo_flow.file_manager import FileManager, is_valid_image_file, scan_for_images
from photo_flow.image_processor import ImageProcessor
from photo_flow.metadata_extractor import MetadataExtractor


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
            is_dup, error = self.file_manager.is_duplicate(file_path, dst_path)
            if error:
                stats['errors'] += 1
                if progress_callback:
                    progress_callback(f"ERROR: {error}")
                continue

            if dst_path.exists() and is_dup:
                stats['skipped'] += 1
                if progress_callback:
                    progress_callback(f"Skipping {file_type} {file_path.name} (already exists)")
            else:
                success, error = self.file_manager.safe_copy(file_path, dst_path)
                if success:
                    stats['processed'] += 1
                    if delete_original:
                        try:
                            file_path.unlink()
                        except Exception as e:
                            stats['errors'] += 1
                            if progress_callback:
                                progress_callback(f"ERROR: Failed to delete original file {file_path}: {str(e)}")
                else:
                    stats['errors'] += 1
                    if progress_callback:
                        progress_callback(f"ERROR: {error}")

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
            final_jpgs = scan_for_images(FINAL_PATH, '.JPG')
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
                                        progress_wrapper if not dry_run else progress_callback, 
                                        dry_run, delete_original=True)  # Explicitly set delete_original=True

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
            'deleted_camera_raws': 0,  # New field for tracking RAWs deleted from camera
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

        staging_files = scan_for_images(STAGING_PATH, '.JPG')

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
            final_files = scan_for_images(FINAL_PATH, '.JPG')
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
            final_files = scan_for_images(FINAL_PATH, '.JPG')  # Move this line here
            if final_files and progress_callback:
                progress_callback(f"Copying {len(final_files)} photos back to camera...")

            # Find the first available camera folder (e.g., 102_FUJI, 103_FUJI)
            camera_folders = [folder for folder in CAMERA_PATH.glob('*_*') if folder.is_dir()]

            if camera_folders:
                # Use the first folder found (could be sorted if needed)
                camera_folder = camera_folders[0]
                if progress_callback:
                    progress_callback(f"Using camera folder: {camera_folder.name}")

                camera_stats = self._process_files(final_files, camera_folder, "photo",
                                                  progress_wrapper if not dry_run else progress_callback,
                                                  dry_run, delete_original=False)
                stats['copied_to_camera'] = camera_stats['processed']
                stats['skipped'] += camera_stats['skipped']
                stats['errors'] += camera_stats['errors']
            else:
                if progress_callback:
                    progress_callback("No camera folders found in DCIM directory. Skipping copy back to camera.")
                stats['errors'] += 1
        elif progress_callback:
            progress_callback("Camera not connected. Skipping copy back to camera.")

        # Delete RAW files from camera for finalized images
        if CAMERA_PATH.exists() and FINAL_PATH.exists():
            if progress_callback:
                progress_callback("Checking for RAW files on camera that can be deleted...")

            # Get all JPGs in the final folder
            final_jpgs = scan_for_images(FINAL_PATH, '.JPG')
            final_jpg_bases = {jpg_file.stem for jpg_file in final_jpgs}

            # Scan camera for RAW files
            camera_files = self.file_manager.scan_camera_files()
            camera_raws = camera_files.get('.RAF', [])

            # Find RAW files on camera that have corresponding JPGs in final folder
            finalized_raws = [raw for raw in camera_raws if raw.stem in final_jpg_bases]

            if finalized_raws and progress_callback:
                progress_callback(f"Deleting {len(finalized_raws)} RAW files from camera...")

            # Delete RAW files from camera
            deleted_count = 0
            for raw_file in finalized_raws:
                if not dry_run:
                    try:
                        raw_file.unlink()
                        deleted_count += 1
                    except Exception as e:
                        stats['errors'] += 1
                        if progress_callback:
                            progress_callback(f"ERROR: Failed to delete RAW file {raw_file}: {str(e)}")
                else:
                    deleted_count += 1

            stats['deleted_camera_raws'] = deleted_count

            if progress_callback:
                progress_callback(f"Deleted {deleted_count} RAW files from camera")

        # Clean up orphaned RAW files
        if progress_callback:
            progress_callback("Checking for orphaned RAW files...")

        if RAWS_PATH.exists() and FINAL_PATH.exists():
            # Get all JPGs in the final folder
            final_jpgs = scan_for_images(FINAL_PATH, '.JPG')

            # Extract base filenames (without extension) from final JPGs
            final_jpg_bases = {jpg_file.stem for jpg_file in final_jpgs}

            # Get all RAFs in the RAWs folder
            raw_files = [raf for raf in RAWS_PATH.glob('*.RAF') if is_valid_image_file(raf)]

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
        final_jpgs = scan_for_images(FINAL_PATH, '.JPG')

        # Extract base filenames (without extension) from final JPGs
        final_jpg_bases = {jpg_file.stem for jpg_file in final_jpgs}

        # Get all RAFs in the RAWs folder
        raw_files = [raf for raf in RAWS_PATH.glob('*.RAF') if is_valid_image_file(raf)]

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
            report.staging_files = len(scan_for_images(STAGING_PATH, '.JPG'))

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
                final_jpgs = scan_for_images(FINAL_PATH, '.JPG')
                final_jpg_bases = {jpg_file.stem for jpg_file in final_jpgs}

            # Filter out JPGs that are already in the Final folder
            pending_jpgs = [jpg for jpg in camera_jpgs if jpg.stem not in final_jpg_bases]
            report.pending_photos = len(pending_jpgs)

            report.pending_raws = len(files.get('.RAF', []))

        return report

    def sync_gallery(self, dry_run: bool = False, progress_callback=None) -> Dict[str, int]:
        """
        Sync high-rated images to the gallery and generate metadata JSON.

        This method:
        1. Scans all JPG files in FINAL_PATH
        2. Extracts metadata for each image
        3. Filters images with rating 4+ for gallery sync
        4. Copies high-rated images to GALLERY_PATH/images/ (only if they've changed)
        5. Removes images from gallery that no longer qualify (rating < 4)
        6. Generates comprehensive metadata JSON file
        7. Saves JSON to GALLERY_PATH/metadata.json
        8. Builds gallery and syncs to remote server (preserving images)

        Args:
            dry_run (bool): If True, only simulate the sync without copying files
            progress_callback (callable): Optional callback function for progress updates

        Returns:
            Dict[str, int]: Statistics about the sync operation
        """
        stats = {
            'scanned': 0,
            'synced': 0,
            'removed': 0,
            'skipped': 0,
            'unchanged': 0,
            'errors': 0,
            'json_updated': False,
            'total_in_gallery': 0
        }

        # Check if FINAL_PATH exists
        if not FINAL_PATH.exists():
            if progress_callback:
                progress_callback("Final folder does not exist. Nothing to sync.")
            return stats

        # Create gallery images directory if it doesn't exist
        gallery_images_path = GALLERY_PATH / "images"
        if not dry_run:
            gallery_images_path.mkdir(parents=True, exist_ok=True)

        # Scan for JPG files in FINAL_PATH
        if progress_callback:
            progress_callback("Scanning for JPG files in Final folder...")

        # Get JPG files with case-insensitive extension matching
        final_jpgs = scan_for_images(FINAL_PATH, '.JPG')
        stats['scanned'] = len(final_jpgs)

        if progress_callback:
            progress_callback(f"Found {len(final_jpgs)} JPG files in Final folder")

        # Extract metadata and filter high-rated images
        high_rated_images = []
        all_metadata = []

        if progress_callback:
            progress_callback("Extracting metadata and filtering high-rated images...")

        # Create a wrapper for the progress callback to show metadata extraction progress
        def progress_wrapper(message):
            if progress_callback:
                progress_callback(message)

        # Process each image
        for i, jpg_path in enumerate(final_jpgs):
            if progress_callback and i % 5 == 0:
                progress_callback(f"Extracting metadata {i + 1}/{len(final_jpgs)}: {jpg_path.name}")

            # Extract metadata
            metadata = MetadataExtractor.extract_metadata(jpg_path)

            # Add metadata to the list
            all_metadata.append(metadata)

            # Check if image has rating 4+
            rating = metadata.get('rating', 0)

            if rating >= 4:
                high_rated_images.append((jpg_path, metadata))

        if progress_callback:
            progress_callback(f"Found {len(high_rated_images)} images with rating 4+")

        # Get existing gallery images
        existing_gallery_images = scan_for_images(gallery_images_path, '.JPG') if gallery_images_path.exists() else []
        existing_gallery_image_names = {img.name for img in existing_gallery_images}

        print(f"Existing gallery images: {len(existing_gallery_images)}")
        print(f"Existing gallery image names: {existing_gallery_image_names}")

        # Determine which images to copy to gallery
        high_rated_image_names = {img[0].name for img in high_rated_images}

        print(f"High-rated images: {len(high_rated_images)}")
        print(f"High-rated image names: {high_rated_image_names}")

        # Images to remove (in gallery but no longer high-rated)
        images_to_remove = [img for img in existing_gallery_images if img.name not in high_rated_image_names]

        # Images to copy (high-rated but not in gallery)
        images_to_copy = [img[0] for img in high_rated_images if img[0].name not in existing_gallery_image_names]

        print(f"Images to remove: {len(images_to_remove)}")
        print(f"Images to copy: {len(images_to_copy)}")

        if progress_callback:
            progress_callback(f"Images to copy: {len(images_to_copy)}, Images to remove: {len(images_to_remove)}")

        # Remove images that no longer qualify
        if not dry_run and images_to_remove:
            if progress_callback:
                progress_callback(f"Removing {len(images_to_remove)} images from gallery...")

            for img_path in images_to_remove:
                try:
                    img_path.unlink()
                    stats['removed'] += 1
                except Exception as e:
                    error_msg = f"Error removing {img_path}: {e}"
                    print(error_msg)
                    if progress_callback:
                        progress_callback(f"ERROR: {error_msg}")
                    stats['errors'] += 1

        # Copy high-rated images to gallery
        if images_to_copy:
            if progress_callback:
                progress_callback(f"Copying {len(images_to_copy)} images to gallery...")

            # Use the generic process_files method for copying
            copy_stats = self._process_files(
                images_to_copy, 
                gallery_images_path, 
                "gallery image",
                progress_callback, 
                dry_run,
                delete_original=False  # Don't delete original files
            )

            stats['synced'] = copy_stats['processed']
            stats['skipped'] = copy_stats['skipped']
            stats['errors'] += copy_stats['errors']

        # Check existing high-rated images for changes
        if progress_callback:
            progress_callback("Checking existing high-rated images for changes...")

        # Images to update (high-rated and already in gallery)
        images_to_update = [(img[0], img[1]) for img in high_rated_images if img[0].name in existing_gallery_image_names]

        # Count of unchanged images
        unchanged_count = 0

        # Update images that have changed
        for src_path, metadata in images_to_update:
            dst_path = gallery_images_path / src_path.name

            # Skip if files are identical
            if FileManager.is_duplicate(src_path, dst_path):
                unchanged_count += 1
                continue

            # Copy the file if it has changed
            if not dry_run:
                try:
                    success, error = FileManager.safe_copy(src_path, dst_path)
                    if success:
                        stats['synced'] += 1
                    else:
                        error_msg = f"Error updating {src_path.name}: {error}"
                        print(error_msg)
                        if progress_callback:
                            progress_callback(f"ERROR: {error_msg}")
                        stats['errors'] += 1
                except Exception as e:
                    error_msg = f"Error updating {src_path.name}: {e}"
                    print(error_msg)
                    if progress_callback:
                        progress_callback(f"ERROR: {error_msg}")
                    stats['errors'] += 1
            else:
                # In dry run mode, just count it
                stats['synced'] += 1

        stats['unchanged'] = unchanged_count

        # Calculate total images in gallery after sync
        if not dry_run:
            stats['total_in_gallery'] = len(scan_for_images(gallery_images_path, '.JPG'))
        else:
            # In dry run mode, estimate the total
            stats['total_in_gallery'] = len(existing_gallery_images) - len(images_to_remove) + len(images_to_copy)

        # Generate metadata JSON for all high-rated images
        if progress_callback:
            progress_callback("Generating metadata JSON...")

        # Filter metadata for high-rated images only
        high_rated_metadata = [metadata for metadata in all_metadata if metadata.get('rating', 0) >= 4]

        # Generate and save metadata JSON
        if not dry_run:
            json_path = GALLERY_PATH / "metadata.json"
            stats['json_updated'] = MetadataExtractor.generate_metadata_json(high_rated_metadata, json_path)

        if progress_callback:
            progress_callback("Gallery sync completed successfully!")

        # Build the gallery and sync to remote server
        if not dry_run:
            # Get the photo_gallery directory path
            photo_gallery_path = GALLERY_PATH.parent

            if progress_callback:
                progress_callback("Building gallery with npm run build...")

            try:
                # Change to photo_gallery directory and run npm build
                build_process = subprocess.run(
                    ["npm", "run", "build"],
                    cwd=photo_gallery_path,
                    capture_output=True,
                    text=True,
                    check=True
                )

                if progress_callback:
                    progress_callback("Gallery build completed successfully.")
                    progress_callback("Syncing dist folder to remote server...")

                # Sync the dist folder to the remote server
                # Using rsync with --delete flag to delete all content in the hosted photo_gallery
                rsync_process = subprocess.run(
                    [
                        "rsync",
                        "-avz",
                        "--delete",
                        f"{photo_gallery_path}/dist/",
                        "jkrumm@5.75.178.196:/home/jkrumm/sideproject-docker-stack/photo_gallery"
                    ],
                    capture_output=True,
                    text=True,
                    check=True
                )

                if progress_callback:
                    progress_callback("Gallery successfully synced to remote server.")

                # Add build and sync info to stats
                stats['build_successful'] = True
                stats['sync_successful'] = True

            except subprocess.CalledProcessError as e:
                error_msg = f"Error during build or sync: {e}"
                stdout_msg = f"Command output: {e.stdout}"
                stderr_msg = f"Command error: {e.stderr}"

                print(error_msg)
                print(stdout_msg)
                print(stderr_msg)

                stats['errors'] += 1
                stats['build_successful'] = False
                stats['sync_successful'] = False

                if progress_callback:
                    progress_callback(f"ERROR: {error_msg}")
                    progress_callback(f"ERROR: {stdout_msg}")
                    progress_callback(f"ERROR: {stderr_msg}")
        else:
            # In dry run mode
            if progress_callback:
                progress_callback("DRY RUN: Would build gallery and sync to remote server")

            # Add build and sync info to stats for dry run
            stats['build_successful'] = False
            stats['sync_successful'] = False

        return stats
