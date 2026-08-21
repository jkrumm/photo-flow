/**
 * Side-by-side compare stage — N frames, one shared zoom and pan.
 *
 * This is the screen's second viewer, and it exists because the single-frame viewer
 * cannot answer the one question a burst produces: *which of these three is sharp?*
 * Stepping between them with an arrow key compares them across a saccade and a repaint;
 * putting them beside each other compares them at a glance.
 *
 * Three things are genuinely different from `PhotoViewer`, and each is a decision:
 *
 * 1. **Zoom is a scale factor, not a boolean.** The single viewer's "1:1" is a CSS sizing
 *    swap: the `<img>` drops its `max-width` and lays out at the served bitmap's own size
 *    inside a scrolling box. That has exactly two states and no way to express 2.4x, which
 *    is the magnification a focus check actually needs. Here the frame is laid out at its
 *    fitted rect and driven by `transform: translate(...) scale(s)`, so every magnification
 *    between fit and true 1:1 is reachable and the peers can be given the *same* number.
 *
 * 2. **Pan is a transform, not scroll.** Synchronising `scrollLeft`/`scrollTop` across the
 *    peers was the alternative and it loses three ways: a scroll offset is in pixels of the
 *    *served* bitmap, so a portrait and a landscape frame from one shoot desync immediately;
 *    scroll-linked writes land after paint, so peers visibly lag the dragged frame; and a
 *    programmatic `scrollTop` fires a `scroll` event on the peer, which needs a re-entrancy
 *    guard to stop the pair oscillating. One transform, one source of truth, no guard.
 *
 * 3. **Offsets are normalised to each frame's own fitted rect**, not to pixels. Two frames
 *    of the same aspect then pan identically; a mixed pair pans to the same *relative* point
 *    of each photograph, which is the only shared meaning "the same place" can have.
 *
 * Deliberately **not** animated. A magnification change is an inspection, and a 160 ms
 * tween between two states is 160 ms of neither — the single viewer's cross-fade exists to
 * hide a network round trip, and there is none here.
 */
import { useCallback, useEffect, useLayoutEffect, useMemo, useRef, useState } from 'react'
import type { PointerEvent as ReactPointerEvent } from 'react'
import { Box, Flex, SimpleGrid, Text } from '@mantine/core'
import { IconX } from '@tabler/icons-react'
import { VX, alpha } from 'basalt-ui/tokens'
import {
  THUMB_LONG_EDGE,
  isRejected,
  originalUrl,
  ratingOf,
  thumbUrl,
  type PhotoRow,
} from '../../lib/photos'

// ── Constants ────────────────────────────────────────────────────────────────

/**
 * Hard ceiling on the compare set.
 *
 * Measured, not chosen. On this machine's stage — 4579x1266 CSS px, read out of the
 * running panel on a 5120x1440 display — the long edge a frame is rendered at goes:
 *
 * ```
 * N         1      2      3      4      5      6
 * 3:2     1899   1899   1524   1142    946    946      (100 / 100 / 80 / 60 / 50 / 50 %)
 * 2:3     1266   1266   1266   1266   1266   1140      (free until six)
 * ```
 *
 * One thing genuinely falls out of that table: a 2-up costs *nothing* on a wide stage,
 * because both cells are still height-limited — the second photograph is free.
 *
 * **Four is a taste call, and the arithmetic does not make it for us.** The marginal cost
 * per added frame in the 3:2 row is 0 %, −20 %, −25 %, −17 %, 0 %; there is no break at
 * four, and five and six landing on the same 946 px is an argument that the sixth frame is
 * free *relative to the fifth*, not an argument for stopping at four. What the cap actually
 * encodes is that a burst you cannot hold in your head is not a comparison, and that a 946
 * px frame — 18 % of a 5200 px master — is below the point where sharpness is decidable at
 * all. That is a judgement; it is recorded as one rather than dressed as a measurement.
 *
 * The cap is also a memory budget, and here the arithmetic DOES bind: `scripts/
 * compare_survey.py` puts the median master's decoded bitmap at ~88 MB, so a 4-up at true
 * 1:1 holds ~352 MB of bitmap in the typical case — and this library's largest master
 * (4160x6240) decodes to ~103.8 MB, so the honest worst case at N = 4 is ~415 MB, not the
 * median figure. A fifth or sixth frame would not just cost more to judge; on the wrong
 * photo it costs another ~100 MB of resident bitmap for a comparison already past the point
 * of being useful.
 *
 * The number is a property of the DISPLAY, too, not of comparison. The same arithmetic on a
 * 14-inch laptop stage (1180x830) makes a 2-up cost 47 % immediately and flattens at 588 px
 * from three onwards — there, the honest cap is two. A display-derived ceiling is the
 * obvious follow-up and is deliberately not built here: this prototype's job is to find
 * that out, not to generalise it.
 */
