"""
The library configuration model — where the photographs are, and how they are arranged.

Two files, and which one holds what is the whole design
------------------------------------------------------
::

    ~/.photoflow/config.toml        INSTALL   [library] · [roots] · [[cameras]] · [[editors]]
    <library root>/photoflow.toml   LIBRARY   [stage] · [layout] · [naming] · [[collections]]
    ~/.photoflow/index.db           CACHE     derived, disposable (decision 0002)
    ~/.photoflow/thumbs/            CACHE     derived, disposable

The split is not filing tidiness, it is a **bootstrap fact plus a portability rule**.

*Bootstrap.* F1 put the collection file next to the library and expected F4's roots to
join it there. They cannot: the roots table is what says where the library IS, so storing
it inside the library needs the answer before it can be read. Something has to be findable
at a fixed location, and that something is the install file.

*Portability.* Everything in the install file is a fact about **this machine** — an
absolute mount point, the volume name a camera shows up under. Copy the library to another
Mac and every one of those values is wrong. Everything in the library file is a fact about
**these photographs** — how their stages are represented, how they are laid out, how they
are named, and which saved queries someone wrote over them. All of that stays true on the
other machine, so it travels with the pictures and survives ``rm ~/.photoflow/index.db``.

``[[editors]]`` is the rule's newest application and a clean one: which applications are
installed is a fact about this Mac, not about these photographs, so the external-editor
table lives in the install file next to the camera volumes.

The rule, stated once so a later link can apply it to a new setting rather than guess:
**if the value would be wrong after copying the library to another computer, it belongs to
the install; otherwise it belongs to the library.**

Strictness follows danger
-------------------------
A path in a config file is one typo away from a destructive operation running against the
wrong tree, so the install file is validated hard and a bad one is **fatal**: absolute
paths only, no two roots naming the same place, no root nested inside another, and every
root put through the guard documented above :func:`root_refusal` — no container directory,
no whole account or whole mounted volume, nothing inside a system tree, nothing that exists
and is not a directory, all judged after symlinks and ``..`` have been resolved. That guard is the load-bearing one:
`sync_gallery` publishes everything rated 4+ under `roots.final` to a PUBLIC host and
`import` moves files off `roots.camera` and deletes the originals, so `final = "/Users"`
is an exfiltration and `camera = "/Volumes/EXT"` empties a disk.

There is no partial application — either the whole file is understood or none of it is
used — and there is no silent fallback. Refusing to start is the safe failure; quietly
running against the built-in defaults while the user's file says the library moved to an
external drive is the unsafe one, because the operation then reports success over a tree
nobody chose.

The library file is the opposite, and deliberately so. Nothing in it can point an
operation at a directory — the three axes below are recorded but not yet acted on — so a
syntax error there degrades to defaults and is reported, exactly the contract
:mod:`photo_flow.collections` already applies to the same file (tolerant read, refused
write). Two contradictory behaviours over one file would be worse than either.

**No config file at all is the normal state**, not an error: every default below
reproduces the hardcoded behaviour photo-flow shipped with.

The three axes (decision 0003) are CONFIGURATION, not behaviour
---------------------------------------------------------------
0003 makes library organisation two orthogonal axes plus a smaller third, so that
migration is per-axis rather than an N x N matrix of model converters:

* ``[stage]``  — separate folders, or in place with an embedded stage tag.
* ``[layout]`` — ``flat`` / ``year-month`` / ``album`` / ``as-is``.
* ``[naming]`` — the filename template.

**Nothing in photo-flow reads a non-default value of any of them.** They are serialised
here so the model exists and can be argued about; performing a restage or a relayout means
moving irreplaceable files and is explicitly Stage H's problem. Because accepting a setting
that does nothing is itself a quiet lie, :attr:`LibraryOrganisation.unimplemented` names
every axis set away from its default, and every surface that shows the config shows that
list.

TOML, and why no new dependency
-------------------------------
``tomllib`` is stdlib from 3.11; this venv runs **Python 3.9.6**, so reading goes through
``tomli`` — the same code by the same author, already an install requirement for
:mod:`photo_flow.collections`, already in the venv. No dependency is added by this module.
Writing the install file is 30 lines of hand-rolled emitter reusing the same reasoning as
``collections._dump_scalar``: the value set is strings and lists of strings, and a TOML
writer package would be supply-chain surface for that.
"""
from __future__ import annotations

import logging
import os
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple

try:  # Python 3.11+
    import tomllib as _toml
except ModuleNotFoundError:  # Python 3.9/3.10 — tomli IS tomllib, same author, same API
    import tomli as _toml  # type: ignore[no-redef]

logger = logging.getLogger(__name__)


class LibraryConfigError(RuntimeError):
    """
    The configuration could not be understood, so photo-flow refuses to act on it.

    Carries the file and the offending key in its message: the reader is a person with a
    text editor open, and "invalid config" without a line to go to is not loud, it is
    merely annoying.
    """


# ---------------------------------------------------------------------------
# Defaults — the hardcoded behaviour photo-flow shipped with, stated once.
#
# Every one of these reproduces the literal that used to live in config.py. With no
# config file present the two are byte-identical, which is what the test suite proves.
# ---------------------------------------------------------------------------

#: Where the install file lives, unless PHOTOFLOW_CONFIG says otherwise.
DEFAULT_INSTALL_PATH = Path.home() / ".photoflow" / "config.toml"
#: Environment override. Exists for tests and for running a second library side by side;
#: it is read at load time, never cached at import.
INSTALL_PATH_ENV = "PHOTOFLOW_CONFIG"
#: The library file's name inside the library root. Not configurable: it is how the
#: library is recognised, and a configurable name would need a config file to find it.
LIBRARY_CONFIG_NAME = "photoflow.toml"

DEFAULT_LIBRARY_ROOT = Path.home() / "Pictures"
#: Roots that are NOT under the library root, and have no business being derived from it:
#: two external volumes and a build directory inside this source tree.
DEFAULT_RAWS_PATH = Path("/Volumes/EXT/Bilder/RAWs")
DEFAULT_VIDEOS_PATH = Path("/Volumes/EXT/Videos/Videos")
DEFAULT_GALLERY_PATH = Path("/Users/johannes.krumm/SourceRoot/photo-flow/photo_gallery/src")

#: The camera the tool was written against — Known Limitation #1, now a row rather than a
#: hardcoded volume name. `volume` is the name the card shows up under in /Volumes.
DEFAULT_CAMERA = {
    "id": "fuji-xt4",
    "name": "Fujifilm X-T4",
    "volume": "Fuji X-T4",
    "dcim": "DCIM",
    "photos": [".JPG"],
    "raws": [".RAF"],
    "videos": [".MOV"],
}

#: The two kinds of file an external application can be handed, and the reason
#: "the editor" is not one setting. A JPEG master goes to a **pixel editor** — it crops,
#: retouches and re-writes an image that already exists. A RAF goes to a **developer** —
#: it demosaics a sensor dump and produces an image that does not exist yet. Those are
#: different programs doing different jobs, so an editor entry declares which it does and
#: the hand-over names the KIND it wants, never an application.
EDITOR_KINDS: Tuple[str, ...] = ("jpeg", "raw")

#: Where `open -a NAME` finds a bundle on macOS. Used only to answer "is this one
#: actually installed?" for display — never to refuse a launch, because Launch Services
#: also resolves bundles this list does not know about.
APPLICATION_DIRS: Tuple[Path, ...] = (Path("/Applications"), Path.home() / "Applications")

#: The editors an install has with no config file: exactly the one v0.4.13 hardcoded, so
#: the default install hands a JPEG to the same application it always did.
#:
#: There is deliberately **no default RAW developer**. Guessing one would either name an
#: application that is not installed (a feature that fails on first use) or pick a
#: favourite out of the four or five plausible ones on a photographer's Mac — a taste call
#: presented as a default. A RAW developer is one `[[editors]]` block, and until there is
#: one the hand-over says so and names the block to paste.
DEFAULT_EDITORS: Tuple[Dict[str, Any], ...] = (
    {"id": "shutterflow", "name": "Shutterflow", "app": "Shutterflow", "handles": ["jpeg"]},
)

