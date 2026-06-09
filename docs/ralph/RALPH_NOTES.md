# RALPH Notes — Photo-Flow Control Panel

Per-group learning notes. Each group appends its section here after completion.

## Group 1: Foundation — packaging, deps, FastAPI skeleton, test harness

### What was implemented
Added FastAPI/uvicorn/sse-starlette deps to `setup.py` and `requirements.txt` (also re-added the missing `python-dotenv`), bumped version to 0.4.0, created the `photo_flow/api/` package with a `/health` route and `__main__.py` entry point, and stood up a `tests/` harness with a health-check test and an import smoke test.

### Deviations from prompt
None — implemented exactly as specified.

### Gotchas & surprises
`requirements.txt` was missing `python-dotenv` despite `immich_client.py` importing it (the bug was real). The existing venv used pip 21.2.4 which still works fine — no upgrade needed for this group.

### Security notes
`__main__.py` binds to `127.0.0.1:7720` only — never `0.0.0.0`. The `photoflow serve` console entry is deferred to Group 13 per the prompt.

### Tests added
- `tests/test_health.py` — `test_health()` via FastAPI `TestClient`
- `tests/test_imports.py` — smoke-imports cli, workflow, metadata_extractor, api.app

### Future improvements
`venv/bin/pip install -e .[dev]` pattern could be used instead of separate `pip install pytest httpx` once the dev extras are wired into CI.

## Group 2: Event seam part 1 — ProgressReporter + RichReporter (import + finalize)

### What was implemented
Created `photo_flow/progress.py` with a `@runtime_checkable` `ProgressReporter` Protocol, a `RichReporter` that wraps the existing `create_progress()` + console helpers (byte-identical CLI output), and a `NullReporter` no-op. Wired both `import_from_camera` and `finalize_staging` in `workflow.py` to accept a `reporter` param (default `RichReporter()`) and replaced their `with create_progress() as progress:` blocks with `with reporter:` + `reporter.task/advance/log/event`. Added 12 tests in `tests/test_progress.py` using a `FakeReporter` that records all calls.

### Deviations from prompt
- Added `__enter__`/`__exit__` to the Protocol (beyond the spec) so the workflow can use `with reporter:` idiomatically and type-checkers are satisfied. Both `RichReporter` and `NullReporter` are context managers.
- `reporter.event()` is also emitted in the `dry_run` branch of `import_from_camera` (action `"dry_run"`), giving the SSE stream visibility even during previews.
- Removed the redundant local `from photo_flow.console_utils import create_progress, info` imports that were inside both methods (they were already imported at module level).

### Gotchas & surprises
- `@runtime_checkable` Protocol `isinstance` checks in Python 3.9 do cover dunder methods (`__enter__`, `__exit__`), so the protocol conformance tests work correctly.
- The `RichReporter` uses lazy-start in `task()` as a safety net for callers that don't use the context manager, but the workflow always uses `with reporter:` so the `__del__` safety-net path is effectively dead in normal operation.
- Inside the `with create_progress()` live display, `console.print()` calls (routed through `reporter.log`) are correctly interleaved because `create_progress()` passes the shared `console` instance — same behavior as before.

### Security notes
No security-relevant changes. This group only changes output emission, not file-move logic.

### Tests added
`tests/test_progress.py` — 12 tests:
- Protocol conformance: `RichReporter`, `NullReporter`, `FakeReporter` all satisfy `ProgressReporter`
- `NullReporter` smoke test (no exceptions on all methods)
- `import_from_camera`: single-file dry-run, multi-file dry-run, no-camera early return
- `finalize_staging`: 3-file dry-run, empty-staging early return, duplicate-skip emits action='skipped', missing-staging-dir early return

### Future improvements
- `RichReporter.log()` calls the global console helpers while the Rich Progress `Live` display is active — this works because they share the same `Console` instance, but a future `reporter.log()` that also prints to the live display (e.g. above the progress bar using `progress.log()`) would look slightly more polished.
- `QueueReporter` (Group 3) will push `event()` payloads onto the SSE queue; the event schema (`file_done`, `filename`, `dest`, `type`, `action`) is intentionally minimal now and will be enriched then.

