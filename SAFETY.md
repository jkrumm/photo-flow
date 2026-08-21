# Photo-Flow Safety Architecture

## Core Safety Principles

This project implements a **safety-first architecture** designed to prevent data loss under all circumstances, including power failures, network interruptions, and user interruptions (Ctrl+C).

## Safety Mechanisms

### 1. Copy-First Approach
- **Never delete source files** until the copy is verified
- All operations use temporary files first
- Hash verification confirms file integrity after every copy
- Source files remain untouched until new version is proven valid

### 2. Atomic File Operations
- File replacements use atomic `replace()` operations
- Temporary files ensure no partial writes to final destinations
- Operations either complete fully or leave original unchanged
- No intermediate corrupted states possible

### 3. Backup & Restore System
- Automatic `.backup` files created before in-place modifications
- Failed operations automatically restore from backup
- Multiple layers of fallback for critical operations

### 4. Complete Metadata Preservation
- Uses industry-standard `exiftool` for metadata operations
- Preserves ALL metadata types: EXIF, IPTC, XMP, ratings, keywords, GPS
- Verification step ensures metadata integrity after processing
- Zero metadata loss guaranteed

### 5. Interruptible Operations
- **Safe to Ctrl+C at any stage** without data corruption
- Temporary files automatically cleaned up on interruption
- Original files never left in invalid states
- Operations can be resumed without conflicts

### 6. Graceful Error Handling
- Pre-flight checks validate all dependencies (exiftool, rsync, paths)
- Detailed error messages with actionable solutions
- Automatic cleanup of temporary files on any failure
- No silent failures - all errors reported clearly

### 7. Network Resilience
- **Tailscale connectivity** for encrypted mesh network backups
- Connection timeouts prevent hanging operations
- rsync partial transfers allow resuming interrupted uploads

### 8. Verification at Every Step
- Hash comparison after every file copy
- Metadata verification after processing
- File system validation before destructive operations

### 9. Soft Delete — Culling Never Unlinks (`trash.py`)
Culling in the control panel is the only routine operation that removes a photo from the pipeline,
so it is the one path that must be reversible:

- **Move, never unlink.** The JPG and its `.photo-edit` edit history are renamed into
  `~/Pictures/.photoflow-trash`. That location is deliberate: it shares a filesystem with both
  Final and Staging, so the move is an atomic rename rather than a copy that can half-finish.
- **The sidecar always travels.** `.photo-edit` is the only copy of Photomator's edit history —
  it is never left behind and never deleted on its own.
- **Move first, record second.** The DB row is written only after the files have landed. A failed
  insert moves them back, and if that move-back itself fails the failure is reported rather than
  papered over. An entry directory with no row (a kill between the two steps) is **adopted** on
  the next `trash list` — never reaped.
- **A trashed photo protects its RAW.** `compute_raw_keep_bases()` unions the trash into the
  keep-set, so `finalize` step 4 — which unlinks orphaned RAWs with no preview and no
  confirmation — cannot destroy the RAW of a photo you can still restore. The RAW becomes an
  orphan only once the entry is purged.
- **Retention keys off `trashed_at`, never file mtime.** A photo keeps its capture-time mtime, so
  an mtime sweep would purge a freshly-culled batch of old photos immediately. `purge` refuses to
  touch anything inside the retention window and prompts before deleting.

Derived data has no such protection and needs none: `~/.photoflow/thumbs` and `~/.photoflow/index.db`
are both rebuildable from the photos themselves.

### 10. Undoable Ratings, and a Gate on Broadcast Writes (v0.4.19)
Rating is not a side effect of culling — it is the entire output of a cull pass, and Photomator
treats the embedded `XMP-xmp:Rating` tag as its single source of truth. A batched write that runs
`exiftool -overwrite_original -XMP-xmp:Rating=N` across a multi-frame selection with no inverse is
a data-loss defect, not a convenience gap: one wrong keystroke over a large selection destroys
unrecoverable judgement. This was found by adversarial review of the compare/contact-sheet work
(v0.4.17–18), which had wired batch rating writes with exactly that gap.

- **Every rating write returns what it overwrote.** `POST /api/photos/rating` reads and returns
  each touched path's prior rating **unconditionally**, not gated on batch size — the client can
  always build the exact inverse, not only above some size guess.
- **`POST /api/photos/rating/undo` is the inverse**, restoring each path to its own prior value.
  Paths are grouped by target rating (at most 7 `exiftool` calls — the whole -1..5 range —
  regardless of batch size). A group whose write fails reports every path in that group in
  `failed_paths` rather than guessing which ones actually failed: an undo would rather over-report
  a failure than tell the caller it landed when it didn't.
- **`⌘Z` inverts whichever action happened last**, trash or rating — the SPA tracks a single
  `UndoableAction`, not a trash-only ref.
- **A write past 20 frames is gated behind a confirm** naming the count and how many of the
  selected frames currently carry a *different* rating (the number that would actually be
  overwritten). The confirm names `⌘Z` as the secondary safeguard — the load-bearing one is that
  the write is undoable at all, at any size.

### 11. The Library-Config Root Guard — Validation as a Safety Feature (`library_config.py`, v0.4.19)
A hand-edited `~/.photoflow/config.toml` can repoint any library root, and `sync_gallery` rsyncs
every rating≥4 JPG under `FINAL_PATH` to a **public** host — so a mistyped root doesn't just point
an operation at the wrong folder, it can turn a routine publish into exfiltration of an entire home
directory. The guard refuses by containment, not a blocklist:

