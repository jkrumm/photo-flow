#!/usr/bin/env python3
"""
One-time script to re-compress all existing JPGs in Final folder with new settings.

This script will:
1. Find all JPGs in Final folder
2. Re-compress each with new settings (5200×3467, Q92, 4:4:4 chroma)
3. Preserve ALL metadata using exiftool
4. Use safety-first approach (backup/restore)

Run with --dry-run first to preview!
"""

import click
from pathlib import Path
from photo_flow.config import FINAL_PATH
from photo_flow.image_processor import ImageProcessor
from photo_flow.file_manager import scan_for_images


@click.command()
@click.option('--dry-run', is_flag=True, help='Preview without actually compressing files')
def recompress_final(dry_run):
    """Re-compress all JPGs in Final folder with new compression settings."""

    click.echo("=" * 60)
    click.echo("Re-compress Final Folder with New Settings")
    click.echo("=" * 60)
    click.echo(f"Final folder: {FINAL_PATH}")
    click.echo(f"New settings: 5200×3467, Quality 92, 4:4:4 chroma")
    click.echo(f"Mode: {'DRY RUN (preview only)' if dry_run else 'LIVE (will modify files)'}")
    click.echo("=" * 60)
    click.echo()

    if not FINAL_PATH.exists():
        click.echo(f"❌ Error: Final folder not found: {FINAL_PATH}")
        return

    # Scan for all JPGs in Final folder
    click.echo("📂 Scanning Final folder for JPGs...")
    jpg_files = scan_for_images(FINAL_PATH, extension='.JPG')

    if not jpg_files:
        click.echo("✅ No JPG files found in Final folder.")
        return

    click.echo(f"Found {len(jpg_files)} JPG files to re-compress")
    click.echo()

    # Ask for confirmation if not dry-run
    if not dry_run:
        click.echo("⚠️  WARNING: This will modify ALL files in your Final folder!")
        click.echo("   - Each file will be backed up before compression")
        click.echo("   - Original files will be replaced with re-compressed versions")
        click.echo("   - All metadata will be preserved using exiftool")
        click.echo()
        if not click.confirm("Do you want to proceed?"):
            click.echo("Operation cancelled.")
            return
        click.echo()

    # Process each file
    compressed = 0
    skipped = 0
    errors = 0

    for idx, jpg_file in enumerate(jpg_files, 1):
        # Show progress
        progress = f"[{idx}/{len(jpg_files)}]"
        filename = jpg_file.name

        if dry_run:
            click.echo(f"{progress} Would re-compress: {filename}")
            compressed += 1
        else:
            click.echo(f"{progress} Compressing: {filename}...", nl=False)

            # Compress the file (in-place)
            success, error = ImageProcessor.compress_jpeg_safe(jpg_file)

            if success:
                click.echo(" ✅ Done")
                compressed += 1
            else:
                click.echo(f" ❌ Failed: {error}")
                errors += 1

    # Summary
    click.echo()
    click.echo("=" * 60)
    click.echo("Re-compression Summary:")
    click.echo(f"  Files {'to be ' if dry_run else ''}compressed: {compressed}")
    if errors > 0:
        click.echo(f"  Errors: {errors}")
    click.echo("=" * 60)

    if dry_run:
        click.echo()
        click.echo("💡 To actually re-compress files, run without --dry-run:")
        click.echo("   python3 recompress_final.py")


# NOTE: disabled for now, since this script is not needed anymore and
# CAUSES RECOMPRESSION OF ALL IMAGES IN FINAL FOLDER
# if __name__ == '__main__':
#    recompress_final()
