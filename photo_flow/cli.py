"""
Command-line interface for the Photo-Flow application.

This module provides the CLI commands for the application.
"""

import click
import logging

from photo_flow.workflow import PhotoWorkflow
from photo_flow.console_utils import console, success, error, info, print_summary
from rich.table import Table

# Configure logging to be silent except for errors
logging.basicConfig(
    level=logging.ERROR,  # Only show errors, not debug/info
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)


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

    console.print("\n[bold]Photo-Flow Status Report[/bold]\n")

    # Create status table
    table = Table(show_header=True, header_style="bold cyan")
    table.add_column("Component", style="dim")
    table.add_column("Status")

    # Add connection status
    camera_status = "[green]✓ Connected[/green]" if report.camera_connected else "[red]✗ Not Connected[/red]"
    ssd_status = "[green]✓ Connected[/green]" if report.ssd_connected else "[red]✗ Not Connected[/red]"

    table.add_row("Camera", camera_status)
    table.add_row("External SSD", ssd_status)

    console.print(table)

    if report.camera_connected:
        console.print("\n[bold]Pending files on camera:[/bold]")
        pending_table = Table(show_header=False)
        pending_table.add_column("Type", style="dim")
        pending_table.add_column("Count", justify="right", style="cyan")

        pending_table.add_row("Videos (.MOV)", str(report.pending_videos))
        pending_table.add_row("Photos (.JPG)", str(report.pending_photos))
        pending_table.add_row("RAW files (.RAF)", str(report.pending_raws))

        console.print(pending_table)

    console.print(f"\n[bold]Staging status:[/bold] [cyan]{report.staging_files}[/cyan] files ready for review\n")


@photoflow.command(name='import')
@click.option('--dry-run', is_flag=True, help='Simulate import without copying files')
def import_cmd(dry_run):
    """Import files from the camera to the appropriate locations."""
    workflow = PhotoWorkflow()

    if dry_run:
        info("[yellow]DRY RUN:[/yellow] Simulating import (no files will be copied)")

    # Call import_from_camera (uses Rich Progress internally)
    stats = workflow.import_from_camera(dry_run=dry_run)

    # Print summary
    if stats['errors'] == 0:
        success("Import completed successfully!")
    else:
        error(f"Import completed with {stats['errors']} errors")

    print_summary("Import Results", {
        "Videos copied to SSD": stats['videos'],
        "Photos copied to Staging": stats['photos'],
        "RAW files backed up": stats['raws'],
        "Files skipped (already exist)": stats['skipped'],
        **({"Errors encountered": stats['errors']} if stats['errors'] > 0 else {})
    })


@photoflow.command()
@click.option('--dry-run', is_flag=True, help='Simulate finalization without moving files')
def finalize(dry_run):
    """Finalize the staging process by moving approved photos to the final folder, copying them back to camera, and cleaning up orphaned RAW files."""
    workflow = PhotoWorkflow()

    if dry_run:
        info("[yellow]DRY RUN:[/yellow] Simulating finalization (no files will be moved, copied, or deleted)")

    # Call finalize_staging (uses Rich Progress internally)
    stats = workflow.finalize_staging(dry_run=dry_run)

    # Print summary
    if stats['errors'] == 0:
        success("Finalization completed successfully!")
    else:
        error(f"Finalization completed with {stats['errors']} errors")

    print_summary("Finalization Results", {
        "Files moved to Final folder": stats['moved'],
        "Files compressed (5200×3467, Q92, 4:4:4)": stats.get('compressed', 0),
        "Files copied back to camera": stats['copied_to_camera'],
        "Orphaned RAW files found": stats['orphaned_raws'],
        "Orphaned RAW files deleted": stats['deleted_raws'],
        "RAW files deleted from camera": stats['deleted_camera_raws'],
        "Files skipped (already exist)": stats['skipped'],
        **({"Errors encountered": stats['errors']} if stats['errors'] > 0 else {})
    })


