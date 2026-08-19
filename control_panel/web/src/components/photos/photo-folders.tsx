/**
 * Folder + date navigation for the culling sidebar.
 *
 * Two views of the same question — WHERE the photos live (the two culling roots) and
 * WHEN they were taken (the loaded rows, grouped year → month). Both write a complete
 * `PhotoFilters` back through `onChange`; the route owns the state, this is pure chrome.
 *
 * Lived in the route as a left-hand rail until the sidebar unification; the rendering is
 * unchanged, only its home is.
 */
import { useMemo, useState } from 'react'
import type { MouseEvent, ReactNode } from 'react'
import { Box, Button, Group, Stack, Text, UnstyledButton } from '@mantine/core'
import { IconChevronDown, IconChevronRight } from '@tabler/icons-react'
import { VX, alpha } from 'basalt-ui/tokens'
import { monthKey, yearKey, type PhotoFilters, type PhotoRoot, type PhotoRow } from '../../lib/photos'

// ── Date grouping ────────────────────────────────────────────────────────────

type MonthGroup = { key: string; count: number }
type YearGroup = { year: string; count: number; months: MonthGroup[] }

/** Group the loaded rows into year → month buckets, newest first. */
function buildDateGroups(rows: PhotoRow[]): YearGroup[] {
  const byYear = new Map<string, Map<string, number>>()
  for (const row of rows) {
    const year = yearKey(row)
    const month = monthKey(row)
    if (year === null || month === null) continue
    let months = byYear.get(year)
    if (months === undefined) {
      months = new Map<string, number>()
      byYear.set(year, months)
    }
    months.set(month, (months.get(month) ?? 0) + 1)
  }
  return [...byYear.entries()]
    .map(([year, months]) => ({
      year,
      count: [...months.values()].reduce((sum, n) => sum + n, 0),
      months: [...months.entries()]
        .map(([key, count]) => ({ key, count }))
        .toSorted((a, b) => b.key.localeCompare(a.key)),
    }))
    .toSorted((a, b) => b.year.localeCompare(a.year))
}

/**
 * End-of-day suffix for a range's upper bound.
 *
 * `date_taken` is stored (and compared) as the string `YYYY-MM-DDTHH:MM:SSZ`, so a bare
 * `YYYY-MM-DD` upper bound sorts *before* every photo taken on that day and silently drops
 * the last day of the range. The server hardens this too; the tree sends the explicit form.
 */
const END_OF_DAY = 'T23:59:59Z'

/** Inclusive ISO date range covering one calendar year. */
function yearRange(year: string): { from: string; to: string } {
  return { from: `${year}-01-01`, to: `${year}-12-31${END_OF_DAY}` }
}

/** Inclusive ISO date range covering one `YYYY-MM` bucket. */
function monthRange(key: string): { from: string; to: string } {
  const year = Number(key.slice(0, 4))
  const month = Number(key.slice(5, 7))
  // Day 0 of the *next* month is the last day of this one.
  const lastDay = new Date(Date.UTC(year, month, 0)).getUTCDate()
  return { from: `${key}-01`, to: `${key}-${String(lastDay).padStart(2, '0')}${END_OF_DAY}` }
}

// ── Root counts ──────────────────────────────────────────────────────────────

/** The two folder counts; `undefined` until the query behind one of them lands. */
export type RootCounts = { final: number | undefined; staging: number | undefined }

/**
 * Resolve both folder counts from the page's own facet query plus ONE extra.
 *
 * `Facets` has no root dimension, so a count for a root other than the filtered one costs a
 * full facet computation — but only one is ever needed. With a root filter applied, the
 * page's own `facetsQuery` already *is* that root's count; with no root filter its `count`
 * is the sum of the two, so the second is arithmetic. (Mid-filter-change the two can come
 * from different `keepPreviousData` generations for a frame; they are labels, and settle.)
 */
