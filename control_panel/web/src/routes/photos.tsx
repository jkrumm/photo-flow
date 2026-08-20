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
import { PhotoFilmstrip } from '../components/photos/photo-filmstrip'
import { splitRootCounts } from '../components/photos/photo-folders'
import { PhotoSidebar } from '../components/photos/photo-sidebar'
import { PhotoTrashDrawer } from '../components/photos/photo-trash-drawer'
import { PhotoViewer } from '../components/photos/photo-viewer'
import classes from '../components/photos/photos-screen.module.css'
import { api } from '../lib/api'
import { photosApi, photosQueries } from '../lib/queries/photos'
import {
  DEFAULT_PHOTO_LIMIT,
  MAX_WARM_PATHS,
  PHOTOS_API,
  REJECTED,
  normalizeFilters,
  stripDefaults,
  thumbUrl,
  toQueryParams,
  withQuery,
} from '../lib/photos'
import type {
  Facets,
  PhotoFilters,
  PhotoListResponse,
  PhotoRoot,
  PhotoRow,
  ThumbTier,
  WarmResult,
} from '../lib/photos'

// ── Search params ────────────────────────────────────────────────────────────

/** Filter set plus the selected photo's path — the complete, linkable cull session. */
export type PhotosSearch = PhotoFilters & {
  /** Absolute path of the selected photo; null when nothing is selected. */
  sel: string | null
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
  return { ...normalizeFilters({ ...source, ...RETIRED_SEARCH_KEYS }), sel }
}