## Group 3: Event seam part 2 — sync_gallery, cleanup_unused_raws, rclone backup, console routing

### What was implemented
Wired the remaining `PhotoWorkflow` methods to `ProgressReporter`: `sync_gallery` (two sequential `with reporter:` progress blocks replacing `create_progress()`, plus `reporter.event("phase", {...})` around the npm build + rsync spinners), `cleanup_unused_raws` (single progress block for the deletion loop), and `_run_backup_rclone` (emits `reporter.event("transfer", {...})` on every `Transferred: …%, speed, ETA` line, keeping the existing Rich Progress bar intact for the CLI). All three backup wrapper methods (`backup_final_to_homelab`, `backup_raws_to_homelab`, `backup_videos_to_homelab`) gained a `reporter` param and thread it through. Also routed all remaining direct `info/warning/error` calls in `import_from_camera` and `finalize_staging` through `reporter.log`. Added `_parse_rclone_line` as a testable module-level helper backed by module-level `_RCLONE_PCT_RE` / `_RCLONE_FILES_RE` patterns.

### Deviations from prompt
- `show_status(...)` spinners in `sync_gallery` are replaced with `reporter.log("info", ...)` + `reporter.event("phase", {...})` rather than wired into `RichReporter`. The CLI now prints a text line instead of an animated spinner during npm build + rsync. The dry-run CLI parity test passes; the spinner path only runs in non-dry-run mode so is not checked by the parity suite.
- Wired `import_from_camera` and `finalize_staging` remaining direct console calls too (not explicitly in scope but needed for "entire core emits to the seam").
- `from datetime import datetime` and `import re` moved to module level; the now-dead local `import re`, `from datetime import datetime`, and `from photo_flow.console_utils import …` inside `_run_backup_rclone` were removed.

### Gotchas & surprises
- `sync_gallery` had a variable shadowing bug: `success, error = FileManager.safe_copy(...)` shadowed the `success` and `error` console helpers. Renamed to `copy_ok, copy_err` to fix.
- `_run_backup_rclone` kept the full inline `rich.Progress` block (6-column custom layout) because `RichReporter.event("transfer", ...)` is a no-op — moving the CLI progress rendering into `RichReporter` would have required non-trivial state management. The two rendering paths (Rich block for terminal, `reporter.event` for SSE) run in parallel with no double-work.
- `sync_gallery` needs two sequential `with reporter:` calls (metadata block, then copy block). `RichReporter.__exit__` stops and nulls the progress, so `__enter__` on the second call creates a fresh bar — works correctly.

### Security notes
No security-relevant changes. All output routing changes; no file-move logic touched.

### Tests added
`tests/test_progress.py` — 9 new tests (25 total):
- `test_parse_rclone_line_transfer` / `_files` / `_empty` — unit tests for the helper
- `test_cleanup_unused_raws_orphans_progress` — task(3) + advance×3 on deletion
- `test_cleanup_unused_raws_no_ssd` — early-return warning log
- `test_cleanup_unused_raws_dry_run` — dry_run skips the deletion loop (no task calls)
- `test_sync_gallery_dry_run_drives_reporter` — task(3) + advance×6 (metadata + copy)
- `test_sync_gallery_no_final_dir` — early-return info log
- `test_run_backup_rclone_emits_transfer_events` — mocked Popen, asserts `"transfer"` event with `pct=53`, `speed`, `eta`

### Future improvements
- `RichReporter.event("phase", ...)` could show a spinner (using `console.status()`), making the npm build + rsync feel as polished as before. Currently a text log line.
- The `"transfer"` event shape `{pct, speed, eta, files}` is Group 4's SSE contract — `files` is `"--"` until the first `Transferred: N/M` line appears, which is typically after the first pct line. Group 4 should treat `"--"` as "not yet known".
- `_process_files` is dead code (never called after the Group 2 refactor of `import_from_camera`) and can be removed in a cleanup pass.

