import { useEffect, useRef } from 'react'
import { motion } from 'framer-motion'
import { Group, Progress, Stack, Text } from '@mantine/core'
import { IconCheck, IconX } from '@tabler/icons-react'
import { VX } from '../../lib/charts/tokens'
import { alpha } from '../../lib/charts/utils/color'
import type { JobEventsState } from '../../hooks/useJobEvents'
import type { ActiveOp } from '../../lib/store'

// ── Result summary ────────────────────────────────────────────────────────────

const RESULT_LABELS: Record<string, string> = {
  photos: 'Photos imported',
  raws: 'RAWs imported',
  videos: 'Videos imported',
  skipped: 'Skipped (duplicates)',
  moved: 'Photos moved to Final',
  edits_moved: 'Sidecars moved',
  orphaned_raws: 'Orphaned RAWs found',
  deleted_raws: 'RAWs deleted',
  deleted_camera_raws: 'Camera RAWs deleted',
  orphaned: 'Orphaned RAWs found',
  deleted: 'RAWs deleted',
  scanned: 'Files scanned',
  synced: 'Photos synced',
  removed: 'Photos removed from gallery',
  unchanged: 'Unchanged',
  total_in_gallery: 'Total in gallery',
  build_successful: 'Build',
  sync_successful: 'Remote sync',
  total_scanned: 'Total files synced',
  all_successful: 'All sources succeeded',
  errors: 'Errors',
}

const SKIP_RESULT = new Set([
  'source',
  'sources',
  'connection_method',
  'trash_path',
  'immich_scan_triggered',
  'json_updated',
  'remote_path',
  'path',
])

function formatResultValue(v: unknown): string {
  if (v === null || v === undefined) return '—'
  if (typeof v === 'boolean') return v ? '✓' : '✗'
  if (Array.isArray(v)) return (v as unknown[]).join(', ')
  return String(v)
}

function ResultSummary({
  result,
  error,
}: {
  result: Record<string, unknown> | null
  error: string | null
}) {
  if (error !== null) {
    return (
      <Group gap="xs" align="flex-start">
        <IconX size={14} style={{ color: VX.bad, marginTop: 2, flexShrink: 0 }} />
        <Text size="xs" style={{ color: VX.bad }}>
          {error}
        </Text>
      </Group>
    )
  }
  if (result === null) {
    return <Text size="xs" c="dimmed">No result data.</Text>
  }
  const entries = Object.entries(result).filter(([k]) => !SKIP_RESULT.has(k))
  return (
    <div
      style={{
        display: 'grid',
        gridTemplateColumns: 'repeat(auto-fill, minmax(140px, 1fr))',
        gap: '4px 16px',
      }}
    >
      {entries.map(([k, v]) => (
        <div key={k} style={{ display: 'flex', flexDirection: 'column', gap: 1 }}>
          <Text size="xs" c="dimmed" style={{ lineHeight: 1.2 }}>
            {RESULT_LABELS[k] ?? k}
          </Text>
          <Text size="sm" fw={600} style={{ fontVariantNumeric: 'tabular-nums' }}>
            {formatResultValue(v)}
          </Text>
        </div>
      ))}
    </div>
  )
}

// ── Log tail ──────────────────────────────────────────────────────────────────

function levelColor(level: string): string {
  if (level === 'error') return VX.bad
  if (level === 'warning') return VX.warn
  if (level === 'success') return VX.good
  return alpha(VX.neutral, 0.7)
}

// ── Main panel ────────────────────────────────────────────────────────────────

export type JobProgressPanelProps = {
  op: ActiveOp | null
  opLabel: string
  state: JobEventsState
}

