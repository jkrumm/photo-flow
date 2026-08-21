/**
 * The narrowing readout — what is currently narrowing the result set, and what each part
 * of it costs.
 *
 * This is the section that makes the model VISIBLE rather than merely obeyed. Before it,
 * a result set of 172 photos was the output of three controls on three different cards
 * (a saved collection, a folder row, a slider) and the only way to find out which one was
 * responsible was to clear them one at a time.
 *
 * Three things it shows, and why each earns its row:
 *
 * 1. **The funnel** — library → scope → view. Three numbers on one line answer "am I
 *    looking at a slice of a slice?", which is the question a saved collection creates.
 * 2. **One chip per narrowing dimension**, tagged with the layer that supplied it. The
 *    layer tag is the model: SCOPE is the collection you chose, REFINE is everything the
 *    rest of the sidebar sets. A REFINE chip is removable, and removing it clears that
 *    whole dimension — the same unit the rule merges on. A SCOPE chip is not: it is the
 *    collection's own clause, and the only way to drop it is to leave the collection.
 * 3. **`−N` per chip**: how many rows that dimension is excluding right now, computed
 *    server-side by re-running the query with that one dimension open. That is the
 *    "what would happen if I changed this" answer, and it is a measured number rather
 *    than an inference, so it cannot disagree with what clicking actually does.
 *
 * An OVERRIDDEN dimension gets a warning-toned chip that names what the scope wanted.
 * The precedence rule lets a refinement replace a scope clause, which is right — but a
 * scope clause that vanishes silently is how a user stops trusting a saved collection.
 */
import { Badge, Box, Group, Stack, Text, Tooltip, UnstyledButton } from '@mantine/core'
import { IconX } from '@tabler/icons-react'
import { VX, alpha } from 'basalt-ui/tokens'
import {
  describeFields,
  excludedBy,
  formatCount,
  type Narrowing,
  type NarrowingDimension,
} from '../../lib/narrowing'

export type PhotoNarrowingProps = {
  narrowing: Narrowing | undefined
  /** Clear one whole dimension. Which fields that means is the server's `fields` map. */
  onClear: (fields: string[]) => void
  /** Leave the collection, keeping every ad-hoc filter. */
  onClearScope: () => void
}

/** One number of the funnel, with its caption under it. */
function FunnelStep({ value, caption, dim = false }: { value: number; caption: string; dim?: boolean }) {
  return (
    <Stack gap={0} align="center" style={{ minWidth: 0 }}>
      <Text size="sm" fw={600} ff="monospace" c={dim ? 'dimmed' : 'bright'}>
        {formatCount(value)}
      </Text>
      <Text size="xs" c="dimmed" truncate="end">
        {caption}
      </Text>
    </Stack>
  )
}

/** The `→` between two funnel steps. */
function FunnelArrow() {
  return (
    <Text size="xs" c="dimmed" aria-hidden>
      →
    </Text>
  )
}

type ChipProps = {
  dimension: NarrowingDimension
  total: number
  onClear: () => void
}

/**
 * One dimension, as a chip.
 *
 * **Only a REFINE chip carries a X.** A scope-sourced dimension is not something this
 * screen can clear: `onClear` resets the named fields in the refine layer, where a
 * scope-supplied dimension was never set, so the click is a no-op — and inside a
 * collection that was every chip on the card. Dropping a scope clause means editing the
 * collection or leaving it, and leaving it is the X on the row above. A control that does
 * nothing is worse than no control, and one that also promises a row count it will never
 * produce is worse again.
 */
