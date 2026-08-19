"""
Content-addressed thumbnail cache for the control panel's culling view.

Two tiers are served (see ``config.THUMB_SIZES``):

* ``grid`` — 320 px long edge, feeds the filmstrip.
* ``view`` — 2048 px long edge, feeds the big viewer.

Both are progressive, optimized JPEGs at ``config.THUMB_QUALITY``.

Cache layout::

    THUMB_CACHE_PATH / <tier> / <key[:2]> / <key>.jpg
    key = sha1(f"{abs_path}|{mtime_ns}|{size}|{tier}").hexdigest()

The key is content-addressed on ``(path, mtime_ns, size)``, so an edited or
re-rated JPG naturally misses and regenerates — no invalidation bookkeeping.
The superseded entry is simply orphaned and reclaimed later by
:func:`purge_orphans`.

Performance note: generation calls ``Image.draft()`` *before* any pixel is
decoded, so libjpeg decodes a Fuji frame at 1/2 or 1/8 scale in the DCT domain
instead of decoding 18–26 MP in full. Measured end-to-end on this library
(M-series, page cache warm), against the same pipeline without ``draft``:

===================  ==========  ==========  ==========
corpus / tier        with draft  no draft    saving
===================  ==========  ==========  ==========
Staging 26 MP grid    130 ms      198 ms      34 %
Staging 26 MP view    261 ms      417 ms      37 %
Final 18 MP   view    253 ms      359 ms      30 %
Final 18 MP   grid    190 ms      198 ms       4 %
===================  ==========  ==========  ==========

Not the order of magnitude one might expect: libjpeg still has to Huffman-decode
every MCU regardless of output scale, and Photomator re-saves Final JPEGs as
*progressive*, where entropy decoding dominates and DCT scaling has little left
to save (hence the 4 % row). The peak-memory saving is the larger prize —
a grid thumbnail decodes into ~1 MB instead of ~78 MB, which is what makes a
4-worker :func:`warm` sane. Keep the call.

The cache is what actually makes stepping instant: a warm hit is ~0.03 ms.

Single decode, many tiers
-------------------------
Profiling a real 5200x3466 Fuji frame shows the decode *dominates* and is nearly
tier-independent — 117 ms at draft(1280) against 39 ms for the LANCZOS resize and
2–9 ms for the encode. Generating ``grid`` and ``view`` through two separate
:func:`get_thumb` calls therefore pays for two full decodes to save nothing.

:func:`get_thumbs` opens the source **once**, drafts to the *largest* requested
tier and derives the smaller ones by successive downscale of the in-memory image.
Two tiers then cost ~1.3x one tier instead of 2x. Drafting to the smallest tier
would be the obvious mistake: it throws away exactly the pixels the large tier
needs.

Everything here is derived and disposable: deleting ``THUMB_CACHE_PATH`` costs
nothing but regeneration.

"""

import hashlib
import logging
import os
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Dict, List, Optional, Sequence, Tuple

from PIL import Image, ImageOps

from photo_flow.config import THUMB_CACHE_PATH, THUMB_QUALITY, THUMB_SIZES

logger = logging.getLogger(__name__)

# Suffix of every cache entry. Tier and source identity live in the key/dir.
_THUMB_SUFFIX = ".jpg"


def _target_size(tier: str) -> int:
    """
    Resolve a tier name to its long-edge pixel target.

    Args:
        tier: Tier name, one of the keys of ``config.THUMB_SIZES``.

    Returns:
        Long-edge target in pixels.

    Raises:
        KeyError: If the tier is unknown.
    """
    return THUMB_SIZES[tier]


def cache_key(src: Path, tier: str) -> str:
    """
    Compute the content-addressed cache key for a source file and tier.

    Args:
        src: Source image path (need not be absolute; it is resolved).
        tier: Tier name ('grid' or 'view').

    Returns:
        Hex SHA-1 digest over (absolute path, mtime_ns, size, tier).

    Raises:
        OSError: If the source file cannot be stat'ed.
        KeyError: If the tier is unknown.
    """
    _target_size(tier)  # validate the tier before touching the filesystem
    resolved = src.resolve()
    stat = resolved.stat()
    material = f"{resolved}|{stat.st_mtime_ns}|{stat.st_size}|{tier}"
    return hashlib.sha1(material.encode("utf-8")).hexdigest()


