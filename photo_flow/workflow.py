"""
Workflow management for the Photo-Flow application.

This module provides the main workflow logic for the application.
"""
import logging
import os
import re
import shutil
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
import subprocess
from typing import Dict, List, Optional

from photo_flow.config import (
    CAMERA_PATH, STAGING_PATH, RAWS_PATH, FINAL_PATH, SSD_PATH, GALLERY_PATH,
    GALLERY_REMOTE_USER, GALLERY_REMOTE_HOST, GALLERY_REMOTE_PATH,
    HOMELAB_USER, HOMELAB_HOST, HOMELAB_SSD_FINAL_PATH, HOMELAB_HDD_RAWS_PATH,
    HOMELAB_HDD_VIDEOS_PATH, HOMELAB_TRASH_PATH, HOMELAB_SSD_TRASH_PATH, RSYNC_EXCLUDE_PATTERNS,
    RCLONE_TRANSFERS, RCLONE_SSH_CIPHER, RCLONE_SFTP_CONCURRENCY, HOMELAB_SSH_OPTS
)
from photo_flow.file_manager import FileManager, is_valid_image_file, scan_for_images
from photo_flow.metadata_extractor import MetadataExtractor
from photo_flow.console_utils import console, create_progress, show_status, info, warning, error
from photo_flow.progress import ProgressReporter, RichReporter
from photo_flow.immich_client import trigger_immich_scan
from photo_flow.timestamp_renamer import generate_timestamped_filename, extract_original_base, is_already_renamed

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# rclone line-parser — module-level so tests can import it directly
# ---------------------------------------------------------------------------

_RCLONE_PCT_RE = re.compile(
    r'Transferred:.*?,\s+(\d+)%,\s+([\d.]+\s*\S+/s),\s+ETA\s+(\S+)'
)
_RCLONE_FILES_RE = re.compile(
    r'Transferred:\s+(\d+)\s*/\s*(\d+),\s+\d+%'
)


