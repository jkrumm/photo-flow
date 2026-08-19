"""
Shared test fixtures.

The job store writes to `~/.photoflow/index.db` by default — the developer's LIVE
index, holding thousands of real photo rows. Any test that enqueues a job would
otherwise append history rows to it. This autouse fixture redirects every test to a
throwaway database, so the live index is never touched by the suite.
"""
import pytest

from photo_flow.api import job_store


@pytest.fixture(autouse=True)
def isolated_job_store(tmp_path, monkeypatch):
    """Point the durable job store at a per-test database."""
    job_store.set_db_path(tmp_path / "jobs-index.db")
    yield
    job_store.set_db_path(None)
