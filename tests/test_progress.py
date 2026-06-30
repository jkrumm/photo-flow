"""
Tests for the ProgressReporter seam and its wiring across all PhotoWorkflow methods.

FakeReporter records every call so we can assert the workflow drives the reporter correctly.
All tests use dry_run=True and/or temp dirs — no real camera/Staging paths are touched.
"""
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest

from photo_flow.progress import ProgressReporter, RichReporter, NullReporter
from photo_flow.workflow import PhotoWorkflow


# ---------------------------------------------------------------------------
# FakeReporter
# ---------------------------------------------------------------------------

class FakeReporter:
    """Records all reporter calls for assertion in tests."""

    def __init__(self):
        self.calls: list = []
        self.cancelled = False  # set True in a test to simulate a Stop request

    def task(self, desc: str, total: int) -> None:
        self.calls.append(("task", desc, total))

    def advance(self, n: int = 1) -> None:
        self.calls.append(("advance", n))

    def log(self, level: str, message: str) -> None:
        self.calls.append(("log", level, message))

    def event(self, type: str, payload: dict) -> None:
        self.calls.append(("event", type, payload))

    def is_cancelled(self) -> bool:
        return self.cancelled

    def __enter__(self):
        return self

    def __exit__(self, *args):
        pass

    # --- helpers ---

    def _by_kind(self, kind: str) -> list:
        return [c for c in self.calls if c[0] == kind]

    def task_calls(self) -> list:
        return self._by_kind("task")

    def advance_calls(self) -> list:
        return self._by_kind("advance")

    def event_calls(self) -> list:
        return self._by_kind("event")

    def log_calls(self) -> list:
        return self._by_kind("log")


# ---------------------------------------------------------------------------
# Protocol conformance
# ---------------------------------------------------------------------------

def test_rich_reporter_satisfies_protocol():
    assert isinstance(RichReporter(), ProgressReporter)


def test_null_reporter_satisfies_protocol():
    assert isinstance(NullReporter(), ProgressReporter)


def test_fake_reporter_satisfies_protocol():
    assert isinstance(FakeReporter(), ProgressReporter)


def test_null_reporter_no_exceptions():
    with NullReporter() as r:
        r.task("Test", 10)
        r.advance()
        r.advance(3)
        r.log("info", "hello")
        r.log("error", "oops")
        r.event("file_done", {"filename": "test.jpg", "action": "moved"})


# ---------------------------------------------------------------------------
# import_from_camera
# ---------------------------------------------------------------------------

def test_import_from_camera_drives_reporter(tmp_path):
    """Reporter receives task(total=1), advance×1, event("file_done")×1 for one JPG dry-run."""
    camera_dir = tmp_path / "camera"
    camera_dir.mkdir()
    staging_dir = tmp_path / "staging"
    staging_dir.mkdir()

    fake_jpg = camera_dir / "DSCF0001.JPG"
    fake_jpg.touch()

    reporter = FakeReporter()
    workflow = PhotoWorkflow()

    with (
        patch("photo_flow.workflow.CAMERA_PATH", camera_dir),
        patch("photo_flow.workflow.STAGING_PATH", staging_dir),
        patch("photo_flow.workflow.SSD_PATH", tmp_path / "ssd"),   # doesn't exist → skip MOV/RAF
        patch("photo_flow.workflow.RAWS_PATH", tmp_path / "raws"),
        patch.object(
            workflow.file_manager,
            "scan_camera_files",
            return_value={".JPG": [fake_jpg], ".RAF": [], ".MOV": []},
        ),
        patch(
            "photo_flow.workflow.generate_timestamped_filename",
            return_value=("DSCF0001.JPG", ""),
        ),
    ):
        stats = workflow.import_from_camera(dry_run=True, reporter=reporter)

    assert stats["photos"] == 1
    assert stats["errors"] == 0

    task_calls = reporter.task_calls()
    assert len(task_calls) == 1
    assert task_calls[0][2] == 1  # total = 1 file

    advance_calls = reporter.advance_calls()
    assert len(advance_calls) == 1

    event_calls = reporter.event_calls()
    assert len(event_calls) == 1
    assert event_calls[0][1] == "file_done"
    assert event_calls[0][2]["filename"] == "DSCF0001.JPG"
    assert event_calls[0][2]["type"] == "photo"


