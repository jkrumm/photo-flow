"""
Command-line interface for the Photo-Flow application.

This module provides the CLI commands for the application.
"""

import click
import logging

# The install config is validated at import of `photo_flow.config`, and a bad one is fatal
# by design — it names the directories the destructive operations run against. Catching it
# HERE, before anything that pulls config in, turns a traceback into one actionable line.
# This is the first thing the module does for that reason; do not move it below the import
# of `workflow`, which imports config transitively.
from photo_flow.library_config import LibraryConfigError

try:
    from photo_flow import config as _config  # noqa: F401
except LibraryConfigError as _config_error:  # pragma: no cover - needs a broken file on disk
    from rich.console import Console

    Console(stderr=True).print(
        f"[red]✗ photo-flow will not start with this configuration:[/red]\n  {_config_error}\n"
        "  Fix the file, or move it aside to fall back to the built-in defaults."
    )
    raise SystemExit(2)

from photo_flow.workflow import PhotoWorkflow
from photo_flow.console_utils import console, success, error, info, warning, print_summary
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

    # Keep the panel's culling view in step with what just landed in Staging. Incremental and
    # cheap (~1.3 s for 679 new rows against a 3 800-row index), and best-effort on purpose:
    # the photos are already safely imported and verified, so a reindex failure must not be
    # reported as a failed import. Mirrors `_with_reindex` on the API's import job.
    if not dry_run and stats['photos'] > 0:
        try:
            from photo_flow.index.indexer import reindex
            reindex()
        except Exception as exc:  # noqa: BLE001 - the import itself already succeeded
            warning(f"Index refresh failed ({exc}) - new photos stay hidden until the next reindex")


