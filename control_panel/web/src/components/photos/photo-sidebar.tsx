/**
 * The culling screen's one piece of chrome.
 *
 * Everything that used to be a top bar, a left folder rail and a right info panel now
 * lives in this single right-hand column: three regions of chrome boxing the photo in
 * from three sides became one edge, and the top bar's ~40px went back to the image.
 *
 * Three rules hold it together:
 *
 * 1. **It is nothing but sections.** Narrowing, Folders, Structure, Filters, Info, View,
 *    Cull, Tools —
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
import {
  Badge,
  Box,
  Button,
  Divider,
  Flex,
  ScrollArea,
  Slider,
  Stack,
  Switch,
  Text,
} from '@mantine/core'
import {
  IconColumns2,
  IconEye,
  IconFilter,
  IconFilterCog,
  IconFolder,
  IconInfoCircle,
  IconStack2,
  IconTool,
  IconTrash,
} from '@tabler/icons-react'
import { createPersistedState } from 'basalt-ui/state'
import {
  activeFilterCount,
  type EditorKind,
  type EditorProfile,
  type Facets,
  type PhotoFilters,
  type PhotoRow,
} from '../../lib/photos'
import { formatCount, type Narrowing } from '../../lib/narrowing'
import { PhotoInfo } from './photo-info-panel'
import { PhotoNarrowing } from './photo-narrowing'
import { PhotoFilterPanel } from './photo-filters'
import { PhotoFolders, rootLabel, type RootCounts } from './photo-folders'
import { PhotoStructure } from './photo-structure'
import {
  STRUCTURE_LABELS,
  type Structure,
  type StructureGroup,
  type StructureKind,
} from '../../lib/structure'
import { MAX_COMPARE } from './photo-compare'
import { DENSITY_STEPS, MAX_DENSITY, MIN_DENSITY } from './photo-grid'
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
// Open by default. It is the section that says what you are looking at and why, which
// is a new question the moment a saved collection can be layered under a filter — and a
// readout nobody opens explains nothing.
const useNarrowingOpen = createPersistedState({
  key: 'photos-section-narrowing',
  version: 1,
  initial: true,
})
const useFoldersOpen = createPersistedState({
  key: 'photos-section-folders',
  version: 1,
  initial: false,
})
// The Stage F3 section: four candidate library layouts, all of them queries. Closed by
// default like the other setup sections — it is a place you go to choose a way of
// navigating, not something you need in front of you while culling.
// Exported because the route gates the structure REQUEST on it: the endpoint reads and
// parses the whole `date_taken` column for `event`, and paying that on every filter tweak
// for a card nobody has opened is exactly the cost this section was kept cheap to avoid.
// `createPersistedState` is backed by `useSyncExternalStore`, so both call sites of this
// one factory hook see the same value.
export const useStructureOpen = createPersistedState({
  key: 'photos-section-structure',
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
// Closed by default. Comparing is a mode you enter with a key and leave with a key; the
// card is the place the vocabulary is written down, not somewhere you work.
const useCompareOpen = createPersistedState({
  key: 'photos-section-compare',
  version: 1,
  initial: false,
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
  rootCounts: RootCounts
  selectedRow: PhotoRow | null
  /** The active collection's id, or null for the whole library. */
  scope: string | null
  /** Server-computed readout of what is narrowing the result set. */
  narrowing: Narrowing | undefined
  onFilters: (next: PhotoFilters) => void
  onScope: (id: string | null) => void
  /** Clear one whole narrowing dimension, by the filter fields the server named. */
  onClearDimension: (fields: string[]) => void
  /** The library-structure experiment: which layout is on show, and its groups. */
  structureKind: StructureKind
  onStructureKind: (kind: StructureKind) => void
  eventGap: number
  onEventGap: (seconds: number) => void
  structure: Structure | undefined
  isStructureGroupActive: (group: StructureGroup) => boolean
  onStructureGroup: (group: StructureGroup | null) => void
  onRate: (rating: number) => void
  onLabel: (label: string) => void
  /** View toggles — each also has a one-key shortcut owned by the route. */
  filmstripOpen: boolean
  onFilmstrip: (open: boolean) => void
  zoomed: boolean
  onZoom: (zoomed: boolean) => void
  canZoom: boolean
  /** Whether a zoom may fetch the master's own bytes rather than magnifying the proxy. */
  trueResolution: boolean
  onTrueResolution: (on: boolean) => void
  /**
   * The marked set: how many frames it holds, how many the compare stage can show, and
   * the three things you can do to it.
   *
   * The two counts are separate because they can differ — the grid marks up to
   * `MAX_MARKED`, the compare stage renders `MAX_COMPARE` of them, and a panel that
   * reported only the second would make five frames disappear without saying so.
   */
  markedCount: number
  compareCount: number
  onCompare: () => void
  onClearCompare: () => void
  onPickKeeper: () => void
  /** The contact sheet: whether it is the stage, and how big its cells are. */
  gridOpen: boolean
  onGrid: (open: boolean) => void
  density: number
  onDensity: (px: number) => void
  showTrashed: boolean
  onShowTrashed: (show: boolean) => void
  onOpenTrash: () => void
  showRejected: boolean
  onShowRejected: (show: boolean) => void
  rejectCount: number
  onPurgeRejects: () => void
  purging: boolean
  /** Configured applications per file kind — the server's `[[editors]]`, filtered by `handles`. */
  jpegEditors: EditorProfile[]
  rawEditors: EditorProfile[]
  /**
   * Hand the selected photo (or, with `target: 'raw'`, the RAW that correlates to it) to
   * an external application. The default JPEG hand-over is also bound to `E` by the route.
   */
  onEdit: (target: EditorKind, editorId?: string) => void
  editing: boolean
}

