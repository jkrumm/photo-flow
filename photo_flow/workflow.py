"""
Workflow management for the Photo-Flow application.

This module provides the main workflow logic for the application.
"""
import logging
import os
import shutil
import tempfile
from dataclasses import dataclass
from pathlib import Path
import subprocess
from typing import Dict, List

from photo_flow.config import CAMERA_PATH, STAGING_PATH, RAWS_PATH, FINAL_PATH, SSD_PATH, GALLERY_PATH, HOMELAB_USER, HOMELAB_HOST, HOMELAB_DEST_PATH, RSYNC_FLAGS, RSYNC_EXCLUDE_PATTERNS, RSYNC_SSH_BASE, RSYNC_SSH_JUMP
from photo_flow.file_manager import FileManager, is_valid_image_file, scan_for_images
from photo_flow.image_processor import ImageProcessor
from photo_flow.metadata_extractor import MetadataExtractor
from photo_flow.console_utils import console, create_progress, show_status, info

logger = logging.getLogger(__name__)


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
        from photo_flow.console_utils import create_progress, info

        # Scan camera for files
        files = self.file_manager.scan_camera_files()

        # Get finalized files to exclude from staging
        final_jpg_bases = set()
        if FINAL_PATH.exists():
            final_jpgs = scan_for_images(FINAL_PATH, '.JPG')
            final_jpg_bases = {jpg_file.stem for jpg_file in final_jpgs}

        # Filter out finalized files
        jpg_files = [jpg for jpg in files.get('.JPG', []) if jpg.stem not in final_jpg_bases]
        raf_files = [raf for raf in files.get('.RAF', []) if raf.stem not in final_jpg_bases]
        mov_files = files.get('.MOV', [])

        # Count skipped finalized files
        skipped_finalized = (len(files.get('.JPG', [])) - len(jpg_files) +
                             len(files.get('.RAF', [])) - len(raf_files))

        if skipped_finalized > 0:
            info(f"Skipping {skipped_finalized} files already in Final folder")

        # Process each file type with Rich Progress
        total_files = len(mov_files) + len(jpg_files) + len(raf_files)

        if total_files == 0:
            info("No new files to import")
            return {'videos': 0, 'photos': 0, 'raws': 0, 'skipped': skipped_finalized, 'errors': 0}

        all_files = []
        file_destinations = []
        file_types = []
        delete_flags = []

        # Prepare all files for batch processing
        for mov in mov_files:
            all_files.append(mov)
            file_destinations.append(SSD_PATH)
            file_types.append("video")
            delete_flags.append(True)

        for jpg in jpg_files:
            all_files.append(jpg)
            file_destinations.append(STAGING_PATH)
            file_types.append("photo")
            delete_flags.append(False)

        for raf in raf_files:
            all_files.append(raf)
            file_destinations.append(RAWS_PATH)
            file_types.append("RAW")
            delete_flags.append(False)

        mov_count = jpg_count = raf_count = 0
        mov_skip = jpg_skip = raf_skip = 0
        errors = 0

        with create_progress() as progress:
            task = progress.add_task(
                f"[cyan]Importing {total_files} files from camera",
                total=total_files
            )

            for file_path, dest, ftype, should_delete in zip(all_files, file_destinations, file_types, delete_flags):
                if dry_run:
                    if ftype == "video":
                        mov_count += 1
                    elif ftype == "photo":
                        jpg_count += 1
                    else:
                        raf_count += 1
                    progress.advance(task)
                    continue

                dst_path = dest / file_path.name

                # Check for duplicates
                is_dup, error = self.file_manager.is_duplicate(file_path, dst_path)
                if error:
                    errors += 1
                    progress.advance(task)
                    continue

                if dst_path.exists() and is_dup:
                    if ftype == "video":
                        mov_skip += 1
                    elif ftype == "photo":
                        jpg_skip += 1
                    else:
                        raf_skip += 1
                else:
                    success, error = self.file_manager.safe_copy(file_path, dst_path)
                    if success:
                        if ftype == "video":
                            mov_count += 1
                        elif ftype == "photo":
                            jpg_count += 1
                        else:
                            raf_count += 1

                        if should_delete:
                            try:
                                file_path.unlink()
                            except Exception as e:
                                errors += 1
                    else:
                        errors += 1

                progress.advance(task)

        return {
            'videos': mov_count,
            'photos': jpg_count,
            'raws': raf_count,
            'skipped': mov_skip + jpg_skip + raf_skip + skipped_finalized,
            'errors': errors
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
        from photo_flow.console_utils import create_progress, info

        stats = {
            'moved': 0,
            'compressed': 0,
            'copied_to_camera': 0,
            'orphaned_raws': 0,
            'deleted_raws': 0,
            'deleted_camera_raws': 0,
            'skipped': 0,
            'errors': 0
        }

        if not STAGING_PATH.exists():
            info("Staging folder not found. Nothing to finalize.")
            return stats

        staging_files = scan_for_images(STAGING_PATH, '.JPG')

        if len(staging_files) == 0:
            info("No photos in staging to finalize")
            return stats

        # Create final directory if it doesn't exist
        if not dry_run:
            FINAL_PATH.mkdir(parents=True, exist_ok=True)

        # Step 1: Compress and move staging files to Final
        with create_progress() as progress:
            task = progress.add_task(
                f"[cyan]Compressing & moving {len(staging_files)} photos to Final",
                total=len(staging_files)
            )

            for staging_file in staging_files:
                final_path = FINAL_PATH / staging_file.name

                # Check for duplicates
                if final_path.exists():
                    is_dup, _ = self.file_manager.is_duplicate(staging_file, final_path)
                    if is_dup:
                        stats['skipped'] += 1
                        progress.advance(task)
                        continue

                if dry_run:
                    stats['moved'] += 1
                    stats['compressed'] += 1
                else:
                    # ATOMIC: Compress → Copy → Delete
                    temp_compressed = None
                    try:
                        with tempfile.NamedTemporaryFile(suffix='.jpg', delete=False) as tmp:
                            temp_compressed = Path(tmp.name)

                        compress_success, compress_error = self.image_processor.compress_jpeg_safe(
                            staging_file, output_path=temp_compressed
                        )

                        if not compress_success:
                            stats['errors'] += 1
                            progress.advance(task)
                            continue

                        copy_success, copy_error = self.file_manager.safe_copy(temp_compressed, final_path)

                        if copy_success:
                            try:
                                staging_file.unlink()
                                stats['moved'] += 1
                                stats['compressed'] += 1
                            except Exception as e:
                                stats['errors'] += 1
                        else:
                            stats['errors'] += 1

                    finally:
                        if temp_compressed and temp_compressed.exists():
                            try:
                                temp_compressed.unlink()
                            except Exception:
                                pass

                progress.advance(task)

        # Step 2: Copy finalized files back to camera
        if CAMERA_PATH.exists():
            final_files = scan_for_images(FINAL_PATH, '.JPG')
            camera_folders = [folder for folder in CAMERA_PATH.glob('*_*') if folder.is_dir()]

            if camera_folders and final_files:
                camera_folder = camera_folders[0]
                info(f"Copying to camera folder: {camera_folder.name}")

                with create_progress() as progress:
                    task = progress.add_task(
                        f"[cyan]Copying {len(final_files)} photos to camera",
                        total=len(final_files)
                    )

                    for file_path in final_files:
                        dst_path = camera_folder / file_path.name
                        is_dup, _ = self.file_manager.is_duplicate(file_path, dst_path)

                        if dst_path.exists() and is_dup:
                            stats['skipped'] += 1
                        elif not dry_run:
                            success, _ = self.file_manager.safe_copy(file_path, dst_path)
                            if success:
                                stats['copied_to_camera'] += 1
                            else:
                                stats['errors'] += 1
                        else:
                            stats['copied_to_camera'] += 1

                        progress.advance(task)
            elif not camera_folders:
                info("No camera folders found - skipping copy to camera")
        else:
            info("Camera not connected - skipping copy to camera")

        # Step 3: Delete RAW files from camera for finalized images
        if CAMERA_PATH.exists() and FINAL_PATH.exists():
            final_jpgs = scan_for_images(FINAL_PATH, '.JPG')
            final_jpg_bases = {jpg_file.stem for jpg_file in final_jpgs}

            camera_files = self.file_manager.scan_camera_files()
            camera_raws = camera_files.get('.RAF', [])
            finalized_raws = [raw for raw in camera_raws if raw.stem in final_jpg_bases]

            if finalized_raws:
                info(f"Deleting {len(finalized_raws)} RAW files from camera")
                for raw_file in finalized_raws:
                    if not dry_run:
                        try:
                            raw_file.unlink()
                            stats['deleted_camera_raws'] += 1
                        except Exception:
                            stats['errors'] += 1
                    else:
                        stats['deleted_camera_raws'] += 1

        # Step 4: Clean up orphaned local RAW files
        if RAWS_PATH.exists() and FINAL_PATH.exists():
            final_jpgs = scan_for_images(FINAL_PATH, '.JPG')
            final_jpg_bases = {jpg_file.stem for jpg_file in final_jpgs}
            raw_files = [raf for raf in RAWS_PATH.glob('*.RAF') if is_valid_image_file(raf)]
            orphaned_raws = [raw_file for raw_file in raw_files if raw_file.stem not in final_jpg_bases]

            stats['orphaned_raws'] = len(orphaned_raws)

            if orphaned_raws:
                info(f"Found {len(orphaned_raws)} orphaned local RAW files")
                if not dry_run:
                    for raw_file in orphaned_raws:
                        try:
                            raw_file.unlink()
                            stats['deleted_raws'] += 1
                        except Exception:
                            stats['errors'] += 1
                else:
                    stats['deleted_raws'] = len(orphaned_raws)

        return stats

    def cleanup_unused_raws(self, dry_run: bool = False, progress_callback=None) -> Dict[str, int]:
        """
        Clean up unused RAW files that don't have corresponding JPGs in the final folder.

        Args:
            dry_run (bool): If True, only simulate the cleanup without deleting files
            progress_callback (callable): Optional callback function for progress updates (DEPRECATED - not used)

        Returns:
            Dict[str, int]: Statistics about the cleanup operation
        """
        from photo_flow.console_utils import info, create_progress

        stats = {
            'orphaned': 0,
            'deleted': 0,
            'errors': 0
        }

        # Check if RAWs and Final folders exist
        if not RAWS_PATH.exists() or not FINAL_PATH.exists():
            return stats

        info("Scanning for orphaned RAW files...")

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

        info(f"Found {len(orphaned_raws)} orphaned RAW files")

        # If not dry run, delete the orphaned RAWs
        if not dry_run and orphaned_raws:
            with create_progress() as progress:
                task = progress.add_task(
                    f"[cyan]Deleting {len(orphaned_raws)} orphaned RAW files",
                    total=len(orphaned_raws)
                )

                for raw_file in orphaned_raws:
                    try:
                        raw_file.unlink()
                        stats['deleted'] += 1
                    except Exception as e:
                        logger.error(f"Failed to delete {raw_file.name}: {e}")
                        stats['errors'] += 1

                    progress.advance(task)

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

        # Get JPG files with case-insensitive extension matching
        final_jpgs = scan_for_images(FINAL_PATH, '.JPG')
        stats['scanned'] = len(final_jpgs)

        # Extract metadata and filter high-rated images
        high_rated_images = []
        all_metadata = []

        # Use Rich Progress for metadata extraction
        from photo_flow.console_utils import create_progress

        with create_progress() as progress:
            task = progress.add_task(
                f"[cyan]Extracting metadata from {len(final_jpgs)} images",
                total=len(final_jpgs)
            )

            for jpg_path in final_jpgs:
                # Extract metadata
                metadata = MetadataExtractor.extract_metadata(jpg_path)

                # Add metadata to the list
                all_metadata.append(metadata)

                # Check if image has rating 4+
                rating = metadata.get('rating', 0)

                if rating >= 4:
                    high_rated_images.append((jpg_path, metadata))

                progress.advance(task)

        info(f"Found {len(high_rated_images)} images with rating 4+")

        # Get existing gallery images
        existing_gallery_images = scan_for_images(gallery_images_path, '.JPG') if gallery_images_path.exists() else []
        existing_gallery_image_names = {img.name for img in existing_gallery_images}

        logger.debug(f"Existing gallery images: {len(existing_gallery_images)}")
        logger.debug(f"Existing gallery image names: {existing_gallery_image_names}")

        # Determine which images to copy to gallery
        high_rated_image_names = {img[0].name for img in high_rated_images}

        logger.debug(f"High-rated images: {len(high_rated_images)}")
        logger.debug(f"High-rated image names: {high_rated_image_names}")

        # Images to remove (in gallery but no longer high-rated)
        images_to_remove = [img for img in existing_gallery_images if img.name not in high_rated_image_names]

        # Images to copy (high-rated but not in gallery)
        images_to_copy = [img[0] for img in high_rated_images if img[0].name not in existing_gallery_image_names]

        logger.debug(f"Images to remove: {len(images_to_remove)}")
        logger.debug(f"Images to copy: {len(images_to_copy)}")

        # Remove images that no longer qualify
        if not dry_run and images_to_remove:
            info(f"Removing {len(images_to_remove)} images no longer rated 4+")
            for img_path in images_to_remove:
                try:
                    img_path.unlink()
                    stats['removed'] += 1
                except Exception as e:
                    error_msg = f"Error removing {img_path}: {e}"
                    logger.error(error_msg)
                    from photo_flow.console_utils import error as print_error
                    print_error(error_msg)
                    stats['errors'] += 1

        # Copy/update high-rated images to gallery
        total_to_process = len(images_to_copy)

        if total_to_process > 0:
            with create_progress() as progress:
                task = progress.add_task(
                    f"[cyan]Copying {total_to_process} new images to gallery",
                    total=total_to_process
                )

                for img_path in images_to_copy:
                    if not dry_run:
                        dst_path = gallery_images_path / img_path.name
                        success, error_msg = FileManager.safe_copy(img_path, dst_path)
                        if success:
                            stats['synced'] += 1
                        else:
                            logger.error(f"Error copying {img_path.name}: {error_msg}")
                            stats['errors'] += 1
                    else:
                        stats['synced'] += 1

                    progress.advance(task)

        # Check existing high-rated images for changes
        images_to_update = [(img[0], img[1]) for img in high_rated_images if
                            img[0].name in existing_gallery_image_names]

        unchanged_count = 0

        # Check and update existing images silently (no verbose output)
        for src_path, metadata in images_to_update:
            dst_path = gallery_images_path / src_path.name

            # Skip if files are identical
            is_dup, err = FileManager.is_duplicate(src_path, dst_path)
            if err:
                logger.error(f"Error checking {src_path.name}: {err}")
                stats['errors'] += 1
            elif is_dup:
                unchanged_count += 1
                continue

            # Copy the file if it has changed
            if not dry_run:
                try:
                    success, error = FileManager.safe_copy(src_path, dst_path)
                    if success:
                        stats['synced'] += 1
                    else:
                        logger.error(f"Error updating {src_path.name}: {error}")
                        stats['errors'] += 1
                except Exception as e:
                    logger.error(f"Error updating {src_path.name}: {e}")
                    stats['errors'] += 1
            else:
                stats['synced'] += 1

        stats['unchanged'] = unchanged_count

        # Calculate total images in gallery after sync
        if not dry_run:
            stats['total_in_gallery'] = len(scan_for_images(gallery_images_path, '.JPG'))
        else:
            # In dry run mode, estimate the total
            stats['total_in_gallery'] = len(existing_gallery_images) - len(images_to_remove) + len(images_to_copy)

        # Generate metadata JSON for all high-rated images
        high_rated_metadata = [metadata for metadata in all_metadata if metadata.get('rating', 0) >= 4]

        if not dry_run:
            json_path = GALLERY_PATH / "metadata.json"
            stats['json_updated'] = MetadataExtractor.generate_metadata_json(high_rated_metadata, json_path)

        # Build the gallery and sync to remote server
        if not dry_run:
            photo_gallery_path = GALLERY_PATH.parent

            # Use status spinner for build
            from photo_flow.console_utils import show_status

            try:
                with show_status("Building gallery with npm", spinner="dots"):
                    # Read the required Node version from .nvmrc
                    nvmrc_path = photo_gallery_path / ".nvmrc"
                    if nvmrc_path.exists():
                        with open(nvmrc_path, 'r') as f:
                            node_version = f.read().strip()

                        node_version_clean = node_version.lstrip('v')
                        node_version_path = os.path.expanduser(f"~/.nvm/versions/node/v{node_version_clean}/bin")

                        env = os.environ.copy()
                        env["PATH"] = f"{node_version_path}:{env['PATH']}"
                    else:
                        env = None

                    # Run npm build
                    build_process = subprocess.run(
                        ["npm", "run", "build"],
                        cwd=photo_gallery_path,
                        capture_output=True,
                        text=True,
                        check=True,
                        env=env
                    )

                # Use status spinner for rsync
                with show_status("Syncing to remote server", spinner="dots"):
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

                stats['build_successful'] = True
                stats['sync_successful'] = True

            except subprocess.CalledProcessError as e:
                from photo_flow.console_utils import error as print_error
                print_error(f"Build/sync failed: {e.stderr if e.stderr else str(e)}")

                logger.error(f"Error during build or sync: {e}")
                logger.error(f"Command output: {e.stdout}")
                logger.error(f"Command error: {e.stderr}")

                stats['errors'] += 1
                stats['build_successful'] = False
                stats['sync_successful'] = False
        else:
            # Dry run - don't actually build/sync
            info("[dim]Dry run: Skipping npm build and remote sync[/dim]")
            stats['sync_successful'] = False

        return stats

    def backup_final_to_homelab(self, dry_run: bool = False, progress_callback=None) -> Dict[str, int]:
        """
        Backup the Final folder to the homelab server via rsync.

        Uses rsync for safe, interruptible syncing. In dry-run mode, no changes
        are made on the remote side and rsync runs with -n.

        Automatically tries direct connection first (IPv6), then falls back to
        ProxyJump via VPS if direct connection fails (IPv4-only networks).

        Args:
            dry_run (bool): If True, only simulate the backup without syncing
            progress_callback (callable): Optional callback function for progress updates (DEPRECATED - not used)

        Returns:
            Dict with keys: 'scanned', 'sync_successful' (bool), 'errors' (int), 'connection_method' (str)
        """
        from photo_flow.console_utils import info, error as print_error, warning

        stats = {
            'scanned': 0,
            'sync_successful': False,
            'errors': 0,
            'connection_method': None,
        }

        # Pre-checks
        if not FINAL_PATH.exists():
            print_error("Final folder does not exist. Nothing to backup.")
            return stats

        if shutil.which("rsync") is None:
            print_error("rsync not found on PATH. Please install rsync.")
            stats['errors'] += 1
            return stats

        # Count JPGs (informational and safety check)
        try:
            jpgs = scan_for_images(FINAL_PATH, '.JPG')
            stats['scanned'] = len(jpgs)

            # Safety check: Ensure Final folder has a reasonable number of files
            # This prevents accidentally wiping the backup if Final is empty or unmounted
            if len(jpgs) < 100:
                warning(f"Final folder only has {len(jpgs)} files. Expected 1000+.")
                warning("This could indicate Final folder is empty or unmounted.")
                warning("Backup aborted to prevent accidental deletion of remote files.")
                stats['errors'] += 1
                return stats
        except Exception as e:
            print_error(f"Failed to scan Final folder: {e}")
            stats['errors'] += 1
            return stats

        remote = f"{HOMELAB_USER}@{HOMELAB_HOST}:{str(HOMELAB_DEST_PATH)}"
        src = f"{str(FINAL_PATH)}/"  # trailing slash = sync contents

        # Try direct connection first (IPv6), then fall back to ProxyJump (IPv4)
        ssh_configs = [
            ("direct", RSYNC_SSH_BASE),
            ("proxyjump", RSYNC_SSH_JUMP)
        ]

        for method, ssh_cmd in ssh_configs:
            # Build rsync command
            cmd = ["rsync"]
            cmd.extend(RSYNC_FLAGS)
            # Add exclusion patterns for system files
            for pattern in RSYNC_EXCLUDE_PATTERNS:
                cmd.extend(["--exclude", pattern])
            cmd.extend(["-e", ssh_cmd])
            if dry_run:
                cmd.append("-n")
            cmd.extend([src, remote])

            if method == "direct":
                info("Attempting direct connection (IPv6)...")
            else:
                warning("Direct connection failed.")
                info("Trying ProxyJump via VPS (IPv4)...")

            try:
                # Let rsync progress output flow to terminal (don't capture stderr)
                subprocess.run(cmd, check=True)
                stats['sync_successful'] = True
                stats['connection_method'] = method
                method_desc = "direct IPv6" if method == "direct" else "ProxyJump via VPS"
                info(f"[green]✓[/green] Backup completed successfully using {method_desc}")
                return stats
            except subprocess.CalledProcessError as e:
                if method == "proxyjump":
                    # Both methods failed
                    stats['errors'] += 1
                    print_error(f"Both connection methods failed. Last error: {e}")
                # Continue to next method

        return stats