export function JobProgressPanel({ op: _op, opLabel, state }: JobProgressPanelProps) {
  const logRef = useRef<HTMLDivElement>(null)

  // Auto-scroll log tail to bottom on new entries
  useEffect(() => {
    const el = logRef.current
    if (el) el.scrollTop = el.scrollHeight
  }, [state.logs.length])

  const pct =
    state.isDone
      ? 100
      : state.taskTotal !== null && state.taskTotal > 0
        ? Math.min((state.taskProgress / state.taskTotal) * 100, 99)
        : 0

  const showTransfer = state.lastTransfer !== null && !state.isDone

  return (
    <motion.div
      initial={{ opacity: 0, y: -8 }}
      animate={{ opacity: 1, y: 0 }}
      exit={{ opacity: 0, y: -8 }}
      transition={{ duration: 0.25 }}
      style={{
        background: VX.surface.panel,
        border: `1px solid ${state.isDone && state.error === null ? alpha(VX.good, 0.4) : state.isDone ? alpha(VX.bad, 0.4) : VX.surface.border}`,
        borderRadius: 10,
        padding: '16px 20px',
      }}
    >
      <Stack gap="sm">
        {/* Header */}
        <Group justify="space-between" align="center" gap="xs">
          <Group gap={8} align="center">
            {state.isDone ? (
              state.error !== null ? (
                <IconX size={16} style={{ color: VX.bad }} />
              ) : (
                <IconCheck size={16} style={{ color: VX.good }} />
              )
            ) : (
              <motion.div
                style={{
                  width: 8,
                  height: 8,
                  borderRadius: '50%',
                  background: 'var(--vx-goodSolid)',
                  flexShrink: 0,
                }}
                animate={{ scale: [1, 1.35, 1], opacity: [1, 0.6, 1] }}
                transition={{ duration: 1, repeat: Infinity }}
              />
            )}
            <Text fw={600} size="sm">
              {opLabel}
            </Text>
            <Text size="xs" c="dimmed">
              {state.isDone
                ? state.error !== null
                  ? 'failed'
                  : 'complete'
                : state.taskDesc ?? 'running…'}
            </Text>
          </Group>
          {!state.isDone && state.taskTotal !== null && state.taskTotal > 0 && (
            <Text size="xs" c="dimmed" style={{ fontVariantNumeric: 'tabular-nums' }}>
              {state.taskProgress} / {state.taskTotal}
            </Text>
          )}
        </Group>

        {/* Progress bar */}
        <Progress
          value={pct}
          color={state.isDone && state.error !== null ? 'red' : state.isDone ? 'green' : 'blue'}
          size="xs"
          radius="xs"
          animated={!state.isDone}
        />

        {/* Last file processed */}
        {state.lastFileDone !== null && !state.isDone && (
          <Text
            size="xs"
            c="dimmed"
            style={{
              fontFamily: 'var(--mantine-font-family-monospace)',
              overflow: 'hidden',
              textOverflow: 'ellipsis',
              whiteSpace: 'nowrap',
            }}
          >
            ↳ {state.lastFileDone}
          </Text>
        )}

        {/* Rclone transfer info (backup ops) */}
        {showTransfer && state.lastTransfer !== null && (
          <Group gap={16} style={{ fontSize: 12 }}>
            {state.lastTransfer.pct !== undefined && (
              <span style={{ color: alpha(VX.neutral, 0.8) }}>
                {Math.round(state.lastTransfer.pct)}%
              </span>
            )}
            {state.lastTransfer.speed !== undefined && (
              <span style={{ color: 'var(--vx-goodSolid)' }}>{state.lastTransfer.speed}</span>
            )}
            {state.lastTransfer.eta !== undefined && (
              <span style={{ color: alpha(VX.neutral, 0.65) }}>
                ETA {state.lastTransfer.eta}
              </span>
            )}
            {state.lastTransfer.files !== undefined && state.lastTransfer.files !== '--' && (
              <span style={{ color: alpha(VX.neutral, 0.55) }}>
                {state.lastTransfer.files} files
              </span>
            )}
          </Group>
        )}

        {/* Log tail */}
        {state.logs.length > 0 && !state.isDone && (
          <div
            ref={logRef}
            style={{
              maxHeight: 120,
              overflowY: 'auto',
              background: VX.surface.bg,
              border: `1px solid ${alpha(VX.neutral, 0.1)}`,
              borderRadius: 6,
              padding: '6px 10px',
              display: 'flex',
              flexDirection: 'column',
              gap: 2,
            }}
          >
            {state.logs.slice(-20).map((log) => (
              <div
                key={log.id}
                style={{
                  display: 'flex',
                  gap: 8,
                  alignItems: 'baseline',
                  fontSize: 11,
                  lineHeight: 1.4,
                }}
              >
                <span
                  style={{
                    color: levelColor(log.level),
                    fontWeight: 600,
                    minWidth: 48,
                    textTransform: 'uppercase',
                    flexShrink: 0,
                  }}
                >
                  {log.level}
                </span>
                <span
                  style={{
                    color: alpha(VX.neutral, 0.8),
                    overflow: 'hidden',
                    textOverflow: 'ellipsis',
                    whiteSpace: 'nowrap',
                  }}
                >
                  {log.message}
                </span>
              </div>
            ))}
          </div>
        )}

        {/* Done: result summary */}
        {state.isDone && (
          <ResultSummary result={state.result} error={state.error} />
        )}
      </Stack>
    </motion.div>
  )
}
