"""
The one read-only door onto RAWS_PATH from a client-facing request.

Decision 0005 (`shutterflow/docs/decisions/0005-raw-scope.md`) keeps RAW support to
existence, correlation, backup and orphan protection — never decoding. Handing a RAF to
an external RAW developer needs exactly one more capability on top of that: given the JPG
a person is looking at in the culling view, find the RAF that came off the same shutter
press. This module is the ONLY thing in photo-flow allowed to resolve a path INTO the RAW
archive on behalf of an HTTP request, and it is read-only by construction — every function
here returns a :class:`Path` or refuses to, it never opens, moves, writes or deletes one.

Why this is a separate resolver and not a widened ``_allowed_roots``
----------------------------------------------------------------------
``photo_flow.api.routes_photos._resolve_in_roots`` is the allowlist every OTHER
client-supplied path goes through, and several of its callers are destructive: rating and
label write-back run exiftool ``-overwrite_original`` on the resolved path, and
``/api/photos/trash`` moves it. Adding RAWS_PATH to that allowlist would make every one of
those endpoints newly capable of writing to or moving an irreplaceable RAW — for a feature
that only ever needs to READ one path, and only from one endpoint. So the RAW hand-over
gets its own narrow function instead: it takes a JPG path that has ALREADY been validated
against the normal culling roots, derives the correlating RAF itself, and hands back a
path — never accepting one from the caller. See the audit in shutterflow's 0005 decision
record for the full reasoning, endpoint by endpoint.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Optional, Tuple

from photo_flow import library_config
from photo_flow.file_manager import scan_for_images
from photo_flow.timestamp_renamer import correlation_base

#: What asking "where is the RAW for this JPG" can answer.
#:   found      — `path` is set, safe to hand to an editor.
#:   no_raw     — the RAW root is reachable but no RAF correlates to this JPG (a JPG-only
#:                shot, or its RAW was already cleaned up as an orphan).
#:   unmounted  — the RAW root's volume is not currently mounted. Temporary and
#:                self-correcting: plug the drive in and retry.
#:   missing    — the RAW root does not exist at all, mounted or not. A misconfiguration,
#:                not a missing drive.
RAW_LINK_STATES: Tuple[str, ...] = ("found", "no_raw", "unmounted", "missing")


@dataclass(frozen=True)
class RawLinkResult:
    """
    The answer to "does this JPG have a RAW, and can I reach it right now?".

    Attributes:
        state: One of :data:`RAW_LINK_STATES`.
        path: The resolved RAF, set only when ``state == "found"``.
        detail: A sentence for a human, already distinguishing *why* there is no path —
            empty only for ``found``.
    """

    state: str
    path: Optional[Path] = None
    detail: str = ""


def find_raw(jpg_path: Path, raws_root: Path) -> RawLinkResult:
    """
    Locate the RAF that correlates to `jpg_path`, or say honestly why there is none.

    Uses :func:`photo_flow.timestamp_renamer.correlation_base`, never
    ``extract_original_base`` — it additionally strips Photomator's ``_2``/``_3``
    duplicate-export suffix, and matching on the wrong function is the exact bug class
    that has previously exposed irreplaceable RAWs to deletion elsewhere in this codebase.

    Args:
        jpg_path: The JPG the culling view is looking at. Already resolved and validated
            against ``CULL_ROOTS`` by the caller — only its filename is used here, and it
            need not exist on disk for the lookup itself to run.
        raws_root: ``config.RAWS_PATH``, passed explicitly (rather than imported) so a
            test can relocate it without monkeypatching this module.

    Returns:
        A :class:`RawLinkResult`. Only ``state == "found"`` carries a `path`; every other
        state carries a `detail` sentence a human can act on.
    """
    state, mount_detail = library_config.root_availability(raws_root)
    if state == "unmounted":
        return RawLinkResult(
            state=state,
            detail=f"The RAW archive is not mounted — {mount_detail} Connect the drive "
                   f"and try again.",
        )
    if state == "missing":
        return RawLinkResult(
            state=state,
            detail=f"The RAW archive does not exist — {mount_detail} Check roots.raws in "
                   f"~/.photoflow/config.toml.",
        )

    target = correlation_base(jpg_path.name)
    for raw in scan_for_images(raws_root, ".RAF"):
        if correlation_base(raw.name) == target:
            return RawLinkResult(state="found", path=raw)

    return RawLinkResult(
        state="no_raw",
        detail=f"No RAW file correlates to {jpg_path.name}. It may have been shot "
               f"JPG-only, or its RAW has already been cleaned up as an orphan.",
    )
