"""
Tests for photo_flow.index.thumbs — the content-addressed thumbnail cache.

Every test redirects ``thumbs.THUMB_CACHE_PATH`` at a tmp_path directory, so the
real ``~/.photoflow/thumbs`` is never read or written.

The invariants under test:
  * the cache key is content-addressed — a changed mtime yields a new key
  * both tiers render, bounded by their configured long edge
  * EXIF orientation is applied, so a portrait frame is never served sideways
  * a generation failure leaves no ``.tmp-*`` turd and no half-written entry
  * ``get_thumbs()`` decodes the source exactly ONCE for however many tiers it
    renders — the whole point of the function, and the one thing a refactor can
    silently undo while every other assertion still passes
  * ``warm()`` counts real generations, not cache hits
  * ``purge_orphans()`` honours the age threshold
  * an unreadable/absent/corrupt source returns (None, message) and never raises
"""

import os
import time
from pathlib import Path

import piexif
import pytest
from PIL import Image

from photo_flow.config import THUMB_SIZES
from photo_flow.index import thumbs


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture(autouse=True)
def cache_dir(tmp_path, monkeypatch):
    """Redirect the thumbnail cache at a tmp dir for the whole module."""
    cache = tmp_path / "thumbs"
    monkeypatch.setattr(thumbs, "THUMB_CACHE_PATH", cache)
    return cache


@pytest.fixture()
def photos_dir(tmp_path):
    d = tmp_path / "Final"
    d.mkdir()
    return d


def _make_jpeg(
    directory: Path,
    name: str,
    size: tuple = (1000, 600),
    orientation: int = 0,
) -> Path:
    """
    Write a real JPEG fixture, optionally carrying an EXIF Orientation tag.

    Args:
        directory: Folder to write into.
        name: Filename (including the .JPG extension).
        size: (width, height) of the stored pixel data.
        orientation: EXIF Orientation value, or 0 to write no EXIF at all.

    Returns:
        Path to the written file.
    """
    path = directory / name
    image = Image.new("RGB", size, (90, 120, 160))
    # A flat colour compresses to almost nothing and hides resampling bugs;
    # a gradient keeps the file honest.
    for x in range(0, size[0], 20):
        for y in range(0, size[1], 20):
            image.putpixel((x, y), (x % 256, y % 256, 40))

    if orientation:
        exif_bytes = piexif.dump({"0th": {piexif.ImageIFD.Orientation: orientation}})
        image.save(path, "JPEG", quality=90, exif=exif_bytes)
    else:
        image.save(path, "JPEG", quality=90)
    return path


def _tmp_leftovers(cache: Path) -> list:
    """Every partial write still sitting in the cache tree."""
    return [p for p in cache.rglob("*") if ".tmp-" in p.name]


def _count_opens(monkeypatch, source: Path) -> list:
    """
    Record every ``Image.open`` of ``source`` from here on.

    Returns the (live) list of recorded opens. Only the source is counted, so a test
    may still open its own output to inspect it.
    """
    real_open = Image.open
    seen: list = []

    def spy(fp, *args, **kwargs):
        if isinstance(fp, (str, Path)) and Path(fp) == source:
            seen.append(fp)
        return real_open(fp, *args, **kwargs)

    monkeypatch.setattr(Image, "open", spy)
    return seen


# ---------------------------------------------------------------------------
# Cache key
# ---------------------------------------------------------------------------

class TestCacheKey:
    def test_stable_for_an_unchanged_file(self, photos_dir):
        photo = _make_jpeg(photos_dir, "a.JPG")
        assert thumbs.cache_key(photo, "grid") == thumbs.cache_key(photo, "grid")

    def test_changes_with_mtime(self, photos_dir):
        photo = _make_jpeg(photos_dir, "a.JPG")
        before = thumbs.cache_key(photo, "grid")

        future = photo.stat().st_mtime + 120
        os.utime(photo, (future, future))

        assert thumbs.cache_key(photo, "grid") != before

    def test_changes_with_size(self, photos_dir):
        photo = _make_jpeg(photos_dir, "a.JPG")
        before = thumbs.cache_key(photo, "grid")

        stat = photo.stat()
        photo.write_bytes(photo.read_bytes() + b"\x00" * 64)
        os.utime(photo, (stat.st_atime, stat.st_mtime))  # isolate the size change

        assert thumbs.cache_key(photo, "grid") != before

    def test_differs_per_tier(self, photos_dir):
        photo = _make_jpeg(photos_dir, "a.JPG")
        assert thumbs.cache_key(photo, "grid") != thumbs.cache_key(photo, "view")

    def test_unknown_tier_raises(self, photos_dir):
        photo = _make_jpeg(photos_dir, "a.JPG")
        with pytest.raises(KeyError):
            thumbs.cache_key(photo, "enormous")

    def test_thumb_path_is_sharded_under_the_tier(self, photos_dir, cache_dir):
        photo = _make_jpeg(photos_dir, "a.JPG")
        key = thumbs.cache_key(photo, "grid")
        path = thumbs.thumb_path(photo, "grid")

        assert path == cache_dir / "grid" / key[:2] / f"{key}.jpg"
        assert not path.exists(), "thumb_path must not generate anything"


