import { api } from '../api'
import type { GallerySyncStatusResponse } from '../api-types'

export const galleryQueries = {
  status: () => ({
    queryKey: ['gallery', 'status'] as const,
    queryFn: () => api.get<GallerySyncStatusResponse>('/gallery/status'),
    staleTime: 30_000,
    refetchInterval: 60_000,
  }),
}