#: Roots the install file may name. Order is the order `config check` prints them in.
ROOT_KEYS: Tuple[str, ...] = ("camera", "staging", "final", "raws", "videos", "gallery", "trash")
#: Roots that hold the library's own photographs, and must therefore never overlap each
#: other or the trash.
LIBRARY_ROOT_KEYS: Tuple[str, ...] = ("staging", "final")

STAGE_MODES: Tuple[str, ...] = ("folders", "in-place")
#: 0003 lists three Layout options and asks whether "no opinion" is a fourth. It is
#: modelled as `as-is` — the lowest-friction on-ramp for a library that already exists and
#: whose owner does not want a tool rearranging it.
LAYOUT_MODES: Tuple[str, ...] = ("flat", "year-month", "album", "as-is")
NAMING_MODES: Tuple[str, ...] = ("import", "never")

DEFAULT_STAGE_MODE = "folders"
#: Only meaningful for `stage.mode = "in-place"`. Namespaced per decision 0004, which
#: requires invented metadata to be namespaced and recorded; 0004 lists the stage tag as
#: still open, so this is the placeholder the question will be answered against.
DEFAULT_STAGE_TAG = "photoflow:stage"
DEFAULT_LAYOUT_MODE = "flat"
#: `%`-codes are strftime against DateTimeOriginal; `{n}` is the collision counter
#: (empty for the first file, then -2, -3, …); `{base}` is the camera's own stem.
#: This exact string reproduces `timestamp_renamer.generate_timestamped_filename`.
DEFAULT_NAMING_TEMPLATE = "%Y-%m-%d_%H-%M-%S{n}_{base}"
DEFAULT_NAMING_APPLY = "import"

#: Placeholders the template may contain. Anything else in braces is a typo, and a typo in
#: a filename template renames files wrongly — so it is refused rather than passed through.
NAMING_PLACEHOLDERS: Tuple[str, ...] = ("{n}", "{base}")


# ---------------------------------------------------------------------------
# Model
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class CameraProfile:
    """
    One camera, as a mount point and the extensions it writes.

    Attributes:
        id: Stable identifier, used to name the profile in reports.
        name: Display name.
        volume: The volume name the card appears under, i.e. ``/Volumes/<volume>``.
        dcim: The image directory inside that volume.
        photos: Extensions imported to the staging root.
        raws: Extensions imported to the RAW root.
        videos: Extensions imported to the video root.
    """

    id: str
    name: str
    volume: str
    dcim: str = "DCIM"
    photos: Tuple[str, ...] = (".JPG",)
    raws: Tuple[str, ...] = (".RAF",)
    videos: Tuple[str, ...] = (".MOV",)

    @property
    def mount_path(self) -> Path:
        """The volume's mount point, whether or not it is currently mounted."""
        return Path("/Volumes") / self.volume

    @property
    def camera_path(self) -> Path:
        """The directory `scan_camera_files` walks — ``/Volumes/<volume>/<dcim>``."""
        return self.mount_path / self.dcim

    @property
    def extensions(self) -> Tuple[str, ...]:
        """Every extension this camera contributes, upper-cased, in photo/raw/video order."""
        return tuple(self.photos) + tuple(self.raws) + tuple(self.videos)

    def is_connected(self) -> bool:
        """True when the card is mounted and its image directory exists."""
        return self.camera_path.exists()

    def to_dict(self) -> Dict[str, Any]:
        """Serialise for the API and for `config show`."""
        return {
            "id": self.id,
            "name": self.name,
            "volume": self.volume,
            "dcim": self.dcim,
            "photos": list(self.photos),
            "raws": list(self.raws),
            "videos": list(self.videos),
            "camera_path": str(self.camera_path),
            "connected": self.is_connected(),
        }


@dataclass(frozen=True)
class EditorProfile:
    """
    One external application the culling view can hand a file to, and what it is FOR.

    A profile is a *role plus an application name*, never a path: ``open -a NAME`` goes
    through Launch Services, so the bundle can live in ``/Applications`` or
    ``~/Applications`` and this does not have to care. Refusing a separator in ``app``
    (see :func:`_read_editors`) keeps it that way — the launch argument is an application
    name, not an arbitrary executable somebody wrote into a config file.

    Attributes:
        id: Stable identifier. What the API asks for and what the UI round-trips.
        name: Display name, shown on the button.
        app: The macOS application name passed to ``open -a``.
        handles: Which of :data:`EDITOR_KINDS` this application is for. An application
            that genuinely does both (Affinity Photo, Photomator) names both; the point
            of the field is that most do not.
    """

    id: str
    name: str
    app: str
    handles: Tuple[str, ...] = ("jpeg",)

    def handles_kind(self, kind: str) -> bool:
        """True when this application is configured for that kind of file."""
        return kind in self.handles

    def is_installed(self) -> bool:
        """
        Whether a bundle by this name sits in one of the standard application folders.

        **Advisory only.** Launch Services resolves names this cannot see (a bundle in a
        subfolder, one registered from a build directory), so a False here annotates the
        UI and never blocks a launch — the launch itself is the authoritative answer, and
        it already reports its own failure without raising.
        """
        bundle = self.app if self.app.endswith(".app") else f"{self.app}.app"
        return any((directory / bundle).exists() for directory in APPLICATION_DIRS)

    def to_dict(self) -> Dict[str, Any]:
        """Serialise for the API and for `config show`."""
        return {
            "id": self.id,
            "name": self.name,
            "app": self.app,
            "handles": list(self.handles),
            "installed": self.is_installed(),
        }


@dataclass(frozen=True)
class InstallConfig:
    """
    Where everything is on THIS machine.

    Attributes:
        path: The file this came from, whether or not it exists.
        present: False when there is no file and every value below is a default.
        library_root: The directory the library file lives in.
        roots: Absolute paths, keyed by :data:`ROOT_KEYS`.
        cameras: Camera profiles, in file order. Never empty — a file with no
            ``[[cameras]]`` entry gets the default profile, because a tool that cannot
            name a camera cannot import.
        editors: External applications the culling view may hand a file to, in file
            order. **May be empty**: an install with `editors = []` has deliberately
            turned the hand-over off, and a tool that cannot open an editor still culls.
    """

    path: Path
    present: bool
    library_root: Path
    roots: Dict[str, Path]
    cameras: Tuple[CameraProfile, ...]
    editors: Tuple[EditorProfile, ...] = ()

    @property
    def library_config_path(self) -> Path:
        """The library file — the second half of the story, found via `library_root`."""
        return self.library_root / LIBRARY_CONFIG_NAME

    def active_camera(self) -> CameraProfile:
        """
        The profile an import would use: the first one whose card is mounted, else the first.

        With a single profile — the default, and today's behaviour — this is a constant.
        With several it is the reason multi-camera support is a config change rather than a
        code change, and it is resolved on call rather than cached, because a card can be
        inserted after the daemon started.
        """
        for camera in self.cameras:
            if camera.is_connected():
                return camera
        return self.cameras[0]

    def editors_for(self, kind: str) -> Tuple[EditorProfile, ...]:
        """
        Every configured application for one kind of file, in file order.

        Args:
            kind: One of :data:`EDITOR_KINDS`.

        Returns:
            The matching profiles. Empty when nothing is configured for that kind —
            the normal state for ``raw`` on a default install.
        """
        return tuple(editor for editor in self.editors if editor.handles_kind(kind))

    def default_editor(self, kind: str) -> Optional[EditorProfile]:
        """
        The application a hand-over uses when the caller names no editor.

        File order is the preference order, deliberately: a list in a text file already
        expresses ranking, and a separate `default = true` key is a second way to say the
        same thing that can disagree with the first.
        """
        matches = self.editors_for(kind)
        return matches[0] if matches else None

    def find_editor(self, identifier: str) -> Optional[EditorProfile]:
        """The profile with this id, or None. Ids are unique — `_read_editors` enforces it."""
        for editor in self.editors:
            if editor.id == identifier:
                return editor
        return None

    def to_dict(self) -> Dict[str, Any]:
        """Serialise for the API and for `config show`."""
        return {
            "path": str(self.path),
            "present": self.present,
            "library_root": str(self.library_root),
            "roots": {key: str(value) for key, value in self.roots.items()},
            "cameras": [camera.to_dict() for camera in self.cameras],
            "active_camera": self.active_camera().id,
            "editors": [editor.to_dict() for editor in self.editors],
        }


