/**
 * The culling screen's one piece of chrome.
 *
 * Everything that used to be a top bar, a left folder rail and a right info panel now
 * lives in this single right-hand column: three regions of chrome boxing the photo in
 * from three sides became one edge, and the top bar's ~40px went back to the image.
 *
 * Three rules hold it together:
 *
 * 1. **It is nothing but sections.** Four of them — Folders, Filters, Info, View —
 *    independent collapsibles (not an accordion: closing one to open another is a tax on
 *    a screen you sit in for an hour), each persisting its own open state and showing a
 *    one-glance `summary` while closed. There is no pinned header above them: a filename
 *    and a portrait/landscape badge are not worth permanent real estate on a screen whose
 *    whole subject is the photograph, so the stars and the label moved into Info.
 * 2. **A section is capped, not unbounded** — see `SidebarSection`. Opening Filters must
 *    not push View off the panel; the list of sections has to stay a list.
 * 3. **The user owns the width.** The leading edge is a drag handle. The live width is
 *    local state and only lands in localStorage on pointer-up, so a drag is not 60
 *    storage writes a second.
 */
import { useRef, useState } from 'react'
import type { KeyboardEvent, PointerEvent } from 'react'
import { Badge, Box, Button, Flex, ScrollArea, Stack, Switch, Text } from '@mantine/core'
import {
  IconEye,
  IconFilter,
  IconFolder,
  IconInfoCircle,
  IconTool,
  IconTrash,
} from '@tabler/icons-react'
import { createPersistedState } from 'basalt-ui/state'
import { activeFilterCount, type Facets, type PhotoFilters, type PhotoRow } from '../../lib/photos'
import { PhotoInfo } from './photo-info-panel'
import { PhotoFilterPanel } from './photo-filters'
import { PhotoFolders, rootLabel, type RootCounts } from './photo-folders'
import { SidebarSection } from './sidebar-section'
import classes from './photos-screen.module.css'

// ── Width ────────────────────────────────────────────────────────────────────

export const SIDEBAR_DEFAULT_WIDTH = 312
const SIDEBAR_MIN_WIDTH = 248
const SIDEBAR_MAX_WIDTH = 560
/** Px per arrow-key press on the focused drag handle. */
const SIDEBAR_KEY_STEP = 16

const clampWidth = (px: number): number =>
  Math.min(SIDEBAR_MAX_WIDTH, Math.max(SIDEBAR_MIN_WIDTH, Math.round(px)))

// ── Persisted view state ─────────────────────────────────────────────────────

const useSidebarWidth = createPersistedState({
  key: 'photos-sidebar-width',
  version: 1,
  initial: SIDEBAR_DEFAULT_WIDTH,
})
const useFoldersOpen = createPersistedState({
  key: 'photos-section-folders',
  version: 1,
  initial: false,
})
const useFiltersOpen = createPersistedState({
  key: 'photos-section-filters',
  version: 1,
  initial: false,
})
// The only section open by default, and it has to be: it carries the stars and the label.
// The other three are setup, and setup folds away.
const useInfoOpen = createPersistedState({ key: 'photos-section-info', version: 1, initial: true })
const useViewOpen = createPersistedState({ key: 'photos-section-view', version: 1, initial: false })
// Open by default: it holds the only button in the app that moves a culled photo, and a
// pass that has accumulated rejects should say so without being opened first.
const useCullOpen = createPersistedState({ key: 'photos-section-cull', version: 1, initial: true })
// Open by default, because the whole point of this section is that nobody could find the
// editor. Once handing a photo to Shutterflow is muscle memory (`E`, or right-click the
// photo) this can go back to being closed like the rest of the setup sections.
const useToolsOpen = createPersistedState({
  key: 'photos-section-tools',
  version: 1,
  initial: true,
})

// ── Resize handle ────────────────────────────────────────────────────────────

type ResizeHandleProps = {
  width: number
  onResize: (px: number) => void
  onCommit: (px: number) => void
}

