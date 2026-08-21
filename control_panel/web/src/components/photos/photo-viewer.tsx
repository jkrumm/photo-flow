/**
 * The big single-photo view of the culling screen.
 *
 * Two measured facts shape the whole component:
 *
 * 1. A **cold** `view`-tier frame costs ~305 ms server-side, ~117 ms of which is the JPEG
 *    decode alone — a floor no tier choice moves. A **warm** one costs 3.4 ms. So on the
 *    first pass through a folder the sharp frame simply is not available in time, and
 *    blocking the paint on it is what makes stepping feel slow.
 * 2. The `grid` tier of the same photo is ~4 KB, and the filmstrip has almost always
 *    already fetched it under the identical URL — so it is effectively free.
 *
 * Hence a **two-stage paint**: the grid thumb goes up blurred the instant it decodes, laid
 * out in exactly the box the sharp frame will occupy, and the `view` frame cross-fades in
 * over it. On top of that sits the older guarantee — the *previous* photo stays painted
 * until the new one has something to show. The chain therefore reads
 * `previous photo → blurred placeholder → sharp frame`, with no unpainted moment, no white
 * flash and no reflow: every step is an opacity change over a fixed box.
 *
 * A naive `<img src={…}>` would unpaint the moment `src` changed and repaint when the new
 * bitmap landed — a white blink on every arrow key. Nothing here ever mounts an `<img>`
 * whose bitmap is not already decoded.
 */
import { useCallback, useEffect, useRef, useState } from 'react'
import { Flex, Loader, Text, UnstyledButton } from '@mantine/core'
import { useReducedMotion } from '@mantine/hooks'
import { IconPhotoOff } from '@tabler/icons-react'
import { motion } from 'motion/react'
import { MOTION_DURATION, MOTION_EASE_STANDARD } from 'basalt-ui'
import { VX, alpha } from 'basalt-ui/tokens'
import { originalUrl, thumbUrl, type PhotoRow } from '../../lib/photos'

/**
 * What is currently on screen. One object rather than three pieces of state, because the
 * three layers only ever move together and a torn combination (a placeholder belonging to
 * one photo under the sharp frame of another) is exactly the artefact to design out.
 */
type Stage = {
  /** Identity of the row being shown — its `view`-tier URL. */
  key: string
  /** Blurred `grid`-tier under-layer; dropped once the sharp frame has finished fading in. */
  placeholder: string | null
  /** Sharp `view`-tier frame; null until it has decoded. */
  sharp: string | null
  /** Last painted frame of the *previous* row, held on top while it fades out. */
  prev: string | null
}

/**
 * Blur applied to the upscaled 320 px grid thumb.
 *
 * Enough that the placeholder reads as a deliberate progressive load rather than a broken
 * low-res frame, and — deliberately — **no compensating `scale()`**. The usual trick of
 * scaling a blurred placeholder up a few percent to hide its soft edge would mean the
 * cross-fade also carries a few percent of zoom, which is precisely the visible shift this
 * component exists to avoid. A soft edge against the dark surface is the cheaper cost.
 */
const PLACEHOLDER_BLUR_PX = 12

