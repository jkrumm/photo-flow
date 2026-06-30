import { useEffect, useRef } from 'react'
import { useQuery, useQueryClient } from '@tanstack/react-query'
import { useActiveJobStore, useJobLiveStore, useUiStore } from '../../lib/store'
import { jobsQueries } from '../../lib/queries/jobs'
import type { ActiveOp } from '../../lib/store'
import { playSuccess, playError } from '../../lib/sound'
import { notifyJobDone } from '../../lib/notify'

type RawSseEvent = Record<string, unknown> & { type: string }
type ManagerEvent = { type: string; job_id?: string; op?: string; status?: string }

/**
 * Headless app-level controller — renders null, mounted once in __root.tsx.
 *
 * Owns TWO persistent EventSource connections:
 *   1. `/events/{job_id}` — per-job progress stream (opened when a job becomes active).
 *      Writes to useJobLiveStore so any route can read live progress.
 *   2. `/jobs/stream` — manager-level lifecycle stream (always open).
 *      On job_started it sets the active job; on any lifecycle event it invalidates
 *      the ['jobs','list'] query to keep the queue panel live.
 *
 * On the terminal `done` event the per-job stream fires the completion seam:
 *   setLastResult → sound → OS notification → clearActiveJob → cache invalidation.
 *
 * Also handles reload re-attach: queries /jobs/active on mount and restores
 * activeJobId when the server still has a running job (the job outlives the tab).
 *
 * Live-session guard: tracks which job IDs we've seen as active in this session.
 * Replayed `done` events for jobs that finished before this mount are silently
 * dropped (no chime/notification); a genuinely observed completion does chime.
 */
export function JobController(): null {
  const queryClient = useQueryClient()
  const activeJobId = useActiveJobStore((s) => s.activeJobId)

  // ── Reload re-attach ──────────────────────────────────────────────────────
  // GET /jobs/active returns the currently running job or null. On a fresh page
  // load the zustand store is empty; if the server reports an in-flight job we
  // re-attach so the SSE stream replays history and progress resumes.
  const { data: activeJob } = useQuery(jobsQueries.active())
  useEffect(() => {
    if (activeJobId !== null) return
    const a = activeJob?.active
    if (!a) return
    useActiveJobStore.getState().setActiveJob(a.job_id, a.op.split(':')[0] as ActiveOp)
  }, [activeJob, activeJobId])

  // ── Live-session guard ────────────────────────────────────────────────────
  // A Set of job IDs observed as activeJobId !== null in this browser session.
  // Guards against chiming for a `done` event replayed for a job that finished
  // before mount (stale SSE history). Jobs we re-attach to mid-run ARE real
  // completions and will chime (the ID is added once activeJobId becomes set).
  const seenJobIdsRef = useRef(new Set<string>())
  useEffect(() => {
    if (activeJobId !== null) seenJobIdsRef.current.add(activeJobId)
  }, [activeJobId])

  // ── Manager-level lifecycle stream (always open) ──────────────────────────
  // Subscribes to /jobs/stream once on mount. On job_started it promotes the
  // queued job to active so the progress panel and per-job SSE kick in.
  // On every lifecycle event it invalidates the queue list query.
  useEffect(() => {
    const base = (import.meta.env.VITE_API_URL as string | undefined) ?? ''
    const es = new EventSource(`${base}/jobs/stream`)

    es.addEventListener('message', (e: MessageEvent<string>) => {
      try {
        const event = JSON.parse(e.data) as ManagerEvent

        if (event.type === 'job_started' && event.job_id && event.op) {
          const op = event.op.split(':')[0] as ActiveOp
          // Promote the job to active only if nothing else is active.
          // (The reload re-attach path also calls setActiveJob, so guard
          // against double-setting with the same jobId — it is idempotent.)
          useActiveJobStore.getState().setActiveJob(event.job_id, op)
        }

        // Invalidate queue list on every lifecycle event so the queue panel
        // stays live without depending solely on SSE delivery.
        void queryClient.invalidateQueries({ queryKey: ['jobs', 'list'] })
        void queryClient.invalidateQueries({ queryKey: ['jobs', 'active'] })
      } catch {
        // ignore malformed frames
      }
    })

    // SSE auto-reconnects on error; just log and let the browser handle it.
    es.addEventListener('error', () => {
      // no-op: browser retries automatically
    })

    return () => {
      es.close()
    }
  }, [queryClient])

  // ── Single per-job EventSource ────────────────────────────────────────────
  useEffect(() => {
    if (activeJobId === null) return
    const capturedJobId = activeJobId

    useJobLiveStore.getState().reset()

    const base = (import.meta.env.VITE_API_URL as string | undefined) ?? ''
    const es = new EventSource(`${base}/events/${capturedJobId}`)

    es.addEventListener('message', (e: MessageEvent<string>) => {
      try {
        const event = JSON.parse(e.data) as RawSseEvent
        useJobLiveStore.getState().applyEvent(event)

        if (event.type === 'done') {
          es.close()

          // Read op + live state before clearing so nothing races.
          const { activeOp, setLastResult, clearActiveJob } = useActiveJobStore.getState()
          const live = useJobLiveStore.getState()

          // 1. Persist the terminal result so the summary stays after clearActiveJob.
          if (activeOp !== null) {
            setLastResult(activeOp, live.result, live.error)
          }

          // 2. Sound + OS notification — only for completions observed live.
          if (activeOp !== null && seenJobIdsRef.current.has(capturedJobId)) {
            if (useUiStore.getState().soundEnabled) {
              if (live.error !== null) playError()
              else playSuccess()
            }
            notifyJobDone(activeOp, live.result, live.error)
          }
          seenJobIdsRef.current.delete(capturedJobId)

          // 3. Clear + invalidate caches.
          clearActiveJob()
          // Zero out the active-job cache immediately so the re-attach effect
          // can't resurrect the just-finished job from a stale poll response.
          queryClient.setQueryData(['jobs', 'active'], { active: null })
          void queryClient.invalidateQueries({ queryKey: ['jobs', 'active'] })
          void queryClient.invalidateQueries({ queryKey: ['jobs', 'list'] })
          void queryClient.invalidateQueries({ queryKey: ['status'] })
          void queryClient.invalidateQueries({ queryKey: ['analytics', 'summary'] })
          void queryClient.invalidateQueries({ queryKey: ['backup', 'availability'] })
        }
      } catch {
        // ignore malformed SSE frames
      }
    })

    es.addEventListener('error', () => {
      es.close()
    })

    return () => {
      es.close()
    }
  }, [activeJobId, queryClient])

  return null
}
