# Group 5: Operation endpoints (dry-run preview + run) for all ops

## What You're Doing

Expose every workflow operation through the API with the confirmation flow: a `dry_run=true`
preview that returns the result dict synchronously, and a real run that starts a job and returns a
`job_id` (whose progress streams over the Group 4 SSE endpoint). Param-ize `backup`'s
interactive-menu logic (which currently lives in `cli.py`) into explicit API parameters.

---

## Research & Exploration First

1. Read `control_panel/PRD.md` → "Job manager + concurrency" (confirmation flow).
2. Read `photo_flow/cli.py` `backup` command (~204–360) — the interactive source-selection menu
   (all / final / raws / videos). That logic must become API params, not be re-prompted.
3. Re-read the `PhotoWorkflow` method signatures from the audit (import/finalize/cleanup/
   sync_gallery/backup_final/raws/videos + `get_backup_availability`).

---

## What to Implement

### `photo_flow/api/routes_ops.py`

For each operation, a `POST /ops/{op}`:
- ops: `import`, `finalize`, `cleanup`, `sync-gallery`, `backup`.
- Query/body params: `dry_run: bool = False`; for `backup`, `source: Literal["final","raws","videos","all"]`.
- **`dry_run=true`** → call the method with `dry_run=True` and a `NullReporter`, return the result
  dict directly (synchronous; this is the preview the confirm modal shows). These are read-only
  previews — fine to run inline.
- **`dry_run=false`** → `JobManager.start(op, lambda reporter: workflow.<method>(reporter=reporter))`,
  return `{"job_id": …}` (202). The single-flight lock prevents concurrent mutating runs.
- `GET /backup/availability` → `get_backup_availability(check_remote=…)` for the pipeline's
  Publish-node freshness.

Keep request/response models as Pydantic models so they show up in OpenAPI (Group 8 generates TS
types from this). Map `backup` `source` to the right `backup_*_to_homelab` method(s) (`all` runs
final→raws→videos sequentially within one job).

---

## Validation

```bash
venv/bin/python -m pytest -q
```
Tests (`tests/test_api_ops.py`):
- `POST /ops/finalize?dry_run=true` returns a result dict with the expected keys (`moved`,
  `edits_moved`, … — use a temp/empty Staging via monkeypatching config paths so nothing real moves).
- `POST /ops/finalize` (real) returns a `job_id`; a second concurrent real op → 409.
- `backup` `source` param routes to the correct method (assert via monkeypatch/spy, not a real
  rclone run).

**Do not run real imports/finalizes/backups against the live photo directories in tests** —
monkeypatch `config` paths to temp dirs or spy on the methods.

---

## Commit

```
feat(api): operation endpoints with dry-run preview + job-backed runs
```

---

## Done

Append notes, then:
```
RALPH_TASK_COMPLETE: Group 5
```