def _parse_rclone_line(line: str) -> dict:
    """
    Parse a single rclone stats line into progress fields.

    Returned keys (all optional):
      pct (int), speed (str), eta (str)  — from the speed/ETA line
      files_done (int), files_total (int) — from the file-count line
    Empty dict when the line matches neither pattern.
    """
    result: dict = {}
    m = _RCLONE_PCT_RE.search(line)
    if m:
        pct, speed, eta = m.groups()
        result["pct"] = int(pct)
        result["speed"] = speed
        result["eta"] = eta
    fm = _RCLONE_FILES_RE.search(line)
    if fm:
        done, total = fm.groups()
        result["files_done"] = int(done)
        result["files_total"] = int(total)
    return result


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
                # Delete from camera since verified backup exists
                if delete_original:
                    try:
                        file_path.unlink()
                    except Exception as e:
                        stats['errors'] += 1
                        if progress_callback:
                            progress_callback(f"ERROR: Failed to delete duplicate original {file_path}: {str(e)}")
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

    def import_from_camera(
        self,
        dry_run: bool = False,
        reporter: Optional[ProgressReporter] = None,
        progress_callback=None,
    ) -> Dict[str, int]:
        """
        Import files from the camera to the appropriate locations.
        Excludes files that are already in the Final folder to avoid re-staging finalized photos.
        """
        if reporter is None:
            reporter = RichReporter()

        # Check camera connection
        if not CAMERA_PATH.exists():
            reporter.log("warning", f"Camera not connected at {CAMERA_PATH} - nothing to import")
            return {'videos': 0, 'photos': 0, 'raws': 0, 'skipped': 0, 'errors': 0}

        # Scan camera for files
        files = self.file_manager.scan_camera_files()

        # Get all files from camera (no pre-filtering by DSCF base)
        # With timestamp naming, counter wrap is handled: new DSCF0430 becomes 2026-01-30_..._DSCF0430.JPG
        # Content-based duplicate check later catches actual duplicates (same file hash)
        jpg_files = files.get('.JPG', [])
        raf_files = files.get('.RAF', [])
        mov_files = files.get('.MOV', [])

        # Check SSD connection for video import
        ssd_connected = SSD_PATH.exists()
        if mov_files and not ssd_connected:
            reporter.log("warning", f"SSD not connected at {SSD_PATH} - skipping {len(mov_files)} video files")
            mov_files = []

        # Check SSD connection for RAW import (RAWs stored on external drive)
        if raf_files and not ssd_connected:
            reporter.log("warning", f"SSD not connected - skipping {len(raf_files)} RAW files")
            raf_files = []

        # Process each file type with Rich Progress
        total_files = len(mov_files) + len(jpg_files) + len(raf_files)

        if total_files == 0:
            reporter.log("info", "No new files to import")
            return {'videos': 0, 'photos': 0, 'raws': 0, 'skipped': 0, 'errors': 0}

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
            delete_flags.append(True)

        for raf in raf_files:
            all_files.append(raf)
            file_destinations.append(RAWS_PATH)
            file_types.append("RAW")
            delete_flags.append(True)

        mov_count = jpg_count = raf_count = 0
        mov_skip = jpg_skip = raf_skip = 0
        errors = 0

        # Track existing filenames per destination for collision detection
        existing_names = {
            SSD_PATH: {f.name for f in SSD_PATH.glob('*')} if SSD_PATH.exists() else set(),
            STAGING_PATH: {f.name for f in STAGING_PATH.glob('*')} if STAGING_PATH.exists() else set(),
            RAWS_PATH: {f.name for f in RAWS_PATH.glob('*')} if RAWS_PATH.exists() else set(),
        }

        with reporter:
            reporter.task(
                f"[cyan]Importing {total_files} files from camera",
                total_files,
            )

            for file_path, dest, ftype, should_delete in zip(all_files, file_destinations, file_types, delete_flags):
                # Generate timestamped filename
                new_filename, ts_error = generate_timestamped_filename(file_path, existing_names[dest])
                if ts_error:
                    logger.warning(f"Using original name: {ts_error}")

                if dry_run:
                    if ftype == "video":
                        mov_count += 1
                    elif ftype == "photo":
                        jpg_count += 1
                    else:
                        raf_count += 1
                    existing_names[dest].add(new_filename)
                    reporter.event("file_done", {"filename": new_filename, "dest": str(dest), "type": ftype, "action": "dry_run"})
                    reporter.advance()
                    continue

                dst_path = dest / new_filename

                # Check for duplicates (by content, not just name)
                # First check if a file with the original base exists
                is_dup = False
                orig_base = extract_original_base(file_path.name)
                for existing in dest.glob('*'):
                    if extract_original_base(existing.name) == orig_base:
                        dup_check, _ = self.file_manager.is_duplicate(file_path, existing)
                        if dup_check:
                            is_dup = True
                            break

                action = "skipped"
                if is_dup:
                    if ftype == "video":
                        mov_skip += 1
                    elif ftype == "photo":
                        jpg_skip += 1
                    else:
                        raf_skip += 1
                    # Delete from camera since verified backup exists
                    if should_delete:
                        try:
                            file_path.unlink()
                        except Exception as e:
                            reporter.log("error", f"Failed to delete duplicate {file_path.name}: {e}")
                            errors += 1
                else:
                    copy_success, copy_error = self.file_manager.safe_copy(file_path, dst_path)
                    if copy_success:
                        action = "copied"
                        existing_names[dest].add(new_filename)
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
                                reporter.log("error", f"Failed to delete original {file_path.name}: {e}")
                                errors += 1
                    else:
                        action = "error"
                        reporter.log("error", f"Failed to copy {file_path.name}: {copy_error}")
                        errors += 1

                reporter.event("file_done", {"filename": new_filename, "dest": str(dest), "type": ftype, "action": action})
                reporter.advance()

        return {
            'videos': mov_count,
            'photos': jpg_count,
            'raws': raf_count,
            'skipped': mov_skip + jpg_skip + raf_skip,
            'errors': errors
        }

    def finalize_staging(
        self,
        dry_run: bool = False,
        reporter: Optional[ProgressReporter] = None,
        progress_callback=None,
    ) -> Dict[str, int]:
        """
        Finalize the staging process by moving approved photos to the final folder
        and cleaning up orphaned RAW files.

        Args:
            dry_run (bool): If True, only simulate the finalization without moving files
            reporter: Progress reporter; defaults to RichReporter (identical CLI output).
            progress_callback: Ignored — kept for call-site compatibility.

        Returns:
            Dict[str, int]: Statistics about the finalization operation
        """
        if reporter is None:
            reporter = RichReporter()

        stats = {
            'moved': 0,
            'edits_moved': 0,
            'orphaned_raws': 0,
            'deleted_raws': 0,
            'deleted_camera_raws': 0,
            'skipped': 0,
            'errors': 0
        }

        if not STAGING_PATH.exists():
            reporter.log("info", "Staging folder not found. Nothing to finalize.")
            return stats

        staging_files = scan_for_images(STAGING_PATH, '.JPG')

        if len(staging_files) == 0:
            reporter.log("info", "No photos in staging to finalize")
            return stats

        # Create final directory if it doesn't exist
        if not dry_run:
            FINAL_PATH.mkdir(parents=True, exist_ok=True)

        # Step 1: Move staging files to Final at full quality (no re-compression).
        # Photomator bakes its edits and embeds the star rating into the JPG itself,
        # so the staging JPG is already the finished, full-quality master. Re-encoding
        # it would only add generation loss, and the web gallery already downscales on
        # demand (Astro + sharp). Photomator's .photo-edit sidecar (the re-editable
        # history) travels with its JPG so finalized photos stay non-destructively editable.
        with reporter:
            reporter.task(
                f"[cyan]Moving {len(staging_files)} photos to Final",
                len(staging_files),
            )

            for staging_file in staging_files:
                final_path = FINAL_PATH / staging_file.name
                sidecar_src = staging_file.with_suffix('.photo-edit')
                sidecar_dst = FINAL_PATH / sidecar_src.name
                has_sidecar = sidecar_src.exists()

                # Check for duplicates
                if final_path.exists():
                    is_dup, _ = self.file_manager.is_duplicate(staging_file, final_path)
                    if is_dup:
                        stats['skipped'] += 1
                        reporter.event("file_done", {"filename": staging_file.name, "action": "skipped"})
                        reporter.advance()
                        continue

                if dry_run:
                    stats['moved'] += 1
                    if has_sidecar:
                        stats['edits_moved'] += 1
                    reporter.event("file_done", {"filename": staging_file.name, "action": "moved"})
                else:
                    # ATOMIC per file: Copy (hash-verified) → Delete. Interrupt-safe:
                    # an unfinished file simply stays in Staging for the next run.
                    copy_success, copy_error = self.file_manager.safe_copy(staging_file, final_path)

                    if not copy_success:
                        reporter.log("error", f"Failed to copy {staging_file.name} to Final: {copy_error}")
                        stats['errors'] += 1
                        reporter.event("file_done", {"filename": staging_file.name, "action": "error"})
                        reporter.advance()
                        continue

                    # Move the .photo-edit sidecar alongside its JPG (hash-verified copy).
                    if has_sidecar:
                        sc_success, sc_error = self.file_manager.safe_copy(sidecar_src, sidecar_dst)
                        if sc_success:
                            try:
                                sidecar_src.unlink()
                                stats['edits_moved'] += 1
                            except Exception as e:
                                reporter.log("error", f"Failed to delete staging sidecar {sidecar_src.name}: {e}")
                                stats['errors'] += 1
                        else:
                            reporter.log("error", f"Failed to copy sidecar {sidecar_src.name} to Final: {sc_error}")
                            stats['errors'] += 1

                    # Remove the staging JPG only after its own verified copy succeeded.
                    try:
                        staging_file.unlink()
                        stats['moved'] += 1
                    except Exception as e:
                        reporter.log("error", f"Failed to delete staging file {staging_file.name}: {e}")
                        stats['errors'] += 1

                    reporter.event("file_done", {"filename": staging_file.name, "action": "moved"})

                reporter.advance()

        # Step 2: Delete RAW files from camera for finalized images
        # Note: RAW files are now deleted during import, so this will typically find nothing.
        # Kept for backwards compatibility in case RAWs are manually added to camera.
        if CAMERA_PATH.exists() and FINAL_PATH.exists():
            final_jpgs = scan_for_images(FINAL_PATH, '.JPG')
            # Use extract_original_base to handle both old and timestamp-renamed files
            final_jpg_bases = {extract_original_base(jpg_file.name) for jpg_file in final_jpgs}

            camera_files = self.file_manager.scan_camera_files()
            camera_raws = camera_files.get('.RAF', [])
            finalized_raws = [raw for raw in camera_raws if extract_original_base(raw.name) in final_jpg_bases]

            if finalized_raws:
                reporter.log("info", f"Deleting {len(finalized_raws)} RAW files from camera")
                for raw_file in finalized_raws:
                    if not dry_run:
                        try:
                            raw_file.unlink()
                            stats['deleted_camera_raws'] += 1
                        except Exception as e:
                            reporter.log("error", f"Failed to delete camera RAW {raw_file.name}: {e}")
                            stats['errors'] += 1
                    else:
                        stats['deleted_camera_raws'] += 1

        # Step 4: Clean up orphaned local RAW files
        if RAWS_PATH.exists() and FINAL_PATH.exists():
            final_jpgs = scan_for_images(FINAL_PATH, '.JPG')
            # Use extract_original_base to handle both old and timestamp-renamed files
            final_jpg_bases = {extract_original_base(jpg_file.name) for jpg_file in final_jpgs}
            raw_files = [raf for raf in RAWS_PATH.glob('*.RAF') if is_valid_image_file(raf)]
            orphaned_raws = [raw_file for raw_file in raw_files if extract_original_base(raw_file.name) not in final_jpg_bases]

            stats['orphaned_raws'] = len(orphaned_raws)

            if orphaned_raws:
                reporter.log("info", f"Found {len(orphaned_raws)} orphaned local RAW files")
                if not dry_run:
                    for raw_file in orphaned_raws:
                        try:
                            raw_file.unlink()
                            stats['deleted_raws'] += 1
                        except Exception as e:
                            reporter.log("error", f"Failed to delete orphaned RAW {raw_file.name}: {e}")
                            stats['errors'] += 1
                else:
                    stats['deleted_raws'] = len(orphaned_raws)

        return stats

    def cleanup_unused_raws(
        self,
        dry_run: bool = False,
        reporter: Optional[ProgressReporter] = None,
        progress_callback=None,
    ) -> Dict[str, int]:
        """
        Clean up unused RAW files that don't have corresponding JPGs in the final folder.

        Args:
            dry_run: If True, only simulate the cleanup without deleting files
            reporter: Progress reporter; defaults to RichReporter (identical CLI output).
            progress_callback: Ignored — kept for call-site compatibility.

        Returns:
            Dict[str, int]: Statistics about the cleanup operation
        """
        if reporter is None:
            reporter = RichReporter()

        stats = {
            'orphaned': 0,
            'deleted': 0,
            'errors': 0
        }

        # Check SSD connection (RAWs stored on external drive)
        if not SSD_PATH.exists():
            reporter.log("warning", "External SSD not connected - RAWs folder unavailable")
            return stats

        # Check if RAWs and Final folders exist
        if not RAWS_PATH.exists():
            reporter.log("warning", f"RAWs folder not found at {RAWS_PATH} - nothing to clean up")
            return stats
        if not FINAL_PATH.exists():
            reporter.log("warning", f"Final folder not found at {FINAL_PATH} - cannot determine orphaned RAWs")
            return stats

        reporter.log("info", "Scanning for orphaned RAW files...")

        # Get all JPGs in the final folder
        final_jpgs = scan_for_images(FINAL_PATH, '.JPG')

        # Extract original base filenames from final JPGs (handles both old and timestamp-renamed files)
        final_jpg_bases = {extract_original_base(jpg_file.name) for jpg_file in final_jpgs}

        # Get all RAFs in the RAWs folder
        raw_files = [raf for raf in RAWS_PATH.glob('*.RAF') if is_valid_image_file(raf)]

        # Find orphaned RAWs (those without a corresponding JPG in final)
        # Compare using original base to handle timestamp-renamed files
        orphaned_raws = [raw_file for raw_file in raw_files
                         if extract_original_base(raw_file.name) not in final_jpg_bases]

        stats['orphaned'] = len(orphaned_raws)
        reporter.log("info", f"Found {len(orphaned_raws)} orphaned RAW files")

        if not dry_run and orphaned_raws:
            with reporter:
                reporter.task(
                    f"[cyan]Deleting {len(orphaned_raws)} orphaned RAW files",
                    len(orphaned_raws)
                )
                for raw_file in orphaned_raws:
                    try:
                        raw_file.unlink()
                        stats['deleted'] += 1
                    except Exception as e:
                        reporter.log("error", f"Failed to delete {raw_file.name}: {e}")
                        stats['errors'] += 1
                    reporter.advance()

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
        # All files on camera are pending - no copy-back means camera only has new photos
        if report.camera_connected:
            files = self.file_manager.scan_camera_files()
            report.pending_videos = len(files.get('.MOV', []))
            report.pending_photos = len(files.get('.JPG', []))
            report.pending_raws = len(files.get('.RAF', []))

        return report

    def sync_gallery(
        self,
        dry_run: bool = False,
        reporter: Optional[ProgressReporter] = None,
        progress_callback=None,
    ) -> Dict[str, int]:
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
            dry_run: If True, only simulate the sync without copying files
            reporter: Progress reporter; defaults to RichReporter (identical CLI output).
            progress_callback: Ignored — kept for call-site compatibility.

        Returns:
            Dict[str, int]: Statistics about the sync operation
        """
        if reporter is None:
            reporter = RichReporter()

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

        if not FINAL_PATH.exists():
            reporter.log("info", "Final folder does not exist. Nothing to sync.")
            return stats

        gallery_images_path = GALLERY_PATH / "images"
        if not dry_run:
            gallery_images_path.mkdir(parents=True, exist_ok=True)

        final_jpgs = scan_for_images(FINAL_PATH, '.JPG')
        stats['scanned'] = len(final_jpgs)

        high_rated_images = []
        all_metadata = []

        with reporter:
            reporter.task(
                f"[cyan]Extracting metadata from {len(final_jpgs)} images",
                len(final_jpgs)
            )
            for jpg_path in final_jpgs:
                metadata = MetadataExtractor.extract_metadata(jpg_path)
                all_metadata.append(metadata)
                if metadata.get('rating', 0) >= 4:
                    high_rated_images.append((jpg_path, metadata))
                reporter.advance()

        reporter.log("info", f"Found {len(high_rated_images)} images with rating 4+")

        existing_gallery_images = scan_for_images(gallery_images_path, '.JPG') if gallery_images_path.exists() else []
        existing_gallery_image_names = {img.name for img in existing_gallery_images}

        logger.debug(f"Existing gallery images: {len(existing_gallery_images)}")
        logger.debug(f"Existing gallery image names: {existing_gallery_image_names}")

        high_rated_image_names = {img[0].name for img in high_rated_images}

        logger.debug(f"High-rated images: {len(high_rated_images)}")
        logger.debug(f"High-rated image names: {high_rated_image_names}")

        images_to_remove = [img for img in existing_gallery_images if img.name not in high_rated_image_names]
        images_to_copy = [img[0] for img in high_rated_images if img[0].name not in existing_gallery_image_names]

        logger.debug(f"Images to remove: {len(images_to_remove)}")
        logger.debug(f"Images to copy: {len(images_to_copy)}")

        if not dry_run and images_to_remove:
            reporter.log("info", f"Removing {len(images_to_remove)} images no longer rated 4+")
            for img_path in images_to_remove:
                try:
                    img_path.unlink()
                    stats['removed'] += 1
                except Exception as e:
                    error_msg = f"Error removing {img_path}: {e}"
                    logger.error(error_msg)
                    reporter.log("error", error_msg)
                    stats['errors'] += 1

        total_to_process = len(images_to_copy)
        if total_to_process > 0:
            with reporter:
                reporter.task(
                    f"[cyan]Copying {total_to_process} new images to gallery",
                    total_to_process
                )
                for img_path in images_to_copy:
                    if not dry_run:
                        dst_path = gallery_images_path / img_path.name
                        copy_ok, copy_err = FileManager.safe_copy(img_path, dst_path)
                        if copy_ok:
                            stats['synced'] += 1
                        else:
                            logger.error(f"Error copying {img_path.name}: {copy_err}")
                            stats['errors'] += 1
                    else:
                        stats['synced'] += 1
                    reporter.advance()

        images_to_update = [(img[0], img[1]) for img in high_rated_images if
                            img[0].name in existing_gallery_image_names]

        unchanged_count = 0
        for src_path, _ in images_to_update:
            dst_path = gallery_images_path / src_path.name
            is_dup, err = FileManager.is_duplicate(src_path, dst_path)
            if err:
                logger.error(f"Error checking {src_path.name}: {err}")
                stats['errors'] += 1
            elif is_dup:
                unchanged_count += 1
                continue

            if not dry_run:
                try:
                    copy_ok, copy_err = FileManager.safe_copy(src_path, dst_path)
                    if copy_ok:
                        stats['synced'] += 1
                    else:
                        logger.error(f"Error updating {src_path.name}: {copy_err}")
                        stats['errors'] += 1
                except Exception as e:
                    logger.error(f"Error updating {src_path.name}: {e}")
                    stats['errors'] += 1
            else:
                stats['synced'] += 1

        stats['unchanged'] = unchanged_count

        if not dry_run:
            stats['total_in_gallery'] = len(scan_for_images(gallery_images_path, '.JPG'))
        else:
            stats['total_in_gallery'] = len(existing_gallery_images) - len(images_to_remove) + len(images_to_copy)

        high_rated_metadata = [m for m in all_metadata if m.get('rating', 0) >= 4]
        if not dry_run:
            json_path = GALLERY_PATH / "metadata.json"
            stats['json_updated'] = MetadataExtractor.generate_metadata_json(high_rated_metadata, json_path)

        if not dry_run:
            photo_gallery_path = GALLERY_PATH.parent
            try:
                reporter.log("info", "Building gallery with npm...")
                reporter.event("phase", {"name": "build", "status": "starting"})

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

                subprocess.run(
                    ["npm", "run", "build"],
                    cwd=photo_gallery_path,
                    capture_output=True,
                    text=True,
                    check=True,
                    env=env
                )
                reporter.event("phase", {"name": "build", "status": "done"})

                reporter.log("info", "Syncing to remote server...")
                reporter.event("phase", {"name": "sync", "status": "starting"})
                # Tailscale already encrypts the link — drop -z, use the fast cipher.
                subprocess.run(
                    [
                        "rsync",
                        "-a",
                        "--delete",
                        "-e", f"ssh -T -c {RCLONE_SSH_CIPHER} -o Compression=no -o ConnectTimeout=5",
                        f"{photo_gallery_path}/dist/",
                        f"{GALLERY_REMOTE_USER}@{GALLERY_REMOTE_HOST}:{GALLERY_REMOTE_PATH}/"
                    ],
                    capture_output=True,
                    text=True,
                    check=True
                )
                reporter.event("phase", {"name": "sync", "status": "done"})

                stats['build_successful'] = True
                stats['sync_successful'] = True

            except subprocess.CalledProcessError as e:
                reporter.log("error", f"Build/sync failed: {e.stderr if e.stderr else str(e)}")
                reporter.event("phase", {"name": "build_or_sync", "status": "failed"})
                logger.error(f"Error during build or sync: {e}")
                logger.error(f"Command output: {e.stdout}")
                logger.error(f"Command error: {e.stderr}")
                stats['errors'] += 1
                stats['build_successful'] = False
                stats['sync_successful'] = False
        else:
            reporter.log("info", "[dim]Dry run: Skipping npm build and remote sync[/dim]")
            stats['sync_successful'] = False

        return stats

    def backup_final_to_homelab(
        self,
        dry_run: bool = False,
        reporter: Optional[ProgressReporter] = None,
        progress_callback=None,
    ) -> Dict[str, any]:
        """
        Backup the Final folder to the homelab server via rclone with parallel transfers.

        Uses rclone sync over SFTP with trash-based deletion. Connects via Tailscale.

        Args:
            dry_run: If True, only simulate the backup without syncing
            reporter: Progress reporter; defaults to RichReporter (identical CLI output).
            progress_callback: Ignored — kept for call-site compatibility.

        Returns:
            Dict with keys: 'source', 'scanned', 'sync_successful', 'connection_method', 'trash_path', 'errors'
        """
        if reporter is None:
            reporter = RichReporter()

        stats = self._run_backup_rclone(
            source_path=FINAL_PATH,
            remote_dest=HOMELAB_SSD_FINAL_PATH,
            source_name='final',
            dry_run=dry_run,
            min_files=100,
            file_pattern='*.JPG',
            trash_base_path=HOMELAB_SSD_TRASH_PATH,
            reporter=reporter,
        )

        if stats['sync_successful'] and not dry_run:
            reporter.log("info", "Triggering Immich library scan...")
            immich_success, immich_msg = trigger_immich_scan()
            stats['immich_scan_triggered'] = immich_success
            if immich_success:
                reporter.log("info", "[green]✓[/green] Immich scan triggered")
            else:
                reporter.log("warning", f"Immich scan failed: {immich_msg}")

        return stats

    def _get_remote_file_count(self, remote_path: Path, extension: str) -> tuple[int, str]:
        """
        Get file count from remote homelab directory via SSH (Tailscale).

        Args:
            remote_path: Remote directory path
            extension: File extension to count (e.g., '*.JPG')

        Returns:
            Tuple of (count, "tailscale") or (-1, error_message) on failure
        """
        try:
            remote = f"{HOMELAB_USER}@{HOMELAB_HOST}"
            count_cmd = f"find {remote_path} -maxdepth 1 -name '{extension}' 2>/dev/null | wc -l"

            result = subprocess.run(
                ["ssh"] + HOMELAB_SSH_OPTS + [remote, count_cmd],
                capture_output=True,
                text=True,
                timeout=10
            )

            if result.returncode == 0:
                count = int(result.stdout.strip())
                return (count, "tailscale")
        except (subprocess.TimeoutExpired, subprocess.CalledProcessError, ValueError):
            pass

        return (-1, "connection failed")

    def get_backup_availability(self, check_remote: bool = False) -> Dict[str, Dict]:
        """
        Check which backup sources are available and optionally compare with remote.

        Args:
            check_remote: If True, SSH to homelab to get remote file counts

        Returns:
            Dict with availability info for each backup source:
            {
                'final': {'available': bool, 'path': Path, 'local_count': int, 'remote_count': int, 'needs_sync': int},
                ...
            }
        """
        result = {
            'final': {
                'available': FINAL_PATH.exists(),
                'path': FINAL_PATH,
                'remote_path': HOMELAB_SSD_FINAL_PATH,
                'local_count': len(list(FINAL_PATH.glob('*.JPG'))) if FINAL_PATH.exists() else 0,
                'extension': '*.JPG',
            },
            'raws': {
                'available': RAWS_PATH.exists(),
                'path': RAWS_PATH,
                'remote_path': HOMELAB_HDD_RAWS_PATH,
                'local_count': len(list(RAWS_PATH.glob('*.RAF'))) if RAWS_PATH.exists() else 0,
                'extension': '*.RAF',
                'requires': 'External SSD',
            },
            'videos': {
                'available': SSD_PATH.exists(),
                'path': SSD_PATH,
                'remote_path': HOMELAB_HDD_VIDEOS_PATH,
                'local_count': len(list(SSD_PATH.glob('*.MOV'))) if SSD_PATH.exists() else 0,
                'extension': '*.MOV',
                'requires': 'External SSD',
            },
        }

        if check_remote:
            connection_method = None
            for key, info in result.items():
                remote_count, method = self._get_remote_file_count(
                    info['remote_path'],
                    info['extension']
                )
                info['remote_count'] = remote_count
                info['needs_sync'] = max(0, info['local_count'] - remote_count) if remote_count >= 0 else -1
                if remote_count >= 0:
                    connection_method = method

            result['_connection'] = connection_method

        return result

    def _run_backup_rclone(
        self,
        source_path: Path,
        remote_dest: Path,
        source_name: str,
        dry_run: bool = False,
        min_files: int = 0,
        file_pattern: str = '*',
        trash_base_path: Path = None,
        reporter: Optional[ProgressReporter] = None,
    ) -> Dict[str, any]:
        """
        Internal helper to run rclone backup with parallel transfers, trash-based deletion,
        and Rich Progress.

        Uses rclone sync over SFTP with --backup-dir to move deleted/replaced files to a
        timestamped trash folder instead of permanent deletion. 8 parallel transfers saturate
        fast LAN bandwidth over Tailscale.

        Args:
            source_path: Local source directory
            remote_dest: Remote destination path
            source_name: Name for trash folder (e.g., 'final', 'raws', 'videos')
            dry_run: If True, simulate only
            min_files: Minimum files required (safety check)
            file_pattern: Glob pattern to count files
            reporter: Progress reporter; defaults to RichReporter (identical CLI output).

        Returns:
            Dict with 'scanned', 'sync_successful', 'connection_method', 'trash_path', 'errors'
        """
        from rich.progress import Progress, SpinnerColumn, BarColumn, TextColumn, TimeElapsedColumn

        if reporter is None:
            reporter = RichReporter()

        stats = {
            'source': source_name,
            'scanned': 0,
            'sync_successful': False,
            'connection_method': None,
            'trash_path': None,
            'errors': 0,
        }

        if not source_path.exists():
            reporter.log("error", f"Source folder does not exist: {source_path}")
            return stats

        if shutil.which("rclone") is None:
            reporter.log("error", "rclone not found on PATH. Install with: brew install rclone")
            stats['errors'] += 1
            return stats

        try:
            files = list(source_path.glob(file_pattern))
            stats['scanned'] = len(files)

            if min_files > 0 and len(files) < min_files:
                reporter.log("warning", f"{source_name.title()} folder only has {len(files)} files. Expected {min_files}+.")
                reporter.log("warning", "This could indicate folder is empty or unmounted.")
                reporter.log("warning", "Backup aborted to prevent accidental deletion of remote files.")
                stats['errors'] += 1
                return stats
        except Exception as e:
            reporter.log("error", f"Failed to scan {source_name} folder: {e}")
            stats['errors'] += 1
            return stats

        timestamp = datetime.now().strftime("%Y-%m-%d_%H-%M")
        base = trash_base_path if trash_base_path is not None else HOMELAB_TRASH_PATH
        trash_folder = f"{base}/{source_name}_{timestamp}"
        stats['trash_path'] = trash_folder

        # rclone on-the-fly SFTP remote (no config file needed)
        remote_base = f':sftp,host="{HOMELAB_HOST}",user="{HOMELAB_USER}",ciphers="{RCLONE_SSH_CIPHER}":'
        dst = remote_base + str(remote_dest)
        trash_remote = remote_base + trash_folder
        src = str(source_path) + "/"  # trailing slash = sync contents

        cmd = [
            "rclone", "sync",
            "--sftp-key-use-agent",  # Use SSH_AUTH_SOCK (1Password agent). Must come before src/dst.
            f"--transfers={RCLONE_TRANSFERS}",
            f"--sftp-concurrency={RCLONE_SFTP_CONCURRENCY}",
            "--backup-dir", trash_remote,
            "-v",          # Required: without -v, rclone emits no stats to the pipe
            "--stats=1s",
            "--retries", "3",
            src, dst,
        ]
        for pattern in RSYNC_EXCLUDE_PATTERNS:
            cmd += ["--exclude", pattern]
        if dry_run:
            cmd.append("--dry-run")

        reporter.log("info", "Connecting via Tailscale...")

        # rclone -v --stats=1s produces multi-line blocks to stderr (merged via STDOUT).
        # Relevant line: "Transferred:\t   21.281 MiB / 40 MiB, 53%, 356.951 KiB/s, ETA 53s"
        # Without -v, rclone emits no stats at all when piped (non-TTY).

        try:
            # rclone's Go SFTP library doesn't read ~/.ssh/config, so it won't find the
            # 1Password SSH agent via IdentityAgent. Set SSH_AUTH_SOCK explicitly so
            # rclone can authenticate using the same agent as plain ssh.
            env = os.environ.copy()
            op_agent = os.path.expanduser(
                "~/Library/Group Containers/2BUA8C4S2C.com.1password/t/agent.sock"
            )
            if os.path.exists(op_agent):
                env["SSH_AUTH_SOCK"] = op_agent

            proc = subprocess.Popen(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                bufsize=1,  # Line buffered
                env=env,
            )

            with Progress(
                SpinnerColumn(),
                TextColumn("[cyan]{task.description}"),
                BarColumn(bar_width=30),
                TextColumn("[progress.percentage]{task.percentage:>3.0f}%"),
                TextColumn("•"),
                TextColumn("{task.fields[speed]:>10}"),
                TextColumn("•"),
                TextColumn("{task.fields[files]}"),
                TextColumn("•"),
                TextColumn("{task.fields[eta]}"),
                TextColumn("•"),
                TimeElapsedColumn(),
                transient=True,
            ) as progress:
                task = progress.add_task(
                    f"Syncing {source_name}...",
                    total=100,
                    speed="--",
                    files="--",
                    eta="--"
                )

                error_lines = []
                transfers_started = False
                current_files = "--"  # latest file-count string for transfer events

                for line in iter(proc.stdout.readline, ''):
                    if not line:
                        break

                    match = _RCLONE_PCT_RE.search(line)
                    if match:
                        if not transfers_started:
                            transfers_started = True
                            progress.update(task, description=f"Syncing {source_name}...")
                        pct, speed, eta = match.groups()
                        progress.update(
                            task,
                            completed=int(pct),
                            speed=speed,
                            eta=f"ETA: {eta}"
                        )
                        # Emit structured event for the SSE stream (no-op for RichReporter).
                        reporter.event("transfer", {
                            "pct": int(pct),
                            "speed": speed,
                            "eta": eta,
                            "files": current_files,
                        })
                    else:
                        files_match = _RCLONE_FILES_RE.search(line)
                        if files_match:
                            done, total_files = files_match.groups()
                            current_files = f"{done}/{total_files}"
                            progress.update(task, files=f"{current_files} files")
                        if not transfers_started:
                            progress.update(task, description=f"Checking {source_name} files...")
                        stripped = line.strip()
                        if stripped:
                            error_lines.append(stripped)

                proc.wait()

            if proc.returncode == 0:
                stats['sync_successful'] = True
                stats['connection_method'] = 'tailscale'
                reporter.log("info", f"[green]✓[/green] {source_name.title()} backup completed via Tailscale")
                return stats
            else:
                stats['errors'] += 1
                reporter.log("error", f"rclone failed (exit code: {proc.returncode})")
                for err_line in error_lines[-5:]:
                    reporter.log("error", f"  {err_line}")

        except Exception as e:
            stats['errors'] += 1
            reporter.log("error", f"Backup failed: {e}")

        return stats

    def backup_raws_to_homelab(
        self,
        dry_run: bool = False,
        reporter: Optional[ProgressReporter] = None,
        progress_callback=None,
    ) -> Dict[str, any]:
        """
        Backup the RAWs folder to homelab HDD via rclone with parallel transfers.

        Args:
            dry_run: If True, simulate only
            reporter: Progress reporter; defaults to RichReporter (identical CLI output).
            progress_callback: Ignored — kept for call-site compatibility.

        Returns:
            Dict with backup stats
        """
        if reporter is None:
            reporter = RichReporter()

        if not RAWS_PATH.exists():
            reporter.log("error", f"RAWs folder not available at {RAWS_PATH}")
            reporter.log("error", "External SSD must be connected for RAWs backup")
            return {'source': 'raws', 'scanned': 0, 'sync_successful': False, 'errors': 1}

        return self._run_backup_rclone(
            source_path=RAWS_PATH,
            remote_dest=HOMELAB_HDD_RAWS_PATH,
            source_name='raws',
            dry_run=dry_run,
            min_files=100,
            file_pattern='*.RAF',
            reporter=reporter,
        )

    def backup_videos_to_homelab(
        self,
        dry_run: bool = False,
        reporter: Optional[ProgressReporter] = None,
        progress_callback=None,
    ) -> Dict[str, any]:
        """
        Backup the Videos folder to homelab HDD via rclone with parallel transfers.

        Args:
            dry_run: If True, simulate only
            reporter: Progress reporter; defaults to RichReporter (identical CLI output).
            progress_callback: Ignored — kept for call-site compatibility.

        Returns:
            Dict with backup stats
        """
        if reporter is None:
            reporter = RichReporter()

        if not SSD_PATH.exists():
            reporter.log("error", f"Videos folder not available at {SSD_PATH}")
            reporter.log("error", "External SSD must be connected for Videos backup")
            return {'source': 'videos', 'scanned': 0, 'sync_successful': False, 'errors': 1}

        return self._run_backup_rclone(
            source_path=SSD_PATH,
            remote_dest=HOMELAB_HDD_VIDEOS_PATH,
            source_name='videos',
            dry_run=dry_run,
            min_files=10,
            file_pattern='*.MOV',
            reporter=reporter,
        )
