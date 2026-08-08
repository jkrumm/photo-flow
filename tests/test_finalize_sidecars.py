"""
Finalize's handling of Photomator .photo-edit sidecars.

The sidecar is the only copy of a photo's re-editable edit history — there is no second
copy anywhere — so every path that moves a JPG must move its sidecar too, and no path may
delete one. These tests pin the two ways a sidecar used to get stranded in Staging:

  1. The JPG is already in Final (duplicate skip), so the loop `continue`d past the sidecar.
  2. The JPG left Staging in an earlier interrupted run, so the JPG-driven loop never
     visits it again.
"""
import pytest

from photo_flow import workflow as workflow_module
from photo_flow.progress import NullReporter
from photo_flow.workflow import PhotoWorkflow

SIDECAR = '.photo-edit'


@pytest.fixture
def paths(tmp_path, monkeypatch):
    """Point the workflow at throwaway Staging/Final dirs; keep camera and RAWs absent."""
    staging = tmp_path / "Staging"
    final = tmp_path / "Final"
    staging.mkdir()
    final.mkdir()

    monkeypatch.setattr(workflow_module, "STAGING_PATH", staging)
    monkeypatch.setattr(workflow_module, "FINAL_PATH", final)
    # Absent → finalize's camera-RAW and orphan-RAW steps are skipped entirely.
    monkeypatch.setattr(workflow_module, "CAMERA_PATH", tmp_path / "no-camera")
    monkeypatch.setattr(workflow_module, "RAWS_PATH", tmp_path / "no-raws")
    return staging, final


def _write(path, content: bytes = b"content"):
    path.write_bytes(content)
    return path


def _finalize(dry_run: bool = False):
    return PhotoWorkflow().finalize_staging(dry_run=dry_run, reporter=NullReporter())


class TestSidecarTravelsWithItsJpg:
    def test_moved_with_its_jpg(self, paths):
        staging, final = paths
        _write(staging / "2026-01-01_10-00-00_DSCF0001.JPG")
        _write(staging / f"2026-01-01_10-00-00_DSCF0001{SIDECAR}", b"edit history")

        stats = _finalize()

        assert stats['moved'] == 1
        assert stats['edits_moved'] == 1
        assert stats['errors'] == 0
        assert (final / f"2026-01-01_10-00-00_DSCF0001{SIDECAR}").read_bytes() == b"edit history"
        assert not (staging / f"2026-01-01_10-00-00_DSCF0001{SIDECAR}").exists()

    def test_moved_even_when_the_jpg_is_a_duplicate(self, paths):
        """JPG already in Final: it is skipped, but the sidecar must still follow it over."""
        staging, final = paths
        _write(staging / "dup.JPG", b"same")
        _write(final / "dup.JPG", b"same")
        _write(staging / f"dup{SIDECAR}", b"edit history")

        stats = _finalize()

        assert stats['skipped'] == 1
        assert stats['edits_moved'] == 1
        assert stats['errors'] == 0
        assert (final / f"dup{SIDECAR}").read_bytes() == b"edit history"
        assert not (staging / f"dup{SIDECAR}").exists()


class TestStrandedSidecarSweep:
    def test_sidecar_whose_jpg_already_reached_final_is_recovered(self, paths):
        """Interrupted earlier run: JPG copied and deleted, sidecar left behind.

        Staging also holds an unrelated JPG, so the main loop runs and the sweep has to
        catch the stranded sidecar afterwards rather than via the empty-staging path.
        """
        staging, final = paths
        _write(staging / "fresh.JPG")
        _write(final / "orphan.JPG")
        _write(staging / f"orphan{SIDECAR}", b"edit history")

        stats = _finalize()

        assert stats['moved'] == 1
        assert stats['edits_moved'] == 1
        assert stats['errors'] == 0
        assert (final / f"orphan{SIDECAR}").read_bytes() == b"edit history"
        assert not (staging / f"orphan{SIDECAR}").exists()

    def test_sweep_runs_even_when_staging_has_no_jpgs(self, paths):
        """The early 'nothing to finalize' return must not skip the sweep."""
        staging, final = paths
        _write(final / "orphan.JPG")
        _write(staging / f"orphan{SIDECAR}", b"edit history")

        stats = _finalize()

        assert stats['moved'] == 0
        assert stats['edits_moved'] == 1
        assert (final / f"orphan{SIDECAR}").exists()

    def test_sidecar_with_no_jpg_anywhere_is_left_alone(self, paths):
        """Irreplaceable history with no owner: report it, never delete it."""
        staging, final = paths
        _write(staging / f"unknown{SIDECAR}", b"edit history")

        stats = _finalize()

        assert stats['edits_moved'] == 0
        assert (staging / f"unknown{SIDECAR}").exists()
        assert not (final / f"unknown{SIDECAR}").exists()

    def test_appledouble_fork_is_not_mistaken_for_a_sidecar(self, paths):
        staging, final = paths
        _write(staging / f"._orphan{SIDECAR}", b"resource fork")

        stats = _finalize()

        assert stats['edits_moved'] == 0
        assert stats['errors'] == 0
        assert (staging / f"._orphan{SIDECAR}").exists()

    def test_dry_run_counts_but_moves_nothing(self, paths):
        staging, final = paths
        _write(final / "orphan.JPG")
        _write(staging / f"orphan{SIDECAR}", b"edit history")

        stats = _finalize(dry_run=True)

        assert stats['edits_moved'] == 1
        assert (staging / f"orphan{SIDECAR}").exists()
        assert not (final / f"orphan{SIDECAR}").exists()