@dataclass(frozen=True)
class StageConfig:
    """0003's Stage axis. `tag` is only meaningful for ``in-place``."""

    mode: str = DEFAULT_STAGE_MODE
    tag: str = DEFAULT_STAGE_TAG


@dataclass(frozen=True)
class LayoutConfig:
    """0003's Layout axis, including the ``as-is`` option 0003 left open."""

    mode: str = DEFAULT_LAYOUT_MODE


@dataclass(frozen=True)
class NamingConfig:
    """0003's third, smaller axis: what an imported file is called."""

    template: str = DEFAULT_NAMING_TEMPLATE
    apply: str = DEFAULT_NAMING_APPLY


@dataclass(frozen=True)
class LibraryOrganisation:
    """
    How THESE photographs are arranged — the part of the config that travels with them.

    Attributes:
        path: The library file this came from.
        present: False when the file has none of these tables and every value is a default.
        readable: False when the file exists but could not be parsed. The values are then
            defaults and :attr:`errors` says why; unlike the install file this is not
            fatal, because nothing here can point an operation at a directory.
        stage / layout / naming: The three axes.
        errors: Human-readable problems found while reading. Empty on a clean read.
    """

    path: Path
    present: bool
    readable: bool
    stage: StageConfig = field(default_factory=StageConfig)
    layout: LayoutConfig = field(default_factory=LayoutConfig)
    naming: NamingConfig = field(default_factory=NamingConfig)
    errors: Tuple[str, ...] = ()

    @property
    def unimplemented(self) -> Tuple[str, ...]:
        """
        The axes set away from their default, and therefore recorded but not acted on.

        Every surface that displays the config displays this. A setting that silently does
        nothing is the same class of failure as a path that silently points elsewhere —
        the user believes something is true about their library that is not.
        """
        pending: List[str] = []
        if self.stage.mode != DEFAULT_STAGE_MODE:
            pending.append(f"stage.mode = {self.stage.mode!r}")
        if self.layout.mode != DEFAULT_LAYOUT_MODE:
            pending.append(f"layout.mode = {self.layout.mode!r}")
        if self.naming.template != DEFAULT_NAMING_TEMPLATE:
            pending.append(f"naming.template = {self.naming.template!r}")
        if self.naming.apply != DEFAULT_NAMING_APPLY:
            pending.append(f"naming.apply = {self.naming.apply!r}")
        return tuple(pending)

    def to_dict(self) -> Dict[str, Any]:
        """Serialise for the API and for `config show`."""
        return {
            "path": str(self.path),
            "present": self.present,
            "readable": self.readable,
            "stage": {"mode": self.stage.mode, "tag": self.stage.tag},
            "layout": {"mode": self.layout.mode},
            "naming": {"template": self.naming.template, "apply": self.naming.apply},
            "unimplemented": list(self.unimplemented),
            "errors": list(self.errors),
        }


# ---------------------------------------------------------------------------
# Validation helpers
#
# Every one of these raises rather than returning a default. That is the point: a value
# that is present but wrong must never be quietly replaced by a value that is right, or
# the file stops describing what the tool actually does.
# ---------------------------------------------------------------------------


def _fail(path: Path, message: str) -> "LibraryConfigError":
    """Build the error, prefixed with the file so the reader knows what to open."""
    return LibraryConfigError(f"{path}: {message}")


def _table(document: Mapping[str, Any], key: str, path: Path) -> Dict[str, Any]:
    """
    Fetch one table, defaulting to empty.

    Raises:
        LibraryConfigError: The key exists but is not a table.
    """
    value = document.get(key, {})
    if not isinstance(value, dict):
        raise _fail(path, f"[{key}] must be a table, not {type(value).__name__}")
    return value


def _reject_unknown(table: Mapping[str, Any], allowed: Sequence[str], where: str, path: Path) -> None:
    """
    Refuse a key this module does not understand.

    An unknown key is nearly always a misspelling of a known one, and a misspelled setting
    reads as "I configured that" while behaving as "I did not". Naming the allowed set in
    the message turns the error into the documentation.

    Raises:
        LibraryConfigError: A key is not in `allowed`.
    """
    for key in table:
        if key not in allowed:
            raise _fail(path, f"unknown key {key!r} in {where} (allowed: {', '.join(allowed)})")


def _string(table: Mapping[str, Any], key: str, default: str, where: str, path: Path) -> str:
    """
    Read a non-blank string.

    Raises:
        LibraryConfigError: The value is not a string, or is blank.
    """
    if key not in table:
        return default
    value = table[key]
    if not isinstance(value, str) or not value.strip():
        raise _fail(path, f"{where}.{key} must be a non-empty string")
    return value.strip()


def _choice(table: Mapping[str, Any], key: str, allowed: Sequence[str], default: str,
            where: str, path: Path) -> str:
    """
    Read a value from a closed set.

    Raises:
        LibraryConfigError: The value is not one of `allowed`.
    """
    value = _string(table, key, default, where, path)
    if value not in allowed:
        raise _fail(path, f"{where}.{key} must be one of {', '.join(allowed)} (got {value!r})")
    return value


def _extensions(table: Mapping[str, Any], key: str, default: Sequence[str],
                where: str, path: Path) -> Tuple[str, ...]:
    """
    Read a list of file extensions, normalised to upper case with a leading dot.

    Raises:
        LibraryConfigError: Not a list of strings, or an entry has no leading dot.
    """
    if key not in table:
        return tuple(default)
    value = table[key]
    if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
        raise _fail(path, f"{where}.{key} must be a list of strings like ['.JPG']")
    out: List[str] = []
    for item in value:
        cleaned = item.strip()
        if not cleaned.startswith(".") or len(cleaned) < 2 or "/" in cleaned:
            raise _fail(path, f"{where}.{key}: {item!r} is not a file extension like '.JPG'")
        out.append(cleaned.upper())
    return tuple(out)