export const Route = createFileRoute('/photos')({
  validateSearch: validatePhotosSearch,
  loaderDeps: ({ search }: { search: PhotosSearch }) => ({ filters: normalizeFilters(search) }),
  // Deliberately NOT awaited, unlike the house `ensureQueryData` idiom: this is a prewarm,
  // not a gate. Awaiting would suspend navigation on every filter tweak and unpaint the
  // frame being culled; the component reads the same keys with `keepPreviousData` instead.
  loader: ({ context, deps }) => {
    void context.queryClient.ensureQueryData(photosQueries.list(deps.filters))
    void context.queryClient.ensureQueryData(photosQueries.facets(deps.filters))
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
  includeTrashed: boolean,
  includeRejected: boolean,
): PhotoListQuery {
  const base = photosQueries.list(filters)
  if (!includeTrashed && !includeRejected) return base
  const params = toQueryParams(filters)
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

function PhotosPage() {
  const search = Route.useSearch()
  const navigate = Route.useNavigate()
  const queryClient = useQueryClient()

  // `normalizeFilters` drops `sel`, so the filter object (and therefore the query key)
  // is untouched by a selection change — stepping never refetches the list.
  const filters = useMemo(() => normalizeFilters(search), [search])

  // Declared up here rather than with the other view prefs below because it is part of the
  // list query key — a cull pass reviewed with its trashed frames still in place is a
  // different result set, not a different rendering of the same one.
  const [showTrashed, setShowTrashed] = usePhotosShowTrashed()
  const [showRejected, setShowRejected] = usePhotosShowRejected()

  const listOptions = listQueryOptions(filters, showTrashed, showRejected)
  const listQuery = useQuery({ ...listOptions, placeholderData: keepPreviousData })
  const rejectsQuery = useQuery(photosQueries.rejects(filters.root))
  const facetsQuery = useQuery({
    ...photosQueries.facets(filters),
    placeholderData: keepPreviousData,
  })
  // One extra facet computation for the rail, not two — see `splitRootCounts`. Picking the
  // *other* root also means clicking a folder reuses this exact key as the new base query.
  const otherRoot: PhotoRoot = filters.root === 'final' ? 'staging' : 'final'
  const otherRootQuery = useQuery({
    ...photosQueries.facets({ ...filters, root: otherRoot }),
    select: (facets: Facets) => facets.count,
  })
  const rootCounts = splitRootCounts(filters.root, facetsQuery.data?.count, otherRootQuery.data)

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

  // Mirror the selection into the URL, debounced: arrow-stepping a 2 000-frame shoot at
  // one `replaceState` per key would hit Safari's history throttle within seconds.
  // `stripDefaults` runs on the way out because the router serialises whatever the object
  // carries — spreading the fully-normalised `prev` would write every unset filter into the
  // URL as `iso_min=null&rating=[]&…` on the first arrow key.
  const commitSelectionToUrl = useDebouncedCallback((path: string | null) => {
    void navigate({
      search: (prev) => ({
        ...stripDefaults(normalizeFilters(prev)),
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
  }, [rows, selectedIndex, warmSettled])

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
    onSuccess: (result) => {
      invalidateFacets()
      // A rating write is also the only way the reject count moves, in either direction.
      invalidateRejects()
      if (result.errors > 0) {
        notifyError(result.messages.join(' · ') || 'Some ratings were not written.', {
          title: 'Rating incomplete',
        })
        invalidateList()
      }
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
   * The ids of the last batch that reached the trash, for ⌘Z.
   *
   * Only the purge writes this now. Per-photo trashing was removed with the reject flag:
   * `x` no longer moves anything, so the undo a cull pass needs is a rating write (press
   * the key again), not a restore. This covers the one action that still moves files.
   */
  const lastTrashedRef = useRef<number[]>([])

  const rateSelected = useCallback(
    (rating: number): void => {
      if (selectedRow === null) return
      rateMutation.mutate({ paths: [selectedRow.path], rating })
    },
    [selectedRow, rateMutation],
  )

  const labelSelected = useCallback(
    (label: string): void => {
      if (selectedRow === null) return
      labelMutation.mutate({ paths: [selectedRow.path], label })
    },
    [selectedRow, labelMutation],
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
    if (selectedRow === null) return
    const alreadyRejected = selectedRow.rating === REJECTED
    rateMutation.mutate({ paths: [selectedRow.path], rating: alreadyRejected ? 0 : REJECTED })
    // Un-rejecting is a correction, and the frame the user is correcting is the one they
    // want to keep looking at. Only the forward judgement advances.
    if (!alreadyRejected) step(1)
  }, [selectedRow, rateMutation, step])

  /**
   * Hand the selected photo to Shutterflow.
   *
   * Nothing is invalidated on success, deliberately. The editor has been *launched*, not
   * run: whatever it writes happens minutes later, in another process, and an invalidation
   * now would refetch the row in its unchanged state and prove nothing. The edit comes back
   * through the index on the next reindex, the same way a Photomator edit does.
   */
  const editMutation = useMutation({
    mutationFn: (path: string) => photosApi.openInEditor(path),
    onSuccess: (result) => {
      // `opened: false` is a normal answer — the editor is optional. It carries its own
      // explanation (not installed, would not launch), so it is shown rather than
      // flattened into a generic failure.
      if (result.opened) notifySuccess(result.message, { title: result.editor })
      else notifyError(result.message, { title: `${result.editor} not available` })
    },
    onError: (error: Error) => notifyError(error.message, { title: 'Could not open the editor' }),
  })

  const editSelected = useCallback((): void => {
    if (selectedRow === null) return
    editMutation.mutate(selectedRow.path)
  }, [selectedRow, editMutation])

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
        lastTrashedRef.current = ids
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

  const undoLastTrash = useCallback((): void => {
    const ids = lastTrashedRef.current
    if (ids.length === 0) return
    lastTrashedRef.current = []
    restoreMutation.mutate(ids)
  }, [restoreMutation])

  // ── View state ─────────────────────────────────────────────────────────────

  const [sidebarOpen, setSidebarOpen] = usePhotosSidebarOpen()
  const [stripOpen, setStripOpen] = usePhotosStripOpen()
  const [zoomed, setZoomed] = useState(false)
  const [trashOpen, setTrashOpen] = useState(false)

  // A new photo is always shown fit-to-window; carrying a pan across frames is disorienting.
  useEffect(() => {
    setZoomed(false)
  }, [selection.path])

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
          ...(selection.path === null ? {} : { sel: selection.path }),
        },
        replace: true,
      })
    },
    [navigate, selection.path],
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

  useHotkeys(
    [
      ['ArrowRight', whenIdle(() => step(1))],
      ['ArrowLeft', whenIdle(() => step(-1))],
      ['j', whenIdle(() => step(1))],
      ['k', whenIdle(() => step(-1))],
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
      ['mod+Z', whenIdle(undoLastTrash)],
      // `e` for edit. Not `mod+e`: this screen's whole vocabulary is single keys, and the
      // action is a launch, not something destructive that wants a modifier in front of it.
      ['e', whenIdle(editSelected)],
      ['i', whenIdle(() => setSidebarOpen(!sidebarOpen))],
      ['f', whenIdle(() => setStripOpen(!stripOpen))],
      ['z', whenIdle(() => setZoomed(!zoomed))],
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
          <Box style={{ flex: 1, minHeight: 0 }} onContextMenu={openStageMenu}>
            <PhotoViewer
              row={selectedRow}
              loading={listQuery.isPending}
              zoomed={zoomed}
              onToggleZoom={() => setZoomed(!zoomed)}
            />
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
              <Menu.Item
                leftSection={<IconTool size={14} />}
                onClick={() => {
                  setMenuAt(null)
                  editSelected()
                }}
              >
                Edit in Shutterflow
              </Menu.Item>
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
            rows={rows}
            rootCounts={rootCounts}
            selectedRow={selectedRow}
            onFilters={applyFilters}
            onRate={rateSelected}
            onLabel={labelSelected}
            filmstripOpen={stripOpen}
            onFilmstrip={setStripOpen}
            zoomed={zoomed}
            onZoom={setZoomed}
            canZoom={selectedRow !== null}
            showTrashed={showTrashed}
            onShowTrashed={setShowTrashed}
            onOpenTrash={() => setTrashOpen(true)}
            showRejected={showRejected}
            onShowRejected={setShowRejected}
            rejectCount={rejectsQuery.data?.count ?? 0}
            onPurgeRejects={purgeRejects}
            purging={purgeRejectsMutation.isPending}
            onEdit={editSelected}
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
      <Activity mode={stripOpen ? 'visible' : 'hidden'}>
        <Box className={classes.filmstripBleed} style={{ flexShrink: 0 }}>
          <PhotoFilmstrip
            rows={rows}
            selectedIndex={selectedIndex}
            onSelect={select}
            showTrashed={showTrashed}
            height={FILMSTRIP_HEIGHT}
          />
        </Box>
      </Activity>

      <PhotoTrashDrawer opened={trashOpen} onClose={() => setTrashOpen(false)} />
    </Flex>
  )
}