function DimensionChip({ dimension, total, onClear }: ChipProps) {
  const excluded = excludedBy(dimension, total)
  const fromScope = dimension.source === 'scope'
  // Scope and refine are told apart by TINT, not by an icon: the two are peers in the
  // model and one of them wearing a badge would read as the more important layer.
  const tint = dimension.overrides ? VX.status.warn : fromScope ? VX.accent : VX.neutral
  // An overridden dimension is sourced from REFINE, so it is removable like any other —
  // clearing it hands the axis back to the collection.
  const removable = !fromScope

  const detail = describeFields(dimension.fields)
  const cost = `excluding ${formatCount(excluded)} photo${excluded === 1 ? '' : 's'}`
  const tooltip = dimension.overrides
    ? `${dimension.label}: the filter panel replaced what "${describeFields(dimension.scope_fields)}" asked for in the collection. Clearing it hands the dimension back to the collection.`
    : removable
      ? `${dimension.label} ${detail} — ${cost}. Clear it to see ${formatCount(dimension.without)}.`
      : `${dimension.label} ${detail} — ${cost}. It comes from the collection, so it is dropped by leaving the collection, not from here.`

  return (
    <Tooltip label={tooltip} multiline w={260} openDelay={400} withArrow>
      <Group
        gap={6}
        wrap="nowrap"
        px={6}
        py={2}
        style={{
          borderRadius: VX.radiusCtrl,
          background: alpha(tint, dimension.overrides ? 0.16 : 0.11),
          minWidth: 0,
        }}
      >
        <Text size="xs" fw={600} style={{ flexShrink: 0 }}>
          {dimension.label}
        </Text>
        <Text size="xs" c="dimmed" truncate="end" style={{ minWidth: 0 }}>
          {detail}
        </Text>
        <Text size="xs" c="dimmed" ff="monospace" style={{ flexShrink: 0 }}>
          −{formatCount(excluded)}
        </Text>
        {removable ? (
          <UnstyledButton
            onClick={onClear}
            aria-label={`Clear ${dimension.label}`}
            style={{ lineHeight: 0, flexShrink: 0, cursor: 'pointer' }}
          >
            <IconX size={11} />
          </UnstyledButton>
        ) : (
          <Badge size="xs" variant="light" color="blue" style={{ flexShrink: 0 }}>
            scope
          </Badge>
        )}
      </Group>
    </Tooltip>
  )
}

export function PhotoNarrowing({ narrowing, onClear, onClearScope }: PhotoNarrowingProps) {
  if (narrowing === undefined) {
    return (
      <Text size="xs" c="dimmed">
        Counting…
      </Text>
    )
  }

  const scoped = narrowing.scope_id !== null
  const nothingNarrows = narrowing.dimensions.length === 0 && !scoped

  return (
    <Stack gap={8}>
      <Group gap={8} wrap="nowrap" justify="space-between">
        <FunnelStep value={narrowing.library_total} caption="library" dim />
        <FunnelArrow />
        {scoped && (
          <>
            <FunnelStep value={narrowing.scope_total} caption={narrowing.scope_name ?? 'scope'} dim />
            <FunnelArrow />
          </>
        )}
        <FunnelStep value={narrowing.total} caption="in view" />
      </Group>

      {scoped && (
        <Group gap={6} wrap="nowrap">
          <Badge size="xs" variant="light" color="blue">
            Collection
          </Badge>
          <Text size="xs" truncate="end" style={{ minWidth: 0, flex: 1 }}>
            {narrowing.scope_name}
          </Text>
          <UnstyledButton
            onClick={onClearScope}
            aria-label="Leave this collection"
            style={{ lineHeight: 0, cursor: 'pointer' }}
          >
            <IconX size={11} />
          </UnstyledButton>
        </Group>
      )}

      {nothingNarrows && (
        <Text size="xs" c="dimmed">
          Nothing is narrowing the library. Pick a collection, a folder or a filter and this
          will say which one — and what it costs.
        </Text>
      )}

      {narrowing.dimensions.length > 0 && (
        <Stack gap={4}>
          {narrowing.dimensions.map((dimension) => (
            <DimensionChip
              key={dimension.dimension}
              dimension={dimension}
              total={narrowing.total}
              onClear={() => onClear(Object.keys(dimension.fields))}
            />
          ))}
        </Stack>
      )}

      {narrowing.dimensions.some((d) => d.overrides) && (
        <Box>
          <Text size="xs" c="dimmed">
            An amber chip is a dimension where your filter replaced the collection&apos;s
            own. Different dimensions stack; the same dimension does not — clearing it
            hands that axis back to the collection.
          </Text>
        </Box>
      )}
    </Stack>
  )
}
