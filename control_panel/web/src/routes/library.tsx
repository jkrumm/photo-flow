import { createFileRoute, useNavigate } from '@tanstack/react-router'
import { Badge, Button, Card, Group, SimpleGrid, Skeleton, Stack, Text, Title } from '@mantine/core'
import { IconHeartbeat, IconRefresh, IconAlertTriangle, IconCheck } from '@tabler/icons-react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { useState } from 'react'
import { api } from '../lib/api'
import { analyticsQueries } from '../lib/queries/analytics'
import { backupQueries } from '../lib/queries/backup'
import { VX, alpha } from '../lib/charts'
import { formatBytes } from '../lib/format'
import type { BackupSourceInfo, RefreshResponse } from '../lib/api-types'

export const Route = createFileRoute('/library')({
  component: LibraryPage,
})

// ── Index refresh ─────────────────────────────────────────────────────────────

function IndexRefreshCard() {
  const queryClient = useQueryClient()
  const [lastResult, setLastResult] = useState<RefreshResponse | null>(null)

  const { mutate, isPending, isError, error } = useMutation({
    mutationFn: () => api.post<RefreshResponse>('/index/refresh'),
    onSuccess: (result) => {
      setLastResult(result)
      void queryClient.invalidateQueries({ queryKey: ['analytics'] })
    },
  })

  const errMsg = isError && error instanceof Error ? error.message : null

  return (
    <Card withBorder padding="md" style={{ borderColor: VX.surface.border }}>
      <Stack gap="sm">
        <Group justify="space-between" align="flex-start">
          <Stack gap={2}>
            <Text fw={500} size="sm">
              Metadata Index
            </Text>
            <Text size="xs" c="dimmed">
              Incrementally reindexes Final photos — only changed files are re-read.
            </Text>
          </Stack>
          <Button
            size="xs"
            leftSection={<IconRefresh size={14} />}
            loading={isPending}
            onClick={() => mutate()}
            style={{ backgroundColor: VX.photo.final, color: VX.surface.bg }}
          >
            Refresh
          </Button>
        </Group>

        {lastResult && (
          <Card
            padding="xs"
            style={{
              backgroundColor: alpha(VX.good, 0.08),
              border: `1px solid ${alpha(VX.good, 0.25)}`,
              borderRadius: 6,
            }}
          >
            <Group gap="md">
              <IconCheck size={14} style={{ color: VX.good }} />
              <Text size="xs" style={{ color: VX.good }}>
                Indexed {lastResult.indexed} · Updated {lastResult.updated} · Removed{' '}
                {lastResult.removed} · Skipped {lastResult.skipped}
              </Text>
            </Group>
          </Card>
        )}

        {errMsg && (
          <Text size="xs" style={{ color: VX.bad }}>
            {errMsg.includes('409') ? 'Refresh already running — try again shortly.' : errMsg}
          </Text>
        )}
      </Stack>
    </Card>
  )
}

// ── Orphaned RAWs ─────────────────────────────────────────────────────────────

