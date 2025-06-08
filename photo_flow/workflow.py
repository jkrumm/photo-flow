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
    
    def import_from_camera(self, dry_run: bool = False) -> Dict[str, int]:
        """
        Import files from the camera to the appropriate locations.
        
        Args:
            dry_run (bool): If True, only simulate the import without copying files
            
        Returns:
            Dict[str, int]: Statistics about the import operation
        """
        # This is just a skeleton implementation
        stats = {
            'videos': 0,
            'photos': 0,
            'raws': 0,
            'errors': 0
        }
        
        # Implementation would go here
        
        return stats
    
    def finalize_staging(self, dry_run: bool = False) -> Dict[str, int]:
        """
        Finalize the staging process by moving approved photos to the final folder.
        
        Args:
            dry_run (bool): If True, only simulate the finalization without moving files
            
        Returns:
            Dict[str, int]: Statistics about the finalization operation
        """
        # This is just a skeleton implementation
        stats = {
            'moved': 0,
            'errors': 0
        }
        
        # Implementation would go here
        
        return stats
    
    def cleanup_unused_raws(self, dry_run: bool = False) -> Dict[str, int]:
        """
        Clean up unused RAW files that don't have corresponding JPGs in the final folder.
        
        Args:
            dry_run (bool): If True, only simulate the cleanup without deleting files
            
        Returns:
            Dict[str, int]: Statistics about the cleanup operation
        """
        # This is just a skeleton implementation
        stats = {
            'orphaned': 0,
            'deleted': 0,
            'errors': 0
        }
        
        # Implementation would go here
        
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