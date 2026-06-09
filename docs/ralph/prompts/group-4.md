# Group 4: Job manager, QueueReporter, SSE, and status endpoints

## What You're Doing

Build the API runtime: a single-flight job manager that runs blocking `PhotoWorkflow` operations
off the event loop, a `QueueReporter` that turns `ProgressReporter` calls into a stream of events,
an SSE endpoint to consume them, and the cheap/expensive status endpoints.

---

## Research & Exploration First

1. Read `control_panel/PRD.md` → "Job manager + concurrency" and "Status: cheap vs expensive".
2. Read `photo_flow/progress.py` (the seam) and `photo_flow/workflow.py` `get_status()` /
   `StatusReport` + `scan_camera_files()`.
3. Research `sse-starlette` `EventSourceResponse` and `asyncio.to_thread` / `asyncio.Queue` usage
   (Context7 or web) — do not invent the API.

---

## What to Implement

### 1. `photo_flow/api/jobs.py` — job manager

- A `JobManager` holding at most **one running mutating job** (an `asyncio.Lock`; reject a second
  start with HTTP 409). Each job: `job_id` (uuid), `op` name, `status` (running/done/failed),
  `result` dict, `events` list + a live `asyncio.Queue` for SSE subscribers.
- `QueueReporter(ProgressReporter)`: `task/advance/log/event` push JSON-serializable event dicts
  (`{"type": "...", ...}`) onto the job's queue (thread-safe — the workflow runs in a worker thread
  via `asyncio.to_thread`, so use `loop.call_soon_threadsafe` or `janus`/a thread-safe queue;
  research the correct cross-thread pattern).
- `start(op_name, fn)` runs `fn(reporter=QueueReporter(job))` in `asyncio.to_thread`, marks
  done/failed, puts a terminal `{"type":"done","result":…}` event, releases the lock.

### 2. `photo_flow/api/routes_status.py`

- `GET /status` — **cheap, pollable**: `camera_connected`, `ssd_connected` (`Path.exists`), and a
  cached `staging_files` count (local glob; cache a few seconds). Must return in well under 100ms.
- `GET /status/pending` — **expensive**: runs `scan_camera_files()` (USB walk) for pending
  videos/photos/raws. Separate so the frontend polls it less often.

### 3. `photo_flow/api/routes_jobs.py`

- `GET /jobs/{job_id}` — current status + result.
- `GET /events/{job_id}` — `EventSourceResponse` streaming that job's queued events until terminal.

### 4. Wire into `app.py`

Register the routers; instantiate one `JobManager` on the app state. CORS not needed (same-origin
in prod; dev uses Vite proxy).

---

## Validation

```bash
venv/bin/python -m pytest -q
```
Tests (`tests/test_api_status.py`, `tests/test_jobs.py`, with `TestClient`):
- `/status` returns the four cheap fields and is fast (no camera scan).
- Job manager: starting a second mutating job while one runs → 409.
- A fake op driving a `QueueReporter` produces ordered events ending in a `done` event; the SSE
  endpoint yields them. (Drive with a dummy fn, not a real workflow op — no real files.)

---

## Commit

```
feat(api): job manager, QueueReporter, SSE stream, status endpoints
```

---

## Done

Append notes (esp. the cross-thread queue solution you chose), then:
```
RALPH_TASK_COMPLETE: Group 4
```
