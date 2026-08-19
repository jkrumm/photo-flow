import { useState, useEffect } from 'react'
import { Button, Checkbox, Group, Modal, Stack, Table, Text } from '@mantine/core'
import { IconAlertTriangle, IconTrash } from '@tabler/icons-react'
import { VX, alpha } from 'basalt-ui/tokens'
import type { ActiveOp } from '../../lib/store'
import { FIELD_LABELS } from '../../lib/op-metadata'

// ── Preview field classification ──────────────────────────────────────────────

const SKIP_FIELDS = new Set(['remote_path', 'path'])

/**
 * Fields whose non-zero value represents a permanent, irreversible local file
 * deletion. These get a distinct visual treatment (red/warn + trash icon) and
 * require an explicit confirmation gesture before the Confirm button enables.
 */
const PERMANENT_DELETE_FIELDS = new Set([
  'orphaned_raws',       // finalize: orphaned RAWs identified for deletion
  'deleted_raws',        // finalize: local RAWs deleted
  'deleted_camera_raws', // finalize: camera RAWs deleted
  'deleted',             // cleanup: RAWs deleted
  'orphaned',            // cleanup: RAWs queued for deletion (dry-run count)
])

function hasPermanentDeletions(preview: Record<string, unknown>): boolean {
  for (const f of PERMANENT_DELETE_FIELDS) {
    const val = preview[f]
    if (typeof val === 'number' && val > 0) return true
  }
  return false
}

// ── Preview formatting ────────────────────────────────────────────────────────

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
          Would move <strong>{moved}</strong> photo{moved !== 1 ? 's' : ''}{edits > 0 ? ` + ${edits} sidecars` : ''} from
          Staging to Final.{' '}
          {orphaned > 0 && (
            <span style={{ color: VX.warnSolid, fontWeight: 600 }}>
              Would permanently delete {orphaned} orphaned RAW{orphaned !== 1 ? 's' : ''}.
            </span>
          )}
        </Text>
      )
    }
    case 'cleanup': {
      const count = Number(p['orphaned'] ?? 0)
      return (
        <Text size="sm">
          Would permanently delete <strong style={{ color: VX.warnSolid }}>{count}</strong> orphaned
          RAW{count !== 1 ? 's' : ''}. This cannot be undone.
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

// ── Preview table row ─────────────────────────────────────────────────────────

function PreviewRow({ field, value }: { field: string; value: unknown }) {
  const isDeletion = PERMANENT_DELETE_FIELDS.has(field)
  const numVal = typeof value === 'number' ? value : null
  const isSignificantDeletion = isDeletion && numVal !== null && numVal > 0

  return (
    <Table.Tr>
      <Table.Td style={{ color: isSignificantDeletion ? VX.warnSolid : undefined, width: '55%' }}>
        <Group gap={5} align="center" wrap="nowrap" {...(isSignificantDeletion ? {} : { c: 'dimmed' })}>
          {isSignificantDeletion && (
            <IconTrash size={12} style={{ flexShrink: 0, color: VX.warnSolid }} />
          )}
          {FIELD_LABELS[field] ?? field}
        </Group>
      </Table.Td>
      <Table.Td
        style={{
          fontVariantNumeric: 'tabular-nums',
          fontWeight: isSignificantDeletion ? 700 : 500,
          color: isSignificantDeletion ? VX.warnSolid : undefined,
        }}
      >
        {isSignificantDeletion ? `−${numVal}` : formatValue(value)}
      </Table.Td>
    </Table.Tr>
  )
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
  const [confirmChecked, setConfirmChecked] = useState(false)

  // Reset the confirmation checkbox each time the modal opens.
  useEffect(() => {
    if (opened) setConfirmChecked(false)
  }, [opened])

  // Require an explicit checkbox only when the preview contains permanent deletions
  // (orphaned RAWs, camera RAWs, etc.). Zero-deletion operations confirm in one click.
  const needsConfirmGesture =
    isDestructive && preview !== null && hasPermanentDeletions(preview)
  const confirmEnabled = !needsConfirmGesture || confirmChecked

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
            px={12}
            py={8}
            style={{
              background: alpha(VX.warnSolid, 0.1),
              outline: `1px solid ${alpha(VX.warnSolid, 0.3)}`,
              borderRadius: VX.radiusCtrl,
            }}
          >
            <IconAlertTriangle
              size={16}
              style={{ color: VX.warnSolid, flexShrink: 0 }}
            />
            <Text size="xs" style={{ color: VX.warnSolid }}>
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
                  <PreviewRow key={k} field={k} value={v} />
                ))}
            </Table.Tbody>
          </Table>
        )}

        {needsConfirmGesture && (
          <Checkbox
            label="I understand these files will be permanently deleted"
            checked={confirmChecked}
            onChange={(e) => setConfirmChecked(e.currentTarget.checked)}
            color="orange"
            size="sm"
          />
        )}

        <Group justify="flex-end" gap="sm" mt="xs">
          <Button variant="subtle" color="gray" onClick={onClose} disabled={isConfirming}>
            Cancel
          </Button>
          <Button
            color={isDestructive ? 'orange' : 'blue'}
            onClick={onConfirm}
            loading={isConfirming}
            disabled={!confirmEnabled}
          >
            {isDestructive ? 'Confirm — delete files' : 'Confirm'}
          </Button>
        </Group>
      </Stack>
    </Modal>
  )
}
