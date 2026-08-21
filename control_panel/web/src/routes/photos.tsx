/**
 * `/photos` — the culling screen (the Adobe Bridge replacement).
 *
 * Four things make this route different from every other screen in the panel:
 *
 * 0. **All chrome lives on one edge.** The photo gets the whole stage; folders, filters,
 *    metadata and view options are sections of a single right-hand `PhotoSidebar`, and
 *    the only control floating over the image is the one that hides that sidebar. (There
 *    used to be a top bar, a left rail AND a right panel — three edges of chrome around
 *    the one thing the screen exists to show.)
 * 1. **It owns the viewport.** No page scroll: a flex column pinned to the height of
 *    `AppShell.Main`'s content box, with its own scroll regions inside.
 * 2. **The whole session lives in the URL.** Filters *and* the selected path are search
 *    params, so a cull session is linkable and survives a reload. Selection is mirrored
 *    with a debounce rather than written per keystroke — arrow-stepping a shoot would
 *    otherwise blow past Safari's `history.replaceState` throttle.
 * 3. **Stepping must never show a spinner.** A settled selection drives a server-side
 *    prewarm of a *directional* ring of frames (both thumbnail tiers, one request) plus an
 *    in-page `decode()` preload of the nearest few, held in a small LRU so back-stepping is
 *    free. `PhotoViewer` paints the cheap grid tier immediately and upgrades to `view` on
 *    decode, so even a cold frame never blocks the paint.
 *
 * Writes (rating, label, trash) are optimistic against the list cache and roll back on
 * error. Trash is a *move*, and the toast carries an Undo wired to the restore endpoint.
 */
