import { api } from '../api'
import type { StatusResponse, PendingResponse } from '../api-types'

export const statusQueries = {
  status: () => ({
    queryKey: ['status'] as const,
    queryFn: () => api.get<StatusResponse>('/status'),
    refetchInterval: 3_000,
    staleTime: 0,
  }),

  pending: () => ({
    queryKey: ['status', 'pending'] as const,
    queryFn: () => api.get<PendingResponse>('/status/pending'),
    staleTime: 30_000,
  }),
}