/**
 * Zoomed sizing: lift every constraint and let the `<img>` lay out at its own intrinsic
 * size, so the magnification is a property of the bitmap in hand rather than of a number
 * this component computed.
 *
 * The alternative, recomputing the served box from the index's `width`/`height` and the
 * tier's long-edge target, has three ways to be wrong at once: it must know that those
 * numbers are DISPLAY dimensions (they are — `MetadataExtractor._display_size` transposes
 * an Orientation 5–8 frame, so a portrait Fuji shot stored as a 5200x3466 raster indexes as
 * 3466x5200), it must reproduce Pillow's `thumbnail()` rounding, and it silently produces a
 * transposed box for any row the index wrote before that transpose landed. The bitmap in
 * hand answers all three for free.
 *
 * **Which bitmap that is, is the whole point.** Serving the `view` tier here made "1:1"
 * mean one *proxy* pixel per screen pixel — 2048 px against a 5200–6240 px master, i.e.
 * 33–39 % of the linear resolution, upscaled. That is not a magnification of the
 * photograph, it is a magnification of the thumbnail. `trueResolution` swaps the master's
 * own bytes in once they have decoded, and the caller pays for it explicitly.
 *
 * **That swap is why {@link masterZoomSize} exists.** With both constraints merely lifted,
 * the box is the *bitmap's* intrinsic size, and the proxy and the master do not have the
 * same one: measured live, a zoom laid the 2048 px proxy out at `clientWidth` 2048 and then
 * — ~2.5 s later, mid-inspection — the same `<img>` re-laid out at 5199 CSS px, a 2.54x
 * geometry change (3.05x on the tallest masters here) with the scroll offsets not rebased,
 * so the photograph tripled in size and jumped towards the top-left while it was being
 * judged for sharpness. Pinning the box to the master's own display dimensions from the
 * first frame makes the swap what this component always claimed it was: identical geometry,
 * more pixels. The proxy is upscaled into that box for the ~2.5 s it is alone there, which
 * is exactly the "magnification of the thumbnail" the user asked to leave — but it is a
 * blur that resolves, not a layout that moves.
 */
const ZOOMED_SIZE = { maxWidth: 'none', maxHeight: 'none' } as const

/**
 * The zoomed box, pinned to the master's geometry when a master is on its way.
 *
 * `width`/`height` come from the index's DISPLAY dimensions, which for the master are exact
 * — no tier target, no `thumbnail()` rounding, nothing to reproduce. That is the difference
 * from the rejected "recompute the served box" idea in {@link ZOOMED_SIZE}: the objection
 * there was to predicting *Pillow's* output, and the master is not Pillow's output.
 *
 * Falls back to the intrinsic-size behaviour when the row carries no dimensions, or when
 * `trueResolution` is off and therefore no swap will happen.
 *
 * @param row The selected photo, or null.
 * @param trueResolution Whether the master will be swapped in behind this zoom.
 * @returns A style object for the zoomed `<img>`.
 */
function masterZoomSize(
  row: PhotoRow | null,
  trueResolution: boolean,
): { maxWidth: 'none'; maxHeight: 'none'; width?: number; height?: number } {
  if (!trueResolution || row === null) return ZOOMED_SIZE
  const { width, height } = row
  if (width === null || height === null || width <= 0 || height <= 0) return ZOOMED_SIZE
  return { ...ZOOMED_SIZE, width, height }
}
const FIT_SIZE = { maxWidth: '100%', maxHeight: '100%' } as const

/**
 * Sizing of the box the sharp frame lays out in — and the reason `h`/`w` are set rather
 * than `mih`/`miw`.
 *
 * `max-height: 100%` on the image only resolves if the box it sits in has a **definite**
 * height. Under `mih="100%"` alone the box's own `height` is `auto`, so per spec the
 * percentage computes to `none`: the frame is width-limited, and a portrait Fuji shot
 * (3466x5200) lays out ~1.5x taller than the viewer and is silently clipped by the
 * surface's `overflow: hidden` — which reads as the filmstrip covering the picture. The
 * placeholder layer never showed this because `position: absolute; inset: 0` gives it a
 * definite height for free.
 *
 * Zoom is the opposite case: the box must be free to exceed the viewport so the surface
 * can scroll, so there `min-height` is exactly right and `height` would cap the pan.
 */
const FIT_BOX = { h: '100%', w: '100%', p: 'xs' } as const
const ZOOM_BOX = { mih: '100%', miw: '100%', p: 0 } as const

/** Shared by every layer, so all three resolve to the same rendered rect. */
const CONTAIN = { objectFit: 'contain', display: 'block' } as const

