import { createFileRoute } from '@tanstack/react-router'
import { Stack, Text, Title } from '@mantine/core'
import { IconPlayerPlay } from '@tabler/icons-react'

export const Route = createFileRoute('/operations')({
  component: OperationsPage,
})

function OperationsPage() {
  return (
    <Stack gap="md">
      <Stack gap={4}>
        <Title order={2} style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
          <IconPlayerPlay size={24} />
          Operations
        </Title>
        <Text c="dimmed" size="sm">
          Import · Finalize · Cleanup · Sync Gallery · Backup
        </Text>
      </Stack>
      <Text c="dimmed" size="sm">
        Operation cards with dry-run confirm modals and live SSE progress coming in Group 11.
      </Text>
    </Stack>
  )
}