def test_import_from_camera_multi_file(tmp_path):
    """advance() is called once per file; event() once per file."""
    camera_dir = tmp_path / "camera"
    camera_dir.mkdir()
    staging_dir = tmp_path / "staging"
    staging_dir.mkdir()

    fake_files = [camera_dir / f"DSCF000{i}.JPG" for i in range(4)]
    for f in fake_files:
        f.touch()

    reporter = FakeReporter()
    workflow = PhotoWorkflow()

    ts_names = [f"2026-01-01_10-00-0{i}_DSCF000{i}.JPG" for i in range(4)]
    call_counter = {"n": 0}

    def fake_ts(file_path, existing_names):
        name = ts_names[call_counter["n"]]
        call_counter["n"] += 1
        return name, ""

    with (
        patch("photo_flow.workflow.CAMERA_PATH", camera_dir),
        patch("photo_flow.workflow.STAGING_PATH", staging_dir),
        patch("photo_flow.workflow.SSD_PATH", tmp_path / "ssd"),
        patch("photo_flow.workflow.RAWS_PATH", tmp_path / "raws"),
        patch.object(
            workflow.file_manager,
            "scan_camera_files",
            return_value={".JPG": fake_files, ".RAF": [], ".MOV": []},
        ),
        patch("photo_flow.workflow.generate_timestamped_filename", side_effect=fake_ts),
    ):
        stats = workflow.import_from_camera(dry_run=True, reporter=reporter)

    assert stats["photos"] == 4
    assert len(reporter.task_calls()) == 1
    assert reporter.task_calls()[0][2] == 4  # total
    assert len(reporter.advance_calls()) == 4
    assert len(reporter.event_calls()) == 4
    assert all(c[1] == "file_done" for c in reporter.event_calls())


def test_import_from_camera_no_camera(tmp_path):
    """When camera is not connected, reporter receives no task/advance/event calls."""
    reporter = FakeReporter()
    workflow = PhotoWorkflow()

    with patch("photo_flow.workflow.CAMERA_PATH", tmp_path / "missing"):
        stats = workflow.import_from_camera(dry_run=True, reporter=reporter)

    assert stats["photos"] == 0
    assert reporter.task_calls() == []
    assert reporter.advance_calls() == []
    assert reporter.event_calls() == []


# ---------------------------------------------------------------------------
# finalize_staging
# ---------------------------------------------------------------------------

def test_finalize_staging_drives_reporter(tmp_path):
    """Reporter receives task(total=3), advance×3, event("file_done", action="moved")×3."""
    staging_dir = tmp_path / "staging"
    staging_dir.mkdir()
    final_dir = tmp_path / "final"  # intentionally not created

    for i in range(3):
        (staging_dir / f"DSCF000{i}.JPG").touch()

    reporter = FakeReporter()
    workflow = PhotoWorkflow()

    with (
        patch("photo_flow.workflow.STAGING_PATH", staging_dir),
        patch("photo_flow.workflow.FINAL_PATH", final_dir),
        patch("photo_flow.workflow.CAMERA_PATH", tmp_path / "camera"),  # absent → step 2 skipped
        patch("photo_flow.workflow.RAWS_PATH", tmp_path / "raws"),      # absent → step 4 skipped
        patch("photo_flow.workflow.SSD_PATH", tmp_path / "ssd"),
    ):
        stats = workflow.finalize_staging(dry_run=True, reporter=reporter)

    assert stats["moved"] == 3
    assert stats["errors"] == 0

    task_calls = reporter.task_calls()
    assert len(task_calls) == 1
    assert task_calls[0][2] == 3  # total = 3 files

    advance_calls = reporter.advance_calls()
    assert len(advance_calls) == 3

    event_calls = reporter.event_calls()
    assert len(event_calls) == 3
    assert all(c[1] == "file_done" for c in event_calls)
    assert all(c[2]["action"] == "moved" for c in event_calls)


