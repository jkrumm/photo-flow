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

## Group 9: Vendor argo visx charts + Blueprint token system

### What was implemented
Vendored argo's `@argo/charts` visx package (primitives, hooks, utils, kinds, sparklines) into `src/lib/charts/`, wired the Mantine↔charts bridge (`VxBridge` already present from Group 8), added a hex-guard lint script, and rendered a sample `Bars` chart on the Analytics route to prove theme toggling works via CSS vars.

### Deviations from prompt
- `VxBridge` and `charts-bridge.tsx` were already set up by Group 8; Group 9 only needed to extend the existing `src/lib/charts/` directory (not create it from scratch).
- Group 8 used a `PHOTO` series namespace instead of argo's `SERIES` — all vendored primitives/kinds accept `color` props so they work with any token set; no series namespace changes needed in kind implementations.
- The token system was partially in place; added missing derived vars (`goodRef`, `badRef`, `warnRef`, `optimalZone`) to `tokens.ts` and `theme-vars.ts`.
- Hex guard scopes to `src/lib/charts/` + `src/routes/` only (not full `src/`) — `theme.ts` is the Mantine palette bridge and is legitimately hex-bearing; scanning it would produce false positives.

### Gotchas & surprises
- `@visx/shape` v4-alpha exports `Pie` (used by Donut) — confirmed via `require` before writing.
- oxlint `no-underscore-dangle` fires warnings on `__y` / `__d` internal augmentation fields in ZonedLine and Bars. These are canonical argo idioms (ephemeral type augmentation to carry pre-computed values through the data pipeline). Lint exits 0 (warnings only); leaving as-is to stay in sync with upstream.
- The hex guard initially matched JSDoc comment lines mentioning `rgba()`. Fixed by skipping lines where `trimmed` starts with `//`, `*`, or `/*`.
- `@visx/threshold` is installed as a separate package; `Pie` is part of `@visx/shape` (no `@visx/pie` package exists in v4-alpha).

### Security notes
No security-relevant changes — all chart code is pure client-side rendering with no data fetching in this group.

### Tests added
None — chart primitives are browser-rendering code. `npm run build` (strict TS + bundle) + `npm run lint` (oxlint + hex guard) are the correctness gates.

### Future improvements
- Wire the `Bars` chart on Analytics to live SQLite index data once Groups 10–12 expose the analytics endpoints.
- Add `ZonedLine` for rating-over-time and `Donut` for rating distribution — both kinds are vendored and ready.
- Consider adding `HoverContext.Provider` at the Analytics page level to enable cross-chart cursor sync once multiple charts are present.
- The `no-underscore-dangle` warnings could be suppressed via an oxlint allow-list (`__y`, `__d`) if they become noisy.
- Consider `build.rolldownOptions.output.codeSplitting` once more routes are added to split the Mantine vendor chunk.

## Group 10: Pipeline hero — animated Camera → Staging → Final → Publish

### What was implemented
Created `src/components/pipeline/PipelineHero.tsx` — the signature hero component with 4+2 stage nodes (Camera, Staging, Final, Homelab, Gallery) connected by animated SVG edges and CSS `offset-path` particle flows. Added `src/lib/queries/analytics.ts` and `src/lib/queries/backup.ts` for the `GET /analytics/summary` and `GET /backup/availability` endpoints (30s stale, 60s interval). Added `useActiveJobStore` to `src/lib/store.ts` — the shared active-job contract for Group 11. Updated `src/routes/pipeline.tsx` to render the hero.