function OrphanedRawsCard() {
  const navigate = useNavigate()
  const { data, isLoading } = useQuery(analyticsQueries.libraryHealth())

  if (isLoading) return <Skeleton h={100} radius="md" />

  const orphaned = data?.orphaned_raws ?? null
  const available = data?.raws_available ?? false

  const isClean = orphaned === 0
  const badgeColor = isClean ? VX.good : VX.warn
  const badgeLabel = isClean ? 'Clean' : `${orphaned} orphaned`

  return (
    <Card withBorder padding="md" style={{ borderColor: VX.surface.border }}>
      <Stack gap="sm">
        <Group justify="space-between" align="center">
          <Stack gap={2}>
            <Text fw={500} size="sm">
              Orphaned RAWs
            </Text>
            <Text size="xs" c="dimmed">
              RAF files in the RAWs folder with no matching Final JPG.
            </Text>
          </Stack>
          {!available ? (
            <Badge style={{ backgroundColor: alpha(VX.neutral, 0.2), color: VX.neutral }}>
              Drive not mounted
            </Badge>
          ) : orphaned === null ? (
            <Badge style={{ backgroundColor: alpha(VX.neutral, 0.2), color: VX.neutral }}>
              Unknown
            </Badge>
          ) : (
            <Badge style={{ backgroundColor: alpha(badgeColor, 0.15), color: badgeColor }}>
              {badgeLabel}
            </Badge>
          )}
        </Group>

        {data && available && (
          <Group gap="xl">
            <Stack gap={0}>
              <Text size="xs" c="dimmed">
                Final
              </Text>
              <Text size="sm" fw={500}>
                {data.final_count.toLocaleString()} photos
              </Text>
            </Stack>
            <Stack gap={0}>
              <Text size="xs" c="dimmed">
                RAWs
              </Text>
              <Text size="sm" fw={500}>
                {data.raws_count.toLocaleString()} files
              </Text>
            </Stack>
          </Group>
        )}

        {orphaned !== null && orphaned > 0 && (
          <Group gap="sm">
            <IconAlertTriangle size={14} style={{ color: VX.warn }} />
            <Text size="xs" style={{ color: VX.warn }}>
              {orphaned} orphaned RAW{orphaned !== 1 ? 's' : ''} have no matching Final JPG.
            </Text>
            <Button
              size="xs"
              variant="subtle"
              style={{ color: VX.photo.published, padding: '2px 6px' }}
              onClick={() => void navigate({ to: '/pipeline', search: { action: 'cleanup' } })}
            >
              Run Cleanup →
            </Button>
          </Group>
        )}
      </Stack>
    </Card>
  )
}

// ── Backup freshness ──────────────────────────────────────────────────────────

function BackupSourceCard({
  label,
  info,
  color,
}: {
  label: string
  info: BackupSourceInfo
  color: string
}) {
  const needsSync = info.needs_sync
  const isFresh = info.remote_count !== null && needsSync !== null && needsSync === 0
  const isStale = info.remote_count !== null && needsSync !== null && needsSync > 0
  const isUnknown = info.remote_count === null

  const statusColor = isFresh ? VX.good : isStale ? VX.warn : VX.neutral
  const statusLabel = isFresh ? 'Synced' : isStale ? `${needsSync} behind` : 'No data'

  return (
    <Card withBorder padding="md" style={{ borderColor: VX.surface.border }}>
      <Stack gap="sm">
        <Group justify="space-between" align="center">
          <Text fw={500} size="sm" style={{ color }}>
            {label}
          </Text>
          <Badge style={{ backgroundColor: alpha(statusColor, 0.15), color: statusColor }}>
            {statusLabel}
          </Badge>
        </Group>

        {!info.available && (
          <Text size="xs" style={{ color: VX.bad }}>
            Drive not mounted
          </Text>
        )}

        {info.available && (
          <Group gap="xl">
            <Stack gap={0}>
              <Text size="xs" c="dimmed">
                Local
              </Text>
              <Text size="sm" fw={500}>
                {info.local_count.toLocaleString()}
              </Text>
            </Stack>
            {!isUnknown && (
              <Stack gap={0}>
                <Text size="xs" c="dimmed">
                  Remote
                </Text>
                <Text size="sm" fw={500}>
                  {(info.remote_count ?? 0).toLocaleString()}
                </Text>
              </Stack>
            )}
            {isUnknown && (
              <Stack gap={0}>
                <Text size="xs" c="dimmed">
                  Remote
                </Text>
                <Text size="sm" c="dimmed">
                  Unavailable
                </Text>
              </Stack>
            )}
          </Group>
        )}

        {info.requires && (
          <Text size="xs" c="dimmed">
            Requires: {info.requires}
          </Text>
        )}
      </Stack>
    </Card>
  )
}

