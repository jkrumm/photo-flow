import { createFileRoute } from '@tanstack/react-router'
import { useQueryClient } from '@tanstack/react-query'
import { AnimatePresence } from 'framer-motion'
import { Stack, Text, Title } from '@mantine/core'
import { IconPlayerPlay } from '@tabler/icons-react'
import { VX } from '../lib/charts/tokens'
import { useJobEvents } from '../hooks/useJobEvents'
import { useActiveJobStore, type ActiveOp } from '../lib/store'
import { OperationCard, type OpCardDef } from '../components/operations/OperationCard'
import { JobProgressPanel } from '../components/operations/JobProgressPanel'

export const Route = createFileRoute('/operations')({
  component: OperationsPage,
})

// ── Op definitions ────────────────────────────────────────────────────────────

const OP_DEFS: OpCardDef[] = [
  {
    id: 'import' as ActiveOp,
    label: 'Import',
    shortDesc: 'Move files from camera to Staging, RAWs, and Videos. Renames with timestamp.',
    color: VX.photo.camera,
    isDestructive: true,
  },
  {
    id: 'finalize' as ActiveOp,
    label: 'Finalize',
    shortDesc: 'Move Staging JPGs to Final at full quality. Carry .photo-edit sidecars.',
    color: VX.photo.staging,
    isDestructive: true,
  },
  {
    id: 'cleanup' as ActiveOp,
    label: 'Cleanup RAWs',
    shortDesc: 'Delete orphaned RAW files that have no matching Final JPG.',
    color: VX.photo.raws,
    isDestructive: true,
  },
  {
    id: 'sync-gallery' as ActiveOp,
    label: 'Sync Gallery',
    shortDesc: 'Sync rating ≥ 4 photos to the gallery, build with npm, deploy to remote.',
    color: VX.photo.published,
    isDestructive: false,
  },
  {
    id: 'backup' as ActiveOp,
    label: 'Backup',
    shortDesc: 'Rclone backup to homelab over Tailscale. Select which source to back up.',
    color: VX.photo.final,
    isDestructive: false,
  },
]

const OP_LABELS: Record<ActiveOp, string> = {
  import: 'Import',
  finalize: 'Finalize',
  cleanup: 'Cleanup RAWs',
  'sync-gallery': 'Sync Gallery',
  backup: 'Backup',
}

// ── Page ──────────────────────────────────────────────────────────────────────

function OperationsPage() {
  const queryClient = useQueryClient()
  const activeJobId = useActiveJobStore((s) => s.activeJobId)
  const activeOp = useActiveJobStore((s) => s.activeOp)
  const clearActiveJob = useActiveJobStore((s) => s.clearActiveJob)

  const eventsState = useJobEvents(activeJobId, {
    onDone: () => {
      clearActiveJob()
      void queryClient.invalidateQueries({ queryKey: ['status'] })
      void queryClient.invalidateQueries({ queryKey: ['analytics', 'summary'] })
      void queryClient.invalidateQueries({ queryKey: ['backup', 'availability'] })
    },
  })

  const isJobRunning = activeJobId !== null

  return (
    <Stack gap="md">
      <Stack gap={4}>
        <Title order={2} style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
          <IconPlayerPlay size={24} />
          Operations
        </Title>
        <Text c="dimmed" size="sm">
          Import · Finalize · Cleanup · Sync Gallery · Backup — each runs with a dry-run
          preview before touching any files.
        </Text>
      </Stack>

      {/* Live progress panel (while a job runs) */}
      <AnimatePresence>
        {isJobRunning && (
          <JobProgressPanel
            op={activeOp}
            opLabel={activeOp !== null ? (OP_LABELS[activeOp] ?? activeOp) : ''}
            state={eventsState}
          />
        )}
      </AnimatePresence>

      {/* Job has just completed — keep result panel until next op */}
      <AnimatePresence>
        {!isJobRunning && eventsState.isDone && (
          <JobProgressPanel
            op={activeOp}
            opLabel={activeOp !== null ? (OP_LABELS[activeOp] ?? activeOp) : 'Last operation'}
            state={eventsState}
          />
        )}
      </AnimatePresence>

      {/* Operation cards */}
      <div
        style={{
          display: 'grid',
          gridTemplateColumns: 'repeat(auto-fill, minmax(220px, 1fr))',
          gap: 12,
        }}
      >
        {OP_DEFS.map((def) => (
          <OperationCard
            key={def.id}
            def={def}
            isDisabled={isJobRunning}
          />
        ))}
      </div>
    </Stack>
  )
}