export const MAX_COMPARE = 4

/** Gap between cells, in px. Sub-scale micro-spacing — no token step is this small. */
const CELL_GAP = 4
/** Padding around the whole grid. */
const STAGE_PAD = 4
/** Ceiling on wheel zoom, as a multiple of true 1:1 — past this you are inspecting JPEG. */
const OVERZOOM = 1.5
/** Wheel sensitivity: one notch is this fraction of a doubling. */
const WHEEL_STEP = 0.0022
/** Scale below which the stage counts as "fit" (float comparison, not an epsilon guess). */
const FIT_EPSILON = 1.001

// ── Geometry ─────────────────────────────────────────────────────────────────

/** Size of a box, in CSS px. */
type Size = { width: number; height: number }

/** The shared view: magnification, plus the image-centre offset in fitted-rect units. */
type View = { scale: number; ox: number; oy: number }

const FIT: View = { scale: 1, ox: 0, oy: 0 }

/**
 * Choose a column count for N frames on a given stage.
 *
 * Tries every column count and keeps the one that renders the largest single frame,
 * measured with the aspect ratio actually being compared. On a 5120x1440 ultrawide that
 * picks a single row for every N up to 4; on a 16:10 laptop it picks 2x2 at N = 4. The
 * layout is therefore a consequence of the display and the photographs, not a preference —
 * which is the whole reason it is computed rather than configured.
 *
 * @param count Number of frames.
 * @param stage Stage content box.
 * @param aspect Representative frame aspect (width / height).
 * @returns Column count in 1..count.
 */
export function bestColumns(count: number, stage: Size, aspect: number): number {
  if (count <= 1) return 1
  let best = 1
  let bestArea = -1
  for (let cols = 1; cols <= count; cols += 1) {
    const rows = Math.ceil(count / cols)
    const cellW = (stage.width - CELL_GAP * (cols - 1)) / cols
    const cellH = (stage.height - CELL_GAP * (rows - 1)) / rows
    if (cellW <= 0 || cellH <= 0) continue
    const fit = Math.min(cellW / aspect, cellH)
    const area = fit * fit * aspect
    if (area > bestArea) {
      bestArea = area
      best = cols
    }
  }
  return best
}

/** Aspect ratio of a row's *display* dimensions, falling back to 3:2 when unindexed. */
function aspectOf(row: PhotoRow): number {
  const width = row.width ?? 0
  const height = row.height ?? 0
  return width > 0 && height > 0 ? width / height : 1.5
}

/** Long edge of the master, in pixels; 0 when the index has no dimensions for it. */
function masterLongEdge(row: PhotoRow): number {
  return Math.max(row.width ?? 0, row.height ?? 0)
}

/** The rect a frame occupies inside its cell at scale 1. */
function fittedRect(row: PhotoRow, cell: Size): Size {
  const aspect = aspectOf(row)
  const fit = Math.min(cell.width / aspect, cell.height)
  return { width: Math.max(1, fit * aspect), height: Math.max(1, fit) }
}

