/**
 * Saved collections — types and the pure translation between a stored query and the
 * screen's filter state.
 *
 * A collection is a **name plus a query**, nothing else. Selecting one applies its
 * filters; it never moves, copies or links a file, and deleting one deletes a query.
 * That is the property being prototyped: whether saved queries can stand in for a
 * physical folder layout.
 *
 * The query lives in `~/Pictures/photoflow.toml`, next to the library — not in the
 * SQLite index, which is a disposable cache (decision 0002). The server tells us which
 * file, so the UI can point at it.
 */

import { stripDefaults, type PhotoFilters } from './photos'

/** Route prefix for every collection endpoint. */
export const COLLECTIONS_API = '/api/collections'

/**
 * The stored filter set.
 *
 * `sort` / `order` are deliberately absent: they are how you look at a set, not which set
 * it is, so applying a collection keeps the ordering you were already using. Every other
 * key is a `GET /api/photos` query parameter, validated server-side against the one
 * dataclass that defines them.
 */
export type CollectionQuery = Partial<Omit<PhotoFilters, 'sort' | 'order'>>

export type Collection = {
  id: string
  name: string
  query: CollectionQuery
  created_at: string
  updated_at: string
  /** Result-set size; `null` when counts were not requested or the index was unreadable. */
  count: number | null
}

export type CollectionListResponse = {
  items: Collection[]
  /** Absolute path of the TOML file backing the list. */
  path: string
}

/** Longest name the server accepts (`collections.MAX_NAME_LENGTH`). */
export const MAX_COLLECTION_NAME = 60

/**
 * The savable part of the current filter state.
 *
 * `stripDefaults` drops every dimension the user did not narrow, so a saved query holds
 * only real decisions and reads as one in the TOML file. `sort` / `order` are then
 * removed because a collection does not own them.
 */
export function filtersToQuery(filters: PhotoFilters): CollectionQuery {
  const { sort: _sort, order: _order, ...query } = stripDefaults(filters)
  return query
}

/** How many dimensions a saved query narrows — the collection row's one-glance summary. */
export function queryFilterCount(query: CollectionQuery): number {
  return Object.keys(query).length
}

/** True when the current filter state is worth saving (an unfiltered "collection" is the library). */
export function isSavable(filters: PhotoFilters): boolean {
  return queryFilterCount(filtersToQuery(filters)) > 0
}