# ---------------------------------------------------------------------------
# The root guard — which directories may be named as a library root
#
# THREAT MODEL, stated so the rule can be argued with rather than guessed at: this defends
# against a TYPO and a careless hand-edit. It is not a sandbox against a hostile local
# user, who can edit this same file and so cannot be stopped by anything written in it.
#
# It matters because the roots are outward-facing and destructive. `sync_gallery` copies
# every rating>=4 JPG under `roots.final` into the gallery and rsyncs it to a PUBLIC host;
# `backup` rsyncs the same tree to the homelab; `import` MOVES files off `roots.camera`
# and deletes the originals; `finalize` and `cleanup` delete RAWs that no JPG under the
# culling roots claims. `final = "/Users"` therefore turns a routine publish into an
# exfiltration, and `camera = "/Volumes/EXT"` empties an external disk. One mistyped line
# is the whole distance between those outcomes, which is why validation here is a safety
# feature and not an ergonomic one.
#
# THE RULE, in four parts, every one of them applied to the FULLY RESOLVED path:
#
# 1. **Containers.** A directory whose children are entire trees — `/` (the system's top
#    level), `/Users` and `/home` (whole accounts), `/Volumes`, `/mnt`, `/media`, `/net`
#    (whole mounted disks and shares) — may be named neither itself nor by one of its
#    direct children. This is the depth floor, expressed against the actual mount and home
#    containers instead of as a raw component count: `/Volumes/EXT` is somebody's entire
#    external disk and is refused, `/Volumes/EXT/Bilder` is a folder on it and is fine.
#    It subsumes the old "not the filesystem root, not bare $HOME" pair and extends it to
#    every other account and every mounted volume, which is where the photographs of other
#    people and the contents of a Time Machine disk live.
# 2. **Protected trees.** Some directories are deep enough to clear the floor and must
#    still never hold a photo root, because they hold the operating system, its caches, or
#    credentials and mail: `/System`, `/usr`, `/private`, `~/Library`, `~/.ssh` and the
#    rest of :data:`PROTECTED_TREES`. A root at ANY depth inside one is refused. The one
#    exemption is the system scratch directory (`tempfile.gettempdir()`, which on macOS
#    lives under `/private/var/folders`): it is the standard home of a throwaway library
#    and it is what the test suite runs the guard against.
# 3. **Resolution first.** `~` is expanded, `..` collapsed, a trailing slash dropped and
#    every symlink followed BEFORE any comparison — otherwise `~/Pictures/../..` or a
#    symlink named `Final` walks straight through the check. The resolved path is also
#    what gets STORED and acted on, so the path that was checked is the path that is used.
#    This is a deliberate reversal of F4's original "no symlink resolution, do not rewrite
#    the user's own words": a guard that checks one path while the operation walks another
#    is not a guard. A path that does not exist yet resolves fine (its existing prefix is
#    resolved, the rest is kept), because a root legitimately does not exist until the
#    first import creates it.
# 4. **Case.** macOS's default filesystem is case-insensitive, so `/users/x` and `/Users/x`
#    are one directory and `resolve()` does not normalise the difference. Every comparison
#    below is therefore casefolded per path component. On a case-sensitive filesystem that
#    can only refuse a path differing from a system directory by case alone, which is the
#    safe direction to be wrong in.
# 5. **Not a file.** A root that does not exist is fine — import creates its destinations —
#    but a root that exists and is not a directory is a typo that happened to hit something.
#
# The rule is completed by :func:`_check_root_overlaps`, which is about the roots as a SET
# rather than one at a time: no two may name the same directory, and none may sit inside
# another. `roots.camera` inside `roots.final` passes every clause above and still makes
# `import` empty Final, because an import moves files off the camera root and deletes the
# originals.
#
# One clause deliberately does NOT trust its input: the scratch-directory exemption reads
# `TMPDIR`, so :func:`_scratch_root` refuses to honour a scratch directory that is the home
# directory or a container. Otherwise `TMPDIR=$HOME` switches the protected-tree clause off
# for the entire account, which is exactly what it did before that qualification existed.
#
# Two candidates weighed and rejected. An **allowed-prefix whitelist** cannot be written
# here: the RAW and video roots live on an external volume and the gallery root lives
# inside this source tree, so the whitelist degenerates to "$HOME, /Volumes and this repo",
# which is not a rule — it is the same refusal set inverted, with worse failure messages.
# A **marker file in every non-default root** fails on the same fact that makes resolution
# lenient: a root need not exist yet, so the marker would have to be optional, and an
# optional marker guards nothing.
# ---------------------------------------------------------------------------

#: Directories whose children are entire trees. Neither a container nor a direct child of
#: one may be a root. Each row is (container, what ONE child is, what they all are) — both
#: phrasings exist so the refusal reads as a sentence in either direction.
ROOT_CONTAINERS: Tuple[Tuple[str, str, str], ...] = (
    ("/", "a top-level system directory", "the system's own top-level directories"),
    ("/Users", "an entire user account", "all user accounts"),
    ("/home", "an entire user account", "all user accounts"),
    ("/Volumes", "an entire mounted volume", "all mounted volumes"),
    ("/mnt", "an entire mounted volume", "all mounted volumes"),
    ("/media", "an entire mounted volume", "all mounted volumes"),
    ("/net", "an entire mounted network share", "all mounted network shares"),
    ("/System/Volumes", "an entire mounted volume", "all mounted volumes"),
)

#: Trees that may not contain a root at any depth: the OS, its caches, credentials, mail.
#: `/private` covers macOS's real `/etc`, `/tmp` and `/var`, which is why the scratch
#: directory needs the explicit exemption in :func:`root_refusal`.
SYSTEM_TREES: Tuple[str, ...] = (
    "/System", "/Library", "/Applications", "/Network", "/bin", "/sbin", "/usr", "/opt",
    "/cores", "/dev", "/private", "/etc", "/var", "/tmp", "/proc", "/sys", "/boot", "/root",
)

#: The same, relative to the home directory — resolved against `Path.home()` at call time
#: so a test or a second account is not compared against the wrong one.
HOME_TREES: Tuple[str, ...] = (
    "Library",      # Mail, Messages, keychains, every app's Application Support
    ".ssh",
    ".gnupg",
    ".aws",
    ".config",
    ".local",
)


def _parts_key(path: Path) -> Tuple[str, ...]:
    """The path's components, casefolded — the unit every comparison here is made in."""
    return tuple(part.casefold() for part in path.parts)


def _same_dir(left: Path, right: Path) -> bool:
    """True when two absolute paths name the same directory, ignoring case."""
    return _parts_key(left) == _parts_key(right)


def _inside(inner: Path, outer: Path) -> bool:
    """
    True when `inner` IS `outer` or sits underneath it, ignoring case.

    Compares components rather than strings, so `/Users/x/Photos2` is not "inside"
    `/Users/x/Photos`.
    """
    inner_parts, outer_parts = _parts_key(inner), _parts_key(outer)
    return inner_parts[: len(outer_parts)] == outer_parts


def resolve_root(path: Path) -> Path:
    """
    Follow every symlink, collapse ``..`` and drop a trailing slash.

    Args:
        path: An absolute path, existing or not.

    Returns:
        The real path. Non-existent components are kept verbatim after the existing
        prefix has been resolved, which is what makes a not-yet-created root usable.

    Raises:
        OSError: The path could not be resolved (a symlink loop, a stalled mount).
        ValueError: The path is not one the OS can even ask about — a NUL byte, which a
            TOML file can write as ``\u0000``. Every caller here turns both into a
            refusal; letting a ValueError out would bypass the one-actionable-line
            failure path, which only catches :class:`LibraryConfigError`.
    """
    return Path(path).resolve()


def _best_effort_resolve(path: Path) -> Path:
    """
    Resolve a derived path, handing back the input unchanged when the OS refuses.

    Used for roots this module computes rather than reads. A failure here is not swallowed
    — :func:`root_refusal` runs the same resolution and reports it as a refusal — this
    only decides which value gets carried into that check.
    """
    try:
        return resolve_root(path)
    except (OSError, ValueError):  # pragma: no cover - needs a symlink loop or a NUL byte
        return path


def _scratch_root() -> Optional[Path]:
    """
    The system scratch directory, resolved — or None when it must not be trusted.

    The exemption this feeds is the one place the guard says *yes* on the strength of an
    ENVIRONMENT VARIABLE, because :func:`tempfile.gettempdir` honours ``TMPDIR``. Left
    unqualified that is a hole, and a measured one: with ``TMPDIR=$HOME`` the exemption
    fires before the protected-tree clause and every path below the home directory
    inherits it, so ``~/Library/Mail`` and ``~/.ssh`` both became acceptable roots.

    So a scratch directory is honoured only when it is plausibly one: not the filesystem
    root, not the home directory or anything inside it, and not one of
    :data:`ROOT_CONTAINERS`. Every real value clears that — ``/var/folders/…/T`` on macOS,
    ``/private/tmp``, ``/tmp`` — and the pathological ones that would switch the guard off
    do not. Dropping the exemption is never itself dangerous: it only ever lifts the
    protected-tree clause, and a directory outside those trees passes on its own merits.

    Returns:
        The resolved scratch directory, or None when there is none worth honouring.
    """
    try:
        scratch = resolve_root(Path(tempfile.gettempdir()))
    except (OSError, RuntimeError, ValueError):  # pragma: no cover - needs a broken TMPDIR
        return None
    if _same_dir(scratch, Path(scratch.anchor)):
        return None
    home = _home()
    if home is not None and _inside(scratch, home):
        return None
    for container, _child, _contents in ROOT_CONTAINERS:
        if _same_dir(scratch, Path(container)):
            return None
    return scratch