/**
 * How far the shared offset may travel before a frame's edge enters its cell.
 *
 * Returned in fitted-rect units so it composes with {@link View}. Clamping against the
 * *tightest* frame is what keeps one number honest for all of them: with mixed aspects,
 * letting the offset run to the widest frame's limit would push the narrowest one's edge
 * into view while the others still showed photograph.
 */
function panLimit(rows: PhotoRow[], cell: Size, scale: number): { ox: number; oy: number } {
  let ox = Infinity
  let oy = Infinity
  for (const row of rows) {
    const rect = fittedRect(row, cell)
    ox = Math.min(ox, Math.max(0, (rect.width * scale - cell.width) / 2 / rect.width))
    oy = Math.min(oy, Math.max(0, (rect.height * scale - cell.height) / 2 / rect.height))
  }
  return { ox: Number.isFinite(ox) ? ox : 0, oy: Number.isFinite(oy) ? oy : 0 }
}

const clamp = (value: number, limit: number): number => Math.min(limit, Math.max(-limit, value))

// ── Props ────────────────────────────────────────────────────────────────────

export type PhotoCompareProps = {
  /** The compare set, in the order the user built it. */
  rows: PhotoRow[]
  /** Path of the focused frame — the one a rating key writes to. */
  focusPath: string | null
  onFocus: (path: string) => void
  /**
   * The route's own zoom boolean, shared with the single viewer so `Z` means one thing.
   * True is read as "go to 1:1"; the fine-grained scale in between lives here.
   */
  zoomed: boolean
  onZoomedChange: (zoomed: boolean) => void
  /**
   * Whether a magnification past the `view` tier's 2048 px may fetch the master itself.
   * Off, the proxy is simply upscaled — which is the A/B control for whether 1:1 matters.
   */
  trueResolution: boolean
}

// ── Component ────────────────────────────────────────────────────────────────

/**
 * N frames side by side under one shared zoom and pan.
 *
 * @param rows The compare set, in selection order.
 * @param focusPath Path of the focused frame.
 * @param onFocus Fired when a frame is clicked.
 * @param zoomed The route's fit/1:1 boolean.
 * @param onZoomedChange Fired when the internal scale crosses away from fit.
 * @param trueResolution Allow fetching the master past the `view` tier's ceiling.
 */
