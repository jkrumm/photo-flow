/**
 * The culling screen's filter panel — one section of the right-hand sidebar.
 *
 * Every control writes a complete `PhotoFilters` object back through `onChange` —
 * the route owns the state (it lives in the URL), this component is pure chrome.
 * Option counts come from `facets`, which the API computes with each dimension's
 * own filter left open, so an option shown here can never yield zero results.
 *
 * It was a one-row top bar until the sidebar unification, which is what forced camera /
 * lens / label / the EXIF ranges behind a popover: there was no horizontal room for a
 * real control surface. A vertical column has that room, so the popover is gone and every
 * control is visible at once — but only while the section is open, so a cull session still
 * gives the whole viewport to the photo. Root moved out entirely; it is a folder, and
 * folders live in their own section.
 */
import { useEffect, useMemo, useState } from 'react'
import {
  Box,
  Button,
  Group,
  MultiSelect,
  RangeSlider,
  Stack,
  Text,
  TextInput,
  Tooltip,
  UnstyledButton,
} from '@mantine/core'
import { useDebouncedCallback } from '@mantine/hooks'
import { IconSearch, IconStar, IconStarFilled, IconStarOff, IconX } from '@tabler/icons-react'
import { VX, alpha } from 'basalt-ui/tokens'
import {
  activeFilterCount,
  clearFilters,
  ratingFacetCount,
  toggleValue,
  type Facets,
  type FacetValue,
  type PhotoFilters,
  type RangeFacet,
} from '../../lib/photos'
import { PF } from '../../lib/series'
import { LabelDot } from './star-rating'

const STAR_RATINGS = [1, 2, 3, 4, 5] as const

// ── Value formatting ─────────────────────────────────────────────────────────

function formatShutter(seconds: number): string {
  if (seconds >= 1) return `${Number.isInteger(seconds) ? seconds : seconds.toFixed(1)}s`
  return `1/${Math.round(1 / Math.max(seconds, 1e-6))}`
}

const formatIso = (v: number): string => `ISO ${Math.round(v)}`
const formatAperture = (v: number): string => `f/${v.toFixed(1)}`
const formatFocal = (v: number): string => `${Math.round(v)}mm`

// ── Rating toggles ───────────────────────────────────────────────────────────

type RatingToggleProps = {
  active: boolean
  label: string
  onToggle: () => void
  children: React.ReactNode
}

function RatingToggle({ active, label, onToggle, children }: RatingToggleProps) {
  return (
    <Tooltip label={label} withArrow openDelay={250}>
      <UnstyledButton
        aria-label={label}
        aria-pressed={active}
        onClick={onToggle}
        px={1}
        style={{ lineHeight: 0, cursor: 'pointer' }}
      >
        {children}
      </UnstyledButton>
    </Tooltip>
  )
}

type RatingFilterProps = {
  selected: number[]
  facets: Facets | undefined
  onToggle: (rating: number) => void
}

/**
 * Six independent glyph toggles — "unrated" plus one per star — whose enabled set
 * is the OR-set the API already takes. Counts ride in the tooltip rather than in
 * permanent text: they matter while choosing, not while culling.
 */
function RatingFilter({ selected, facets, onToggle }: RatingFilterProps) {
  const off = alpha(VX.neutral, 0.3)
  const suffix = (rating: number): string =>
    facets === undefined ? '' : ` · ${ratingFacetCount(facets, rating)}`
  const unrated = selected.includes(0)

  return (
    <Group gap={4} wrap="nowrap" style={{ flexShrink: 0 }}>
      <RatingToggle active={unrated} label={`Unrated${suffix(0)}`} onToggle={() => onToggle(0)}>
        <IconStarOff size={16} style={{ color: unrated ? VX.neutral : off }} />
      </RatingToggle>
      {STAR_RATINGS.map((rating) => {
        const active = selected.includes(rating)
        return (
          <RatingToggle
            key={rating}
            active={active}
            label={`${rating} star${rating === 1 ? '' : 's'}${suffix(rating)}`}
            onToggle={() => onToggle(rating)}
          >
            {active ? (
              <IconStarFilled size={16} style={{ color: PF.published }} />
            ) : (
              <IconStar size={16} style={{ color: off }} />
            )}
          </RatingToggle>
        )
      })}
    </Group>
  )
}

