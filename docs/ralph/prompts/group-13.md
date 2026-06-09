# Group 13: `photoflow serve`, static serving, LaunchAgent, docs

## What You're Doing

Ship it: add the `photoflow serve` console command, have FastAPI serve the built SPA as static
files (single localhost app), create the always-on LaunchAgent, sync packaging, and update the
docs. After this group the control panel runs at `http://localhost:7720` under launchd, installable
as a PWA.

---

## Research & Exploration First

1. Read `control_panel/PRD.md` → "Deployment".
2. Read `setup.py` `entry_points` (the existing `photoflow` console script) and `photo_flow/cli.py`
   (to add a `serve` command consistent with the others).
3. Look at an existing LaunchAgent plist pattern (the user runs usage-tracker / audio-proxy
   LaunchAgents — `~/Library/LaunchAgents/`); research `launchd` `KeepAlive`/`RunAtLoad`. Read the
   project `CLAUDE.md` for the Caddy `*.test` dev-proxy convention.

---

## What to Implement

### 1. Static serving

In `photo_flow/api/app.py`, mount the built SPA: serve `control_panel/web/dist/` at `/` with SPA
fallback (StaticFiles + an index.html catch-all that doesn't shadow `/api`, `/health`, `/status`,
`/ops`, `/jobs`, `/analytics`, `/events`, `/index`, `/backup`, `/openapi.json`). If `dist/` is
absent (dev), skip mounting and log a hint to run `npm run dev`.

### 2. `photoflow serve`

Add a Click `serve` command (`--port 7720`, `--host 127.0.0.1`) that runs uvicorn. Add it to
`setup.py`? No — it's a subcommand of the existing `photoflow` group, so just add it in `cli.py`.
Keep `python -m photo_flow.api` working too.

### 3. LaunchAgent

Create `control_panel/launchd/com.jkrumm.photoflow.plist` (template, paths documented) that runs
`venv/bin/photoflow serve` (or the pipx path), `RunAtLoad`, `KeepAlive`, logs to a file. Document
install: `cp … ~/Library/LaunchAgents/ && launchctl load …`. Bind localhost only.

### 4. Packaging + build wiring

- Ensure `setup.py`/`requirements.txt` carry all runtime deps (fastapi/uvicorn/sse-starlette/
  python-dotenv). 
- Add a `control_panel/web` build note / make target: `npm run build` produces `dist/` that
  `serve` mounts. Optional: a Caddy `photoflow.test` HTTPS entry (document; the dotfiles Caddyfile
  edit + reload is the user's to commit).

### 5. Docs

- Update the project `CLAUDE.md`: new `control_panel/` + `photo_flow/api/` + `photo_flow/index/`
  architecture, the `serve` command, the LaunchAgent, the event seam, the SQLite index, and bump
  the version + add a "Recent Changes" entry (v0.4.0 — Control Panel).
- Update `README.md` with how to build + run the control panel.

---

## Validation

```bash
venv/bin/python -m pytest -q
venv/bin/photoflow --help        # shows `serve`
venv/bin/photoflow serve --help
cd control_panel/web && npm run build    # produces dist/ for static serving
```
Manually: `venv/bin/photoflow serve` then open `http://localhost:7720` — the SPA loads and talks to
the API same-origin; SSE + status polling work end to end. Confirm the LaunchAgent plist loads.

---

## Commit

```
feat(api): photoflow serve, static SPA serving, LaunchAgent, and docs
```

---

## Done

Append final notes, then:
```
RALPH_TASK_COMPLETE: Group 13
```