export function PhotoCompare({
  rows,
  focusPath,
  onFocus,
  zoomed,
  onZoomedChange,
  trueResolution,
}: PhotoCompareProps) {
  const stageRef = useRef<HTMLDivElement>(null)
  const [stage, setStage] = useState<Size>({ width: 0, height: 0 })
  const [view, setView] = useState<View>(FIT)
  /**
   * The live view, readable synchronously — the wheel handler's only source of truth.
   *
   * The listener is attached by hand (see below) and fires far faster than React can commit
   * a new `transform` on up to four large `<img>` elements. A handler closing over the
   * *rendered* `view` recomputes every event in a burst from the same stale scale, so all
   * but the last are discarded and an entire gesture moves the magnification by exactly one
   * notch. Measured in the running panel before this fix: six notches paced one per render
   * reached 3.00x (2^(0.264*6), as designed); the same six dispatched in one task reached
   * 1.20x, and 80 synchronous zoom-out notches moved 7.39x to 6.16x. The ref is written by
   * {@link applyView} *before* `setView`, so a burst composes.
   */
  const viewRef = useRef<View>(FIT)
  /**
   * Per-frame offset nudges, layered on the shared one — the `Alt` escape hatch.
   *
   * Kept as a delta rather than an absolute so a frame that has been nudged still follows
   * the shared pan afterwards. Cleared whenever the stage returns to fit, because a solo
   * offset that survives a reset is a frame silently not showing what the others show.
   */
  const [solo, setSolo] = useState<Record<string, { ox: number; oy: number }>>({})
  /** Masters that have finished decoding, by path. */
  const [fullReady, setFullReady] = useState<Record<string, boolean>>({})

  const hoveredRef = useRef<string | null>(null)
  const dragRef = useRef<{ x: number; y: number; path: string; solo: boolean } | null>(null)

  useLayoutEffect(() => {
    const node = stageRef.current
    if (node === null) return
    const measure = (): void => setStage({ width: node.clientWidth, height: node.clientHeight })
    measure()
    const observer = new ResizeObserver(measure)
    observer.observe(node)
    return () => observer.disconnect()
  }, [])

  const count = rows.length
  const focused = rows.find((row) => row.path === focusPath) ?? rows[0] ?? null

  const content: Size = {
    width: Math.max(0, stage.width - STAGE_PAD * 2),
    height: Math.max(0, stage.height - STAGE_PAD * 2),
  }
  const columns = bestColumns(count, content, focused === null ? 1.5 : aspectOf(focused))
  const gridRows = Math.max(1, Math.ceil(count / columns))
  const cellWidth = Math.max(1, (content.width - CELL_GAP * (columns - 1)) / columns)
  const cellHeight = Math.max(1, (content.height - CELL_GAP * (gridRows - 1)) / gridRows)
  // Memoised so it is a stable dependency for the callbacks below — a fresh object per
  // render would re-register the (non-passive, hand-attached) wheel listener every time.
  const cell = useMemo<Size>(
    () => ({ width: cellWidth, height: cellHeight }),
    [cellWidth, cellHeight],
  )

  const dpr = typeof window === 'undefined' ? 1 : window.devicePixelRatio

  /**
   * The scale at which one master pixel covers one device pixel — true 1:1.
   *
   * Derived from the FOCUSED frame, not from the tightest one: 1:1 is a statement about
   * the photograph you are judging, and letting a differently-sized peer cap it would
   * quietly show something other than 1:1 while the caption said otherwise.
   */
  const oneToOne = useMemo(() => {
    if (focused === null) return 1
    const rect = fittedRect(focused, cell)
    const fittedLong = Math.max(rect.width, rect.height) * dpr
    const master = masterLongEdge(focused)
    if (fittedLong <= 0 || master <= 0) return 1
    return Math.max(1, master / fittedLong)
  }, [focused, cell, dpr])

  // `Z` is the route's boolean and this is the stage that gives it a number. Only the
  // transitions are acted on: a `zoomed` that already agrees with the live scale must not
  // snap a wheel-zoomed view back to exactly 1:1 on an unrelated re-render.
  const zoomedRef = useRef(zoomed)
  useEffect(() => {
    if (zoomedRef.current === zoomed) return
    zoomedRef.current = zoomed
    if (zoomed) {
      viewRef.current = { scale: oneToOne, ox: 0, oy: 0 }
      setView(viewRef.current)
      return
    }
    viewRef.current = FIT
    setView(FIT)
    setSolo({})
  }, [zoomed, oneToOne])

  // …and the reverse direction, so a wheel zoom lights the sidebar's switch.
  useEffect(() => {
    const next = view.scale > FIT_EPSILON
    if (next === zoomedRef.current) return
    zoomedRef.current = next
    onZoomedChange(next)
  }, [view.scale, onZoomedChange])

  // A change of compare set is a change of subject: hold the magnification (you are still
  // checking focus) but drop the pan, which pointed at a place in a frame no longer here.
  const setKey = rows.map((row) => row.path).join(' ')
  useEffect(() => {
    setView((previous) => {
      const next = { ...previous, ox: 0, oy: 0 }
      viewRef.current = next
      return next
    })
    setSolo({})
  }, [setKey])

  /** Does this frame need more pixels than the `view` tier can supply? */
  const needsMaster = useCallback(
    (row: PhotoRow): boolean => {
      if (!trueResolution) return false
      const rect = fittedRect(row, cell)
      const needed = Math.max(rect.width, rect.height) * view.scale * dpr
      return needed > THUMB_LONG_EDGE.view && masterLongEdge(row) > THUMB_LONG_EDGE.view
    },
    [trueResolution, cell, view.scale, dpr],
  )

  // Fetch the masters the current magnification actually justifies, and only those. This
  // is the one place in the screen that may pull up to 21.7 MB a frame, so it is driven by the
  // magnification rather than by the selection — a compare set at fit costs nothing extra.
  useEffect(() => {
    const wanted = rows.filter((row) => needsMaster(row) && fullReady[row.path] !== true)
    if (wanted.length === 0) return
    let cancelled = false
    const images: HTMLImageElement[] = []
    for (const row of wanted) {
      const image = new Image()
      image.decoding = 'async'
      image.src = originalUrl(row)
      images.push(image)
      void image
        .decode()
        .then(() => {
          if (!cancelled) setFullReady((previous) => ({ ...previous, [row.path]: true }))
          return undefined
        })
        .catch(() => undefined)
    }
    return () => {
      cancelled = true
      // Abandon a master that is no longer justified — at ~17 MB a stranded request is
      // worth an order of magnitude more of the connection budget than a `view` frame.
      for (const image of images) if (!image.complete) image.src = ''
    }
  }, [rows, needsMaster, fullReady])

  // Masters decoded for a magnification the user has since left are the largest bitmaps on
  // the screen; drop them when the stage returns to fit so the renderer gets the memory back.
  useEffect(() => {
    if (view.scale <= FIT_EPSILON) setFullReady({})
  }, [view.scale])

  // The readout below is rendered INSIDE this component rather than lifted to the route.
  // A wheel zoom changes it continuously, and reporting it upwards would re-render the
  // filmstrip and the whole sidebar on every notch of a gesture whose entire job is to
  // stay at 60 fps.
  const usingMaster = focused !== null && fullReady[focused.path] === true

  // ── Interaction ────────────────────────────────────────────────────────────

  const applyView = useCallback(
    (next: View): void => {
      const limit = panLimit(rows, cell, next.scale)
      const settled = {
        scale: next.scale,
        ox: clamp(next.ox, limit.ox),
        oy: clamp(next.oy, limit.oy),
      }
      // Synchronously, before the state write: see {@link viewRef}.
      viewRef.current = settled
      setView(settled)
    },
    [rows, cell],
  )

  /**
   * Wheel / pinch zoom about the cursor.
   *
   * Registered by hand rather than through `onWheel` because React attaches wheel listeners
   * passively at the root, and a passive listener cannot `preventDefault()` — the page would
   * zoom (a trackpad pinch arrives as `ctrl+wheel`) instead of the photograph.
   */
  useEffect(() => {
    const node = stageRef.current
    if (node === null) return

    const onWheel = (event: WheelEvent): void => {
      event.preventDefault()
      const path = hoveredRef.current
      const row = rows.find((candidate) => candidate.path === path) ?? focused
      if (row === null) return

      // `viewRef`, never the rendered `view` — a gesture arrives faster than React commits.
      const live = viewRef.current
      const factor = Math.pow(2, -event.deltaY * WHEEL_STEP)
      const next = Math.min(oneToOne * OVERZOOM, Math.max(1, live.scale * factor))
      if (next === live.scale) return

      // Keep the point under the cursor still: its offset from the frame's centre, in
      // fitted-rect units, scales with the magnification, so the view offset absorbs the
      // difference. Falling back to the frame centre when the cursor is off-frame keeps a
      // keyboard-driven zoom centred rather than lurching towards a corner.
      const frame = node.querySelector<HTMLElement>(`[data-frame="${CSS.escape(row.path)}"]`)
      let px = 0
      let py = 0
      if (frame !== null) {
        const box = frame.getBoundingClientRect()
        const rect = fittedRect(row, cell)
        px = (event.clientX - (box.left + box.width / 2)) / rect.width
        py = (event.clientY - (box.top + box.height / 2)) / rect.height
      }
      const ratio = next / live.scale
      applyView({
        scale: next,
        ox: live.ox * ratio + px * (1 - ratio),
        oy: live.oy * ratio + py * (1 - ratio),
      })
    }

    node.addEventListener('wheel', onWheel, { passive: false })
    return () => node.removeEventListener('wheel', onWheel)
  }, [rows, focused, cell, oneToOne, applyView])

  const beginDrag = (event: ReactPointerEvent<HTMLDivElement>, path: string): void => {
    if (view.scale <= FIT_EPSILON) return
    event.currentTarget.setPointerCapture(event.pointerId)
    dragRef.current = { x: event.clientX, y: event.clientY, path, solo: event.altKey }
  }

  const continueDrag = (event: ReactPointerEvent<HTMLDivElement>, row: PhotoRow): void => {
    const drag = dragRef.current
    if (drag === null) return
    const rect = fittedRect(row, cell)
    const dx = (event.clientX - drag.x) / rect.width
    const dy = (event.clientY - drag.y) / rect.height
    dragRef.current = { ...drag, x: event.clientX, y: event.clientY }
    if (drag.solo) {
      // Alt breaks the link, for the frame under the pointer only. It exists for the case
      // shared pan is wrong for — two frames that are not the same composition — and it is
      // a nudge on top of the shared offset, so the pair re-links the moment Alt is let go.
      setSolo((previous) => {
        const current = previous[drag.path] ?? { ox: 0, oy: 0 }
        return { ...previous, [drag.path]: { ox: current.ox + dx, oy: current.oy + dy } }
      })
      return
    }
    applyView({ scale: view.scale, ox: view.ox + dx, oy: view.oy + dy })
  }

  const endDrag = (): void => {
    dragRef.current = null
  }

  // ── Render ─────────────────────────────────────────────────────────────────

  if (count === 0) {
    return (
      <Flex align="center" justify="center" h="100%" style={{ background: VX.surface.bg }}>
        <Text size="sm" c="dimmed">
          Nothing selected to compare.
        </Text>
      </Flex>
    )
  }

  return (
    <SimpleGrid
      ref={stageRef}
      cols={columns}
      spacing={CELL_GAP}
      verticalSpacing={CELL_GAP}
      p={STAGE_PAD}
      h="100%"
      w="100%"
      // `SimpleGrid` already emits `repeat(cols, minmax(0, 1fr))` for the columns; only the
      // ROW track has to be stated, because the default auto rows would let a 2x2 layout
      // size each row to its content and leave the frames different heights.
      style={{
        position: 'relative',
        background: VX.surface.bg,
        gridTemplateRows: `repeat(${gridRows}, minmax(0, 1fr))`,
        overflow: 'hidden',
      }}
    >
      {rows.map((row, index) => {
        const rect = fittedRect(row, cell)
        const nudge = solo[row.path] ?? { ox: 0, oy: 0 }
        const tx = (view.ox + nudge.ox) * rect.width
        const ty = (view.oy + nudge.oy) * rect.height
        const isFocus = row.path === focusPath
        const master = fullReady[row.path] === true
        return (
          <Box
            key={row.path}
            data-frame={row.path}
            onMouseEnter={() => {
              hoveredRef.current = row.path
            }}
            onMouseLeave={() => {
              if (hoveredRef.current === row.path) hoveredRef.current = null
            }}
            onPointerDown={(event) => beginDrag(event, row.path)}
            onPointerMove={(event) => continueDrag(event, row)}
            onPointerUp={endDrag}
            onPointerCancel={endDrag}
            onClick={() => onFocus(row.path)}
            style={{
              position: 'relative',
              overflow: 'hidden',
              borderRadius: VX.radiusCtrl,
              background: alpha(VX.neutral, 0.04),
              // Two neutral levels, no accent: the focused frame is the one a star key
              // writes to, and brightness alone says so — a saturated ring between two
              // photographs would be judged instead of them.
              boxShadow: isFocus
                ? `inset 0 0 0 2px ${alpha(VX.neutral, 0.8)}`
                : `inset 0 0 0 1px ${alpha(VX.neutral, 0.18)}`,
              cursor: view.scale > FIT_EPSILON ? 'grab' : 'pointer',
              // The browser must not claim the gesture we are about to read.
              touchAction: 'none',
            }}
          >
            <Flex align="center" justify="center" h="100%" w="100%">
              <img
                src={master ? originalUrl(row) : thumbUrl(row, 'view')}
                alt={row.filename}
                decoding="async"
                draggable={false}
                style={{
                  display: 'block',
                  width: rect.width,
                  height: rect.height,
                  objectFit: 'contain',
                  transform: `translate(${tx}px, ${ty}px) scale(${view.scale})`,
                  // Past true 1:1 there is nothing left to interpolate FROM, so smoothing
                  // would draw a soft frame as a smooth one — which is the judgement being
                  // made. Below it, smoothing is what makes a proxy look like a photograph.
                  imageRendering: view.scale > oneToOne ? 'pixelated' : 'auto',
                  opacity: isRejected(row) ? 0.45 : 1,
                }}
              />
            </Flex>

            {isRejected(row) && (
              <Flex
                aria-hidden
                align="center"
                justify="center"
                style={{ position: 'absolute', inset: 0, color: VX.status.bad }}
              >
                <IconX size={40} stroke={2.5} />
              </Flex>
            )}

            <FrameCaption
              index={index}
              filename={row.filename}
              rating={ratingOf(row)}
              master={master}
              focused={isFocus}
            />
          </Box>
        )
      })}

      {/*
        What you are actually looking at, stated in the two numbers that matter: the
        magnification, and whether it came out of the 2048 px proxy or the master itself.
        Without the second one "1:1" is a claim the screen cannot back — which is the
        whole reason this stage exists.
      */}
      <Flex
        gap={8}
        align="center"
        px={8}
        py={3}
        style={{
          position: 'absolute',
          left: STAGE_PAD + 6,
          top: STAGE_PAD + 6,
          borderRadius: VX.radiusPill,
          background: alpha(VX.surface.panel, 0.82),
          backdropFilter: 'blur(6px)',
          pointerEvents: 'none',
        }}
      >
        <Text size="xs" c="dimmed" ff="monospace">
          {count} up
        </Text>
        <Text size="xs" c="dimmed" ff="monospace">
          {view.scale <= FIT_EPSILON ? 'fit' : `${view.scale.toFixed(2)}x`}
        </Text>
        <Text size="xs" c="dimmed" ff="monospace">
          {usingMaster ? 'master' : `proxy 2048px`}
        </Text>
        <Text size="xs" c="dimmed" ff="monospace">
          {`1:1 at ${oneToOne.toFixed(2)}x`}
        </Text>
      </Flex>
    </SimpleGrid>
  )
}

