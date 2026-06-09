import { Button, Group, Modal, Stack, Table, Text } from '@mantine/core'
import { IconAlertTriangle } from '@tabler/icons-react'
import type { ActiveOp } from '../../lib/store'

// ── Preview formatting ────────────────────────────────────────────────────────

const FIELD_LABELS: Record<string, string> = {
  photos: 'Photos',
  raws: 'RAWs',
  videos: 'Videos',
  skipped: 'Skipped (duplicates)',
  errors: 'Errors',
  moved: 'Photos to move',
  edits_moved: 'Sidecars to move',
  orphaned_raws: 'Orphaned RAWs',
  deleted_raws: 'RAWs to delete',
  deleted_camera_raws: 'Camera RAWs to delete',
  orphaned: 'Orphaned RAWs',
  deleted: 'To delete',
  scanned: 'Files scanned',
  synced: 'Photos to sync',
  removed: 'Photos to remove',
  unchanged: 'Unchanged',
  total_in_gallery: 'Total in gallery',
  json_updated: 'Metadata JSON updated',
  build_successful: 'Build successful',
  sync_successful: 'Sync successful',
  source: 'Source',
  sources: 'Sources',
  total_scanned: 'Total files scanned',
  all_successful: 'All successful',
  connection_method: 'Connection',
  immich_scan_triggered: 'Immich scan triggered',
  trash_path: 'Trash path',
}

const SKIP_FIELDS = new Set(['remote_path', 'path'])

function formatValue(v: unknown): string {
  if (v === null || v === undefined) return '—'
  if (typeof v === 'boolean') return v ? 'Yes' : 'No'
  if (Array.isArray(v)) return v.join(', ')
  return String(v)
}

function PreviewSummary({ opId, preview }: { opId: ActiveOp; preview: Record<string, unknown> }) {
  const p = preview
  switch (opId) {
    case 'import': {
      const photos = Number(p['photos'] ?? 0)
      const raws = Number(p['raws'] ?? 0)
      const videos = Number(p['videos'] ?? 0)
      const skipped = Number(p['skipped'] ?? 0)
      return (
        <Text size="sm">
          Would import <strong>{photos}</strong> photos, <strong>{raws}</strong> RAWs,{' '}
          <strong>{videos}</strong> videos.{' '}
          {skipped > 0 && <>{skipped} duplicates will be skipped.</>}
        </Text>
      )
    }
    case 'finalize': {
      const moved = Number(p['moved'] ?? 0)
      const edits = Number(p['edits_moved'] ?? 0)
      const orphaned = Number(p['orphaned_raws'] ?? 0)
      return (
        <Text size="sm">
          Would move <strong>{moved}</strong> photos{edits > 0 ? ` + ${edits} sidecars` : ''} from
          Staging to Final.{' '}
          {orphaned > 0 && (
            <>
              Would delete <strong>{orphaned}</strong> orphaned RAWs.
            </>
          )}
        </Text>
      )
    }
    case 'cleanup': {
      const count = Number(p['orphaned'] ?? 0)
      return (
        <Text size="sm">
          Would permanently delete <strong>{count}</strong> orphaned RAW{count !== 1 ? 's' : ''}.
          This cannot be undone.
        </Text>
      )
    }
    case 'sync-gallery': {
      const synced = Number(p['synced'] ?? 0)
      const removed = Number(p['removed'] ?? 0)
      const unchanged = Number(p['unchanged'] ?? 0)
      return (
        <Text size="sm">
          Would sync <strong>{synced}</strong> photos to gallery, remove{' '}
          <strong>{removed}</strong>. {unchanged} already up to date.
        </Text>
      )
    }
    case 'backup': {
      if ('total_scanned' in p) {
        const total = Number(p['total_scanned'] ?? 0)
        const sources = Array.isArray(p['sources'])
          ? (p['sources'] as unknown[]).join(' + ')
          : 'all'
        return (
          <Text size="sm">
            Would backup <strong>{total}</strong> files ({sources}) to homelab.
          </Text>
        )
      }
      const scanned = Number(p['scanned'] ?? 0)
      const source = String(p['source'] ?? 'final')
      return (
        <Text size="sm">
          Would backup <strong>{scanned}</strong> files ({source}) to homelab.
        </Text>
      )
    }
    default:
      return <Text size="sm">Review the details below before confirming.</Text>
  }
}

// ── Modal ─────────────────────────────────────────────────────────────────────

export type DryRunModalProps = {
  opened: boolean
  onClose: () => void
  onConfirm: () => void
  isConfirming: boolean
  opId: ActiveOp
  opLabel: string
  isDestructive: boolean
  preview: Record<string, unknown> | null
}

export function DryRunModal({
  opened,
  onClose,
  onConfirm,
  isConfirming,
  opId,
  opLabel,
  isDestructive,
  preview,
}: DryRunModalProps) {
  return (
    <Modal
      opened={opened}
      onClose={onClose}
      title={`Confirm: ${opLabel}`}
      size="md"
      centered
    >
      <Stack gap="md">
        {preview !== null && <PreviewSummary opId={opId} preview={preview} />}

        {isDestructive && (
          <Group
            gap="xs"
            style={{
              background: 'color-mix(in srgb, var(--vx-warn) 10%, transparent)',
              border: '1px solid color-mix(in srgb, var(--vx-warn) 30%, transparent)',
              borderRadius: 6,
              padding: '8px 12px',
            }}
          >
            <IconAlertTriangle
              size={16}
              style={{ color: 'var(--vx-warnSolid)', flexShrink: 0 }}
            />
            <Text size="xs" style={{ color: 'var(--vx-warnSolid)' }}>
              This operation permanently deletes files. It cannot be undone.
            </Text>
          </Group>
        )}

        {preview !== null && (
          <Table
            fz="xs"
            withTableBorder={false}
            withColumnBorders={false}
            withRowBorders={true}
            style={{ tableLayout: 'fixed' }}
          >
            <Table.Tbody>
              {Object.entries(preview)
                .filter(([k]) => !SKIP_FIELDS.has(k))
                .map(([k, v]) => (
                  <Table.Tr key={k}>
                    <Table.Td style={{ color: 'var(--mantine-color-dimmed)', width: '55%' }}>
                      {FIELD_LABELS[k] ?? k}
                    </Table.Td>
                    <Table.Td style={{ fontVariantNumeric: 'tabular-nums', fontWeight: 500 }}>
                      {formatValue(v)}
                    </Table.Td>
                  </Table.Tr>
                ))}
            </Table.Tbody>
          </Table>
        )}

        <Group justify="flex-end" gap="sm" mt="xs">
          <Button variant="subtle" color="gray" onClick={onClose} disabled={isConfirming}>
            Cancel
          </Button>
          <Button
            color={isDestructive ? 'orange' : 'blue'}
            onClick={onConfirm}
            loading={isConfirming}
          >
            {isDestructive ? 'Confirm — delete files' : 'Confirm'}
          </Button>
        </Group>
      </Stack>
    </Modal>
  )
}
