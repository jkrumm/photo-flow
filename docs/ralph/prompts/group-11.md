# Group 11: Operations UI — dry-run confirm modals + live SSE progress

## What You're Doing

Build the Operations experience: cards for each operation (import, finalize, cleanup, sync-gallery,
backup), each opening a **dry-run-preview confirm modal** (shows what would happen, from the
`dry_run=true` endpoint) before the real run, then a **live progress panel** consuming the SSE
stream (per-file progress, rclone throughput/ETA, scrolling log tail). This is the safety-critical
UX — destructive ops must never run without explicit confirmation.

---

## Research & Exploration First

1. Read `control_panel/PRD.md` → "Screens" #2 (Operations) and "Job manager + concurrency".
2. Read the Group 4/5 API: `POST /ops/{op}?dry_run=`, `POST /ops/{op}`, `GET /events/{job_id}`,
   `GET /jobs/{job_id}`, `GET /backup/availability`. Note the event types from Groups 2–4:
   `task`, `advance`, `log`, `event` with subtypes `file_done` / `transfer` / `phase` / `done`.
3. Research consuming SSE in React (`EventSource`, cleanup on unmount, reconnect) — a small
   `useJobEvents(jobId)` hook. Verify there isn't already a shared active-job store from Group 10.

---

## What to Implement

- **Operation cards**: one per op. `backup` card has a `source` selector (final/raws/videos/all).
- **Confirm modal**: on trigger, call `dry_run=true`, render the returned counts/plan in a Mantine
  modal ("Would move 42 photos, move 42 .photo-edit sidecars, delete 12 camera RAWs…"). Destructive
  ops (cleanup, finalize, import) get a clear warning style. Confirm → `POST /ops/{op}` (real) →
  receive `job_id`.
- **Live progress panel**: subscribe to `GET /events/{job_id}` via `useJobEvents`. Render: overall
  progress bar (`task`/`advance`), per-file ticker (`file_done`), rclone throughput/ETA
  (`transfer`), and a scrolling log tail (`log`). On `done`, show the result summary; refresh
  affected queries (status, analytics). Handle the 409 (a job already running) gracefully.
- **Shared active-job store** (Zustand): holds the current `job_id` + latest progress so the Group
  10 pipeline hero can animate the matching edge. Reconcile with whatever Group 10 stubbed.
- Aesthetic: same calm, professional, data-dense bar as the hero. Use Mantine + `VX` tokens.

---

## Validation

```bash
cd control_panel/web && npm run build && npm run lint
```
Strict TS + lint pass. Manually (with the API running, `npm run dev`): a `--dry-run`-style preview
appears before any real op; SSE progress streams live for a real (safe, e.g. dry-or-empty) op;
concurrent-op 409 is handled. Add component tests for the modal flow + `useJobEvents` reducer with
mocked events where practical.

---

## Commit

```
feat(web): operation cards with dry-run confirm modals and live SSE progress
```

---

## Done

Append notes, then:
```
RALPH_TASK_COMPLETE: Group 11
```