def test_finalize_staging_empty_staging(tmp_path):
    """No staging files → no reporter calls."""
    staging_dir = tmp_path / "staging"
    staging_dir.mkdir()

    reporter = FakeReporter()
    workflow = PhotoWorkflow()

    with (
        patch("photo_flow.workflow.STAGING_PATH", staging_dir),
        patch("photo_flow.workflow.FINAL_PATH", tmp_path / "final"),
        patch("photo_flow.workflow.CAMERA_PATH", tmp_path / "camera"),
        patch("photo_flow.workflow.RAWS_PATH", tmp_path / "raws"),
        patch("photo_flow.workflow.SSD_PATH", tmp_path / "ssd"),
    ):
        stats = workflow.finalize_staging(dry_run=True, reporter=reporter)

    assert stats["moved"] == 0
    assert reporter.task_calls() == []
    assert reporter.advance_calls() == []
    assert reporter.event_calls() == []


def test_finalize_staging_skips_duplicates(tmp_path):
    """A file already in Final (duplicate) emits action='skipped', not 'moved'."""
    staging_dir = tmp_path / "staging"
    staging_dir.mkdir()
    final_dir = tmp_path / "final"
    final_dir.mkdir()

    # Place the same file in both staging and final (identical content → duplicate)
    content = b"fake jpg data"
    (staging_dir / "DSCF0001.JPG").write_bytes(content)
    (final_dir / "DSCF0001.JPG").write_bytes(content)

    reporter = FakeReporter()
    workflow = PhotoWorkflow()

    with (
        patch("photo_flow.workflow.STAGING_PATH", staging_dir),
        patch("photo_flow.workflow.FINAL_PATH", final_dir),
        patch("photo_flow.workflow.CAMERA_PATH", tmp_path / "camera"),
        patch("photo_flow.workflow.RAWS_PATH", tmp_path / "raws"),
        patch("photo_flow.workflow.SSD_PATH", tmp_path / "ssd"),
    ):
        stats = workflow.finalize_staging(dry_run=False, reporter=reporter)

    assert stats["skipped"] == 1
    assert stats["moved"] == 0

    event_calls = reporter.event_calls()
    assert len(event_calls) == 1
    assert event_calls[0][2]["action"] == "skipped"


def test_finalize_staging_no_staging_dir(tmp_path):
    """Missing staging dir → early return, no reporter calls."""
    reporter = FakeReporter()
    workflow = PhotoWorkflow()

    with patch("photo_flow.workflow.STAGING_PATH", tmp_path / "missing"):
        stats = workflow.finalize_staging(dry_run=True, reporter=reporter)

    assert reporter.task_calls() == []
    assert reporter.advance_calls() == []


def test_finalize_cancel_skips_raw_deletion(tmp_path):
    """When the reporter is already cancelled, finalize returns after Step 1 without
    deleting any RAW files — neither camera RAWs nor orphaned local RAWs."""
    staging_dir = tmp_path / "staging"
    staging_dir.mkdir()
    final_dir = tmp_path / "final"
    final_dir.mkdir()
    camera_dir = tmp_path / "camera" / "DCIM" / "100FUJI"
    camera_dir.mkdir(parents=True)
    raws_dir = tmp_path / "raws"
    raws_dir.mkdir()
    ssd_dir = tmp_path / "ssd"
    ssd_dir.mkdir()

    # Create a staging JPG (so the method enters the Step 1 loop).
    (staging_dir / "DSCF0001.JPG").touch()
    # Create a camera RAW and a local orphaned RAW — these must NOT be deleted.
    camera_raf = camera_dir / "DSCF0001.RAF"
    camera_raf.touch()
    orphan_raf = raws_dir / "DSCF9999.RAF"
    orphan_raf.touch()

    reporter = FakeReporter()
    reporter.cancelled = True  # simulate watchdog/Stop pressed before Step 1 begins

    workflow = PhotoWorkflow()

    with (
        patch("photo_flow.workflow.STAGING_PATH", staging_dir),
        patch("photo_flow.workflow.FINAL_PATH", final_dir),
        patch("photo_flow.workflow.CAMERA_PATH", camera_dir.parent.parent),
        patch("photo_flow.workflow.RAWS_PATH", raws_dir),
        patch("photo_flow.workflow.SSD_PATH", ssd_dir),
        patch.object(
            workflow.file_manager,
            "scan_camera_files",
            return_value={".RAF": [camera_raf], ".JPG": [], ".MOV": []},
        ),
    ):
        stats = workflow.finalize_staging(dry_run=False, reporter=reporter)

    # No RAWs must have been deleted.
    assert camera_raf.exists(), "Camera RAW must not be deleted after a cancelled finalize"
    assert orphan_raf.exists(), "Orphaned local RAW must not be deleted after a cancelled finalize"
    # Counts reflect no deletions.
    assert stats["deleted_camera_raws"] == 0
    assert stats["deleted_raws"] == 0