// ── Range slider ─────────────────────────────────────────────────────────────

type ScaleKind = 'linear' | 'log'

/**
 * Shutter speed spans 1/4000s to 30s — five orders of magnitude, which a linear
 * slider spends entirely in the first two pixels. That dimension rides a log scale.
 */
const SCALES: Record<ScaleKind, { to: (v: number) => number; from: (v: number) => number }> = {
  linear: { to: (v) => v, from: (v) => v },
  log: { to: (v) => Math.log10(Math.max(v, 1e-6)), from: (v) => 10 ** v },
}

/**
 * One bucket of `facets.histograms` (`GET /api/photos/facets`). Declared structurally
 * rather than imported: the backdrop only needs the shape, so it stays independent of
 * what the API layer names the wire type — and of whether it has declared it yet.
 */
type HistogramBin = { lo: number; hi: number; count: number }

type HistogramKey = 'iso' | 'aperture' | 'shutter' | 'focal'

type WithHistograms = Facets & {
  histograms?: Partial<Record<HistogramKey, readonly HistogramBin[]>>
}

/** Backdrop geometry, in the histogram's own 100 × HIST_HEIGHT viewBox units. */
type HistogramBar = { x: number; w: number; y: number; h: number; mid: number }

const HIST_HEIGHT = 24
const HIST_WIDTH = 100

const clamp01 = (v: number): number => Math.min(1, Math.max(0, v))

/**
 * Project the API's buckets onto the slider's coordinate space.
 *
 * The server buckets ISO / shutter / focal logarithmically while some of those
 * sliders run linear, so a bar's width is derived from its own edges rather than
 * assumed uniform — otherwise the backdrop would disagree with the handle above it.
 * Purely geometric: nothing here depends on the current selection, so it survives
 * a drag untouched.
 */
function computeBars(
  bins: readonly HistogramBin[],
  toScale: (v: number) => number,
  sMin: number,
  sMax: number,
): HistogramBar[] {
  const span = sMax - sMin
  if (bins.length === 0 || span <= 0) return []
  let peak = 0
  for (const bin of bins) peak = Math.max(peak, bin.count)
  if (peak <= 0) return []

  const bars: HistogramBar[] = []
  for (const bin of bins) {
    const x0 = clamp01((toScale(bin.lo) - sMin) / span)
    const x1 = clamp01((toScale(bin.hi) - sMin) / span)
    // An empty bucket draws nothing; a populated one keeps a visible floor so a
    // thin-but-real bucket does not read as a hole.
    const h = bin.count === 0 ? 0 : Math.max(1.5, (bin.count / peak) * HIST_HEIGHT)
    bars.push({
      x: x0 * HIST_WIDTH,
      w: Math.max((x1 - x0) * HIST_WIDTH - 0.3, 0.4),
      y: HIST_HEIGHT - h,
      h,
      mid: (x0 + x1) / 2,
    })
  }
  return bars
}

type RangeRowProps = {
  label: string
  domain: RangeFacet | undefined
  bins: readonly HistogramBin[] | undefined
  min: number | null
  max: number | null
  scale?: ScaleKind
  quantize: (v: number) => number
  format: (v: number) => string
  onCommit: (next: { min: number | null; max: number | null }) => void
}

