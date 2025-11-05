"""
Console utilities for rich terminal output.

This module provides centralized Rich console helpers for consistent,
beautiful terminal output throughout the Photo-Flow application.
"""

from rich.console import Console
from rich.progress import (
    Progress,
    SpinnerColumn,
    TextColumn,
    BarColumn,
    TaskProgressColumn,
    TimeRemainingColumn,
    MofNCompleteColumn,
)
from contextlib import contextmanager

# Single console instance used throughout the application
console = Console()


def success(message: str) -> None:
    """
    Print a success message with green checkmark.

    Args:
        message: The success message to display
    """
    console.print(f"[bold green]✓[/bold green] {message}")


def warning(message: str) -> None:
    """
    Print a warning message with yellow exclamation mark.

    Args:
        message: The warning message to display
    """
    console.print(f"[bold yellow]![/bold yellow] {message}")


def error(message: str) -> None:
    """
    Print an error message with red X.

    Args:
        message: The error message to display
    """
    console.print(f"[bold red]✗[/bold red] {message}")


def info(message: str) -> None:
    """
    Print an informational message.

    Args:
        message: The info message to display
    """
    console.print(message)


@contextmanager
def show_status(message: str, spinner: str = "dots"):
    """
    Context manager for showing a status spinner during long operations.

    Args:
        message: Status message to display
        spinner: Spinner style (default: "dots")

    Example:
        with show_status("Building gallery..."):
            run_npm_build()
    """
    with console.status(f"[bold blue]{message}[/bold blue]", spinner=spinner):
        yield


def create_progress() -> Progress:
    """
    Create a Rich Progress instance with standard columns for file operations.

    Returns:
        Configured Progress instance

    Example:
        with create_progress() as progress:
            task = progress.add_task("Importing files", total=100)
            for file in files:
                process_file(file)
                progress.advance(task)
    """
    return Progress(
        SpinnerColumn(),
        TextColumn("[bold blue]{task.description}"),
        BarColumn(),
        MofNCompleteColumn(),
        TaskProgressColumn(),
        TimeRemainingColumn(),
        console=console,
    )


def print_summary(title: str, stats: dict) -> None:
    """
    Print a formatted summary of operation results.

    Args:
        title: Summary title
        stats: Dictionary of stat names to values

    Example:
        print_summary("Import completed", {
            "Videos": 45,
            "Photos": 120,
            "Skipped": 15
        })
    """
    console.print()  # Blank line
    console.print(f"[bold]{title}[/bold]")
    for key, value in stats.items():
        console.print(f"  {key}: [cyan]{value}[/cyan]")
