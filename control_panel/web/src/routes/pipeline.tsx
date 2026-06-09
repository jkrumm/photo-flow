import { createFileRoute } from '@tanstack/react-router'
import { Stack, Text, Title } from '@mantine/core'
import { IconCamera } from '@tabler/icons-react'
import { PipelineHero } from '../components/pipeline/PipelineHero'

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
          Camera → Staging → Final → Publish — live counts, click any stage to operate
        </Text>
      </Stack>
      <PipelineHero />
    </Stack>
  )
}