function BackupSection() {
  const navigate = useNavigate()
  const { data, isLoading } = useQuery(backupQueries.availabilityRemote())

  if (isLoading) {
    return (
      <SimpleGrid cols={{ base: 1, sm: 3 }}>
        {[0, 1, 2].map((i) => (
          <Skeleton key={i} h={120} radius="md" />
        ))}
      </SimpleGrid>
    )
  }
  if (!data) return null

  const connectionOk = data.connection !== null

  return (
    <Stack gap="sm">
      <Group gap="sm" align="center">
        <Text fw={500} size="sm">
          Backup Status
        </Text>
        <Badge
          style={{
            backgroundColor: alpha(connectionOk ? VX.good : VX.neutral, 0.15),
            color: connectionOk ? VX.good : VX.neutral,
          }}
        >
          {connectionOk ? `Connected via ${data.connection}` : 'Not connected'}
        </Badge>
        <Button
          size="xs"
          variant="subtle"
          ml="auto"
          style={{ color: VX.photo.final }}
          onClick={() => void navigate({ to: '/pipeline', search: { action: 'backup' } })}
        >
          Run Backup →
        </Button>
        <Button
          size="xs"
          variant="subtle"
          style={{ color: VX.photo.published }}
          onClick={() => void navigate({ to: '/pipeline', search: { action: 'sync' } })}
        >
          Sync Gallery →
        </Button>
      </Group>

      <SimpleGrid cols={{ base: 1, sm: 3 }}>
        <BackupSourceCard label="Final" info={data.final} color={VX.photo.final} />
        <BackupSourceCard label="RAWs" info={data.raws} color={VX.photo.raws} />
        <BackupSourceCard label="Videos" info={data.videos} color={VX.photo.videos} />
      </SimpleGrid>

      {!connectionOk && (
        <Group gap="sm">
          <IconAlertTriangle size={14} style={{ color: VX.neutral }} />
          <Text size="xs" c="dimmed">
            Remote counts require a Tailscale connection to homelab.
          </Text>
        </Group>
      )}
    </Stack>
  )
}

// ── Storage overview ──────────────────────────────────────────────────────────

function StorageOverviewCard() {
  const { data, isLoading } = useQuery(analyticsQueries.storage())

  if (isLoading) return <Skeleton h={80} radius="md" />
  if (!data) return null

  const totalBytes = data.final.bytes + data.staging.bytes + data.raws.bytes + data.videos.bytes
  const totalFiles =
    data.final.count + data.staging.count + data.raws.count + data.videos.count

  return (
    <Card withBorder padding="md" style={{ borderColor: VX.surface.border }}>
      <Stack gap={4}>
        <Text fw={500} size="sm">
          Total Local Storage
        </Text>
        <Group gap="xl">
          <Stack gap={0}>
            <Text size="xs" c="dimmed">
              Files
            </Text>
            <Text size="sm" fw={500}>
              {totalFiles.toLocaleString()}
            </Text>
          </Stack>
          <Stack gap={0}>
            <Text size="xs" c="dimmed">
              Size
            </Text>
            <Text size="sm" fw={500}>
              {formatBytes(totalBytes)}
            </Text>
          </Stack>
          <Stack gap={0}>
            <Text size="xs" c="dimmed">
              Final
            </Text>
            <Text size="sm" fw={500} style={{ color: VX.photo.final }}>
              {formatBytes(data.final.bytes)}
            </Text>
          </Stack>
          <Stack gap={0}>
            <Text size="xs" c="dimmed">
              RAWs
            </Text>
            <Text
              size="sm"
              fw={500}
              style={{ color: data.raws.available ? VX.photo.raws : VX.neutral }}
            >
              {data.raws.available ? formatBytes(data.raws.bytes) : '—'}
            </Text>
          </Stack>
        </Group>
      </Stack>
    </Card>
  )
}

// ── Page ──────────────────────────────────────────────────────────────────────

function LibraryPage() {
  return (
    <Stack gap="md">
      <Stack gap={4}>
        <Title order={2} style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
          <IconHeartbeat size={24} />
          Library Health
        </Title>
        <Text c="dimmed" size="sm">
          Orphaned RAWs · Backup freshness · Index maintenance
        </Text>
      </Stack>

      <IndexRefreshCard />
      <OrphanedRawsCard />
      <BackupSection />
      <StorageOverviewCard />
    </Stack>
  )
}
