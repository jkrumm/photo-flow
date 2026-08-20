/**
 * Photos culling screen — shared types, URL builders and pure filter helpers.
 *
 * Types mirror the Pydantic models in `photo_flow/api/routes_photos.py` field for
 * field. They deliberately live here rather than in `api-types.ts`: that file is
 * regenerated from the live OpenAPI schema (`npm run gen:api`) and must not be
 * hand-edited.
 *
 * `PhotoFilters` is the one non-wire type. It is a **fully populated, plain
 * serialisable object** — every key is always present, "unset" is `null` for a
 * scalar and `[]` for a list. That shape is what makes it safe as a TanStack
 * Router `validateSearch` result under `exactOptionalPropertyTypes`: there is no
 * optional-vs-undefined ambiguity to get wrong, and `{ ...filters, iso_min: x }`
 * always typechecks. Field names match the API query parameters 1:1, so the URL
 * of a cull session reads the same as the request it produces.
 */

const BASE = (import.meta.env.VITE_API_URL as string | undefined) ?? ''

/** Route prefix for every culling endpoint (see the module docstring in routes_photos.py). */
export const PHOTOS_API = '/api/photos'

/** Server-side cap on `POST /api/photos/warm` (`MAX_WARM_PATHS`). */
export const MAX_WARM_PATHS = 200

/** Server-side cap on the rating / label / trash batch endpoints (`MAX_WRITE_PATHS`). */
export const MAX_WRITE_PATHS = 500

/** Default page size of `GET /api/photos` (server default; max is 10000). */
export const DEFAULT_PHOTO_LIMIT = 2000

// ── Wire types ───────────────────────────────────────────────────────────────

export type PhotoRoot = 'final' | 'staging'
export type Orientation = 'landscape' | 'portrait' | 'square'
export type ThumbTier = 'grid' | 'view'
export type PhotoSort = 'date_taken' | 'filename' | 'rating' | 'iso' | 'focal_mm'
export type SortOrder = 'asc' | 'desc'

export const PHOTO_ROOTS: readonly PhotoRoot[] = ['final', 'staging']
export const ORIENTATIONS: readonly Orientation[] = ['landscape', 'portrait', 'square']
export const PHOTO_SORTS: readonly PhotoSort[] = [
  'date_taken',
  'filename',
  'rating',
  'iso',
  'focal_mm',
]
export const SORT_ORDERS: readonly SortOrder[] = ['asc', 'desc']

/** One indexed photo. `rating` is null when the XMP tag is absent (never rated / cleared). */
export type PhotoRow = {
  path: string
  filename: string
  root: string
  rating: number | null
  label: string
  orientation: string | null
  width: number | null
  height: number | null
  date_taken: string | null
  iso: number | null
  aperture_f: number | null
  shutter_s: number | null
  focal_mm: number | null
  camera_model: string | null
  lens_model: string
  has_sidecar: boolean
  size: number
  mtime: number
}

export type PhotoListResponse = {
  total: number
  offset: number
  limit: number
  items: PhotoRow[]
}

export type FacetValue = {
  value: string
  count: number
}

export type RangeFacet = {
  min: number | null
  max: number | null
}

export type DateFacet = {
  min: string | null
  max: string | null
}

/**
 * Facet counts for the current selection. Each dimension is computed with every
 * *other* filter applied but its own left open, so an option list never collapses
 * to the value the user just picked.
 */
export type Facets = {
  count: number
  /** Keyed '-1'…'5'; missing ratings are folded into '0', '-1' is the reject count. */
  ratings: Record<string, number>
  labels: FacetValue[]
  orientations: Record<string, number>
  camera_models: FacetValue[]
  lens_models: FacetValue[]
  iso: RangeFacet
  focal: RangeFacet
  aperture: RangeFacet
  shutter: RangeFacet
  date: DateFacet
}

/** `GET /api/photos/meta` — the indexed row plus live sizes and a best-effort exiftool dump. */
export type PhotoMeta = PhotoRow & {
  file_size: number | null
  sidecar_size: number | null
  /** exiftool `-j -G0:1` output; `{}` when the read failed or timed out. */
  exif_extra: Record<string, unknown>
}

export type WriteResult = {
  updated: number
  errors: number
  messages: string[]
}

