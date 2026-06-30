"""
Job manager + QueueReporter for the photo-flow control panel API.

Concurrency model:
- `PhotoWorkflow` methods are blocking sync IO; they run via asyncio.to_thread.
- Exactly ONE mutating job executes at a time (single worker, one dequeue). Jobs are
  admitted to a FIFO asyncio.Queue — no more hard 409 reject, just serialized execution.
- A single long-lived `_worker()` coroutine drains the queue one job at a time.
  The `assert self._current is None` guard before each dispatch makes the invariant
  machine-verifiable at runtime.
- QueueReporter bridges the worker thread back to the event loop via
  loop.call_soon_threadsafe so subscriber queues are populated safely.
- Job.subscribe() pre-loads past events and adds the queue to the fan-out list
  atomically (both steps in the event loop thread).
- For destructive ops (cleanup / finalize) a re-validation step re-runs the dry-run
  preview at dequeue time and compares to the approved preview the user saw.  If the
  new destructive count is LARGER than what the user approved, the job is parked in
  `needs_confirm` status and the manager emits a `job_needs_confirm` event so the UI
  can ask the user to re-confirm with the updated numbers.
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import subprocess
import threading
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Awaitable, Callable, Dict, List, Optional

from rich.text import Text

_log = logging.getLogger(__name__)


def _strip_markup(s: str) -> str:
    """Remove Rich console markup (e.g. '[cyan]…[/cyan]') so API/SSE consumers get plain
    text. The workflow embeds these tags for the CLI's RichReporter; the web UI must not
    render them literally."""
    try:
        return Text.from_markup(s).plain
    except Exception:
        return s


# ---------------------------------------------------------------------------
# Last-run persistence
# ---------------------------------------------------------------------------

# Atomic JSON file tracking the last terminal result for each op.
# Written by _persist_last_run after every job reaches a terminal status.
_LAST_RUN_PATH = Path.home() / ".photoflow" / "last_run.json"


def _last_run_key(op: str) -> str:
    """Map internal op names to last_run.json keys (backup:* → 'backup')."""
    if op.startswith("backup"):
        return "backup"
    return op


def _persist_last_run(job: "Job") -> None:
    """Atomically record this job's terminal state in ~/.photoflow/last_run.json.

    Best-effort: any failure is logged at DEBUG level and swallowed so it never
    breaks the job or the queue.  Uses temp-file + os.replace for atomicity.
    """
    try:
        key = _last_run_key(job.op)
        data: dict = {}
        try:
            data = json.loads(_LAST_RUN_PATH.read_text())
        except Exception:
            pass  # Missing or malformed — start fresh
        entry: dict = {
            "ts": datetime.now(timezone.utc).isoformat(),
            "ok": job.status == "done",
        }
        if job.result is not None:
            entry["counts"] = job.result
        data[key] = entry
        _LAST_RUN_PATH.parent.mkdir(parents=True, exist_ok=True)
        tmp = _LAST_RUN_PATH.with_suffix(".tmp")
        tmp.write_text(json.dumps(data, indent=2, default=str))
        os.replace(str(tmp), str(_LAST_RUN_PATH))
    except Exception as exc:
        _log.debug("_persist_last_run: write skipped (%s)", exc)


# ---------------------------------------------------------------------------
# Per-op watchdog timeouts
# ---------------------------------------------------------------------------

# All timeouts are cooperative (set cancel_event, never hard-kill) so the
# workflow's copy-verify-then-delete per-file atomicity is always preserved.
# Values are deliberately generous — a first full RAF backup over Tailscale can
# be large and slow; a tight timer risks interrupting a legitimate transfer.
_TIMEOUT_SYNC_GALLERY = 20 * 60      # 20 min — npm build + rsync typically <5 min
_TIMEOUT_BACKUP = 90 * 60            # 90 min — full RAF/video backup over Tailscale
_TIMEOUT_IMPORT_FINALIZE_CLEANUP = 60 * 60  # 60 min — camera scan + verified file moves
_TIMEOUT_DEFAULT = 60 * 60           # 60 min — fallback for any unrecognised op


def _op_timeout(op: str) -> float:
    """Return the watchdog timeout in seconds for the given op name."""
    if op == "sync-gallery":
        return _TIMEOUT_SYNC_GALLERY
    if op.startswith("backup"):
        return _TIMEOUT_BACKUP
    if op in ("import", "finalize", "cleanup"):
        return _TIMEOUT_IMPORT_FINALIZE_CLEANUP
    return _TIMEOUT_DEFAULT


async def _watchdog(job: "Job", timeout_s: float) -> None:
    """Cooperatively cancel a job that exceeds its per-op timeout.

    Sets job.cancel_event after `timeout_s` seconds if the job is still running.
    Does NOT hard-kill any subprocess — the workflow stops at its next
    is_cancelled() check between files so copy-verify-then-delete is never torn.
    Best-effort: always cancelled by _dispatch's finally block when a job
    completes normally within its timeout.
    """
    await asyncio.sleep(timeout_s)
    if job.status == "running" and not job.cancel_event.is_set():
        _log.warning(
            "Watchdog: job %s (op=%s) exceeded %.0fs — setting cancel_event",
            job.job_id, job.op, timeout_s,
        )
        job.cancel_event.set()


class QueueReporter:
    """Pushes structured progress events to SSE subscribers thread-safely.

    Satisfies the ProgressReporter structural protocol. The workflow fn runs
    in a worker thread; calls here cross back to the event loop thread via
    loop.call_soon_threadsafe.
    """

    def __init__(self, job: "Job", loop: asyncio.AbstractEventLoop) -> None:
        self._job = job
        self._loop = loop

    def _push(self, event: dict) -> None:
        self._loop.call_soon_threadsafe(self._job._enqueue, event)

    def task(self, desc: str, total: int) -> None:
        self._push({"type": "task", "desc": _strip_markup(desc), "total": total})

    def advance(self, n: int = 1) -> None:
        self._push({"type": "advance", "n": n})

    def log(self, level: str, message: str) -> None:
        self._push({"type": "log", "level": level, "message": _strip_markup(message)})

    def event(self, type: str, payload: dict) -> None:
        self._push({"type": type, **payload})

    def is_cancelled(self) -> bool:
        """Cooperative cancellation flag — the workflow polls this between files."""
        return self._job.cancel_event.is_set()

    def __enter__(self) -> "QueueReporter":
        return self

    def __exit__(self, exc_type, exc_val, exc_tb) -> None:
        pass


class Job:
    """Represents a single operation — queued, running, or completed."""

    def __init__(self, job_id: str, op: str, seq: int = 0) -> None:
        self.job_id = job_id
        self.op = op
        self.seq = seq
        # Status machine: queued → running → done | failed | cancelled | needs_confirm
        self.status: str = "queued"
        self.result: Optional[Dict[str, Any]] = None
        self.error: Optional[str] = None
        self.events: List[Dict[str, Any]] = []
        self._queues: List[asyncio.Queue] = []
        # Set by JobManager.cancel(); polled by QueueReporter.is_cancelled() in the
        # worker thread so the workflow can stop cooperatively between files.
        self.cancel_event = threading.Event()
        # The approved dry-run preview the user confirmed before enqueueing.
        # Used for the dispatch-time re-validation guard on destructive ops.
        self.destructive_preview: Optional[Dict[str, Any]] = None
        # Fresh preview computed at dequeue time when the destructive set grew —
        # surfaced to the UI so the user can re-confirm with the updated numbers.
        self.fresh_preview: Optional[Dict[str, Any]] = None
        # Async callable () -> Dict; provided for destructive ops so the worker
        # can re-run the dry-run check without importing the workflow directly.
        self.revalidator: Optional[Callable[[], Awaitable[Dict[str, Any]]]] = None
        # The actual work function fn(reporter) -> Dict.
        self._fn: Optional[Callable[..., Any]] = None
        # Position in the queue (0 = next to run). Updated by JobManager.get_jobs().
        self.position: int = 0

    def _enqueue(self, event: dict) -> None:
        """Append event to history and fan out to all subscriber queues.

        Always called in the event loop thread (via call_soon_threadsafe or
        directly from async code), so appending to self.events and iterating
        self._queues is safe without additional locking.
        """
        self.events.append(event)
        for q in self._queues:
            q.put_nowait(event)

    async def subscribe(self) -> asyncio.Queue:
        """Return a queue pre-loaded with all past events.

        Must be called from the event loop. Pre-loading and appending to
        _queues both happen in the event loop thread, so no race with
        _enqueue (which also runs in the event loop thread).
        """
        q: asyncio.Queue = asyncio.Queue()
        for event in self.events:
            q.put_nowait(event)
        self._queues.append(q)
        return q

    def unsubscribe(self, q: asyncio.Queue) -> None:
        try:
            self._queues.remove(q)
        except ValueError:
            pass

    def to_dict(self) -> dict:
        return {
            "job_id": self.job_id,
            "op": self.op,
            "status": self.status,
            "seq": self.seq,
            "position": self.position,
            "result": self.result,
            "error": self.error,
            "fresh_preview": self.fresh_preview,
        }


# Maximum number of terminal jobs (done/failed/cancelled) retained in memory.
_MAX_TERMINAL_JOBS = 50


class JobManager:
    """Serial FIFO queue for mutating workflow operations.

    Exactly ONE job executes at a time (single worker coroutine, one dequeue).
    New jobs are always ADMITTED to the queue and NEVER hard-rejected —
    they wait their turn. The `assert self._current is None` guard in
    `_dispatch()` makes the single-flight invariant machine-verifiable.

    Destructive ops (cleanup / finalize) carry the user's approved preview.
    At dequeue time the worker re-runs the dry-run to check whether the
    deletion set has grown; if so the job is parked in `needs_confirm` so
    the user can re-confirm rather than losing unexpected RAWs.
    """

    def __init__(self) -> None:
        self._jobs: Dict[str, Job] = {}
        # The job currently executing (None when idle).
        self._current: Optional[Job] = None
        # FIFO queue of pending Job objects.
        self._queue: Optional[asyncio.Queue] = None
        # Ordered list of job IDs mirroring the queue contents (for reorder support).
        self._queued_ids: List[str] = []
        # Monotonically increasing sequence number for ordering.
        self._seq: int = 0
        # Single worker task — created on first enqueue, never re-created.
        self._worker_task: Optional[asyncio.Task] = None
        # Manager-level lifecycle event fan-out (job_queued / job_started / etc.).
        self._manager_subs: List[asyncio.Queue] = []
        # caffeinate subprocess — kept alive while any job is queued OR running.
        self._caffeinate: Optional[subprocess.Popen] = None

    # ── Queue initialization (lazy — must be called from running event loop) ──

    def _get_queue(self) -> asyncio.Queue:
        if self._queue is None:
            self._queue = asyncio.Queue()
        return self._queue

    # ── Public API ────────────────────────────────────────────────────────────

    @property
    def is_running(self) -> bool:
        return self._current is not None and self._current.status == "running"

    @property
    def current_job(self) -> Optional[Job]:
        """The job currently running (None if idle) — lets a fresh page reconnect."""
        return (
            self._current
            if self._current is not None and self._current.status == "running"
            else None
        )

    def get_job(self, job_id: str) -> Optional[Job]:
        return self._jobs.get(job_id)

    def get_jobs(self) -> List[Job]:
        """Return jobs in display order: queued (by position) → running → needs_confirm → recent terminal."""
        queued: List[Job] = []
        for i, jid in enumerate(self._queued_ids):
            job = self._jobs.get(jid)
            if job is not None:
                job.position = i
                queued.append(job)

        running = [j for j in self._jobs.values() if j.status == "running"]
        needs_confirm = [j for j in self._jobs.values() if j.status == "needs_confirm"]
        terminal = sorted(
            [j for j in self._jobs.values() if j.status in ("done", "failed", "cancelled")],
            key=lambda j: j.seq,
            reverse=True,
        )[:10]

        return queued + running + needs_confirm + terminal

    async def enqueue(
        self,
        op_name: str,
        fn: Callable,
        *,
        destructive_preview: Optional[Dict[str, Any]] = None,
        revalidator: Optional[Callable[[], Awaitable[Dict[str, Any]]]] = None,
    ) -> Job:
        """Admit a new job to the FIFO queue. Never raises — always enqueues.

        fn signature: fn(reporter: QueueReporter) -> Dict[str, Any]
        fn runs in a worker thread via asyncio.to_thread.
        destructive_preview: the dry-run result the user approved; stored for
            dispatch-time re-validation (only used when revalidator is also provided).
        revalidator: async callable that re-runs the dry-run; provided only for
            cleanup and finalize.
        """
        self._seq += 1
        job_id = str(uuid.uuid4())
        job = Job(job_id=job_id, op=op_name, seq=self._seq)
        job._fn = fn
        job.destructive_preview = destructive_preview
        job.revalidator = revalidator

        self._jobs[job_id] = job
        self._queued_ids.append(job_id)
        job.position = len(self._queued_ids) - 1

        queue = self._get_queue()
        queue.put_nowait(job)

        self._notify_manager(
            {
                "type": "job_queued",
                "job_id": job_id,
                "op": op_name,
                "status": "queued",
                "position": job.position,
            }
        )
        self._maybe_start_caffeinate()
        self._ensure_worker()
        self._evict_old_jobs()
        return job

    async def start(self, op_name: str, fn: Callable) -> Job:
        """Thin alias for enqueue() — preserved for compatibility."""
        return await self.enqueue(op_name, fn)

    def cancel(self, job_id: str) -> bool:
        """Request cancellation of a running or queued job.

        Running → sets cancel_event; workflow stops at its next per-file check.
        Queued  → sets cancel_event; worker skips it at dequeue.
        needs_confirm → marks cancelled immediately (already parked).
        """
        job = self._jobs.get(job_id)
        if job is None:
            return False
        if job.status == "running":
            job.cancel_event.set()
            return True
        if job.status == "queued":
            job.cancel_event.set()
            return True
        if job.status == "needs_confirm":
            job.status = "cancelled"
            self._notify_manager(
                {"type": "job_finished", "job_id": job_id, "op": job.op, "status": "cancelled"}
            )
            self._evict_old_jobs()
            return True
        return False

    def reorder_job(self, job_id: str, direction: str) -> bool:
        """Move a QUEUED job one slot up or down in the queue.

        Safe to call from the event loop thread: the drain+refill of the asyncio.Queue
        runs synchronously with no yield points, so the worker's `await queue.get()`
        cannot interleave mid-reorder.
        """
        job = self._jobs.get(job_id)
        if job is None or job.status != "queued":
            return False
        try:
            idx = self._queued_ids.index(job_id)
        except ValueError:
            return False

        if direction == "up" and idx > 0:
            self._queued_ids[idx], self._queued_ids[idx - 1] = (
                self._queued_ids[idx - 1],
                self._queued_ids[idx],
            )
        elif direction == "down" and idx < len(self._queued_ids) - 1:
            self._queued_ids[idx], self._queued_ids[idx + 1] = (
                self._queued_ids[idx + 1],
                self._queued_ids[idx],
            )
        else:
            return False

        self._rebuild_queue()
        self._notify_manager({"type": "queue_reordered", "job_id": job_id})
        return True

    # ── Manager-level event fan-out ────────────────────────────────────────────

    async def subscribe_manager(self) -> asyncio.Queue:
        """Return a new queue that will receive manager lifecycle events."""
        q: asyncio.Queue = asyncio.Queue()
        self._manager_subs.append(q)
        return q

    def unsubscribe_manager(self, q: asyncio.Queue) -> None:
        try:
            self._manager_subs.remove(q)
        except ValueError:
            pass

    def _notify_manager(self, event: dict) -> None:
        for q in self._manager_subs:
            q.put_nowait(event)

    # ── Internal helpers ───────────────────────────────────────────────────────

    def _ensure_worker(self) -> None:
        """Start the single worker task if it isn't already running."""
        if self._worker_task is None or self._worker_task.done():
            self._worker_task = asyncio.create_task(self._worker())

    def _rebuild_queue(self) -> None:
        """Drain and refill the asyncio.Queue to match the _queued_ids order."""
        queue = self._get_queue()
        while True:
            try:
                queue.get_nowait()
            except asyncio.QueueEmpty:
                break
        for jid in self._queued_ids:
            j = self._jobs.get(jid)
            if j is not None:
                queue.put_nowait(j)

    def _evict_old_jobs(self) -> None:
        """Cap terminal jobs at _MAX_TERMINAL_JOBS; evict oldest by seq."""
        terminal = [
            j
            for j in self._jobs.values()
            if j.status in ("done", "failed", "cancelled")
        ]
        if len(terminal) <= _MAX_TERMINAL_JOBS:
            return
        terminal.sort(key=lambda j: j.seq)
        to_evict = terminal[: len(terminal) - _MAX_TERMINAL_JOBS]
        for j in to_evict:
            del self._jobs[j.job_id]

    def _maybe_start_caffeinate(self) -> None:
        """Keep the Mac awake while jobs are queued or running. Best-effort."""
        if self._caffeinate is None:
            try:
                self._caffeinate = subprocess.Popen(["caffeinate", "-i", "-m", "-s"])
            except Exception:
                pass

    def _maybe_stop_caffeinate(self) -> None:
        """Release caffeinate once the queue is empty and no job is executing."""
        queue = self._get_queue()
        if self._caffeinate is not None and queue.empty() and self._current is None:
            try:
                self._caffeinate.terminate()
                self._caffeinate.wait(timeout=2)
            except Exception:
                pass
            self._caffeinate = None

    # ── Worker coroutine ───────────────────────────────────────────────────────

    async def _worker(self) -> None:
        """Single long-lived coroutine — drains the queue one job at a time.

        Survives individual job failures: the try/except around `_dispatch` logs
        the error and continues. If `_dispatch` itself has an unhandled bug the
        worker resets `_current` and notifies subscribers so the queue keeps moving.
        """
        queue = self._get_queue()
        while True:
            try:
                job = await queue.get()
            except asyncio.CancelledError:
                break  # Clean shutdown
            except Exception as exc:
                _log.error("_worker: queue.get() failed: %s", exc)
                await asyncio.sleep(0.1)
                continue

            # Remove from the tracking list (dequeued by worker).
            try:
                self._queued_ids.remove(job.job_id)
            except ValueError:
                pass

            try:
                await self._dispatch(job)
            except Exception as exc:
                _log.error("_worker: unhandled error dispatching %s: %s", job.job_id, exc)
                if job.status not in ("done", "failed", "cancelled", "needs_confirm"):
                    job.status = "failed"
                    job.error = f"Internal worker error: {exc}"
                    job._enqueue({"type": "done", "result": None, "error": job.error})
                if self._current is job:
                    self._current = None
                self._notify_manager(
                    {"type": "job_finished", "job_id": job.job_id, "op": job.op, "status": job.status}
                )
                self._evict_old_jobs()
                self._maybe_stop_caffeinate()

    async def _dispatch(self, job: Job) -> None:
        """Execute one job from the queue. Only one call is ever in-flight."""
        # Guard: job was cancelled while waiting in the queue.
        if job.cancel_event.is_set():
            job.status = "cancelled"
            job._enqueue({"type": "done", "result": None, "error": "cancelled"})
            self._notify_manager(
                {"type": "job_finished", "job_id": job.job_id, "op": job.op, "status": "cancelled"}
            )
            self._evict_old_jobs()
            self._maybe_stop_caffeinate()
            return

        # Dispatch-time re-validation for destructive ops (cleanup / finalize).
        #
        # The user approved a specific dry-run preview (stored as `destructive_preview`).
        # We re-run the same dry-run now, just before execution, and compare the destructive
        # count. If the new count is LARGER than what the user saw, the Staging∪Final keep-set
        # has changed since they confirmed (e.g. new photos imported while the job was queued)
        # and the user must re-confirm with the updated numbers.
        #
        # NOTE: the dry-run only exposes COUNTS, not the actual RAW base list, so this is a
        # count-based comparison (weaker than a set diff — a count-equal permutation is not
        # caught). Safe against the critical data-loss scenario (a growing deletion set).
        if job.revalidator is not None:
            needs_reconfirm, fresh = await self._revalidate(job)
            if needs_reconfirm:
                job.status = "needs_confirm"
                job.fresh_preview = fresh
                job._enqueue({"type": "done", "result": None, "error": "needs_confirm"})
                self._notify_manager(
                    {
                        "type": "job_needs_confirm",
                        "job_id": job.job_id,
                        "op": job.op,
                        "status": "needs_confirm",
                        "fresh_preview": fresh,
                    }
                )
                self._evict_old_jobs()
                self._maybe_stop_caffeinate()
                return

        # ── Single-flight assertion — must hold before every dispatch ──────────
        assert self._current is None, (
            f"BUG: single-flight violated — _current={self._current!r}"
        )

        self._current = job
        job.status = "running"
        loop = asyncio.get_running_loop()
        reporter = QueueReporter(job, loop)
        self._notify_manager(
            {"type": "job_started", "job_id": job.job_id, "op": job.op, "status": "running"}
        )

        # Watchdog: cooperatively cancel the job after a generous per-op timeout.
        # Sets cancel_event only — never hard-kills — so copy-verify-then-delete
        # atomicity is always preserved; the workflow stops at its next per-file check.
        watchdog_task = asyncio.create_task(
            _watchdog(job, _op_timeout(job.op)),
            name=f"watchdog-{job.job_id}",
        )

        done_event: dict = {"type": "done", "result": None, "error": "cancelled"}
        try:
            result = await asyncio.to_thread(job._fn, reporter=reporter)
            job.result = result
            if job.cancel_event.is_set():
                job.status = "cancelled"
                done_event = {"type": "done", "result": result, "error": "cancelled"}
            else:
                job.status = "done"
                done_event = {"type": "done", "result": result, "error": None}
        except Exception as exc:
            job.status = "failed"
            job.error = str(exc)
            done_event = {"type": "done", "result": None, "error": str(exc)}
        finally:
            # Cancel watchdog — no-op if it already fired or job exceeded the timeout.
            watchdog_task.cancel()
            try:
                await watchdog_task
            except asyncio.CancelledError:
                pass
            # Persist terminal state atomically — best-effort, never breaks the queue.
            _persist_last_run(job)
            job._enqueue(done_event)
            self._current = None
            self._notify_manager(
                {"type": "job_finished", "job_id": job.job_id, "op": job.op, "status": job.status}
            )
            self._evict_old_jobs()
            self._maybe_stop_caffeinate()

    async def _revalidate(
        self, job: Job
    ) -> tuple[bool, Optional[Dict[str, Any]]]:
        """Re-run the dry-run preview and compare to the approved count.

        Returns (needs_reconfirm: bool, fresh_preview: dict | None).
        """
        try:
            fresh = await job.revalidator()  # type: ignore[misc]
            approved = job.destructive_preview or {}
            if job.op == "cleanup":
                approved_count = int(approved.get("orphaned", 0) or 0)
                fresh_count = int(fresh.get("orphaned", 0) or 0)
            elif job.op == "finalize":
                approved_count = int(approved.get("orphaned_raws", 0) or 0)
                fresh_count = int(fresh.get("orphaned_raws", 0) or 0)
            else:
                return False, None

            if fresh_count > approved_count:
                return True, fresh
            return False, None
        except Exception as exc:
            _log.warning(
                "_revalidate: error during re-validation (%s) — failing closed (needs_confirm)", exc
            )
            return True, None
