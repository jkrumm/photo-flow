import { createFileRoute } from '@tanstack/react-router'
import { Stack, Text, Title } from '@mantine/core'
import { IconHeartbeat } from '@tabler/icons-react'

export const Route = createFileRoute('/library')({
  component: LibraryPage,
})

function LibraryPage() {
  return (
    <Stack gap="md">
      <Stack gap={4}>
        <Title order={2} style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
          <IconHeartbeat size={24} />
          Library Health
        </Title>
        <Text c="dimmed" size="sm">
          Orphaned RAWs · Backup freshness · Last sync timestamps
        </Text>
      </Stack>
      <Text c="dimmed" size="sm">
        Library health dashboard coming in Group 12.
      </Text>
    </Stack>
  )
}
