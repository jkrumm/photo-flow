import { api } from '../api'
import type { JobHistoryResponse, JobListResponse } from '../api-types'

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

  /**
   * GET /jobs/history — the DURABLE record, newest first.
   *
   * Distinct from `list()` in the one way that matters: it survives a server restart.
   * `GET /jobs` reads the in-memory manager, so a crash / logout / `make reload` empties
   * it completely; this reads SQLite and still shows the backup that was 80 % done.
   */
  history: (limit = 25) => ({
    queryKey: ['jobs', 'history', limit] as const,
    queryFn: () => api.get<JobHistoryResponse>('/jobs/history', { limit }),
    staleTime: 0,
  }),

  /**
   * Terminal jobs the UI has never surfaced — the catch-up feed for the notification
   * bell. Fetched once on mount: a job that finished while the panel was closed
   * produced no toast and no history entry, so the bell recorded only what was watched.
   */
  unannounced: () => ({
    queryKey: ['jobs', 'unannounced'] as const,
    queryFn: () => api.get<JobHistoryResponse>('/jobs/history', { unannounced: true }),
    staleTime: Infinity,
    refetchOnWindowFocus: false,
  }),
}

export const jobsApi = {
  /** Flag records as surfaced so a job never notifies twice. */
  ack: (jobIds: string[]) =>
    api.post<{ acknowledged: number }>('/jobs/history/ack', undefined, { job_ids: jobIds }),
}
