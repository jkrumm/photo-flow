# Photo-Flow Control Panel — PRD

## Problem

Photo-flow is a CLI-only tool. There is no at-a-glance view of where photos are in the
pipeline (Camera → Staging → Final → Publish), no safe GUI way to trigger operations with
confirmation and live progress, and no analytics over the photo library (when shot, how many,
rating distribution, camera settings). The accumulated, safety-critical Python core
(`PhotoWorkflow`) is sound and must be *reused*, not rewritten.

## Goals

- A **local-only, always-on** web control panel that visualizes the workflow and triggers every
  operation with confirmation + live progress.
- **Reuse the Python core in-process** — FastAPI imports `PhotoWorkflow` directly; the CLI stays
  a first-class, unchanged-behavior adapter over the same core.
- **Live operation progress** over SSE (import/finalize/sync/backup), **polled status** for
  cheap signals (device mounts, counts).
- A **SQLite metadata index** powering fast analytics (time-series, ratings, EXIF settings, GPS).
- A **highly aesthetic, data-dense** React SPA reusing argo's `@argo/charts` + token system +
  app-shell, with a bespoke animated pipeline hero (framer-motion).

## Non-goals

- No rewrite of the file-move/hash/safety core in Rust/Swift/TS.
- No Windows support; no multi-camera support; no distribution to other users.
- No public exposure — binds `127.0.0.1` only. (Tailscale/phone access is a possible v2, out of scope.)
- No writing back to Immich; Photomator stays the source of truth for ratings/edits.

## Architecture: one core, three adapters

```
                ┌──────────────────────────────────────────┐
                │  photo_flow/  (existing Python core)       │
                │  PhotoWorkflow  ·  FileManager  ·          │
                │  MetadataExtractor  ·  config  ·  immich    │
                └───────────────┬──────────────────────────┘
                                │ imports directly (no shell-out)
          ┌─────────────────────┼─────────────────────┐
          ▼                     ▼                     ▼
   cli.py (Click)        photo_flow/api/ (FastAPI)   index (SQLite)
   RichReporter          QueueReporter → SSE          metadata cache
                                │
                                │ serves static build + /api + /events
                                ▼
                   control_panel/web/  (Vite React SPA, PWA)
```

### The event seam (Group 1 — the keystone)

Today progress is rendered as Rich bars **inside** `PhotoWorkflow`; `progress_callback` is dead.
Introduce a `ProgressReporter` protocol the core emits to:

- `reporter.task(desc, total)` / `.advance(n)` / `.log(level, msg)` / `.event(type, payload)`
- **`RichReporter`** (CLI) — renders to the existing Rich console; preserves current CLI output.
- **`QueueReporter`** (API) — pushes structured events onto a thread-safe queue an SSE endpoint drains.

Touch points (from code audit): ~5 `create_progress()` blocks (`import_from_camera`,
`finalize_staging`, two in `sync_gallery`, `cleanup_unused_raws`), the `_run_backup_rclone`
parse loop (already yields `(pct, speed, eta, files)` tuples), and ~30 global `console`
`info/warning/error` calls routed through `reporter.log`. **No regression net exists — change
behavior-preserving, verify the CLI still works after each method.**

### Job manager + concurrency

`PhotoWorkflow` methods are blocking sync IO. The API runs them via `asyncio.to_thread` behind a
**single-flight lock** (one mutating op at a time). Each run gets a `job_id`; `GET /events/{job_id}`
is the SSE stream; `GET /jobs/{job_id}` the terminal result. Confirmation flow:
`POST /ops/{name}?dry_run=true` returns the preview dict → UI shows it in a modal →
`POST /ops/{name}` starts the real job.

### Status: cheap vs expensive split

- **Cheap (poll every 2–3s):** `camera_connected`, `ssd_connected` (`Path.exists`), cached
  `staging_files` count. New endpoint `GET /status` returns these instantly.
- **Expensive (on demand / poll 30s when camera present):** `scan_camera_files()` (USB SD walk)
  → `GET /status/pending`. Never hashed during status (confirmed by audit).

### SQLite metadata index

- Location: `~/.photoflow/index.db` (data, not in repo).
- One row per Final JPG, keyed/invalidated by `(path, size, mtime)` (mirrors `FileManager._hash_cache`).
- Columns from the sparse `extract_metadata()` dict — **most nullable**. Re-parse the formatted
  strings (`aperture` `"f/2.8"`, `shutter_speed` `"1/250"`, `focal_length` `"35mm"`) into numeric
  columns for charting; `iso`, `rating`, `lat/long`, `date_taken` already usable.