export function splitRootCounts(
  root: PhotoRoot | null,
  base: number | undefined,
  other: number | undefined,
): RootCounts {
  if (root === 'final') return { final: base, staging: other }
  if (root === 'staging') return { final: other, staging: base }
  // No root filter: `other` counted Final, `base` counted both.
  const staging = base === undefined || other === undefined ? undefined : Math.max(base - other, 0)
  return { final: other, staging }
}

/** Human label for the active root — the Folders section's collapsed summary. */
export function rootLabel(root: PhotoRoot | null): string {
  if (root === 'final') return 'Final'
  if (root === 'staging') return 'Staging'
  return 'All'
}

// ── Rows ─────────────────────────────────────────────────────────────────────

type FolderRowProps = {
  label: string
  count: number | undefined
  active: boolean
  indent?: number
  leading?: ReactNode
  onClick: () => void
}

function FolderRow({ label, count, active, indent = 0, leading, onClick }: FolderRowProps) {
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
  rows: PhotoRow[]
  counts: RootCounts
  onChange: (next: PhotoFilters) => void
}

/** The two culling roots, then the loaded rows grouped year → month. */
export function PhotoFolders({ filters, rows, counts, onChange }: PhotoFoldersProps) {
  const [openYears, setOpenYears] = useState<string[]>([])
  const groups = useMemo(() => buildDateGroups(rows), [rows])

  const setRoot = (root: PhotoRoot | null): void => onChange({ ...filters, root })

  const setRange = (range: { from: string; to: string } | null): void =>
    onChange({
      ...filters,
      date_from: range === null ? null : range.from,
      date_to: range === null ? null : range.to,
    })

  const rangeActive = (range: { from: string; to: string }): boolean =>
    filters.date_from === range.from && filters.date_to === range.to

  const toggleYear = (year: string): void =>
    setOpenYears((open) => (open.includes(year) ? open.filter((y) => y !== year) : [...open, year]))

  const allCount =
    counts.final === undefined || counts.staging === undefined
      ? undefined
      : counts.final + counts.staging

  return (
    <Stack gap="sm">
      <Stack gap={2}>
        <FolderRow
          label="All"
          count={allCount}
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

      <Stack gap={2}>
        <Group justify="space-between" gap={4} wrap="nowrap" pl={6}>
          <Text size="xs" c="dimmed" tt="uppercase" fw={600}>
            Dates
          </Text>
          {(filters.date_from !== null || filters.date_to !== null) && (
            <Button size="compact-xs" variant="subtle" onClick={() => setRange(null)}>
              Clear
            </Button>
          )}
        </Group>

        {groups.length === 0 && (
          <Text size="xs" c="dimmed" pl={6}>
            No dated photos loaded.
          </Text>
        )}

        {groups.map((group) => {
          const expanded = openYears.includes(group.year)
          return (
            <Box key={group.year}>
              <FolderRow
                label={group.year}
                count={group.count}
                active={rangeActive(yearRange(group.year))}
                leading={
                  <UnstyledButton
                    component="span"
                    aria-label={expanded ? `Collapse ${group.year}` : `Expand ${group.year}`}
                    onClick={(event: MouseEvent) => {
                      event.stopPropagation()
                      toggleYear(group.year)
                    }}
                    style={{ lineHeight: 0, cursor: 'pointer' }}
                  >
                    {expanded ? <IconChevronDown size={12} /> : <IconChevronRight size={12} />}
                  </UnstyledButton>
                }
                onClick={() =>
                  setRange(rangeActive(yearRange(group.year)) ? null : yearRange(group.year))
                }
              />
              {expanded &&
                group.months.map((month) => {
                  const range = monthRange(month.key)
                  return (
                    <FolderRow
                      key={month.key}
                      label={month.key}
                      count={month.count}
                      indent={16}
                      active={rangeActive(range)}
                      onClick={() => setRange(rangeActive(range) ? null : range)}
                    />
                  )
                })}
            </Box>
          )
        })}
      </Stack>
    </Stack>
  )
}