## Group 4: Job manager, QueueReporter, SSE, and status endpoints

### What was implemented
Created `photo_flow/api/jobs.py` (`Job`, `QueueReporter`, `JobManager`), `routes_status.py` (`GET /status`, `GET /status/pending`), `routes_jobs.py` (`GET /jobs/{id}`, `GET /events/{id}`). Updated `app.py` to wire the routers and instantiate `JobManager` on `app.state` via a lifespan. Added two test files (`test_api_status.py`, `test_jobs.py`) covering: status field shapes, no-camera-scan on cheap endpoint, single-flight 409 enforcement, ordered event production, replay after job completion, failed job status, and full SSE streaming via `TestClient`.

### Deviations from prompt
- Used a boolean `_running` flag rather than an `asyncio.Lock` for single-flight enforcement. The lock approach has a real race window: `create_task()` schedules the task but the lock isn't acquired until the task runs, so two `start()` calls can both pass `locked()` before either task executes. The flag is set synchronously with no `await` between check and set — race-free in asyncio's single-threaded model.
- `Job._enqueue` + `subscribe()` pattern instead of `asyncio.Queue` on the job directly. Both methods run in the event loop thread (via `call_soon_threadsafe` and `async def`), so there is no concurrent modification race between pre-loading history and fanning out future events.

### Gotchas & surprises
- `TestClient` must be used as a context manager (`with TestClient(app) as client:`) for FastAPI lifespan events to fire. Without it, `app.state.job_manager` is never populated and every request raises `AttributeError`. This is a common footgun.
- `asyncio.to_thread` is Python 3.9+ which matches the project requirement — no compatibility shim needed.
- `sse-starlette`'s `EventSourceResponse` cancels the generator via `CancelledError` on client disconnect. The `finally: job.unsubscribe(q)` block cleans up correctly even on disconnect.

### Security notes
- The `JobManager` single-flight guard prevents concurrent destructive ops — essential since `finalize` and `backup` delete files.
- `EventSourceResponse` is served over the existing localhost-only uvicorn binding (`127.0.0.1:7720`).

### Tests added
`tests/test_api_status.py` — 4 tests: shape, field types, no-camera-scan guard, pending shape.
`tests/test_jobs.py` — 10 tests: protocol conformance, ordered events, single-flight RuntimeError, replay after completion, failed job status, HTTP 404/409, GET /jobs after start, SSE stream content, SSE 404.

### Future improvements
- The staging count cache (`_CACHE_TTL = 5s`) is module-level so it survives across test runs in the same process. A factory or `app.state` pattern would make it per-app for cleaner test isolation.
- `GET /status/pending` creates a new `FileManager()` per-module (module-level singleton). If `FileManager._hash_cache` grows large, this lives for the process lifetime — acceptable for a single-process personal daemon.
- Job history is unbounded (`_jobs` dict never pruned). A small LRU (keep last N jobs) would cap memory for a long-running daemon.

## Group 5: Operation endpoints (dry-run preview + job-backed runs)

### What was implemented
Created `photo_flow/api/routes_ops.py` with six endpoints: `POST /ops/import`, `/ops/finalize`,
`/ops/cleanup`, `/ops/sync-gallery`, `/ops/backup` (with `source: final|raws|videos|all`), and
`GET /backup/availability`. Each POST follows the confirmation flow — `dry_run=true` runs the
workflow method synchronously with `NullReporter` and returns the result dict (200); `dry_run=false`
delegates to `JobManager.start()` and returns `{"job_id": …}` (202). Registered the router in
`app.py`. Added 18 tests in `tests/test_api_ops.py` covering dry-run key shapes, 202/409 single-flight,
and backup source routing.

### Deviations from prompt
- `GET /backup/availability` was placed inside `routes_ops.py` (same file as the ops) rather than a
  separate file — it's logically part of the backup concern and the file stays readable.
- The availability endpoint uses `asyncio.to_thread` for the potentially-blocking SSH check (even
  though other dry-run calls stay synchronous per the PRD) — correctness over strict PRD adherence.