@photoflow.command()
@click.option('--dry-run', is_flag=True, help='Simulate finalization without moving files')
def finalize(dry_run):
    """Finalize the staging process by moving approved photos to the final folder and cleaning up orphaned RAW files."""
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
        "Files moved to Final folder (full quality)": stats['moved'],
        "Edit sidecars moved (.photo-edit)": stats.get('edits_moved', 0),
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
    """Backup photos, RAWs, and videos to homelab via rsync with interactive selection."""
    from rich.panel import Panel
    from photo_flow.console_utils import show_status

    workflow = PhotoWorkflow()

    # Get backup availability with remote check
    with show_status("Checking homelab connection...", spinner="dots"):
        availability = workflow.get_backup_availability(check_remote=True)

    connection = availability.pop('_connection', None)
    remote_unreachable = connection is None

    # Build status display
    status_lines = []
    for key, info_data in availability.items():
        if info_data['available']:
            local = info_data['local_count']
            remote = info_data.get('remote_count', -1)
            needs = info_data.get('needs_sync', -1)

            if remote >= 0:
                if needs > 0:
                    status_lines.append(
                        f"  [yellow]![/yellow] {key.upper():8s} {local:,} local │ {remote:,} remote │ [yellow]~{needs:,} new[/yellow]"
                    )
                else:
                    status_lines.append(
                        f"  [green]✓[/green] {key.upper():8s} {local:,} local │ {remote:,} remote │ [green]synced[/green]"
                    )

                # needs_sync already includes these; break them out so a sidecar-only
                # change reads as an edit-history gap rather than a mystery count.
                sidecars_behind = info_data.get('sidecar_needs_sync') or 0
                if sidecars_behind > 0:
                    status_lines.append(
                        f"    {'':8s} [dim]└ {sidecars_behind:,} .photo-edit sidecar(s) pending[/dim]"
                    )
            else:
                status_lines.append(f"  [yellow]~[/yellow] {key.upper():8s} {local:,} files ready [dim](remote unreachable)[/dim]")
        else:
            requires = info_data.get('requires', 'Unknown')
            status_lines.append(f"  [red]✗[/red] {key.upper():8s} {requires} not connected")

    status_text = "\n".join(status_lines)
    title = "Backup Status"
    if connection:
        method_desc = "IPv6" if connection == "direct" else "IPv4 via VPS"
        title += f" [dim]({method_desc})[/dim]"
    border = "yellow" if remote_unreachable else "cyan"
    console.print(Panel(status_text, title=title, border_style=border))

    if remote_unreachable:
        warning("Could not reach homelab — check Tailscale (`tailscale status`) before proceeding")

    # Build menu options based on availability
    available_sources = [k for k, v in availability.items() if v['available']]

    if not available_sources:
        error("No backup sources available. Connect required drives and try again.")
        return

    # Menu options
    options = []

    # Option 1: All available
    if len(available_sources) > 1:
        all_desc = " → ".join([s.upper() for s in ['final', 'raws', 'videos'] if s in available_sources])
        options.append(('all', f"All available ({all_desc})"))

    # Individual options
    for source in ['final', 'raws', 'videos']:
        avail = availability[source]
        if avail['available']:
            label = {'final': 'JPEGs only (Final folder)', 'raws': 'RAWs only', 'videos': 'Videos only'}[source]
            options.append((source, label))
        else:
            requires = avail.get('requires', 'Unknown')
            label = {'final': 'JPEGs', 'raws': 'RAWs', 'videos': 'Videos'}[source]
            options.append((None, f"{label} (unavailable - {requires} not connected)"))

    # Optional Staging mirror — last, and never folded into "all"
    if availability.get('staging', {}).get('available'):
        options.append(('staging', 'Staging mirror (optional safety copy, no trash)'))

    # Display menu
    console.print("\n[bold]Select what to backup:[/bold]")
    for i, (key, label) in enumerate(options, 1):
        if key is None:
            console.print(f"  [dim]{i}. {label}[/dim]")
        else:
            console.print(f"  {i}. {label}")

    # Get user choice
    console.print()
    try:
        choice = click.prompt("Enter choice", type=int, default=1)
    except click.Abort:
        console.print("\n[yellow]Aborted.[/yellow]")
        return

    if choice < 1 or choice > len(options):
        error(f"Invalid choice: {choice}")
        return

    selected_key, selected_label = options[choice - 1]

    if selected_key is None:
        error("Selected source is not available. Connect required drives and try again.")
        return

    console.print()
    if dry_run:
        info("[yellow]DRY RUN:[/yellow] Simulating backup to homelab (no remote changes)")

    # Determine which sources to backup
    if selected_key == 'all':
        sources_to_backup = [s for s in ['final', 'raws', 'videos'] if s in available_sources]
    else:
        sources_to_backup = [selected_key]

    # Run backups in order
    all_stats = []
    total_errors = 0

    for source in sources_to_backup:
        console.print(f"\n[bold cyan]Backing up {source.upper()}...[/bold cyan]")

        if source == 'final':
            stats = workflow.backup_final_to_homelab(dry_run=dry_run)
        elif source == 'raws':
            stats = workflow.backup_raws_to_homelab(dry_run=dry_run)
        elif source == 'videos':
            stats = workflow.backup_videos_to_homelab(dry_run=dry_run)
        elif source == 'staging':
            stats = workflow.backup_staging_to_homelab(dry_run=dry_run)

        all_stats.append(stats)
        total_errors += stats.get('errors', 0)

    # Print summary for each backup
    console.print()
    for stats in all_stats:
        source = stats.get('source', 'unknown')
        results = {
            f"Files scanned in {source.title()}": stats.get('scanned', 0)
        }

        if not dry_run:
            results["Backup successful"] = "Yes" if stats.get('sync_successful') else "No"
            if stats.get('connection_method'):
                method_desc = "Tailscale"
                results["Connection method"] = method_desc
            if stats.get('trash_path'):
                results["Trash location"] = stats['trash_path']
        else:
            results["Backup status"] = "Would be performed (dry run)"

        if stats.get('errors', 0) > 0:
            results["Errors encountered"] = stats['errors']

        print_summary(f"{source.title()} Backup Results", results)

    # Final status
    if total_errors == 0:
        success("All backups completed successfully!")
    else:
        error(f"Backups completed with {total_errors} total errors")


@photoflow.command()
@click.option('--host', default='127.0.0.1', show_default=True, help='Host to bind (localhost only)')
@click.option('--port', default=7717, show_default=True, help='Port for the control panel')
def serve(host: str, port: int) -> None:
    """Start the web control panel at http://localhost:7717."""
    import uvicorn
    from photo_flow.api.app import app as _app

    info(f"Starting Photo-Flow control panel at [cyan]http://{host}:{port}[/cyan]")
    info("Open [cyan]http://localhost:7717[/cyan] in your browser (or install as PWA)")
    uvicorn.run(_app, host=host, port=port)


def _human_bytes(num_bytes: int) -> str:
    """Format a byte count for terminal output (e.g. 1536 → '1.5 KB')."""
    size = float(num_bytes)
    for unit in ('B', 'KB', 'MB', 'GB', 'TB'):
        if abs(size) < 1024.0 or unit == 'TB':
            return f"{size:.1f} {unit}" if unit != 'B' else f"{int(size)} B"
        size /= 1024.0
    return f"{size:.1f} TB"


