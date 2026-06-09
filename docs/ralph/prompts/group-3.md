# Group 3: Event seam part 2 — sync_gallery, cleanup, rclone backup, console routing

## What You're Doing

Finish the seam: wire the remaining `PhotoWorkflow` methods to `ProgressReporter` —
`sync_gallery` (two progress blocks + npm/rsync spinners), `cleanup_unused_raws`, and the
`_run_backup_rclone` parse loop (which already yields structured `(pct, speed, eta, files)`
tuples — route those to `reporter.event("transfer", {...})`). Route the remaining global `console`
calls through `reporter.log`. After this group, the entire core emits to the seam and the CLI is
still byte-for-byte identical.

---

## Research & Exploration First

1. Read `photo_flow/workflow.py`: `sync_gallery` (~593–674 + the npm/rsync `show_status` spinners
   ~734–774), `cleanup_unused_raws`, and `_run_backup_rclone` (~1044–1110, the rclone stderr
   regex-parse loop). Also the three `backup_*_to_homelab` wrappers and `get_backup_availability`.
2. Re-read `photo_flow/progress.py` from Group 2 — reuse `RichReporter`; you may need to extend the
   `event` types (e.g. `"transfer"` for rclone pct/speed/eta, `"build"` for npm, `"phase"`).

---

## What to Implement

1. **`sync_gallery`**: replace both `create_progress()` blocks with `reporter.task/advance`. The
   npm build + rsync currently run inside `show_status(...)` spinners with `capture_output=True` —
   wrap those as `reporter.log("info", "Building gallery…")` / `reporter.event("phase", {...})`
   around the blocking subprocess calls (keep them blocking and behavior-identical).
2. **`cleanup_unused_raws`**: route its progress + the deprecated callback through the reporter.
3. **`_run_backup_rclone`**: the loop already parses `(pct, speed, eta, files)` — instead of (or in
   addition to, for Rich) pushing to a Rich `Progress`, emit `reporter.event("transfer",
   {"pct":…, "speed":…, "eta":…, "files":…})`. `RichReporter` should render these as the existing
   rclone progress bar so the CLI looks the same.
4. **Console routing**: replace the remaining direct `info()/warning()/error()/console.print(...)`
   calls in these methods with `reporter.log(...)`. Module-level helper calls outside `PhotoWorkflow`
   can stay.
5. Give all reporter-bearing methods the same `reporter: Optional[ProgressReporter] = None`
   signature with a default `RichReporter()`.

---

## Validation

```bash
venv/bin/python -c "import photo_flow.workflow"
venv/bin/python -m pytest -q
venv/bin/photoflow status
venv/bin/photoflow cleanup --dry-run
venv/bin/photoflow sync-gallery --dry-run
venv/bin/photoflow backup --dry-run     # exercises the rclone path (dry run)
```
Extend `tests/test_progress.py` with `FakeReporter` assertions for `sync_gallery` (dry run) and
`cleanup_unused_raws` (temp dirs). Assert the rclone parse loop, given a sample stderr line, emits
a `"transfer"` event with the right fields (factor the line-parse into a testable helper if needed,
without changing behavior).

---

## Commit

```
refactor(core): wire sync/cleanup/backup to ProgressReporter; route console logs
```

---

## Done

Append notes (call out the rclone event shape — Group 4's SSE depends on it), then:
```
RALPH_TASK_COMPLETE: Group 3
```