def thumb_path(src: Path, tier: str) -> Path:
    """
    Return the cache location for a source file's thumbnail.

    Performs no I/O beyond a single ``stat()`` on the source; the returned path
    may or may not exist yet.

    Args:
        src: Source image path.
        tier: Tier name ('grid' or 'view').

    Returns:
        Absolute path inside THUMB_CACHE_PATH where the thumbnail lives.

    Raises:
        OSError: If the source file cannot be stat'ed.
        KeyError: If the tier is unknown.
    """
    key = cache_key(src, tier)
    return THUMB_CACHE_PATH / tier / key[:2] / f"{key}{_THUMB_SUFFIX}"


def _draft_box(size: Tuple[int, int], target: int) -> Tuple[int, int]:
    """
    Compute the aspect-preserving output size for a long-edge target.

    Used as the ``draft()`` box so the DCT-domain reduction is constrained by
    the long edge, matching what ``thumbnail()`` will produce.

    Args:
        size: Source (width, height) as stored in the file.
        target: Long-edge target in pixels.

    Returns:
        (width, height) with the long edge at ``target``, or the input size
        unchanged when the source is already smaller than the target.
    """
    width, height = size
    longest = max(width, height)
    if longest <= target:
        return width, height
    scale = target / longest
    return max(1, round(width * scale)), max(1, round(height * scale))


def _encode(image: Image.Image, dst: Path, tier: str, icc_profile: Optional[bytes]) -> None:
    """
    Encode an already-sized image into the cache atomically.

    Args:
        image: RGB image at its final tier dimensions.
        dst: Destination cache path.
        tier: Tier name, used only to pick the JPEG quality.
        icc_profile: Source colour profile to embed, or None.

    Raises:
        OSError: On an unwritable cache directory or a failed save.
    """
    dst.parent.mkdir(parents=True, exist_ok=True)

    # Write to a private sibling, then rename: a reader never sees a
    # half-written cache file, and concurrent generators of the same key
    # simply overwrite each other with byte-identical output.
    tmp = dst.with_name(f"{dst.name}.tmp-{os.getpid()}-{threading.get_ident()}")
    try:
        save_kwargs: Dict[str, object] = {
            "quality": THUMB_QUALITY[tier],
            "progressive": True,
            "optimize": True,
        }
        if icc_profile:
            save_kwargs["icc_profile"] = icc_profile
        image.save(tmp, "JPEG", **save_kwargs)
        os.replace(tmp, dst)
    finally:
        if tmp.exists():
            tmp.unlink(missing_ok=True)