@photoflow.group(name='config')
def config_group():
    """Inspect the library configuration (paths, cameras, organisation)."""
    pass


@config_group.command(name='show')
def config_show():
    """Print the resolved configuration and where each half of it came from."""
    from photo_flow import library_config

    install = _config.INSTALL
    organisation = _config.ORGANISATION

    console.print("\n[bold]Install[/bold] — where things are on this machine")
    console.print(f"  [dim]{install.path}[/dim]"
                  f"{'' if install.present else '  [yellow](no file — built-in defaults)[/yellow]'}")
    roots_table = Table(show_header=True, header_style="bold cyan")
    roots_table.add_column("Root", style="dim")
    roots_table.add_column("Path")
    roots_table.add_column("On disk", justify="center")
    for key in library_config.ROOT_KEYS:
        path = install.roots[key]
        mark = "[green]✓[/green]" if path.exists() else "[dim]—[/dim]"
        roots_table.add_row(key, str(path), mark)
    console.print(roots_table)

    camera_table = Table(show_header=True, header_style="bold cyan")
    camera_table.add_column("Camera", style="dim")
    camera_table.add_column("Volume")
    camera_table.add_column("Extensions")
    camera_table.add_column("Connected", justify="center")
    active = install.active_camera()
    for camera in install.cameras:
        marker = " [cyan](active)[/cyan]" if camera.id == active.id else ""
        camera_table.add_row(
            f"{camera.name}{marker}",
            str(camera.camera_path),
            " ".join(camera.extensions),
            "[green]✓[/green]" if camera.is_connected() else "[dim]—[/dim]",
        )
    console.print(camera_table)

    console.print("\n[bold]Library[/bold] — how these photographs are arranged")
    console.print(f"  [dim]{organisation.path}[/dim]"
                  f"{'' if organisation.present else '  [yellow](no settings — defaults)[/yellow]'}")
    axes = Table(show_header=True, header_style="bold cyan")
    axes.add_column("Axis", style="dim")
    axes.add_column("Value")
    axes.add_row("stage.mode", organisation.stage.mode)
    axes.add_row("stage.tag", organisation.stage.tag)
    axes.add_row("layout.mode", organisation.layout.mode)
    axes.add_row("naming.template", organisation.naming.template)
    axes.add_row("naming.apply", organisation.naming.apply)
    console.print(axes)

    for message in organisation.errors:
        error(message)
    if organisation.unimplemented:
        warning("Set, but no operation reads it yet — restage and relayout are not built:")
        for item in organisation.unimplemented:
            console.print(f"    {item}")
    console.print()


@config_group.command(name='check')
def config_check():
    """Validate both configuration files. Exits non-zero when something is wrong."""
    problems = list(_config.ORGANISATION.errors)
    if problems:
        for message in problems:
            error(message)
        raise SystemExit(1)
    # Reaching this line at all means the install file already parsed and validated:
    # `photo_flow.config` refuses to import otherwise.
    success(f"Configuration is valid ({_config.INSTALL.path}, {_config.ORGANISATION.path})")


@config_group.command(name='init')
def config_init():
    """Write a commented install config file holding exactly the current defaults."""
    from photo_flow import library_config

    try:
        written = library_config.write_install_template()
    except library_config.LibraryConfigError as exc:
        error(str(exc))
        raise SystemExit(1)
    success(f"Wrote {written}")
    info("Every value in it is the current default, so nothing changes until you edit it.")


@photoflow.group(name='trash')
def trash_group():
    """Manage culled photos in the soft-delete trash (restore, purge, stats)."""
    pass


@trash_group.command(name='list')
@click.option('--limit', default=100, show_default=True, help='Maximum number of entries to list')
def trash_list(limit):
    """List trashed photos, newest first."""
    from photo_flow import trash as trash_mod

    entries = trash_mod.list_trash(limit=limit)

    if not entries:
        info("Trash is empty.")
        return

    table = Table(show_header=True, header_style="bold cyan")
    table.add_column("ID", justify="right", style="dim")
    table.add_column("Filename")
    table.add_column("From", style="dim")
    table.add_column("Rating", justify="right")
    table.add_column("Age", justify="right")
    table.add_column("Size", justify="right")
    table.add_column("Edit history", justify="center")

    for entry in entries:
        rating = entry.get('rating')
        age = f"{entry['age_days']:.1f}d"
        age_cell = f"[yellow]{age}[/yellow]" if entry['purgeable'] else age
        name_cell = entry['filename'] if entry['exists'] else f"[red]{entry['filename']} (missing)[/red]"
        table.add_row(
            str(entry['id']),
            name_cell,
            entry['root'],
            "-" if rating is None else str(rating),
            age_cell,
            _human_bytes(entry['size']),
            "✓" if entry.get('sidecar_trashed_path') else "-",
        )

    console.print()
    console.print(table)
    console.print(f"\n[dim]Entries marked yellow are past the {trash_mod.TRASH_RETENTION_DAYS}-day retention "
                  f"and can be purged.[/dim]\n")


