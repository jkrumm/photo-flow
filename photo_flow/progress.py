"""
Progress reporting seam — decouples the photo-flow core from its output sink.

- RichReporter  : renders to the existing Rich terminal UI (CLI default).
- NullReporter  : silent no-op (tests, programmatic callers that manage output themselves).
- QueueReporter : Group 3 — pushes structured events onto the SSE queue for the API.

ProgressReporter is a structural Protocol; callers type-hint against it so future
reporter implementations drop in without touching the core.
"""
from __future__ import annotations

from typing import Protocol, runtime_checkable

from photo_flow.console_utils import create_progress, success, warning, error, info

_LEVEL_FN = {
    "success": success,
    "warning": warning,
    "error": error,
    "info": info,
}


@runtime_checkable
class ProgressReporter(Protocol):
    def task(self, desc: str, total: int) -> None: ...
    def advance(self, n: int = 1) -> None: ...
    def log(self, level: str, message: str) -> None: ...
    def event(self, type: str, payload: dict) -> None: ...
    def is_cancelled(self) -> bool: ...
    def __enter__(self) -> ProgressReporter: ...
    def __exit__(self, exc_type, exc_val, exc_tb) -> None: ...


class RichReporter:
    """Wraps the existing Rich Progress + console helpers. Byte-for-byte identical CLI output."""

    def __init__(self) -> None:
        self._progress = None
        self._task_id = None

    # --- context manager (preferred usage) ---

    def __enter__(self) -> RichReporter:
        self._progress = create_progress()
        self._progress.start()
        return self

    def __exit__(self, exc_type, exc_val, exc_tb) -> None:
        if self._progress is not None:
            self._progress.stop()
            self._progress = None
            self._task_id = None

    # --- reporter interface ---

    def task(self, desc: str, total: int) -> None:
        if self._progress is None:
            # Lazy start for standalone usage (not via context manager).
            self._progress = create_progress()
            self._progress.start()
        self._task_id = self._progress.add_task(desc, total=total)

    def advance(self, n: int = 1) -> None:
        if self._progress is not None and self._task_id is not None:
            self._progress.advance(self._task_id, n)

    def log(self, level: str, message: str) -> None:
        _LEVEL_FN.get(level, info)(message)

    def event(self, type: str, payload: dict) -> None:
        pass  # no-op for Rich; QueueReporter (Group 3) pushes onto the SSE queue

    def is_cancelled(self) -> bool:
        return False  # CLI cancellation is Ctrl+C, not cooperative

    def __del__(self) -> None:
        # Safety net for standalone usage that forgets to close.
        if self._progress is not None:
            try:
                self._progress.stop()
            except Exception:
                pass


class NullReporter:
    """Silent no-op — useful for tests and API callers that manage output elsewhere."""

    def task(self, desc: str, total: int) -> None:
        pass

    def advance(self, n: int = 1) -> None:
        pass

    def log(self, level: str, message: str) -> None:
        pass

    def event(self, type: str, payload: dict) -> None:
        pass

    def is_cancelled(self) -> bool:
        return False

    def __enter__(self) -> NullReporter:
        return self

    def __exit__(self, exc_type, exc_val, exc_tb) -> None:
        pass
