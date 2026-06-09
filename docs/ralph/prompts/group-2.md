# Group 2: Event seam part 1 — ProgressReporter + RichReporter (import + finalize)

## What You're Doing

Introduce the progress/event abstraction the core will emit to, with a Rich-backed implementation
that preserves the **exact current CLI output**, and wire it into `import_from_camera` and
`finalize_staging`. The existing `progress_callback(str)` param is too thin and mostly dead —
replace its role with a structured `ProgressReporter`. **This edits safety-critical file-move code:
change emission only, never the copy/verify/delete logic or ordering.**

---

## Research & Exploration First

1. Read `control_panel/PRD.md` → "The event seam" section.
2. Read `photo_flow/workflow.py` `import_from_camera` (~lines 203–274) and `finalize_staging`
   (~lines 328–385) — note every `create_progress()`/`progress.add_task`/`progress.advance` and
   every `info()/warning()/error()` call.
3. Read `photo_flow/console_utils.py` — `create_progress()` and the console helpers.

---

## What to Implement

### 1. `photo_flow/progress.py` — the seam

```python
from typing import Protocol, Optional

class ProgressReporter(Protocol):
    def task(self, desc: str, total: int) -> None: ...        # start/replace the active task
    def advance(self, n: int = 1) -> None: ...                # tick the active task
    def log(self, level: str, message: str) -> None: ...      # level: info|success|warning|error
    def event(self, type: str, payload: dict) -> None: ...    # structured milestone (e.g. file done)
```

- `RichReporter` — wraps the existing Rich `create_progress()` + console helpers so output is
  **byte-for-byte what the CLI shows today**. `task()` opens a Rich progress task, `advance()`
  advances it, `log()` routes to `success/info/warning/error`, `event()` is a no-op for Rich.
  Manage the Rich `Progress` context internally (enter on first `task`, exit on close / context
  manager). Provide a `NullReporter` too (does nothing) as the default.

### 2. Wire into `import_from_camera` and `finalize_staging`

- Replace the `progress_callback=None` param with `reporter: Optional[ProgressReporter] = None`,
  defaulting to a `RichReporter()` so the CLI path is unchanged. (Keep a `progress_callback` alias
  param accepting None for backward compat if any caller passes it, but it can be ignored.)
- Replace the inline `with create_progress() as progress:` blocks with `reporter.task(...)` /
  `reporter.advance()`. Route the `info/warning/error` calls through `reporter.log(...)`.
- Emit `reporter.event("file_done", {...})` per file processed (filename, dest, action) — this is
  what SSE will stream later. Keep it cheap.

### 3. CLI stays identical

`cli.py` should still produce the same terminal output. If `cli.py` passes nothing, the default
`RichReporter` renders exactly as before. Verify manually.

---

## Validation

```bash
venv/bin/python -c "import photo_flow.workflow, photo_flow.progress"
venv/bin/python -m pytest -q
# CLI parity — output must match prior behavior:
venv/bin/photoflow status
venv/bin/photoflow import --dry-run
venv/bin/photoflow finalize --dry-run
```
Add `tests/test_progress.py`: a `FakeReporter` collecting calls; assert `import_from_camera` /
`finalize_staging` drive `task/advance/log/event` correctly (use `dry_run=True` and/or temp dirs
so no real files move — DO NOT touch real camera/Staging paths in tests).

---

## Commit

```
refactor(core): add ProgressReporter seam; wire import + finalize
```

---

## Done

Append learning notes (especially any behavior-preservation risks), then:
```
RALPH_TASK_COMPLETE: Group 2
```