@trash_group.command(name='restore')
@click.argument('entry_ids', nargs=-1, required=True, type=int)
def trash_restore(entry_ids):
    """Restore trashed photos (and their .photo-edit sidecars) by entry ID."""
    from photo_flow import trash as trash_mod

    stats = trash_mod.restore(list(entry_ids))

    for message in stats['messages']:
        warning(message)

    if stats['errors'] == 0:
        success("Restore completed successfully!")
    else:
        error(f"Restore completed with {stats['errors']} errors")

    print_summary("Restore Results", {
        "Photos restored": stats['restored'],
        **({"Errors encountered": stats['errors']} if stats['errors'] > 0 else {})
    })


@trash_group.command(name='purge')
@click.option('--days', default=None, type=int,
              help='Minimum age in days (default: the configured retention period)')
@click.option('--dry-run', is_flag=True, help='Show what would be purged without deleting anything')
def trash_purge(days, dry_run):
    """Permanently delete trashed photos past the retention period."""
    from photo_flow import trash as trash_mod

    threshold = trash_mod.TRASH_RETENTION_DAYS if days is None else days

    if dry_run:
        info("[yellow]DRY RUN:[/yellow] Simulating trash purge (no files will be deleted)")

    # Always preview first — this is the one command in the trash group that destroys data.
    preview = trash_mod.purge(older_than_days=threshold, dry_run=True)

    console.print(f"[bold]Trash Purge Preview[/bold] (older than {threshold} days):")
    console.print(f"  Entries eligible: [cyan]{preview['purged']}[/cyan]")
    console.print(f"  Space to reclaim: [cyan]{_human_bytes(preview['bytes'])}[/cyan]")

    if dry_run or preview['purged'] == 0:
        if preview['purged'] == 0:
            info("Nothing to purge.")
        return

    if not click.confirm(
        f"Permanently delete {preview['purged']} trashed photo(s)? This cannot be undone.",
        default=False
    ):
        console.print("[yellow]Aborted.[/yellow] No files were deleted.")
        return

    stats = trash_mod.purge(older_than_days=threshold, dry_run=False)

    if stats['errors'] == 0:
        success("Trash purge completed successfully!")
    else:
        error(f"Trash purge completed with {stats['errors']} errors")

    print_summary("Trash Purge Results", {
        "Entries purged": stats['purged'],
        "Space reclaimed": _human_bytes(stats['bytes']),
        **({"Errors encountered": stats['errors']} if stats['errors'] > 0 else {})
    })


@trash_group.command(name='stats')
def trash_stats_cmd():
    """Show trash size, entry count, and how much is past retention."""
    from photo_flow import trash as trash_mod

    stats = trash_mod.trash_stats()

    print_summary("Trash Status", {
        "Entries": stats['count'],
        "Space used": _human_bytes(stats['bytes']),
        "Oldest entry": stats['oldest_iso'] or "-",
        f"Past retention ({trash_mod.TRASH_RETENTION_DAYS}d)": stats['purgeable'],
    })
    console.print()


@photoflow.group()
def service():
    """Manage the always-on control panel LaunchAgent (localhost:7717)."""
    pass


@service.command(name='install')
@click.option('--no-build', is_flag=True, help='Skip building the SPA (use existing dist/)')
@click.option('--host', default='127.0.0.1', show_default=True, help='Host to bind (localhost only)')
@click.option('--port', default=7717, show_default=True, help='Port for the control panel')
def service_install(no_build, host, port):
    """Build the SPA, install the LaunchAgent, and start it."""
    from photo_flow import service as svc
    svc.install(build=not no_build, host=host, port=port)


@service.command(name='uninstall')
def service_uninstall():
    """Stop and remove the LaunchAgent."""
    from photo_flow import service as svc
    svc.uninstall()


@service.command(name='status')
def service_status():
    """Show whether the control panel service is installed and running."""
    from photo_flow import service as svc
    svc.status()


@service.command(name='restart')
def service_restart():
    """Reload the service (pick up a new SPA build or code change)."""
    from photo_flow import service as svc
    svc.restart()


if __name__ == '__main__':
    photoflow()