type FrameCaptionProps = {
  index: number
  filename: string
  rating: number
  master: boolean
  focused: boolean
}

/** Per-frame caption: its position in the set, its name, its star, and its pixel source. */
function FrameCaption({ index, filename, rating, master, focused }: FrameCaptionProps) {
  return (
    <Flex
      gap={6}
      align="center"
      wrap="nowrap"
      px={7}
      py={2}
      style={{
        position: 'absolute',
        left: 6,
        bottom: 6,
        borderRadius: VX.radiusPill,
        background: alpha(VX.surface.panel, 0.82),
        backdropFilter: 'blur(6px)',
        pointerEvents: 'none',
        opacity: focused ? 1 : 0.62,
        maxWidth: 'calc(100% - 12px)',
      }}
    >
      <Text size="xs" ff="monospace" c={focused ? 'bright' : 'dimmed'}>
        {index + 1}
      </Text>
      <Text size="xs" c="dimmed" truncate="start">
        {filename}
      </Text>
      {rating > 0 && (
        <Text size="xs" ff="monospace" c="dimmed">
          {rating}
        </Text>
      )}
      {master && (
        <Text size="xs" ff="monospace" c="dimmed" title="Serving the master's own bytes">
          1:1
        </Text>
      )}
    </Flex>
  )
}