function RangeRow({
  label,
  domain,
  bins,
  min,
  max,
  scale = 'linear',
  quantize,
  format,
  onCommit,
}: RangeRowProps) {
  const lo = domain?.min ?? null
  const hi = domain?.max ?? null
  const disabled = lo === null || hi === null || hi <= lo
  const s = SCALES[scale]
  const sMin = disabled ? 0 : s.to(lo)
  const sMax = disabled ? 1 : s.to(hi)
  const step = (sMax - sMin) / 200
  const clamp = (v: number): number => Math.min(sMax, Math.max(sMin, v))

  const current: [number, number] = [
    min === null ? sMin : clamp(s.to(min)),
    max === null ? sMax : clamp(s.to(max)),
  ]

  // Geometry depends only on the buckets and the track's domain — never on the
  // handle position, so dragging repaints fills and not the whole backdrop.
  const bars = useMemo(
    () => (disabled ? [] : computeBars(bins ?? [], s.to, sMin, sMax)),
    [bins, disabled, s, sMin, sMax],
  )
  const from = (current[0] - sMin) / Math.max(sMax - sMin, 1e-9)
  const to = (current[1] - sMin) / Math.max(sMax - sMin, 1e-9)

  // Uncontrolled + remounted on external change: the slider owns the drag, the URL
  // owns the committed value, and neither has to mirror the other mid-gesture.
  const syncKey = `${sMin}|${sMax}|${min ?? 'x'}|${max ?? 'x'}`

  return (
    <Stack gap={2}>
      <Group justify="space-between" gap={4}>
        <Text size="xs" c="dimmed">
          {label}
        </Text>
        <Text size="xs" ff="monospace" c={min === null && max === null ? 'dimmed' : VX.neutral}>
          {disabled ? '—' : `${format(s.from(current[0]))} – ${format(s.from(current[1]))}`}
        </Text>
      </Group>
      {/* The backdrop is absolutely positioned so it contributes no flow height; the
          slider is padded down onto it, landing its track across the bars' feet. */}
      <Box style={{ position: 'relative' }}>
        {bars.length > 0 && (
          <svg
            aria-hidden
            width="100%"
            height={HIST_HEIGHT}
            viewBox={`0 0 ${HIST_WIDTH} ${HIST_HEIGHT}`}
            preserveAspectRatio="none"
            style={{ position: 'absolute', left: 0, top: 0, pointerEvents: 'none' }}
          >
            {/* Bucket index is the honest key: bars are a fixed positional series, and
                two buckets can share an x once they clamp to the track's edge. */}
            {bars.map((bar, bucket) => (
              <rect
                key={bucket}
                x={bar.x}
                y={bar.y}
                width={bar.w}
                height={bar.h}
                fill={
                  bar.mid >= from && bar.mid <= to ? alpha(VX.accent, 0.5) : alpha(VX.line, 0.45)
                }
              />
            ))}
          </svg>
        )}
        <Box pt={bars.length > 0 ? 14 : 0}>
          <RangeSlider
            key={syncKey}
            defaultValue={current}
            min={sMin}
            max={sMax}
            step={step}
            disabled={disabled}
            size="xs"
            label={(v) => format(quantize(s.from(v)))}
            onChangeEnd={(v) => {
              const eps = step / 2
              onCommit({
                min: v[0] <= sMin + eps ? null : quantize(s.from(v[0])),
                max: v[1] >= sMax - eps ? null : quantize(s.from(v[1])),
              })
            }}
          />
        </Box>
      </Box>
    </Stack>
  )
}

// ── Multi-select helpers ─────────────────────────────────────────────────────

/** Facet values → Mantine options, with the count folded into the label. */
function facetOptions(values: FacetValue[] | undefined, selected: string[]) {
  const options = (values ?? [])
    .filter((f) => f.value !== '')
    .map((f) => ({ value: f.value, label: `${f.value} (${f.count})` }))
  // Keep a selection visible even if the current facet slice no longer lists it.
  const known = new Set(options.map((o) => o.value))
  for (const value of selected) {
    if (!known.has(value)) options.push({ value, label: value })
  }
  return options
}

// ── Panel ────────────────────────────────────────────────────────────────────

export type PhotoFilterPanelProps = {
  value: PhotoFilters
  facets: Facets | undefined
  onChange: (f: PhotoFilters) => void
}

/**
 * Filter panel: the six rating toggles, filename search, camera / lens / label
 * multi-selects, and the four EXIF ranges with their distribution drawn behind
 * each slider.
 *
 * @param value The complete current filter set (never partial — see `PhotoFilters`).
 * @param facets Facet counts for the current selection; undefined while loading.
 * @param onChange Receives the full next filter set.
 */
