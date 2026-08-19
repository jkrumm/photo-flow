"""
A trashed photo's RAW must survive as long as the photo can still be restored.

`trash_photos` deliberately leaves the `.RAF` alone, and the trash promises a 30-day
restore window. But `compute_raw_keep_bases` used to build its keep-set from Final ∪
Staging only — the trash lives beside both, not inside either — so a culled photo's RAW
counted as orphaned the instant it was trashed. `finalize_staging` step 4 then unlinks
orphaned RAWs with **no preview and no confirmation**, so the first routine
`photoflow finalize` after a culling session would have destroyed the RAW of every photo
still sitting in the trash. `trash restore` would hand back the JPG; the irreplaceable
original would be gone.

These tests pin the union (Final ∪ Staging ∪ trash) at both orphan call sites, and pin
the other half of the contract too: once an entry is *purged*, its RAW is an orphan again.
"""
import pytest

from photo_flow import trash as trash_module
from photo_flow import workflow as workflow_module
from photo_flow.index.db import get_db
from photo_flow.progress import NullReporter
from photo_flow.workflow import PhotoWorkflow, compute_raw_keep_bases


@pytest.fixture
def env(tmp_path, monkeypatch):
    """Relocate every root the orphan logic reads, plus the index DB the trash records into."""
    staging = tmp_path / "Staging"
    final = tmp_path / "Final"
    raws = tmp_path / "RAWs"
    ssd = tmp_path / "SSD"
    trash = tmp_path / ".photoflow-trash"
    for directory in (staging, final, raws, ssd):
        directory.mkdir()

    monkeypatch.setattr(workflow_module, "STAGING_PATH", staging)
    monkeypatch.setattr(workflow_module, "FINAL_PATH", final)
    monkeypatch.setattr(workflow_module, "RAWS_PATH", raws)
    monkeypatch.setattr(workflow_module, "SSD_PATH", ssd)
    monkeypatch.setattr(workflow_module, "TRASH_PATH", trash)
    # Absent → finalize's camera-RAW step is skipped entirely.
    monkeypatch.setattr(workflow_module, "CAMERA_PATH", tmp_path / "no-camera")

    # photo_flow.trash reads config at call time.
    monkeypatch.setattr(
        trash_module.config, "CULL_ROOTS", {"final": final, "staging": staging}
    )
    monkeypatch.setattr(trash_module.config, "TRASH_PATH", trash)

    conn = get_db(tmp_path / "index.db")
    yield {"staging": staging, "final": final, "raws": raws, "trash": trash, "conn": conn}
    conn.close()


def _write(path, content: bytes = b"content"):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(content)
    return path


def _finalize():
    return PhotoWorkflow().finalize_staging(dry_run=False, reporter=NullReporter())


def _cleanup():
    return PhotoWorkflow().cleanup_unused_raws(dry_run=False, reporter=NullReporter())


class TestTrashedPhotosKeepTheirRaws:
    def test_finalize_does_not_delete_the_raw_of_a_trashed_final_jpg(self, env):
        jpg = _write(env["final"] / "2026-03-03_17-36-33_DSCF0770.JPG")
        raf = _write(env["raws"] / "2026-03-03_17-36-33_DSCF0770.RAF", b"raw")
        # An unrelated photo awaiting finalize, so step 4 actually runs (an empty Staging
        # returns early). Its own RAW is kept via the Staging half of the keep-set.
        _write(env["staging"] / "2026-03-04_09-00-00_DSCF0900.JPG")
        _write(env["raws"] / "2026-03-04_09-00-00_DSCF0900.RAF", b"raw")

        result = trash_module.trash_photos([jpg], conn=env["conn"])
        assert result["trashed"] == 1 and result["errors"] == 0
        assert not jpg.exists(), "precondition: the JPG has left Final"

        stats = _finalize()

        assert stats['orphaned_raws'] == 0
        assert stats['deleted_raws'] == 0
        assert raf.exists(), "the RAW of a still-restorable photo must survive finalize"

    def test_cleanup_does_not_delete_the_raw_of_a_trashed_staging_jpg(self, env):
        jpg = _write(env["staging"] / "2026-03-03_17-36-33_DSCF0771.JPG")
        raf = _write(env["raws"] / "2026-03-03_17-36-33_DSCF0771.RAF", b"raw")

        trash_module.trash_photos([jpg], conn=env["conn"])
        stats = _cleanup()

        assert stats['orphaned'] == 0
        assert raf.exists()

    def test_photomator_duplicate_suffix_still_correlates_from_the_trash(self, env):
        """`DSCF0770_2.jpg` in the trash must protect `DSCF0770.RAF`, as it does in Final."""
        jpg = _write(env["final"] / "2026-03-03_17-36-33_DSCF0770_2.jpg")
        raf = _write(env["raws"] / "2026-03-03_17-36-33_DSCF0770.RAF", b"raw")

        trash_module.trash_photos([jpg], conn=env["conn"])
        stats = _cleanup()

        assert stats['orphaned'] == 0
        assert raf.exists()

    def test_raw_becomes_an_orphan_once_the_entry_is_purged(self, env):
        """The keep-set must not grow forever: purge ends the restore window, and the RAW with it."""
        jpg = _write(env["final"] / "2026-03-03_17-36-33_DSCF0772.JPG")
        raf = _write(env["raws"] / "2026-03-03_17-36-33_DSCF0772.RAF", b"raw")

        trash_module.trash_photos([jpg], conn=env["conn"])
        purged = trash_module.purge(older_than_days=0, conn=env["conn"])
        assert purged["purged"] == 1

        stats = _cleanup()

        assert stats['orphaned'] == 1
        assert stats['deleted'] == 1
        assert not raf.exists()


class TestKeepBases:
    def test_union_covers_all_three_roots(self, env):
        _write(env["final"] / "2026-01-01_10-00-00_DSCF0001.JPG")
        _write(env["staging"] / "2026-01-01_10-00-01_DSCF0002.JPG")
        _write(env["trash"] / "20260101T100002-abcd1234" / "2026-01-01_10-00-02_DSCF0003.JPG")

        assert compute_raw_keep_bases() == {"DSCF0001", "DSCF0002", "DSCF0003"}

    def test_missing_trash_directory_is_not_an_error(self, env):
        _write(env["final"] / "2026-01-01_10-00-00_DSCF0001.JPG")

        assert not env["trash"].exists()
        assert compute_raw_keep_bases() == {"DSCF0001"}

    def test_appledouble_fork_in_the_trash_is_ignored(self, env):
        _write(env["trash"] / "20260101T100002-abcd1234" / "._2026-01-01_10-00-02_DSCF0003.JPG")

        assert compute_raw_keep_bases() == set()