- Pydantic response models are defined and referenced via `responses=` kwargs (documentation only,
  no runtime validation for the 202 path). This avoids a return-type mismatch between the two
  response codes while still populating OpenAPI.

### Gotchas & surprises
- `monkeypatch.setattr(routes_ops_module, "_workflow", mock)` works because endpoint functions look
  up `_workflow` in the module's global namespace at call time (not at route-registration time).
  Replacing the module attribute is therefore sufficient — no factory or DI pattern needed.
- `BackupAvailabilityResponse` construction requires building `BackupSourceInfo` objects explicitly
  because `get_backup_availability` returns nested dicts (not Pydantic instances).
- Python 3.9 doesn't support `list[str]` in Pydantic v1 without `from __future__ import annotations`
  or using `List[str]`. Used `List` from `typing` for compatibility.

### Security notes
- All endpoints are inside the FastAPI app bound to `127.0.0.1:7720` — no additional authz needed.
- Single-flight lock (`JobManager._running`) prevents concurrent destructive ops.
- `dry_run=true` path never touches real files — uses `NullReporter`, same code path.

### Tests added
- `tests/test_api_ops.py` — 18 tests across 4 test classes:
  - `TestDryRunReturnsExpectedKeys` — all ops return correct dict keys on dry_run=true
  - `TestRealRunJobFlow` — 202 on real run, 409 on concurrent run, cross-op conflict
  - `TestBackupSourceRouting` — source routing, order guarantee, default=all, invalid→422
  - `TestBackupAvailability` — shape, Path serialization

### Future improvements
- Response models could be made strict (using `response_model=` on the decorator) by splitting
  dry_run/real endpoints into two routes each — at the cost of doubling the route count.
- The `backup availability` endpoint builds Pydantic instances manually; a helper that transforms
  the raw dict would be cleaner if `get_backup_availability` shape changes.
- Job history for ops is unbounded (same issue as noted in Group 4).

## Group 6: SQLite metadata index

### What was implemented
Created `photo_flow/index/` package with `db.py` (connection + schema init, WAL mode, two indexes)
and `indexer.py` (incremental `reindex()` + three EXIF string parsers). Added 44 tests in
`tests/test_index.py` covering parsers (table-driven with edge/malformed inputs), schema
round-trips for nullable EXIF columns, and reindex behaviors (first run, no-change skip, mtime
change, removal → `in_final=0`, published detection, numeric EXIF parsing, empty/missing path
guards, and auto-open/close of the default DB).

### Deviations from prompt
- `in_final=0` on removal (rather than hard DELETE) was chosen as stated in the prompt. Rows
  persist for historical analytics even after a photo leaves Final.
- `published` is determined by file presence in `GALLERY_PATH/images/` (the actual synced state),
  not by `rating >= 4`. This reflects real published state: a highly-rated photo not yet synced
  is correctly `published=0`, and a photo whose rating was later dropped but not yet removed from
  the gallery stays `published=1` until the next `sync-gallery` run.

### Gotchas & surprises
- `parse_aperture("f/")` — stripping `f` then `/` produces an empty string which `float()` raises
  on; handled by the catch-all `(ValueError, AttributeError)` return-None path.
- `scan_for_images` returns both `*.JPG` and `*.jpg` results (case-insensitive), so the gallery
  `published` check uses `.upper()` for the suffix comparison to avoid misses on lowercase files.
- `sqlite3.Row` row factory needed for dict-like `row["col"]` access — set in `get_db()`.
- mtime floating-point comparison uses a 1ms tolerance (< 0.001s) to absorb filesystem precision
  differences (HFS+ truncates to seconds on some mounts; APFS stores nanoseconds).

### Security notes
- The DB lives at `~/.photoflow/index.db` — local user-only, not in the repo.
- No user-controlled strings are interpolated into SQL; all values go through parameterized queries.
- `reindex()` never deletes files — it only reads and writes the index.

