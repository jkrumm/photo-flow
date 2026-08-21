# Photo-Flow

A personal CLI tool for managing Fuji X-T4 camera photos/videos with a staging workflow for JPG photography with RAW backups, plus an optional gallery sync for high-rated images.

> **Note**: This is a personal tool designed for local use on a single developer's machine, not production software intended for distribution or multi-user environments.

## Control Panel

A local-only web UI lives at `http://localhost:7717` — pipeline status, operation triggers with live progress, and analytics over the photo library.

Built on [basalt-ui](https://www.npmjs.com/package/basalt-ui) over Mantine 9: shared theme and
`--vx-*` token system, `BasaltShell`, visx charts, and a ⌘K command palette for navigation.
Press ⌘B to collapse the sidebar.

Jobs run in the always-on daemon and are recorded durably, so closing the panel — or restarting
the service — never loses track of one:

- **Job history** on the Pipeline screen lists what actually ran, with outcome, duration and counts.
- A job the server was **still running when it stopped** (crash, logout, `make reload`) comes back
  marked `interrupted` instead of vanishing, and the pipeline advisor stops treating its timestamp
  as a completed run.
- A job that **finishes while the panel is shut** still lands in the notification bell the next time
  you open it.

### Build & run

```bash
# Build the SPA (one-time, redo after UI changes)
cd control_panel/web && npm install && npm run build && cd ../..

# Start the server
photoflow serve
# → http://localhost:7717
```

### Install as always-on daemon (launchd)

```bash
cp control_panel/launchd/com.jkrumm.photoflow.plist ~/Library/LaunchAgents/
launchctl load ~/Library/LaunchAgents/com.jkrumm.photoflow.plist
# Runs on login, restarts on crash, logs to /tmp/photoflow.{log,err}
```

To uninstall: `launchctl unload ~/Library/LaunchAgents/com.jkrumm.photoflow.plist && rm ~/Library/LaunchAgents/com.jkrumm.photoflow.plist`

### Picking up changes

```bash
make reload           # rebuild the SPA, restart the daemon, verify the live endpoints
make reload-if-stale  # the same, but a no-op when nothing changed since the last one
```

`reload-if-stale` is wired to the Claude Code `Stop` hook (`.claude/settings.json` — the one file
under `.claude/` that is version-controlled), so an agent turn that touches the panel or the Python
core ends with the running Mac app already serving it. It is cheap to call: ~50 ms when nothing
changed, ~2 s for a Python-only change (daemon restart, SPA bundle untouched), ~10 s for a panel
change (full rebuild). The stamp is written only on success, so a failed build retries next turn
rather than declaring itself deployed.

Both halves are required after any panel or API change: rebuilding `dist/` alone does not reload
the Python process, so new API routes stay invisible until the daemon restarts. `make reload`
prints a live check afterwards — the API line must read `application/json`. If it reads
`text/html`, the route is missing and FastAPI's SPA catch-all answered instead.

The Dock PWA refreshes itself. Its service worker installs the new build in the background and
claims the page; `main.tsx` listens for `controllerchange` and reloads, so an open window picks the
change up on its own within a few seconds. Without that listener the window kept serving the
precached old bundle indefinitely — a shipped change was simply never visible in the installed app,
only in a browser tab you happened to reload twice.

### Optional: HTTPS via Caddy

Add to `~/dotfiles/config/Caddyfile` (port 7718 = Vite dev; 7717 = prod):

```
photoflow.test {
  reverse_proxy 127.0.0.1:7717
}
```

Then `caddy-reload` and commit in dotfiles.

---

## Features

- Import photos and videos from Fuji X-T4 camera
- Manage workflow with staging and final folders for JPGs
- Backup RAW (.RAF) files
- Copy videos (.MOV) to external SSD
- Sync high-rated photos (rating ≥ 4) to a gallery with intelligent file handling
- Cull in the control panel — browse Final and Staging full-screen, filter on any EXIF field, rate
  with the number keys, reject and undo without moving a file, and compare or grid-review a
  whole result set
- Save a filter state as a collection, or browse a computed structure (month/album/event) —
  neither moves anything on disk
- Hand a photo to a configured external editor, JPEG or its correlating RAW, straight from the
  culling screen
- Hash-based duplicate detection and post-copy verification
- Dry-run mode for import, cleanup, and sync-gallery
- Confirmation prompts for destructive actions
- **Safety-first architecture**: copy-first approach, atomic operations, automatic backups
- **Complete metadata preservation**: All EXIF, IPTC, XMP data preserved during compression
- **Smart connectivity**: Backup via Tailscale (encrypted mesh network)
- **Beautiful CLI output**: Rich-formatted terminal output with color-coded status, progress indicators, and structured summaries
- Case-insensitive handling for .JPG/.RAF

## Quick Start

### 1. Install pipx (one-time setup)
```bash
brew install pipx
pipx ensurepath
source ~/.zshrc
```
### 2. Install Photo-Flow
```bash
git clone https://github.com/yourusername/photo-flow.git
cd photo-flow
pipx install -e .
```
### 3. Start using it anywhere!
```bash
photoflow status
photoflow import
photoflow finalize
photoflow cleanup
photoflow sync-gallery
photoflow backup
```
That's it! No virtual environments to activate, works from any directory. ✨

## Commands

### `photoflow serve`
Start the web control panel:
```bash
photoflow serve                        # http://127.0.0.1:7717 (default)
photoflow serve --port 7717 --host 127.0.0.1
```
Serves the built SPA + API on the same origin. Requires `npm run build` in `control_panel/web/` first. The PWA manifest lets you install it as a desktop app.

### `photoflow status`
Check the current status of your workflow:
- Camera connection status (color-coded: green ✓ / red ✗)
- SSD connection status (color-coded: green ✓ / red ✗)
- Pending files on camera (displayed in formatted table)
- Files in staging area

**Output**: Rich-formatted tables with color-coded status indicators

### `photoflow import`
Import files from your camera:
- JPG files → Staging folder (deleted from camera after verification)
- RAW files → RAWs backup folder (deleted from camera after verification)
- Video files → External SSD (deleted from camera after verification)

**Result**: Camera is completely empty after import, ready for new photos.

Add `--dry-run` to preview without copying files.

**Example:**
```bash
photoflow import --dry-run
photoflow import
```

**Output**: Rich progress bar showing completion, success/error indicators (✓/✗), and structured summary

### `photoflow finalize`
Move and compress approved photos from staging to final folder:
- **Atomically processes each photo**: compress → copy to Final → delete from Staging
  - **Compresses** (resize to ≤5200×3467, quality 92, 4:4:4 chroma, preserves ALL metadata)
  - **Interrupt-safe**: Ctrl+C leaves remaining files in Staging for retry
  - **Guarantees**: Every file in Final is compressed
- Copies compressed photos back to camera for viewing
- Removes orphaned RAW files (no matching JPG in Final)
- Cleans up RAW files from camera

Add `--dry-run` to preview without moving files.

**Example:**
```bash
photoflow finalize --dry-run
photoflow finalize
```

**Output**: Rich progress bars for each step (compress/move, copy to camera, cleanup), success indicators, detailed summary

### `photoflow cleanup`
Remove unused RAW files that don’t have corresponding JPGs in the Final folder.

- Always previews first; asks for confirmation before deleting
- Use `--dry-run` to see only the preview and skip deletion

**Example:**
```bash
photoflow cleanup --dry-run
photoflow cleanup
```

### `photoflow sync-gallery`
Sync high-rated photos (4+ stars) to your gallery:
- Copies new high-rated images to gallery
- Updates changed images (using fast hash comparison)
- Removes images no longer rated 4+
- Generates metadata JSON
- Builds and syncs gallery to remote server

Add `--dry-run` to preview without syncing files.

**Example:**
```bash
photoflow sync-gallery --dry-run
photoflow sync-gallery
```

### `photoflow backup`
Backup to your homelab over Tailscale, with an interactive source picker:
- **Tailscale connectivity**: Connects via encrypted mesh network (no port exposure needed)
- **Sources**: `Final` (JPGs + their `.photo-edit` edit history), `RAWs`, `Videos` — or all three
- **Smart filtering**: Excludes system files (`.DS_Store`, `._*`, `Thumbs.db`, etc.) - only backs up your photos
- **Trash, not deletion**: replaced/removed files are parked in a timestamped trash folder for 30 days
- **Optional Staging mirror**: a fourth, opt-in source that mirrors Staging (JPGs + sidecars) so
  work-in-progress survives a disk failure between import and finalize. No trash — the mirror is
  transient and empties itself once you finalize. Never included in "all"; pick it explicitly.

Add `--dry-run` to preview without sending data.

**Example:**
```bash
photoflow backup --dry-run
photoflow backup
```

### `photoflow trash`
Manages the soft-delete trash that the control panel's Photos screen writes to. Culling never
deletes: the JPG and its `.photo-edit` edit history are **moved** to `~/Pictures/.photoflow-trash`
(same filesystem as Final and Staging, so the move is an instant, atomic rename).

- **A trashed photo keeps its RAW.** While the entry exists, `finalize` and `cleanup` both treat
  the matching `.RAF` as a keeper. It becomes an orphan only once you purge the entry.
- **Retention is 30 days**, measured from when the photo was trashed — not from the file's date.
- `purge` never touches an entry that is still inside the retention window, and asks before
  deleting anything.

```bash
photoflow trash stats                  # count, size, how much is past retention
photoflow trash list                   # newest first, with age and rating
photoflow trash restore 42 43          # put them back where they came from
photoflow trash purge --dry-run        # preview what 30-day retention would release
photoflow trash purge --days 60
```

## The Photos screen (culling)

Open the control panel (`photoflow serve` → http://127.0.0.1:7717) and pick **Photos** under
Workflow. It replaces a Bridge culling session:

- **One sidebar, on the right**, nothing in it but collapsible cards — **Narrowing**, **Folders**
  (with saved collections directly under the roots), **Structure**, **Filters**, **Info**,
  **Compare**, **Tools**, **Cull** and **View**. Each remembers whether you left it open, caps at
  44% of the window height and scrolls inside itself, so opening one never pushes the others away.
  Drag the sidebar's left edge to resize it. The only thing floating over the image is the position
  counter and the button that hides the sidebar.
- **Everything else is the photograph.** The screen runs edge to edge — no page gutter, no page
  scrollbar — and the filmstrip spans the full window width underneath, nav rail included.
- **Browse** Final and Staging side by side, with **Folders** for the physical roots and
  **Structure** for a computed layout (flat / month / album / event) — every group is a query, so
  nothing on disk moves and a photo that fits no album still shows up somewhere.
- **Narrow in two layers.** A folder or a saved **collection** *scopes* the library; the **Filters**
  card *refines* inside it — refine overrides scope on a shared field (pick a different rating
  inside a "5-star" collection) and intersects on every other. The **Narrowing** card lists what's
  currently applied, what supplied it, and what removing it would cost in row count. **Save the
  current filter state as a collection** from the Folders card to come back to it later; collections
  live in `~/Pictures/photoflow.toml`, next to the library.
- **Filter** on anything the EXIF carries — rating, ISO, aperture, shutter, focal length, camera,
  lens, colour label, keyword, filename. Each filter shows live counts (with a histogram for the
  numeric ones), and an option that would return nothing is never offered.
- **Rate** with `0`–`5`. The star is written straight into the JPG as `XMP-xmp:Rating` via
  exiftool, so `sync-gallery` and Immich pick it up with no extra step. Photomator is the other
  writer of that tag — rate in one place per photo, not both. **Every rating write is undoable**:
  `⌘Z` restores whatever it overwrote, and a write past 20 frames asks for confirmation first,
  naming how many frames actually carry a different value.
- **Reject** with `x` (or `⌫`/`Delete`) instead of rating — the photo dims in the strip and drops
  out of the default view on the next refetch, but **nothing moves on disk**. Pressing the key
  again un-rejects. The **Cull** card shows the reject count and a "Show rejected" switch; only its
  **"Move rejects to trash…"** button actually files them, behind a confirm.
- **Trash** is therefore a deliberate, batched step, not a keypress — restore what it moved with
  `photoflow trash restore` (see above).
- **Step** with `←`/`→` (or `j`/`k`). Neighbours are pre-rendered and pre-decoded, so the next
  frame is already on screen; a warm thumbnail serves in ~2 ms.
- **Compare** up to 4 frames on one shared zoom/pan transform — `c` compares the current frame with
  its neighbour, `shift`+arrows grow the set, `cmd`/`ctrl`-click a filmstrip frame to add a
  non-adjacent one, `p` picks the focused frame as keeper and rejects the rest, `Esc` leaves. Above
  the 2048px thumbnail tier, 1:1 serves the master's own bytes (`GET /api/photos/original`), not an
  upscaled proxy.
- **Contact sheet** (`g`) — a virtualized grid over the entire result set, density adjustable with
  `[`/`]`. Selection is the same marked set compare uses: `shift`-click a range, `cmd`/`ctrl`-click
  to toggle, then a rating key writes to everyone selected — the one place on this screen a single
  keystroke broadcasts.
- **Edit** with `e`, the Tools card, or right-click the photograph — hands the JPG (or, for RAW
  developers, its correlating RAF) to a configured external editor. It's a launch, not a write:
  photo-flow doesn't wait for it or learn what changed, and the edit shows up on the next reindex
  the same way a Photomator edit does. Configure editors under `[[editors]]` in
  `~/.photoflow/config.toml` (see Configuration below); with none configured the button says so.
- `i` toggles the sidebar, `f` the filmstrip, `z` 1:1 zoom, `⌘Z` inverts the last action (trash or
  rating, whichever happened last).

Thumbnails are cached under `~/.photoflow/thumbs`. That directory is derived and disposable —
deleting it costs nothing but regeneration.

## Configuration

Paths default to the literals below, hardcoded in `photo_flow/config.py`. To override them without
touching source, drop an optional `~/.photoflow/config.toml` — machine facts: library roots,
`[[cameras]]`, `[[editors]]`. A second, optional file lives *inside* the library
(`<library root>/photoflow.toml` — by default `~/Pictures/photoflow.toml`) and holds facts about
the photographs themselves: saved collections and three organisation axes (stage/layout/naming)
that are accepted and validated but currently **inert** — no operation reads a non-default value
yet, so setting one does nothing. It travels if you copy the library to another machine; the
install file does not.

```bash
photoflow config show    # resolved roots, cameras, organisation axes — and where each came from
photoflow config check   # validate both files; non-zero exit on a problem
photoflow config init    # write ~/.photoflow/config.toml holding exactly today's defaults
```

`config init` refuses to overwrite an existing file. A bad install file is fatal (the daemon won't
start rather than run against a guessed tree); a bad library file degrades to defaults and reports.
Roots are refused by containment, not a blocklist — no `/`, `/Users`, `/Volumes`, `/System`,
`~/.ssh`, or anything inside/under one of those, checked on the fully resolved path. This exists
because `sync-gallery` publishes rating≥4 JPGs under `FINAL_PATH` to a public host — a mistyped
root is a data-exfiltration bug, not just a wrong-folder bug.

To hand photos to an external editor from the Photos screen's Tools card, add one row per
application under `[[editors]]`:

```toml
[[editors]]
id = "shutterflow"
name = "Shutterflow"
app = "Shutterflow"       # macOS application name, resolved via `open -a` — not a path
handles = ["jpeg"]        # or ["raw"], or both for an app that does both
```

Defaults, byte-identical to the pre-v0.4.16 hardcoded literals:
```python
CAMERA_PATH = Path("/Volumes/Fuji X-T4/DCIM")
STAGING_PATH = Path("/Users/johannes.krumm/Pictures/Staging")
RAWS_PATH = Path("/Volumes/EXT/Bilder/RAWs")
FINAL_PATH = Path("/Users/johannes.krumm/Pictures/Final")
SSD_PATH = Path("/Volumes/EXT/Videos/Videos")
GALLERY_PATH = Path("/Users/johannes.krumm/SourceRoot/photo-flow/photo_gallery/src")
TRASH_PATH = Path("/Users/johannes.krumm/Pictures/.photoflow-trash")

# Remote backup (homelab) - via Tailscale
HOMELAB_USER = "jkrumm"
HOMELAB_HOST = "100.85.139.104"  # Tailscale IP
HOMELAB_DEST_PATH = Path("/home/jkrumm/ssd/SSD/Bilder/Fuji")
RSYNC_FLAGS = ["-av", "--delete", "--partial", "--whole-file", "--progress"]
RSYNC_EXCLUDE_PATTERNS = [".DS_Store", "._*", "Thumbs.db", ".Spotlight-V100", ".Trashes", ".fseventsd"]
RSYNC_SSH_CMD = "ssh -T -c aes128-gcm@openssh.com -o Compression=no -o ConnectTimeout=5"

EXTENSIONS = {'.JPG', '.RAF', '.MOV'}
```

`RAWS_PATH` is deliberately not reachable from any culling-screen endpoint except the read-only
RAW hand-off — see SAFETY.md.

## Output Examples

The tool provides beautiful, color-coded terminal output:

```
Photo-Flow Status Report

┏━━━━━━━━━━━━━━┳━━━━━━━━━━━━━━━━━┓
┃ Component    ┃ Status          ┃
┡━━━━━━━━━━━━━━╇━━━━━━━━━━━━━━━━━┩
│ Camera       │ ✓ Connected     │
│ External SSD │ ✓ Connected     │
└──────────────┴─────────────────┘

Pending files on camera:
┌────────────────────┬───────┐
│ Videos (.MOV)      │    45 │
│ Photos (.JPG)      │   120 │
│ RAW files (.RAF)   │   120 │
└────────────────────┴───────┘

Staging status: 12 files ready for review
```

All commands provide:
- ✓ **Success indicators** in green
- ✗ **Error indicators** in red
- **Structured summaries** of results
- **Progress updates** during long operations

### Prerequisites
- **exiftool** for metadata preservation: `brew install exiftool`
- **rsync** for backups: usually pre-installed on macOS
- Node.js and npm for gallery build/sync (optional, respects `.nvmrc` if present)

## File Structure
```
Camera (before import):
/Volumes/Fuji X-T4/DCIM/
├── 102_FUJI/
│   ├── DSCF0430.JPG
│   ├── DSCF0430.RAF
│   └── DSCF1451.MOV

Camera (after import):
/Volumes/Fuji X-T4/DCIM/
└── 102_FUJI/  # Empty - all files moved

Local:
Pictures/
├── Staging/          # DSCF0430.JPG (moved from camera)
└── Final/            # Final approved photos → upload to immich

External SSD:
/Volumes/EXT/
├── Videos/Videos/    # DSCF1451.MOV (moved from camera)
└── Bilder/RAWs/      # DSCF0430.RAF (backup)

Gallery workspace:
/Users/johannes.krumm/SourceRoot/photo-flow/photo_gallery/
└── src/images/       # Synced high-rated JPGs
```

## Maintenance

### Update after code changes
Since it's installed in editable mode (`-e`), your changes are reflected immediately. No need to reinstall!

### Upgrade Photo-Flow
```bash
pipx upgrade photo-flow
```
### Uninstall
```bash
pipx uninstall photo-flow
```

## For Developers

If you prefer working in a virtual environment:
```bash
python3 -m venv venv
source venv/bin/activate
pip install -e .
```
Note: You'll need to activate the venv each time before using commands.

## License

This project is licensed under the terms of the license included in the repository.