function ResizeHandle({ width, onResize, onCommit }: ResizeHandleProps) {
  const drag = useRef<{ x: number; width: number } | null>(null)
  const [dragging, setDragging] = useState(false)

  const handlePointerDown = (event: PointerEvent<HTMLDivElement>): void => {
    event.currentTarget.setPointerCapture(event.pointerId)
    drag.current = { x: event.clientX, width }
    setDragging(true)
  }

  const handlePointerMove = (event: PointerEvent<HTMLDivElement>): void => {
    const start = drag.current
    if (start === null) return
    // The handle is on the sidebar's LEADING edge, so dragging left widens it.
    onResize(clampWidth(start.width + (start.x - event.clientX)))
  }

  const handlePointerUp = (): void => {
    if (drag.current === null) return
    drag.current = null
    setDragging(false)
    onCommit(width)
  }

  // Arrow keys nudge the width. The route's cull shortcuts skip any event whose target
  // sits inside a `[role="separator"]`, so this never also steps the photo selection.
  const handleKeyDown = (event: KeyboardEvent<HTMLDivElement>): void => {
    const delta =
      event.key === 'ArrowLeft' ? SIDEBAR_KEY_STEP : event.key === 'ArrowRight' ? -SIDEBAR_KEY_STEP : 0
    if (delta === 0) return
    event.preventDefault()
    const next = clampWidth(width + delta)
    onResize(next)
    onCommit(next)
  }

  return (
    <Box
      className={classes.resizer}
      // A focusable window splitter IS `role="separator"` per WAI-ARIA (tabindex +
      // aria-valuenow/min/max); `<hr>` is the decorative case the rule assumes and cannot
      // take a drag or a keypress.
      // eslint-disable-next-line jsx-a11y/prefer-tag-over-role
      role="separator"
      aria-orientation="vertical"
      aria-label="Resize sidebar"
      aria-valuenow={width}
      aria-valuemin={SIDEBAR_MIN_WIDTH}
      aria-valuemax={SIDEBAR_MAX_WIDTH}
      tabIndex={0}
      data-dragging={dragging}
      onPointerDown={handlePointerDown}
      onPointerMove={handlePointerMove}
      onPointerUp={handlePointerUp}
      onPointerCancel={handlePointerUp}
      onKeyDown={handleKeyDown}
    />
  )
}

// ── Sidebar ──────────────────────────────────────────────────────────────────

export type PhotoSidebarProps = {
  filters: PhotoFilters
  facets: Facets | undefined
  rows: PhotoRow[]
  rootCounts: RootCounts
  selectedRow: PhotoRow | null
  onFilters: (next: PhotoFilters) => void
  onRate: (rating: number) => void
  onLabel: (label: string) => void
  /** View toggles — each also has a one-key shortcut owned by the route. */
  filmstripOpen: boolean
  onFilmstrip: (open: boolean) => void
  zoomed: boolean
  onZoom: (zoomed: boolean) => void
  canZoom: boolean
  showTrashed: boolean
  onShowTrashed: (show: boolean) => void
  onOpenTrash: () => void
  showRejected: boolean
  onShowRejected: (show: boolean) => void
  rejectCount: number
  onPurgeRejects: () => void
  purging: boolean
  /** Hand the selected photo to the external editor. Also bound to `E` by the route. */
  onEdit: () => void
  editing: boolean
}