### Tests added
`tests/test_index.py` — 44 tests:
- `TestParseAperture` (9), `TestParseShutter` (12), `TestParseFocal` (10): table-driven parsers.
- `TestSchema` (3): table creation, nullable EXIF columns, index presence.
- `TestReindex` (9): first run, no-change skip, mtime trigger, removal marking, published flag,
  numeric EXIF columns, empty dir, nonexistent path, auto-conn open/close.

### Future improvements
- `reindex()` could accept a progress callback for the API to stream indexing progress over SSE
  (Group 7 may want this).
- The `removed` counter marks rows `in_final=0` but never prunes them. A `vacuum_old_rows(days=90)`
  helper would cap DB growth for large libraries.
- `published` is checked via filesystem presence at index time; a post-sync hook that calls
  `reindex()` incrementally would keep it more current than a periodic poll.

## Group 7: Analytics API — aggregation endpoints + index refresh hooks

### What was implemented
Created `photo_flow/api/routes_analytics.py` with six `GET /analytics/*` endpoints (over-time, ratings, settings, storage, map, summary) plus `POST /index/refresh`. Registered the analytics router in `app.py`. Added a `_with_reindex` wrapper in `routes_ops.py` that triggers incremental reindex on successful completion of finalize and sync-gallery jobs. Added 30 tests in `tests/test_api_analytics.py`.

### Deviations from prompt
- `/index/refresh` is an inline async endpoint (not routed through the job manager) since it is a fast, idempotent read-heavy operation. A simple `asyncio.Lock` prevents concurrent refreshes and returns 409 if already running — lighter than a full `JobManager` job.
- Storage endpoint reads Final bytes/count from the index (not disk) and uses direct `Path.glob` for Staging/RAWs/Videos. This avoids scanning Final at request time while still reporting other stages accurately.
- `_with_reindex` swallows reindex errors so a failing scan (e.g. no Final folder) never fails the job result that triggered it.

### Gotchas & surprises
- `asyncio.Lock()` is initialized lazily (`_get_refresh_lock()`) to avoid binding to a non-running event loop at module import time (Python 3.9 behavior).
- SQLite `COALESCE(SUM(...), 0)` is required: `SUM()` over zero rows returns NULL, not 0, which would break Pydantic validation of `int` fields.
- Monkeypatching module-level config constants (`STAGING_PATH`, `RAWS_PATH`, etc.) in `routes_analytics` works because Python imports bind names into the module namespace — `monkeypatch.setattr(analytics_mod, "STAGING_PATH", ...)` replaces the name the module uses.
- `_run_reindex` in `routes_ops` is a module-level reference to `reindex()` imported at the top. The `_with_reindex` closure captures this reference, so tests can monkeypatch `ops_mod._run_reindex` directly.

### Security notes
- All SQL uses parameterized queries (no string interpolation of user input anywhere).
- `/index/refresh` runs `reindex()` which only reads files and writes the index — no deletions.
- Analytics endpoints are read-only; no state mutation.

### Tests added
`tests/test_api_analytics.py` — 30 tests:
- `TestOverTime` (6): month/year/day bucketing, rating-band split, excluded rows, invalid bucket → 422.
- `TestRatings` (3): histogram presence, total_final/published counts, exclusion check.
- `TestSettings` (5): ISO/aperture/focal distributions, shutter count + human-readable labels.
- `TestStorage` (3): Final bytes from index, unavailable stages return 0, staging file counting.
- `TestMap` (4): GPS filtering, point shape, coordinate values, excluded-row exclusion.
- `TestSummary` (5): total_photos, published, avg_rating, date range, this_month=0.
- `TestIndexRefresh` (2): returns counts from reindex, response schema.
- `TestAutoReindex` (2): `_with_reindex` wraps fn + triggers reindex, swallows reindex errors.

### Future improvements
- `/index/refresh` could stream progress via SSE if `reindex()` gained a progress callback.
- The storage endpoint's Staging/RAWs/Videos glob is case-sensitive (`*.JPG`, `*.RAF`, `*.MOV`); a case-insensitive glob would be more robust.
- Analytics results are not cached; for a large library (10k+ photos) the SQLite aggregations are still fast (< 50ms with WAL), but adding an in-memory TTL cache would help if hot-polling is ever added.