def _home() -> Optional[Path]:
    """The home directory, resolved, or None when the environment has none."""
    try:
        return resolve_root(Path.home())
    except (OSError, RuntimeError, ValueError):  # pragma: no cover - HOME unset, no passwd row
        return None


def root_refusal(path: Path) -> Optional[str]:
    """
    Say why `path` may not be one of the roots in :data:`ROOT_KEYS`, or None when it may.

    Public and free of side effects — it reads the filesystem (resolution, and one stat to
    tell a directory from a file) and changes nothing — so any surface that accepts a
    directory, a future settings screen or another `photoflow config` subcommand, can ask
    the same question the loader asks instead of inventing a second, weaker rule. The
    argument is resolved here, so passing a raw or an already-resolved path gives the same
    answer.

    Args:
        path: The candidate directory. It need not exist.

    Returns:
        A sentence naming the problem — no key and no filename, because the caller owns
        those — or None when the path is an acceptable root.
    """
    return _refusal(path, containers=True)


def library_root_refusal(path: Path) -> Optional[str]:
    """
    Say why `path` may not be the LIBRARY root, or None when it may.

    Weaker than :func:`root_refusal` by exactly one clause, and deliberately: nothing
    scans, publishes or deletes under `library.root` itself. It is a place to derive
    Staging, Final and the trash from, and a place to keep ``photoflow.toml``. So a
    library sitting at the top of a dedicated external disk — ``library.root =
    "/Volumes/Photos"`` — is a normal setup and must stay expressible, even though that
    same path is refused as a *root*, where it would mean "scan the whole disk".

    The dangerous library roots are still refused, one step later and by the clause that
    actually applies: ``library.root = "/"`` derives ``roots.final = "/Final"``, whose
    parent is a container, so :func:`_check_roots_safe` refuses it and names the root it
    would have scanned.

    Args:
        path: The candidate directory. It need not exist.

    Returns:
        A sentence naming the problem, or None.
    """
    return _refusal(path, containers=False)


def _refusal(path: Path, *, containers: bool) -> Optional[str]:
    """
    The guard itself.

    Args:
        path: The candidate directory, raw or resolved.
        containers: Apply the container clause — refuse a container directory and any
            direct child of one. False for the library root (see
            :func:`library_root_refusal`).

    Returns:
        A sentence naming the problem, or None.
    """
    if not path.is_absolute():
        return "must be an absolute path"
    try:
        resolved = resolve_root(path)
    except (OSError, ValueError) as exc:
        return f"could not be resolved: {exc}"

    if _same_dir(resolved, Path(resolved.anchor)):
        return ("is the filesystem root; every scan in this tool is recursive, so a root "
                "here reaches the whole disk")

    home = _home()
    if home is not None and _same_dir(resolved, home):
        return ("is your home directory itself; a backup or a gallery publish rooted here "
                "would carry your whole account with it")

    if containers:
        # Exact matches first: `/Users` is both a container and a child of `/`, and naming
        # it deserves the message about what it holds rather than the one about depth.
        for container, _child, contents in ROOT_CONTAINERS:
            if _same_dir(resolved, Path(container)):
                return (f"is {container}, where {contents} live — name a directory inside "
                        f"a library, not the container that holds them all")
        for container, child, _contents in ROOT_CONTAINERS:
            if _same_dir(resolved.parent, Path(container)):
                return (f"is {child}; a root must be at least one level inside "
                        f"{container}, or a publish and a delete reach the entire tree")

    # The scratch exemption lifts exactly ONE clause — the protected trees, because the
    # system scratch directory lives inside one (`/private/var/folders` on macOS) and is
    # the standard home of a throwaway library and of this suite's fixtures. It is not a
    # blanket "anything under TMPDIR is fine": the clauses either side of it still apply.
    scratch = _scratch_root()
    exempt = scratch is not None and _inside(resolved, scratch)

    trees = [Path(tree) for tree in SYSTEM_TREES]
    if home is not None:
        trees += [home / name for name in HOME_TREES]
    for tree in trees:
        if not exempt and _inside(resolved, tree):
            return (f"is inside {tree}, which holds the system, its caches or your "
                    f"credentials — not photographs")

    # Last, because a file inside a system tree deserves the more alarming diagnosis. A
    # root that does not exist yet is fine (import creates its destinations); a root that
    # exists and is NOT a directory is a mistyped path that happens to hit something, and
    # every operation would then either scan nothing or try to write inside a file.
    if resolved.exists() and not resolved.is_dir():
        return "exists but is not a directory; a root is the folder the photographs live in"
    return None


def _resolve_path(raw: str, where: str, path: Path, *,
                  containers: bool = True) -> Path:
    """
    Turn a configured string into a safe absolute directory path, or refuse it.

    ``~`` is expanded — a config file that cannot say "my home" would be unusable on a
    second machine — the result must be absolute, and it must then survive
    :func:`root_refusal`.

    Args:
        raw: The configured value.
        where: Dotted key, for the error message.
        path: The file being read, for the error message.
        containers: False for `library.root`, which is not itself scanned — see
            :func:`library_root_refusal`.

    Returns:
        The fully resolved path: symlinks followed, ``..`` collapsed. This is the value
        that gets stored and acted on, so the path that was checked is the path that is
        used.

    Raises:
        LibraryConfigError: The value is relative, cannot be resolved, or names a
            directory the guard refuses. The message carries the key AND the value,
            because the reader is a person with the file open.
    """
    expanded = Path(os.path.expanduser(raw.strip()))
    if not expanded.is_absolute():
        raise _fail(path, f"{where} must be an absolute path (got {raw!r})")
    try:
        resolved = resolve_root(expanded)
    except (OSError, ValueError) as exc:
        raise _fail(path, f"{where} = {raw!r}: could not be resolved: {exc}") from exc
    reason = _refusal(resolved, containers=containers)
    if reason is not None:
        raise _fail(path, f"{where} = {_shown(raw, resolved)}: {reason}")
    return resolved


def _shown(raw: str, resolved: Path) -> str:
    """Render a refused value, naming both what was written and what it resolved to."""
    if raw.strip() == str(resolved):
        return repr(raw.strip())
    return f"{raw.strip()!r} (which is {resolved})"


#: Where a root that the `[roots]` table did not name came from. Named in the refusal, so
#: "roots.staging is an entire user account" does not read as being about a line that is
#: not in the file — the line to go and fix is `library.root` or a camera profile.
_ROOT_ORIGIN: Dict[str, str] = {
    "camera": "derived from [[cameras]]",
    "staging": "derived from library.root",
    "final": "derived from library.root",
    "trash": "derived from library.root",
    "raws": "built-in default",
    "videos": "built-in default",
    "gallery": "built-in default",
}


def _check_roots_safe(roots: Mapping[str, Path], path: Path,
                      configured: Sequence[str] = ()) -> None:
    """
    Run the guard over every assembled root, including the derived ones.

    The per-value check in :func:`_resolve_path` only sees roots the file names. A root
    can also arrive DERIVED — from `library.root`, or as ``/Volumes/<volume>/<dcim>`` out
    of a camera profile — and those reach exactly the same destructive operations. An
    import moves files off the camera root and deletes the originals, so a profile with
    ``volume = "EXT"`` and ``dcim = "."`` would empty an external disk; that path never
    passed through `_resolve_path`, so it is checked here.

    Args:
        roots: The assembled roots, keyed by :data:`ROOT_KEYS`.
        path: The file being read, for the error message.
        configured: The keys the `[roots]` table named explicitly. Anything else gets its
            origin named in the refusal.

    Raises:
        LibraryConfigError: Any root fails :func:`root_refusal`.
    """
    for key in ROOT_KEYS:
        reason = root_refusal(roots[key])
        if reason is None:
            continue
        origin = "" if key in configured else f" ({_ROOT_ORIGIN[key]})"
        raise _fail(path, f"roots.{key} = {str(roots[key])!r}{origin}: {reason}")


