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
   * POST /ops/{op} — starts a background job, returns {job_id}.
   * Returns 202 on success, throws on 409 (job already running).
   */
  start: (op: string, extra?: QueryParams): Promise<JobStarted> =>
    api.post<JobStarted>(`/ops/${op}`, extra),

  /** Build extra params for backup based on source. */
  backupParams: (source: BackupSource): QueryParams => ({ source }),
}
