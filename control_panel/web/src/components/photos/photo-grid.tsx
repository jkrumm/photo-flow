/**
 * Full-page contact sheet over the whole result set.
 *
 * The filmstrip and this component window the same rows with the same arithmetic, and
 * the resemblance stops there. A strip is a *one-dimensional neighbourhood* of the
 * focused frame — at 82 px tall on a 2 500 px stage it holds ~22 cells, i.e. 0.6 % of a
 * 3 797-row library, and the only question it can answer is "what is next to this one".
 * A grid trades magnification for extent: the same stage at 180 px cells holds ~130
 * frames, so a whole shoot is two or three flicks away and a *set* — every frame of one
 * burst, every frame with a rating, the six that are obviously out — becomes something
 * you can see at once and act on at once. That is the difference this screen is for.
 *
 * Three decisions are worth reading before changing anything here:
 *
 * 1. **Uniform cells, `object-fit: contain`.** The strip crops (`cover`) because a strip
 *    cell is a locator and a cropped locator is still recognisable. A grid cell is being
 *    *judged*, and a crop hides exactly what a triage pass looks at — where the subject
 *    sits in the frame, and whether it is level. Portrait frames therefore letterbox
 *    inside a 3:2 cell and pay ~33 % of the cell in dead space. That is the price of not
 *    lying about composition, and it is why the density control exists.
 *
 * 2. **Cells are absolutely positioned in a track of the full extent**, exactly as in the
 *    strip: uniform size makes "what is visible" pure arithmetic against `scrollTop`, with
 *    no measurement pass and no virtualization dependency. `rows.length` here is up to
 *    3 797, and the DOM holds ~200 of them.
 *
 * 3. **It owns no selection state.** Every gesture is reported to `onSelect` with its
 *    modifiers, and the route resolves them against the one marked set the compare stage
 *    also reads. A grid with its own notion of "selected" would be the second selection
 *    model on this screen, and two of them is how a screen starts disagreeing with itself.
 */
import { useCallback, useEffect, useLayoutEffect, useMemo, useRef, useState } from 'react'
import { Box, Flex, ScrollArea, Text, UnstyledButton } from '@mantine/core'
import { IconX } from '@tabler/icons-react'
import { VX, alpha } from 'basalt-ui/tokens'
import {
  THUMB_LONG_EDGE,
  isRejected,
  ratingOf,
  thumbUrl,
  type PhotoRow,
  type ThumbTier,
} from '../../lib/photos'
import type { SelectMods } from './photo-filmstrip'
import { LabelDot } from './star-rating'

/** A row as the grid reads it — `trashed` arrives only with `include_trashed=true`. */
type GridRow = PhotoRow & { trashed?: boolean }

/** Grid rows rendered beyond each edge of the viewport, so a flick never shows a hole. */
const OVERSCAN_ROWS = 2
/** Gap between cells, in px (sub-scale micro-spacing — no token equivalent). */
const GAP = 6
/** Padding inside the scroll viewport. */
const PAD = 8
/** Cell aspect (width / height): a 3:2 landscape frame, which is what the X-T4 shoots. */
const CELL_ASPECT = 1.5
/** Selection ring spread, in px. Painted outside the cell, so the track is inset by it. */
const RING = 2

/**
 * Density stops, as target cell WIDTH in px.
 *
 * The top stop is the `grid` tier's own long edge, and that is not a coincidence: past it
 * the sheet is magnifying a thumbnail. It is a *target*, though, not the resolved width —
 * columns divide the viewport, so the actual cell overshoots whenever the width does not
 * divide evenly (measured: a 979 px viewport at the 320 stop resolves to two 478 px
 * cells). {@link UPGRADE_RATIO} is what keeps that honest.
 *
 * Measured occupancy at these stops, on the four viewports in `scripts/grid_survey.py`,
 * is the table the F6 density finding is drawn from.
 */
export const DENSITY_STEPS: readonly number[] = [96, 128, 160, 200, 256, 320]