def _check_root_overlaps(roots: Mapping[str, Path], path: Path) -> None:
    """
    Refuse root combinations that would make an operation eat its own input.

    Four real hazards, each one a plausible typo rather than a theoretical one:

    * **Two roots naming the same directory.** ``final == staging`` turns finalize into a
      copy of a directory onto itself followed by a delete of the source.
    * **One culling root inside the other.** Every scan is recursive, so Staging inside
      Final means finalize keeps rediscovering what it just moved.
    * **The trash inside a culling root, or a culling root inside the trash.** A trashed
      photo would be re-indexed as a live one, and purging the trash would reach live files.
    * **Any other root inside any other root.** The three cases above were the ones with
      names; the general rule is what actually holds, and the unnamed pairs are no safer.
      Measured before this clause existed: ``camera`` inside ``final`` loaded clean, and
      `import` MOVES every JPG, RAF and MOV off the camera root and deletes the originals
      — i.e. it would empty Final into Staging. ``gallery`` containing ``final`` hands
      `sync_gallery`'s "remove what no longer rates 4+" step the masters themselves, and
      ``raws`` under a culling root hands `cleanup` a tree nobody meant it to scan.

    No legitimate layout needs one stage inside another — each root is a distinct step of
    the pipeline, and the built-in defaults are pairwise disjoint, which the suite pins so
    the rule cannot be tightened into breaking the daily driver.

    Raises:
        LibraryConfigError: Any of the four.
    """
    seen: Dict[Tuple[str, ...], str] = {}
    for key in ROOT_KEYS:
        value = roots[key]
        # Keyed on the casefolded components, not the Path: on macOS's case-insensitive
        # default filesystem `~/Pictures/Final` and `~/Pictures/final` are one directory,
        # and two roots naming it under different spellings is the hazard below.
        fingerprint = _parts_key(value)
        if fingerprint in seen:
            raise _fail(path, f"roots.{key} and roots.{seen[fingerprint]} are the same directory: {value}")
        seen[fingerprint] = key

    staging, final, trash = roots["staging"], roots["final"], roots["trash"]
    for a, b in ((staging, final), (final, staging)):
        if _inside(a, b):
            raise _fail(path, f"roots.staging and roots.final must not contain each other ({a} is inside {b})")
    for key in LIBRARY_ROOT_KEYS:
        root = roots[key]
        if _inside(trash, root):
            raise _fail(path, f"roots.trash must not be inside roots.{key} ({trash} is inside {root})")
        if _inside(root, trash):
            raise _fail(path, f"roots.{key} must not be inside roots.trash ({root} is inside {trash})")

    # The general rule, after the three specific messages above, which read better for the
    # pairs they name. Ordered pairs, so the refusal always says which one is inside which.
    for outer in ROOT_KEYS:
        for inner in ROOT_KEYS:
            if inner == outer or not _inside(roots[inner], roots[outer]):
                continue
            raise _fail(
                path,
                f"roots.{inner} must not be inside roots.{outer} "
                f"({roots[inner]} is inside {roots[outer]}): every root is a separate stage "
                f"of the pipeline, and each one is scanned, published from or emptied "
                f"recursively",
            )


def _check_template(template: str, path: Path) -> str:
    """
    Validate a filename template.

    Args:
        template: The configured value.
        path: The file being read.

    Returns:
        The template unchanged.

    Raises:
        LibraryConfigError: The template drops the original stem, contains a path
            separator, or uses a placeholder that does not exist. All three rename files
            wrongly and only the third is visible by reading the string.
    """
    if "{base}" not in template:
        raise _fail(path, "naming.template must contain {base} — dropping the camera's own "
                          "stem breaks JPG-to-RAW correlation")
    if "/" in template or os.sep in template:
        raise _fail(path, "naming.template is a filename, not a path — it must not contain a separator")
    rest = template
    for placeholder in NAMING_PLACEHOLDERS:
        rest = rest.replace(placeholder, "")
    if "{" in rest or "}" in rest:
        raise _fail(path, f"naming.template has an unknown placeholder; known ones are "
                          f"{', '.join(NAMING_PLACEHOLDERS)}")
    return template


# ---------------------------------------------------------------------------
# Reading
# ---------------------------------------------------------------------------


def install_path() -> Path:
    """
    Where the install file is expected.

    Reads :data:`INSTALL_PATH_ENV` at call time rather than at import so a test — and a
    second library run side by side — can relocate it without reloading the package.
    """
    override = os.environ.get(INSTALL_PATH_ENV)
    return Path(os.path.expanduser(override)) if override else DEFAULT_INSTALL_PATH


def _read_document(path: Path) -> Optional[Dict[str, Any]]:
    """
    Parse one TOML file.

    Returns:
        The document, or None when the file does not exist.

    Raises:
        LibraryConfigError: The file exists but could not be read or parsed.
    """
    try:
        raw = path.read_bytes()
    except FileNotFoundError:
        return None
    except OSError as exc:
        raise _fail(path, f"could not be read: {exc}") from exc
    try:
        return _toml.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, _toml.TOMLDecodeError) as exc:
        raise _fail(path, f"is not valid TOML: {exc}") from exc


def _read_cameras(document: Mapping[str, Any], path: Path) -> Tuple[CameraProfile, ...]:
    """
    Read ``[[cameras]]``, defaulting to the single profile the tool shipped with.

    Raises:
        LibraryConfigError: The array is malformed, an id repeats, or a volume is not a
            plain name (a volume containing a separator is someone writing a path into a
            field that is joined onto ``/Volumes``).
    """
    entries = document.get("cameras", [])
    if not isinstance(entries, list) or not all(isinstance(item, dict) for item in entries):
        raise _fail(path, "[[cameras]] must be an array of tables")
    if not entries:
        entries = [dict(DEFAULT_CAMERA)]

    allowed = ("id", "name", "volume", "dcim", "photos", "raws", "videos")
    cameras: List[CameraProfile] = []
    seen: List[str] = []
    for index, entry in enumerate(entries):
        where = f"[[cameras]] #{index + 1}"
        _reject_unknown(entry, allowed, where, path)
        volume = _string(entry, "volume", "", where, path)
        if not volume:
            raise _fail(path, f"{where} needs a volume (the name the card mounts under)")
        if "/" in volume:
            raise _fail(path, f"{where}.volume is a volume NAME, not a path (got {volume!r})")
        dcim = _string(entry, "dcim", "DCIM", where, path)
        if dcim.startswith("/"):
            raise _fail(path, f"{where}.dcim is relative to the volume (got {dcim!r})")
        # `..` would walk the camera root back out of the volume — and import MOVES files
        # off the camera root and deletes the originals. The assembled-roots guard would
        # refuse the result anyway; refusing it here names the key that caused it.
        if ".." in Path(dcim).parts:
            raise _fail(path, f"{where}.dcim must stay inside the volume (got {dcim!r})")
        identifier = _string(entry, "id", volume.lower().replace(" ", "-"), where, path)
        if identifier in seen:
            raise _fail(path, f"{where}: duplicate camera id {identifier!r}")
        seen.append(identifier)
        cameras.append(
            CameraProfile(
                id=identifier,
                name=_string(entry, "name", volume, where, path),
                volume=volume,
                dcim=dcim,
                photos=_extensions(entry, "photos", DEFAULT_CAMERA["photos"], where, path),
                raws=_extensions(entry, "raws", DEFAULT_CAMERA["raws"], where, path),
                videos=_extensions(entry, "videos", DEFAULT_CAMERA["videos"], where, path),
            )
        )
    return tuple(cameras)


