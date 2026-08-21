"""
Configuration settings for the Photo-Flow application.

Paths, camera profiles and the three organisation axes come from
:mod:`photo_flow.library_config`, which reads two optional TOML files. **With no file
present every value below is the literal this module used to hardcode**, so the default
install behaves exactly as it always did — that is what the test suite runs against.

Read `library_config`'s module docstring for which file holds what and why. The short
version: `~/.photoflow/config.toml` says WHERE things are on this machine (roots, camera
volumes), `<library root>/photoflow.toml` says HOW the library is arranged (stage, layout,
naming) and holds the saved collections.

A malformed install file raises :class:`~photo_flow.library_config.LibraryConfigError`
here, at import, and takes the process down with it. That is deliberate: the file names
directories, and an operation that moves photographs must never run against a tree that
was guessed at. `photo_flow.cli` catches it and prints one actionable line.
"""

from pathlib import Path

from photo_flow import library_config
from photo_flow.library_config import LibraryConfigError  # noqa: F401  (re-exported)

# The resolved install: raises on a malformed file rather than falling back (see above).
INSTALL = library_config.load_install()

# File paths — every one of them a root the install config named or defaulted.
CAMERA_PATH = INSTALL.roots["camera"]
STAGING_PATH = INSTALL.roots["staging"]
RAWS_PATH = INSTALL.roots["raws"]
FINAL_PATH = INSTALL.roots["final"]
SSD_PATH = INSTALL.roots["videos"]
GALLERY_PATH = INSTALL.roots["gallery"]

# Camera profiles — Known Limitation #1 ("hardcoded to the Fuji X-T4 volume name") is now
# a row in a config file. `CAMERA_PROFILE` is resolved at import for the module-level
# constant above; call `INSTALL.active_camera()` to re-resolve after a card is inserted.
CAMERA_PROFILES = INSTALL.cameras
CAMERA_PROFILE = INSTALL.active_camera()

# Gallery deployment (VPS) — static Astro site rsynced to a host directory
# served by nginx (vps repo: apps/photo-gallery/). Public at photos.jkrumm.com.
GALLERY_REMOTE_USER = "jkrumm"
GALLERY_REMOTE_HOST = "100.97.220.54"  # VPS Tailscale IP (matches ~/.ssh/config "vps")
GALLERY_REMOTE_PATH = Path("/home/jkrumm/photo-gallery-dist")

# Remote backup (homelab) settings
HOMELAB_USER = "jkrumm"
HOMELAB_HOST = "100.85.139.104"  # Tailscale IP
# SSD backup path (for Final JPEGs - fast access)
HOMELAB_SSD_FINAL_PATH = Path("/home/jkrumm/ssd/SSD/Bilder/Fuji")
# Optional safety mirror of Staging. Deliberately a SIBLING of the Fuji folder, not inside it:
# Immich mounts .../Bilder/Fuji read-only as an external library, and unfinalized, unrated
# photos have no business showing up there.
HOMELAB_SSD_STAGING_PATH = Path("/home/jkrumm/ssd/SSD/Bilder/Staging")
# HDD backup paths (for large files - RAWs and Videos)
HOMELAB_HDD_RAWS_PATH = Path("/mnt/hdd/fuji/RAWs")
HOMELAB_HDD_VIDEOS_PATH = Path("/mnt/hdd/fuji/Videos")
# Trash folders for deleted files (must be on the same filesystem as the destination)
HOMELAB_SSD_TRASH_PATH = Path("/home/jkrumm/ssd/SSD/Bilder/.trash")  # Same SSD as Final
HOMELAB_HDD_TRASH_PATH = Path("/mnt/hdd/fuji/.trash")                 # Same HDD as RAWs/Videos
# Legacy alias - kept for any direct references
HOMELAB_TRASH_PATH = HOMELAB_HDD_TRASH_PATH
# Legacy alias for backwards compatibility
HOMELAB_DEST_PATH = HOMELAB_SSD_FINAL_PATH
# Exclude system files from backup (macOS resource forks, Windows thumbnails, etc.)
# These files are not portable and not part of the actual photo data.
# NEVER add EDIT_SIDECAR_SUFFIX here — the .photo-edit sidecars are the only copy of
# Photomator's re-editable edit history and must ride along to the homelab.
RSYNC_EXCLUDE_PATTERNS = [
    ".DS_Store",      # macOS folder view settings
    "._*",            # macOS AppleDouble resource forks (extended attributes)
    "Thumbs.db",      # Windows thumbnail cache
    ".Spotlight-V100", # macOS Spotlight index
    ".Trashes",       # macOS trash folder
    ".fseventsd",     # macOS filesystem events
]
# rclone parallel transfer settings
# Connection via Tailscale (encrypted mesh network, no port exposure needed)
RCLONE_TRANSFERS = 8           # Parallel file transfers (optimal for 5–100MB files over LAN)
RCLONE_SSH_CIPHER = "aes128-gcm@openssh.com"
RCLONE_SFTP_CONCURRENCY = 64  # Concurrent SFTP requests per transfer (speeds up large files)
# SSH options list for direct SSH calls (e.g. remote file count check)
HOMELAB_SSH_OPTS = ["-T", "-c", "aes128-gcm@openssh.com", "-o", "Compression=no", "-o", "ConnectTimeout=5"]