/**
 * How far a cell may exceed the `grid` tier before the sheet fetches `view` instead.
 *
 * A few percent of upscale is invisible; 50 % is a soft cell in a grid whose whole job is
 * to let you judge a photograph. So a cell that outgrows its tier asks for the next one —
 * the same rule the compare stage applies when a magnification outgrows `view`.
 *
 * **It is self-limiting, which is why it is safe.** A cell only gets that large when the
 * viewport fits very few of them: the worst case reachable from `DENSITY_STEPS` is ~12–16
 * cells on screen, i.e. ~6 MB of `view` frames, against the ~130 cells a dense sheet holds
 * at 10 KB each. Big cells and many cells are mutually exclusive.
 */
const UPGRADE_RATIO = 1.25

/** The density a fresh install starts at — see the F6 finding on judgeability. */
export const DEFAULT_DENSITY = 200

/** The ends of the density range, for the slider's bounds. */
export const MIN_DENSITY = Math.min(...DENSITY_STEPS)
export const MAX_DENSITY = Math.max(...DENSITY_STEPS)

export type PhotoGridProps = {
  /** The full ordered result set. Not a slice — the window is computed here. */
  rows: GridRow[]
  /** Index of the focused frame; -1 when nothing is selected. */
  selectedIndex: number
  /** Fired with the clicked index and the click's modifier state. */
  onSelect: (index: number, mods: SelectMods) => void
  /** Double-click / Enter — "show me this one big", i.e. leave the grid on this frame. */
  onActivate: (index: number) => void
  /** Indices in the marked set, focus included. */
  marked?: ReadonlySet<number>
  /**
   * Target cell width in px. The actual width is this rounded down to fit a whole number
   * of columns, so the sheet always ends flush with both edges.
   */
  cellSize: number
  /** Report the resolved column count upward, so the route's Up/Down keys move by a row. */
  onColumns?: (columns: number) => void
  /**
   * Ask the server to generate thumbnails for the paths now on screen.
   *
   * Fired with the visible window plus its overscan — NOT with a directional ring. The
   * viewer's prewarm is a ±15-frame ring around one focus, which is the right shape for
   * stepping a list and the wrong shape for a viewport holding 130 frames at once: it
   * would warm 15 of them and leave 115 to be generated by the request that displays
   * them. The caller debounces.
   *
   * The tier is reported too, because the sheet does not always want `grid` — see
   * {@link UPGRADE_RATIO}.
   */
  onWarm?: (paths: string[], tier: ThumbTier) => void
  /** Whether soft-deleted rows are being shown; they render desaturated and dimmed. */
  showTrashed?: boolean
}

/**
 * Windowed contact sheet.
 *
 * @param rows The full ordered result set.
 * @param selectedIndex Index of the focused frame; -1 when nothing is selected.
 * @param onSelect Fired with the clicked index and the click's modifier state.
 * @param onActivate Fired on double-click — the caller decides what "open" means.
 * @param marked Indices in the marked set, so members can be outlined.
 * @param cellSize Target cell width in px.
 * @param onColumns Reports the resolved column count.
 * @param onWarm Reports the visible paths, for a server-side prewarm.
 * @param showTrashed Render `trashed` rows greyed rather than as peers.
 */
