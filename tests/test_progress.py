"""
Tests for the ProgressReporter seam and its wiring in import_from_camera / finalize_staging.

FakeReporter records every call so we can assert the workflow drives the reporter correctly.
All tests use dry_run=True and/or temp dirs — no real camera/Staging paths are touched.
"""
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

    def task(self, desc: str, total: int) -> None:
        self.calls.append(("task", desc, total))

    def advance(self, n: int = 1) -> None:
        self.calls.append(("advance", n))

    def log(self, level: str, message: str) -> None:
        self.calls.append(("log", level, message))

    def event(self, type: str, payload: dict) -> None:
        self.calls.append(("event", type, payload))

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
