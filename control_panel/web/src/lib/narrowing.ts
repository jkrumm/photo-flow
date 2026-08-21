/**
 * The narrowing model, client side — types and pure helpers only.
 *
 * The MODEL itself lives on the server (`routes_photos.compose`). This file deliberately
 * holds no copy of the precedence rule: the screen names its scope with `?collection=`
 * and reads the consequences back from `GET /api/photos/narrowing`. A rule implemented
 * twice is a rule that drifts, and this is the one the whole screen is built on.
 *
 * THE RULE, for the reader who lands here first:
 *
 *   REFINE overrides SCOPE on a shared DIMENSION, and intersects with it on every other.
 *
 * SCOPE is the saved collection you selected. REFINE is everything else the sidebar
 * sets — the folder rail, the date tree and the filter panel are all one layer, because
 * a "folder" here is a query over the `root` column and not a place. The full table is
 * in `photo_flow/api/routes_photos.py` and in `tests/test_narrowing.py`.
 */

/** One dimension currently narrowing the result set. */
export type NarrowingDimension = {
  /** Server-side dimension key (`rating`, `focal`, `keyword`, …). */
  dimension: string
  /** Human label — supplied by the server so the two never disagree. */
  label: string
  /** Which layer supplies the effective clause. Never both: one layer owns a dimension. */
  source: 'scope' | 'refine'
  /** The scope also set this dimension and lost. Shown, never swallowed. */
  overrides: boolean
  /** The effective field values, for the chip's detail text. */
  fields: Record<string, unknown>
  /** What the scope wanted, when it was overridden. */
  scope_fields: Record<string, unknown>
  /** Rows that would be in view if this dimension were cleared. Always >= total. */
  without: number
}

export type Narrowing = {
  scope_id: string | null
  scope_name: string | null
  /** Rows in view now. */
  total: number
  /** Rows the scope alone yields — equals `total` when there is no scope. */
  scope_total: number
  /** Rows the unfiltered library yields under the same defaults (rejects hidden). */
  library_total: number
  dimensions: NarrowingDimension[]
}

/**
 * Group a count for display: `3,515`.
 *
 * Explicitly `en-US`, not the browser locale. Every artifact in this project is English,
 * and on a German-locale machine the default `toLocaleString()` renders 3 515 as
 * "3.515" — which next to a "−2.191" delta reads as a decimal, not a thousand.
 */
export function formatCount(value: number): string {
  return value.toLocaleString('en-US')
}

/** How many rows this dimension is currently excluding. */
export function excludedBy(dimension: NarrowingDimension, total: number): number {
  return Math.max(dimension.without - total, 0)
}

/**
 * Render a dimension's effective values as one short line.
 *
 * Field names are shortened to their distinguishing part (`rating_min` → `min`), which is
 * enough next to a label that already says "Rating" and keeps the chip to one line.
 */
export function describeFields(fields: Record<string, unknown>): string {
  const parts: string[] = []
  for (const [key, value] of Object.entries(fields)) {
    const suffix = key.endsWith('_min') ? '≥' : key.endsWith('_max') ? '≤' : ''
    const rendered = Array.isArray(value) ? value.join(', ') : String(value)
    parts.push(suffix === '' ? rendered : `${suffix} ${rendered}`)
  }
  return parts.join(' · ')
}
