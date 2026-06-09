import { Group, Text, Tooltip } from '@mantine/core'
import { useQuery } from '@tanstack/react-query'
import { statusQueries } from '../../lib/queries/status'

function Dot({ connected, label }: { connected: boolean; label: string }) {
  return (
    <Tooltip label={`${label}: ${connected ? 'connected' : 'not found'}`} withArrow>
      <div
        style={{
          width: 8,
          height: 8,
          borderRadius: '50%',
          background: connected ? 'var(--vx-goodSolid)' : 'var(--mantine-color-dimmed)',
          flexShrink: 0,
        }}
        aria-label={`${label} ${connected ? 'connected' : 'disconnected'}`}
      />
    </Tooltip>
  )
}

/**
 * Compact status indicator — polls GET /status every 3s and shows:
 * - Camera connected dot
 * - SSD connected dot
 * - Staging file count (when > 0)
 */
export function StatusIndicator() {
  const { data } = useQuery(statusQueries.status())

  if (!data) return null

  return (
    <Group gap="xs" wrap="nowrap">
      <Dot connected={data.camera_connected} label="Camera" />
      <Dot connected={data.ssd_connected} label="SSD" />
      {data.staging_files > 0 && (
        <Text size="xs" c="dimmed" style={{ fontVariantNumeric: 'tabular-nums' }}>
          {data.staging_files} staging
        </Text>
      )}
    </Group>
  )
}