/** Entry stub echoed by `POST /api/photos/trash`. `id` is null on a dry run. */
export type TrashedEntry = {
  id: number | null
  filename: string
  original_path: string
  rating: number | null
}

export type TrashResult = {
  trashed: number
  entries: TrashedEntry[]
  errors: number
  messages: string[]
}

/**
 * The result of handing a photo to the external editor.
 *
 * `opened: false` is a normal answer, not an error: the editor is optional and a machine
 * without it installed must say so plainly rather than surface a 500.
 */
export type OpenInEditorResult = {
  opened: boolean
  editor: string
  message: string
}

/** How many photos are flagged rejected and awaiting the batch purge. */
export type RejectSummary = {
  count: number
}

/** A row of the `trash` table, enriched by the API with age / purgeable / exists. */
export type TrashEntry = {
  id: number
  original_path: string
  root: string
  trashed_path: string
  sidecar_original_path: string | null
  sidecar_trashed_path: string | null
  filename: string
  size: number
  rating: number | null
  trashed_at: string
  age_days: number
  purgeable: boolean
  exists: boolean
}

export type TrashStats = {
  count: number
  bytes: number
  oldest_iso: string | null
  purgeable: number
}

export type TrashListResponse = {
  entries: TrashEntry[]
  stats: TrashStats
}

export type RestoreResult = {
  restored: number
  errors: number
  messages: string[]
}

export type PurgeResult = {
  purged: number
  bytes: number
  errors: number
}

export type WarmResult = {
  generated: number
}

// ── Filters ──────────────────────────────────────────────────────────────────

/**
 * The complete culling filter set. Every key is always present; `null` / `[]`
 * mean "not filtering on this dimension".
 *
 * `sort` / `order` ride along because they belong to the same top bar and the
 * same URL. `GET /api/photos/facets` ignores both, so one serialiser feeds both
 * endpoints.
 */
export type PhotoFilters = {
  root: PhotoRoot | null
  /** OR-set of exact star ratings; missing ratings count as 0. */
  rating: number[]
  rating_min: number | null
  label: string[]
  orientation: Orientation | null
  iso_min: number | null
  iso_max: number | null
  aperture_min: number | null
  aperture_max: number | null
  shutter_min: number | null
  shutter_max: number | null
  focal_min: number | null
  focal_max: number | null
  camera_model: string[]
  lens_model: string[]
  /** ISO date (YYYY-MM-DD) — a string, never a Date, so the object stays URL-serialisable. */
  date_from: string | null
  date_to: string | null
  /** Filename substring; matched with LIKE, wildcards escaped server-side. */
  q: string | null
  has_sidecar: boolean | null
  sort: PhotoSort
  order: SortOrder
}

/** The unfiltered state — also the baseline `activeFilterCount` measures against. */
export const DEFAULT_FILTERS: PhotoFilters = {
  root: null,
  rating: [],
  rating_min: null,
  label: [],
  orientation: null,
  iso_min: null,
  iso_max: null,
  aperture_min: null,
  aperture_max: null,
  shutter_min: null,
  shutter_max: null,
  focal_min: null,
  focal_max: null,
  camera_model: [],
  lens_model: [],
  date_from: null,
  date_to: null,
  q: null,
  has_sidecar: null,
  sort: 'date_taken',
  order: 'asc',
}

/** Filter keys that count toward `activeFilterCount` (sort/order are view state, not filters). */
const FILTER_KEYS = [
  'root',
  'rating',
  'rating_min',
  'label',
  'orientation',
  'iso_min',
  'iso_max',
  'aperture_min',
  'aperture_max',
  'shutter_min',
  'shutter_max',
  'focal_min',
  'focal_max',
  'camera_model',
  'lens_model',
  'date_from',
  'date_to',
  'q',
  'has_sidecar',
] as const satisfies readonly (keyof PhotoFilters)[]

function isSet(value: PhotoFilters[keyof PhotoFilters]): boolean {
  if (value === null) return false
  if (Array.isArray(value)) return value.length > 0
  if (typeof value === 'string') return value.length > 0
  return true
}

/**
 * How many dimensions the user has narrowed. Drives the "Clear filters" button
 * and the filter-count badge; `sort` and `order` are excluded on purpose.
 */