# ---------------------------------------------------------------------------
# Culling view (control panel "Photos" screen)
# ---------------------------------------------------------------------------
# Roots the culling browser may read. Nothing outside this map is servable —
# routes_photos.py resolves every incoming path against it and 400s on a miss.
CULL_ROOTS = {
    "final": FINAL_PATH,
    "staging": STAGING_PATH,
}

# RAWS_PATH is deliberately NOT in CULL_ROOTS, and must not be added to it.
#
# `routes_photos._resolve_in_roots` is the allowlist for every client-supplied path, and
# its consumers include `POST /api/photos/rating` (exiftool `-overwrite_original` on the
# named file) and `POST /api/photos/trash` (a move). Widening the allowlist to the RAW
# archive would make an irreplaceable RAF writable and trashable through endpoints that
# have no reason to address one — and writing a RAF is barred by decision 0005 besides.
#
# The RAW hand-over (`POST /api/photos/open-in-editor` with `target: "raw"` in the body)
# therefore never accepts a RAW path from a client at all. It takes the JPG's own path,
# which is validated here as usual, and derives the RAF itself through
# `photo_flow.raw_link` — which owns the only read-only door onto this root.

# The library root — the directory the library's own configuration lives in.
#
# Was `FINAL_PATH.parent`, which was true only because Final happened to sit directly
# under it. It is now named in its own right, so pointing `roots.final` at another disk
# does not silently move the library file to that disk's parent directory.
LIBRARY_ROOT = INSTALL.library_root

# Durable, hand-editable library configuration, stored NEXT TO the library.
#
# Decision 0002 (`shutterflow/docs/decisions/0002-source-of-truth.md`) makes the SQLite
# index a disposable cache: nothing may exist only in it. A saved collection is unique
# state — a name plus a query nobody can reconstruct from the photographs — so it lives
# in a file that survives `rm ~/.photoflow/index.db`, travels when the library is copied
# to another machine, and can be read by a text editor in ten years. The three
# organisation axes below join it for the same reason: they describe the pictures.
#
# In the library, not ~/.photoflow: the file belongs to the LIBRARY, not to this install.
# Visible, not dotted: it is meant to be opened and edited by hand.
#
# F1 expected the roots and camera profiles to land here too. They could not: the roots
# table is what says where the library IS, so reading it from inside the library needs the
# answer first. They live in the install file instead — which is also where they belong on
# the portability test, since a mount point is wrong on any other machine.
LIBRARY_CONFIG_PATH = INSTALL.library_config_path

# The three organisation axes of decision 0003 — Stage, Layout and the filename template.
#
# CONFIGURATION, NOT BEHAVIOUR: no operation in photo-flow reads a non-default value.
# Performing a restage or a relayout means moving irreplaceable files and belongs to a
# later stage. `ORGANISATION.unimplemented` names every axis set away from its default so
# a setting that does nothing is at least visible; `photoflow config` prints it.
ORGANISATION = library_config.load_organisation(LIBRARY_CONFIG_PATH)

# The external applications the culling view can hand a file to.
#
# Was a single hardcoded name (`EXTERNAL_EDITOR_APP = "Shutterflow"`). It is now a row in
# the install config, for one reason that is worth stating rather than assuming: a JPEG
# master goes to a **pixel editor** and a RAF goes to a **RAW developer**, and on almost
# every machine those are different programs. "The editor" was never one setting; it was
# one setting because there was only ever one destination.
#
# Each profile is still a macOS application NAME resolved by Launch Services (`open -a`),
# never a path: the bundle moves between ~/Applications and /Applications depending on how
# it was installed and a hardcoded path would break on the other one.
#
# Nothing in the pipeline depends on any of this. If an app is absent the endpoint reports
# that and no photo is touched — the hand-over is a convenience on top of the library,
# never a step in it.
EXTERNAL_EDITORS = INSTALL.editors

# Derived thumbnail/preview cache. Disposable: safe to delete, regenerates on demand.
THUMB_CACHE_PATH = Path.home() / ".photoflow" / "thumbs"
# Long-edge pixels per tier. `grid` feeds the filmstrip, `view` the big viewer.
THUMB_SIZES = {"grid": 320, "view": 2048}
THUMB_QUALITY = {"grid": 78, "view": 86}

# Soft-delete staging area for culled photos. Deliberately under ~/Pictures so a move
# from Final or Staging is a same-filesystem rename (atomic, instant, no copy).
# Leading dot keeps it out of Photos/Photomator library scans.
TRASH_PATH = INSTALL.roots["trash"]
# Days a trashed file is retained before `photoflow trash purge` may delete it.
TRASH_RETENTION_DAYS = 30

# Image processing settings
CLARITY_ADJUSTMENT = -3

# File extensions to process — the union over every configured camera profile, so adding
# a second body with a different RAW extension is a config change rather than a code one.
EXTENSIONS = {ext for profile in CAMERA_PROFILES for ext in profile.extensions}

# Photomator's re-editable edit history, stored as <jpg-stem>.photo-edit next to the JPG.
# Irreplaceable (there is no second copy) and ~17 MB each, so it travels with its JPG
# through finalize and is included in the Final backup.
EDIT_SIDECAR_SUFFIX = '.photo-edit'