export function PhotoSidebar({
  filters,
  facets,
  rows,
  rootCounts,
  selectedRow,
  onFilters,
  onRate,
  onLabel,
  filmstripOpen,
  onFilmstrip,
  zoomed,
  onZoom,
  canZoom,
  showTrashed,
  onShowTrashed,
  onOpenTrash,
  showRejected,
  onShowRejected,
  rejectCount,
  onPurgeRejects,
  purging,
  onEdit,
  editing,
}: PhotoSidebarProps) {
  const [storedWidth, setStoredWidth] = useSidebarWidth()
  // Live width during a drag; `null` means "not dragging, read the persisted value".
  const [dragWidth, setDragWidth] = useState<number | null>(null)
  const width = dragWidth ?? storedWidth

  const [foldersOpen, setFoldersOpen] = useFoldersOpen()
  const [filtersOpen, setFiltersOpen] = useFiltersOpen()
  const [infoOpen, setInfoOpen] = useInfoOpen()
  const [viewOpen, setViewOpen] = useViewOpen()
  const [cullOpen, setCullOpen] = useCullOpen()
  const [toolsOpen, setToolsOpen] = useToolsOpen()

  // Root is excluded: `clearFilters` deliberately keeps it, so counting it here would
  // advertise a filter the Clear button does not clear.
  const filterCount = activeFilterCount({ ...filters, root: null })

  return (
    <Flex wrap="nowrap" style={{ flexShrink: 0 }}>
      <ResizeHandle
        width={width}
        onResize={setDragWidth}
        onCommit={(px) => {
          setStoredWidth(px)
          setDragWidth(null)
        }}
      />

      {/*
        No background on the column. The sections are cards (see `SidebarSection`), so the
        chrome takes up exactly as much of the right edge as it has content — below the last
        card the page surface simply continues, and the screen ends where the photographs do
        rather than at the bottom of a full-height slab.
      */}
      <Flex direction="column" w={width} h="100%" style={{ flexShrink: 0 }}>
        <ScrollArea style={{ flex: 1, minHeight: 0 }} type="hover" scrollbars="y" scrollbarSize={9}>
          {/* The cards ride the window's right edge — a 4px inset is enough for their ring and
              shadow to draw, and anything wider reads as a margin the photograph paid for. */}
          <Stack gap={6} p={4}>
            <SidebarSection
              title="Folders"
              icon={<IconFolder size={14} />}
              open={foldersOpen}
              onToggle={() => setFoldersOpen(!foldersOpen)}
              summary={
                <Text size="xs" c="dimmed">
                  {rootLabel(filters.root)}
                </Text>
              }
            >
              <PhotoFolders
                filters={filters}
                rows={rows}
                counts={rootCounts}
                onChange={onFilters}
              />
            </SidebarSection>

            <SidebarSection
              title="Filters"
              icon={<IconFilter size={14} />}
              open={filtersOpen}
              onToggle={() => setFiltersOpen(!filtersOpen)}
              summary={
                filterCount > 0 ? (
                  <Badge size="xs" variant="light">
                    {filterCount}
                  </Badge>
                ) : (
                  <Text size="xs" c="dimmed" ff="monospace">
                    {facets?.count ?? '—'}
                  </Text>
                )
              }
            >
              <PhotoFilterPanel value={filters} facets={facets} onChange={onFilters} />
            </SidebarSection>

            <SidebarSection
              title="Info"
              icon={<IconInfoCircle size={14} />}
              open={infoOpen}
              onToggle={() => setInfoOpen(!infoOpen)}
            >
              <PhotoInfo row={selectedRow} onRate={onRate} onLabel={onLabel} />
            </SidebarSection>

            {/*
              Tools sits between Info and Cull on purpose: it is what you do to the photo
              in front of you, which is the same class of action as rating it, and it is
              not part of ending the pass.
            */}
            <SidebarSection
              title="Tools"
              icon={<IconTool size={14} />}
              open={toolsOpen}
              onToggle={() => setToolsOpen(!toolsOpen)}
              summary={selectedRow === null ? 'no photo' : 'Shutterflow'}
            >
              <Stack gap="xs">
                <Button
                  size="compact-xs"
                  variant="default"
                  fullWidth
                  disabled={selectedRow === null}
                  loading={editing}
                  onClick={onEdit}
                >
                  Edit in Shutterflow
                </Button>
                <Text size="xs" c="dimmed">
                  Press <strong>E</strong>, or right-click the photo. Shutterflow crops and
                  straightens without re-encoding — the edit is written into the
                  photograph&apos;s own XMP packet, so this library keeps one master, not a
                  copy.
                </Text>
              </Stack>
            </SidebarSection>

            <SidebarSection
              title="Cull"
              icon={<IconTrash size={14} />}
              open={cullOpen}
              onToggle={() => setCullOpen(!cullOpen)}
              summary={rejectCount === 0 ? 'none' : `${rejectCount} rejected`}
            >
              <Stack gap="xs">
                <Text size="xs" c="dimmed">
                  {rejectCount === 0
                    ? 'Press X to reject the current photo. Nothing moves until you purge.'
                    : `${rejectCount} photo${rejectCount === 1 ? '' : 's'} flagged rejected. The files have not moved.`}
                </Text>
                <Switch
                  size="xs"
                  label="Show rejected"
                  description="Keep rejects in the pass, dimmed"
                  checked={showRejected}
                  onChange={(event) => onShowRejected(event.currentTarget.checked)}
                />
                <Button
                  size="compact-xs"
                  variant="default"
                  fullWidth
                  disabled={rejectCount === 0}
                  loading={purging}
                  onClick={onPurgeRejects}
                >
                  Move rejects to trash…
                </Button>
              </Stack>
            </SidebarSection>

            <SidebarSection
              title="View"
              icon={<IconEye size={14} />}
              open={viewOpen}
              onToggle={() => setViewOpen(!viewOpen)}
            >
              <Stack gap="xs">
                <Switch
                  size="xs"
                  label="Filmstrip"
                  description="F"
                  checked={filmstripOpen}
                  onChange={(event) => onFilmstrip(event.currentTarget.checked)}
                />
                <Switch
                  size="xs"
                  label="Zoom 1:1"
                  description="Z"
                  disabled={!canZoom}
                  checked={zoomed}
                  onChange={(event) => onZoom(event.currentTarget.checked)}
                />
                <Switch
                  size="xs"
                  label="Show trashed"
                  description="Culled frames stay in place, greyed"
                  checked={showTrashed}
                  onChange={(event) => onShowTrashed(event.currentTarget.checked)}
                />
                <Button size="compact-xs" variant="default" fullWidth onClick={onOpenTrash}>
                  Open trash…
                </Button>
              </Stack>
            </SidebarSection>
          </Stack>
        </ScrollArea>
      </Flex>
    </Flex>
  )
}