export function activeFilterCount(filters: PhotoFilters): number {
  let count = 0
  for (const key of FILTER_KEYS) {
    if (isSet(filters[key])) count += 1
  }
  return count
}

/** True when any filter dimension is narrowed. */
export function hasActiveFilters(filters: PhotoFilters): boolean {
  return activeFilterCount(filters) > 0
}

/**
 * Reset every filter dimension, keeping the current root and sort — clearing the
 * filters should not also throw the user out of the folder they are culling.
 */
export function clearFilters(filters: PhotoFilters): PhotoFilters {
  return { ...DEFAULT_FILTERS, root: filters.root, sort: filters.sort, order: filters.order }
}

/** Add or remove one value from a multi-select filter array (chips, MultiSelect). */
export function toggleValue<T>(values: readonly T[], value: T): T[] {
  return values.includes(value) ? values.filter((v) => v !== value) : [...values, value]
}

// ── Coercion (validateSearch) ────────────────────────────────────────────────

function asOneOf<T extends string>(value: unknown, allowed: readonly T[]): T | null {
  return typeof value === 'string' && (allowed as readonly string[]).includes(value)
    ? (value as T)
    : null
}

function asNumber(value: unknown): number | null {
  if (typeof value === 'number') return Number.isFinite(value) ? value : null
  if (typeof value === 'string' && value.trim() !== '') {
    const parsed = Number(value)
    return Number.isFinite(parsed) ? parsed : null
  }
  return null
}

function asString(value: unknown): string | null {
  if (typeof value === 'string' && value.length > 0) return value
  if (typeof value === 'number') return String(value)
  return null
}

function asBoolean(value: unknown): boolean | null {
  if (typeof value === 'boolean') return value
  if (value === 'true') return true
  if (value === 'false') return false
  return null
}

function asList(value: unknown): unknown[] {
  if (Array.isArray(value)) return value
  if (value === null || value === undefined || value === '') return []
  return [value]
}

function asStringList(value: unknown): string[] {
  const out: string[] = []
  for (const item of asList(value)) {
    const parsed = asString(item)
    if (parsed !== null) out.push(parsed)
  }
  return out
}

function asNumberList(value: unknown): number[] {
  const out: number[] = []
  for (const item of asList(value)) {
    const parsed = asNumber(item)
    if (parsed !== null) out.push(parsed)
  }
  return out
}

/**
 * Coerce arbitrary search-param input into a complete `PhotoFilters`.
 *
 * Built for a TanStack Router `validateSearch`: unknown keys are dropped, bad
 * values fall back to the default rather than throwing, and the result is always
 * fully populated. A hand-edited URL degrades to "no filter", never to a crash.
 *
 * @param raw Parsed search object (or anything else — non-objects yield the defaults).
 */
export function normalizeFilters(raw: unknown): PhotoFilters {
  const src = (typeof raw === 'object' && raw !== null ? raw : {}) as Record<string, unknown>
  return {
    root: asOneOf(src.root, PHOTO_ROOTS),
    rating: asNumberList(src.rating).filter((n) => n >= 0 && n <= 5),
    rating_min: asNumber(src.rating_min),
    label: asStringList(src.label),
    orientation: asOneOf(src.orientation, ORIENTATIONS),
    iso_min: asNumber(src.iso_min),
    iso_max: asNumber(src.iso_max),
    aperture_min: asNumber(src.aperture_min),
    aperture_max: asNumber(src.aperture_max),
    shutter_min: asNumber(src.shutter_min),
    shutter_max: asNumber(src.shutter_max),
    focal_min: asNumber(src.focal_min),
    focal_max: asNumber(src.focal_max),
    camera_model: asStringList(src.camera_model),
    lens_model: asStringList(src.lens_model),
    date_from: asString(src.date_from),
    date_to: asString(src.date_to),
    q: asString(src.q),
    has_sidecar: asBoolean(src.has_sidecar),
    sort: asOneOf(src.sort, PHOTO_SORTS) ?? DEFAULT_FILTERS.sort,
    order: asOneOf(src.order, SORT_ORDERS) ?? DEFAULT_FILTERS.order,
  }
}

/**
 * Drop every default-valued key, so a navigation writes only what the user
 * actually chose and the URL stays readable.
 */
