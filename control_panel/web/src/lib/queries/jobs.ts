import { api } from '../api'
import type { JobListResponse } from '../api-types'

export type ActiveJobResponse = {
  active: { job_id: string; op: string; status: string } | null
}

export const jobsQueries = {
  /**
   * GET /jobs/active — the job currently running server-side, or null.
   *
   * Jobs run in the always-on service, so reloading or reopening the tab does not stop
   * them. The UI polls this on load / window-focus and re-attaches to the SSE stream
   * (which replays history) so progress resumes seamlessly.
   */
  active: () => ({
    queryKey: ['jobs', 'active'] as const,
    queryFn: () => api.get<ActiveJobResponse>('/jobs/active'),
    staleTime: 0,
    refetchOnWindowFocus: true,
  }),

  /**
   * GET /jobs — full queue list: queued + running + needs_confirm + recent terminal.
   *
   * Driven live by the /jobs/stream EventSource in JobController (which invalidates
   * this query on every lifecycle event). The 5 s refetchInterval is a fallback for
   * missed events (e.g. transient SSE disconnect).
   */
  list: () => ({
    queryKey: ['jobs', 'list'] as const,
    queryFn: () => api.get<JobListResponse>('/jobs'),
    staleTime: 0,
    refetchInterval: 5000,
  }),
}