export function PhotoGrid({
  rows,
  selectedIndex,
  onSelect,
  onActivate,
  marked,
  cellSize,
  onColumns,
  onWarm,
  showTrashed = false,
}: PhotoGridProps) {
  const cellRefs = useRef(new Map<number, HTMLElement>())
  const [scrollTop, setScrollTop] = useState(0)
  const [view, setView] = useState({ width: 0, height: 0 })

  /**
   * The scroll viewport, held in STATE rather than in a ref — and that is load-bearing.
   *
   * This component returns early while the result set is empty, so on a cold load the
   * `ScrollArea` does not exist during the first commit. A `useLayoutEffect([])` therefore
   * ran against `null`, attached no `ResizeObserver`, and never ran again once the rows
   * arrived and the viewport finally mounted: `view.width` stayed 0, so the sheet resolved
   * one column of 1 px cells and rendered as a hairline. A state-holding ref re-runs the
   * effect on the commit that mounts the node, which is the commit that matters.
   */
  const [viewport, setViewport] = useState<HTMLDivElement | null>(null)

  useLayoutEffect(() => {
    if (viewport === null) return
    const measure = (): void =>
      setView({ width: viewport.clientWidth, height: viewport.clientHeight })
    measure()
    const observer = new ResizeObserver(measure)
    observer.observe(viewport)
    return () => observer.disconnect()
  }, [viewport])

  // Columns come from the viewport, not from a breakpoint: the sheet is a measuring
  // surface and a column that is 40 px narrower than asked for is better than a gutter.
  const inner = Math.max(1, view.width - PAD * 2)
  const columns = Math.max(1, Math.floor((inner + GAP) / (cellSize + GAP)))
  const cellWidth = Math.max(1, Math.floor((inner - GAP * (columns - 1)) / columns))
  const cellHeight = Math.max(1, Math.round(cellWidth / CELL_ASPECT))
  // The tier the cell has earned — see `UPGRADE_RATIO`.
  const tier: ThumbTier = cellWidth > THUMB_LONG_EDGE.grid * UPGRADE_RATIO ? 'view' : 'grid'
  const rowStep = cellHeight + GAP
  const gridRows = Math.ceil(rows.length / columns)
  const trackHeight = gridRows === 0 ? 0 : gridRows * rowStep - GAP + RING * 2

  useEffect(() => {
    onColumns?.(columns)
  }, [columns, onColumns])

  /**
   * The visible window — the viewport's own rows and nothing else.
   *
   * **Deliberately NOT widened to contain the focus, and that is the F6 correction to the
   * filmstrip's arithmetic.** The strip widens its window to span from the focus to the
   * scroll position, which is harmless there because the two are never far apart: the
   * strip re-centres on every step. A sheet is scrolled independently of the focus, so the
   * same rule makes the window the whole span between them — measured here, a fling to the
   * bottom of an unfiltered library with the focus still on row 0 mounted **all 3 515**
   * cells and fired a prewarm for every path in the result set. A span is not a window.
   *
   * The focused cell is mounted separately instead (see `focusOutside` below), which is
   * all `scrollIntoView` actually needed.
   */
  const [start, end] = useMemo(() => {
    if (rows.length === 0) return [0, 0] as const
    const height = view.height > 0 ? view.height : rowStep * 4
    const fromRow = Math.floor(scrollTop / rowStep) - OVERSCAN_ROWS
    const toRow = Math.ceil((scrollTop + height) / rowStep) + OVERSCAN_ROWS
    const from = Math.max(0, fromRow * columns)
    const to = Math.min(rows.length, Math.max(0, toRow) * columns)
    return [from, to] as const
  }, [rows.length, scrollTop, view.height, rowStep, columns])

  // Warm exactly what is mounted. Keyed on the window rather than on the focus, because in
  // a grid those are different sets — see `onWarm`.
  useEffect(() => {
    if (onWarm === undefined || end <= start) return
    onWarm(
      rows.slice(start, end).map((row) => row.path),
      tier,
    )
  }, [rows, start, end, tier, onWarm])

  // `block: 'nearest'` so stepping along a row never scrolls, and stepping off the end of
  // one moves by exactly one row — the sheet stays still under the eye until it has to move.
  useEffect(() => {
    if (selectedIndex < 0) return
    cellRefs.current.get(selectedIndex)?.scrollIntoView({ block: 'nearest' })
  }, [selectedIndex])

  const registerCell = useCallback((index: number, node: HTMLElement | null) => {
    if (node === null) cellRefs.current.delete(index)
    else cellRefs.current.set(index, node)
  }, [])

  if (rows.length === 0) {
    return (
      <Flex align="center" justify="center" h="100%" style={{ background: VX.surface.bg }}>
        <Text size="sm" c="dimmed">
          No photos match the current filters.
        </Text>
      </Flex>
    )
  }

  /**
   * The cell indices this commit mounts: the viewport window, plus the focused cell when
   * it sits outside it.
   *
   * That one extra cell is all `scrollIntoView` ever needed after a Home/End jump or a
   * filter change — and it is the whole of what the discarded "widen the window to the
   * focus" rule was trying to buy.
   */
  const mounted: number[] = []
  for (let index = start; index < end; index += 1) mounted.push(index)
  if (selectedIndex >= 0 && (selectedIndex < start || selectedIndex >= end)) {
    mounted.push(selectedIndex)
  }

  return (
    <ScrollArea
      viewportRef={setViewport}
      type="scroll"
      scrollHideDelay={1200}
      scrollbars="y"
      scrollbarSize={10}
      h="100%"
      onScrollPositionChange={({ y }) => setScrollTop(y)}
      style={{ background: VX.surface.bg }}
    >
      <Box p={PAD}>
        <Box h={trackHeight} style={{ position: 'relative' }}>
          {mounted.map((index) => {
            const row = rows[index]
            if (row === undefined) return null
            const column = index % columns
            const gridRow = Math.floor(index / columns)
            const selected = index === selectedIndex
            const isMarked = !selected && marked !== undefined && marked.has(index)
            const rating = ratingOf(row)
            const trashed = showTrashed && row.trashed === true
            const rejected = isRejected(row)
            return (
              <UnstyledButton
                key={row.path}
                ref={(node: HTMLButtonElement | null) => registerCell(index, node)}
                aria-label={`${row.filename}${rating > 0 ? `, rating ${rating}` : ''}${
                  rejected ? ', rejected' : ''
                }${trashed ? ', trashed' : ''}${isMarked ? ', selected' : ''}`}
                aria-current={selected}
                onClick={(event) =>
                  onSelect(index, { range: event.shiftKey, toggle: event.metaKey || event.ctrlKey })
                }
                onDoubleClick={() => onActivate(index)}
                w={cellWidth}
                h={cellHeight}
                style={{
                  position: 'absolute',
                  left: column * (cellWidth + GAP),
                  top: RING + gridRow * rowStep,
                  overflow: 'hidden',
                  borderRadius: VX.radiusCtrl,
                  // A cell is a window onto a photograph, so it needs a floor to sit on —
                  // with `contain`, a portrait frame shows the backing on two sides and an
                  // untinted one would read as a hole in the sheet.
                  background: alpha(VX.neutral, 0.06),
                  boxShadow: selected
                    ? `0 0 0 ${RING}px ${alpha(VX.neutral, 0.85)}`
                    : isMarked
                      ? `0 0 0 ${RING}px ${alpha(VX.accent, 0.7)}`
                      : 'none',
                  zIndex: selected ? 2 : isMarked ? 1 : 0,
                  cursor: 'pointer',
                }}
              >
                <img
                  src={thumbUrl(row, tier)}
                  alt=""
                  loading="lazy"
                  decoding="async"
                  style={{
                    width: '100%',
                    height: '100%',
                    // See the header: a triage cell is judged, so it is never cropped.
                    objectFit: 'contain',
                    display: 'block',
                    filter: trashed ? 'grayscale(1)' : undefined,
                    opacity: trashed ? 0.4 : rejected ? 0.4 : 1,
                  }}
                />
                {rejected && !trashed && (
                  <Flex
                    aria-hidden
                    align="center"
                    justify="center"
                    style={{ position: 'absolute', inset: 0, color: VX.status.bad }}
                  >
                    <IconX size={Math.max(18, Math.round(cellHeight * 0.28))} stroke={3} />
                  </Flex>
                )}
                {rating > 0 && (
                  <Text
                    ff="monospace"
                    fz={VX.text.micro}
                    lh={1}
                    px={3}
                    py={1}
                    style={{
                      position: 'absolute',
                      left: 3,
                      bottom: 3,
                      color: alpha(VX.neutral, 0.85),
                      background: alpha(VX.surface.bg, 0.66),
                      borderRadius: VX.radiusPill,
                    }}
                  >
                    {rating}
                  </Text>
                )}
                {row.label !== '' && (
                  <Box style={{ position: 'absolute', right: 4, bottom: 4, opacity: 0.85 }}>
                    <LabelDot label={row.label} size={7} />
                  </Box>
                )}
              </UnstyledButton>
            )
          })}
        </Box>
      </Box>
    </ScrollArea>
  )
}
