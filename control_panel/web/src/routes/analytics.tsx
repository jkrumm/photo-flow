import { createFileRoute } from '@tanstack/react-router'
import { Stack, Text, Title } from '@mantine/core'
import { IconChartHistogram } from '@tabler/icons-react'
import { useEffect, useRef, useState } from 'react'
import { Bars, ChartCard, ChartLegend, VX } from '../lib/charts'

export const Route = createFileRoute('/analytics')({
  component: AnalyticsPage,
})

/** Sample dummy data — photos finalized per month in 2026. */
const SAMPLE_DATA = [
  { month: '2026-01-01', final: 38, published: 12 },
  { month: '2026-02-01', final: 54, published: 19 },
  { month: '2026-03-01', final: 29, published: 8 },
  { month: '2026-04-01', final: 61, published: 24 },
  { month: '2026-05-01', final: 47, published: 15 },
  { month: '2026-06-01', final: 33, published: 11 },
]

function PhotosByMonthChart() {
  const containerRef = useRef<HTMLDivElement>(null)
  const [width, setWidth] = useState(640)

  useEffect(() => {
    if (!containerRef.current) return
    const ro = new ResizeObserver(([entry]) => {
      if (entry) setWidth(Math.max(entry.contentRect.width, 200))
    })
    ro.observe(containerRef.current)
    return () => ro.disconnect()
  }, [])

  return (
    <div ref={containerRef}>
      <ChartCard
        title="Photos by Month"
        subtitle="Final vs. published (rating ≥ 4)"
        tooltip="Number of photos finalized and published each month. Published = rating ≥ 4 synced to gallery."
      >
        <ChartLegend
          items={[
            { key: 'final', label: 'Final', color: VX.photo.final, shape: 'bar' },
            { key: 'published', label: 'Published', color: VX.photo.published, shape: 'bar' },
          ]}
        />
        <Bars
          data={SAMPLE_DATA}
          width={width}
          height={220}
          chartId="photos-by-month"
          getX={(d) => d.month}
          getValue={(d, key) =>
            key === 'final' ? d.final : key === 'published' ? d.published : null
          }
          positiveBars={[
            { key: 'final', label: 'Final', color: VX.photo.final },
            { key: 'published', label: 'Published', color: VX.photo.published },
          ]}
          leftAxis={{ domain: 'auto' }}
          formatValue={(v) => String(v)}
        />
      </ChartCard>
    </div>
  )
}

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
      <PhotosByMonthChart />
      <Text c="dimmed" size="sm">
        Full analytics charts (rating distribution, EXIF, GPS map) will be wired to the SQLite
        index in Groups 10–12.
      </Text>
    </Stack>
  )
}
