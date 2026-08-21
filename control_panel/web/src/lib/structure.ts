/**
 * Library structures — the candidate layouts, client side.
 *
 * THE STAGE F3 QUESTION: which way of arranging a photo library actually gets navigated
 * by, and does any of them have to be a directory on disk?
 *
 * The answer this file is built to test is that none of them does. Every structure
 * arrives from `GET /api/photos/structure` as a list of groups, and every group carries
 * the `PhotoFilters` mapping that resolves to exactly its own photos — the same mapping
 * `POST /api/collections` stores. So a "folder" is a `WHERE` clause, clicking one is a
 * filter change, and keeping one is a saved collection. Nothing moves.
 *
 * Four kinds, and the one real difference between them:
 *
 * - `flat` / `month` — derived from a column, pure SQL, no state anywhere.
 * - `album` — the `dc:subject` tag set. The only structure whose membership a human
 *   AUTHORED, and therefore the only one that can hold a hand-picked set. The membership
 *   lives in the JPEG, not in the index, so it survives `rm index.db` (decision 0002).
 * - `event` — a time-gap clustering over `date_taken`. The group's query is an ordinary
 *   date range, but the BOUNDARY is computed rather than stored: it is the one structure
 *   whose *grouping function* has no expression in the filter vocabulary. `gap_seconds`
 *   is a request parameter for that reason — there is no single correct grain.
 */

/** The four candidate layouts, in the order the switch offers them. */
export const STRUCTURE_KINDS = ['flat', 'month', 'album', 'event'] as const

export type StructureKind = (typeof STRUCTURE_KINDS)[number]

/** Switch labels. Short on purpose: the control sits in a 248px sidebar. */
export const STRUCTURE_LABELS: Record<StructureKind, string> = {
  flat: 'Flat',
  month: 'Month',
  album: 'Album',
  event: 'Event',
}

/**
 * What each kind is, in one line — the section's own explanation of the experiment.
 * Shown under the switch rather than in a tooltip: the point of the screen is to make
 * the four comparable, and a comparison you have to hover for is not one.
 */
export const STRUCTURE_HINTS: Record<StructureKind, string> = {
  flat: 'The two directories the library actually has.',
  month: 'Calendar months, as a date range each.',
  album: 'Hand-written keyword tags — the only membership a person chose.',
  event: 'Shoots, found by a gap in capture time. Boundaries are computed, not stored.',
}

/**
 * Selectable event grains, in seconds.
 *
 * Measured on the real library (3 797 dated photos): 10 min gives 409 mostly-useless
 * fragments, 7 days gives 19 blobs. 2 days is the default because it is the largest
 * threshold that still keeps every hand-tagged trip in one piece WITHOUT merging a
 * month of ordinary shooting into a single 26-day "event", which 3 days does.
 */
export const EVENT_GAPS: { value: number; label: string }[] = [
  { value: 3600, label: '1 h' },
  { value: 8 * 3600, label: '8 h' },
  { value: 86400, label: '1 d' },
  { value: 2 * 86400, label: '2 d' },
  { value: 3 * 86400, label: '3 d' },
  { value: 7 * 86400, label: '7 d' },
]

export const DEFAULT_EVENT_GAP = 2 * 86400

/** One navigable node of a structure. `query` is what clicking it applies. */
export type StructureGroup = {
  key: string
  label: string
  sublabel: string
  count: number
  /** A `PhotoFilters`-shaped mapping. Storable verbatim as a saved collection. */
  query: Record<string, unknown>
}

export type Structure = {
  kind: StructureKind
  /** Only set for `event`. */
  gap_seconds: number | null
  total: number
  /** Photos the structure cannot place. The measurement of whether a layout covers. */
  ungrouped: number
  groups: StructureGroup[]
  /** Server wall time for the grouping — "cheap or needs an artifact", measured. */
  elapsed_ms: number
}

/**
 * The `PhotoFilters` fields each kind's groups can set — its own dimension, and only it.
 *
 * Selecting a group resets these before applying its query, so clicking a second month
 * replaces the first rather than intersecting with it. Deliberately per-kind rather than
 * one combined list: a combined list would make picking a month also clear the folder
 * you were in, which is the opposite of the model — a structure group is a REFINE on one
 * dimension, and every other dimension keeps narrowing alongside it.
 */
export const STRUCTURE_KIND_FIELDS: Record<StructureKind, readonly string[]> = {
  flat: ['root'],
  month: ['date_from', 'date_to'],
  album: ['keyword'],
  event: ['date_from', 'date_to'],
}
