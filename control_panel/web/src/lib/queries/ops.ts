import { api } from '../api'
import type { JobStarted, BackupSource } from '../api-types'

type QueryParams = Record<string, string | number | boolean>

export const opsApi = {
  /**
   * POST /ops/{op}?dry_run=true — returns a preview dict (no files touched).
   * Extra params (e.g. source for backup) are passed as query params.
   */
  dryRun: (op: string, extra?: QueryParams): Promise<Record<string, unknown>> =>
    api.post<Record<string, unknown>>(`/ops/${op}`, { dry_run: true, ...extra }),

  /**
   * POST /ops/{op} — enqueues a background job, returns {job_id, status, position}.
   * Always succeeds (202); never throws 409. Extra query params (e.g. source) go in
   * `extra`; `approvedPreview` is sent as a JSON body for destructive ops (cleanup /
   * finalize) and enables the dispatch-time re-validation guard.
   */
  start: (
    op: string,
    extra?: QueryParams,
    body?: Record<string, unknown>,
  ): Promise<JobStarted> =>
    api.post<JobStarted>(`/ops/${op}`, extra, body),

  /** Build extra params for backup based on source. */
  backupParams: (source: BackupSource): QueryParams => ({ source }),

  /**
   * POST /jobs/{id}/cancel — request cooperative cancellation.
   * Works for running, queued, and needs_confirm jobs. Throws on 404/409.
   */
  cancel: (jobId: string): Promise<{ cancelled: boolean; job_id: string }> =>
    api.post<{ cancelled: boolean; job_id: string }>(`/jobs/${jobId}/cancel`),

  /**
   * POST /jobs/{id}/move — move a queued job one slot {up|down}.
   * No-op for running/terminal jobs (returns moved: false).
   */
  move: (jobId: string, direction: 'up' | 'down'): Promise<{ moved: boolean; job_id: string }> =>
    api.post<{ moved: boolean; job_id: string }>(`/jobs/${jobId}/move`, undefined, { direction }),
}