# ---------------------------------------------------------------------------
# Generation
# ---------------------------------------------------------------------------

class TestGetThumb:
    @pytest.mark.parametrize("tier", sorted(THUMB_SIZES))
    def test_generates_within_the_tier_long_edge(self, photos_dir, tier):
        photo = _make_jpeg(photos_dir, "big.JPG", size=(3000, 2000))

        result, error = thumbs.get_thumb(photo, tier)

        assert error == ""
        assert result is not None and result.is_file()
        with Image.open(result) as out:
            assert out.format == "JPEG"
            assert max(out.size) == THUMB_SIZES[tier]
            # Aspect ratio preserved (3:2)
            assert out.size[0] / out.size[1] == pytest.approx(1.5, abs=0.01)

    def test_second_call_is_a_cache_hit(self, photos_dir):
        photo = _make_jpeg(photos_dir, "hit.JPG")

        first, _ = thumbs.get_thumb(photo, "grid")
        assert first is not None
        stamp = first.stat().st_mtime_ns

        second, _ = thumbs.get_thumb(photo, "grid")

        assert second == first
        assert second is not None and second.stat().st_mtime_ns == stamp, "regenerated on a hit"

    def test_edited_source_regenerates_under_a_new_key(self, photos_dir):
        photo = _make_jpeg(photos_dir, "edited.JPG", size=(800, 600))
        first, _ = thumbs.get_thumb(photo, "grid")

        # Re-save at a different aspect: same path, new mtime → new key.
        _make_jpeg(photos_dir, "edited.JPG", size=(600, 800))
        second, _ = thumbs.get_thumb(photo, "grid")

        assert first is not None and second is not None
        assert second != first
        with Image.open(second) as out:
            assert out.size[1] > out.size[0], "the new thumbnail must reflect the new source"

    def test_smaller_source_is_not_upscaled(self, photos_dir):
        photo = _make_jpeg(photos_dir, "small.JPG", size=(120, 80))

        result, error = thumbs.get_thumb(photo, "view")

        assert error == ""
        assert result is not None
        with Image.open(result) as out:
            assert out.size == (120, 80)

    def test_exif_orientation_is_applied(self, photos_dir):
        """Orientation 6 = rotate 90° CW; a stored-landscape frame must come out portrait."""
        photo = _make_jpeg(photos_dir, "rotated.JPG", size=(600, 300), orientation=6)

        result, error = thumbs.get_thumb(photo, "grid")

        assert error == ""
        assert result is not None
        with Image.open(result) as out:
            assert out.size[1] > out.size[0], "EXIF orientation was ignored"
            # 600x300 stored → 300x600 after the transpose → 160x320 at the grid long edge
            assert out.size == (160, 320)

    def test_no_orientation_tag_is_left_alone(self, photos_dir):
        photo = _make_jpeg(photos_dir, "flat.JPG", size=(600, 300))

        result, _ = thumbs.get_thumb(photo, "grid")

        assert result is not None
        with Image.open(result) as out:
            assert out.size == (320, 160)

    def test_no_tmp_file_left_behind(self, photos_dir, cache_dir):
        photo = _make_jpeg(photos_dir, "clean.JPG")

        thumbs.get_thumb(photo, "grid")

        assert _tmp_leftovers(cache_dir) == []

    def test_failed_save_leaves_neither_tmp_nor_entry(self, photos_dir, cache_dir, monkeypatch):
        photo = _make_jpeg(photos_dir, "boom.JPG")

        def explode(*_args, **_kwargs):
            raise OSError("disk went away mid-save")

        monkeypatch.setattr(Image.Image, "save", explode)

        result, error = thumbs.get_thumb(photo, "grid")

        assert result is None
        assert "boom.JPG" in error
        assert _tmp_leftovers(cache_dir) == []
        assert not thumbs.thumb_path(photo, "grid").exists()


