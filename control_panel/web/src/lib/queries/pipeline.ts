import { api } from '../api'
import type { PipelineStatus } from '../api-types'

export const pipelineQueries = {
  /**
   * Richer pipeline snapshot — counts across all five transitions plus last-run
   * timestamps. Matches the 3-second cadence of the cheap status poll.
   */
  snapshot: () => ({
    queryKey: ['status', 'pipeline'] as const,
    queryFn: () => api.get<PipelineStatus>('/status/pipeline'),
    refetchInterval: 3_000,
    staleTime: 0,
  }),
}
