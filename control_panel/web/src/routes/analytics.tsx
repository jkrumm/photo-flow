import { createFileRoute } from '@tanstack/react-router'
import { Stack, Text, Title } from '@mantine/core'
import { IconChartHistogram } from '@tabler/icons-react'

export const Route = createFileRoute('/analytics')({
  component: AnalyticsPage,
})

function AnalyticsPage() {
  return (
    <Stack gap="md">
      <Stack gap={4}>
        <Title order={2} style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
          <IconChartHistogram size={24} />
          Analytics
        </Title>
        <Text c="dimmed" size="sm">
          Photos over time · Ratings · ISO / Aperture / Focal · Storage · GPS map
        </Text>
      </Stack>
      <Text c="dimmed" size="sm">
        Analytics charts coming in Group 9.
      </Text>
    </Stack>
  )
}