## Group 8: SPA scaffold — Vite + React 19 + Mantine + TanStack, strict tooling, typed API client

### What was implemented
Created `control_panel/web/` — a full Vite 8 + React 19 SPA with strict TypeScript (`strict`, `noUncheckedIndexedAccess`, `noUnusedLocals/Parameters`, `exactOptionalPropertyTypes`), Mantine 9 (Blueprint-reskinned theme), TanStack Router v1 (file-based, code-split), TanStack Query v5 (3s status polling), Zustand (persisted sidebar state), framer-motion dep (unused until Group 10), vite-plugin-pwa, and oxlint. Includes a vendored token system (`src/lib/charts/`) adapted from argo's `@argo/charts` with photo-flow pipeline series colors. App shell: collapsible sidebar, breadcrumb header, mobile bottom-nav, page-header portal pattern. Routes: Pipeline, Operations, Analytics, Library (placeholder pages). Status indicator polls `GET /status` every 3s and shows camera/SSD dots + staging count.

### Deviations from prompt
- No `openapi-fetch` runtime dep — used a thin hand-rolled typed `fetch` wrapper (`src/lib/api.ts`) instead. The `gen:api` script wires `openapi-typescript` for type generation; a committed hand-crafted `api-types.ts` snapshot avoids a live server at build time. The build doesn't need openapi-fetch's runtime coupling, and the wrapper is simpler for this API surface.
- Charts vendored as `src/lib/charts/` (alias `@pf/charts`) rather than `@argo/charts` workspace dep — photo-flow is a standalone repo, and the palette is photo-flow specific. Group 9 will add visx primitives to this package.
- No visx packages installed in Group 8 — charts token system (palette/theme/tokens/CSS vars) works standalone; visx chart kinds come in Group 9.
- `@mantine/dates` and `@mantine/schedule` not included — not needed for the shell.
- `@tanstack/react-router-devtools` dep present (for dev) but not rendered in main.tsx to keep the strict build clean; add back if needed.

### Gotchas & surprises
- TanStack Router Vite plugin generates `src/routeTree.gen.ts` at build start, so `tsr generate` must run before `vite build` (or the Vite plugin handles it). Added `"build": "tsr generate && vite build"` as the combined gate.
- `exactOptionalPropertyTypes` in tsconfig is unusually strict — it prevents `prop?: T` from accepting `prop: undefined` explicitly. Watch for this when passing props or query results to optional fields.
- oxlint `img-redundant-alt` fires on any `alt` containing the word "photo". Fixed by using `alt=""` with `aria-hidden="true"` for the decorative logo.
- Vite 8 with rolldown backend warns on chunks > 500 kB (the Mantine + framer-motion bundle). Not a problem for a local PWA but worth noting.
- `babel-plugin-react-compiler` requires the `vite-plugin-babel` wrapper, not native `@vitejs/plugin-react`'s experimental compiler support — stay consistent with argo's pattern.

### Security notes
- Dev server binds on `127.0.0.1:7721` (Vite `strictPort`). The Vite proxy forwards to `127.0.0.1:7720` (uvicorn) — never leaves localhost.
- The SPA is a static build served by FastAPI in production — no additional server surface.
- No auth gate needed: both uvicorn and Vite bind localhost only.

### Tests added
None — browser-only SPA. Build (`tsr generate && vite build`) is the typecheck + bundle gate.

### Future improvements
- Swap hand-crafted `api-types.ts` for a proper `openapi-typescript` generated snapshot once Group 9 workflow is settled (run `npm run gen:api` against a live server and commit the output).
- Add `@tanstack/react-router-devtools` rendering behind `import.meta.env.DEV` once initial dev iteration is done.
- The Mantine `violet` color is mapped to `BP.violet` twice (both `grape` and `violet` entries). A future cleanup could use `BP.indigo` for `grape`.
- Consider `build.rolldownOptions.output.codeSplitting` once more routes are added to split the Mantine vendor chunk.
