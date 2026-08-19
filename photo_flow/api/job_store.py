"""
Durable job records for the control panel.

Why this exists
---------------
`JobManager` holds its jobs in a plain in-memory dict. That is fine while the process
lives, and worthless the moment it does not: a crash, a logout, or a `make reload`
mid-backup takes every record with it. A 22 GB backup that was 80 % through does not
fail — it simply stops existing, and `last_run.json` is left implying the op never ran.

This module is the second copy. Every job writes four short rows' worth of state to the
`jobs` table in `~/.photoflow/index.db` (schema v3): admitted, started, and terminal. On
the next startup `reconcile_interrupted()` finds anything still marked `queued`/`running`
— nothing can legitimately be in that state in a fresh process — and marks it
`interrupted`, which is what the panel then shows instead of nothing at all.

Contract
--------
- **Every write is best-effort.** Each entry point swallows and DEBUG-logs its own
  failure. A jobs-table problem must never break the queue or a running photo
  operation; the durable record is a convenience, the file operations are not.
- **Events are NOT persisted.** The per-job SSE log stays in memory: it is large, it is
  only useful while someone is watching, and its terminal summary is in `result`.
- **Writes happen on the event-loop thread.** They are three tiny statements per job, but
  the connection is opened with a short lock timeout so a concurrent indexer
  transaction can never stall the loop for sqlite3's default five seconds.
- **Ordering is by rowid**, i.e. insertion order. `JobManager._seq` restarts at 0 in
  every new process, so it orders within a run but not across restarts.

`announced` is the notification-coverage flag: 0 until the SPA has surfaced the job's
terminal state in the notification history. A job that finishes while the panel is shut
therefore still produces exactly one entry the next time it opens.
"""
from __future__ import annotations

import json
import logging
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence

from photo_flow.index.db import get_db

_log = logging.getLogger(__name__)

# Lock-wait budget for a write from the event-loop thread. Short on purpose — see
# the module docstring. A busy database loses the record, not the job.
_LOCK_TIMEOUT_S = 2.0

# Rows retained in the jobs table. Generous: a row is a few hundred bytes and this
# is the only surviving evidence that an op ever ran.
_MAX_HISTORY_ROWS = 500

# Statuses that cannot legitimately survive a process boundary.
_NON_TERMINAL = ("queued", "running")

# Test seam: when set, every helper here opens this database instead of the default.
_db_path_override: Optional[Path] = None


def set_db_path(path: Optional[Path]) -> None:
    """Point the job store at a specific database file (tests only)."""
    global _db_path_override
    _db_path_override = path


def _connect() -> sqlite3.Connection:
    return get_db(_db_path_override, timeout=_LOCK_TIMEOUT_S)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _row_to_dict(row: sqlite3.Row) -> Dict[str, Any]:
    """Map a `jobs` row to the API shape, decoding the JSON `result` blob."""
    result: Optional[Dict[str, Any]] = None
    raw = row["result"]
    if raw:
        try:
            decoded = json.loads(raw)
            if isinstance(decoded, dict):
                result = decoded
        except Exception:
            result = None
    return {
        "job_id": row["job_id"],
        "op": row["op"],
        "status": row["status"],
        "queued_at": row["queued_at"],
        "started_at": row["started_at"],
        "finished_at": row["finished_at"],
        "result": result,
        "error": row["error"],
        "announced": bool(row["announced"]),
    }


# ---------------------------------------------------------------------------
# Writes (best-effort — never raise into the job path)
# ---------------------------------------------------------------------------


def record_queued(job_id: str, op: str, seq: int) -> None:
    """Insert the row for a newly admitted job."""
    try:
        conn = _connect()
        try:
            conn.execute(
                "INSERT OR REPLACE INTO jobs (job_id, op, status, seq, queued_at) "
                "VALUES (?, ?, 'queued', ?, ?)",
                (job_id, op, seq, _now()),
            )
            conn.commit()
        finally:
            conn.close()
    except Exception as exc:
        _log.debug("job_store.record_queued(%s): skipped (%s)", job_id, exc)


def record_started(job_id: str) -> None:
    """Mark a job as running and stamp `started_at`."""
    try:
        conn = _connect()
        try:
            conn.execute(
                "UPDATE jobs SET status = 'running', started_at = ? WHERE job_id = ?",
                (_now(), job_id),
            )
            conn.commit()
        finally:
            conn.close()
    except Exception as exc:
        _log.debug("job_store.record_started(%s): skipped (%s)", job_id, exc)


