import { api } from '../api'
import type { SummaryResponse } from '../api-types'

export const analyticsQueries = {
  summary: () => ({
    queryKey: ['analytics', 'summary'] as const,
    queryFn: () => api.get<SummaryResponse>('/analytics/summary'),
    staleTime: 30_000,
    refetchInterval: 60_000,
  }),
}