def _read_editors(document: Mapping[str, Any], path: Path) -> Tuple[EditorProfile, ...]:
    """
    Read ``[[editors]]``, defaulting to the single JPEG editor the tool shipped with.

    An explicit ``editors = []`` is honoured as "no hand-over on this machine" rather than
    silently replaced by the default — unlike ``[[cameras]]``, where an empty list cannot
    mean anything, because a tool with no camera cannot import.

    Raises:
        LibraryConfigError: The array is malformed, an id repeats, ``app`` is a path
            rather than an application name, or ``handles`` names a kind that does not
            exist.
    """
    if "editors" not in document:
        entries: List[Any] = [dict(entry) for entry in DEFAULT_EDITORS]
    else:
        entries = document["editors"]
    if not isinstance(entries, list) or not all(isinstance(item, dict) for item in entries):
        raise _fail(path, "[[editors]] must be an array of tables")

    allowed = ("id", "name", "app", "handles")
    editors: List[EditorProfile] = []
    seen: List[str] = []
    for index, entry in enumerate(entries):
        where = f"[[editors]] #{index + 1}"
        _reject_unknown(entry, allowed, where, path)
        app = _string(entry, "app", "", where, path)
        if not app:
            raise _fail(path, f"{where} needs an app (the application NAME `open -a` resolves)")
        # A NAME, not a path — the same rule as `cameras.volume`, and here it is also what
        # keeps the launch argument from becoming "run this arbitrary binary for me".
        if "/" in app or os.sep in app:
            raise _fail(path, f"{where}.app is an application NAME, not a path (got {app!r})")
        handles = entry.get("handles", ["jpeg"])
        if not isinstance(handles, list) or not all(isinstance(item, str) for item in handles):
            raise _fail(path, f"{where}.handles must be a list of strings like ['jpeg']")
        cleaned = tuple(item.strip().lower() for item in handles)
        if not cleaned:
            raise _fail(
                path,
                f"{where}.handles must name at least one of {', '.join(EDITOR_KINDS)} — "
                f"an editor that handles nothing can never be offered",
            )
        for kind in cleaned:
            if kind not in EDITOR_KINDS:
                raise _fail(
                    path,
                    f"{where}.handles: {kind!r} is not a file kind "
                    f"(allowed: {', '.join(EDITOR_KINDS)})",
                )
        identifier = _string(entry, "id", app.lower().replace(" ", "-"), where, path)
        if identifier in seen:
            raise _fail(path, f"{where}: duplicate editor id {identifier!r}")
        seen.append(identifier)
        editors.append(
            EditorProfile(
                id=identifier,
                name=_string(entry, "name", app, where, path),
                app=app,
                handles=tuple(dict.fromkeys(cleaned)),
            )
        )
    return tuple(editors)


def _default_editors() -> Tuple[EditorProfile, ...]:
    """The editors of an install with no file — i.e. v0.4.13's single hardcoded name."""
    return tuple(
        EditorProfile(
            id=str(entry["id"]),
            name=str(entry["name"]),
            app=str(entry["app"]),
            handles=tuple(entry["handles"]),
        )
        for entry in DEFAULT_EDITORS
    )


#: What a root's directory currently is. `unmounted` is the one that matters: an external
#: volume that is not plugged in is a temporary, self-correcting condition, and reporting
#: it as "file not found" sends the reader looking for a photograph that is fine.
ROOT_STATES: Tuple[str, ...] = ("ready", "unmounted", "missing")


def root_availability(root: Path) -> Tuple[str, str]:
    """
    Say whether a root is usable, and if not, whether the reason is a missing VOLUME.

    The distinction is the whole function. ``/Volumes/EXT/Bilder/RAWs`` not existing
    almost always means the external disk is unplugged, not that the RAW archive was
    deleted — and those two sentences send the reader to completely different places.
    A path is judged unmounted when some ancestor of it is a direct child of a mount
    container (:data:`ROOT_CONTAINERS` — ``/Volumes``, ``/mnt``, ``/media``, ``/net``)
    and that ancestor does not exist. The container itself existing while the volume
    directory under it does not IS what "not mounted" looks like on macOS.

    Args:
        root: The configured root, already resolved.

    Returns:
        ``(state, detail)`` where state is one of :data:`ROOT_STATES` and detail is a
        sentence for a human — empty when the state is ``ready``.
    """
    try:
        if root.is_dir():
            return "ready", ""
    except OSError:  # pragma: no cover — a stale mount can raise here
        pass

    mount_containers = {
        container for container, _child, _contents in ROOT_CONTAINERS if container != "/"
    }
    # Nearest first: /Volumes/EXT/Bilder/RAWs asks about /Volumes/EXT.
    for ancestor in [root] + list(root.parents):
        parent = ancestor.parent
        if str(parent) not in mount_containers:
            continue
        if not ancestor.exists():
            return "unmounted", f"{ancestor.name} is not mounted ({parent} has no {ancestor.name})."
        break

    return "missing", f"{root} does not exist."


def load_install(path: Optional[Path] = None) -> InstallConfig:
    """
    Read the install file, or return the built-in defaults when there is none.

    All-or-nothing: any problem raises and nothing from the file is applied. A half-read
    roots table is the one outcome that could point an operation at a directory the user
    never named.

    Args:
        path: Override the location; defaults to :func:`install_path`.

    Returns:
        The resolved install configuration.

    Raises:
        LibraryConfigError: The file exists and is malformed, or any value fails validation.
    """
    target = Path(path) if path is not None else install_path()
    document = _read_document(target)
    if document is None:
        return _default_install(target)

    _reject_unknown(document, ("library", "roots", "cameras", "editors"), "the install file", target)

    library_table = _table(document, "library", target)
    _reject_unknown(library_table, ("root",), "[library]", target)
    library_root = (
        _resolve_path(
            _string(library_table, "root", "", "library", target), "library.root", target,
            containers=False,
        )
        if "root" in library_table
        else _best_effort_resolve(DEFAULT_LIBRARY_ROOT)
    )

    cameras = _read_cameras(document, target)
    editors = _read_editors(document, target)
    roots_table = _table(document, "roots", target)
    _reject_unknown(roots_table, ROOT_KEYS, "[roots]", target)
    defaults = _default_roots(library_root, cameras[0])
    roots: Dict[str, Path] = {}
    configured: List[str] = []
    for key in ROOT_KEYS:
        if key not in roots_table:
            roots[key] = defaults[key]
            continue
        value = roots_table[key]
        if not isinstance(value, str):
            raise _fail(target, f"roots.{key} must be a string path")
        roots[key] = _resolve_path(value, f"roots.{key}", target)
        configured.append(key)
    _check_roots_safe(roots, target, configured)
    _check_root_overlaps(roots, target)

    return InstallConfig(
        path=target,
        present=True,
        library_root=library_root,
        roots=roots,
        cameras=cameras,
        editors=editors,
    )


def _default_roots(library_root: Path, camera: CameraProfile) -> Dict[str, Path]:
    """
    The roots implied by a library root and a camera, with no file present.

    Staging, Final and the trash hang off the library root; the RAW and video drives and
    the gallery build directory do not, because they are external volumes and a path
    inside this source tree. Deriving those from the library root would be tidier and
    wrong.
    """
    derived = {
        "camera": camera.camera_path,
        "staging": library_root / "Staging",
        "final": library_root / "Final",
        "raws": DEFAULT_RAWS_PATH,
        "videos": DEFAULT_VIDEOS_PATH,
        "gallery": DEFAULT_GALLERY_PATH,
        # Leading dot keeps it out of Photos/Photomator library scans; under the library
        # root so a move out of Final or Staging is a same-filesystem rename.
        "trash": library_root / ".photoflow-trash",
    }
    # Resolved like a configured root, so every value in `InstallConfig.roots` is the one
    # the guard checked regardless of where it came from.
    return {key: _best_effort_resolve(value) for key, value in derived.items()}


