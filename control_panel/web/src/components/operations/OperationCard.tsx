import { useState } from 'react'
import { useMutation } from '@tanstack/react-query'
import { notifications } from '@mantine/notifications'
import { Badge, Button, Card, Group, SegmentedControl, Stack, Text } from '@mantine/core'
import { VX } from '../../lib/charts/tokens'
import { alpha } from '../../lib/charts/utils/color'
import { opsApi } from '../../lib/queries/ops'
import { useActiveJobStore, type ActiveOp } from '../../lib/store'
import type { BackupSource } from '../../lib/api-types'
import { DryRunModal } from './DryRunModal'

// ── Op card definition ────────────────────────────────────────────────────────

export type OpCardDef = {
  id: ActiveOp
  label: string
  shortDesc: string
  color: string
  isDestructive: boolean
}

// ── Backup source control ─────────────────────────────────────────────────────

const BACKUP_SOURCES: { value: BackupSource; label: string }[] = [
  { value: 'all', label: 'All' },
  { value: 'final', label: 'Final' },
  { value: 'raws', label: 'RAWs' },
  { value: 'videos', label: 'Videos' },
]

// ── OperationCard ─────────────────────────────────────────────────────────────

export type OperationCardProps = {
  def: OpCardDef
  isDisabled: boolean
}

export function OperationCard({ def, isDisabled }: OperationCardProps) {
  const [modalOpen, setModalOpen] = useState(false)
  const [preview, setPreview] = useState<Record<string, unknown> | null>(null)
  const [backupSource, setBackupSource] = useState<BackupSource>('all')

  const setActiveJob = useActiveJobStore((s) => s.setActiveJob)

  const extra =
    def.id === 'backup' ? opsApi.backupParams(backupSource) : undefined

  const dryRunMutation = useMutation({
    mutationFn: () => opsApi.dryRun(def.id, extra),
    onSuccess: (data) => {
      setPreview(data)
      setModalOpen(true)
    },
    onError: (err: Error) => {
      notifications.show({
        title: `${def.label} preview failed`,
        message: err.message,
        color: 'red',
        autoClose: 6000,
      })
    },
  })

  const startMutation = useMutation({
    mutationFn: () => opsApi.start(def.id, extra),
    onSuccess: (data) => {
      setModalOpen(false)
      setActiveJob(data.job_id, def.id)
    },
    onError: (err: Error) => {
      setModalOpen(false)
      const is409 = err.message.includes('409')
      notifications.show({
        title: is409 ? 'Job already running' : `${def.label} failed to start`,
        message: is409
          ? 'Wait for the current job to complete before starting a new one.'
          : err.message,
        color: 'red',
        autoClose: 6000,
      })
    },
  })

  const isLoading = dryRunMutation.isPending || startMutation.isPending
  const effectivelyDisabled = isDisabled || isLoading

  return (
    <>
      <Card
        style={{
          border: `1px solid ${VX.surface.border}`,
          background: VX.surface.panel,
          borderRadius: 10,
          opacity: effectivelyDisabled ? 0.6 : 1,
          transition: 'opacity 0.2s',
          position: 'relative',
          overflow: 'hidden',
        }}
        p="md"
      >
        {/* colored top accent bar */}
        <div
          aria-hidden="true"
          style={{
            position: 'absolute',
            top: 0,
            left: 0,
            right: 0,
            height: 3,
            background: def.color,
            borderRadius: '10px 10px 0 0',
            opacity: effectivelyDisabled ? 0.4 : 0.8,
          }}
        />

        <Stack gap="sm" mt={6}>
          {/* header */}
          <Group justify="space-between" align="flex-start" gap="xs">
            <Stack gap={2}>
              <Text
                fw={600}
                size="sm"
                style={{ color: effectivelyDisabled ? alpha(VX.neutral, 0.5) : VX.neutral }}
              >
                {def.label}
              </Text>
              <Text size="xs" c="dimmed" style={{ lineHeight: 1.35 }}>
                {def.shortDesc}
              </Text>
            </Stack>
            {def.isDestructive && (
              <Badge color="orange" variant="light" size="xs" style={{ flexShrink: 0 }}>
                destructive
              </Badge>
            )}
          </Group>

          {/* backup source selector */}
          {def.id === 'backup' && (
            <SegmentedControl
              value={backupSource}
              onChange={(v) => setBackupSource(v as BackupSource)}
              data={BACKUP_SOURCES}
              size="xs"
              disabled={effectivelyDisabled}
              fullWidth
            />
          )}

          {/* action button */}
          <Button
            variant="light"
            color={def.isDestructive ? 'orange' : 'blue'}
            size="xs"
            onClick={() => dryRunMutation.mutate()}
            loading={dryRunMutation.isPending}
            disabled={effectivelyDisabled && !dryRunMutation.isPending}
            fullWidth
          >
            {isDisabled && !isLoading ? 'Job running…' : 'Run'}
          </Button>
        </Stack>
      </Card>

      <DryRunModal
        opened={modalOpen}
        onClose={() => setModalOpen(false)}
        onConfirm={() => startMutation.mutate()}
        isConfirming={startMutation.isPending}
        opId={def.id}
        opLabel={def.label}
        isDestructive={def.isDestructive}
        preview={preview}
      />
    </>
  )
}
