import { api } from '../api'
import {
  DEFAULT_PHOTO_LIMIT,
  MAX_WARM_PATHS,
  MAX_WRITE_PATHS,
  PHOTOS_API,
  toQueryParams,
  withQuery,
} from '../photos'
import type {
  Facets,
  PhotoFilters,
  PhotoListResponse,
  OpenInEditorResult,
  PhotoMeta,
  PurgeResult,
  RejectSummary,
  RestoreResult,
  ThumbTier,
  TrashListResponse,
  TrashResult,
  WarmResult,
  WriteResult,
} from '../photos'

/**
 * Query factories for the Photos culling screen.
 *
 * Key hierarchy is `['photos', <action>, ...params]` — invalidating `['photos']`
 * catches the list, the facets and every meta row at once, which is what a rating
 * write or a trash move needs.
 *
 * Mutations live in the route (they need optimistic cache surgery against a
 * specific list key); `photosApi` below is just their transport.
 */
export const photosQueries = {
  list: (filters: PhotoFilters, limit: number = DEFAULT_PHOTO_LIMIT, offset = 0) => ({
    queryKey: ['photos', 'list', filters, limit, offset] as const,
    queryFn: () => {
      const params = toQueryParams(filters)
      params.set('limit', String(limit))
      params.set('offset', String(offset))
      return api.get<PhotoListResponse>(withQuery(PHOTOS_API, params))
    },
    staleTime: 30_000,
  }),

  facets: (filters: PhotoFilters) => ({
    queryKey: ['photos', 'facets', filters] as const,
    queryFn: () => api.get<Facets>(withQuery(`${PHOTOS_API}/facets`, toQueryParams(filters))),
    staleTime: 30_000,
  }),

  /**
   * Full metadata for one photo, including the live exiftool dump.
   * Disabled for an empty path so the info panel can call it unconditionally.
   *
   * The request is abortable and the caller MUST feed it a debounced path: the server
   * spawns an exiftool process per call, and an arrow-key held down would otherwise queue
   * dozens of them ahead of the viewer's own thumbnail in the browser's 6-connection
   * budget — the one thing the thumb route promises never to do.
   */
  meta: (path: string) => ({
    queryKey: ['photos', 'meta', path] as const,
    queryFn: ({ signal }: { signal: AbortSignal }) =>
      api.get<PhotoMeta>(
        `${PHOTOS_API}/meta?${new URLSearchParams({ path }).toString()}`,
        undefined,
        { signal },
      ),
    enabled: path.length > 0,
    staleTime: 30_000,
  }),

  trash: (limit = 500) => ({
    queryKey: ['photos', 'trash', limit] as const,
    queryFn: () => api.get<TrashListResponse>(`${PHOTOS_API}/trash`, { limit }),
    staleTime: 10_000,
  }),

  /**
   * How many photos are rejected and awaiting a purge.
   *
   * Deliberately NOT under the `['photos', 'list']` prefix: a reject is a rating write,
   * and the list is the one thing a rating does not invalidate. This count is its own
   * key so the purge button can refresh on every reject without dragging the list —
   * ~1 MB of rows — along behind a keypress-rate loop.
   */
  rejects: (root: string | null = null) => ({
    queryKey: ['photos', 'rejects', root] as const,
    queryFn: () =>
      api.get<RejectSummary>(`${PHOTOS_API}/rejects`, root === null ? undefined : { root }),
    staleTime: 10_000,
  }),
}

/**
 * Write-side transport for the culling screen. Every call is a plain promise —
 * wrap it in `useMutation` at the call site so the optimistic update and the
 * rollback stay next to the cache they touch.
 */
export const photosApi = {
  /**
   * Write an XMP star rating to a batch of photos. Rating 0 clears the tag.
   * The server reindexes the touched files before responding.
   */
  setRating: (paths: string[], rating: number): Promise<WriteResult> =>
    api.post<WriteResult>(`${PHOTOS_API}/rating`, undefined, {
      paths: paths.slice(0, MAX_WRITE_PATHS),
      rating,
    }),

  /**
   * Hand one photo to the external editor (Shutterflow).
   *
   * A launch, not a write: nothing here waits for the editor or learns what it did. The
   * editor writes the master's XMP packet in place, so the change arrives back through
   * the index the same way a Photomator edit does — on the next reindex, via mtime.
   */
  openInEditor: (path: string): Promise<OpenInEditorResult> =>
    api.post<OpenInEditorResult>(`${PHOTOS_API}/open-in-editor`, undefined, { path }),

  /** Write an XMP colour label to a batch of photos; an empty string clears it. */
  setLabel: (paths: string[], label: string): Promise<WriteResult> =>
    api.post<WriteResult>(`${PHOTOS_API}/label`, undefined, {
      paths: paths.slice(0, MAX_WRITE_PATHS),
      label,
    }),

  /**
   * Move every rejected photo into the trash — the batch step that ends a cull pass.
   *
   * Takes no paths: the server re-reads the rejects from the index, so what gets moved
   * is the judgement as it stands at the moment of the click, not a list this client
   * assembled at some earlier point in the pass.
   */
  purgeRejects: (root: string | null = null, dryRun = false): Promise<TrashResult> =>
    api.post<TrashResult>(`${PHOTOS_API}/rejects/purge`, {
      ...(root === null ? {} : { root }),
      dry_run: dryRun,
    }),

  /** Soft-delete: move photos (and their .photo-edit sidecars) into the trash. */
  trash: (paths: string[], dryRun = false): Promise<TrashResult> =>
    api.post<TrashResult>(`${PHOTOS_API}/trash`, undefined, {
      paths: paths.slice(0, MAX_WRITE_PATHS),
      dry_run: dryRun,
    }),

  /** Undo a trash move. Refused (not overwritten) when the original path is occupied. */
  restore: (ids: number[]): Promise<RestoreResult> =>
    api.post<RestoreResult>(`${PHOTOS_API}/trash/restore`, undefined, { ids }),

  /** Permanently delete trash entries older than the retention window. */
  purge: (options: { days?: number; dryRun?: boolean } = {}): Promise<PurgeResult> =>
    api.post<PurgeResult>(`${PHOTOS_API}/trash/purge`, undefined, {
      ...(options.days === undefined ? {} : { days: options.days }),
      dry_run: options.dryRun ?? false,
    }),

  /**
   * Fire-and-forget thumbnail prewarm. Capped at the server's 200-path limit;
   * an empty batch short-circuits (the endpoint rejects it with a 400).
   */
  warm: (paths: string[], tier: ThumbTier): Promise<WarmResult> => {
    const batch = paths.slice(0, MAX_WARM_PATHS)
    if (batch.length === 0) return Promise.resolve({ generated: 0 })
    return api.post<WarmResult>(`${PHOTOS_API}/warm`, undefined, { paths: batch, tier })
  },
}