def _generate(src: Path, targets: Dict[str, Path]) -> None:
    """
    Render every requested tier from a SINGLE decode of the source.

    The decode is the expensive half (~117 ms against ~39 ms for the resize on a
    26 MP frame) and barely varies with the output tier, so the tiers are derived
    from one in-memory image by successive downscale — largest first, because
    drafting to the small tier would discard the data the large one needs.

    Args:
        src: Source image path.
        targets: Mapping of tier name → destination cache path. Must be non-empty
            and contain only known tiers.

    Raises:
        OSError: On unreadable source or unwritable cache directory.
        Exception: Any Pillow decoding error is propagated to the caller.
    """
    ordered = sorted(targets.items(), key=lambda item: _target_size(item[0]), reverse=True)
    largest = _target_size(ordered[0][0])

    with Image.open(src) as im:
        # True display size of the SOURCE, captured before draft() distorts it. Every tier's
        # output box is computed from this, so a tier's dimensions are a pure function of
        # (source, tier) and never depend on which other tiers shared the batch. Reading
        # .size and the Orientation tag is header-only — it does not decode, so this stays
        # one decode per source.
        #
        # Deriving the boxes any other way desynchronises the two entry points. Letting
        # thumbnail() round against whatever draft() produced makes
        # `get_thumbs(src, ['grid','view'])` and `get_thumb(src, 'grid')` disagree by a pixel
        # (213 vs 214 on a 5200x3466 frame): the first drafts to 2048 and rounds 213.29 down,
        # the second drafts to 320 — a 1/8 reduction that already rounded 433.25 up to 434 —
        # and rounds 213.66 up. Same cache key, two possible answers, whichever path ran
        # first winning, so the filmstrip's cell aspect would shift depending on whether a
        # prewarm or a direct fetch got there first.
        base_size = im.size
        if im.getexif().get(0x0112) in (5, 6, 7, 8):
            base_size = (base_size[1], base_size[0])

        # DCT-domain downscale hint. MUST happen before the first pixel access,
        # otherwise Pillow has already loaded and draft() silently does nothing.
        #
        # The box has to be the ASPECT-PRESERVED output size, not (target,
        # target): draft only reduces while BOTH dimensions stay >= the box, so
        # a square box lets the SHORT edge veto the reduction. On a 5200x3466
        # frame a (2048, 2048) box yields no reduction at all, while the
        # (2048, 1365) box below yields the 1/2 scale we actually want.
        im.draft("RGB", _draft_box(im.size, largest))
        # Orientation must be applied AFTER draft (draft is a loader hint and is
        # cancelled by a prior load) and BEFORE thumbnail, so portrait shots are
        # not served sideways and the long edge is measured post-rotation.
        work = ImageOps.exif_transpose(im) or im
        icc_profile = work.info.get("icc_profile")

        for tier, dst in ordered:
            box = _draft_box(base_size, _target_size(tier))
            # Successive downscale, largest first: `work` is already the previous (larger)
            # tier, so each step resizes from it rather than re-reading the source. The box
            # comes from base_size, not from work.size, so the chain cannot accumulate
            # rounding drift. resize() to an explicit box rather than thumbnail(), which
            # would recompute the box from whatever work currently measures.
            if work.size != box:
                work = work.resize(box, Image.LANCZOS, reducing_gap=2.0)
            encodable = work if work.mode == "RGB" else work.convert("RGB")
            _encode(encodable, dst, tier, icc_profile)


def get_thumbs(src: Path, tiers: Sequence[str]) -> Dict[str, Tuple[Optional[Path], str]]:
    """
    Return cached thumbnails for several tiers, decoding the source at most once.

    Cache hits are served without touching the source at all; every tier that
    misses is rendered from one shared decode (see :func:`_generate`). Never
    raises — each tier reports its own failure through its error string, so one
    unreadable photo cannot take down a prewarm batch.

    Args:
        src: Source image path.
        tiers: Tier names to resolve. Duplicates are collapsed; an unknown tier
            fails only itself.

    Returns:
        Mapping of tier name → (thumbnail path, error message). On success the
        path is set and the message is empty; on failure the path is None.
    """
    results: Dict[str, Tuple[Optional[Path], str]] = {}
    wanted: List[str] = []
    for tier in tiers:
        if tier in results:
            continue
        if tier not in THUMB_SIZES:
            results[tier] = (None, f"Unknown thumbnail tier: {tier}")
            continue
        results[tier] = (None, "")
        wanted.append(tier)

    if not wanted:
        return results

    def fail_all(message: str) -> Dict[str, Tuple[Optional[Path], str]]:
        for name in wanted:
            results[name] = (None, message)
        return results

    try:
        if not src.is_file():
            return fail_all(f"Source not found: {src}")
        destinations = {tier: thumb_path(src, tier) for tier in wanted}
    except OSError as exc:
        return fail_all(f"Cannot stat source {src}: {exc}")

    missing: Dict[str, Path] = {}
    for tier in wanted:
        dst = destinations[tier]
        if dst.is_file():
            results[tier] = (dst, "")
        else:
            missing[tier] = dst

    if not missing:
        return results

    error = ""
    try:
        _generate(src, missing)
    except Exception as exc:  # noqa: BLE001 - a bad JPEG must not break the caller
        logger.error(
            "Thumbnail generation failed for %s (%s): %s", src, ",".join(missing), exc
        )
        error = f"Thumbnail generation failed for {src.name}: {exc}"

    for tier, dst in missing.items():
        # A mid-batch failure can still have landed the larger tiers; report each
        # tier by what actually reached the cache rather than by the exception.
        results[tier] = (dst, "") if dst.is_file() else (None, error)
    return results


def get_thumb(src: Path, tier: str) -> Tuple[Optional[Path], str]:
    """
    Return the cached thumbnail for a source file, generating it on a miss.

    Thin single-tier wrapper over :func:`get_thumbs`; never raises.

    Args:
        src: Source image path.
        tier: Tier name ('grid' or 'view').

    Returns:
        Tuple of (thumbnail path, error message). On success the path is set
        and the message is empty; on failure the path is None.
    """
    return get_thumbs(src, [tier])[tier]