# ---------------------------------------------------------------------------
# Failure modes — get_thumb must never raise
# ---------------------------------------------------------------------------

class TestGetThumbFailures:
    def test_missing_source(self, photos_dir):
        result, error = thumbs.get_thumb(photos_dir / "ghost.JPG", "grid")

        assert result is None
        assert "not found" in error.lower()

    def test_corrupt_source(self, photos_dir):
        broken = photos_dir / "corrupt.JPG"
        broken.write_bytes(b"this is definitely not a JPEG")

        result, error = thumbs.get_thumb(broken, "grid")

        assert result is None
        assert error != ""

    def test_empty_source(self, photos_dir):
        empty = photos_dir / "empty.JPG"
        empty.write_bytes(b"")

        result, error = thumbs.get_thumb(empty, "grid")

        assert result is None
        assert error != ""

    def test_unknown_tier(self, photos_dir):
        photo = _make_jpeg(photos_dir, "a.JPG")

        result, error = thumbs.get_thumb(photo, "gigantic")

        assert result is None
        assert "tier" in error.lower()

    def test_directory_as_source(self, photos_dir):
        result, error = thumbs.get_thumb(photos_dir, "grid")

        assert result is None
        assert error != ""


# ---------------------------------------------------------------------------
# get_thumbs — many tiers, one decode
# ---------------------------------------------------------------------------