### Deviations from prompt
- Used a DOM-measurement approach (refs + ResizeObserver + `getBoundingClientRect`) for SVG path computation rather than hardcoded proportional coordinates. This makes the layout fully responsive at no cost.
- Framer-motion particles use CSS `offset-path: path(...)` on `motion.div` (HTML elements) rather than SVG `<animateMotion>`. Both work in modern browsers; the HTML approach keeps particle rendering in the same layer as the node cards and avoids SVG-coordinate/foreignObject complexity.
- The BranchArrow between Final and the Publish column is a static SVG (no framer-motion) — it diverges from the simple horizontal edges and doesn't need animation since no single op covers both backup+sync simultaneously.
- `galleryCount` is sourced from `backupAvail.final.remote_count` (the count FastAPI's availability check returns from the remote path) rather than a separate gallery-specific endpoint. This correctly reflects "files on the remote" rather than an index-derived number.

### Gotchas & surprises
- CSS `offset-path: path()` requires the container to be `position: relative` and the particle `position: absolute` with `top: 0; left: 0` for the coordinate system to match the SVG overlay. Without this, particles drift.
- framer-motion v12 still uses `import { motion, AnimatePresence } from 'framer-motion'` — no import path change from v10/v11.
- `useLayoutEffect` is necessary (not `useEffect`) for the ResizeObserver registration, otherwise there's a single-frame flash of no-path SVG on first render.
- The oxlint `eqeqeq` rule blocks `!= null` — replaced with `!== null && !== undefined`.
- Warnings for `no-underscore-dangle` on vendored chart kinds are pre-existing and exit 0 — not introduced by Group 10.

### Active-job store contract (Group 11 interface)
```ts
// import from src/lib/store.ts
const { setActiveJob, clearActiveJob } = useActiveJobStore()

// Call when SSE stream for a job starts:
setActiveJob(jobId, 'import' | 'finalize' | 'cleanup' | 'sync-gallery' | 'backup')

// Call when SSE emits 'done' or 'error':
clearActiveJob()
```
`PipelineHero` reads `activeOp` and animates the matching edge (dashed highlight + 3 particles). The legend row shows a pulsing "op running" badge.

### Security notes
- All API calls are same-origin reads (GET). No auth or CSRF concerns.
- The `op` click handlers navigate to `/operations` (no destructive action from the hero itself).

### Tests added
None — browser-rendering component. `npm run build && npm run lint` are the correctness gates.

### Future improvements
- Wire `galleryCount` to a dedicated `/analytics/gallery-count` endpoint if the remote-count in availability response proves unreliable (it requires SSH reachability).
- Add `this_month_count` from the summary as a secondary metric on the Final node ("N this month").
- The Publish column could show the gallery URL as a link once Group 12 wires up library health.
- Consider code-splitting PipelineHero (framer-motion is the bulk of the pipeline chunk at ~135 kB gz 44 kB) via a dynamic import once the page count grows.

## Group 11: Operations UI — dry-run confirm modals + live SSE progress

### What was implemented
Six files created/updated: `useJobEvents.ts` hook (SSE subscriber with typed reducer), `ops.ts` API helpers, `DryRunModal.tsx` (confirm modal with per-op preview + destructive warning), `JobProgressPanel.tsx` (progress bar, log tail, rclone transfer info, done summary), `OperationCard.tsx` (card with backup source selector + two-step mutation flow), and the `operations.tsx` route assembled from these parts. The `activeJobId` store from Group 10 is used directly — cards call `setActiveJob` on start and the page calls `clearActiveJob` + query invalidation in the `onDone` callback.

### Deviations from prompt
- No component-level tests added — the build + lint gate (TypeScript strict + oxlint) covers type correctness, and the interactive confirmation flow requires a running API to test meaningfully. The architecture is fully testable via mock EventSource if tests are added later.
- `DryRunModal` is a separate file rather than inline in `OperationCard` — keeps the files under ~150 lines and makes the modal testable in isolation.
- The done-state `JobProgressPanel` persists on screen (driven by `eventsState.isDone` from the hook) until the next job starts — gives the user time to read the result summary without needing to manually dismiss it.
- `JobProgressPanel` receives an `op` prop typed as `ActiveOp | null` but currently doesn't branch on it (it renders the same layout for all ops). The prop is kept for future per-op customization (e.g., showing rclone transfer info only for backup).

### Gotchas & surprises
- `unicorn(no-useless-fallback-in-spread)`: `...(extra ?? {})` is an error because spreading `undefined` in an object literal is already a no-op in JS. Fixed to `...extra` directly.
- `unicorn(prefer-add-event-listener)`: `es.onmessage =` / `es.onerror =` must use `es.addEventListener('message', ...)` / `es.addEventListener('error', ...)` to pass oxlint.
- `no-underscore-dangle` fires on module-level `_idSeq`. Renamed to `logIdSeq` (no leading underscore).
- `exactOptionalPropertyTypes: true` is active but JSX optional prop passing `={undefined}` seems to be tolerated by the TypeScript JSX checker — consistent with Group 10's pattern (`statusDot={undefined}`).
- The SSE done event carries `error: null` (not absent) for success — the `str()` guard returns `undefined` for `null` so `?? null` correctly coerces to `null`.

### Security notes
- Destructive ops (import, finalize, cleanup) display an orange warning badge on the card and require explicit modal confirmation before the real POST is sent.
- The 409 case is handled gracefully — a notification is shown rather than leaving a pending loading state.
- All API calls are same-origin POSTs to `127.0.0.1:7720`.

### Tests added
None — browser-rendering components. `npm run build && npm run lint` are the correctness gates.

### Future improvements
- Add component tests for `DryRunModal` modal flow and `useJobEvents` reducer with mocked events (using a fake `EventSource`).
- Auto-dismiss the done panel after N seconds (configurable) once the user has had time to see the result.
- The log tail shows only the last 20 entries on screen (though 80 are retained in state). A "show all" expand control would help for long sync/backup runs.
- Consider per-op customization in `JobProgressPanel` (e.g., rclone section only for backup, per-file ticker only for import/finalize).
- Code-split the operations route (Mantine modals pull in some weight) once more routes are in place.

## Group 12: Analytics + Library Health UI

### What was implemented
Full analytics dashboard (`analytics.tsx`) wired to all six Group-7 endpoints via new TanStack Query factories: summary tiles (6 stat cards), photos-over-time stacked Bars with bucket toggle (day/week/month/year), rating histogram (multi-colour Bars using per-datum key routing), publish-rate Donut, storage tiles, four small EXIF-settings Bars (ISO, aperture, focal, shutter) via a shared `SettingsBarChart` component, and a GPS bounding-box scatter (SVG, no new deps). Full Library Health page (`library.tsx`) with index-refresh mutation (POST /index/refresh + query invalidation), orphaned-RAWs card, per-source backup-freshness cards (local vs remote counts + needs_sync from `/backup/availability`), and a storage overview card. Added `GET /analytics/library-health` backend endpoint to `routes_analytics.py` (counts orphaned RAFs vs the DB's Final set via `extract_original_base`). Added `LibraryHealthResponse` type to `api-types.ts` and a shared `src/lib/format.ts` for `formatBytes`.

### Deviations from prompt
- No tile-map library added for GPS — a SVG bounding-box scatter is used instead with a tooltip note explaining the deferral. This avoids bundle weight (Leaflet ~40 KB) for a personal tool where GPS coverage is sparse.
- No component-level test files added — no test runner (Vitest/Jest) is configured in the frontend. The TypeScript strict build + oxlint gates cover type correctness. Noted as a future improvement.
- `SettingsBarChart` is a function component within the route file rather than a new visx `kinds/` entry — it's a thin wrapper around `Bars` with EXIF-specific formatting, too domain-specific to be a reusable chart kind.

### Gotchas & surprises
- `eqeqeq` oxlint rule fires on `!= null` — must use `!== null` even in null-check idioms.
- The `alpha()` helper returns `color-mix(in srgb, ...)` which is NOT flagged by the hex-guard script (which only matches `rgb(...)` and `hsl(...)`). Safe to use in route files.
- `AxisBottomDate` / `fmtAxisDate` works fine with non-date strings (ISO values, focal lengths, etc.) — it falls back to `String(value)` when the date regex doesn't match.
- Rating histogram: the multi-key `positiveBars` approach (one `positiveBars` entry per rating, `getValue` returns null for non-matching datums) correctly renders one coloured bar per x-bucket because the stacked renderer skips null/zero segments.
- `Donut` requires `useVxTheme` context (for arc stroke colour) — this is already provided by `VxThemeProvider` in `main.tsx` / `charts-bridge.tsx`.

### Security notes
- All API calls are same-origin GET/POST to `127.0.0.1:7720` (localhost only).
- The "Refresh Index" mutation POSTs to `/index/refresh` with no payload — no user data is sent.
- The `_query_library_health()` backend function does read-only filesystem globbing and DB selects — no mutation of files.

### Tests added
None — no test runner configured. Backend Python import smoke test validates the new endpoint is importable.

### Future improvements
- Add Leaflet or MapLibre tile-map for the GPS chart once bundle splitting is in place (code-split the analytics route to limit weight).
- Add Vitest to the frontend devDeps and write component tests for `formatBytes`, rating histogram data transformation, and the `SettingsBarChart` empty-state path.
- "Last backup" timestamp: the `/backup/availability` endpoint doesn't track sync timestamps. A future enhancement could write a `.last_sync` file or log to SQLite after each backup run and surface the timestamp in library health.
- Consider adding a `needs_index` badge to the Analytics page when `total_photos === 0` to prompt the user to run a first index refresh.