- Refuses a **container directory** (`/`, `/Users`, `/Volumes`, `/home`, `/mnt`, `/media`, `/net`,
  `/System/Volumes`) or any **direct child** of one, and anything **at any depth** inside a system
  or credential tree (`/System`, `/Library`, `/usr`, `/private`, `~/Library`, `~/.ssh`, `~/.gnupg`,
  `~/.aws`, `~/.config`, `~/.local`).
- Paths are fully **resolved** (`~`, `..`, symlinks) *before* the check, and compared **casefolded
  per component** — macOS's default filesystem is case-insensitive and `realpath` does not
  normalise case, so a check against the raw path can pass while the operation walks a differently
  cased match.
- The guard runs over the **assembled** roots, not only the ones a config file names explicitly —
  a root derived from a camera profile (`volume = "EXT"`, `dcim = "."`) is checked too, because
  `import` deletes originals from wherever it points.
- **Refusal is fatal.** No partial application, no silent fallback to a default — a silent
  fallback means the operator believes an operation ran against the configured tree when it ran
  against another.

`library_root_refusal()` is a looser, second tier for `library.root` itself (minus the containment
clause), so a library deliberately sitting at the top of a dedicated disk (`/Volumes/Photos`) stays
expressible.

### 12. The RAW Hand-off Is Read-Only and Outside the Shared Allowlist (`raw_link.py`, v0.4.19)
`RAWS_PATH` is deliberately **not** in `CULL_ROOTS` / `_allowed_roots` — the allowlist every other
client-supplied path in the culling API goes through. Several of that allowlist's consumers are
destructive (rating and label write-back run `exiftool -overwrite_original`; trash moves the file),
so widening it to include the RAW archive would make an irreplaceable RAF newly writable and
trashable through endpoints that have no reason to ever address one. Instead:

- `photo_flow/raw_link.py` is the **one** module allowed to resolve a path into the RAW archive on
  behalf of an HTTP request, and it is read-only by construction — every function returns a `Path`
  or refuses to; none opens, moves, writes, or deletes one.
- It takes a JPG path **already validated** against the ordinary culling roots and derives the
  correlating RAF itself; it never accepts a RAW path from a client.
- Correlation uses `timestamp_renamer.correlation_base` (Photomator `_2`/`_3`-suffix tolerant),
  never `extract_original_base` — the latter's mismatch on a suffixed duplicate export is the
  exact bug class that has previously exposed irreplaceable RAWs to deletion elsewhere in this
  codebase (see the v0.4.1 changelog entry in CLAUDE.md).

**Not yet exercised against real hardware.** `/Volumes/EXT` is unmounted on the development
machine, so no real RAF has ever been handed to a real RAW developer through this path — only a
monkeypatched `open` call and a relocated root have been tested.

## Implementation Examples

### Safe File Copy (`file_manager.py`)
```python
def safe_copy(src, dst):
    # 1. Check if destination exists and is identical
    if dst.exists() and is_duplicate(src, dst):
        return True, ""

    # 2. Copy with metadata preservation
    shutil.copy2(src, dst)

    # 3. Verify copy integrity
    is_duplicate, error = is_duplicate(src, dst)
    if not is_duplicate:
        return False, "Verification failed"

    return True, ""
```

### Safe Image Compression (`image_processor.py`) — retained for reference, not called
`finalize` has done a verified full-quality copy since v0.3.4 (Photomator bakes edits and the
rating into the Staging JPG before it ever reaches this pipeline, so re-compressing at finalize
only added generation loss). No command in the current pipeline calls this module; the pattern
below is kept as a reference for the backup/restore idiom it demonstrates, not as a description of
what finalize does today.
```python
def compress_jpeg_safe(input_path):
    # 1. Create compressed version in temporary file
    with tempfile.NamedTemporaryFile() as tmp:
        # Process image...

        # 2. Copy ALL metadata using exiftool
        subprocess.run(['exiftool', '-TagsFromFile', input_path, tmp.name])

        # 3. Verify integrity
        Image.open(tmp.name).verify()

        # 4. Create backup of original
        backup_path = input_path.with_suffix('.backup')
        shutil.copy2(input_path, backup_path)

        # 5. Atomic replace (only after everything succeeded)
        tmp.replace(input_path)

        # 6. Remove backup
        backup_path.unlink()
```

### Safe Network Operations (`workflow.py`)
```python
def backup_final_to_homelab():
    # Connect via Tailscale (encrypted mesh network)
    try:
        subprocess.run(['rsync', '-e', ssh_cmd, src, remote], check=True)
        return success
    except Exception as e:
        return error
```

## Why This Level of Safety?

**Personal photos are irreplaceable.** Unlike code or documents, a corrupted or lost photo cannot be recreated. This justifies the comprehensive safety measures:

1. **No tolerance for data loss** - Photos contain memories that cannot be recovered
2. **Frequent interruptions** - Mobile workflows often face connectivity issues
3. **Metadata is crucial** - Ratings, keywords, and EXIF data represent significant time investment
4. **Traveling photographer needs** - IPv4/IPv6 connectivity varies by location

## Dependencies

- **exiftool**: Industry standard for metadata operations (`brew install exiftool`)
- **rsync**: For safe, resumable transfers (pre-installed on macOS)
- **Python PIL**: For image processing with integrity verification

## Testing Safety

Run operations with `--dry-run` first to preview changes:

```bash
photoflow import --dry-run      # Preview file operations
photoflow finalize --dry-run    # Preview the copy-then-delete moves
photoflow backup --dry-run      # Preview network operations
```

The dry-run mode exercises the same code paths without making changes, allowing verification of safety logic.