/**
 * JobQueuePanel — shows the FIFO job queue below the pipeline graph.
 *
 * Displays queued + running + needs_confirm jobs. Hidden when the list is empty.
 * Features:
 *   - Op label + status badge per job
 *   - Cancel button (running, queued, needs_confirm)
 *   - Up / Down reorder arrows (queued jobs only)
 *   - "Review & confirm" for needs_confirm jobs — opens DryRunModal with the fresh preview
 */
import { useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { ActionIcon, Badge, Group, Stack, Text, Tooltip } from '@mantine/core'
import { notifications } from '@mantine/notifications'
import {
  IconX,
  IconChevronUp,
  IconChevronDown,
  IconAlertTriangle,
} from '@tabler/icons-react'
import { motion, AnimatePresence } from 'framer-motion'
import { VX } from '../../lib/charts/tokens'
import { alpha } from '../../lib/charts/utils/color'
import { jobsQueries } from '../../lib/queries/jobs'
import { opsApi } from '../../lib/queries/ops'
import { OP_LABELS } from '../../lib/op-metadata'
import type { ActiveOp } from '../../lib/store'
import type { JobQueueItem } from '../../lib/api-types'
import { DryRunModal } from './DryRunModal'

// ── Status badge ──────────────────────────────────────────────────────────────

function StatusBadge({ status }: { status: string }) {
  const map: Record<string, { color: string; label: string }> = {
    queued: { color: 'blue', label: 'Queued' },
    running: { color: 'green', label: 'Running' },
    needs_confirm: { color: 'orange', label: 'Needs review' },
    done: { color: 'gray', label: 'Done' },
    failed: { color: 'red', label: 'Failed' },
    cancelled: { color: 'gray', label: 'Cancelled' },
  }
  const m = map[status] ?? { color: 'gray', label: status }
  return (
    <Badge color={m.color} variant="light" size="xs" radius="sm">
      {m.label}
    </Badge>
  )
}

// ── Single row ────────────────────────────────────────────────────────────────

interface JobRowProps {
  job: JobQueueItem
  isFirst: boolean
  isLast: boolean
  onCancel: (id: string) => void
  onMoveUp: (id: string) => void
  onMoveDown: (id: string) => void
  onReview: (job: JobQueueItem) => void
}

function JobRow({ job, isFirst, isLast, onCancel, onMoveUp, onMoveDown, onReview }: JobRowProps) {
  const isQueued = job.status === 'queued'
  const isRunning = job.status === 'running'
  const isNeedsConfirm = job.status === 'needs_confirm'
  const canCancel = isQueued || isRunning || isNeedsConfirm

  // Derive a readable op label (strip the "backup:all" suffix if present).
  const baseOp = job.op.split(':')[0] as ActiveOp
  const opLabel = OP_LABELS[baseOp] ?? job.op

  return (
    <motion.div
      layout
      initial={{ opacity: 0, y: -6 }}
      animate={{ opacity: 1, y: 0 }}
      exit={{ opacity: 0, y: -6 }}
      transition={{ duration: 0.2 }}
      style={{
        display: 'flex',
        alignItems: 'center',
        gap: 10,
        padding: '7px 10px',
        borderRadius: 8,
        background: isRunning
          ? alpha('var(--vx-goodSolid)', 0.07)
          : isNeedsConfirm
            ? alpha(VX.warnSolid, 0.07)
            : alpha(VX.neutral, 0.03),
        border: `1px solid ${
          isRunning
            ? alpha('var(--vx-goodSolid)', 0.2)
            : isNeedsConfirm
              ? alpha(VX.warnSolid, 0.3)
              : VX.surface.border
        }`,
      }}
    >
      {/* Position badge for queued */}
      {isQueued && (
        <Text
          size="xs"
          fw={600}
          style={{ color: alpha(VX.neutral, 0.4), minWidth: 16, textAlign: 'center' }}
        >
          {job.position + 1}
        </Text>
      )}
      {isRunning && (
        <motion.div
          style={{
            width: 7,
            height: 7,
            borderRadius: '50%',
            background: 'var(--vx-goodSolid)',
            flexShrink: 0,
          }}
          animate={{ scale: [1, 1.4, 1], opacity: [1, 0.5, 1] }}
          transition={{ duration: 1, repeat: Infinity }}
        />
      )}
      {isNeedsConfirm && (
        <IconAlertTriangle size={14} style={{ color: VX.warnSolid, flexShrink: 0 }} />
      )}

      <Text size="sm" fw={500} style={{ flex: 1, minWidth: 0, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
        {opLabel}
        {job.op.includes(':') && (
          <Text span size="xs" c="dimmed" ml={4}>
            ({job.op.split(':')[1]})
          </Text>
        )}
      </Text>

      <StatusBadge status={job.status} />

      {/* needs_confirm: Review button */}
      {isNeedsConfirm && (
        <Tooltip label="The deletion set grew since you confirmed — review the updated preview" withArrow w={240} multiline>
          <motion.button
            type="button"
            onClick={() => onReview(job)}
            whileHover={{ scale: 1.04 }}
            whileTap={{ scale: 0.96 }}
            style={{
              padding: '3px 8px',
              borderRadius: 6,
              border: `1px solid ${alpha(VX.warnSolid, 0.5)}`,
              background: alpha(VX.warnSolid, 0.1),
              color: VX.warnSolid,
              fontSize: 11,
              fontWeight: 600,
              cursor: 'pointer',
              whiteSpace: 'nowrap',
            }}
          >
            Review &amp; confirm
          </motion.button>
        </Tooltip>
      )}

      {/* Reorder arrows (queued only) */}
      {isQueued && (
        <Group gap={2}>
          <Tooltip label="Move up" withArrow openDelay={500}>
            <ActionIcon
              variant="subtle"
              size="xs"
              color="gray"
              disabled={isFirst}
              onClick={() => onMoveUp(job.job_id)}
              aria-label="Move job up"
            >
              <IconChevronUp size={12} />
            </ActionIcon>
          </Tooltip>
          <Tooltip label="Move down" withArrow openDelay={500}>
            <ActionIcon
              variant="subtle"
              size="xs"
              color="gray"
              disabled={isLast}
              onClick={() => onMoveDown(job.job_id)}
              aria-label="Move job down"
            >
              <IconChevronDown size={12} />
            </ActionIcon>
          </Tooltip>
        </Group>
      )}

      {/* Cancel */}
      {canCancel && (
        <Tooltip label={isRunning ? 'Stop at next file' : 'Remove from queue'} withArrow openDelay={400}>
          <ActionIcon
            variant="subtle"
            size="xs"
            color="red"
            onClick={() => onCancel(job.job_id)}
            aria-label={isRunning ? 'Stop job' : 'Cancel queued job'}
          >
            <IconX size={12} />
          </ActionIcon>
        </Tooltip>
      )}
    </motion.div>
  )
}

// ── Panel ─────────────────────────────────────────────────────────────────────

/**
 * JobQueuePanel is mounted below the pipeline graph in PipelineHero.
 * It hides itself when there are no queued/running/needs_confirm jobs.
 */
export function JobQueuePanel() {
  const queryClient = useQueryClient()
  const { data } = useQuery(jobsQueries.list())

  // needs_confirm review state
  const [reviewJob, setReviewJob] = useState<JobQueueItem | null>(null)
  const [reviewModalOpen, setReviewModalOpen] = useState(false)

  const reEnqueueMutation = useMutation({
    mutationFn: (job: JobQueueItem) => {
      // split()[0] is typed string | undefined; fall back to the full op string.
      const baseOp = job.op.split(':')[0] ?? job.op
      const freshPreview = job.fresh_preview ?? {}
      return opsApi.start(baseOp, undefined, { approved_preview: freshPreview })
    },
    onSuccess: () => {
      setReviewModalOpen(false)
      setReviewJob(null)
      void queryClient.invalidateQueries({ queryKey: ['jobs', 'list'] })
    },
    onError: (err: Error) => {
      notifications.show({ title: 'Re-enqueue failed', message: err.message, color: 'red', autoClose: 6000 })
    },
  })

  const cancelMutation = useMutation({
    mutationFn: (jobId: string) => opsApi.cancel(jobId),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ['jobs', 'list'] })
      void queryClient.invalidateQueries({ queryKey: ['jobs', 'active'] })
    },
    onError: (err: Error) => {
      notifications.show({ title: 'Cancel failed', message: err.message, color: 'red', autoClose: 5000 })
    },
  })

  const moveMutation = useMutation({
    mutationFn: ({ jobId, direction }: { jobId: string; direction: 'up' | 'down' }) =>
      opsApi.move(jobId, direction),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ['jobs', 'list'] })
    },
  })

  // Only show queued, running, and needs_confirm jobs in the panel.
  const activeJobs = (data?.jobs ?? []).filter(
    (j) => j.status === 'queued' || j.status === 'running' || j.status === 'needs_confirm',
  )
  const queuedJobs = activeJobs.filter((j) => j.status === 'queued')

  if (activeJobs.length === 0) return null

  const handleReview = (job: JobQueueItem) => {
    setReviewJob(job)
    setReviewModalOpen(true)
  }

  // Derive the op type for DryRunModal's preview formatter.
  const reviewOp = reviewJob
    ? (reviewJob.op.split(':')[0] as ActiveOp)
    : 'cleanup'

  return (
    <>
      <motion.div
        initial={{ opacity: 0, height: 0 }}
        animate={{ opacity: 1, height: 'auto' }}
        exit={{ opacity: 0, height: 0 }}
        transition={{ duration: 0.25 }}
        style={{
          marginTop: 12,
          paddingTop: 12,
          borderTop: `1px solid ${VX.surface.border}`,
        }}
      >
        <Text
          size="xs"
          fw={600}
          style={{ color: alpha(VX.neutral, 0.5), textTransform: 'uppercase', letterSpacing: '0.06em', marginBottom: 8 }}
        >
          Queue — {activeJobs.length} job{activeJobs.length === 1 ? '' : 's'}
        </Text>
        <Stack gap={4}>
          <AnimatePresence>
            {activeJobs.map((job) => {
              const qIdx = queuedJobs.indexOf(job)
              return (
                <JobRow
                  key={job.job_id}
                  job={job}
                  isFirst={qIdx <= 0}
                  isLast={qIdx >= queuedJobs.length - 1}
                  onCancel={(id) => cancelMutation.mutate(id)}
                  onMoveUp={(id) => moveMutation.mutate({ jobId: id, direction: 'up' })}
                  onMoveDown={(id) => moveMutation.mutate({ jobId: id, direction: 'down' })}
                  onReview={handleReview}
                />
              )
            })}
          </AnimatePresence>
        </Stack>
      </motion.div>

      {/* needs_confirm re-confirm modal */}
      <DryRunModal
        opened={reviewModalOpen}
        onClose={() => {
          setReviewModalOpen(false)
          setReviewJob(null)
        }}
        onConfirm={() => reviewJob && reEnqueueMutation.mutate(reviewJob)}
        isConfirming={reEnqueueMutation.isPending}
        opId={reviewOp}
        opLabel={OP_LABELS[reviewOp] ?? reviewOp}
        isDestructive
        preview={reviewJob?.fresh_preview ?? null}
      />
    </>
  )
}
