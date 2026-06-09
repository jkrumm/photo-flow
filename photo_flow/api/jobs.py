"""
Job manager + QueueReporter for the photo-flow control panel API.

Concurrency model:
- `PhotoWorkflow` methods are blocking sync IO; they run via asyncio.to_thread.
- Only ONE mutating job runs at a time (single-flight via a boolean flag set
  synchronously before any await — race-free in asyncio's single-threaded loop).
- QueueReporter bridges the worker thread back to the event loop via
  loop.call_soon_threadsafe so subscriber queues are populated safely.
- Job.subscribe() pre-loads past events and adds the queue to the fan-out list
  atomically (both steps in the event loop thread).
"""
from __future__ import annotations

import asyncio
import uuid
from typing import Any, Callable, Dict, List, Optional


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
        self._push({"type": "task", "desc": desc, "total": total})

    def advance(self, n: int = 1) -> None:
        self._push({"type": "advance", "n": n})

    def log(self, level: str, message: str) -> None:
        self._push({"type": "log", "level": level, "message": message})

    def event(self, type: str, payload: dict) -> None:
        self._push({"type": type, **payload})

    def __enter__(self) -> "QueueReporter":
        return self

    def __exit__(self, exc_type, exc_val, exc_tb) -> None:
        pass


class Job:
    """Represents a single running (or completed) operation."""

    def __init__(self, job_id: str, op: str) -> None:
        self.job_id = job_id
        self.op = op
        self.status: str = "running"
        self.result: Optional[Dict[str, Any]] = None
        self.error: Optional[str] = None
        self.events: List[Dict[str, Any]] = []
        self._queues: List[asyncio.Queue] = []

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
            "result": self.result,
            "error": self.error,
        }


class JobManager:
    """Manages a single-flight operation queue for mutating workflow ops.

    Only one mutating job runs at a time. A second start() while one is
    running raises RuntimeError — callers map this to HTTP 409.
    """

    def __init__(self) -> None:
        self._running: bool = False
        self._jobs: Dict[str, Job] = {}

    @property
    def is_running(self) -> bool:
        return self._running

    def get_job(self, job_id: str) -> Optional[Job]:
        return self._jobs.get(job_id)

    async def start(self, op_name: str, fn: Callable) -> Job:
        """Start a mutating job. Raises RuntimeError if one is already running.

        fn signature: fn(reporter: QueueReporter) -> Dict[str, Any]
        fn is called in a worker thread via asyncio.to_thread.
        """
        if self._running:
            raise RuntimeError("A job is already running")

        # Set flag synchronously — no await between check and set, so
        # concurrent start() calls in the single-threaded event loop are safe.
        self._running = True

        job_id = str(uuid.uuid4())
        job = Job(job_id=job_id, op=op_name)
        self._jobs[job_id] = job

        loop = asyncio.get_running_loop()
        reporter = QueueReporter(job, loop)

        async def _run() -> None:
            # Default done event for cancelled/unexpected paths
            done_event: dict = {"type": "done", "result": None, "error": "cancelled"}
            try:
                result = await asyncio.to_thread(fn, reporter=reporter)
                job.result = result
                job.status = "done"
                done_event = {"type": "done", "result": result, "error": None}
            except Exception as exc:
                job.status = "failed"
                job.error = str(exc)
                done_event = {"type": "done", "result": None, "error": str(exc)}
            finally:
                job._enqueue(done_event)
                self._running = False

        asyncio.create_task(_run())
        return job
