/**
 * Trash drawer — the undo surface behind culling.
 *
 * A culled photo is moved, never deleted, so this drawer is the only place the move
 * is visible and reversible. Purge is the one genuinely destructive action here and
 * is therefore both confirm-gated and limited to entries the server already considers
 * past retention (`purgeable`); it never offers to empty the whole trash.
 */
import { useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import {
  ActionIcon,
  Badge,
  Box,
  Button,
  Drawer,
  Flex,
  Group,
  Loader,
  ScrollArea,
  Stack,
  Text,
  Tooltip,
} from '@mantine/core'
import { modals } from '@mantine/modals'
import { IconArrowBackUp, IconPhotoOff, IconTrash } from '@tabler/icons-react'
import { notifyError, notifySuccess } from 'basalt-ui/notifications'
import { VX, alpha } from 'basalt-ui/tokens'
import { formatBytes } from '../../lib/format'
import { thumbUrl, type TrashEntry } from '../../lib/photos'
import { photosApi, photosQueries } from '../../lib/queries/photos'
import { StarRating } from './star-rating'

const THUMB_SIZE = 52

/** Thumbnail of a trashed file; falls back to a glyph when the source is gone. */
function TrashThumb({ entry }: { entry: TrashEntry }) {
  const [broken, setBroken] = useState(false)

  if (broken || !entry.exists) {
    return (
      <Flex
        align="center"
        justify="center"
        w={THUMB_SIZE}
        h={THUMB_SIZE}
        style={{
          flexShrink: 0,
          borderRadius: VX.radiusCtrl,
          background: alpha(VX.neutral, 0.08),
        }}
      >
        <IconPhotoOff size={16} style={{ color: alpha(VX.neutral, 0.4) }} />
      </Flex>
    )
  }

  return (
    <Box
      w={THUMB_SIZE}
      h={THUMB_SIZE}
      style={{ flexShrink: 0, borderRadius: VX.radiusCtrl, overflow: 'hidden' }}
    >
      <img
        src={thumbUrl({ path: entry.trashed_path, mtime: 0 }, 'grid')}
        alt=""
        width={THUMB_SIZE}
        height={THUMB_SIZE}
        loading="lazy"
        decoding="async"
        onError={() => setBroken(true)}
        style={{ width: '100%', height: '100%', objectFit: 'cover', display: 'block' }}
      />
    </Box>
  )
}

function ageLabel(days: number): string {
  if (days < 1) return 'today'
  if (days < 2) return 'yesterday'
  return `${Math.floor(days)} days ago`
}

export type PhotoTrashDrawerProps = {
  opened: boolean
  onClose: () => void
}

/**
 * Drawer listing trashed photos with per-entry restore and a retention purge.
 *
 * @param opened Whether the drawer is visible.
 * @param onClose Fired on escape / overlay click / close button.
 */
export function PhotoTrashDrawer({ opened, onClose }: PhotoTrashDrawerProps) {
  const queryClient = useQueryClient()
  const trash = useQuery({ ...photosQueries.trash(), enabled: opened })

  const invalidate = (): void => {
    void queryClient.invalidateQueries({ queryKey: ['photos'] })
  }

  const restore = useMutation({
    mutationFn: (ids: number[]) => photosApi.restore(ids),
    onSuccess: (result) => {
      invalidate()
      if (result.errors > 0) {
        notifyError(result.messages.join(' · ') || 'Some entries could not be restored.', {
          title: 'Restore incomplete',
        })
        return
      }
      notifySuccess(`${result.restored} photo${result.restored === 1 ? '' : 's'} restored.`)
    },
    onError: (error: Error) => notifyError(error.message, { title: 'Restore failed' }),
  })

  const purge = useMutation({
    mutationFn: () => photosApi.purge(),
    onSuccess: (result) => {
      invalidate()
      notifySuccess(`${result.purged} entries purged · ${formatBytes(result.bytes)} reclaimed.`)
    },
    onError: (error: Error) => notifyError(error.message, { title: 'Purge failed' }),
  })

  const stats = trash.data?.stats
  const entries = trash.data?.entries ?? []
  const purgeable = stats?.purgeable ?? 0

  const confirmPurge = (): void => {
    modals.openConfirmModal({
      title: 'Purge trash',
      children: (
        <Text size="sm">
          Permanently delete {purgeable} entr{purgeable === 1 ? 'y' : 'ies'} past the retention
          window. This cannot be undone — the files and their .photo-edit sidecars are gone for
          good.
        </Text>
      ),
      labels: { confirm: 'Purge', cancel: 'Keep' },
      confirmProps: { color: 'red' },
      onConfirm: () => purge.mutate(),
    })
  }

  return (
    <Drawer opened={opened} onClose={onClose} position="right" size="md" title="Trash">
      <Stack gap="sm" h="100%">
        <Group justify="space-between" align="center" wrap="nowrap">
          <Group gap={8} wrap="nowrap">
            <Badge size="sm" variant="light">
              {stats?.count ?? 0}
            </Badge>
            <Text size="xs" c="dimmed" ff="monospace">
              {formatBytes(stats?.bytes ?? 0)}
            </Text>
          </Group>
          <Tooltip
            label={
              purgeable > 0
                ? `${purgeable} past retention`
                : 'Nothing is past the retention window yet'
            }
            withArrow
          >
            <Button
              size="compact-xs"
              variant="default"
              color="red"
              leftSection={<IconTrash size={13} />}
              disabled={purgeable === 0}
              loading={purge.isPending}
              onClick={confirmPurge}
            >
              Purge {purgeable > 0 ? purgeable : ''}
            </Button>
          </Tooltip>
        </Group>

        {trash.isPending && (
          <Flex justify="center" py="sm">
            <Loader size="sm" />
          </Flex>
        )}

        {!trash.isPending && entries.length === 0 && (
          <Text size="xs" c="dimmed">
            Trash is empty.
          </Text>
        )}

        <ScrollArea type="hover" scrollbars="y" scrollbarSize={9} style={{ flex: 1, minHeight: 0 }}>
          <Stack gap={8}>
            {entries.map((entry) => (
              <Group key={entry.id} gap="xs" align="center" wrap="nowrap">
                <TrashThumb entry={entry} />
                <Stack gap={2} style={{ flex: 1, minWidth: 0 }}>
                  <Text size="xs" fw={600} truncate="end">
                    {entry.filename}
                  </Text>
                  <Group gap={8} wrap="nowrap">
                    <Text size="xs" c="dimmed">
                      {ageLabel(entry.age_days)}
                    </Text>
                    <Text size="xs" c="dimmed" ff="monospace">
                      {formatBytes(entry.size)}
                    </Text>
                    {entry.purgeable && (
                      <Badge size="xs" variant="light" color="orange">
                        past retention
                      </Badge>
                    )}
                  </Group>
                  <StarRating value={entry.rating ?? 0} size={10} readOnly />
                </Stack>
                <Tooltip label="Restore to its original folder" withArrow>
                  <ActionIcon
                    variant="default"
                    aria-label={`Restore ${entry.filename}`}
                    loading={restore.isPending && restore.variables?.[0] === entry.id}
                    onClick={() => restore.mutate([entry.id])}
                  >
                    <IconArrowBackUp size={15} />
                  </ActionIcon>
                </Tooltip>
              </Group>
            ))}
          </Stack>
        </ScrollArea>
      </Stack>
    </Drawer>
  )
}
