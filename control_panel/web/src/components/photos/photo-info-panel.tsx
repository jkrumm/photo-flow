/**
 * The sidebar's Info section: everything about the selected photo.
 *
 * It opens with the *write* surface — the two fields culling actually changes, stars and
 * colour label — and then the read surface: what the index knows, plus the live `exiftool`
 * dump the API attaches to `GET /api/photos/meta`.
 *
 * What is deliberately NOT here: the filename as a heading, and the portrait/landscape
 * badge. Both were noise on a screen where you are looking at the photograph — the frame
 * itself says which way up it is, and the full path is one row down for the rare moment
 * the name matters.
 */
import { useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import { useDebouncedValue } from '@mantine/hooks'
import { Box, Collapse, Divider, Group, Loader, Stack, Text, Tooltip, UnstyledButton } from '@mantine/core'
import { IconChevronDown, IconChevronRight, IconPhotoOff } from '@tabler/icons-react'
import { VX, alpha } from 'basalt-ui/tokens'
import { formatBytes } from '../../lib/format'
import { photosQueries } from '../../lib/queries/photos'
import { ratingOf, type PhotoRow } from '../../lib/photos'
import { PHOTO_LABELS, StarRating } from './star-rating'

// ── Value formatting ─────────────────────────────────────────────────────────

function formatShutter(seconds: number | null): string {
  if (seconds === null) return '—'
  if (seconds >= 1) return `${Number.isInteger(seconds) ? seconds : seconds.toFixed(1)} s`
  return `1/${Math.round(1 / Math.max(seconds, 1e-6))} s`
}

function formatDate(iso: string | null): string {
  if (iso === null) return '—'
  const parsed = new Date(iso)
  if (Number.isNaN(parsed.getTime())) return iso
  return parsed.toLocaleString()
}

function formatDimensions(row: PhotoRow): string {
  if (row.width === null || row.height === null) return '—'
  const megapixels = (row.width * row.height) / 1_000_000
  return `${row.width} × ${row.height} · ${megapixels.toFixed(1)} MP`
}

/** Render an arbitrary exiftool value as one line of text. */
function formatExifValue(value: unknown): string {
  if (value === null || value === undefined) return '—'
  if (Array.isArray(value)) return value.map((v) => String(v)).join(', ')
  if (typeof value === 'object') return JSON.stringify(value)
  return String(value)
}

// ── Rows ─────────────────────────────────────────────────────────────────────

type InfoRowProps = {
  label: string
  value: string
  mono?: boolean
  /**
   * Hold the value to one line and hang the full string in a tooltip.
   *
   * `flex: 1 1 0` is not cosmetic here. This row lives inside a `ScrollArea`, whose content
   * box is sized by its content — so a `white-space: nowrap` child has nothing to shrink
   * against and simply widens the whole panel instead, carrying every OTHER row's
   * right-aligned value off the visible edge with it. A zero flex-basis makes the text
   * contribute no intrinsic width, which is what actually pins the row to the panel.
   */
  truncate?: boolean
  /** Tooltip text for a truncated row; defaults to the value itself. */
  tooltip?: string
}

function InfoRow({ label, value, mono = true, truncate = false, tooltip }: InfoRowProps) {
  const font = mono ? 'monospace' : 'text'
  return (
    <Group justify="space-between" align="flex-start" gap="xs" wrap="nowrap">
      <Text size="xs" c="dimmed" style={{ flexShrink: 0 }}>
        {label}
      </Text>
      {truncate ? (
        <Tooltip label={tooltip ?? value} withArrow multiline maw={320} openDelay={300}>
          <Text size="xs" ff={font} ta="right" truncate="end" style={{ flex: '1 1 0', minWidth: 0 }}>
            {value}
          </Text>
        </Tooltip>
      ) : (
        <Text size="xs" ff={font} ta="right" style={{ wordBreak: 'break-all' }}>
          {value}
        </Text>
      )}
    </Group>
  )
}

// ── Label picker ─────────────────────────────────────────────────────────────

function LabelPicker({ value, onLabel }: { value: string; onLabel: (s: string) => void }) {
  return (
    <Group gap={6} wrap="nowrap">
      {PHOTO_LABELS.map((option) => {
        const active = option.value.toLowerCase() === value.toLowerCase()
        return (
          <Tooltip key={option.value} label={option.value} withArrow openDelay={300}>
            <UnstyledButton
              aria-label={active ? `Clear ${option.value} label` : `Set ${option.value} label`}
              aria-pressed={active}
              onClick={() => onLabel(active ? '' : option.value)}
              w={16}
              h={16}
              style={{
                borderRadius: VX.radiusPill,
                background: option.color,
                opacity: active ? 1 : 0.42,
                boxShadow: active ? `0 0 0 2px ${alpha(option.color, 0.35)}` : 'none',
                cursor: 'pointer',
              }}
            />
          </Tooltip>
        )
      })}
      <Text size="xs" c="dimmed">
        {value === '' ? 'No label' : value}
      </Text>
    </Group>
  )
}

// ── Section ──────────────────────────────────────────────────────────────────

export type PhotoInfoProps = {
  row: PhotoRow | null
  onRate: (v: number) => void
  onLabel: (s: string) => void
}

/**
 * Write surface first, then everything the index and exiftool know.
 *
 * @param row Selected photo, or null when nothing is selected.
 * @param onRate Writes a star rating (0 clears the XMP tag).
 * @param onLabel Writes a colour label ('' clears it).
 */
export function PhotoInfo({ row, onRate, onLabel }: PhotoInfoProps) {
  const [showRaw, setShowRaw] = useState(false)
  // `/meta` spawns an exiftool process server-side. Stepping through the filmstrip with a
  // held arrow key would fire one per frame and starve the viewer's own thumbnail request
  // in the browser's per-origin connection budget, so only a settled selection fetches.
  // The query is abortable too (see photosQueries.meta), so a superseded one is cancelled.
  const [debouncedPath] = useDebouncedValue(row?.path ?? '', 200)
  const meta = useQuery(photosQueries.meta(debouncedPath))

  if (row === null) {
    return (
      <Group gap={6} justify="center" py="xs">
        <IconPhotoOff size={16} style={{ color: alpha(VX.neutral, 0.35) }} />
        <Text size="xs" c="dimmed">
          No photo selected
        </Text>
      </Group>
    )
  }

  // During the debounce window the query still holds the *previous* photo's payload —
  // match on path so the panel never labels one photo with another's sizes.
  const photoMeta = meta.data !== undefined && meta.data.path === row.path ? meta.data : null
  const metaLoading = photoMeta === null && !meta.isError
  const extraEntries = Object.entries(photoMeta?.exif_extra ?? {}).filter(
    ([key]) => key !== 'SourceFile',
  )

  return (
    <Stack gap="sm">
      {/* The two fields a cull writes, before anything it only reads. */}
      <Stack gap={8}>
        <StarRating value={ratingOf(row)} size={22} onChange={onRate} />
        <LabelPicker value={row.label} onLabel={onLabel} />
      </Stack>

      <Divider />

      <Stack gap={4}>
        <InfoRow label="Folder" value={row.root} mono={false} />
        <InfoRow label="Camera" value={row.camera_model ?? '—'} mono={false} />
        <InfoRow label="Lens" value={row.lens_model === '' ? '—' : row.lens_model} mono={false} />
        <InfoRow
          label="Focal"
          value={row.focal_mm === null ? '—' : `${Math.round(row.focal_mm)} mm`}
        />
        <InfoRow
          label="Aperture"
          value={row.aperture_f === null ? '—' : `f/${row.aperture_f.toFixed(1)}`}
        />
        <InfoRow label="Shutter" value={formatShutter(row.shutter_s)} />
        <InfoRow label="ISO" value={row.iso === null ? '—' : String(row.iso)} />
        <InfoRow label="Dimensions" value={formatDimensions(row)} />
        <InfoRow label="Taken" value={formatDate(row.date_taken)} />
        <InfoRow label="File size" value={formatBytes(photoMeta?.file_size ?? row.size)} />
        {photoMeta !== null && photoMeta.sidecar_size !== null && (
          <InfoRow label="Sidecar" value={formatBytes(photoMeta.sidecar_size)} />
        )}
        {/* The filename identifies the file; the full path is one hover away. Spelled out
            in full, this was a three-line block and the ugliest thing in the panel — for a
            string nobody reads while culling. */}
        <InfoRow label="File" value={row.filename} tooltip={row.path} truncate />
      </Stack>

      {/* Live exiftool dump — best effort, `{}` when the read failed or timed out. */}
      <Box>
        <UnstyledButton onClick={() => setShowRaw((open) => !open)} aria-expanded={showRaw} w="100%">
          <Group gap={6} wrap="nowrap">
            {showRaw ? <IconChevronDown size={13} /> : <IconChevronRight size={13} />}
            <Text size="xs" c="dimmed">
              Raw EXIF
            </Text>
            {metaLoading ? (
              <Loader size={11} />
            ) : (
              <Text size="xs" c="dimmed" ff="monospace">
                {extraEntries.length}
              </Text>
            )}
          </Group>
        </UnstyledButton>
        <Collapse expanded={showRaw}>
          <Stack gap={2} pt={6}>
            {extraEntries.length === 0 && (
              <Text size="xs" c="dimmed">
                {metaLoading ? 'Reading…' : 'No additional EXIF available.'}
              </Text>
            )}
            {extraEntries.map(([key, value]) => (
              <InfoRow key={key} label={key} value={formatExifValue(value)} />
            ))}
          </Stack>
        </Collapse>
      </Box>
    </Stack>
  )
}
