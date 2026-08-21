/**
 * Horizontal filmstrip of the current result set.
 *
 * A shoot is routinely 2 000+ frames, so the strip is windowed: every cell has the
 * same width, which turns "what is visible" into pure index arithmetic against
 * `scrollLeft` — no measurement pass, no virtualization dependency. Cells are
 * absolutely positioned inside a track sized to the full extent, so the scrollbar
 * stays honest while only ~30 `<img>` nodes exist at a time.
 *
 * The cell is dense on purpose: the thumbnail is the content, so the rating and the
 * colour label are micro annotations in the bottom corners and appear only when the
 * photo actually carries them.
 */
import { useCallback, useEffect, useLayoutEffect, useMemo, useRef, useState } from 'react'
import { Box, Flex, ScrollArea, Text, UnstyledButton } from '@mantine/core'
import { IconX } from '@tabler/icons-react'
import { VX, alpha } from 'basalt-ui/tokens'
import { isRejected, ratingOf, thumbUrl, type PhotoRow } from '../../lib/photos'
import { LabelDot } from './star-rating'
import classes from './photos-screen.module.css'

/**
 * A row as the strip reads it. `trashed` is set by `GET /api/photos?include_trashed=true`;
 * it is declared structurally here so the strip does not depend on the shared row type
 * having caught up with the endpoint.
 */
type StripRow = PhotoRow & { trashed?: boolean }

/** Cells rendered beyond each edge of the viewport, so a fast scroll never shows a hole. */
const OVERSCAN = 16
/** Gap between cells, in px (sub-scale micro-spacing — no token equivalent). */
const GAP = 3
/** Cell width as a multiple of the strip height (a 3:2 landscape frame plus a little air). */
const ASPECT = 1.36
/**
 * The selection ring's spread, and the track's vertical inset.
 *
 * The ring is a `box-shadow`, so it paints outside the cell's own box: without the inset the
 * scroll viewport clipped its top edge, and a selection outlined on three sides reads as a
 * rendering bug rather than as a selection.
 */
const RING = 2

/**
 * Breathing room at the two ends of the track, so the first and last frame are held off the
 * window rather than butted against it — and their selection ring has room to draw.
 */
const EDGE = 6

/**
 * Modifier state of a click, resolved by the caller into what it means for the selection.
 *
 * The strip reports the gesture; it does not own the selection model. `range` is Shift
 * (extend from the anchor), `toggle` is Cmd/Ctrl (add or remove one frame) — the two
 * conventions every file browser and every photo tool already teaches.
 */
export type SelectMods = { range: boolean; toggle: boolean }

export type PhotoFilmstripProps = {
  rows: PhotoRow[]
  /**
   * The FOCUSED frame — the one the viewer shows and a rating key writes to.
   *
   * Still a single index, deliberately. Multi-select added a compare *set*; it did not
   * make the focus ambiguous, and a screen whose star key has no single target is a
   * screen that cannot be culled with.
   */
  selectedIndex: number
  onSelect: (i: number, mods: SelectMods) => void
  /**
   * Indices in the MARKED set, focus included.
   *
   * One set, shared with the contact sheet and read by the compare stage — see the route's
   * `marked`. The strip only draws it; what it means (a comparison here, a batch there) is
   * the surface's business, not the strip's.
   */
  marked?: ReadonlySet<number>
  /** Strip height in px; the cell width is derived from it. */
  height?: number
  /**
   * Whether soft-deleted rows are being shown. Rows carrying `trashed: true` then
   * render desaturated and dimmed — reviewable in place, still selectable.
   */
  showTrashed?: boolean
}

/**
 * Windowed horizontal thumbnail strip that keeps the selection centred.
 *
 * @param rows The full ordered result set (not a slice — the window is computed here).
 * @param selectedIndex Index of the focused frame; -1 when nothing is selected.
 * @param onSelect Fired with the clicked index and the click's modifier state.
 * @param marked Indices in the marked set, so members can be outlined.
 * @param height Strip height in px.
 * @param showTrashed Render `trashed` rows greyed out rather than as peers.
 */