# ---------------------------------------------------------------------------
# _parse_rclone_line helper
# ---------------------------------------------------------------------------

def test_parse_rclone_line_transfer():
    """Speed/ETA line is parsed into pct, speed, eta keys."""
    from photo_flow.workflow import _parse_rclone_line
    line = "Transferred:   21.281 MiB / 40 MiB, 53%, 356.951 KiB/s, ETA 53s"
    result = _parse_rclone_line(line)
    assert result["pct"] == 53
    assert "KiB/s" in result["speed"]
    assert result["eta"] == "53s"
    assert "files_done" not in result


def test_parse_rclone_line_files():
    """File-count line is parsed into files_done and files_total keys."""
    from photo_flow.workflow import _parse_rclone_line
    line = "Transferred:         5 / 10, 50%"
    result = _parse_rclone_line(line)
    assert result["files_done"] == 5
    assert result["files_total"] == 10
    assert "pct" not in result


def test_parse_rclone_line_empty():
    """Non-matching line returns empty dict."""
    from photo_flow.workflow import _parse_rclone_line
    result = _parse_rclone_line("2026/06/09 10:00:00 INFO  : some other rclone log line")
    assert result == {}


# ---------------------------------------------------------------------------
# cleanup_unused_raws
# ---------------------------------------------------------------------------

def test_cleanup_unused_raws_orphans_progress(tmp_path):
    """Orphaned RAFs trigger task(total=3) + advance×3 calls on the reporter."""
    raws_dir = tmp_path / "raws"
    raws_dir.mkdir()
    final_dir = tmp_path / "final"
    final_dir.mkdir()
    ssd_dir = tmp_path / "ssd"
    ssd_dir.mkdir()

    for i in range(3):
        (raws_dir / f"DSCF000{i}.RAF").touch()
    # No matching JPGs in final → all three RAFs are orphans.

    reporter = FakeReporter()
    workflow = PhotoWorkflow()

    with (
        patch("photo_flow.workflow.RAWS_PATH", raws_dir),
        patch("photo_flow.workflow.FINAL_PATH", final_dir),
        patch("photo_flow.workflow.SSD_PATH", ssd_dir),
    ):
        stats = workflow.cleanup_unused_raws(dry_run=False, reporter=reporter)

    assert stats["orphaned"] == 3
    assert stats["deleted"] == 3
    assert stats["errors"] == 0

    task_calls = reporter.task_calls()
    assert len(task_calls) == 1
    assert task_calls[0][2] == 3  # total

    advance_calls = reporter.advance_calls()
    assert len(advance_calls) == 3


def test_cleanup_unused_raws_no_ssd(tmp_path):
    """Missing SSD → early return with a warning log, no task/advance calls."""
    reporter = FakeReporter()
    workflow = PhotoWorkflow()

    with patch("photo_flow.workflow.SSD_PATH", tmp_path / "missing_ssd"):
        stats = workflow.cleanup_unused_raws(dry_run=False, reporter=reporter)

    assert stats["orphaned"] == 0
    assert reporter.task_calls() == []
    assert reporter.advance_calls() == []
    assert any("SSD" in c[2] for c in reporter.log_calls())