def record_terminal(
    job_id: str,
    status: str,
    result: Optional[Dict[str, Any]] = None,
    error: Optional[str] = None,
) -> None:
    """Write the job's final state and prune the table back to `_MAX_HISTORY_ROWS`.

    `needs_confirm` is written here too: it is a park, not a completion, but it is the
    last thing that happened to the job and the panel has to be able to see it.
    """
    try:
        payload = json.dumps(result, default=str) if result is not None else None
        conn = _connect()
        try:
            conn.execute(
                "UPDATE jobs SET status = ?, finished_at = ?, result = ?, error = ? "
                "WHERE job_id = ?",
                (status, _now(), payload, error, job_id),
            )
            conn.execute(
                "DELETE FROM jobs WHERE rowid NOT IN "
                "(SELECT rowid FROM jobs ORDER BY rowid DESC LIMIT ?)",
                (_MAX_HISTORY_ROWS,),
            )
            conn.commit()
        finally:
            conn.close()
    except Exception as exc:
        _log.debug("job_store.record_terminal(%s): skipped (%s)", job_id, exc)


def mark_announced(job_ids: Sequence[str]) -> int:
    """Flag jobs whose terminal state the UI has surfaced. Returns rows updated."""
    ids = [j for j in job_ids if j]
    if not ids:
        return 0
    try:
        conn = _connect()
        try:
            placeholders = ",".join("?" for _ in ids)
            cur = conn.execute(
                f"UPDATE jobs SET announced = 1 WHERE job_id IN ({placeholders})",
                tuple(ids),
            )
            conn.commit()
            return cur.rowcount or 0
        finally:
            conn.close()
    except Exception as exc:
        _log.debug("job_store.mark_announced: skipped (%s)", exc)
        return 0


# ---------------------------------------------------------------------------
# Startup reconciliation
# ---------------------------------------------------------------------------


def reconcile_interrupted() -> List[Dict[str, Any]]:
    """Mark every job left mid-flight by a dead process as `interrupted`.

    A fresh process owns no jobs, so any row still `queued` or `running` is by
    definition the residue of a restart. Returns the affected records (already
    updated) so the caller can surface them — `queued` and `running` are kept
    distinguishable through the `error` text, because only the latter actually
    touched the filesystem.

    Returns an empty list on any failure: an unreadable database must not stop the
    server from coming up.
    """
    try:
        conn = _connect()
        try:
            rows = conn.execute(
                "SELECT * FROM jobs WHERE status IN (?, ?) ORDER BY rowid",
                _NON_TERMINAL,
            ).fetchall()
            if not rows:
                return []
            ts = _now()
            records: List[Dict[str, Any]] = []
            for row in rows:
                was_running = row["status"] == "running"
                error = (
                    "Interrupted — the server stopped while this job was running."
                    if was_running
                    else "Interrupted — the server stopped before this job started."
                )
                conn.execute(
                    "UPDATE jobs SET status = 'interrupted', finished_at = ?, error = ?, "
                    "announced = 0 WHERE job_id = ?",
                    (ts, error, row["job_id"]),
                )
                record = _row_to_dict(row)
                record.update(
                    {
                        "status": "interrupted",
                        "finished_at": ts,
                        "error": error,
                        "announced": False,
                        "was_running": was_running,
                    }
                )
                records.append(record)
            conn.commit()
            return records
        finally:
            conn.close()
    except Exception as exc:
        _log.warning("job_store.reconcile_interrupted: skipped (%s)", exc)
        return []


# ---------------------------------------------------------------------------
# Reads
# ---------------------------------------------------------------------------


def list_history(limit: int = 50, unannounced_only: bool = False) -> List[Dict[str, Any]]:
    """Return persisted job records, newest first.

    Ordered by rowid (insertion order) rather than `seq`, which restarts at 0 in each
    new process. `unannounced_only` narrows to terminal jobs the UI has not yet
    surfaced — the query that gives the notification bell its coverage of jobs that
    finished while the panel was shut.
    """
    try:
        conn = _connect()
        try:
            if unannounced_only:
                sql = (
                    "SELECT * FROM jobs WHERE announced = 0 AND status NOT IN (?, ?) "
                    "ORDER BY rowid DESC LIMIT ?"
                )
                params: tuple = (*_NON_TERMINAL, limit)
            else:
                sql = "SELECT * FROM jobs ORDER BY rowid DESC LIMIT ?"
                params = (limit,)
            return [_row_to_dict(r) for r in conn.execute(sql, params)]
        finally:
            conn.close()
    except Exception as exc:
        _log.debug("job_store.list_history: skipped (%s)", exc)
        return []
