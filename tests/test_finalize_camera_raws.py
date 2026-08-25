"""
Finalize's step 2 — deleting a finalized photo's RAW from the camera card.

The card is the ONLY other place a camera RAF exists. Step 2 used to delete one on the
sole evidence that a JPG with the same base had reached Final, which is not evidence the
RAW was ever imported: `import_from_camera` routes RAWs to the external SSD and skips
them entirely when it is unmounted, while the JPGs go to Staging and finalize normally.

Observed live on 2026-08-25 with the SSD unmounted: 328 of 707 camera RAFs matched a
Final JPG and had no copy anywhere else. These tests pin the guard that now stands
between that state and an irreversible unlink.
"""
import pytest

from photo_flow import file_manager as file_manager_module
from photo_flow import workflow as workflow_module
from photo_flow.progress import NullReporter
from photo_flow.workflow import PhotoWorkflow


@pytest.fixture
def tree(tmp_path, monkeypatch):
    """Staging (empty), Final, a camera DCIM dir and a RAWs dir, all throwaway."""
    staging = tmp_path / "Staging"
    final = tmp_path / "Final"
    camera = tmp_path / "DCIM"
    raws = tmp_path / "RAWs"
    for d in (staging, final, camera, raws):
        d.mkdir()
    # scan_camera_files globs CAMERA_PATH/*_*; a Fuji-shaped subfolder is what it expects.
    shot = camera / "100_FUJI"
    shot.mkdir()

    monkeypatch.setattr(workflow_module, "STAGING_PATH", staging)
    monkeypatch.setattr(workflow_module, "FINAL_PATH", final)
    monkeypatch.setattr(workflow_module, "CAMERA_PATH", camera)
    # file_manager imports CAMERA_PATH from config itself — patching only the
    # workflow module leaves scan_camera_files pointed at the real card.
    monkeypatch.setattr(file_manager_module, "CAMERA_PATH", camera)
    monkeypatch.setattr(workflow_module, "RAWS_PATH", raws)
    # finalize returns early when Staging holds no JPG, and step 2 is never reached.
    # This photo is unrelated to the RAWs under test — it exists only to get us there.
    (staging / "2026-01-28_09-00-00_DSCF9999.JPG").write_bytes(b"passenger")
    return staging, final, shot, raws


def _finalize(dry_run: bool = False):
    return PhotoWorkflow().finalize_staging(dry_run=dry_run, reporter=NullReporter())


def test_camera_raw_deleted_when_its_import_is_visible_on_the_raw_drive(tree):
    _staging, final, shot, raws = tree
    (final / "2026-01-28_10-29-15_DSCF0430.JPG").write_bytes(b"jpg")
    (raws / "2026-01-28_10-29-15_DSCF0430.RAF").write_bytes(b"raf")
    card_raw = shot / "DSCF0430.RAF"
    card_raw.write_bytes(b"raf")

    stats = _finalize()

    assert not card_raw.exists()
    assert stats['deleted_camera_raws'] == 1
    assert stats['unbacked_camera_raws'] == 0


def test_camera_raw_is_kept_when_it_was_never_imported(tree):
    """The regression that mattered: JPG in Final, RAW nowhere but the card."""
    _staging, final, shot, _raws = tree
    (final / "2026-01-28_10-29-15_DSCF0430.JPG").write_bytes(b"jpg")
    card_raw = shot / "DSCF0430.RAF"
    card_raw.write_bytes(b"raf")

    stats = _finalize()

    assert card_raw.exists(), "deleted the only copy of an unimported RAW"
    assert stats['deleted_camera_raws'] == 0
    assert stats['unbacked_camera_raws'] == 1


def test_nothing_is_deleted_when_the_raw_drive_is_unmounted(tree, monkeypatch):
    """Cannot verify => delete nothing. An unmounted SSD is not evidence of a backup."""
    _staging, final, shot, _raws = tree
    monkeypatch.setattr(workflow_module, "RAWS_PATH", shot.parent.parent / "not-mounted")
    (final / "2026-01-28_10-29-15_DSCF0430.JPG").write_bytes(b"jpg")
    card_raw = shot / "DSCF0430.RAF"
    card_raw.write_bytes(b"raf")

    stats = _finalize()

    assert card_raw.exists()
    assert stats['deleted_camera_raws'] == 0


def test_mixed_card_deletes_only_the_verified_ones(tree):
    _staging, final, shot, raws = tree
    for base in ("DSCF0001", "DSCF0002", "DSCF0003"):
        (final / f"2026-01-28_10-29-15_{base}.JPG").write_bytes(b"jpg")
        (shot / f"{base}.RAF").write_bytes(b"raf")
    # Only the first two ever reached the RAW drive.
    (raws / "2026-01-28_10-29-15_DSCF0001.RAF").write_bytes(b"raf")
    (raws / "2026-01-28_10-29-15_DSCF0002.RAF").write_bytes(b"raf")

    stats = _finalize()

    assert not (shot / "DSCF0001.RAF").exists()
    assert not (shot / "DSCF0002.RAF").exists()
    assert (shot / "DSCF0003.RAF").exists()
    assert stats['deleted_camera_raws'] == 2
    assert stats['unbacked_camera_raws'] == 1


def test_a_raw_whose_jpg_never_reached_final_is_untouched(tree):
    """Out of step 2's scope entirely — it must not become collateral of the new guard."""
    _staging, _final, shot, raws = tree
    (raws / "2026-01-28_10-29-15_DSCF0430.RAF").write_bytes(b"raf")
    card_raw = shot / "DSCF0430.RAF"
    card_raw.write_bytes(b"raf")

    stats = _finalize()

    assert card_raw.exists()
    assert stats['deleted_camera_raws'] == 0
    assert stats['unbacked_camera_raws'] == 0


def test_dry_run_deletes_nothing_but_still_reports(tree):
    _staging, final, shot, raws = tree
    (final / "2026-01-28_10-29-15_DSCF0430.JPG").write_bytes(b"jpg")
    (raws / "2026-01-28_10-29-15_DSCF0430.RAF").write_bytes(b"raf")
    card_raw = shot / "DSCF0430.RAF"
    card_raw.write_bytes(b"raf")

    stats = _finalize(dry_run=True)

    assert card_raw.exists()
    assert stats['deleted_camera_raws'] == 1
