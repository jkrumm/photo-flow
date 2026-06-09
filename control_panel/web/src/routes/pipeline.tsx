import { createFileRoute } from '@tanstack/react-router'
import { Stack, Text, Title } from '@mantine/core'
import { IconCamera } from '@tabler/icons-react'

export const Route = createFileRoute('/pipeline')({
  component: PipelinePage,
})

function PipelinePage() {
  return (
    <Stack gap="md">
      <Stack gap={4}>
        <Title order={2} style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
          <IconCamera size={24} />
          Pipeline
        </Title>
        <Text c="dimmed" size="sm">
          Camera → Staging → Final → Publish
        </Text>
      </Stack>
      <Text c="dimmed" size="sm">
        Animated pipeline hero coming in Group 10.
      </Text>
    </Stack>
  )
}