export function PhotoFilterPanel({ value, facets, onChange }: PhotoFilterPanelProps) {
  const [search, setSearch] = useState(value.q ?? '')
  // `clearFilters` deliberately keeps the root (clearing filters should not throw the
  // user out of the folder they are culling), so root is excluded from the count the
  // button offers to clear — otherwise it would advertise a no-op.
  const clearableCount = activeFilterCount({ ...value, root: null })

  useEffect(() => {
    setSearch(value.q ?? '')
  }, [value.q])

  const commitSearch = useDebouncedCallback((text: string) => {
    onChange({ ...value, q: text.trim() === '' ? null : text.trim() })
  }, 300)

  const patch = (next: Partial<PhotoFilters>): void => onChange({ ...value, ...next })
  const withHistograms: WithHistograms | undefined = facets
  const histograms = withHistograms?.histograms

  return (
    <Stack gap="sm">
      <Stack gap={4}>
        <Text size="xs" c="dimmed">
          Rating
        </Text>
        <RatingFilter
          selected={value.rating}
          facets={facets}
          onToggle={(rating) =>
            patch({ rating: toggleValue(value.rating, rating).toSorted((a, b) => a - b) })
          }
        />
      </Stack>

      <TextInput
        size="xs"
        label="Filename"
        placeholder="Any"
        leftSection={<IconSearch size={13} />}
        value={search}
        onChange={(event) => {
          setSearch(event.currentTarget.value)
          commitSearch(event.currentTarget.value)
        }}
      />

      <MultiSelect
        size="xs"
        label="Camera"
        clearable
        searchable
        placeholder="Any"
        value={value.camera_model}
        data={facetOptions(facets?.camera_models, value.camera_model)}
        onChange={(next) => patch({ camera_model: next })}
      />

      <MultiSelect
        size="xs"
        label="Lens"
        clearable
        searchable
        placeholder="Any"
        value={value.lens_model}
        data={facetOptions(facets?.lens_models, value.lens_model)}
        onChange={(next) => patch({ lens_model: next })}
      />

      <MultiSelect
        size="xs"
        label="Label"
        clearable
        placeholder="Any"
        value={value.label}
        data={facetOptions(facets?.labels, value.label)}
        onChange={(next) => patch({ label: next })}
        renderOption={({ option }) => (
          <Group gap={6} wrap="nowrap">
            <LabelDot label={option.value} />
            <Text size="xs">{option.label}</Text>
          </Group>
        )}
      />

      <RangeRow
        label="ISO"
        domain={facets?.iso}
        bins={histograms?.iso}
        min={value.iso_min}
        max={value.iso_max}
        quantize={(v) => Math.round(v)}
        format={formatIso}
        onCommit={({ min, max }) => patch({ iso_min: min, iso_max: max })}
      />
      <RangeRow
        label="Aperture"
        domain={facets?.aperture}
        bins={histograms?.aperture}
        min={value.aperture_min}
        max={value.aperture_max}
        quantize={(v) => Math.round(v * 10) / 10}
        format={formatAperture}
        onCommit={({ min, max }) => patch({ aperture_min: min, aperture_max: max })}
      />
      <RangeRow
        label="Shutter"
        domain={facets?.shutter}
        bins={histograms?.shutter}
        min={value.shutter_min}
        max={value.shutter_max}
        scale="log"
        quantize={(v) => Number(v.toPrecision(4))}
        format={formatShutter}
        onCommit={({ min, max }) => patch({ shutter_min: min, shutter_max: max })}
      />
      <RangeRow
        label="Focal length"
        domain={facets?.focal}
        bins={histograms?.focal}
        min={value.focal_min}
        max={value.focal_max}
        quantize={(v) => Math.round(v)}
        format={formatFocal}
        onCommit={({ min, max }) => patch({ focal_min: min, focal_max: max })}
      />

      {clearableCount > 0 && (
        <Button
          size="compact-xs"
          variant="default"
          fullWidth
          leftSection={<IconX size={13} />}
          onClick={() => onChange(clearFilters(value))}
        >
          Clear {clearableCount} filter{clearableCount === 1 ? '' : 's'}
        </Button>
      )}
    </Stack>
  )
}
