"""
get_backup_availability: sidecar-aware freshness counting.

A Photomator re-edit of an already-backed-up photo changes only the .photo-edit sidecar.
A JPG-only count reports that as "Synced" while the homelab still holds stale edit
history, so needs_sync has to include the sidecar gap.
"""
import pytest

from photo_flow import workflow as workflow_module
from photo_flow.workflow import PhotoWorkflow


@pytest.fixture
def local_library(tmp_path, monkeypatch):
    """Final with 3 JPGs + 2 sidecars; the other sources absent."""
    final = tmp_path / "Final"
    final.mkdir()
    for i in range(3):
        (final / f"photo{i}.JPG").write_bytes(b"jpg")
    for i in range(2):
        (final / f"photo{i}.photo-edit").write_bytes(b"edit")

    monkeypatch.setattr(workflow_module, "FINAL_PATH", final)
    monkeypatch.setattr(workflow_module, "STAGING_PATH", tmp_path / "Staging")
    monkeypatch.setattr(workflow_module, "RAWS_PATH", tmp_path / "no-raws")
    monkeypatch.setattr(workflow_module, "SSD_PATH", tmp_path / "no-ssd")
    monkeypatch.setattr(PhotoWorkflow, "_check_homelab_reachable", lambda self: "tailscale")
    return final


def _with_remote_counts(monkeypatch, counts: dict):
    """Stub the SSH count probe: {glob pattern: remote count}."""
    monkeypatch.setattr(
        PhotoWorkflow,
        "_get_remote_file_count",
        lambda self, remote_path, extension: (counts.get(extension, 0), "tailscale"),
    )


class TestSidecarCounting:
    def test_local_counts_are_split_by_kind(self, local_library):
        avail = PhotoWorkflow().get_backup_availability()
        assert avail['final']['local_count'] == 3
        assert avail['final']['sidecar_local_count'] == 2

    def test_sidecar_gap_alone_marks_the_source_stale(self, local_library, monkeypatch):
        """All JPGs are on the remote, one sidecar is not — must not read as Synced."""
        _with_remote_counts(monkeypatch, {'*.JPG': 3, '*.photo-edit': 1})

        final = PhotoWorkflow().get_backup_availability(check_remote=True)['final']

        assert final['sidecar_needs_sync'] == 1
        assert final['needs_sync'] == 1

    def test_fully_synced_reports_zero(self, local_library, monkeypatch):
        _with_remote_counts(monkeypatch, {'*.JPG': 3, '*.photo-edit': 2})

        final = PhotoWorkflow().get_backup_availability(check_remote=True)['final']

        assert final['needs_sync'] == 0

    def test_jpg_and_sidecar_gaps_add_up(self, local_library, monkeypatch):
        _with_remote_counts(monkeypatch, {'*.JPG': 1, '*.photo-edit': 0})

        final = PhotoWorkflow().get_backup_availability(check_remote=True)['final']

        assert final['needs_sync'] == 4  # 2 JPGs + 2 sidecars

    def test_sources_without_sidecars_are_untouched(self, local_library, monkeypatch):
        _with_remote_counts(monkeypatch, {'*.JPG': 3, '*.photo-edit': 2})

        avail = PhotoWorkflow().get_backup_availability(check_remote=True)

        assert 'sidecar_needs_sync' not in avail['raws']
        assert 'sidecar_needs_sync' not in avail['videos']

    def test_unreachable_remote_yields_unknown_not_zero(self, local_library, monkeypatch):
        monkeypatch.setattr(
            PhotoWorkflow,
            "_get_remote_file_count",
            lambda self, remote_path, extension: (-1, "connection failed"),
        )

        final = PhotoWorkflow().get_backup_availability(check_remote=True)['final']

        assert final['needs_sync'] == -1
        assert final['sidecar_needs_sync'] == -1


class TestStagingSource:
    def test_staging_is_present_and_flagged_optional(self, local_library):
        staging = PhotoWorkflow().get_backup_availability()['staging']
        assert staging['optional'] is True
        assert staging['remote_path'] == workflow_module.HOMELAB_SSD_STAGING_PATH