export function stripDefaults(filters: PhotoFilters): Partial<PhotoFilters> {
  const out: Record<string, unknown> = {}
  for (const key of Object.keys(DEFAULT_FILTERS) as (keyof PhotoFilters)[]) {
    const value = filters[key]
    const fallback = DEFAULT_FILTERS[key]
    if (Array.isArray(value)) {
      if (value.length > 0) out[key] = value
      continue
    }
    if (value !== fallback && value !== null) out[key] = value
  }
  return out as Partial<PhotoFilters>
}

// ── URL building ─────────────────────────────────────────────────────────────

/**
 * Serialise a filter set into query parameters.
 *
 * List filters are repeated (`?rating=4&rating=5`) — that is the shape FastAPI's
 * `Query(default=None)` list dependency expects. `null` / `[]` are omitted, so an
 * unset dimension never reaches the server as an empty string.
 */
export function toQueryParams(filters: PhotoFilters): URLSearchParams {
  const params = new URLSearchParams()
  const append = (key: string, value: string | number | boolean | null): void => {
    if (value === null || value === '') return
    params.append(key, String(value))
  }

  append('root', filters.root)
  for (const value of filters.rating) append('rating', value)
  append('rating_min', filters.rating_min)
  for (const value of filters.label) append('label', value)
  append('orientation', filters.orientation)
  append('iso_min', filters.iso_min)
  append('iso_max', filters.iso_max)
  append('aperture_min', filters.aperture_min)
  append('aperture_max', filters.aperture_max)
  append('shutter_min', filters.shutter_min)
  append('shutter_max', filters.shutter_max)
  append('focal_min', filters.focal_min)
  append('focal_max', filters.focal_max)
  for (const value of filters.camera_model) append('camera_model', value)
  for (const value of filters.lens_model) append('lens_model', value)
  append('date_from', filters.date_from)
  append('date_to', filters.date_to)
  append('q', filters.q)
  append('has_sidecar', filters.has_sidecar)
  append('sort', filters.sort)
  append('order', filters.order)

  return params
}

/** Join a path and query params, omitting the `?` when there is nothing to send. */
export function withQuery(path: string, params: URLSearchParams): string {
  const query = params.toString()
  return query ? `${path}?${query}` : path
}

/**
 * URL of a cached thumbnail.
 *
 * `v` is the client-side cache-buster: the server ignores it, but including the
 * mtime means an edited or re-rated photo gets a distinct URL, which is what makes
 * the `immutable, max-age=1y` response header safe.
 */
export function thumbUrl(row: Pick<PhotoRow, 'path' | 'mtime'>, tier: ThumbTier): string {
  const params = new URLSearchParams({ path: row.path, tier, v: String(row.mtime) })
  return `${BASE}${PHOTOS_API}/thumb?${params.toString()}`
}

// ── Display helpers ──────────────────────────────────────────────────────────

/**
 * The XMP reject value. A third cull state, NOT a low star count — see the server's
 * `REJECTED` in `routes_photos.py` for the full rationale.
 */
export const REJECTED = -1

/** Effective star rating — an absent XMP tag reads as 0, matching the facet buckets. */
export function ratingOf(row: Pick<PhotoRow, 'rating'>): number {
  return row.rating ?? 0
}

/** Whether this photo has been culled but not yet purged. */
export function isRejected(row: Pick<PhotoRow, 'rating'>): boolean {
  return row.rating === REJECTED
}

/** Facet count for one star bucket ('-1'…'5'), 0 when the facets have not loaded. */
export function ratingFacetCount(facets: Facets | undefined, rating: number): number {
  return facets?.ratings[String(rating)] ?? 0
}

/** Facet count for one orientation, 0 when the facets have not loaded. */
export function orientationFacetCount(
  facets: Facets | undefined,
  orientation: Orientation,
): number {
  return facets?.orientations[orientation] ?? 0
}

/** `YYYY-MM` group key for the date rail; null when the photo has no capture date. */
export function monthKey(row: Pick<PhotoRow, 'date_taken'>): string | null {
  return row.date_taken ? row.date_taken.slice(0, 7) : null
}

/** `YYYY` group key for the date rail; null when the photo has no capture date. */
export function yearKey(row: Pick<PhotoRow, 'date_taken'>): string | null {
  return row.date_taken ? row.date_taken.slice(0, 4) : null
}
