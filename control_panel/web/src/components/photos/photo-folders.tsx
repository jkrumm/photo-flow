/**
 * Folder navigation for the culling sidebar.
 *
 * Two ways into the same library, deliberately in ONE list:
 *
 * - **Folders** — WHERE the photos live (the two culling roots). Physical.
 * - **Collections** — a saved query, presented exactly like a folder. Virtual, and the
 *   one row here that sets a LAYER rather than a filter value: picking a collection
 *   narrows the library, and every other row then narrows *inside* it.
 *
 * Dates USED to be a third block here, grouped year → month from the LOADED rows. It has
 * moved to the Structure section (`photo-structure.tsx`), where the same months are one of
 * four candidate layouts and are computed server-side over the whole library. The old tree
 * was silently truncated: it grouped at most `DEFAULT_PHOTO_LIMIT` (2 000) rows against a
 * library of 3 797, so the oldest months were simply missing from it.
 *
 * Collections sit directly under the roots on purpose. The Stage F1 question is
 * whether a saved query can stand in for a physical folder layout, and the honest way to
 * ask it is to put the two side by side, identical in shape, and see which one gets
 * clicked. Nothing here moves a file — every row writes a complete `PhotoFilters` back
 * through `onChange` and the route owns the state.
 */
import type { ReactNode } from 'react'
import { Group, Stack, Text, UnstyledButton } from '@mantine/core'
import { VX, alpha } from 'basalt-ui/tokens'
import type { PhotoFilters, PhotoRoot } from '../../lib/photos'
import { PhotoCollections } from './photo-collections'

// ── Root counts ──────────────────────────────────────────────────────────────

/**
 * The three folder counts; `undefined` until the query behind one lands.
 *
 * `all` is deliberately its own number rather than `final + staging`. Each row's count is
 * "what you would see if you clicked me", and clicking All clears the root REFINE — which
 * hands the axis back to the active collection. Inside a collection that pins `root=final`,
 * All is the Final count, not the sum. The sum is only correct when nothing is scoped, and
 * that coincidence is exactly what made the old arithmetic look right for a year.
 */
export type RootCounts = {
  all: number | undefined
  final: number | undefined
  staging: number | undefined
}

/** Human label for the active root — the Folders section's collapsed summary. */
export function rootLabel(root: PhotoRoot | null): string {
  if (root === 'final') return 'Final'
  if (root === 'staging') return 'Staging'
  return 'All'
}

// ── Rows ─────────────────────────────────────────────────────────────────────

export type FolderRowProps = {
  label: string
  /** Dimmed detail after the label — a month name, an event's span in days. */
  hint?: string
  count: number | undefined
  active: boolean
  indent?: number
  leading?: ReactNode
  onClick: () => void
}

/**
 * One selectable row — a root, a year, a month, or a saved collection.
 *
 * Exported because a collection MUST look like a folder: the F1 question is whether a
 * saved query can stand in for a physical one, and giving the two different chrome would
 * answer it by suggestion rather than by use.
 */
export function FolderRow({
  label,
  hint,
  count,
  active,
  indent = 0,
  leading,
  onClick,
}: FolderRowProps) {
  return (
    <UnstyledButton
      onClick={onClick}
      aria-pressed={active}
      w="100%"
      px={6}
      py={3}
      style={{
        borderRadius: VX.radiusCtrl,
        background: active ? alpha(VX.accent, 0.13) : 'transparent',
        cursor: 'pointer',
      }}
    >
      <Group gap={4} wrap="nowrap" justify="space-between" pl={indent}>
        <Group gap={4} wrap="nowrap" style={{ minWidth: 0 }}>
          {leading}
          <Text size="xs" fw={active ? 600 : 400} truncate="end">
            {label}
          </Text>
          {hint !== undefined && hint !== '' && (
            <Text size="xs" c="dimmed" style={{ flexShrink: 0 }}>
              {hint}
            </Text>
          )}
        </Group>
        <Text size="xs" c="dimmed" ff="monospace">
          {count ?? '—'}
        </Text>
      </Group>
    </UnstyledButton>
  )
}

// ── Panel ────────────────────────────────────────────────────────────────────

export type PhotoFoldersProps = {
  filters: PhotoFilters
  counts: RootCounts
  /** The active collection's id, or null. Collections are a LAYER, not a filter value. */
  scope: string | null
  onChange: (next: PhotoFilters) => void
  onScope: (id: string | null) => void
}

/** The two culling roots, then the saved collections. Dates live in the Structure section. */
export function PhotoFolders({ filters, counts, scope, onChange, onScope }: PhotoFoldersProps) {
  const setRoot = (root: PhotoRoot | null): void => onChange({ ...filters, root })

  return (
    <Stack gap="sm">
      <Stack gap={2}>
        <FolderRow
          label="All"
          count={counts.all}
          active={filters.root === null}
          onClick={() => setRoot(null)}
        />
        <FolderRow
          label="Final"
          count={counts.final}
          active={filters.root === 'final'}
          onClick={() => setRoot(filters.root === 'final' ? null : 'final')}
        />
        <FolderRow
          label="Staging"
          count={counts.staging}
          active={filters.root === 'staging'}
          onClick={() => setRoot(filters.root === 'staging' ? null : 'staging')}
        />
      </Stack>

      <PhotoCollections filters={filters} scope={scope} onScope={onScope} />
    </Stack>
  )
}