def test_cleanup_unused_raws_dry_run(tmp_path):
    """dry_run=True: orphans are counted but no task/advance (no deletion loop)."""
    raws_dir = tmp_path / "raws"
    raws_dir.mkdir()
    final_dir = tmp_path / "final"
    final_dir.mkdir()
    ssd_dir = tmp_path / "ssd"
    ssd_dir.mkdir()

    for i in range(2):
        (raws_dir / f"DSCF000{i}.RAF").touch()

    reporter = FakeReporter()
    workflow = PhotoWorkflow()

    with (
        patch("photo_flow.workflow.RAWS_PATH", raws_dir),
        patch("photo_flow.workflow.FINAL_PATH", final_dir),
        patch("photo_flow.workflow.SSD_PATH", ssd_dir),
    ):
        stats = workflow.cleanup_unused_raws(dry_run=True, reporter=reporter)

    assert stats["orphaned"] == 2
    assert stats["deleted"] == 0
    # dry_run skips the deletion loop — no progress bar.
    assert reporter.task_calls() == []
    assert reporter.advance_calls() == []


# ---------------------------------------------------------------------------
# sync_gallery
# ---------------------------------------------------------------------------

def test_sync_gallery_dry_run_drives_reporter(tmp_path):
    """dry_run=True: reporter gets task(total=N) + advance×N for metadata extraction."""
    final_dir = tmp_path / "final"
    final_dir.mkdir()

    for i in range(3):
        (final_dir / f"DSCF000{i}.JPG").touch()

    reporter = FakeReporter()
    workflow = PhotoWorkflow()

    with (
        patch("photo_flow.workflow.FINAL_PATH", final_dir),
        patch("photo_flow.workflow.GALLERY_PATH", tmp_path / "gallery"),
        patch(
            "photo_flow.workflow.MetadataExtractor.extract_metadata",
            return_value={"rating": 4, "filename": "test.JPG"},
        ),
    ):
        stats = workflow.sync_gallery(dry_run=True, reporter=reporter)

    assert stats["scanned"] == 3

    task_calls = reporter.task_calls()
    # First task = metadata extraction (total=3).
    assert len(task_calls) >= 1
    assert task_calls[0][2] == 3

    # 3 advances for metadata + 3 advances for copying (all 3 are new since gallery is empty).
    advance_calls = reporter.advance_calls()
    assert len(advance_calls) == 6  # 3 metadata + 3 copy


def test_sync_gallery_no_final_dir(tmp_path):
    """Missing Final folder → early return with info log, no task calls."""
    reporter = FakeReporter()
    workflow = PhotoWorkflow()

    with patch("photo_flow.workflow.FINAL_PATH", tmp_path / "missing"):
        stats = workflow.sync_gallery(dry_run=True, reporter=reporter)

    assert stats["scanned"] == 0
    assert reporter.task_calls() == []
    assert any("Nothing to sync" in c[2] for c in reporter.log_calls())


# ---------------------------------------------------------------------------
# _run_backup_rclone emits "transfer" events
# ---------------------------------------------------------------------------

def test_run_backup_rclone_emits_transfer_events(tmp_path):
    """When rclone outputs a stats line, reporter.event('transfer', ...) is emitted."""
    source_dir = tmp_path / "source"
    source_dir.mkdir()
    (source_dir / "photo.JPG").touch()  # min_files=0 so this is just for the glob

    reporter = FakeReporter()
    workflow = PhotoWorkflow()

    # Simulate rclone stdout: one pct/speed/ETA line followed by stream end.
    rclone_line = "Transferred:   21.281 MiB / 40 MiB, 53%, 356.951 KiB/s, ETA 53s\n"
    mock_proc = MagicMock()
    mock_proc.stdout.readline.side_effect = [rclone_line, ""]
    mock_proc.returncode = 0
    mock_proc.wait = MagicMock()

    with (
        patch("shutil.which", return_value="/usr/bin/rclone"),
        patch("subprocess.Popen", return_value=mock_proc),
    ):
        stats = workflow._run_backup_rclone(
            source_path=source_dir,
            remote_dest=Path("/remote/final"),
            source_name="final",
            dry_run=False,
            min_files=0,
            reporter=reporter,
        )

    transfer_events = [c for c in reporter.event_calls() if c[1] == "transfer"]
    assert len(transfer_events) >= 1
    ev = transfer_events[0][2]
    assert ev["pct"] == 53
    assert "KiB/s" in ev["speed"]
    assert ev["eta"] == "53s"


