/**
 * JobHistoryPanel — the durable record of what actually ran.
 *
 * Distinct from JobQueuePanel in the one way that matters: that panel reads the
 * in-memory manager, which a server restart empties completely. This reads
 * `GET /jobs/history` (SQLite), so a backup that was 80 % through when the daemon was
 * restarted still appears here — as `interrupted` rather than as nothing at all.
 *
 * Deliberately a summary, not a log: op, outcome, when, how long, and the headline
 * counts. The full SSE event stream is not persisted (large, and only useful while
 * someone is watching); its terminal summary is the `result` shown here.
 */
import { useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import { Box, Card, Collapse, Group, Stack, Text, UnstyledButton } from '@mantine/core'
import { IconChevronRight, IconHistory } from '@tabler/icons-react'
import { motion } from 'motion/react'
import { MOTION_DURATION, MOTION_EASE_STANDARD } from 'basalt-ui'
import { useReducedMotion } from '@mantine/hooks'
import { VX, alpha } from 'basalt-ui/tokens'
import { jobsQueries } from '../../lib/queries/jobs'
import { OP_LABELS, RESULT_LABELS } from '../../lib/op-metadata'
import type { ActiveOp } from '../../lib/store'
import type { JobHistoryEntry } from '../../lib/api-types'
import { JobStatusBadge } from './JobStatusBadge'

const HISTORY_LIMIT = 25

/** Result keys that are transport detail rather than an outcome worth a summary line. */
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

function relativeTime(iso: string | null): string {
  if (iso === null) return '—'
  const then = new Date(iso).getTime()
  if (Number.isNaN(then)) return '—'
  const deltaS = Math.max(0, Math.round((Date.now() - then) / 1000))
  if (deltaS < 60) return 'just now'
  const mins = Math.round(deltaS / 60)
  if (mins < 60) return `${mins}m ago`
  const hours = Math.round(mins / 60)
  if (hours < 24) return `${hours}h ago`
  return `${Math.round(hours / 24)}d ago`
}

/** Wall-clock time the job spent running — the number that says "this was 22 GB". */
function duration(startedAt: string | null, finishedAt: string | null): string | null {
  if (startedAt === null || finishedAt === null) return null
  const ms = new Date(finishedAt).getTime() - new Date(startedAt).getTime()
  if (Number.isNaN(ms) || ms < 0) return null
  const secs = Math.round(ms / 1000)
  if (secs < 60) return `${secs}s`
  const mins = Math.floor(secs / 60)
  if (mins < 60) return `${mins}m ${secs % 60}s`
  return `${Math.floor(mins / 60)}h ${mins % 60}m`
}

/** The two or three counts worth showing inline, in the op's own vocabulary. */
function summarize(entry: JobHistoryEntry): string | null {
  if (entry.result === null) return null
  const parts = Object.entries(entry.result)
    .filter(([k, v]) => !SKIP_RESULT.has(k) && typeof v === 'number' && v > 0)
    .slice(0, 3)
    .map(([k, v]) => `${v} ${(RESULT_LABELS[k] ?? k).toLowerCase()}`)
  return parts.length > 0 ? parts.join(' · ') : null
}

function HistoryRow({ entry }: { entry: JobHistoryEntry }) {
  const baseOp = entry.op.split(':')[0] as ActiveOp
  const label = OP_LABELS[baseOp] ?? entry.op
  const source = entry.op.includes(':') ? entry.op.split(':')[1] : null
  const summary = summarize(entry)
  const took = duration(entry.started_at, entry.finished_at)
  const isBad = entry.status === 'failed' || entry.status === 'interrupted'

  return (
    <Group gap={10} py={6} align="flex-start" wrap="nowrap">
      <Stack gap={2} style={{ flex: 1, minWidth: 0 }}>
        <Group gap={8} wrap="nowrap">
          <Text size="sm" fw={500} truncate>
            {label}
            {source !== null && (
              <Text span size="xs" c="dimmed" ml={4}>
                ({source})
              </Text>
            )}
          </Text>
          <JobStatusBadge status={entry.status} />
        </Group>
        {entry.error !== null && (
          <Text size="xs" style={{ color: isBad ? VX.bad : VX.warn }}>
            {entry.error}
          </Text>
        )}
        {entry.error === null && summary !== null && (
          <Text size="xs" c="dimmed">
            {summary}
          </Text>
        )}
      </Stack>
      <Stack gap={2} align="flex-end" style={{ flexShrink: 0 }}>
        <Text size="xs" c="dimmed" ff="monospace">
          {relativeTime(entry.finished_at ?? entry.queued_at)}
        </Text>
        {took !== null && (
          <Text size="xs" ff="monospace" style={{ color: alpha(VX.neutral, 0.45) }}>
            {took}
          </Text>
        )}
      </Stack>
    </Group>
  )
}

export function JobHistoryPanel() {
  const [opened, setOpened] = useState(false)
  const reducedMotion = useReducedMotion()
  const { data } = useQuery(jobsQueries.history(HISTORY_LIMIT))

  const entries = data?.jobs ?? []
  if (entries.length === 0) return null

  const interrupted = entries.filter((e) => e.status === 'interrupted').length

  const chevron = <IconChevronRight size={14} style={{ color: alpha(VX.neutral, 0.5) }} />

  return (
    <Card py="xs" px="sm">
      <UnstyledButton onClick={() => setOpened((o) => !o)} aria-expanded={opened}>
        <Group gap={8} wrap="nowrap">
          <IconHistory size={16} style={{ color: alpha(VX.neutral, 0.55) }} />
          <Text size="sm" fw={600} style={{ flex: 1 }}>
            Job history
          </Text>
          {interrupted > 0 && (
            <Text size="xs" style={{ color: VX.warn }}>
              {interrupted} interrupted
            </Text>
          )}
          <Text size="xs" c="dimmed">
            {entries.length}
          </Text>
          {reducedMotion ? (
            <Box style={{ lineHeight: 0, transform: opened ? 'rotate(90deg)' : undefined }}>
              {chevron}
            </Box>
          ) : (
            <motion.div
              style={{ lineHeight: 0 }}
              animate={{ rotate: opened ? 90 : 0 }}
              transition={{ duration: MOTION_DURATION.fast, ease: MOTION_EASE_STANDARD }}
            >
              {chevron}
            </motion.div>
          )}
        </Group>
      </UnstyledButton>

      <Collapse expanded={opened}>
        <Stack gap={0} mt={6}>
          {entries.map((entry) => (
            <HistoryRow key={entry.job_id} entry={entry} />
          ))}
        </Stack>
      </Collapse>
    </Card>
  )
}
