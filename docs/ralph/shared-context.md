# Photo-Flow Control Panel — RALPH Shared Context

You are implementing: **a local-only, always-on web control panel for photo-flow** — a FastAPI
server that imports the existing Python core directly and serves an aesthetic React SPA showing
live pipeline status, triggering operations with confirmation + live SSE progress, and rendering
analytics over a SQLite metadata index. Read this fully before starting your group.

The authoritative design is **`control_panel/PRD.md`** — read it first, every group.

---

## What Photo-Flow Is

Photo-flow is a personal CLI tool for a Fuji X-T4 photography workflow:
**Camera → Staging → Final → Publish** (Homelab backup + a public photo website). It imports
JPG/RAF/MOV off the camera with hash-verified copy-then-delete, finalizes Staging JPGs into Final
at full quality (carrying Photomator `.photo-edit` sidecars), syncs rating ≥ 4 photos to a web
gallery, and backs Final/RAWs/Videos up to a homelab over Tailscale (rclone). Ratings/edits come
from Photomator (embedded XMP); Immich is a read-only viewer.

**Safety-first, no-data-loss tolerance.** The file-move/hash core is battle-tested and has **zero
automated tests**. Do not change its behavior — only add the seam described in Groups 2–3. When in
doubt, preserve existing behavior exactly and verify the CLI still works.

This work **reuses the Python core in-process** (FastAPI imports `PhotoWorkflow`); it does NOT
rewrite anything in Rust/Swift/TS. The CLI stays a first-class, behavior-unchanged adapter.

---

## Repository Layout

| Path | What |
|-|-|
| `photo_flow/workflow.py` | `PhotoWorkflow` — the core. All operations return `Dict[str,int]`. |
| `photo_flow/cli.py` | Thin Click adapter over `PhotoWorkflow`. Must stay behavior-unchanged. |
| `photo_flow/file_manager.py` | Hash-verified copy, `_hash_cache` (path,size,mtime,partial). |
| `photo_flow/metadata_extractor.py` | `extract_metadata()` → sparse dict (rating, date_taken, iso, aperture, etc.). |
| `photo_flow/console_utils.py` | Rich console: `console`, `success/error/info/warning`, `create_progress()`. |
| `photo_flow/config.py` | Hardcoded paths + Tailscale hosts + rsync/rclone settings. |
| `photo_flow/immich_client.py` | `trigger_immich_scan()` — reads `.env` (already present on disk). |
| `photo_flow/api/` | **NEW** — FastAPI app (you create this). Imports the core directly. |
| `control_panel/web/` | **NEW** — Vite React SPA (you create this). |
| `control_panel/PRD.md` | The authoritative design doc. Read first. |
| `setup.py` / `requirements.txt` | Packaging. `console_scripts: photoflow=photo_flow.cli:photoflow`. |
| `photo_gallery/` | Existing Astro gallery (the website build target). Do NOT touch. |

**Reference repo for frontend reuse:** `/Users/johannes.krumm/SourceRoot/argo` — its
`packages/charts/` (`@argo/charts`, visx primitives + Blueprint token system) and
`apps/dashboard/src/components/app-shell/` are vendored (copied) in Groups 8–9. Read argo's files
directly when those groups run; they are the source of truth for the chart/token/shell patterns.

---

## Tech Stack

| Concern | Choice |
|-|-|
| Core | Python 3.9+, existing (Click, Rich, Pillow, piexif, defusedxml) |
| API | FastAPI + uvicorn + sse-starlette; imports `PhotoWorkflow` in-process |
| Index | SQLite (`sqlite3` stdlib) at `~/.photoflow/index.db` |
| Concurrency | `asyncio.to_thread` + single-flight `asyncio.Lock` (one mutating op at a time) |
| Realtime | SSE (`EventSource`) for op progress; polling for status |
| Frontend | Vite 8, React 19 (+ React Compiler), TanStack Router + Query, Mantine 9, Zustand |
| Charts | Vendored `@argo/charts` (visx) + Blueprint token system (CSS-var architecture) |
| Animation | `framer-motion` (the pipeline hero only — charts stay visx) |
| API types | `openapi-typescript` generated from FastAPI's OpenAPI (NOT Eden — backend is FastAPI) |
| PWA | `vite-plugin-pwa` |
| Server port | `127.0.0.1:7720` (localhost only — never exposed) |

---

## Validation Commands

The runner auto-detects which to run. **Python always; Node only once `control_panel/web/` exists.**

**Python (every group):**
```bash
venv/bin/python -c "import photo_flow.cli, photo_flow.workflow, photo_flow.metadata_extractor"  # import smoke
venv/bin/python -m pytest -q     # new unit tests (tests/ dir)
```

**Node (only when `control_panel/web/package.json` exists):**
```bash
cd control_panel/web && npm run build   # vite build = tsc strict + bundle; fails on type errors
npm run lint
```

**Manual CLI parity (Groups 2–3, do this yourself before signaling complete):**
```bash
venv/bin/photoflow status            # must render identically to before
venv/bin/photoflow finalize --dry-run
venv/bin/photoflow import --dry-run
```

Use the repo `venv/` (already present). Group 1 installs the new deps into it with `pip install -e .`.

---

## Research Before Implementing

1. **Read `control_panel/PRD.md` first** — it has the architecture, decisions, and per-screen scope.
2. Explore existing code with Read/Grep before writing — match patterns (return-dict shapes, Rich usage).
3. For FastAPI/SSE/sse-starlette/openapi-typescript/framer-motion/visx specifics, research with
   Context7 or web before coding — do not invent APIs.
4. For Groups 8–9, read the actual argo files under `/Users/johannes.krumm/SourceRoot/argo` — copy
   real code, adapt the `[data-mantine-color-scheme]` coupling as noted in the PRD.
5. The group prompt is direction, not prescription — a better approach you can justify is welcome,
   but **never** change core file-move behavior.

---

## Learning Notes

After completing each group, **always append** to `docs/ralph/RALPH_NOTES.md`:

```markdown
## Group N: <title>

### What was implemented
<1–3 sentences>

### Deviations from prompt
<what you changed and why>

### Gotchas & surprises
<library APIs, quirks, tooling surprises>

### Security notes
<localhost binding, destructive-op guards, anything security-relevant>

### Tests added
<list of test files/functions added>

### Future improvements
<deferred work, tech debt>
```

---

## Commit Format

Conventional commits, no AI attribution:
```
feat(api): ...
feat(web): ...
refactor(core): ...
```

**Use raw `git` only.** `git add <files>` + `git commit -m "..."`. Do NOT invoke `/commit`, `/pr`,
`/check`, `/review`, `/ship` or any slash-command skill — they are interactive and will hang/no-op
in headless mode. Stage only files you changed. Commit before signaling completion. Do NOT push.

---

## Completion Signal

Output exactly one of these as the very last line (literal text, not in a code block):

```
RALPH_TASK_COMPLETE: Group N
```

If blocked by something unresolvable:

```
RALPH_TASK_BLOCKED: Group N - <one sentence>
```