export function PhotoSidebar({
  filters,
  facets,
  rootCounts,
  selectedRow,
  scope,
  narrowing,
  onFilters,
  onScope,
  onClearDimension,
  structureKind,
  onStructureKind,
  eventGap,
  onEventGap,
  structure,
  isStructureGroupActive,
  onStructureGroup,
  onRate,
  onLabel,
  filmstripOpen,
  onFilmstrip,
  zoomed,
  onZoom,
  canZoom,
  trueResolution,
  onTrueResolution,
  markedCount,
  compareCount,
  onCompare,
  onClearCompare,
  onPickKeeper,
  gridOpen,
  onGrid,
  density,
  onDensity,
  showTrashed,
  onShowTrashed,
  onOpenTrash,
  showRejected,
  onShowRejected,
  rejectCount,
  onPurgeRejects,
  purging,
  jpegEditors,
  rawEditors,
  onEdit,
  editing,
}: PhotoSidebarProps) {
  const [storedWidth, setStoredWidth] = useSidebarWidth()
  // Live width during a drag; `null` means "not dragging, read the persisted value".
  const [dragWidth, setDragWidth] = useState<number | null>(null)
  const width = dragWidth ?? storedWidth

  const [narrowingOpen, setNarrowingOpen] = useNarrowingOpen()
  const [foldersOpen, setFoldersOpen] = useFoldersOpen()
  const [structureOpen, setStructureOpen] = useStructureOpen()
  const [filtersOpen, setFiltersOpen] = useFiltersOpen()
  const [infoOpen, setInfoOpen] = useInfoOpen()
  const [viewOpen, setViewOpen] = useViewOpen()
  const [cullOpen, setCullOpen] = useCullOpen()
  const [toolsOpen, setToolsOpen] = useToolsOpen()
  const [compareOpen, setCompareOpen] = useCompareOpen()

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
            {/*
              First card, above Folders and Filters, because it is the answer those two
              produce. It also carries the only control that leaves a collection, which
              needs to be reachable without hunting for the row you clicked.
            */}
            <SidebarSection
              title="Narrowing"
              icon={<IconFilterCog size={14} />}
              open={narrowingOpen}
              onToggle={() => setNarrowingOpen(!narrowingOpen)}
              summary={
                <Text size="xs" c="dimmed" ff="monospace">
                  {narrowing === undefined ? '—' : formatCount(narrowing.total)}
                </Text>
              }
            >
              <PhotoNarrowing
                narrowing={narrowing}
                onClear={onClearDimension}
                onClearScope={() => onScope(null)}
              />
            </SidebarSection>

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
                counts={rootCounts}
                scope={scope}
                onChange={onFilters}
                onScope={onScope}
              />
            </SidebarSection>

            <SidebarSection
              title="Structure"
              icon={<IconStack2 size={14} />}
              open={structureOpen}
              onToggle={() => setStructureOpen(!structureOpen)}
              summary={
                <Text size="xs" c="dimmed">
                  {STRUCTURE_LABELS[structureKind]}
                  {structure === undefined ? '' : ` · ${structure.groups.length}`}
                </Text>
              }
            >
              <PhotoStructure
                kind={structureKind}
                onKind={onStructureKind}
                gapSeconds={eventGap}
                onGap={onEventGap}
                structure={structure}
                isActive={isStructureGroupActive}
                onSelect={onStructureGroup}
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
              Compare sits directly under Info: it is a way of LOOKING at the photograph
              the Info card describes, and the pick button below is the only control on
              this screen that writes to more than one file at a time.
            */}
            <SidebarSection
              title="Compare"
              icon={<IconColumns2 size={14} />}
              open={compareOpen}
              onToggle={() => setCompareOpen(!compareOpen)}
              summary={
                markedCount === 0
                  ? 'off'
                  : gridOpen
                    ? `${markedCount} selected`
                    : markedCount > MAX_COMPARE
                      ? `${compareCount} of ${markedCount}`
                      : `${markedCount} up`
              }
            >
              <Stack gap="xs">
                <Text size="xs" c="dimmed">
                  <strong>C</strong> compares the current frame with its neighbour.{' '}
                  <strong>Shift</strong>+arrows grow the set to {MAX_COMPARE};{' '}
                  <strong>Cmd</strong>-click the strip to add a frame that is not adjacent.
                  Arrows then move between the frames, <strong>Esc</strong> leaves.
                </Text>
                {markedCount > MAX_COMPARE && !gridOpen && (
                  <Text size="xs" c="dimmed">
                    {markedCount} frames are selected; the comparison shows the first{' '}
                    {MAX_COMPARE}. The rest are still selected — press <strong>G</strong> to
                    act on all of them in the grid.
                  </Text>
                )}
                {gridOpen && markedCount > 0 && (
                  <Text size="xs" c="dimmed">
                    In the grid a rating key writes to all {markedCount} selected frames. In
                    the viewer it never does — there a star is a judgement about the one
                    photograph in front of you.
                  </Text>
                )}
                <Button
                  size="compact-xs"
                  variant="default"
                  fullWidth
                  disabled={!canZoom}
                  onClick={onCompare}
                >
                  {compareCount >= 2 ? 'Leave comparison' : 'Compare with next'}
                </Button>
                <Button
                  size="compact-xs"
                  variant="default"
                  fullWidth
                  disabled={compareCount < 2}
                  onClick={onPickKeeper}
                >
                  Pick this one, reject the rest (P)
                </Button>
                <Text size="xs" c="dimmed">
                  A star key still writes to the focused frame only — a rating is a judgement
                  about one photograph. Picking is the judgement about the set, and it only
                  flags: nothing moves until you purge.
                </Text>
                {markedCount > 0 && (
                  <Button size="compact-xs" variant="default" fullWidth onClick={onClearCompare}>
                    Clear selection ({markedCount})
                  </Button>
                )}
              </Stack>
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
              summary={
                selectedRow === null
                  ? 'no photo'
                  : [...jpegEditors, ...rawEditors].map((editor) => editor.name).join(', ') ||
                    'none configured'
              }
            >
              <Stack gap="xs">
                {jpegEditors.length === 0 ? (
                  <Text size="xs" c="dimmed">
                    No JPEG editor configured.
                  </Text>
                ) : (
                  jpegEditors.map((editor) => (
                    <Button
                      key={editor.id}
                      size="compact-xs"
                      variant="default"
                      fullWidth
                      disabled={selectedRow === null}
                      loading={editing}
                      onClick={() => onEdit('jpeg', editor.id)}
                    >
                      Edit in {editor.name}
                    </Button>
                  ))
                )}
                <Text size="xs" c="dimmed">
                  Press <strong>E</strong> for the first editor above, or right-click the
                  photo. An editor crops and straightens without re-encoding — the edit is
                  written into the photograph&apos;s own XMP packet, so this library keeps
                  one master, not a copy.
                </Text>

                <Divider label="RAW" labelPosition="left" />

                {rawEditors.length === 0 ? (
                  <Text size="xs" c="dimmed">
                    No RAW developer configured. Add one under [[editors]] in
                    ~/.photoflow/config.toml.
                  </Text>
                ) : (
                  rawEditors.map((editor) => (
                    <Button
                      key={editor.id}
                      size="compact-xs"
                      variant="default"
                      fullWidth
                      disabled={selectedRow === null}
                      loading={editing}
                      onClick={() => onEdit('raw', editor.id)}
                    >
                      Open RAW in {editor.name}
                    </Button>
                  ))
                )}
                <Text size="xs" c="dimmed">
                  Takes the RAW that correlates to this JPG — a JPG-only shot, or an already
                  cleaned-up RAW, says so rather than opening the wrong file.
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
                  label="Contact sheet"
                  description="G — the whole result set, two-dimensional"
                  checked={gridOpen}
                  onChange={(event) => onGrid(event.currentTarget.checked)}
                />
                {gridOpen && (
                  <Box px={2} pb={4}>
                    <Text size="xs" c="dimmed" mb={4}>
                      Cell size — <strong>[</strong> / <strong>]</strong>
                    </Text>
                    <Slider
                      size="xs"
                      label={(value) => `${value} px`}
                      value={density}
                      min={MIN_DENSITY}
                      max={MAX_DENSITY}
                      marks={DENSITY_STEPS.map((value) => ({ value }))}
                      restrictToMarks
                      onChange={onDensity}
                    />
                  </Box>
                )}
                <Switch
                  size="xs"
                  label="Filmstrip"
                  description={gridOpen ? 'F — hidden while the sheet is up' : 'F'}
                  disabled={gridOpen}
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
                  label="True 1:1"
                  description="Zoom fetches the master (5-22 MB), not the 2048px proxy"
                  checked={trueResolution}
                  onChange={(event) => onTrueResolution(event.currentTarget.checked)}
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
