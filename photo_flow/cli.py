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

    stats = workflow.import_from_camera(dry_run=dry_run)

    click.echo("\nImport Results:")
    click.echo(f"  Videos copied to SSD: {stats['videos']}")
    click.echo(f"  Photos copied to Staging: {stats['photos']}")
    click.echo(f"  RAW files backed up: {stats['raws']}")

    if stats['errors'] > 0:
        click.echo(f"  Errors encountered: {stats['errors']}")


@photoflow.command()
@click.option('--dry-run', is_flag=True, help='Simulate finalization without moving files')
def finalize(dry_run):
    """Finalize the staging process by moving approved photos to the final folder."""
    workflow = PhotoWorkflow()

    if dry_run:
        click.echo("DRY RUN: Simulating finalization (no files will be moved)")

    stats = workflow.finalize_staging(dry_run=dry_run)

    click.echo("\nFinalization Results:")
    click.echo(f"  Files moved to Final folder: {stats['moved']}")

    if stats['errors'] > 0:
        click.echo(f"  Errors encountered: {stats['errors']}")


@photoflow.command()
@click.option('--dry-run', is_flag=True, help='Simulate cleanup without deleting files')
@click.confirmation_option(prompt='Are you sure you want to delete orphaned RAW files?')
def cleanup(dry_run):
    """Clean up unused RAW files that don't have corresponding JPGs in the final folder."""
    workflow = PhotoWorkflow()

    if dry_run:
        click.echo("DRY RUN: Simulating cleanup (no files will be deleted)")

    stats = workflow.cleanup_unused_raws(dry_run=dry_run)

    click.echo("\nCleanup Results:")
    click.echo(f"  Orphaned RAW files found: {stats['orphaned']}")
    click.echo(f"  RAW files deleted: {stats['deleted']}")

    if stats['errors'] > 0:
        click.echo(f"  Errors encountered: {stats['errors']}")


if __name__ == '__main__':
    photoflow()
