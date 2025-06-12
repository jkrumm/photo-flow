"""
Command-line interface for the Photo-Flow application.

This module provides the CLI commands for the application.
"""

import click

from photo_flow.workflow import PhotoWorkflow


@click.group()
@click.version_option()
def photoflow():
    """
    Photo-Flow: CLI tool for managing Fuji X-T4 camera photos/videos.

    This tool provides a workflow for JPG photography with RAW backups.
    """
    pass


@photoflow.command()
def status():
    """Check the current status of the workflow."""
    workflow = PhotoWorkflow()
    report = workflow.get_status()

    click.echo("Photo-Flow Status Report")
    click.echo("======================")
    click.echo(f"Camera connected: {report.camera_connected}")
    click.echo(f"SSD connected: {report.ssd_connected}")

    if report.camera_connected:
        click.echo("\nPending files on camera:")
        click.echo(f"  Videos (.MOV): {report.pending_videos}")
        click.echo(f"  Photos (.JPG): {report.pending_photos}")
        click.echo(f"  RAW files (.RAF): {report.pending_raws}")

    click.echo(f"\nStaging status: {report.staging_files} files ready for review")


@photoflow.command(name='import')
@click.option('--dry-run', is_flag=True, help='Simulate import without copying files')
def import_cmd(dry_run):
    """Import files from the camera to the appropriate locations."""
    workflow = PhotoWorkflow()

    if dry_run:
        click.echo("DRY RUN: Simulating import (no files will be copied)")

    # Define progress callback function
    def progress_callback(message):
        # Clear the current line and print the progress message
        click.echo(f"\r\033[K{message}", nl=False)

    # Call import_from_camera with progress callback
    stats = workflow.import_from_camera(dry_run=dry_run, progress_callback=progress_callback)

    # Print a newline to ensure results start on a new line
    click.echo("\n")

    click.echo("Import Results:")
    click.echo(f"  Videos copied to SSD: {stats['videos']}")
    click.echo(f"  Photos copied to Staging: {stats['photos']}")
    click.echo(f"  RAW files backed up: {stats['raws']}")
    click.echo(f"  Files skipped (already exist): {stats['skipped']}")

    if stats['errors'] > 0:
        click.echo(f"  Errors encountered: {stats['errors']}")


@photoflow.command()
@click.option('--dry-run', is_flag=True, help='Simulate finalization without moving files')
def finalize(dry_run):
    """Finalize the staging process by moving approved photos to the final folder, copying them back to camera, and cleaning up orphaned RAW files."""
    workflow = PhotoWorkflow()

    if dry_run:
        click.echo("DRY RUN: Simulating finalization (no files will be moved, copied, or deleted)")

    # Define progress callback function
    def progress_callback(message):
        # Clear the current line and print the progress message
        click.echo(f"\r\033[K{message}", nl=False)

    # Call finalize_staging with progress callback
    stats = workflow.finalize_staging(dry_run=dry_run, progress_callback=progress_callback)

    # Print a newline to ensure results start on a new line
    click.echo("\n")

    click.echo("Finalization Results:")
    click.echo(f"  Files moved to Final folder: {stats['moved']}")
    click.echo(f"  Files copied back to camera: {stats['copied_to_camera']}")
    click.echo(f"  Orphaned RAW files found: {stats['orphaned_raws']}")
    click.echo(f"  Orphaned RAW files deleted: {stats['deleted_raws']}")
    click.echo(f"  Files skipped (already exist): {stats['skipped']}")

    if stats['errors'] > 0:
        click.echo(f"  Errors encountered: {stats['errors']}")


@photoflow.command()
@click.option('--dry-run', is_flag=True, help='Simulate gallery sync without copying files')
def sync_gallery(dry_run):
    """Sync high-rated photos to gallery and generate metadata JSON."""
    workflow = PhotoWorkflow()

    if dry_run:
        click.echo("DRY RUN: Simulating gallery sync (no files will be copied or removed)")

    # Define progress callback function
    def progress_callback(message):
        # Clear the current line and print the progress message
        click.echo(f"\r\033[K{message}", nl=False)

    # Call sync_gallery with progress callback
    stats = workflow.sync_gallery(dry_run=dry_run, progress_callback=progress_callback)

    # Print a newline to ensure results start on a new line
    click.echo("\n")

    click.echo("Gallery Sync Results:")
    click.echo(f"  Images synced to gallery: {stats['synced']}")
    click.echo(f"  Images removed from gallery: {stats['removed']}")
    click.echo(f"  Metadata JSON updated: {'Yes' if stats['json_updated'] else 'No'}")
    click.echo(f"  Total images in gallery: {stats['total_in_gallery']}")

    # Display build and sync status if available
    if 'build_successful' in stats:
        if not dry_run:
            click.echo(f"  Gallery build: {'Successful' if stats['build_successful'] else 'Failed'}")
            click.echo(f"  Remote sync: {'Successful' if stats['sync_successful'] else 'Failed'}")
        else:
            click.echo("  Gallery build and remote sync: Would be performed (dry run)")

    if stats['errors'] > 0:
        click.echo(f"  Errors encountered: {stats['errors']}")


if __name__ == '__main__':
    photoflow()