export function PhotoFilmstrip({
  rows,
  selectedIndex,
  onSelect,
  marked,
  height = 72,
  showTrashed = false,
}: PhotoFilmstripProps) {
  const cellRefs = useRef(new Map<number, HTMLElement>())
  const [scrollLeft, setScrollLeft] = useState(0)
  const [viewWidth, setViewWidth] = useState(0)
  /**
   * The scroll viewport, in state rather than in a ref.
   *
   * Same reason as `PhotoGrid`: the early return below means the `ScrollArea` is absent
   * during the first commit of a cold load, so a `useLayoutEffect([])` measured nothing
   * and never re-ran. Here it degraded quietly rather than visibly — `viewWidth` stayed 0,
   * the window fell back to its `step * 8` guess, and the strip mounted a fixed ~40-cell
   * window regardless of how wide it actually was.
   */
  const [viewport, setViewport] = useState<HTMLDivElement | null>(null)

  const cellWidth = Math.round(height * ASPECT)
  const step = cellWidth + GAP
  // Leading inset, the frames, then the trailing inset. Both ends are plain EDGE now: the strip's
  // box ends at the window edge, so a trailing inset is worth exactly what it measures.
  const trackWidth = rows.length === 0 ? 0 : EDGE + (rows.length * step - GAP) + EDGE

  // Track the viewport width so the window math has a real measure to work from.
  useLayoutEffect(() => {
    if (viewport === null) return
    setViewWidth(viewport.clientWidth)
    const observer = new ResizeObserver(() => setViewWidth(viewport.clientWidth))
    observer.observe(viewport)
    return () => observer.disconnect()
  }, [viewport])

  /**
   * The visible window — the viewport's own cells, and nothing between them and the
   * selection.
   *
   * It used to be *widened* to contain the selection, so that `scrollIntoView` had a
   * mounted node to reach after a Home/End jump. That silently made the window the whole
   * SPAN between the scroll position and the selection: drag the strip to the far end of a
   * 3 500-frame result set while the selection stays behind and every cell in between
   * mounts. Stepping re-centres the strip, so it rarely bit here — F6 found it by giving
   * the same arithmetic a scroll position that moves independently of the focus.
   *
   * The selected cell is mounted separately instead, which is all that was ever needed.
   */
  const [start, end] = useMemo(() => {
    if (rows.length === 0) return [0, 0] as const
    const width = viewWidth > 0 ? viewWidth : step * 8
    const from = Math.floor(scrollLeft / step) - OVERSCAN
    const to = Math.ceil((scrollLeft + width) / step) + OVERSCAN
    return [Math.max(0, from), Math.min(rows.length, to)] as const
  }, [rows.length, scrollLeft, viewWidth, step])

  // Keep the selection in view. `block: 'nearest'` so a page-level scroll is never
  // dragged along; `inline: 'center'` so stepping reads as the strip moving under
  // a fixed playhead rather than the selection creeping to an edge.
  useEffect(() => {
    if (selectedIndex < 0) return
    cellRefs.current.get(selectedIndex)?.scrollIntoView({ block: 'nearest', inline: 'center' })
  }, [selectedIndex])

  const registerCell = useCallback((index: number, node: HTMLElement | null) => {
    if (node === null) cellRefs.current.delete(index)
    else cellRefs.current.set(index, node)
  }, [])

  if (rows.length === 0) {
    return (
      <Flex align="center" justify="center" h={height} style={{ background: VX.surface.bg }}>
        <Text size="xs" c="dimmed">
          No photos match the current filters.
        </Text>
      </Flex>
    )
  }

  /** Window indices, plus the selection when it sits outside the window. */
  const mounted: number[] = []
  for (let index = start; index < end; index += 1) mounted.push(index)
  if (selectedIndex >= 0 && (selectedIndex < start || selectedIndex >= end)) {
    mounted.push(selectedIndex)
  }

  return (
    <ScrollArea
      viewportRef={setViewport}
      // macOS behaviour: the bar appears while the strip is actually moving and fades out again.
      // `type="hover"` parked a permanent hairline under 3 000 frames — a thumb a few pixels
      // wide, unusable as a control and pointing at nothing while you cull. Shown only during a
      // scroll it answers the one question it can answer ("where am I in the shoot?") and then
      // gets out of the way. It overlays the frames rather than reserving a row, and the thumb
      // treatment is basalt's own (see `styles.css`), so it matches every other bar in the app.
      type="scroll"
      scrollHideDelay={1200}
      scrollbars="x"
      scrollbarSize={10}
      className={classes.filmstripScroll}
      h={height + RING * 2}
      onScrollPositionChange={({ x }) => setScrollLeft(x)}
      style={{ background: VX.surface.bg }}
    >
      <Box w={trackWidth} h={height + RING * 2} style={{ position: 'relative' }}>
        {mounted.map((index) => {
          const row: StripRow | undefined = rows[index]
          if (row === undefined) return null
          const selected = index === selectedIndex
          // A compare member that is not the focus. Two neutral levels rather than a
          // second colour: the strip's whole language is brightness over a wall of
          // photographs, and "in the comparison" is a weaker statement than "this one".
          const inCompare = !selected && marked !== undefined && marked.has(index)
          const rating = ratingOf(row)
          const trashed = showTrashed && row.trashed === true
          // A reject is dimmed but NOT desaturated. The distinction is the point of the
          // three-state model: a trashed frame has left the library, a rejected one is a
          // judgement the user can still reverse — and judging colour is half of why they
          // are looking at it. Greyscale would make an un-reject a decision made blind.
          const rejected = isRejected(row)
          return (
            <UnstyledButton
              key={row.path}
              ref={(node: HTMLButtonElement | null) => registerCell(index, node)}
              aria-label={`${row.filename}${rating > 0 ? `, rating ${rating}` : ''}${
                rejected ? ', rejected' : ''
              }${trashed ? ', trashed' : ''}${inCompare ? ', selected' : ''}`}
              aria-current={selected}
              onClick={(event) =>
                onSelect(index, { range: event.shiftKey, toggle: event.metaKey || event.ctrlKey })
              }
              w={cellWidth}
              h={height}
              style={{
                position: 'absolute',
                left: EDGE + index * step,
                top: RING,
                overflow: 'hidden',
                borderRadius: VX.radiusCtrl,
                background: alpha(VX.neutral, 0.08),
                // Only the selection is outlined. The 1px ring every other cell used to carry
                // drew a grid of hairlines across the strip — with the thumbnails butted up at
                // a 3px gap, the frames already separate themselves.
                //
                // Neutral, not the accent: the ring says "you are here" over a wall of
                // photographs, and a saturated blue edge competes with their colour for no
                // added meaning. Brightness alone marks the position.
                boxShadow: selected
                  ? `0 0 0 ${RING}px ${alpha(VX.neutral, 0.8)}`
                  : inCompare
                    ? `0 0 0 ${RING}px ${alpha(VX.neutral, 0.4)}`
                    : 'none',
                // The ring spreads into the gap on both sides, so the selected cell has to
                // paint last whichever way the window is walked.
                zIndex: selected ? 2 : inCompare ? 1 : 0,
                opacity: selected || inCompare ? 1 : 0.82,
                cursor: 'pointer',
              }}
            >
              <img
                src={thumbUrl(row, 'grid')}
                alt=""
                width={cellWidth}
                height={height}
                loading="lazy"
                decoding="async"
                style={{
                  width: '100%',
                  height: '100%',
                  objectFit: 'cover',
                  display: 'block',
                  // A culled frame stays legible but must never read as a peer of
                  // the keepers it sits between.
                  filter: trashed ? 'grayscale(1)' : undefined,
                  opacity: trashed ? 0.4 : rejected ? 0.35 : 1,
                }}
              />
              {rejected && !trashed && (
                <Flex
                  aria-hidden
                  align="center"
                  justify="center"
                  style={{
                    position: 'absolute',
                    inset: 0,
                    // The mark, not the dimming, is what makes a reject unambiguous — a
                    // dim frame alone reads as "still loading" at strip size.
                    color: VX.status.bad,
                  }}
                >
                  <IconX size={18} stroke={3} />
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
                    left: 2,
                    bottom: 2,
                    color: alpha(VX.neutral, 0.85),
                    background: alpha(VX.surface.bg, 0.66),
                    borderRadius: VX.radiusPill,
                  }}
                >
                  {rating}
                </Text>
              )}
              {row.label !== '' && (
                <Box style={{ position: 'absolute', right: 3, bottom: 3, opacity: 0.85 }}>
                  <LabelDot label={row.label} size={6} />
                </Box>
              )}
            </UnstyledButton>
          )
        })}
      </Box>
    </ScrollArea>
  )
}
