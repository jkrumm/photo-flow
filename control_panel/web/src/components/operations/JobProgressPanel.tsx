import { useEffect, useRef } from 'react'
import { motion } from 'framer-motion'
import { Button, Group, Progress, Stack, Text } from '@mantine/core'
import { IconCheck, IconPlayerStopFilled, IconX } from '@tabler/icons-react'
import { VX } from '../../lib/charts/tokens'
import { alpha } from '../../lib/charts/utils/color'
import type { JobEventsState } from '../../hooks/useJobEvents'
import type { ActiveOp } from '../../lib/store'
import { useActiveJobStore } from '../../lib/store'
import { RESULT_LABELS } from '../../lib/op-metadata'

// ── Result summary ────────────────────────────────────────────────────────────

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

function formatDuration(ms: number): string {
  const s = Math.round(ms / 1000)
  if (s < 60) return `${s}s`
  const m = Math.floor(s / 60)
  if (m < 60) return `${m}m ${s % 60}s`
  const h = Math.floor(m / 60)
  return `${h}h ${m % 60}m`
}

// ── Main panel ────────────────────────────────────────────────────────────────

export type JobProgressPanelProps = {
  op: ActiveOp | null
  opLabel: string
  state: JobEventsState
  /** When true, render flush (no outer border/background/padding) — for embedding inside another panel. */
  embedded?: boolean
  /** Cancel handler — when provided and the job is running, a Stop button shows by the progress bar. */
  onStop?: () => void
  /** True while a cancel request is in flight. */
  stopping?: boolean
  /**
   * When true, the panel is actively running: live progress from `state` is shown and any
   * durable last-result from the store is ignored. When false (or omitted), the panel falls
   * back to the durable store result so the summary persists after clearActiveJob().
   */
  isRunning?: boolean
}

export function JobProgressPanel({ op: _op, opLabel, state, embedded = false, onStop, stopping = false, isRunning = false }: JobProgressPanelProps) {
  const logRef = useRef<HTMLDivElement>(null)

  // Read durable terminal state so the result summary survives the useJobEvents reset that
  // happens when clearActiveJob() nulls the jobId. isRunning guards against showing a stale
  // result from a previous job while a new one is in progress.
  const durableResult = useActiveJobStore((s) => s.lastResult)
  const durableError = useActiveJobStore((s) => s.lastError)

  const showDurable = !isRunning && !state.isDone && (durableResult !== null || durableError !== null)
  const effectiveResult = state.isDone ? state.result : (showDurable ? durableResult : null)
  const effectiveError = state.isDone ? state.error : (showDurable ? durableError : null)
  const effectiveDone = state.isDone || showDurable

  // Auto-scroll log tail to bottom on new entries
  useEffect(() => {
    const el = logRef.current
    if (el) el.scrollTop = el.scrollHeight
  }, [state.logs.length])

  const pct =
    effectiveDone
      ? 100
      : state.taskTotal !== null && state.taskTotal > 0
        ? Math.min((state.taskProgress / state.taskTotal) * 100, 99)
        : 0

  const showTransfer = state.lastTransfer !== null && !effectiveDone

  // Derived ETA + throughput for per-file ops (backup gets speed/ETA from rsync directly).
  // Rate is a cumulative average since the task started — smoother than per-tick deltas.
  const etaRef = useRef<{ key: string; startTs: number; startProgress: number } | null>(null)
  let etaLabel: string | null = null
  let rateLabel: string | null = null
  if (effectiveDone) {
    etaRef.current = null
  } else if (state.taskTotal !== null && state.taskTotal > 0 && state.taskProgress > 0) {
    const key = `${state.taskDesc ?? ''}|${state.taskTotal}`
    if (etaRef.current === null || etaRef.current.key !== key) {
      etaRef.current = { key, startTs: Date.now(), startProgress: state.taskProgress }
    }
    const { startTs, startProgress } = etaRef.current
    const doneSince = state.taskProgress - startProgress
    const elapsed = Date.now() - startTs
    if (doneSince > 0 && elapsed > 750) {
      const ratePerMs = doneSince / elapsed
      const remaining = (state.taskTotal - state.taskProgress) / ratePerMs
      if (remaining > 0) etaLabel = formatDuration(remaining)
      const perSec = ratePerMs * 1000
      rateLabel = perSec >= 1 ? `${perSec.toFixed(0)}/s` : `${(perSec * 60).toFixed(0)}/min`
    }
  }

  return (
    <motion.div
      initial={{ opacity: 0, y: -8 }}
      animate={{ opacity: 1, y: 0 }}
      exit={{ opacity: 0, y: -8 }}
      transition={{ duration: 0.25 }}
      style={
        embedded
          ? { background: 'transparent', border: 'none', padding: 0 }
          : {
              background: VX.surface.panel,
              border: `1px solid ${effectiveDone && effectiveError === null ? alpha(VX.good, 0.4) : effectiveDone ? alpha(VX.bad, 0.4) : VX.surface.border}`,
              borderRadius: 10,
              padding: '16px 20px',
            }
      }
    >
      <Stack gap="sm">
        {/* Header */}
        <Group justify="space-between" align="center" gap="xs">
          <Group gap={8} align="center">
            {effectiveDone ? (
              effectiveError !== null ? (
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
              {effectiveDone
                ? effectiveError !== null
                  ? 'failed'
                  : 'complete'
                : state.taskDesc ?? 'running…'}
            </Text>
          </Group>
          {!effectiveDone && (
            <Group gap={10} align="center" wrap="nowrap">
              {state.taskTotal !== null && state.taskTotal > 0 && (
                <Text size="xs" c="dimmed" style={{ fontVariantNumeric: 'tabular-nums' }}>
                  {state.taskProgress} / {state.taskTotal}
                  {rateLabel ? ` · ${rateLabel}` : ''}
                  {etaLabel ? ` · ETA ${etaLabel}` : ''}
                </Text>
              )}
              {onStop && (
                <Button
                  size="compact-xs"
                  variant="light"
                  color="red"
                  leftSection={<IconPlayerStopFilled size={11} />}
                  loading={stopping}
                  onClick={onStop}
                >
                  Stop
                </Button>
              )}
            </Group>
          )}
        </Group>

        {/* Progress bar */}
        <Progress
          value={pct}
          color={effectiveDone && effectiveError !== null ? 'red' : effectiveDone ? 'green' : 'blue'}
          size="xs"
          radius="xs"
          animated={!effectiveDone}
        />

        {/* Last file processed */}
        {state.lastFileDone !== null && !effectiveDone && (
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
        {state.logs.length > 0 && !effectiveDone && (
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

        {/* Done: result summary — reads from durable store when state has been reset */}
        {effectiveDone && (
          <ResultSummary result={effectiveResult} error={effectiveError} />
        )}
      </Stack>
    </motion.div>
  )
}
