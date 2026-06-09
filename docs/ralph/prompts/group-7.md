# Group 7: Analytics API — aggregation endpoints + index refresh hooks

## What You're Doing

Expose the index through analytics endpoints the SPA charts will consume, add a refresh endpoint +
auto-refresh after finalize/sync jobs, and a library-health endpoint. Pure read aggregations over
SQLite (fast).

---

## Research & Exploration First

1. Read `control_panel/PRD.md` → "Screens (v1)" #3 Analytics and #4 Library health.
2. Read Group 6's `photo_flow/index/` modules (schema + `reindex()`).
3. Read `photo_flow/workflow.py` `get_backup_availability()` (local vs remote counts) and
   `cleanup_unused_raws` (orphaned-RAW detection) — the health endpoint surfaces these.

---

## What to Implement

### `photo_flow/api/routes_analytics.py`

SQL aggregations (return JSON shaped for charts — arrays of `{x, y, …}`):
- `GET /analytics/over-time?bucket=day|week|month|year` → counts per bucket, split by rating band
  (e.g. `<4` vs `>=4`) for an overlay.
- `GET /analytics/ratings` → histogram of rating 0–5, plus Final-total vs published counts.
- `GET /analytics/settings` → distributions for `iso`, `aperture_f`, `focal_mm`, `shutter_s`
  (bucketed/grouped counts).
- `GET /analytics/storage` → total bytes + file counts per stage (Final from index; Staging via a
  quick glob; RAWs/Videos sizes if cheaply available).
- `GET /analytics/map` → `[{lat, lng, count|filename, date_taken}]` for photos with GPS.
- `GET /analytics/summary` → headline tiles (total photos, total published, this-month count, avg
  rating, date range).

### Refresh

- `POST /index/refresh` → runs `reindex()` (in a thread; reuse the job manager or a simple guarded
  task), returns counts.
- After a successful **finalize** or **sync-gallery** job (Group 5), trigger an index refresh so
  analytics stay current. Wire this in the job-completion path.

---

## Validation

```bash
venv/bin/python -m pytest -q
```
Tests (`tests/test_api_analytics.py`): seed a temp index DB with known rows, assert each endpoint's
aggregation (bucketing, rating bands, settings distributions, summary tiles) is correct. Assert
`/index/refresh` returns counts. Use a temp DB path (monkeypatch the index location) — never the
real `~/.photoflow/index.db`.

---

## Commit

```
feat(api): analytics aggregation endpoints + index refresh hooks
```

---

## Done

Append notes, then:
```
RALPH_TASK_COMPLETE: Group 7
```