class TestGetThumbs:
    def test_both_tiers_from_one_decode(self, photos_dir, monkeypatch):
        """
        The saving that justifies this function: the decode dominates generation and
        barely varies with the tier, so two tiers must cost ONE Image.open of the source.
        """
        photo = _make_jpeg(photos_dir, "dual.JPG", size=(3000, 2000))
        opens = _count_opens(monkeypatch, photo)

        results = thumbs.get_thumbs(photo, ["grid", "view"])

        assert len(opens) == 1, f"decoded the source {len(opens)} times, not once"
        assert set(results) == {"grid", "view"}
        for tier, (path, error) in results.items():
            assert error == ""
            assert path is not None and path.is_file()
            with Image.open(path) as out:
                assert max(out.size) == THUMB_SIZES[tier]

    def test_derived_grid_matches_the_single_tier_geometry(self, photos_dir):
        """
        The small tier is downscaled from the large one, not from the source. That must
        not change what the filmstrip gets: same long edge, same aspect.
        """
        photo = _make_jpeg(photos_dir, "shape.JPG", size=(3000, 2000))
        solo, _ = thumbs.get_thumb(photo, "grid")
        assert solo is not None
        with Image.open(solo) as out:
            solo_size = out.size
        solo.unlink()

        derived, _ = thumbs.get_thumbs(photo, ["grid", "view"])["grid"]

        assert derived is not None
        with Image.open(derived) as out:
            assert max(out.size) == THUMB_SIZES["grid"] == max(solo_size)
            assert out.size[0] / out.size[1] == pytest.approx(1.5, abs=0.01)

    @pytest.mark.parametrize("size", [(5200, 3466), (3466, 5200), (6240, 4160)])
    def test_tier_geometry_is_independent_of_the_batch(self, photos_dir, size):
        """
        A tier's dimensions must be a pure function of (source, tier) — identical whether
        it was generated alone or alongside a larger tier. Both entry points share one
        cache key, so a disagreement means the filmstrip's cell aspect depends on whether
        a prewarm or a direct fetch happened to get there first.

        The X-T4's native rasters are the fixture on purpose: 5200x3466 has an inexact
        aspect (1.50029), which is what exposes the bug. Deriving each box from whatever
        draft() produced gave 214 px standalone (drafted 1/8, itself rounding 433.25 up)
        against 213 px in a grid+view batch (drafted 1/2).
        """
        photo = _make_jpeg(photos_dir, f"batch_{size[0]}x{size[1]}.JPG", size=size)

        solo, _ = thumbs.get_thumb(photo, "grid")
        assert solo is not None
        with Image.open(solo) as out:
            solo_size = out.size
        solo.unlink()

        derived, _ = thumbs.get_thumbs(photo, ["grid", "view"])["grid"]
        assert derived is not None
        with Image.open(derived) as out:
            assert out.size == solo_size, (
                f"grid is {out.size} in a grid+view batch but {solo_size} alone"
            )

    def test_cached_tier_is_not_regenerated(self, photos_dir):
        photo = _make_jpeg(photos_dir, "mixed.JPG", size=(1200, 800))
        grid, _ = thumbs.get_thumb(photo, "grid")
        assert grid is not None
        stamp = grid.stat().st_mtime_ns

        results = thumbs.get_thumbs(photo, ["grid", "view"])

        assert results["grid"][0] == grid
        assert grid.stat().st_mtime_ns == stamp, "a warm tier was regenerated"
        assert results["view"][0] is not None and results["view"][0].is_file()

    def test_only_the_missing_tier_is_decoded_for(self, photos_dir, monkeypatch):
        photo = _make_jpeg(photos_dir, "warmgrid.JPG", size=(1200, 800))
        thumbs.get_thumbs(photo, ["grid", "view"])
        opens = _count_opens(monkeypatch, photo)

        results = thumbs.get_thumbs(photo, ["grid", "view"])

        assert opens == [], "an all-hit call must not touch the source at all"
        assert all(path is not None for path, _ in results.values())

    def test_duplicate_tiers_collapse(self, photos_dir):
        photo = _make_jpeg(photos_dir, "dupe.JPG")

        results = thumbs.get_thumbs(photo, ["grid", "grid"])

        assert list(results) == ["grid"]
        assert results["grid"][0] is not None

    def test_empty_tier_list(self, photos_dir):
        photo = _make_jpeg(photos_dir, "none.JPG")
        assert thumbs.get_thumbs(photo, []) == {}

    def test_unknown_tier_fails_only_itself(self, photos_dir):
        photo = _make_jpeg(photos_dir, "partial.JPG")

        results = thumbs.get_thumbs(photo, ["grid", "enormous"])

        assert results["grid"][0] is not None and results["grid"][1] == ""
        assert results["enormous"][0] is None
        assert "tier" in results["enormous"][1].lower()

    def test_missing_source_fails_every_tier(self, photos_dir):
        results = thumbs.get_thumbs(photos_dir / "ghost.JPG", ["grid", "view"])

        assert set(results) == {"grid", "view"}
        for path, error in results.values():
            assert path is None
            assert "not found" in error.lower()

    def test_corrupt_source_fails_every_tier_without_raising(self, photos_dir, cache_dir):
        broken = photos_dir / "broken.JPG"
        broken.write_bytes(b"definitely not a JPEG")

        results = thumbs.get_thumbs(broken, ["grid", "view"])

        assert all(path is None and error != "" for path, error in results.values())
        assert _tmp_leftovers(cache_dir) == []

    def test_a_tier_that_landed_before_the_failure_is_still_reported(
        self, photos_dir, cache_dir, monkeypatch
    ):
        """
        Tiers are written largest-first from one decode. If the second encode dies, the
        first is already a valid cache entry — reporting it as failed would throw away
        work and force another 250 ms decode on the next request.
        """
        photo = _make_jpeg(photos_dir, "halfway.JPG", size=(1200, 800))
        real_save = Image.Image.save
        saves = []

        def save_once(self, *args, **kwargs):
            saves.append(1)
            if len(saves) > 1:
                raise OSError("disk went away mid-batch")
            return real_save(self, *args, **kwargs)

        monkeypatch.setattr(Image.Image, "save", save_once)

        results = thumbs.get_thumbs(photo, ["grid", "view"])

        assert results["view"][0] is not None and results["view"][0].is_file()
        assert results["view"][1] == ""
        assert results["grid"][0] is None and "halfway.JPG" in results["grid"][1]
        assert _tmp_leftovers(cache_dir) == []


# ---------------------------------------------------------------------------
# warm
# ---------------------------------------------------------------------------

class TestWarmTiers:
    def test_counts_generations_per_tier(self, photos_dir):
        photos = [_make_jpeg(photos_dir, f"m{i}.JPG", size=(400, 300)) for i in range(3)]

        generated = thumbs.warm_tiers(photos, ["grid", "view"])

        assert generated == 6, "3 sources x 2 tiers"
        assert all(thumbs.thumb_path(p, t).is_file() for p in photos for t in ("grid", "view"))

    def test_only_the_missing_tier_is_generated(self, photos_dir):
        photos = [_make_jpeg(photos_dir, f"m{i}.JPG", size=(400, 300)) for i in range(3)]
        thumbs.warm(photos, "grid")

        generated = thumbs.warm_tiers(photos, ["grid", "view"])

        assert generated == 3, "the grid tier was already warm"

    def test_fully_warm_batch_generates_nothing(self, photos_dir):
        photos = [_make_jpeg(photos_dir, f"m{i}.JPG", size=(400, 300)) for i in range(2)]
        thumbs.warm_tiers(photos, ["grid", "view"])

        assert thumbs.warm_tiers(photos, ["grid", "view"]) == 0

    def test_one_unknown_tier_poisons_the_call(self, photos_dir, cache_dir):
        photo = _make_jpeg(photos_dir, "a.JPG")

        assert thumbs.warm_tiers([photo], ["grid", "nope"]) == 0
        assert not thumbs.thumb_path(photo, "grid").exists(), "nothing may be written"

    def test_empty_tier_list(self, photos_dir):
        photo = _make_jpeg(photos_dir, "a.JPG")
        assert thumbs.warm_tiers([photo], []) == 0

    def test_unreadable_entries_are_skipped_not_fatal(self, photos_dir):
        good = _make_jpeg(photos_dir, "good.JPG", size=(400, 300))
        corrupt = photos_dir / "bad.JPG"
        corrupt.write_bytes(b"nope")

        assert thumbs.warm_tiers([good, corrupt], ["grid", "view"]) == 2