def test_run_backup_rclone_honors_cancel(tmp_path):
    """A cancel request terminates the rclone subprocess and reports cancelled, not success."""
    source_dir = tmp_path / "source"
    source_dir.mkdir()
    (source_dir / "photo.JPG").touch()

    reporter = FakeReporter()
    reporter.cancelled = True  # user clicked Stop before the first stats line
    workflow = PhotoWorkflow()

    mock_proc = MagicMock()
    mock_proc.stdout.readline.side_effect = [
        "Transferred:   1 MiB / 40 MiB, 2%, 5 MiB/s, ETA 8s\n", "",
    ]
    mock_proc.returncode = -15  # SIGTERM

    with (
        patch("shutil.which", return_value="/usr/bin/rclone"),
        patch("subprocess.Popen", return_value=mock_proc),
    ):
        stats = workflow._run_backup_rclone(
            source_path=source_dir,
            remote_dest=Path("/remote/final"),
            source_name="final",
            dry_run=False,
            min_files=0,
            reporter=reporter,
        )

    mock_proc.terminate.assert_called_once()
    assert stats.get("cancelled") is True
    assert stats.get("sync_successful") is not True


def test_get_gallery_sync_status_symmetric_diff(tmp_path, monkeypatch):
    """pending is the symmetric diff of high-rated Final names vs gallery folder (catches swaps)."""
    from photo_flow import workflow as wfmod
    import photo_flow.index.db as dbmod

    gallery = tmp_path / "gallery"
    (gallery / "images").mkdir(parents=True)
    (gallery / "images" / "a.JPG").touch()  # published
    (gallery / "images" / "b.JPG").touch()  # published but rating since dropped → should be removed
    monkeypatch.setattr(wfmod, "GALLERY_PATH", gallery)

    class FakeConn:
        def execute(self, _sql):
            return [("/Final/a.JPG",), ("/Final/c.JPG",)]  # 4★ keepers: a (in gallery), c (missing)

        def close(self):
            pass

    monkeypatch.setattr(dbmod, "get_db", lambda *a, **k: FakeConn())

    status = PhotoWorkflow().get_gallery_sync_status()
    assert status["target"] == 2          # a, c
    assert status["current"] == 2         # a, b
    assert status["pending"] == 2         # {c to publish, b to remove}
    assert status["up_to_date"] is False


def test_prune_remote_trash_keeps_recent_purges_old(monkeypatch):
    """Retention keys off the folder-name timestamp (when trashed), NOT file mtime.

    Locks the bug we hit live: rclone preserves each RAW's original capture-time mtime, so an
    mtime-based sweep would delete a freshly-trashed folder of months-old photos immediately.
    """
    from photo_flow import workflow as wfmod

    now = datetime.now()
    old = (now - timedelta(days=40)).strftime("%Y-%m-%d_%H-%M")
    recent = (now - timedelta(days=5)).strftime("%Y-%m-%d_%H-%M")
    listing = f"raws_{old}/\nfinal_{old}/\nraws_{recent}/\nunrecognized_dir/\n"

    purged: list[str] = []

    def fake_run(cmd, **kwargs):
        m = MagicMock()
        m.returncode = 0
        if "lsf" in cmd:
            m.stdout = listing
        elif "purge" in cmd:
            purged.append(cmd[-1])
            m.stdout = ""
        else:
            m.stdout = ""
        return m

    monkeypatch.setattr(wfmod.subprocess, "run", fake_run)

    PhotoWorkflow()._prune_remote_trash(
        Path("/mnt/hdd/fuji/.trash"), retention_days=30, reporter=FakeReporter()
    )

    assert any(f"raws_{old}" in p for p in purged), "old folder must be purged"
    assert any(f"final_{old}" in p for p in purged), "old folder (any source prefix) must be purged"
    assert not any(recent in p for p in purged), "within-window folder must be kept"
    assert not any("unrecognized_dir" in p for p in purged), "undateable name must never be purged"
    assert len(purged) == 2