export type PhotoViewerProps = {
  row: PhotoRow | null
  /** True while the surrounding list query is in flight (shows a spinner over the last frame). */
  loading?: boolean
  zoomed: boolean
  onToggleZoom: () => void
  /**
   * While zoomed, fetch the master itself instead of magnifying the 2048 px proxy.
   *
   * Off by default so the cull loop's measured behaviour is exactly what it was: the
   * request is 0.43–21.7 MB (median 7.3 MB) and decodes to a ~104 MB bitmap, which is not
   * something a viewer should do because a selection changed. It is fired by `Z`, and by
   * nothing else.
   */
  trueResolution?: boolean
}

/**
 * Big centred image with a two-stage, flash-free frame swap and a 1:1 pan mode.
 *
 * @param row Selected photo, or null when the filter set is empty.
 * @param loading Whether the owning query is refetching.
 * @param zoomed 1:1 pan mode — the image renders at its served pixel size and the surface scrolls.
 * @param onToggleZoom Fired on click / Enter / Space over the image surface.
 */
export function PhotoViewer({
  row,
  loading = false,
  zoomed,
  onToggleZoom,
  trueResolution = false,
}: PhotoViewerProps) {
  const reducedMotion = useReducedMotion()
  const sharpSrc = row === null ? null : thumbUrl(row, 'view')
  const placeholderSrc = row === null ? null : thumbUrl(row, 'grid')

  // The stage is mirrored into a ref because every writer is an async image callback, not a
  // render: they need to read the *committed* stage, and a functional setState updater is
  // the wrong place to make the "does this arrival still belong to the current row" call.
  const stageRef = useRef<Stage | null>(null)
  const [stage, setStage] = useState<Stage | null>(null)
  const [decoding, setDecoding] = useState(false)
  const [failed, setFailed] = useState(false)

  const apply = useCallback((next: Stage | null): void => {
    stageRef.current = next
    setStage(next)
  }, [])

  useEffect(() => {
    if (sharpSrc === null) {
      apply(null)
      setDecoding(false)
      setFailed(false)
      return
    }
    // Already fully resolved for this row — a re-run (reduced-motion flip) must not
    // re-enter the decode dance and re-trigger the fade.
    if (stageRef.current?.key === sharpSrc && stageRef.current.sharp !== null) return

    let cancelled = false
    const inFlight: HTMLImageElement[] = []
    setFailed(false)
    setDecoding(true)

    /** Promote a decoded bitmap onto the stage. Never downgrades sharp → placeholder. */
    const arrive = (src: string, sharp: boolean): void => {
      if (cancelled) return
      const current = stageRef.current
      const fresh = current === null || current.key !== sharpSrc

      if (fresh) {
        apply({
          key: sharpSrc,
          placeholder: sharp ? null : src,
          sharp: sharp ? src : null,
          // Reduced motion skips the cross-fade entirely — the swap is instant either way,
          // the outgoing layer only exists to hide the seam.
          prev: reducedMotion ? null : (current?.sharp ?? current?.placeholder ?? null),
        })
      } else if (sharp) {
        apply({ ...current, sharp: src, placeholder: reducedMotion ? null : current.placeholder })
      } else if (current.sharp === null && current.placeholder === null) {
        apply({ ...current, placeholder: src })
      }

      if (sharp) setDecoding(false)
    }

    const load = (src: string, sharp: boolean, onFail?: () => void): void => {
      const image = new Image()
      image.decoding = 'async'
      image.src = src
      inFlight.push(image)
      image
        .decode()
        .then(() => arrive(src, sharp))
        .catch(() => {
          if (cancelled) return
          // `decode()` also rejects on some browsers for reasons other than a broken
          // bitmap, so trust `naturalWidth` over the rejection before giving up: a
          // false failure here would strand the viewer on the previous photo.
          if (image.complete && image.naturalWidth > 0) {
            arrive(src, sharp)
            return
          }
          onFail?.()
        })
    }

    // Both tiers are requested in the same tick. The server generates them from ONE decode
    // of the source (`POST /warm` with both tiers, fired by the route), so the grid request
    // is not extra work on the backend — and in the common case it is a browser cache hit
    // put there by the filmstrip.
    if (placeholderSrc !== null) load(placeholderSrc, false)
    load(sharpSrc, true, () => {
      setDecoding(false)
      setFailed(true)
    })

    return () => {
      cancelled = true
      // Abandon the superseded fetches, don't just ignore their results.
      //
      // Holding an arrow key runs this effect per step, and every run asks for a `view`
      // frame. Left running, ~30 cold 305 ms requests would saturate the browser's
      // six-connection budget and the frame the user actually stopped on would queue
      // *behind* all of them — the exact stall this component exists to prevent. Clearing
      // `src` aborts the pending load and frees the slot immediately.
      for (const image of inFlight) {
        if (!image.complete) image.src = ''
      }
    }
  }, [placeholderSrc, sharpSrc, reducedMotion, apply])

  /**
   * The master's own bytes, decoded, once a zoom has asked for them.
   *
   * Kept out of the `Stage` machine on purpose: that machine exists to make a *selection
   * change* flash-free, and this is not a selection change — {@link masterZoomSize} pins
   * the box to the master's geometry before the master exists, so the sharper frame lands
   * in exactly the rect the proxy occupied and there is no seam to hide. Folding it in
   * would mean a fourth layer and a fourth way for the three to tear.
   */
  const [fullSrc, setFullSrc] = useState<string | null>(null)

  useEffect(() => {
    if (row === null || !zoomed || !trueResolution) {
      setFullSrc(null)
      return
    }
    const url = originalUrl(row)
    let cancelled = false
    const image = new Image()
    image.decoding = 'async'
    image.src = url
    image
      .decode()
      .then(() => {
        if (!cancelled) setFullSrc(url)
        return undefined
      })
      .catch(() => undefined)
    return () => {
      cancelled = true
      // Zooming out of a frame whose 17 MB master is still in flight must not hold a
      // connection to completion for a bitmap nobody will look at.
      if (!image.complete) image.src = ''
      setFullSrc(null)
    }
  }, [row, zoomed, trueResolution])

  /** Retire the under-layer once the sharp frame has fully covered it. */
  const dropPlaceholder = useCallback((): void => {
    const current = stageRef.current
    if (current === null || current.placeholder === null) return
    apply({ ...current, placeholder: null })
  }, [apply])

  const dropPrev = useCallback((): void => {
    const current = stageRef.current
    if (current === null || current.prev === null) return
    apply({ ...current, prev: null })
  }, [apply])

  const zoomStyle = zoomed ? masterZoomSize(row, trueResolution) : FIT_SIZE
  const empty = row === null
  const painted = stage !== null && (stage.sharp !== null || stage.placeholder !== null)
  const showSpinner = (loading || decoding) && !painted
  // 1:1 pan lays the sharp frame out at its own intrinsic size inside a scrolling box; an
  // `inset: 0` placeholder tracks the viewport rather than that box, so it is not shown.
  // The route resets zoom on every selection change, so this never hides a live placeholder.
  const placeholder = zoomed ? null : (stage?.placeholder ?? null)
  // The master supersedes the proxy only while zoomed, and only once it has decoded — so
  // the zoom is instant at proxy resolution and sharpens when the bytes land.
  const sharp = zoomed && fullSrc !== null ? fullSrc : (stage?.sharp ?? null)
  const outgoing = stage?.prev ?? null

  return (
    <UnstyledButton
      aria-label={
        row === null
          ? 'No photo selected'
          : `${row.filename} — ${zoomed ? 'zoomed, click to fit' : 'click to zoom 1:1'}`
      }
      aria-pressed={zoomed}
      disabled={empty}
      onClick={onToggleZoom}
      w="100%"
      h="100%"
      style={{
        position: 'relative',
        background: VX.surface.bg,
        cursor: empty ? 'default' : zoomed ? 'zoom-out' : 'zoom-in',
        // theme-allow: the 1:1 pan surface owns its own scroll node — this is the image
        // itself being panned, not a chrome column that should float a ScrollArea bar.
        overflow: zoomed ? 'auto' : 'hidden',
      }}
    >
      {/* Stage 1 — the blurred grid thumb. Same padding box, same `contain` fit, so the
          upgrade to the sharp frame is a pure opacity change over an identical rect. */}
      {placeholder !== null && (
        <Flex
          aria-hidden="true"
          align="center"
          justify="center"
          p="xs"
          style={{ position: 'absolute', inset: 0, pointerEvents: 'none' }}
        >
          <img
            src={placeholder}
            alt=""
            style={{ ...CONTAIN, ...FIT_SIZE, filter: `blur(${PLACEHOLDER_BLUR_PX}px)` }}
          />
        </Flex>
      )}

      {/* Stage 2 — the sharp frame. `position: relative` is load-bearing: without it this
          in-flow content would paint *below* the absolutely-positioned placeholder. */}
      <Flex
        align="center"
        justify="center"
        {...(zoomed ? ZOOM_BOX : FIT_BOX)}
        style={{ position: 'relative' }}
      >
        {sharp !== null &&
          (reducedMotion ? (
            <img
              key={sharp}
              src={sharp}
              alt={row?.filename ?? ''}
              decoding="async"
              style={{ ...CONTAIN, ...zoomStyle }}
            />
          ) : (
            // Always a `motion.img` on this branch: swapping the element type once the
            // placeholder is dropped would remount the node and reintroduce the blink.
            // `initial` is read at mount only, so a frame that arrives with no placeholder
            // under it (grid failed, or the view tier won the race) simply paints opaque
            // and lets the outgoing layer above cover the seam, exactly as before.
            <motion.img
              key={sharp}
              src={sharp}
              alt={row?.filename ?? ''}
              decoding="async"
              initial={{ opacity: placeholder === null ? 1 : 0 }}
              animate={{ opacity: 1 }}
              transition={{ duration: MOTION_DURATION.fast, ease: MOTION_EASE_STANDARD }}
              onAnimationComplete={dropPlaceholder}
              style={{ ...CONTAIN, ...zoomStyle }}
            />
          ))}

        {empty && (
          <Flex direction="column" align="center" gap="xs">
            <IconPhotoOff size={28} style={{ color: alpha(VX.neutral, 0.35) }} />
            <Text size="sm" c="dimmed">
              No photo selected
            </Text>
          </Flex>
        )}

        {/* Keyed off the SHARP frame, not `painted`: a view tier that 500s while the grid
            thumb succeeded would otherwise leave the user staring at a permanently blurred
            frame with no explanation. The message sits over the placeholder. */}
        {failed && !empty && sharp === null && (
          <Text size="sm" style={{ color: VX.bad }}>
            Could not load this image.
          </Text>
        )}
      </Flex>

      {/* Outgoing frame — painted over both stages, then removed once faded. */}
      {outgoing !== null && (
        <Flex
          aria-hidden="true"
          align="center"
          justify="center"
          p="xs"
          style={{ position: 'absolute', inset: 0, pointerEvents: 'none' }}
        >
          <motion.img
            key={outgoing}
            src={outgoing}
            alt=""
            initial={{ opacity: 1 }}
            animate={{ opacity: 0 }}
            transition={{ duration: MOTION_DURATION.fast, ease: MOTION_EASE_STANDARD }}
            onAnimationComplete={dropPrev}
            style={{ ...CONTAIN, ...FIT_SIZE }}
          />
        </Flex>
      )}

      {showSpinner && (
        <Flex
          align="center"
          justify="center"
          style={{ position: 'absolute', inset: 0, pointerEvents: 'none' }}
        >
          <Loader size="sm" />
        </Flex>
      )}
    </UnstyledButton>
  )
}
