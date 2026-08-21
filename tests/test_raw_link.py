"""
Tests for `photo_flow.raw_link` — the one read-only door onto RAWS_PATH.

Every function here is exercised directly, with a `tmp_path`-relocated `raws_root`
argument — nothing reads or writes the real `~/Pictures` tree, and nothing here ever
mounts, moves, or writes to a file. The unmounted-volume case is the exception worth
noting: it points `raws_root` at a fabricated `/Volumes/<name>` path so the outcome does
not depend on what is actually plugged into the machine running the suite. See
`tests/test_photos_api.py::TestOpenInEditorRaw` for the same behaviour exercised through
the HTTP endpoint that is `raw_link`'s only caller.
"""
from __future__ import annotations

from pathlib import Path

from photo_flow import raw_link


def _write(directory: Path, name: str) -> Path:
    """A throwaway file — only its name matters to correlation, never its bytes."""
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / name
    path.write_bytes(b"not a real raf")
    return path


class TestFindRaw:
    def test_finds_the_correlating_raw(self, tmp_path):
        raws = tmp_path / "RAWs"
        raf = _write(raws, "2026-03-03_17-36-33_DSCF0770.RAF")
        jpg = tmp_path / "Final" / "2026-03-03_17-36-33_DSCF0770.JPG"

        result = raw_link.find_raw(jpg, raws)

        assert result.state == "found"
        assert result.path == raf
        assert result.detail == ""

    def test_correlates_through_a_photomator_duplicate_suffix(self, tmp_path):
        """`DSCF0770_2.jpg` (a Photomator re-export) must still find `DSCF0770.RAF`."""
        raws = tmp_path / "RAWs"
        raf = _write(raws, "2026-03-03_17-36-33_DSCF0770.RAF")
        jpg = tmp_path / "Final" / "2026-03-03_17-36-33_DSCF0770_2.jpg"

        result = raw_link.find_raw(jpg, raws)

        assert result.state == "found"
        assert result.path == raf

    def test_lowercase_raf_extension_still_matches(self, tmp_path):
        raws = tmp_path / "RAWs"
        raf = _write(raws, "2026-03-03_17-36-33_DSCF0770.raf")
        jpg = tmp_path / "Final" / "2026-03-03_17-36-33_DSCF0770.JPG"

        result = raw_link.find_raw(jpg, raws)

        assert result.state == "found"
        assert result.path == raf

    def test_a_jpg_with_no_correlating_raw(self, tmp_path):
        raws = tmp_path / "RAWs"
        raws.mkdir()
        _write(raws, "2026-03-03_17-36-33_DSCF0001.RAF")
        jpg = tmp_path / "Final" / "2026-03-03_17-36-33_DSCF9999.JPG"

        result = raw_link.find_raw(jpg, raws)

        assert result.state == "no_raw"
        assert result.path is None
        assert "No RAW file correlates" in result.detail

    def test_an_unmounted_raw_volume_is_reported_as_unmounted(self, tmp_path):
        # A made-up volume name under /Volumes: guaranteed not to be mounted, regardless
        # of what is actually plugged into the machine running this test.
        raws = Path("/Volumes/PhotoFlowTestVolumeNotMounted/RAWs")
        jpg = tmp_path / "Final" / "2026-03-03_17-36-33_DSCF0002.JPG"

        result = raw_link.find_raw(jpg, raws)

        assert result.state == "unmounted"
        assert result.path is None
        assert "not mounted" in result.detail

    def test_a_missing_raw_root_is_reported_as_missing_not_unmounted(self, tmp_path):
        # Not under a mount container at all, so `root_availability` calls this "missing"
        # rather than "unmounted" — the two need different next actions from a human.
        raws = tmp_path / "gone" / "RAWs"
        jpg = tmp_path / "Final" / "2026-03-03_17-36-33_DSCF0003.JPG"

        result = raw_link.find_raw(jpg, raws)

        assert result.state == "missing"
        assert result.path is None
        assert "does not exist" in result.detail
        assert "not mounted" not in result.detail
