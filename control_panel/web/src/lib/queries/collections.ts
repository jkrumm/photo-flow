import { api } from '../api'
import {
  COLLECTIONS_API,
  type Collection,
  type CollectionListResponse,
  type CollectionQuery,
} from '../collections'

/**
 * Query factory and write transport for saved collections.
 *
 * Key hierarchy is `['collections', ...]`, deliberately NOT under `['photos']`: a rating
 * write invalidates `['photos', 'facets']` at keypress rate, and the collection list is a
 * file read that nothing about rating a photo can change. Only the *counts* move, and
 * they ride the same key with the flag in it so a countless listing is never refetched
 * for a reason that only concerns counts.
 */
export const collectionsQueries = {
  list: (withCounts = false) => ({
    queryKey: ['collections', 'list', withCounts] as const,
    queryFn: () =>
      api.get<CollectionListResponse>(COLLECTIONS_API, { with_counts: withCounts }),
    staleTime: 30_000,
  }),
}

export const collectionsApi = {
  /**
   * Save the current view under a name.
   *
   * `scope` is the collection the view was seen THROUGH, if any. The server composes the
   * two and stores the flattened result, so a saved collection is always a complete
   * answer rather than a delta on another one that could change under it later.
   */
  create: (name: string, query: CollectionQuery, scope: string | null = null): Promise<Collection> =>
    api.post<Collection>(COLLECTIONS_API, undefined, { name, query, scope }),

  rename: (id: string, name: string): Promise<Collection> =>
    api.patch<Collection>(`${COLLECTIONS_API}/${encodeURIComponent(id)}`, undefined, { name }),

  /** Replace the stored query wholesale — "save over" the collection you are looking at. */
  replaceQuery: (
    id: string,
    query: CollectionQuery,
    scope: string | null = null,
  ): Promise<Collection> =>
    api.patch<Collection>(`${COLLECTIONS_API}/${encodeURIComponent(id)}`, undefined, {
      query,
      scope,
    }),

  remove: (id: string): Promise<{ deleted: boolean }> =>
    api.del<{ deleted: boolean }>(`${COLLECTIONS_API}/${encodeURIComponent(id)}`),
}
