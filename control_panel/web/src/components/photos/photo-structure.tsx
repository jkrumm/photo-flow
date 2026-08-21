/**
 * The library-structure section — four candidate layouts, side by side, all queries.
 *
 * This is the Stage F3 experiment made clickable. `flat`, `month`, `album` and `event`
 * are the four ways this library could have been arranged on disk; here they are four
 * settings of one switch, and every row under it is a saved query rather than a
 * directory. Switching between them moves nothing, costs one request, and is reversible.
 *
 * Three things the section reports that a folder tree cannot, and each is a measurement
 * the experiment needs:
 *
 * 1. **How many groups**, right there next to the switch. A layout that produces 409
 *    groups over 3 800 photos has answered the question about itself.
 * 2. **How many photos it cannot place** (`ungrouped`). `album` leaves 1 805 of 2 364
 *    Final photos homeless; `month` and `event` leave none. A directory layout hides
 *    this — it just makes you invent a `Misc/` folder.
 * 3. **What it cost to compute**, in milliseconds. That is the real content of "does
 *    this need to be physical": a structure that is cheap at query time never needs a
 *    stored artifact, and this number is the evidence rather than the argument.
 *
 * The rows are `FolderRow`, the same component the two real directories use. That
 * identity is deliberate and is the whole method: if a derived group and a physical
 * folder are indistinguishable in use, the layout was never load-bearing.
 */
import { Group, SegmentedControl, Stack, Text } from '@mantine/core'
import { VX } from 'basalt-ui/tokens'
import {
  EVENT_GAPS,
  STRUCTURE_HINTS,
  STRUCTURE_LABELS,
  STRUCTURE_KINDS,
  type Structure,
  type StructureGroup,
  type StructureKind,
} from '../../lib/structure'
import { FolderRow } from './photo-folders'

export type PhotoStructureProps = {
  kind: StructureKind
  onKind: (kind: StructureKind) => void
  gapSeconds: number
  onGap: (seconds: number) => void
  structure: Structure | undefined
  /** True when this group's query is exactly what the filters currently say. */
  isActive: (group: StructureGroup) => boolean
  /** Apply a group's query — or clear it, when the active group is clicked again. */
  onSelect: (group: StructureGroup | null) => void
}

export function PhotoStructure({
  kind,
  onKind,
  gapSeconds,
  onGap,
  structure,
  isActive,
  onSelect,
}: PhotoStructureProps) {
  const groups = structure?.groups ?? []

  return (
    <Stack gap={6}>
      <SegmentedControl
        size="xs"
        fullWidth
        value={kind}
        onChange={(next) => onKind(next as StructureKind)}
        data={STRUCTURE_KINDS.map((value) => ({ value, label: STRUCTURE_LABELS[value] }))}
      />

      <Text size="xs" c="dimmed">
        {STRUCTURE_HINTS[kind]}
      </Text>

      {kind === 'event' && (
        <Group gap={6} wrap="nowrap" justify="space-between">
          <Text size="xs" c="dimmed" style={{ flexShrink: 0 }}>
            Gap
          </Text>
          <SegmentedControl
            size="xs"
            value={String(gapSeconds)}
            onChange={(next) => onGap(Number(next))}
            data={EVENT_GAPS.map((gap) => ({ value: String(gap.value), label: gap.label }))}
          />
        </Group>
      )}

      {structure !== undefined && (
        // The three measurements, on one line. `ungrouped` only appears when it is
        // non-zero, because "0 unplaced" is the uninteresting answer and this line has
        // to stay readable at 248px.
        <Group gap={6} wrap="nowrap" justify="space-between">
          <Text size="xs" c="dimmed" ff="monospace">
            {groups.length} group{groups.length === 1 ? '' : 's'}
          </Text>
          {structure.ungrouped > 0 && (
            <Text size="xs" ff="monospace" c={VX.status.warn}>
              {structure.ungrouped} unplaced
            </Text>
          )}
          <Text size="xs" c="dimmed" ff="monospace">
            {structure.elapsed_ms} ms
          </Text>
        </Group>
      )}

      {structure !== undefined && groups.length === 0 && (
        <Text size="xs" c="dimmed">
          Nothing to group here.
        </Text>
      )}

      <Stack gap={2}>
        {groups.map((group) => {
          const active = isActive(group)
          return (
            <FolderRow
              key={group.key}
              label={group.label}
              hint={group.sublabel}
              count={group.count}
              active={active}
              onClick={() => onSelect(active ? null : group)}
            />
          )
        })}
      </Stack>
    </Stack>
  )
}