- Tracks derived state per photo: `in_final`, `published` (in gallery, rating ≥ 4).
- Refresh triggers: after `finalize`/`sync` jobs, plus a manual `POST /index/refresh` and a
  periodic rescan (incremental — only mtime-changed files re-read).

## Frontend

- **Stack:** Vite 8 + React 19 (+ React Compiler) + TanStack Router + TanStack Query +
  Mantine 9 + Zustand. Vendored copy of argo's **`@argo/charts`** (visx) + token system
  (`palette.ts`/`theme-vars.ts`/`tokens.ts`/`theme.ts`/`charts-bridge.tsx`) + **app-shell**.
  Add **framer-motion** (argo has none) for the pipeline hero + transitions. `vite-plugin-pwa`
  for install-as-app.
- **API client:** generate types from FastAPI's OpenAPI via `openapi-typescript` (replaces argo's
  Eden/Elysia coupling); thin typed `fetch` wrapper + TanStack Query factories (argo pattern).
- **Realtime:** SSE (`EventSource`) for active job progress; TanStack Query `refetchInterval`
  (2–3s) for `/status`.
- **Served by FastAPI** in prod (static `web/dist` + SPA fallback); Vite dev server proxies
  `/api` + `/events` to uvicorn during development.

### Screens (v1, all at once)

1. **Pipeline hero** — animated Camera → Staging → Final → Publish(Homelab backup • photo website)
   flow with live counts at each node, device-connected indicators, and a "files moving" animation
   while a job runs (framer-motion, driven by SSE). Each node = a trigger affordance.
2. **Operations** — cards for import / finalize / cleanup / sync-gallery / backup(final/raws/videos);
   each opens a dry-run-preview confirm modal, then shows a live SSE progress panel
   (per-file progress, throughput/ETA for rclone, log tail).
3. **Analytics** — over the SQLite index: photos-over-time (day/week/month/year) with rating
   overlay; rating distribution (Final vs published); ISO / aperture / focal-length / shutter
   distributions; storage by stage; a GPS map of where photos were taken.
4. **Library health** — orphaned RAWs, backup freshness (local vs remote counts via
   `get_backup_availability(check_remote=True)`), last sync/backup timestamps.

## Deployment

- New console entry `photoflow serve` (uvicorn) + `python -m photo_flow.api`.
- **LaunchAgent** (matching usage-tracker/audio-proxy pattern) keeps uvicorn alive, bound to
  `127.0.0.1:7720`. Optional Caddy `photoflow.test` HTTPS entry + dotfiles commit.
- Add `fastapi`, `uvicorn`, `sse-starlette` to `setup.py`/`requirements.txt` (currently out of sync —
  also re-add `python-dotenv`).

## Decisions made (challenge if wrong)

- Port **7720**, bind localhost only. Index at `~/.photoflow/index.db`.
- FastAPI lives **inside the package** (`photo_flow/api/`) so it imports the core trivially and
  ships with the install; SPA lives in **`control_panel/web/`**, served static by FastAPI.
- Type-safe client via **openapi-typescript** (not Eden — backend is FastAPI, not Elysia).
- **Vendor** argo's charts + tokens + shell (copy, not a workspace dep) — photo-flow is a separate
  repo; track origin in a header comment.
- SSE for op progress; polling for status. framer-motion for the bespoke pipeline viz only;
  charts stay visx.

## Success criteria

- `photoflow serve` runs under a LaunchAgent; opening `http://localhost:7720` shows live pipeline
  status that updates within ~3s of plugging in the camera.
- Every CLI operation is triggerable from the UI with a dry-run-preview confirm and live progress;
  destructive ops cannot run concurrently (single-flight) and cannot be reached off-localhost.
- CLI behavior is unchanged after the event-seam refactor (manual parity check per command).
- Analytics screen renders time/rating/EXIF charts over the full Final library in <1s from the
  index (no per-load EXIF scan).
- The SPA installs as a PWA and reuses argo's chart/token/shell system (visual consistency).

## Risks

- **Safety-critical core, zero tests.** The seam refactor edits file-move code paths. Mitigate:
  behavior-preserving emitter, per-command CLI parity verification, no logic changes in Group 1.
- **EXIF string parsing** for numeric charts (aperture/shutter/focal) — handle malformed/missing.
- **Always-on daemon that can delete files** — single-flight lock, localhost bind, dry-run-first
  confirm modals, no destructive op without explicit UI confirmation.
- **Index drift** on Photomator re-rating — mtime-keyed invalidation + incremental rescan.
