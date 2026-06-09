import { api } from '../api'
import type { BackupAvailabilityResponse } from '../api-types'

export const backupQueries = {
  availability: () => ({
    queryKey: ['backup', 'availability'] as const,
    queryFn: () => api.get<BackupAvailabilityResponse>('/backup/availability'),
    staleTime: 30_000,
    refetchInterval: 60_000,
  }),
}
