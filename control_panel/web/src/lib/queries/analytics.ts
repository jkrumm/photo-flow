import { api } from '../api'
import type {
  BucketGrain,
  BucketPoint,
  LibraryHealthResponse,
  MapPoint,
  RatingsResponse,
  SettingsResponse,
  StorageResponse,
  SummaryResponse,
} from '../api-types'

export const analyticsQueries = {
  summary: () => ({
    queryKey: ['analytics', 'summary'] as const,
    queryFn: () => api.get<SummaryResponse>('/analytics/summary'),
    staleTime: 30_000,
    refetchInterval: 60_000,
  }),

  overTime: (bucket: BucketGrain) => ({
    queryKey: ['analytics', 'over-time', bucket] as const,
    queryFn: () => api.get<BucketPoint[]>('/analytics/over-time', { bucket }),
    staleTime: 60_000,
  }),

  ratings: () => ({
    queryKey: ['analytics', 'ratings'] as const,
    queryFn: () => api.get<RatingsResponse>('/analytics/ratings'),
    staleTime: 60_000,
  }),

  settings: () => ({
    queryKey: ['analytics', 'settings'] as const,
    queryFn: () => api.get<SettingsResponse>('/analytics/settings'),
    staleTime: 60_000,
  }),

  storage: () => ({
    queryKey: ['analytics', 'storage'] as const,
    queryFn: () => api.get<StorageResponse>('/analytics/storage'),
    staleTime: 30_000,
    refetchInterval: 60_000,
  }),

  map: () => ({
    queryKey: ['analytics', 'map'] as const,
    queryFn: () => api.get<MapPoint[]>('/analytics/map'),
    staleTime: 300_000,
  }),

  libraryHealth: () => ({
    queryKey: ['analytics', 'library-health'] as const,
    queryFn: () => api.get<LibraryHealthResponse>('/analytics/library-health'),
    staleTime: 30_000,
    refetchInterval: 60_000,
  }),
}