class TestWarm:
    def test_generates_every_missing_thumbnail(self, photos_dir):
        photos = [_make_jpeg(photos_dir, f"w{i}.JPG", size=(400, 300)) for i in range(5)]

        generated = thumbs.warm(photos, "grid")

        assert generated == 5
        assert all(thumbs.thumb_path(p, "grid").is_file() for p in photos)

    def test_cache_hits_are_not_counted(self, photos_dir):
        photos = [_make_jpeg(photos_dir, f"w{i}.JPG", size=(400, 300)) for i in range(3)]
        thumbs.warm(photos[:2], "grid")

        generated = thumbs.warm(photos, "grid")

        assert generated == 1

    def test_unreadable_entries_are_skipped_not_fatal(self, photos_dir):
        good = _make_jpeg(photos_dir, "good.JPG", size=(400, 300))
        missing = photos_dir / "gone.JPG"
        corrupt = photos_dir / "bad.JPG"
        corrupt.write_bytes(b"nope")

        generated = thumbs.warm([good, missing, corrupt], "grid")

        assert generated == 1

    def test_unknown_tier_returns_zero(self, photos_dir):
        photo = _make_jpeg(photos_dir, "a.JPG")
        assert thumbs.warm([photo], "nope") == 0

    def test_empty_batch(self):
        assert thumbs.warm([], "grid") == 0


# ---------------------------------------------------------------------------
# purge_orphans / cache_stats
# ---------------------------------------------------------------------------

class TestPurgeOrphans:
    def test_respects_the_age_threshold(self, photos_dir):
        fresh = _make_jpeg(photos_dir, "fresh.JPG", size=(400, 300))
        stale = _make_jpeg(photos_dir, "stale.JPG", size=(400, 300))
        fresh_thumb, _ = thumbs.get_thumb(fresh, "grid")
        stale_thumb, _ = thumbs.get_thumb(stale, "grid")
        assert fresh_thumb is not None and stale_thumb is not None

        old = time.time() - 40 * 86400
        os.utime(stale_thumb, (old, old))

        removed = thumbs.purge_orphans(max_age_days=30)

        assert removed == 1
        assert fresh_thumb.is_file()
        assert not stale_thumb.exists()

    def test_nothing_to_purge(self, photos_dir):
        photo = _make_jpeg(photos_dir, "a.JPG", size=(400, 300))
        thumbs.get_thumb(photo, "grid")

        assert thumbs.purge_orphans(max_age_days=30) == 0

    def test_missing_cache_dir_is_a_noop(self):
        assert thumbs.purge_orphans(max_age_days=30) == 0


class TestCacheStats:
    def test_empty_cache(self):
        stats = thumbs.cache_stats()

        assert stats["files"] == 0
        assert stats["bytes"] == 0
        assert set(stats["by_tier"]) == set(THUMB_SIZES)

    def test_counts_per_tier(self, photos_dir):
        photo = _make_jpeg(photos_dir, "a.JPG", size=(900, 600))
        other = _make_jpeg(photos_dir, "b.JPG", size=(900, 600))
        thumbs.get_thumb(photo, "grid")
        thumbs.get_thumb(other, "grid")
        thumbs.get_thumb(photo, "view")

        stats = thumbs.cache_stats()

        assert stats["files"] == 3
        assert stats["bytes"] > 0
        assert stats["by_tier"]["grid"]["files"] == 2
        assert stats["by_tier"]["view"]["files"] == 1
        assert (
            stats["bytes"]
            == stats["by_tier"]["grid"]["bytes"] + stats["by_tier"]["view"]["bytes"]
        )
