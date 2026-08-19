/**
 * Star rating control + the app's colour-label vocabulary.
 *
 * This is the most primitive module of the culling screen, so the shared label
 * dictionary lives here rather than in a heavier sibling — the filmstrip, the
 * filter bar and the info panel all read it without pulling each other in.
 */
import { useState } from 'react'
import { Box, Group, UnstyledButton, VisuallyHidden } from '@mantine/core'
import { IconStar, IconStarFilled } from '@tabler/icons-react'
import { VX, alpha } from 'basalt-ui/tokens'
import { PF } from '../../lib/series'

const STARS = [1, 2, 3, 4, 5] as const

/**
 * The XMP `xmp:Label` values photo-flow offers. Adobe Bridge / Photomator write a
 * free-form string, so an unknown label still renders (see {@link labelColor}) —
 * this list only drives the pickers.
 */
export const PHOTO_LABELS: readonly { value: string; color: string }[] = [
  { value: 'Red', color: VX.bad },
  { value: 'Yellow', color: VX.warn },
  { value: 'Green', color: VX.good },
  { value: 'Blue', color: PF.final },
  { value: 'Purple', color: PF.videos },
]

const LABEL_COLORS = new Map(PHOTO_LABELS.map((l) => [l.value.toLowerCase(), l.color]))

/**
 * Token colour for a label string, or `null` when the label is empty.
 * An unrecognised label falls back to the neutral ink so it is still visible.
 */
export function labelColor(label: string): string | null {
  if (label === '') return null
  return LABEL_COLORS.get(label.toLowerCase()) ?? alpha(VX.neutral, 0.55)
}

export type LabelDotProps = {
  label: string
  size?: number
}

/** A small filled dot carrying a photo's colour label; renders nothing when unlabelled. */
export function LabelDot({ label, size = 8 }: LabelDotProps) {
  const color = labelColor(label)
  if (color === null) return null
  return (
    <Box
      aria-label={`Label ${label}`}
      w={size}
      h={size}
      style={{ borderRadius: VX.radiusPill, background: color, flexShrink: 0 }}
    />
  )
}

export type StarRatingProps = {
  /** Current rating, 0–5. An absent XMP tag reads as 0. */
  value: number
  /** Glyph size in px. */
  size?: number
  /** Render as static glyphs — no buttons, no hover preview. */
  readOnly?: boolean
  /** Clicking the current rating clears it (emits 0). */
  onChange?: (v: number) => void
}

/**
 * Five-star rating control.
 *
 * Filled stars carry the "published" series hue (the same colour the analytics
 * screen spends on rating ≥ 4), empty stars are dimmed neutral ink. Hovering
 * previews the rating that a click would write.
 */
export function StarRating({ value, size = 16, readOnly = false, onChange }: StarRatingProps) {
  const [hover, setHover] = useState<number | null>(null)
  const shown = hover ?? value
  const filledColor = PF.published
  const emptyColor = alpha(VX.neutral, 0.28)

  if (readOnly || onChange === undefined) {
    return (
      <Group gap={1} wrap="nowrap">
        <VisuallyHidden>{`Rating ${value} of 5`}</VisuallyHidden>
        {STARS.map((star) =>
          star <= value ? (
            <IconStarFilled key={star} size={size} aria-hidden style={{ color: filledColor }} />
          ) : (
            <IconStar key={star} size={size} aria-hidden style={{ color: emptyColor }} />
          ),
        )}
      </Group>
    )
  }

  return (
    <Group gap={2} wrap="nowrap" onMouseLeave={() => setHover(null)}>
      {STARS.map((star) => (
        <UnstyledButton
          key={star}
          aria-label={star === value ? `Clear rating (currently ${value})` : `Rate ${star} stars`}
          aria-pressed={star <= value}
          onMouseEnter={() => setHover(star)}
          onFocus={() => setHover(star)}
          onBlur={() => setHover(null)}
          onClick={() => onChange(star === value ? 0 : star)}
          style={{ lineHeight: 0, cursor: 'pointer' }}
        >
          {star <= shown ? (
            <IconStarFilled
              size={size}
              style={{ color: hover !== null ? alpha(filledColor, 0.75) : filledColor }}
            />
          ) : (
            <IconStar size={size} style={{ color: emptyColor }} />
          )}
        </UnstyledButton>
      ))}
    </Group>
  )
}