@photoflow.command()
@click.option('--dry-run', is_flag=True, help='Simulate gallery sync without copying files')
def sync_gallery(dry_run):
    """Sync high-rated photos to gallery and generate metadata JSON."""
    workflow = PhotoWorkflow()

    if dry_run:
        info("[yellow]DRY RUN:[/yellow] Simulating gallery sync (no files will be copied or removed)")

    # Use Rich Progress directly instead of verbose callbacks
    stats = workflow.sync_gallery(dry_run=dry_run, progress_callback=None)

    # Print summary
    if stats['errors'] == 0:
        success("Gallery sync completed successfully!")
    else:
        error(f"Gallery sync completed with {stats['errors']} errors")

    results = {
        "Images synced to gallery": stats['synced'],
        "Images unchanged (already up-to-date)": stats.get('unchanged', 0),
        "Images removed from gallery": stats['removed'],
        "Metadata JSON updated": "Yes" if stats['json_updated'] else "No",
        "Total images in gallery": stats['total_in_gallery']
    }

    # Add build and sync status
    if 'build_successful' in stats:
        if not dry_run:
            results["Gallery build"] = "Successful" if stats['build_successful'] else "Failed"
            results["Remote sync"] = "Successful" if stats['sync_successful'] else "Failed"
        else:
            results["Gallery build and remote sync"] = "Would be performed (dry run)"

    if stats['errors'] > 0:
        results["Errors encountered"] = stats['errors']

    print_summary("Gallery Sync Results", results)


@photoflow.command()
@click.option('--dry-run', is_flag=True, help='Simulate RAW cleanup without deleting files')
def cleanup(dry_run):
    """Remove unused RAW files that don't have corresponding JPGs in the Final folder."""
    workflow = PhotoWorkflow()

    if dry_run:
        info("[yellow]DRY RUN:[/yellow] Simulating RAW cleanup (no files will be deleted)")

    # Always preview first to show what would be deleted (uses Rich Progress internally)
    preview_stats = workflow.cleanup_unused_raws(dry_run=True)

    console.print("[bold]RAW Cleanup Preview:[/bold]")
    console.print(f"  Orphaned RAW files found: [cyan]{preview_stats['orphaned']}[/cyan]")

    if dry_run or preview_stats['orphaned'] == 0:
        if preview_stats['orphaned'] == 0:
            info("Nothing to delete.")
        return

    # Ask for confirmation before deleting
    if not click.confirm(f"Delete {preview_stats['orphaned']} orphaned RAW files?", default=False):
        console.print("[yellow]Aborted.[/yellow] No files were deleted.")
        return

    # Perform deletion (uses Rich Progress internally)
    stats = workflow.cleanup_unused_raws(dry_run=False)

    if stats['errors'] == 0:
        success("RAW cleanup completed successfully!")
    else:
        error(f"RAW cleanup completed with {stats['errors']} errors")

    print_summary("RAW Cleanup Results", {
        "Orphaned RAW files deleted": stats['deleted'],
        **({"Errors encountered": stats['errors']} if stats['errors'] > 0 else {})
    })


@photoflow.command(name='backup')
@click.option('--dry-run', is_flag=True, help='Simulate backup without transferring files')
def backup(dry_run):
    """Backup the Final folder to homelab via rsync."""
    workflow = PhotoWorkflow()

    if dry_run:
        info("[yellow]DRY RUN:[/yellow] Simulating backup to homelab (no remote changes)")

    # Call backup_final_to_homelab (uses Rich console internally, rsync output flows through)
    stats = workflow.backup_final_to_homelab(dry_run=dry_run)

    # Print summary
    if stats.get('sync_successful'):
        success("Backup completed successfully!")
    elif stats.get('errors', 0) > 0:
        error(f"Backup failed with {stats['errors']} errors")

    results = {
        "Files scanned in Final": stats.get('scanned', 0)
    }

    if not dry_run:
        results["Backup successful"] = "Yes" if stats.get('sync_successful') else "No"
        if stats.get('connection_method'):
            method_desc = "direct IPv6" if stats['connection_method'] == "direct" else "ProxyJump via VPS (IPv4)"
            results["Connection method"] = method_desc
    else:
        results["Backup status"] = "Would be performed (dry run)"

    if stats.get('errors', 0) > 0:
        results["Errors encountered"] = stats['errors']

    print_summary("Backup Results", results)


if __name__ == '__main__':
    photoflow()