import { Activity, useCallback, useEffect, useMemo, useRef, useState } from 'react'
import type { MouseEvent as ReactMouseEvent } from 'react'
import { createFileRoute } from '@tanstack/react-router'
import type { SearchSchemaInput } from '@tanstack/react-router'
import { keepPreviousData, useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { ActionIcon, Box, Button, Code, Flex, Group, Menu, Text, Tooltip } from '@mantine/core'
import { useDebouncedCallback, useHotkeys } from '@mantine/hooks'
import { modals } from '@mantine/modals'
import { notifications } from '@mantine/notifications'
import {
  IconArrowBackUp,
  IconLayoutSidebarRightCollapse,
  IconLayoutSidebarRightExpand,
  IconTool,
} from '@tabler/icons-react'
import { notifyError, notifySuccess } from 'basalt-ui/notifications'
import { createPersistedState } from 'basalt-ui/state'
import { VX, alpha } from 'basalt-ui/tokens'
import { PhotoCompare, MAX_COMPARE } from '../components/photos/photo-compare'
import { PhotoFilmstrip } from '../components/photos/photo-filmstrip'
import { PhotoGrid, DENSITY_STEPS, DEFAULT_DENSITY } from '../components/photos/photo-grid'
import type { SelectMods } from '../components/photos/photo-filmstrip'
import { PhotoSidebar, useStructureOpen } from '../components/photos/photo-sidebar'
import {
  DEFAULT_EVENT_GAP,
  STRUCTURE_KIND_FIELDS,
  type StructureGroup,
  type StructureKind,
} from '../lib/structure'
import { PhotoTrashDrawer } from '../components/photos/photo-trash-drawer'
import { PhotoViewer } from '../components/photos/photo-viewer'
import classes from '../components/photos/photos-screen.module.css'
import { api } from '../lib/api'
import { configQueries } from '../lib/queries/config'
import { photosApi, photosQueries } from '../lib/queries/photos'
import {
  DEFAULT_FILTERS,
  DEFAULT_PHOTO_LIMIT,
  MAX_WARM_PATHS,
  MAX_WRITE_PATHS,
  PHOTOS_API,
  REJECTED,
  normalizeFilters,
  stripDefaults,
  thumbUrl,
  toQueryParams,
  withQuery,
} from '../lib/photos'
import type {
  EditorKind,
  Facets,
  PhotoFilters,
  PhotoListResponse,
  PhotoRow,
  ThumbTier,
  WarmResult,
} from '../lib/photos'

// ── Search params ────────────────────────────────────────────────────────────

/** Filter set plus the selected photo's path — the complete, linkable cull session. */
export type PhotosSearch = PhotoFilters & {
  /** Absolute path of the selected photo; null when nothing is selected. */
  sel: string | null
  /**
   * The active collection's id — the SCOPE layer — or null for the whole library.
   *
   * Explicit state, and this reverses F1, which derived "which collection am I in" by
   * comparing the current filter values to each saved query. That worked only while
   * selecting a collection meant *replacing* the filters. Now that a collection is a
   * layer you narrow inside, "Keepers" and "Keepers plus a 200mm filter" are different
   * views of the same collection, and no comparison of filter values can tell the second
   * one from an unscoped 200mm view that happens to look similar.
   *
   * Like `sel`, it is dropped by `normalizeFilters`, so it never re-keys the filter object.
   */
  col: string | null
}

/**
 * Search params the top bar no longer exposes, and which are therefore pinned to their
 * defaults rather than read from the URL.
 *
 * `sort` / `order` and `orientation` lost their controls in the filter bar: ordering is
 * the list endpoint's own default (`date_taken` ascending — the same values
 * `normalizeFilters` falls back to), and orientation earned neither the row nor the
 * facet round trip. Blanking the raw values *before* normalisation rather than deleting
 * the fields keeps this independent of `PhotoFilters`' shape, and means an old bookmark
 * carrying `?sort=rating&orientation=portrait` still parses — it is silently normalised
 * back to the default, and `stripDefaults` drops the keys on the next navigation, so the
 * URL self-heals instead of throwing.
 */
const RETIRED_SEARCH_KEYS = { sort: undefined, order: undefined, orientation: undefined }

/**
 * Coerce raw search params into a complete session.
 *
 * The `SearchSchemaInput` intersection declares the *input* as partial, which is what
 * lets `navigate({ search: { root: 'final' } })` typecheck — everything else falls back
 * to its default rather than having to be restated at every call site.
 */
function validatePhotosSearch(raw: Partial<PhotosSearch> & SearchSchemaInput): PhotosSearch {
  const source = raw as Record<string, unknown>
  const sel = typeof source.sel === 'string' && source.sel.length > 0 ? source.sel : null
  const col = typeof source.col === 'string' && source.col.length > 0 ? source.col : null
  return { ...normalizeFilters({ ...source, ...RETIRED_SEARCH_KEYS }), sel, col }
}

export const Route = createFileRoute('/photos')({
  validateSearch: validatePhotosSearch,
  loaderDeps: ({ search }: { search: PhotosSearch }) => ({
    filters: normalizeFilters(search),
    collection: search.col,
  }),
  // Deliberately NOT awaited, unlike the house `ensureQueryData` idiom: this is a prewarm,
  // not a gate. Awaiting would suspend navigation on every filter tweak and unpaint the
  // frame being culled; the component reads the same keys with `keepPreviousData` instead.
  loader: ({ context, deps }) => {
    void context.queryClient.ensureQueryData(photosQueries.list(deps.filters, deps.collection))
    void context.queryClient.ensureQueryData(photosQueries.facets(deps.filters, deps.collection))
  },
  component: PhotosPage,
})

// ── Constants ────────────────────────────────────────────────────────────────

const FILMSTRIP_HEIGHT = 82

/**
 * The prewarm ring, in frames, measured from the settled selection.
 *
 * Asymmetric on purpose. Culling is a directional activity: someone stepping forward
 * through a shoot gets nothing from eight frames behind them, and the ring is not free —
 * a cold frame costs ~79 ms of parallel server capacity, i.e. roughly 12 frames/second.
 * Spending that budget in the direction of travel is what keeps the user ahead of it,
 * and the short tail behind covers the one-or-two-frame back-check after a rating.
 */
const WARM_AHEAD = 12
const WARM_BEHIND = 3
/** Frames decoded in-page around the selection, so a single step is already resolved. */
const PRELOAD_AHEAD = 3
const PRELOAD_BEHIND = 1
/**
 * Decoded-image cache size, in frames.
 *
 * Sized to cover back-stepping well past the {@link PRELOAD_AHEAD} ring, so a re-check of
 * something rated a moment ago repaints from memory rather than refetching.
 *
 * **It is not cheap, and the cost is not the file size.** A `view` frame is a ~450 KB JPEG
 * on the wire but a 1365x2048 bitmap once decoded — 11.2 MB at 4 bytes/px. Measured on this
 * library (Chrome 151, 32 GB): holding 24 of them moves renderer RSS by **~116 MB**, i.e.
 * ~4.8 MB resident per frame once the browser's own compaction and purging are accounted
 * for. Read any change to this constant as tens of megabytes, not as a count.
 */
const LRU_CAP = 24

/**
 * Both tiers in one request.
 *
 * The server generates them from a SINGLE source decode (the ~117 ms that dominates the
 * cost), so grid+view together run ~1.3x one tier rather than 2x — and that one pass
 * leaves both the filmstrip cell and the viewer's placeholder/sharp pair hot.
 */
const WARM_TIERS: readonly ThumbTier[] = ['grid', 'view']

/**
 * How long the selection must sit still before the ring is warmed.
 *
 * Key-repeat is far faster than the ~12 frames/sec the generator sustains, so firing per
 * step would queue work for frames the user has already blown past. Warming only once the
 * selection settles keeps the queue pointed at where the user actually stopped; holding
 * the key still looks smooth because the viewer's grid-tier placeholder never waits on it.
 */
const WARM_DEBOUNCE_MS = 140
const SELECTION_URL_DEBOUNCE_MS = 250
const UNDO_TOAST_MS = 8000
/** Facet counts trail a burst of ratings by this much rather than recomputing per keypress. */
const FACETS_INVALIDATE_DEBOUNCE_MS = 800

/**
 * Full-bleed inside `AppShell.Main`.
 *
 * Every other screen in the panel is a page of cards and wants the shell's gutter. This one
 * is a viewer: the gutter is 13px of dead surface on all four sides framing the one thing
 * the screen exists to show, and the filmstrip should run to the window edge rather than
 * stop short of it. `AppShell.Main` pads with
 * `{header,footer,navbar}-offset + --app-shell-padding` (Mantine's own rule), so a negative
 * margin of exactly `--app-shell-padding` cancels the gutter and leaves the offsets — the
 * content still clears the header, the navbar and the mobile footer.
 *
 * **The width is deliberately left at `auto`.** It was `calc(100% + var(--app-shell-padding) *
 * 2)`, which is arithmetically identical and wrong in practice: it resolves the container width
 * and the two gutters separately and adds them, so on a display running a fractional scale
 * factor (any "More Space" Mac) the two roundings do not cancel and the box lands a device
 * pixel or two short of the right edge — a thin dark band against the window frame, on the
 * right only, because the LEFT edge is fixed by the margin rather than by the sum. A block box
 * with `width: auto` derives both edges from the container instead, so a negative margin widens
 * it by exactly the gutter with no second rounding.
 *
 * The height is pinned rather than left at `100%` (which does not resolve against
 * `min-height: 100dvh`), and is now the *padded* box: the two gutters it used to subtract
 * are exactly what the bleed reclaims.
 */
const CONTENT_HEIGHT =
  'calc(100dvh - var(--app-shell-header-offset, 0rem) - var(--app-shell-footer-offset, 0rem))'
const BLEED_MARGIN = 'calc(var(--app-shell-padding) * -1)'

/**
 * Hard ceiling on the marked set while the contact sheet is open.
 *
 * It is the server's own `MAX_WRITE_PATHS`, not a number chosen here: the only reason to
 * mark 400 frames is to write to them, and a selection larger than the write endpoint
 * accepts is a selection whose whole purpose 413s. Shift-selecting past it truncates from
 * the anchor outward, so the frame you started on is always one of the ones you get.
 */
const MAX_MARKED = MAX_WRITE_PATHS

/**
 * Above this many frames, a star-rating broadcast asks first — how many, and how many
 * already carry a different rating.
 *
 * A normal cull touches one photo at a time; the sheet exists precisely to make marking
 * dozens of frames at once easy, which is also what makes one wrong keystroke there
 * capable of rewriting hundreds of masters in a single call. This is not a batch-size
 * ceiling (that is {@link MAX_MARKED}) — it is the line past which a broadcast stops
 * being an obviously-intended action and starts being one keystroke away from an
 * accident, so a small, deliberately low number is the right choice, not a tuned one.
 */
const RATING_CONFIRM_THRESHOLD = 20

/**
 * How long the visible window must sit still before the sheet's thumbnails are warmed.
 *
 * Longer than {@link WARM_DEBOUNCE_MS}, and for the opposite reason. The viewer's ring is
 * small and directional, so warming early is cheap; a grid viewport is ~130 frames, so
 * warming during a flick would queue several hundred generations for rows that scrolled
 * past before the first one landed.
 */
const GRID_WARM_DEBOUNCE_MS = 220

/**
 * The density stop closest to an arbitrary value.
 *
 * A persisted density from an older `DENSITY_STEPS` (or a hand-edited localStorage entry)
 * must still be steppable rather than snapping the sheet to the smallest cell.
 *
 * @param value Current cell width in px.
 * @returns Index into `DENSITY_STEPS`.
 */
function nearestDensityIndex(value: number): number {
  let best = 0
  for (let at = 1; at < DENSITY_STEPS.length; at += 1) {
    const here = DENSITY_STEPS[at]
    const champion = DENSITY_STEPS[best]
    if (here === undefined || champion === undefined) continue
    if (Math.abs(here - value) < Math.abs(champion - value)) best = at
  }
  return best
}

/** Empty, stable identity — a fresh `[]` per render would churn every effect below. */
const NO_ROWS: PhotoRow[] = []

const usePhotosSidebarOpen = createPersistedState({
  key: 'photos-sidebar-open',
  version: 1,
  initial: true,
})
const usePhotosStripOpen = createPersistedState({
  key: 'photos-filmstrip-open',
  version: 1,
  initial: true,
})
/**
 * Whether the stage is the contact sheet rather than a single frame.
 *
 * Persisted like every other way of LOOKING at the library (filmstrip, structure kind),
 * and deliberately not in the URL: a shared link should resolve to a set of photographs,
 * not to how the recipient's screen is arranged.
 */
const usePhotosGridOpen = createPersistedState({
  key: 'photos-grid-open',
  version: 1,
  initial: false,
})
/** Target cell width of the contact sheet, in px. See `DENSITY_STEPS`. */
const usePhotosGridDensity = createPersistedState({
  key: 'photos-grid-density',
  version: 1,
  initial: DEFAULT_DENSITY,
})
const usePhotosShowTrashed = createPersistedState({
  key: 'photos-show-trashed',
  version: 1,
  initial: false,
})
const usePhotosShowRejected = createPersistedState({
  key: 'photos-show-rejected',
  version: 1,
  initial: false,
})
/**
 * Whether an explicit zoom may fetch the master's own bytes instead of magnifying the
 * 2048 px `view` proxy.
 *
 * Default ON, because the alternative is a viewer whose "1:1" is a 33–39 % proxy upscaled
 * — a claim about sharpness made out of pixels that cannot carry it. The switch stays
 * because it is the A/B control for exactly that question: turn it off and the same zoom
 * shows what the panel showed before F5.
 */
const usePhotosTrueResolution = createPersistedState({
  key: 'photos-true-resolution',
  version: 1,
  initial: true,
})
// Which candidate library layout the Structure section is showing, and — for `event` —
// the silence that ends one. Persisted rather than put in the URL: it is a way of LOOKING
// at the library, like the filmstrip toggle, not part of the query the URL has to
// reproduce. The rows a structure group selects DO land in the URL, as an ordinary date
// or keyword filter, so a link still resolves to the same photos.
const usePhotosStructureKind = createPersistedState<StructureKind>({
  key: 'photos-structure-kind',
  version: 1,
  initial: 'month',
})
const usePhotosEventGap = createPersistedState({
  key: 'photos-structure-event-gap',
  version: 1,
  initial: DEFAULT_EVENT_GAP,
})

// ── Transport the shared factory does not cover ──────────────────────────────

/**
 * `photosQueries.list`, plus the soft-deleted rows.
 *
 * `include_trashed` is a *view* preference, not a filter: it is persisted rather than
 * living in the URL, so it cannot ride inside `PhotoFilters` and therefore cannot be
 * expressed by the shared factory. The key is the factory's own key with a suffix, which
 * keeps the `['photos', 'list']` prefix that every invalidation in this file targets, and
 * keeps the "off" case byte-identical to what the route loader prefetches.
 */
type PhotoListQuery = {
  /** Widened from the factory's tuple: the two branches differ in arity, and a union of
   *  tuple keys defeats `useQuery`'s overload resolution for no benefit here. */
  queryKey: readonly unknown[]
  queryFn: () => Promise<PhotoListResponse>
  staleTime: number
}

function listQueryOptions(
  filters: PhotoFilters,
  collection: string | null,
  includeTrashed: boolean,
  includeRejected: boolean,
): PhotoListQuery {
  const base = photosQueries.list(filters, collection)
  if (!includeTrashed && !includeRejected) return base
  const params = toQueryParams(filters)
  if (collection !== null) params.set('collection', collection)
  params.set('limit', String(DEFAULT_PHOTO_LIMIT))
  params.set('offset', '0')
  if (includeTrashed) params.set('include_trashed', 'true')
  if (includeRejected) params.set('include_rejected', 'true')
  return {
    ...base,
    // Both flags ride the key so the four combinations cannot collide in the cache.
    queryKey: [...base.queryKey, includeTrashed, includeRejected] as const,
    queryFn: () => api.get<PhotoListResponse>(withQuery(PHOTOS_API, params)),
  }
}

/**
 * Fire-and-forget prewarm of BOTH thumbnail tiers (see `WARM_TIERS`).
 *
 * `photosApi.warm` still speaks the endpoint's legacy single-`tier` form, which the server
 * honours verbatim; this is the multi-tier body. Failures are swallowed on purpose — a
 * prewarm that did not happen costs latency, never correctness.
 */
function warmBothTiers(paths: string[]): void {
  const batch = paths.slice(0, MAX_WARM_PATHS)
  if (batch.length === 0) return
  void api
    .post<WarmResult>(`${PHOTOS_API}/warm`, undefined, { paths: batch, tiers: WARM_TIERS })
    .catch(() => undefined)
}

// ── Page ─────────────────────────────────────────────────────────────────────

type Selection = {
  /** The selected photo's path, or null when the result set is empty. */
  path: string | null
  /** Last resolved index — the fallback when the selected path leaves the result set. */
  index: number
}

/**
 * The last action ⌘Z can invert — a trash move or a rating write.
 *
 * A trash entry is undone by restoring its ids; a rating write is undone by writing
 * each touched path back to the rating it held before, which is exactly what
 * `RatingWriteResult.previous` was captured for. Only one slot: doing another
 * undoable thing supersedes whatever came before it, the same single-level undo the
 * trash toast already offered.
 */
type UndoableAction =
  | { kind: 'trash'; ids: number[] }
  | { kind: 'rating'; items: { path: string; rating: number }[] }

function PhotosPage() {
  const search = Route.useSearch()
  const navigate = Route.useNavigate()
  const queryClient = useQueryClient()

  // `normalizeFilters` drops `sel`, so the filter object (and therefore the query key)
  // is untouched by a selection change — stepping never refetches the list.
  const filters = useMemo(() => normalizeFilters(search), [search])
  // The scope layer. Not part of `filters` on purpose: it names a LAYER, not a dimension,
  // so folding it in would make `activeFilterCount` count it as a filter and let
  // `clearFilters` throw the user out of the collection they were culling.
  const scope = search.col

  // Declared up here rather than with the other view prefs below because it is part of the
  // list query key — a cull pass reviewed with its trashed frames still in place is a
  // different result set, not a different rendering of the same one.
  const [showTrashed, setShowTrashed] = usePhotosShowTrashed()
  const [showRejected, setShowRejected] = usePhotosShowRejected()
  const [structureKind, setStructureKind] = usePhotosStructureKind()
  const [structureOpen] = useStructureOpen()
  const [eventGap, setEventGap] = usePhotosEventGap()

  const listOptions = listQueryOptions(filters, scope, showTrashed, showRejected)
  const listQuery = useQuery({ ...listOptions, placeholderData: keepPreviousData })
  const rejectsQuery = useQuery(photosQueries.rejects(filters.root))
  // Which external applications are configured, per file kind — drives the Tools section
  // and the right-click menu. Read-only chrome data, not part of the cull session.
  const configQuery = useQuery(configQueries.config())
  const jpegEditors = useMemo(
    () => configQuery.data?.install.editors.filter((e) => e.handles.includes('jpeg')) ?? [],
    [configQuery.data],
  )
  const rawEditors = useMemo(
    () => configQuery.data?.install.editors.filter((e) => e.handles.includes('raw')) ?? [],
    [configQuery.data],
  )
  const facetsQuery = useQuery({
    ...photosQueries.facets(filters, scope, showRejected),
    placeholderData: keepPreviousData,
  })
  // The readout of what is narrowing the result set. Its own query, deliberately: it is
  // chrome for a collapsible card and must not make the two queries the photographs
  // depend on any heavier.
  const narrowingQuery = useQuery({
    ...photosQueries.narrowing(filters, scope, showRejected),
    placeholderData: keepPreviousData,
  })
  // Both folder counts, each asked for EXPLICITLY.
  //
  // This used to be one facet query plus arithmetic (`splitRootCounts`): with no root
  // filter the page's own count was the sum of the two roots, so the second was the
  // first subtracted from it. That shortcut assumed nothing narrowed the library ABOVE
  // the rail — and a scope does. Inside "Keepers" (`root=final, rating>=4`) the page's
  // count is already Final-only, so the subtraction produced Staging = 0 where the
  // composed answer is 11. The arithmetic could not be patched: with a scope in play
  // there is no expression for "the other root" that does not involve asking.
  //
  // Each of these keys is also the exact key the list uses once that folder is clicked,
  // so a click is served from cache. Cost is one extra facet computation (~22 ms on the
  // real 3 800-row index) per filter change — and none per arrow key, since stepping the
  // selection does not re-key the filters.
  const finalCountQuery = useQuery({
    ...photosQueries.facets({ ...filters, root: 'final' }, scope, showRejected),
    select: (facets: Facets) => facets.count,
  })
  const stagingCountQuery = useQuery({
    ...photosQueries.facets({ ...filters, root: 'staging' }, scope, showRejected),
    select: (facets: Facets) => facets.count,
  })
  // "All" is NOT final + staging. It means "clear the root REFINE", which hands the axis
  // back to the scope — inside Keepers (`root=final`) that is 1 947, not the 3 080 the sum
  // gives. When no root refine is active this is the page's own facet key, so it costs
  // nothing; the extra request only happens while a folder is actually selected.
  const allCountQuery = useQuery({
    ...photosQueries.facets({ ...filters, root: null }, scope, showRejected),
    select: (facets: Facets) => facets.count,
  })
  // The Stage F3 experiment: the four candidate layouts, each as a list of queries.
  // Its own query rather than a slice of the facets, because switching kinds must not
  // re-fetch anything the photographs depend on.
  const structureQuery = useQuery({
    ...photosQueries.structure(filters, scope, structureKind, eventGap, showRejected),
    placeholderData: keepPreviousData,
    // Only while the section is open — see `useStructureOpen`.
    enabled: structureOpen,
  })

  const rootCounts = {
    all: allCountQuery.data,
    final: finalCountQuery.data,
    staging: stagingCountQuery.data,
  }

  const listKey = listOptions.queryKey
  const rows = listQuery.data?.items ?? NO_ROWS
  const total = listQuery.data?.total ?? 0

  // ── Selection ──────────────────────────────────────────────────────────────

  const [selection, setSelection] = useState<Selection>({ path: search.sel, index: 0 })

  const selectedIndex = (() => {
    if (rows.length === 0) return -1
    if (selection.path !== null) {
      const found = rows.findIndex((row) => row.path === selection.path)
      if (found >= 0) return found
    }
    return Math.min(Math.max(selection.index, 0), rows.length - 1)
  })()
  const selectedRow = selectedIndex < 0 ? null : (rows[selectedIndex] ?? null)

  // Reconcile: when the selected path leaves the result set (filter change, trash, a
  // reload with a stale `sel`), the index fallback above picks a neighbour — write that
  // back so the URL, the filmstrip and the preloader all agree on one selection.
  useEffect(() => {
    const path = selectedRow?.path ?? null
    // An empty result set has no index to remember — keep the last one so re-widening the
    // filters lands near where the user was. (Writing -1 back here would also never settle:
    // `selectedIndex` stays -1, so every render would re-set a fresh object and loop.)
    const index = selectedIndex < 0 ? selection.index : selectedIndex
    if (path === selection.path && index === selection.index) return
    setSelection({ path, index })
  }, [selectedRow, selectedIndex, selection.path, selection.index])

  const select = useCallback(
    (index: number): void => {
      const row = rows[index]
      if (row === undefined) return
      setSelection({ path: row.path, index })
    },
    [rows],
  )

  const step = useCallback(
    (delta: number): void => {
      if (rows.length === 0) return
      const next = Math.min(Math.max(selectedIndex + delta, 0), rows.length - 1)
      select(next)
    },
    [rows.length, selectedIndex, select],
  )

  // ── The contact sheet ──────────────────────────────────────────────────────

  // Declared above the marked set because `pick` reads `markCap`, and `markCap` is the
  // one place the grid changes what a selection gesture MEANS.
  const [gridOpen, setGridOpen] = usePhotosGridOpen()
  const [density, setDensity] = usePhotosGridDensity()
  /** Columns the sheet resolved for the current viewport; the route's Up/Down step by this. */
  const [gridColumns, setGridColumns] = useState(1)

  /**
   * How many frames may be marked at once.
   *
   * Four in the viewer, because there the set is a compare *layout* and the fifth frame
   * costs more than it buys (see `MAX_COMPARE`). {@link MAX_MARKED} in the grid, because
   * there the set is a *batch* and its natural ceiling is the server's own write cap.
   */
  const markCap = gridOpen ? MAX_MARKED : MAX_COMPARE

  // ── The marked set ─────────────────────────────────────────────────────────

  /**
   * The marked frames, as paths, in the order the user picked them.
   *
   * **One set, two readers, and that is the F6 correction to F5.** F5 called this "the
   * compare set" and capped it at {@link MAX_COMPARE}, which conflated a *selection* with
   * the layout of one view of it. The grid needs the same gesture vocabulary (plain /
   * Shift / Cmd) over sets far larger than four, and building it a second `Set<string>`
   * would have put two notions of "selected" on one screen. So the set is uncapped-ish
   * (see {@link MAX_MARKED}), the compare stage renders its first `MAX_COMPARE` members,
   * and the cap is a property of that view rather than of the selection.
   *
   * The consequence is deliberate and visible: mark nine frames in the grid, leave it, and
   * the compare stage says "4 of 9" rather than silently dropping five.
   *
   * Paths and not indices: the list re-sorts, re-filters and re-fetches under the user,
   * and an index survives none of that. Order is preserved rather than normalised to list
   * order because for the compare stage the set is a *layout* — the frame you put on the
   * left should stay on the left while you look at it.
   *
   * Deliberately NOT in the URL. Filters and the focused photo are the session; a
   * two-second comparison is not, and putting it in the URL would either debounce (another
   * timer) or push a history entry per Shift+Arrow.
   */
  const [marked, setMarked] = useState<string[]>([])
  /** Where a Shift-range starts. Set by every plain click and by entering compare mode. */
  const anchorRef = useRef(0)

  const markedRows = useMemo(() => {
    if (marked.length === 0) return NO_ROWS
    const byPath = new Map(rows.map((row) => [row.path, row]))
    return marked
      .map((path) => byPath.get(path))
      .filter((row): row is PhotoRow => row !== undefined)
  }, [marked, rows])

  const markedIndices = useMemo(() => {
    const indices = new Set<number>()
    if (marked.length === 0) return indices
    const wanted = new Set(marked)
    rows.forEach((row, index) => {
      if (wanted.has(row.path)) indices.add(index)
    })
    return indices
  }, [marked, rows])

  // Prune members that have left the result set — a filter change, or a purge. Guarded on a
  // non-empty list so the momentary `NO_ROWS` of a first load does not silently empty the set.
  useEffect(() => {
    if (marked.length === 0 || rows.length === 0) return
    const present = new Set(rows.map((row) => row.path))
    const next = marked.filter((path) => present.has(path))
    if (next.length !== marked.length) setMarked(next)
  }, [rows, marked])

  /**
   * What the compare stage actually shows: the first {@link MAX_COMPARE} marked frames.
   *
   * A truncation, and a visible one — `PhotoSidebar` reports "4 of 9" rather than letting
   * the other five disappear. The alternative, capping the set itself whenever the grid is
   * closed, silently discards a selection the user built on the other surface.
   */
  const compareRows = useMemo(
    () => (markedRows.length > MAX_COMPARE ? markedRows.slice(0, MAX_COMPARE) : markedRows),
    [markedRows],
  )

  // The grid is a stage of its own, so it is never *also* comparing: two frames marked
  // there is a batch of two, not a 2-up.
  const comparing = !gridOpen && compareRows.length >= 2

  /**
   * One handler for every way a frame gets picked, keyboard and mouse alike.
   *
   * Plain click replaces (and leaves compare mode), Shift extends a range from the anchor,
   * Cmd/Ctrl toggles one frame. The two modifiers are the conventions every file browser
   * already teaches; what is worth writing down is that a range is truncated at
   * {@link MAX_COMPARE} *from the anchor outward*, so the frame you started from is always
   * one of the ones you get.
   */
  const pick = useCallback(
    (index: number, mods: SelectMods): void => {
      const row = rows[index]
      if (row === undefined) return

      if (mods.range) {
        const from = Math.min(Math.max(anchorRef.current, 0), rows.length - 1)
        const heading = index >= from ? 1 : -1
        const paths: string[] = []
        for (
          let cursor = from;
          paths.length < markCap && (heading > 0 ? cursor <= index : cursor >= index);
          cursor += heading
        ) {
          const member = rows[cursor]
          if (member !== undefined) paths.push(member.path)
        }
        const last = paths[paths.length - 1]
        setMarked(paths.length > 1 ? paths : [])
        if (last !== undefined) {
          setSelection({ path: last, index: rows.findIndex((candidate) => candidate.path === last) })
        }
        return
      }

      if (mods.toggle) {
        setMarked((previous) => {
          if (previous.includes(row.path)) {
            const next = previous.filter((path) => path !== row.path)
            // In the grid a set of one is a legitimate state — it is "this photo and no
            // other", the thing a batch write acts on. In the single viewer it is not: a
            // comparison of one is just the viewer, so the set collapses to empty there.
            return next.length > 1 || markCap > MAX_COMPARE ? next : []
          }
          // The first Cmd-click starts the set from what is already on screen — otherwise
          // "add this one" would produce a comparison of one.
          const base =
            previous.length > 0
              ? previous
              : selection.path !== null && selection.path !== row.path
                ? [selection.path]
                : []
          if (base.length >= markCap) return previous
          return [...base, row.path]
        })
        setSelection({ path: row.path, index })
        return
      }

      anchorRef.current = index
      setMarked([])
      setSelection({ path: row.path, index })
    },
    [rows, selection.path, markCap],
  )

  /** Grow or shrink the marked set by one frame, from the keyboard. */
  const extendMarked = useCallback(
    (delta: number): void => {
      if (rows.length === 0 || selectedIndex < 0) return
      if (marked.length === 0) anchorRef.current = selectedIndex
      const next = Math.min(Math.max(selectedIndex + delta, 0), rows.length - 1)
      pick(next, { range: true, toggle: false })
    },
    [rows.length, selectedIndex, marked.length, pick],
  )

  /**
   * `C` — enter or leave the comparison.
   *
   * Entering makes a 2-up out of the focused frame and its neighbour, because a burst is
   * consecutive and the pair you want is almost always adjacent. Everything else is one
   * Shift+Arrow away.
   */
  const toggleCompare = useCallback((): void => {
    if (comparing) {
      setMarked([])
      return
    }
    if (selectedIndex < 0) return
    anchorRef.current = selectedIndex
    const partner = rows[selectedIndex + 1] ?? rows[selectedIndex - 1]
    const current = rows[selectedIndex]
    if (partner === undefined || current === undefined) return
    setMarked(
      partner === rows[selectedIndex + 1]
        ? [current.path, partner.path]
        : [partner.path, current.path],
    )
  }, [comparing, rows, selectedIndex])

  /** Move the focus within the comparison, wrapping. Plain arrows do this while comparing. */
  const cycleFocus = useCallback(
    (delta: number): void => {
      if (compareRows.length < 2) return
      const current = compareRows.findIndex((row) => row.path === selection.path)
      const from = current < 0 ? 0 : current
      const next = (from + delta + compareRows.length) % compareRows.length
      const row = compareRows[next]
      if (row === undefined) return
      setSelection({
        path: row.path,
        index: rows.findIndex((candidate) => candidate.path === row.path),
      })
    },
    [compareRows, rows, selection.path],
  )

  // Mirror the selection into the URL, debounced: arrow-stepping a 2 000-frame shoot at
  // one `replaceState` per key would hit Safari's history throttle within seconds.
  // `stripDefaults` runs on the way out because the router serialises whatever the object
  // carries — spreading the fully-normalised `prev` would write every unset filter into the
  // URL as `iso_min=null&rating=[]&…` on the first arrow key.
  const commitSelectionToUrl = useDebouncedCallback((path: string | null) => {
    void navigate({
      search: (prev) => ({
        ...stripDefaults(normalizeFilters(prev)),
        // `stripDefaults` only knows about `PhotoFilters`, so the scope has to be carried
        // across by hand or every arrow key would drop the user out of their collection.
        ...(typeof (prev as { col?: string | null }).col === 'string'
          ? { col: (prev as { col: string }).col }
          : {}),
        ...(path === null ? {} : { sel: path }),
      }),
      replace: true,
    })
  }, SELECTION_URL_DEBOUNCE_MS)

  useEffect(() => {
    commitSelectionToUrl(selection.path)
  }, [selection.path, commitSelectionToUrl])

  // ── Prefetch: warm the server cache, decode the immediate neighbours ────────

  const lruRef = useRef(new Map<string, HTMLImageElement>())

  /**
   * Drop one entry, abandoning its fetch if it never finished.
   *
   * `lru.delete(url)` alone releases only OUR reference. An entry evicted while its request
   * is still in flight keeps that request alive: it holds one of the browser's six
   * connections to completion and then decodes a ~450 KB `view` frame nobody is waiting for.
   * A fast walk evicts exactly those entries — the ring refills faster than a cold frame
   * lands — which is what put the measured peak ~200 MB above the same walk's settled
   * footprint. Clearing `src` is the identical abort `PhotoViewer` already performs on its
   * own superseded loads.
   */
  const release = useCallback((lru: Map<string, HTMLImageElement>, url: string): void => {
    const image = lru.get(url)
    if (image !== undefined && !image.complete) image.src = ''
    lru.delete(url)
  }, [])

  const preload = useCallback(
    (url: string): void => {
      const lru = lruRef.current
      const cached = lru.get(url)
      if (cached !== undefined) {
        // Re-insert to mark it most-recently-used.
        lru.delete(url)
        lru.set(url, cached)
        return
      }
      const image = new Image()
      image.decoding = 'async'
      image.src = url
      void image.decode().catch(() => undefined)
      lru.set(url, image)
      while (lru.size > LRU_CAP) {
        const oldest = lru.keys().next().value
        if (oldest === undefined) break
        release(lru, oldest)
      }
    },
    [release],
  )

  /**
   * Hand the ring back when the screen goes away.
   *
   * Leaving `/photos` otherwise strands up to {@link LRU_CAP} decoded frames — ~116 MB
   * measured — reachable until the component graph itself is collected, which on a route
   * swap is whenever the collector next feels like it rather than at navigation. Any entry
   * still loading is aborted rather than left to land into a cache no one will read.
   */
  useEffect(() => {
    const lru = lruRef.current
    return () => {
      // Safe to delete while iterating: each key is dropped as it is visited, and a Map
      // iterator only skips entries removed BEFORE it reaches them.
      for (const url of lru.keys()) release(lru, url)
    }
  }, [release])

  /**
   * Everything that must NOT run per keystroke, run once the selection stops moving.
   *
   * Both halves are coalesced together deliberately: the in-page decodes compete with the
   * viewer's own request for the browser's six-connection budget, so firing them mid-repeat
   * would delay the one frame the user is actually looking at.
   */
  const warmSettled = useDebouncedCallback((batch: { decode: string[]; warm: string[] }) => {
    for (const url of batch.decode) preload(url)
    warmBothTiers(batch.warm)
  }, WARM_DEBOUNCE_MS)

  /** +1 while stepping forward, -1 backward; sticky, so a click or a re-render keeps it. */
  const directionRef = useRef(1)
  const lastIndexRef = useRef<number | null>(null)

  useEffect(() => {
    if (selectedIndex < 0) return

    const previous = lastIndexRef.current
    if (previous !== null && previous !== selectedIndex) {
      directionRef.current = selectedIndex > previous ? 1 : -1
    }
    lastIndexRef.current = selectedIndex

    /**
     * The sheet is on screen, so the ring's whole premise is gone.
     *
     * `warmGridWindow` reshapes the *sheet's* prefetch correctly, but this effect was left
     * running underneath it, and the two are not the same shape. Measured in the running
     * panel with the sheet open at 236 px cells: every settled step fired a SECOND warm
     * POST — 15 paths at BOTH tiers — and decoded up to four `view` frames (2.1 MB encoded
     * over two keystrokes, ~4.8 MB resident each) into an LRU capped at 24, i.e. up to
     * ~116 MB of full-size bitmaps held for a screen whose entire visible content is ~4.7 MB
     * of 320 px thumbnails. None of it is on screen and almost none of it is ever opened.
     *
     * What the ring still legitimately owes the sheet is ONE frame: the focused one, which
     * is what `Enter` opens. Warming it server-side is enough — the viewer paints the cheap
     * `grid` tier instantly and upgrades, so the upgrade only has to be a warm 3 ms fetch
     * instead of a 321 ms cold generation (both measured). Decoding it here in advance buys
     * a fraction of that and costs 4.8 MB a step, so the sheet warms and does not decode.
     */
    if (gridOpen) {
      const focused = rows[selectedIndex]
      warmSettled({ decode: [], warm: focused === undefined ? [] : [focused.path] })
      return
    }

    // Offsets are expressed in the direction of travel, so one loop covers both headings.
    const heading = directionRef.current
    const at = (offset: number): PhotoRow | undefined => rows[selectedIndex + offset * heading]

    const decode: string[] = []
    const warm: string[] = []
    const collect = (ahead: number, behind: number, sink: (row: PhotoRow) => void): void => {
      for (let offset = 1; offset <= ahead; offset += 1) {
        const row = at(offset)
        if (row !== undefined) sink(row)
      }
      for (let offset = 1; offset <= behind; offset += 1) {
        const row = at(-offset)
        if (row !== undefined) sink(row)
      }
    }
    collect(PRELOAD_AHEAD, PRELOAD_BEHIND, (row) => decode.push(thumbUrl(row, 'view')))
    collect(WARM_AHEAD, WARM_BEHIND, (row) => warm.push(row.path))

    warmSettled({ decode, warm })
  }, [rows, selectedIndex, warmSettled, gridOpen])

  /**
   * Warm what the contact sheet is showing.
   *
   * **The 1-D ring above does not survive the change of shape, and this is the evidence.**
   * `warmSettled` walks a ±15-frame ring around one focus; a sheet at 200 px cells on this
   * stage mounts ~130 frames at once and a single flick replaces all of them. Pointing the
   * ring at the focus would warm 15 of 130 and leave the other 115 to be generated by the
   * `<img>` requests that display them — which is the cold-cache stall the ring exists to
   * prevent, at eight times the scale.
   *
   * So the sheet reports its own window and this warms exactly that, in `MAX_WARM_PATHS`
   * chunks, debounced. It is a different *shape* of prefetch, not a different mechanism:
   * the endpoint, the tiers and the cache are the same ones the strip uses.
   *
   * One tier, and it is the tier the sheet reports rather than a constant: at its densest
   * that is `grid`, and at its coarsest the cells have outgrown `grid` and are drawing
   * `view` (see `UPGRADE_RATIO`). Warming both unconditionally would multiply a 130-frame
   * warm by a ~425 KB frame nobody on this screen is looking at.
   */
  const warmGridWindow = useDebouncedCallback((paths: string[], tier: ThumbTier) => {
    for (let at = 0; at < paths.length; at += MAX_WARM_PATHS) {
      const batch = paths.slice(at, at + MAX_WARM_PATHS)
      void api
        .post<WarmResult>(`${PHOTOS_API}/warm`, undefined, { paths: batch, tiers: [tier] })
        .catch(() => undefined)
    }
  }, GRID_WARM_DEBOUNCE_MS)

  // ── Writes ─────────────────────────────────────────────────────────────────

  /**
   * Invalidation here is deliberately narrow, because a cull is a keypress-rate write loop.
   * Blanket-invalidating `['photos']` per star refetched the whole list (~1 MB of rows), all
   * facet queries and the selected row's `meta` — an exiftool spawn — for a change the
   * optimistic `setQueryData` had already applied correctly.
   *
   * What is actually stale after a rating / label / trash:
   * - the **list**: nothing. The patch above holds the post-write state, and a rating changes
   *   no row's existence. It is refetched only when the write did NOT land as assumed
   *   (transport error, or a partial `errors > 0`), which is when the patch is the lie.
   * - the **facets**: yes — rating and label counts move. Debounced, since rating four photos
   *   in a second must not queue four facet recomputations.
   * - the **trash list**: only a trash / restore touches it.
   * - **meta**: no. The info panel reads the star from the patched row; only its exiftool dump
   *   would differ, and that re-reads on the next selection anyway.
   */
  const invalidateList = useCallback((): void => {
    void queryClient.invalidateQueries({ queryKey: ['photos', 'list'] })
  }, [queryClient])

  const invalidateTrashList = useCallback((): void => {
    void queryClient.invalidateQueries({ queryKey: ['photos', 'trash'] })
  }, [queryClient])

  const invalidateRejects = useCallback((): void => {
    void queryClient.invalidateQueries({ queryKey: ['photos', 'rejects'] })
  }, [queryClient])

  const invalidateFacets = useDebouncedCallback(
    (): void => {
      void queryClient.invalidateQueries({ queryKey: ['photos', 'facets'] })
    },
    // Leaving the screen mid-burst must still mark the counts stale — the default cancels
    // the pending call, which would strand them until the 30 s staleTime elapses.
    { delay: FACETS_INVALIDATE_DEBOUNCE_MS, flushOnUnmount: true },
  )

  /** See {@link UndoableAction} — set by a trash purge or a rating write, read by ⌘Z. */
  const lastActionRef = useRef<UndoableAction | null>(null)

  /** Optimistically patch the rows of the current list page. */
  const patchRows = useCallback(
    (patch: (items: PhotoRow[]) => PhotoRow[]): PhotoListResponse | undefined => {
      const previous = queryClient.getQueryData<PhotoListResponse>(listKey)
      queryClient.setQueryData<PhotoListResponse>(listKey, (old) => {
        if (old === undefined) return old
        const items = patch(old.items)
        return { ...old, items, total: old.total - (old.items.length - items.length) }
      })
      return previous
    },
    [queryClient, listKey],
  )

  const rateMutation = useMutation({
    mutationFn: ({ paths, rating }: { paths: string[]; rating: number }) =>
      photosApi.setRating(paths, rating),
    onMutate: async ({ paths, rating }) => {
      await queryClient.cancelQueries({ queryKey: listKey })
      const targets = new Set(paths)
      const previous = patchRows((items) =>
        items.map((row) => (targets.has(row.path) ? { ...row, rating } : row)),
      )
      return { previous }
    },
    onError: (error: Error, _variables, context) => {
      if (context?.previous !== undefined) queryClient.setQueryData(listKey, context.previous)
      notifyError(error.message, { title: 'Rating failed' })
      invalidateList()
      invalidateFacets()
    },
    onSuccess: (result, variables) => {
      invalidateFacets()
      // A rating write is also the only way the reject count moves, in either direction.
      invalidateRejects()
      if (result.errors > 0) {
        notifyError(result.messages.join(' · ') || 'Some ratings were not written.', {
          title: 'Rating incomplete',
        })
        invalidateList()
        // A partial write means the batch was one exiftool process over ALL paths, so a
        // failure cannot be attributed to any specific subset — offering an undo here
        // would risk "restoring" paths that were never actually touched. No undo target
        // is honest; a wrong one is not.
        return
      }
      // The server read every path's prior rating before writing (`previous`) — this is
      // what makes ⌘Z a real inverse rather than a confirmation-only speed bump.
      const items = variables.paths.flatMap((path) => {
        const prior = result.previous[path]
        return prior === undefined ? [] : [{ path, rating: prior }]
      })
      if (items.length > 0) lastActionRef.current = { kind: 'rating', items }
    },
  })

  const ratingUndoMutation = useMutation({
    mutationFn: (items: { path: string; rating: number }[]) => photosApi.undoRating(items),
    onMutate: async (items) => {
      await queryClient.cancelQueries({ queryKey: listKey })
      const target = new Map(items.map((item) => [item.path, item.rating]))
      const previous = patchRows((rowsToPatch) =>
        rowsToPatch.map((row) => {
          const rating = target.get(row.path)
          return rating === undefined ? row : { ...row, rating }
        }),
      )
      return { previous }
    },
    onError: (error: Error, _variables, context) => {
      if (context?.previous !== undefined) queryClient.setQueryData(listKey, context.previous)
      notifyError(error.message, { title: 'Undo failed' })
      invalidateList()
      invalidateFacets()
    },
    onSuccess: (result, variables) => {
      invalidateFacets()
      invalidateRejects()
      if (result.errors > 0) {
        // The optimistic patch above is wrong for the paths the server could not
        // confirm — refetch the truth rather than let a rating that was never
        // actually restored sit in the cache looking undone.
        invalidateList()
        notifyError(
          `${result.errors} of ${variables.length} rating${variables.length === 1 ? '' : 's'} ` +
            'could not be restored.',
          { title: 'Undo incomplete' },
        )
        return
      }
      notifySuccess(`${result.restored} rating${result.restored === 1 ? '' : 's'} restored.`)
    },
  })

  const labelMutation = useMutation({
    mutationFn: ({ paths, label }: { paths: string[]; label: string }) =>
      photosApi.setLabel(paths, label),
    onMutate: async ({ paths, label }) => {
      await queryClient.cancelQueries({ queryKey: listKey })
      const targets = new Set(paths)
      const previous = patchRows((items) =>
        items.map((row) => (targets.has(row.path) ? { ...row, label } : row)),
      )
      return { previous }
    },
    onError: (error: Error, _variables, context) => {
      if (context?.previous !== undefined) queryClient.setQueryData(listKey, context.previous)
      notifyError(error.message, { title: 'Label failed' })
      invalidateList()
      invalidateFacets()
    },
    onSuccess: (result) => {
      invalidateFacets()
      // A partial write leaves the optimistic patch claiming a label the file never got.
      if (result.errors > 0) invalidateList()
    },
  })

  const restoreMutation = useMutation({
    mutationFn: (ids: number[]) => photosApi.restore(ids),
    onSuccess: (result) => {
      // A restore puts rows back into the result set — the list genuinely is stale here.
      invalidateList()
      invalidateTrashList()
      invalidateFacets()
      if (result.errors > 0) {
        notifyError(result.messages.join(' · ') || 'Some entries could not be restored.', {
          title: 'Restore incomplete',
        })
        return
      }
      notifySuccess(`${result.restored} photo${result.restored === 1 ? '' : 's'} restored.`)
    },
    onError: (error: Error) => notifyError(error.message, { title: 'Restore failed' }),
  })

  /**
   * The trash toast carries an Undo button, which `notify()` cannot express — it records
   * `String(message)` into the notification history, so a ReactNode message would land
   * there as `[object Object]`. This is the documented raw-`notifications.show` escape
   * hatch (see basalt-notifications.md); the cost is that this one toast is invisible to
   * the bell, which is the right trade for an action button on a destructive move.
   */
  const showUndoToast = useCallback(
    (ids: number[], count: number): void => {
      const id = `photos-trash-${ids.join('-')}`
      notifications.show({
        id,
        color: 'blue',
        role: 'status',
        autoClose: UNDO_TOAST_MS,
        message: (
          <Group justify="space-between" wrap="nowrap" gap="sm">
            <Text size="sm">
              {count} photo{count === 1 ? '' : 's'} moved to trash
            </Text>
            <Button
              size="compact-xs"
              variant="default"
              leftSection={<IconArrowBackUp size={13} />}
              onClick={() => {
                notifications.hide(id)
                restoreMutation.mutate(ids)
              }}
            >
              Undo
            </Button>
          </Group>
        ),
      })
    },
    [restoreMutation],
  )

  /**
   * What a write acts on — and the one place the two surfaces genuinely disagree.
   *
   * In the viewer it is always the focused frame, *including while comparing*. F5's
   * reasoning stands unchanged and is worth restating because this function looks like it
   * contradicts it: a star is a judgement about one photograph, and broadcasting it across
   * a comparison would say "these are equally good", which is the opposite of what a
   * comparison is for. The only set-shaped statement there is `P` — pick this, reject
   * those — and it stays the only one.
   *
   * In the grid the same marked set means something else. You do not mark 40 frames to
   * choose between them; you mark them because they share an answer — a whole burst that
   * missed focus, a whole sequence worth three stars. Broadcasting is the entire reason to
   * select more than one. Same set, same gestures, different verb, because the surface
   * says what the selection is FOR.
   */
  const writeTargets = useCallback((): string[] => {
    if (gridOpen && markedRows.length > 0) return markedRows.map((row) => row.path)
    return selectedRow === null ? [] : [selectedRow.path]
  }, [gridOpen, markedRows, selectedRow])

  /**
   * A star write, gated when it would broadcast past {@link RATING_CONFIRM_THRESHOLD}.
   *
   * The confirmation is the secondary safeguard — the load-bearing one is that the
   * write it confirms is undoable at all (see `rateMutation.onSuccess`). It states both
   * numbers the brief asked for: how many frames are touched, and how many of them
   * currently disagree with the value about to be written, which is the count that
   * actually gets overwritten rather than merely reconfirmed.
   */
  const rateSelected = useCallback(
    (rating: number): void => {
      const paths = writeTargets()
      if (paths.length === 0) return
      if (paths.length <= RATING_CONFIRM_THRESHOLD) {
        rateMutation.mutate({ paths, rating })
        return
      }
      const targets = new Set(paths)
      const differing = rows.filter(
        (row) => targets.has(row.path) && row.rating !== rating,
      ).length
      const title =
        rating === 0
          ? `Clear the rating on ${paths.length} photos?`
          : `Set ${rating}★ on ${paths.length} photos?`
      modals.openConfirmModal({
        title,
        children: (
          <Text size="sm">
            {differing} of {paths.length} currently carry a different rating and will be
            overwritten. Press <Code>⌘Z</Code> right after to undo it.
          </Text>
        ),
        labels: { confirm: `Rate ${paths.length} photos`, cancel: 'Cancel' },
        confirmProps: { color: 'blue' },
        onConfirm: () => rateMutation.mutate({ paths, rating }),
      })
    },
    [writeTargets, rateMutation, rows],
  )

  const labelSelected = useCallback(
    (label: string): void => {
      const paths = writeTargets()
      if (paths.length === 0) return
      labelMutation.mutate({ paths, label })
    },
    [writeTargets, labelMutation],
  )

  /**
   * The primary cull action: flag the photo rejected and move on.
   *
   * Note what does NOT happen — the frame is not removed from the strip. A reject is a
   * judgement, so the photo dims in place and stays steppable, which is what makes the
   * pass reversible in feel and not only in fact: pressing `x` again, or any star, takes
   * it straight back. Rejects leave the result set on the next genuine refetch, not
   * under the user's cursor mid-pass.
   */
  const rejectSelected = useCallback((): void => {
    const paths = writeTargets()
    if (paths.length === 0) return
    // A batch un-rejects only when EVERY member is already rejected; otherwise `x` over a
    // mixed selection would silently un-reject the ones already answered. One key, one
    // meaning: "make these rejected", and pressing it again on the same set undoes it.
    const targets = new Set(paths)
    const alreadyRejected = rows
      .filter((row) => targets.has(row.path))
      .every((row) => row.rating === REJECTED)
    rateMutation.mutate({ paths, rating: alreadyRejected ? 0 : REJECTED })
    // Un-rejecting is a correction, and the frame the user is correcting is the one they
    // want to keep looking at. Only the forward judgement advances — and only in the
    // single viewer: in the grid the frames are all still on screen, so advancing the
    // focus past a batch just moves a ring the user was not looking at.
    if (!alreadyRejected && !gridOpen) step(1)
  }, [writeTargets, rows, rateMutation, step, gridOpen])

  /**
   * `P` — the compare verb: keep the focused frame, reject the others, move on.
   *
   * **This, and only this, is why a rating key does NOT apply to the whole compare set.**
   * A star is a judgement about one photograph and means the same thing whether or not
   * anything is beside it; broadcasting it to three frames would say "these three are
   * equally good", which is the precise opposite of what a comparison is for. A pick is a
   * judgement about the *set* — "this one, not those" — and it is the only statement that
   * genuinely has N targets. So the multi-path write exists, it is one keystroke, and it
   * writes different values to different files in a single batch, which is exactly what
   * the endpoints were already able to do.
   *
   * The losers are rejected, not trashed: nothing moves, and pressing `X` on any of them
   * puts it back. A batch that moved four files on one keypress would be the one
   * irreversible thing on this screen.
   */
  const pickKeeper = useCallback((): void => {
    if (!comparing || selectedRow === null) return
    const losers = compareRows.filter((row) => row.path !== selectedRow.path)
    if (losers.length === 0) return
    rateMutation.mutate({ paths: losers.map((row) => row.path), rating: REJECTED })
    setMarked([])
    // Advance past the whole group, not by one — every frame in it has just been answered.
    const last = Math.max(...compareRows.map((row) => rows.indexOf(row)))
    if (last >= 0) select(Math.min(last + 1, rows.length - 1))
  }, [comparing, selectedRow, compareRows, rows, rateMutation, select])

  /**
   * Hand the selected photo — or the RAW that correlates to it — to an external
   * application.
   *
   * Nothing is invalidated on success, deliberately. The application has been *launched*,
   * not run: whatever it writes happens minutes later, in another process, and an
   * invalidation now would refetch the row in its unchanged state and prove nothing. The
   * edit comes back through the index on the next reindex, the same way a Photomator edit
   * does.
   */
  const editMutation = useMutation({
    mutationFn: (vars: { path: string; target?: EditorKind; editorId?: string }) =>
      photosApi.openInEditor(vars.path, {
        ...(vars.target === undefined ? {} : { target: vars.target }),
        ...(vars.editorId === undefined ? {} : { editor: vars.editorId }),
      }),
    onSuccess: (result) => {
      // `opened: false` is a normal answer — the application is optional, or (for a RAW)
      // may simply not exist for this shot. It carries its own explanation, so it is
      // shown rather than flattened into a generic failure.
      if (result.opened) notifySuccess(result.message, { title: result.editor })
      else notifyError(result.message, { title: result.editor || 'Not available' })
    },
    onError: (error: Error) => notifyError(error.message, { title: 'Could not open the editor' }),
  })

  const openInEditor = useCallback(
    (target: EditorKind, editorId?: string): void => {
      if (selectedRow === null) return
      editMutation.mutate({
        path: selectedRow.path,
        target,
        ...(editorId === undefined ? {} : { editorId }),
      })
    },
    [selectedRow, editMutation],
  )

  const editSelected = useCallback((): void => openInEditor('jpeg'), [openInEditor])

  /**
   * Where the context menu was summoned, in the stage's own coordinates.
   *
   * Stage-relative rather than viewport-relative because the anchor is an absolutely
   * positioned child of the stage — which is also what keeps the menu from opening over
   * the sidebar when the click was near the right edge of the photo.
   */
  const [menuAt, setMenuAt] = useState<{ x: number; y: number } | null>(null)

  const openStageMenu = useCallback(
    (event: ReactMouseEvent<HTMLDivElement>): void => {
      if (selectedRow === null) return
      event.preventDefault()
      const box = event.currentTarget.getBoundingClientRect()
      setMenuAt({ x: event.clientX - box.left, y: event.clientY - box.top })
    },
    [selectedRow],
  )

  const purgeRejectsMutation = useMutation({
    mutationFn: () => photosApi.purgeRejects(filters.root),
    onSuccess: (result) => {
      // Every one of these rows just left the result set — unlike a reject, this genuinely
      // is a file move, so the list, the facets, the trash drawer and the count all shift.
      invalidateList()
      invalidateTrashList()
      invalidateFacets()
      invalidateRejects()
      const ids = result.entries
        .map((entry) => entry.id)
        .filter((id): id is number => id !== null)
      if (ids.length > 0) {
        lastActionRef.current = { kind: 'trash', ids }
        showUndoToast(ids, result.trashed)
      }
      if (result.errors > 0) {
        notifyError(result.messages.join(' · ') || 'Some rejects were not moved.', {
          title: 'Purge incomplete',
        })
      }
    },
    onError: (error: Error) => notifyError(error.message, { title: 'Purge failed' }),
  })

  /**
   * Confirm, then move every reject to the trash.
   *
   * The confirmation is the deliberate part — this is the one moment in a cull pass that
   * touches the filesystem, so it never rides a keystroke and never enters ⌘K (DESIGN.md).
   * "Purge" still only means trash: `photoflow trash restore` and the undo toast both work.
   */
  const purgeRejects = useCallback((): void => {
    const count = rejectsQuery.data?.count ?? 0
    if (count === 0) return
    modals.openConfirmModal({
      title: `Move ${count} rejected photo${count === 1 ? '' : 's'} to trash?`,
      children: (
        <Text size="sm">
          Their <Code>.photo-edit</Code> sidecars travel with them, and nothing is unlinked —
          this can be undone from the trash.
        </Text>
      ),
      labels: { confirm: 'Move to trash', cancel: 'Cancel' },
      confirmProps: { color: 'red' },
      onConfirm: () => purgeRejectsMutation.mutate(),
    })
  }, [rejectsQuery.data, purgeRejectsMutation])

  /** ⌘Z — invert whichever {@link UndoableAction} happened last, trash or rating. */
  const undoLastAction = useCallback((): void => {
    const action = lastActionRef.current
    if (action === null) return
    lastActionRef.current = null
    if (action.kind === 'trash') restoreMutation.mutate(action.ids)
    else ratingUndoMutation.mutate(action.items)
  }, [restoreMutation, ratingUndoMutation])

  // ── View state ─────────────────────────────────────────────────────────────

  const [sidebarOpen, setSidebarOpen] = usePhotosSidebarOpen()
  const [stripOpen, setStripOpen] = usePhotosStripOpen()
  const [trueResolution, setTrueResolution] = usePhotosTrueResolution()
  const [zoomed, setZoomed] = useState(false)
  const [trashOpen, setTrashOpen] = useState(false)

  // A new photo is always shown fit-to-window; carrying a pan across frames is disorienting.
  //
  // …except while comparing, where holding the magnification across a focus change is the
  // entire mechanism: the frames are side by side at one scale, and dropping back to fit
  // because the focus moved from frame 1 to frame 2 would undo the comparison every time
  // the user acted on it. Leaving compare mode resets, which is the same rule seen from
  // the other side.
  useEffect(() => {
    if (comparing) return
    setZoomed(false)
  }, [selection.path, comparing])

  // Lock the page itself for as long as this route is mounted — see `.noPageScroll`. Every
  // other screen in the panel is a scrolling page, so this is put back on unmount.
  useEffect(() => {
    // Typed as possibly-undefined by the CSS-module declaration; `classList.add('')` throws.
    const lock = classes.noPageScroll
    if (lock === undefined) return
    const { documentElement, body } = document
    documentElement.classList.add(lock)
    body.classList.add(lock)
    return () => {
      documentElement.classList.remove(lock)
      body.classList.remove(lock)
    }
  }, [])

  const applyFilters = useCallback(
    (next: PhotoFilters): void => {
      void navigate({
        search: {
          ...stripDefaults(next),
          ...(scope === null ? {} : { col: scope }),
          ...(selection.path === null ? {} : { sel: selection.path }),
        },
        replace: true,
      })
    },
    [navigate, scope, selection.path],
  )

  /**
   * Select (or leave) a collection, KEEPING the ad-hoc filters.
   *
   * This is the whole shape of the model in one handler: a collection is a layer, so
   * picking one does not overwrite what you had narrowed — the server composes them, and
   * the Narrowing card says which won where they disagree.
   */
  const applyScope = useCallback(
    (id: string | null): void => {
      void navigate({
        search: {
          ...stripDefaults(filters),
          ...(id === null ? {} : { col: id }),
          ...(selection.path === null ? {} : { sel: selection.path }),
        },
        replace: true,
      })
    },
    [navigate, filters, selection.path],
  )

  /**
   * Clear one whole narrowing dimension, by the filter fields the server named.
   *
   * The server sends the effective FIELDS of each dimension, so the client never has to
   * hold its own copy of "which fields make up the focal dimension" — the one place that
   * mapping exists is `QUERY_DIMENSIONS`. Clearing a dimension the SCOPE supplied clears
   * it in the refine layer, which does nothing on its own; so a scope-sourced dimension is
   * additionally shadowed by leaving the collection, which the card offers separately.
   */
  const clearDimension = useCallback(
    (fields: string[]): void => {
      const cleared: Record<string, unknown> = { ...filters }
      for (const key of fields) {
        if (key in DEFAULT_FILTERS) {
          cleared[key] = DEFAULT_FILTERS[key as keyof PhotoFilters]
        }
      }
      applyFilters(normalizeFilters(cleared))
    },
    [filters, applyFilters],
  )

  /**
   * Is this structure group exactly what the filters currently say?
   *
   * Compared field by field against the group's own query, which is the same mapping the
   * click applies — so the highlight cannot drift from what selecting the row would do.
   * Nothing is stored to mark the "current folder": there is no such thing, only a filter
   * state that happens to match a group.
   */
  const isStructureGroupActive = useCallback(
    (group: StructureGroup): boolean => {
      const current = filters as unknown as Record<string, unknown>
      return Object.entries(group.query).every(([key, value]) => {
        const mine = current[key]
        if (Array.isArray(value) || Array.isArray(mine)) {
          const a = (Array.isArray(value) ? value : []).map(String).toSorted()
          const b = (Array.isArray(mine) ? mine : []).map(String).toSorted()
          return a.length === b.length && a.every((item, index) => item === b[index])
        }
        return mine === value
      })
    },
    [filters],
  )

  /**
   * Apply (or clear) a structure group.
   *
   * A group is a REFINE, never a scope: it is one dimension's worth of filter, exactly
   * like clicking a folder row. Only the ACTIVE KIND's own fields are reset first, so
   * picking July keeps you in the folder you were in and merely replaces June.
   */
  const applyStructureGroup = useCallback(
    (group: StructureGroup | null): void => {
      const next: Record<string, unknown> = { ...filters }
      for (const key of STRUCTURE_KIND_FIELDS[structureKind]) {
        if (key in DEFAULT_FILTERS) next[key] = DEFAULT_FILTERS[key as keyof PhotoFilters]
      }
      if (group !== null) Object.assign(next, group.query)
      applyFilters(normalizeFilters(next))
    },
    [filters, applyFilters, structureKind],
  )

  // ── Keyboard ───────────────────────────────────────────────────────────────

  // @mantine/hooks 9.5.1 defaults `tagsToIgnore` to ['INPUT','TEXTAREA','SELECT'] and also
  // skips contenteditable targets — verified against the installed source. Restated here
  // so a future default change cannot silently start firing `Delete` while the user is
  // typing in the filename filter.
  const IGNORED_TAGS = ['INPUT', 'TEXTAREA', 'SELECT']

  /**
   * Focused elements that own the keyboard but are not one of `IGNORED_TAGS`.
   *
   * A Mantine 9.5.1 `RangeSlider` thumb is a focusable `div` carrying `role="slider"`
   * (`Slider/Thumb/Thumb.mjs` — verified against the installed source), so nudging
   * ISO / aperture / shutter / focal with the arrow keys used to step the photo selection
   * at the same time. `role="dialog"` is every Modal/Drawer plus any Popover dropdown —
   * while one has focus the cull shortcuts are not what the user is aiming at.
   * `role="separator"` is the sidebar's own drag handle, which takes the arrow keys to
   * nudge its width.
   */
  const KEYBOARD_OWNING_ROLES = '[role="slider"],[role="dialog"],[role="separator"]'

  /** The trash drawer owns the keyboard while it is open, as does a focused slider/overlay. */
  const whenIdle = (run: () => void) => (event: KeyboardEvent): void => {
    if (trashOpen) return
    const target = event.target
    if (target instanceof Element && target.closest(KEYBOARD_OWNING_ROLES) !== null) return
    run()
  }

  /**
   * `Enter`'s own guard, additionally refusing a focused `<button>`.
   *
   * Every other shortcut here is a single unmodified key a native control never reacts to
   * on its own — but a browser fires a `click` on whatever `<button>` is focused (a Mantine
   * `UnstyledButton`, e.g. a sidebar folder or collection row, renders one) when `Enter` is
   * pressed. `BUTTON` is deliberately absent from `IGNORED_TAGS` (a button IS how you
   * activate it) and carries none of `KEYBOARD_OWNING_ROLES`, so tabbing to a sidebar row
   * and pressing Enter used to both activate the row and close the contact sheet. Scoped to
   * this one binding — `Enter` has no other meaning on this screen, so nothing else needs it.
   */
  const whenIdleNotButton = (run: () => void) => (event: KeyboardEvent): void => {
    const target = event.target
    if (target instanceof Element && target.tagName === 'BUTTON') return
    whenIdle(run)(event)
  }

  /**
   * What an arrow key means depends on whether a comparison is open.
   *
   * Single frame: step the list, as always. Comparing: move the focus *between* the frames
   * on screen — the backlog's "keys to pick between them without leaving the comparison".
   * The cost is real and worth naming: while comparing you cannot walk the list, and
   * `Escape` (or `C`) is how you get that back. The alternative — arrows sliding the whole
   * N-frame window through the list — was not built, and is the first thing to try if this
   * one turns out to be the wrong half of the trade.
   */
  const stepOrCycle = (delta: number) => (): void => {
    if (comparing) cycleFocus(delta)
    else step(delta)
  }

  /**
   * Step density by one stop.
   *
   * `[` and `]` because they are the two keys every image tool already uses for "smaller /
   * larger", and because every letter that means anything on this screen is taken.
   */
  const stepDensity = (delta: number) => (): void => {
    const at = DENSITY_STEPS.indexOf(density)
    const from = at < 0 ? nearestDensityIndex(density) : at
    const next = DENSITY_STEPS[Math.min(Math.max(from + delta, 0), DENSITY_STEPS.length - 1)]
    if (next !== undefined) setDensity(next)
  }

  useHotkeys(
    [
      ['ArrowRight', whenIdle(stepOrCycle(1))],
      ['ArrowLeft', whenIdle(stepOrCycle(-1))],
      ['j', whenIdle(stepOrCycle(1))],
      ['k', whenIdle(stepOrCycle(-1))],
      // Shift grows the comparison by a frame in that direction, from the anchor. The
      // keyboard equivalents matter more here than the mouse ones: this is a screen whose
      // whole vocabulary is single keys, and reaching for the strip to build a pair costs
      // more than the comparison saves.
      ['shift+ArrowRight', whenIdle(() => extendMarked(1))],
      ['shift+ArrowLeft', whenIdle(() => extendMarked(-1))],
      ['shift+J', whenIdle(() => extendMarked(1))],
      ['shift+K', whenIdle(() => extendMarked(-1))],
      ['c', whenIdle(toggleCompare)],
      ['Escape', whenIdle(() => setMarked([]))],
      // The one multi-path write on the screen — see `pickKeeper`.
      ['p', whenIdle(pickKeeper)],
      ['Home', whenIdle(() => select(0))],
      ['End', whenIdle(() => select(rows.length - 1))],
      ['0', whenIdle(() => rateSelected(0))],
      ['1', whenIdle(() => rateSelected(1))],
      ['2', whenIdle(() => rateSelected(2))],
      ['3', whenIdle(() => rateSelected(3))],
      ['4', whenIdle(() => rateSelected(4))],
      ['5', whenIdle(() => rateSelected(5))],
      // `x` is the Lightroom/Bridge reject key and the primary cull action. Backspace and
      // Delete are folded onto it deliberately: they USED to trash-and-advance, and a key
      // that moved a file on every press is exactly what the three-state model replaces.
      // Trashing is now the batch purge below, behind a count and a confirmation.
      ['x', whenIdle(rejectSelected)],
      ['Backspace', whenIdle(rejectSelected)],
      ['Delete', whenIdle(rejectSelected)],
      ['mod+Z', whenIdle(undoLastAction)],
      // `e` for edit. Not `mod+e`: this screen's whole vocabulary is single keys, and the
      // action is a launch, not something destructive that wants a modifier in front of it.
      ['e', whenIdle(editSelected)],
      ['i', whenIdle(() => setSidebarOpen(!sidebarOpen))],
      ['f', whenIdle(() => setStripOpen(!stripOpen))],
      ['z', whenIdle(() => setZoomed(!zoomed))],
      // `g` for grid. The sheet and the viewer are two stages of one screen, so this is a
      // toggle rather than a route: the filters, the marked set and the focused frame all
      // survive it, which is what makes "look at the whole shoot, then go back to this
      // frame" one keystroke each way.
      ['g', whenIdle(() => setGridOpen(!gridOpen))],
      // Up/Down are a row of the sheet. Bound unconditionally rather than only while the
      // grid is open: in the single viewer `gridColumns` is whatever the sheet last
      // resolved, and stepping by a row through a list you are seeing one frame of is a
      // reasonable page-down — it is the same motion, seen from the other stage.
      ['ArrowDown', whenIdle(() => step(gridOpen ? gridColumns : 1))],
      ['ArrowUp', whenIdle(() => step(gridOpen ? -gridColumns : -1))],
      ['[', whenIdle(stepDensity(-1))],
      [']', whenIdle(stepDensity(1))],
      // Enter leaves the sheet on the focused frame — "show me this one big". The exact
      // inverse of `g`, and the reason a double-click on a cell does the same thing.
      ['Enter', whenIdleNotButton(() => setGridOpen(false))],
    ],
    IGNORED_TAGS,
  )

  // ── Render ─────────────────────────────────────────────────────────────────

  const position = selectedIndex < 0 ? '0' : String(selectedIndex + 1)

  // No `overflow: hidden` on the outer column, deliberately: the filmstrip reaches PAST it on
  // the left (it cancels the nav rail's offset) and a clip there would cut exactly the part the
  // bleed exists to show. The clip lives on the stage row instead — the box that actually has
  // something to contain.
  return (
    <Flex direction="column" h={CONTENT_HEIGHT} m={BLEED_MARGIN}>
      <Flex style={{ flex: 1, minHeight: 0, overflow: 'hidden' }} wrap="nowrap">
        {/*
          The stage: the photo and nothing else.

          `position: relative` is load-bearing — it is what the floating cluster below
          anchors to, so the toggle sits at the top-right of the IMAGE rather than of the
          window, and therefore never lands on top of the sidebar.
        */}
        <Flex direction="column" style={{ flex: 1, minWidth: 0, position: 'relative' }}>
          {/*
            One stage, two viewers. `PhotoViewer` is unchanged and still owns the cull loop:
            its placeholder chain, its LRU and its flash-free frame swap are tuned for
            stepping a shoot at key-repeat speed, and none of that is what a comparison
            needs. `PhotoCompare` takes over only once there are two frames to show, which
            is a mode the user entered deliberately.
          */}
          <Box style={{ flex: 1, minHeight: 0 }} onContextMenu={openStageMenu}>
            {gridOpen ? (
              <PhotoGrid
                rows={rows}
                selectedIndex={selectedIndex}
                onSelect={pick}
                onActivate={(index) => {
                  select(index)
                  setGridOpen(false)
                }}
                marked={markedIndices}
                cellSize={density}
                onColumns={setGridColumns}
                onWarm={warmGridWindow}
                showTrashed={showTrashed}
              />
            ) : comparing ? (
              <PhotoCompare
                rows={compareRows}
                focusPath={selection.path}
                onFocus={(path) =>
                  setSelection({
                    path,
                    index: rows.findIndex((candidate) => candidate.path === path),
                  })
                }
                zoomed={zoomed}
                onZoomedChange={setZoomed}
                trueResolution={trueResolution}
              />
            ) : (
              <PhotoViewer
                row={selectedRow}
                loading={listQuery.isPending}
                zoomed={zoomed}
                onToggleZoom={() => setZoomed(!zoomed)}
                trueResolution={trueResolution}
              />
            )}
          </Box>

          {/*
            Right-click the photograph.

            The target is a 1x1 box placed where the click landed, which is the standard way
            to give a popover a position rather than an element — Mantine's `Menu` anchors to
            a node and a context menu has none. `pointerEvents: none` so the invisible anchor
            can never eat a click of its own.
          */}
          <Menu
            opened={menuAt !== null}
            onClose={() => setMenuAt(null)}
            position="bottom-start"
            withinPortal
            shadow="shadow-raised"
          >
            <Menu.Target>
              <Box
                style={{
                  position: 'absolute',
                  left: menuAt?.x ?? 0,
                  top: menuAt?.y ?? 0,
                  width: 1,
                  height: 1,
                  pointerEvents: 'none',
                }}
              />
            </Menu.Target>
            <Menu.Dropdown>
              {(jpegEditors.length > 0 ? jpegEditors : [null]).map((editor, index) => (
                <Menu.Item
                  key={editor?.id ?? `jpeg-default-${index}`}
                  leftSection={<IconTool size={14} />}
                  onClick={() => {
                    setMenuAt(null)
                    openInEditor('jpeg', editor?.id)
                  }}
                >
                  Edit{editor ? ` in ${editor.name}` : ''}
                </Menu.Item>
              ))}
              {rawEditors.length > 0 && (
                <>
                  <Menu.Divider />
                  <Menu.Label>Open RAW in</Menu.Label>
                  {rawEditors.map((editor) => (
                    <Menu.Item
                      key={editor.id}
                      leftSection={<IconTool size={14} />}
                      onClick={() => {
                        setMenuAt(null)
                        openInEditor('raw', editor.id)
                      }}
                    >
                      {editor.name}
                    </Menu.Item>
                  ))}
                </>
              )}
            </Menu.Dropdown>
          </Menu>

          {/*
            The only chrome that floats over the photo: where you are in the set, and the
            one control that takes the sidebar away. It rests at 40% opacity and comes up
            on hover (see the module CSS) — present enough to find, quiet enough to ignore.
            Everything else that used to live in a top bar is a sidebar section now.
          */}
          <Group
            className={classes.overlay}
            gap={6}
            wrap="nowrap"
            px={8}
            py={3}
            style={{
              background: alpha(VX.surface.panel, 0.82),
              borderRadius: VX.radiusPill,
              backdropFilter: 'blur(6px)',
            }}
          >
            <Text size="xs" c="dimmed" ff="monospace">
              {position} / {total}
            </Text>
            <Tooltip label={sidebarOpen ? 'Hide sidebar (I)' : 'Show sidebar (I)'} withArrow>
              <ActionIcon
                size="sm"
                variant="subtle"
                // Neutral, not the accent. This is chrome pointing at chrome — it carries
                // no signal, and "ink earns its color" means it stays grey.
                color="gray"
                aria-label="Toggle sidebar"
                aria-pressed={sidebarOpen}
                onClick={() => setSidebarOpen(!sidebarOpen)}
              >
                {sidebarOpen ? (
                  <IconLayoutSidebarRightCollapse size={16} />
                ) : (
                  <IconLayoutSidebarRightExpand size={16} />
                )}
              </ActionIcon>
            </Tooltip>
          </Group>
        </Flex>

        {sidebarOpen && (
          <PhotoSidebar
            filters={filters}
            facets={facetsQuery.data}
            rootCounts={rootCounts}
            selectedRow={selectedRow}
            scope={scope}
            narrowing={narrowingQuery.data}
            onFilters={applyFilters}
            onScope={applyScope}
            structureKind={structureKind}
            onStructureKind={setStructureKind}
            eventGap={eventGap}
            onEventGap={setEventGap}
            structure={structureQuery.data}
            isStructureGroupActive={isStructureGroupActive}
            onStructureGroup={applyStructureGroup}
            onClearDimension={clearDimension}
            onRate={rateSelected}
            onLabel={labelSelected}
            filmstripOpen={stripOpen}
            onFilmstrip={setStripOpen}
            zoomed={zoomed}
            onZoom={setZoomed}
            canZoom={selectedRow !== null}
            trueResolution={trueResolution}
            onTrueResolution={setTrueResolution}
            markedCount={markedRows.length}
            compareCount={compareRows.length}
            onCompare={toggleCompare}
            onClearCompare={() => setMarked([])}
            gridOpen={gridOpen}
            onGrid={setGridOpen}
            density={density}
            onDensity={setDensity}
            onPickKeeper={pickKeeper}
            showTrashed={showTrashed}
            onShowTrashed={setShowTrashed}
            onOpenTrash={() => setTrashOpen(true)}
            showRejected={showRejected}
            onShowRejected={setShowRejected}
            rejectCount={rejectsQuery.data?.count ?? 0}
            onPurgeRejects={purgeRejects}
            purging={purgeRejectsMutation.isPending}
            jpegEditors={jpegEditors}
            rawEditors={rawEditors}
            onEdit={openInEditor}
            editing={editMutation.isPending}
          />
        )}
      </Flex>

      {/*
        The strip spans the WHOLE window, under the sidebar as well as the stage.

        It is a timeline of the result set, not an accessory of the viewer: cutting it off at
        the sidebar's edge cost it ~300px of frames and left a stub of empty surface beside
        it. Sitting outside the stage row is also what lets it run edge to edge.

        It is HIDDEN, never unmounted. Toggling it used to conditionally render it, which
        made a one-keystroke view toggle one of the most expensive things on the screen:
        every remount threw away ~30 decoded `<img>` nodes and rebuilt them, and — worse —
        reset the strip's own `scrollLeft` / `viewWidth` state to 0, so it first materialised
        the window at index 0 and only then `scrollIntoView`'d to the selection, building a
        second, disjoint window of cells. Two waves of image nodes, a fresh ResizeObserver
        and two layout passes, for content that was already correct a moment earlier.

        `<Activity mode="hidden">` keeps the DOM and the component state and only drops the
        effects, so re-showing is a `display` flip plus a ResizeObserver re-attach — no
        refetch, no re-decode, no scroll jump. The one layout change left (the viewer
        reclaiming the strip's height) is the point of the toggle, not a cost.
      */}
      {/*
        The strip is hidden while the sheet is up, and that is a finding rather than a
        layout choice: a windowed row of 320 px thumbnails UNDER a windowed sheet of the
        same 320 px thumbnails is the same rows twice, and the strip's one advantage —
        that it is always there while you look at something else — is exactly what the
        sheet takes over. They are two stages of one screen, not two panes.
      */}
      <Activity mode={stripOpen && !gridOpen ? 'visible' : 'hidden'}>
        <Box className={classes.filmstripBleed} style={{ flexShrink: 0 }}>
          <PhotoFilmstrip
            rows={rows}
            selectedIndex={selectedIndex}
            onSelect={pick}
            marked={markedIndices}
            showTrashed={showTrashed}
            height={FILMSTRIP_HEIGHT}
          />
        </Box>
      </Activity>

      <PhotoTrashDrawer opened={trashOpen} onClose={() => setTrashOpen(false)} />
    </Flex>
  )
}