def warm_tiers(paths: Sequence[Path], tiers: Sequence[str], workers: int = 4) -> int:
    """
    Pre-generate several tiers for a batch of sources in parallel.

    Pillow releases the GIL during JPEG decode and resize, so threads genuinely
    parallelise here. Per source the tiers share one decode, which is what makes
    warming the filmstrip and the viewer together cost ~1.3x rather than 2x.
    Already-cached entries are counted as free hits, not generations.

    Args:
        paths: Source image paths to warm.
        tiers: Tier names to warm per source.
        workers: Thread pool size.

    Returns:
        Number of thumbnails actually generated, counted per tier (cache hits
        excluded). Zero if any requested tier is unknown.
    """
    wanted = list(dict.fromkeys(tiers))
    if not wanted:
        return 0
    for tier in wanted:
        if tier not in THUMB_SIZES:
            logger.error("warm() called with unknown tier: %s", tier)
            return 0

    pending: List[Tuple[Path, List[str]]] = []
    for path in paths:
        try:
            if not path.is_file():
                continue
            todo = [tier for tier in wanted if not thumb_path(path, tier).is_file()]
        except OSError as exc:
            logger.debug("Skipping unreadable source during warm: %s (%s)", path, exc)
            continue
        if todo:
            pending.append((path, todo))

    if not pending:
        return 0

    generated = 0
    with ThreadPoolExecutor(max_workers=max(1, workers)) as pool:
        for outcome in pool.map(lambda item: get_thumbs(item[0], item[1]), pending):
            generated += sum(1 for result, _error in outcome.values() if result is not None)
    return generated


def warm(paths: Sequence[Path], tier: str, workers: int = 4) -> int:
    """
    Pre-generate one tier for a batch of sources. Single-tier :func:`warm_tiers`.

    Args:
        paths: Source image paths to warm.
        tier: Tier name ('grid' or 'view').
        workers: Thread pool size.

    Returns:
        Number of thumbnails actually generated (cache hits excluded).
    """
    return warm_tiers(paths, [tier], workers)


def purge_orphans(max_age_days: int = 30) -> int:
    """
    Delete cache entries that have not been touched for ``max_age_days``.

    Retention keys off the CACHE FILE's own mtime, which is correct here and
    only here: these are derived files this module wrote, so their mtime is
    their creation time. (Contrast the trash purge, which must key off the
    recorded trash timestamp — a photo keeps its capture-time mtime, so an
    mtime sweep there would delete freshly-trashed old photos immediately.)

    Args:
        max_age_days: Age threshold in days. Entries younger than this are kept.

    Returns:
        Number of cache files deleted.
    """
    if not THUMB_CACHE_PATH.is_dir():
        return 0

    cutoff = time.time() - max_age_days * 86400
    removed = 0
    for entry in THUMB_CACHE_PATH.rglob(f"*{_THUMB_SUFFIX}"):
        try:
            if entry.is_file() and entry.stat().st_mtime < cutoff:
                entry.unlink()
                removed += 1
        except OSError as exc:
            logger.debug("Could not purge cache entry %s: %s", entry, exc)
    return removed


def cache_stats() -> Dict[str, object]:
    """
    Summarise the on-disk size of the thumbnail cache.

    Returns:
        Dict with 'files' (int), 'bytes' (int) and 'by_tier'
        ({tier: {'files': int, 'bytes': int}}) for every configured tier.
    """
    by_tier: Dict[str, Dict[str, int]] = {
        tier: {"files": 0, "bytes": 0} for tier in THUMB_SIZES
    }
    total_files = 0
    total_bytes = 0

    for tier in THUMB_SIZES:
        tier_dir = THUMB_CACHE_PATH / tier
        if not tier_dir.is_dir():
            continue
        for entry in tier_dir.rglob(f"*{_THUMB_SUFFIX}"):
            try:
                size = entry.stat().st_size
            except OSError:
                continue
            by_tier[tier]["files"] += 1
            by_tier[tier]["bytes"] += size
            total_files += 1
            total_bytes += size

    return {"files": total_files, "bytes": total_bytes, "by_tier": by_tier}