def _default_install(target: Path) -> InstallConfig:
    """The configuration of an install with no file — i.e. today's hardcoded behaviour."""
    camera = CameraProfile(
        id=str(DEFAULT_CAMERA["id"]),
        name=str(DEFAULT_CAMERA["name"]),
        volume=str(DEFAULT_CAMERA["volume"]),
        dcim=str(DEFAULT_CAMERA["dcim"]),
        photos=tuple(DEFAULT_CAMERA["photos"]),
        raws=tuple(DEFAULT_CAMERA["raws"]),
        videos=tuple(DEFAULT_CAMERA["videos"]),
    )
    roots = _default_roots(_best_effort_resolve(DEFAULT_LIBRARY_ROOT), camera)
    # The defaults are code, but they are computed from `Path.home()`, which is
    # environment. Checking them costs seven `resolve()` calls at import and means an
    # environment that makes the built-in defaults unsafe fails loudly instead of running.
    _check_roots_safe(roots, target)
    return InstallConfig(
        path=target,
        present=False,
        library_root=_best_effort_resolve(DEFAULT_LIBRARY_ROOT),
        roots=roots,
        cameras=(camera,),
        editors=_default_editors(),
    )


def load_organisation(path: Path) -> LibraryOrganisation:
    """
    Read the library file's three organisation axes.

    Unlike :func:`load_install` this never raises. Nothing here can send an operation at a
    directory — the axes are recorded, not acted on — and the same file is read by
    :mod:`photo_flow.collections`, which already degrades on a parse error and refuses the
    subsequent write. One file may not have two contradictory failure modes.

    Unknown TOP-LEVEL tables are ignored: the file is shared with ``[[collections]]`` and
    is meant to be annotated by hand. Unknown keys inside the three tables this module
    owns are reported, because there they are misspelled settings.

    Args:
        path: The library file.

    Returns:
        The organisation, with defaults for anything missing or unreadable, and `errors`
        naming every problem found.
    """
    try:
        document = _read_document(path)
    except LibraryConfigError as exc:
        logger.warning("%s", exc)
        return LibraryOrganisation(path=path, present=False, readable=False, errors=(str(exc),))
    if document is None:
        return LibraryOrganisation(path=path, present=False, readable=True)

    owned = ("stage", "layout", "naming")
    present = any(key in document for key in owned)
    try:
        stage_table = _table(document, "stage", path)
        _reject_unknown(stage_table, ("mode", "tag"), "[stage]", path)
        stage = StageConfig(
            mode=_choice(stage_table, "mode", STAGE_MODES, DEFAULT_STAGE_MODE, "stage", path),
            tag=_string(stage_table, "tag", DEFAULT_STAGE_TAG, "stage", path),
        )
        layout_table = _table(document, "layout", path)
        _reject_unknown(layout_table, ("mode",), "[layout]", path)
        layout = LayoutConfig(
            mode=_choice(layout_table, "mode", LAYOUT_MODES, DEFAULT_LAYOUT_MODE, "layout", path)
        )
        naming_table = _table(document, "naming", path)
        _reject_unknown(naming_table, ("template", "apply"), "[naming]", path)
        naming = NamingConfig(
            template=_check_template(
                _string(naming_table, "template", DEFAULT_NAMING_TEMPLATE, "naming", path), path
            ),
            apply=_choice(naming_table, "apply", NAMING_MODES, DEFAULT_NAMING_APPLY, "naming", path),
        )
    except LibraryConfigError as exc:
        logger.warning("%s", exc)
        return LibraryOrganisation(path=path, present=present, readable=False, errors=(str(exc),))

    return LibraryOrganisation(path=path, present=present, readable=True,
                               stage=stage, layout=layout, naming=naming)


# ---------------------------------------------------------------------------
# Writing the install file
#
# Only the install file, only when it is absent, and only the defaults. There is no
# "save my settings" path here on purpose: the file is meant to be read and edited by a
# person, and a writer that rewrites a hand-annotated document is how F1's collection
# store lost its `[library]` table. The library file already has a lossless writer in
# `photo_flow.collections`, which preserves the three organisation tables verbatim.
# ---------------------------------------------------------------------------


def _toml_string(value: str) -> str:
    """Quote one string for TOML, escaping the characters a path can legally contain."""
    escaped = value.replace("\\", "\\\\").replace('"', '\\"')
    return f'"{escaped}"'


def render_install(install: InstallConfig) -> str:
    """
    Render an install configuration as a commented TOML document.

    Args:
        install: The configuration to serialise — normally the defaults.

    Returns:
        The file text, ending in a newline.
    """
    lines = [
        "# photo-flow installation configuration.",
        "#",
        "# WHERE things are on THIS machine. Everything here is a local fact — a mount",
        "# point, a volume name — and would be wrong on another computer.",
        "#",
        "# HOW the library is arranged lives in the library itself, next to the pictures:",
        f"#   {install.library_config_path}",
        "# ([stage], [layout], [naming] and the saved [[collections]]).",
        "#",
        "# Delete this file to go back to the built-in defaults. Every value below IS the",
        "# built-in default, so this file changes nothing until you edit it.",
        "",
        "[library]",
        f"root = {_toml_string(str(install.library_root))}",
        "",
        "[roots]",
        "# Absolute paths. `~` is expanded; symlinks and `..` are resolved before anything",
        "# below is checked. No root may sit inside another — every scan in the tool is",
        "# recursive, and each root is scanned, published from or emptied independently.",
        "#",
        "# Refused, because a backup and a gallery publish start at these directories: a",
        "# whole mounted volume (/Volumes/EXT), a whole account (/Users/someone), any",
        "# container (/, /Users, /Volumes) and anything inside a system tree (/System,",
        "# /usr, /private, ~/Library, ~/.ssh). Name a directory INSIDE a library.",
    ]
    for key in ROOT_KEYS:
        lines.append(f"{key} = {_toml_string(str(install.roots[key]))}")
    for camera in install.cameras:
        lines += [
            "",
            "[[cameras]]",
            "# `volume` is the NAME the card mounts under, i.e. /Volumes/<volume>.",
            f"id = {_toml_string(camera.id)}",
            f"name = {_toml_string(camera.name)}",
            f"volume = {_toml_string(camera.volume)}",
            f"dcim = {_toml_string(camera.dcim)}",
            f"photos = [{', '.join(_toml_string(e) for e in camera.photos)}]",
            f"raws = [{', '.join(_toml_string(e) for e in camera.raws)}]",
            f"videos = [{', '.join(_toml_string(e) for e in camera.videos)}]",
        ]
    lines += [
        "",
        "# External applications the culling view can hand a file to (E and R on the",
        "# photos screen). `app` is the application NAME Launch Services resolves, not a",
        "# path, so the bundle may live in /Applications or ~/Applications.",
        "#",
        "# `handles` is the point of the table: a JPEG master goes to a pixel editor and a",
        "# RAF goes to a RAW developer, and those are usually different programs. Within one",
        "# kind, the first entry listed is the one the keyboard shortcut uses.",
        "#",
        "# No RAW developer is configured by default — naming one would be a guess about",
        "# what you have installed. Uncomment and edit to get `R` working:",
        "#",
        "#   [[editors]]",
        '#   id = "darktable"',
        '#   name = "darktable"',
        '#   app = "darktable"',
        '#   handles = ["raw"]',
        "#",
        "# Set `editors = []` (replacing every block below) to turn the hand-over off.",
    ]
    for editor in install.editors:
        lines += [
            "",
            "[[editors]]",
            f"id = {_toml_string(editor.id)}",
            f"name = {_toml_string(editor.name)}",
            f"app = {_toml_string(editor.app)}",
            f"handles = [{', '.join(_toml_string(k) for k in editor.handles)}]",
        ]
    return "\n".join(lines) + "\n"


def write_install_template(path: Optional[Path] = None) -> Path:
    """
    Write a commented default install file, refusing to overwrite an existing one.

    Args:
        path: Override the location; defaults to :func:`install_path`.

    Returns:
        The path written.

    Raises:
        LibraryConfigError: A file is already there. Overwriting it would destroy a
            hand-edited document to produce one that says exactly what the defaults
            already say.
        OSError: The file could not be written.
    """
    target = Path(path) if path is not None else install_path()
    if target.exists():
        raise _fail(target, "already exists — edit it, or move it aside first")
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(render_install(_default_install(target)), encoding="utf-8")
    return target
