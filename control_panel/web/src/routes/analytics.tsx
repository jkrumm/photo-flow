import { createFileRoute } from '@tanstack/react-router'
import { Card, Skeleton, SimpleGrid, Stack, Text, Title, SegmentedControl } from '@mantine/core'
import { IconChartHistogram } from '@tabler/icons-react'
import { useEffect, useRef, useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import { Bars, ChartCard, ChartLegend, Donut, VX, alpha } from '../lib/charts'
import { analyticsQueries } from '../lib/queries/analytics'
import { formatBytes } from '../lib/format'
import type { BucketGrain, MapPoint, SettingsEntry, StorageResponse } from '../lib/api-types'

export const Route = createFileRoute('/analytics')({
  component: AnalyticsPage,
})

// ── Shared hook ───────────────────────────────────────────────────────────────

function useChartWidth() {
  const ref = useRef<HTMLDivElement>(null)
  const [width, setWidth] = useState(640)
  useEffect(() => {
    if (!ref.current) return
    const ro = new ResizeObserver(([entry]) => {
      if (entry) setWidth(Math.max(entry.contentRect.width, 200))
    })
    ro.observe(ref.current)
    return () => ro.disconnect()
  }, [])
  return { ref, width }
}

// ── Shared helpers ────────────────────────────────────────────────────────────

function EmptyChart({ height }: { height: number }) {
  return (
    <div
      style={{
        height,
        display: 'flex',
        alignItems: 'center',
        justifyContent: 'center',
        color: alpha(VX.neutral, 0.55),
        fontSize: 13,
      }}
    >
      No data — refresh the index to populate.
    </div>
  )
}

// ── Summary tiles ─────────────────────────────────────────────────────────────

function SummaryTiles() {
  const { data, isLoading } = useQuery(analyticsQueries.summary())

  if (isLoading) {
    return (
      <SimpleGrid cols={{ base: 2, sm: 3, lg: 6 }}>
        {Array.from({ length: 6 }).map((_, i) => (
          <Skeleton key={i} h={76} radius="md" />
        ))}
      </SimpleGrid>
    )
  }
  if (!data) return null

  const publishRate =
    data.total_photos > 0
      ? `${Math.round((data.total_published / data.total_photos) * 100)}%`
      : '—'

  const tiles = [
    { label: 'Total Photos', value: data.total_photos.toLocaleString(), color: VX.photo.final },
    { label: 'Published', value: data.total_published.toLocaleString(), color: VX.photo.published },
    { label: 'This Month', value: data.this_month_count.toLocaleString(), color: VX.photo.staging },
    {
      label: 'Avg Rating',
      value: data.avg_rating !== null ? `${data.avg_rating.toFixed(1)} ★` : '—',
      color: VX.photo.rating4,
    },
    {
      label: 'Since',
      value: data.earliest_date ? data.earliest_date.slice(0, 7) : '—',
      color: VX.neutral,
    },
    { label: 'Publish Rate', value: publishRate, color: VX.good },
  ]

  return (
    <SimpleGrid cols={{ base: 2, sm: 3, lg: 6 }}>
      {tiles.map(({ label, value, color }) => (
        <Card key={label} withBorder padding="sm" style={{ borderColor: VX.surface.border }}>
          <Text size="xs" c="dimmed" mb={4}>
            {label}
          </Text>
          <Text fw={600} size="xl" style={{ color }}>
            {value}
          </Text>
        </Card>
      ))}
    </SimpleGrid>
  )
}

// ── Photos over time ──────────────────────────────────────────────────────────

const BUCKET_OPTIONS = [
  { label: 'Day', value: 'day' },
  { label: 'Week', value: 'week' },
  { label: 'Month', value: 'month' },
  { label: 'Year', value: 'year' },
] satisfies { label: string; value: BucketGrain }[]

function PhotosOverTimeChart() {
  const [bucket, setBucket] = useState<BucketGrain>('month')
  const { data, isLoading } = useQuery(analyticsQueries.overTime(bucket))
  const { ref, width } = useChartWidth()
  const [highlighted, setHighlighted] = useState<string | null>(null)

  const toggle = (
    <SegmentedControl
      value={bucket}
      onChange={(v) => setBucket(v as BucketGrain)}
      data={BUCKET_OPTIONS}
      size="xs"
    />
  )

  return (
    <div ref={ref}>
      <ChartCard
        title="Photos over Time"
        subtitle="Finalized per period — rating ≥ 4 vs. other"
        tooltip="Number of photos finalized per time period, colour-split by rating band."
        extra={toggle}
      >
        <ChartLegend
          items={[
            { key: 'high_rated', label: 'Rating ≥ 4', color: VX.photo.published, shape: 'bar' },
            { key: 'other', label: 'Other', color: VX.photo.final, shape: 'bar' },
          ]}
          highlighted={highlighted}
          onHighlight={setHighlighted}
        />
        {isLoading ? (
          <Skeleton h={220} />
        ) : !data || data.length === 0 ? (
          <EmptyChart height={220} />
        ) : (
          <Bars
            data={data}
            width={width}
            height={220}
            chartId="photos-over-time"
            getX={(d) => d.x}
            getValue={(d, key) =>
              key === 'high_rated' ? d.high_rated : key === 'other' ? d.other : null
            }
            positiveBars={[
              { key: 'high_rated', label: 'Rating ≥ 4', color: VX.photo.published },
              { key: 'other', label: 'Other', color: VX.photo.final },
            ]}
            leftAxis={{ domain: 'auto' }}
            formatValue={(v) => String(v)}
            highlightedKey={highlighted}
          />
        )}
      </ChartCard>
    </div>
  )
}

// ── Rating histogram ──────────────────────────────────────────────────────────

type RatingDatum = { x: string; count: number; rKey: string }

const ratingColor = (rKey: string): string => {
  const map: Record<string, string> = {
    rNull: alpha(VX.neutral, 0.45),
    r0: alpha(VX.neutral, 0.7),
    r1: VX.photo.rating1,
    r2: VX.photo.rating2,
    r3: VX.photo.rating3,
    r4: VX.photo.rating4,
    r5: VX.photo.rating5,
  }
  return map[rKey] ?? VX.neutral
}

function RatingHistogramChart() {
  const { data, isLoading } = useQuery(analyticsQueries.ratings())
  const { ref, width } = useChartWidth()

  if (isLoading) {
    return (
      <div ref={ref}>
        <Skeleton h={220} />
      </div>
    )
  }
  if (!data || data.histogram.length === 0) {
    return (
      <div ref={ref}>
        <ChartCard title="Rating Distribution" tooltip="Photo counts by star rating.">
          <EmptyChart height={160} />
        </ChartCard>
      </div>
    )
  }

  const countByRating = new Map<number | null, number>(
    data.histogram.map((h) => [h.rating, h.count]),
  )

  const allSlots: Array<{ rating: number | null; rKey: string; label: string }> = [
    { rating: null, rKey: 'rNull', label: '–' },
    { rating: 0, rKey: 'r0', label: '0★' },
    { rating: 1, rKey: 'r1', label: '1★' },
    { rating: 2, rKey: 'r2', label: '2★' },
    { rating: 3, rKey: 'r3', label: '3★' },
    { rating: 4, rKey: 'r4', label: '4★' },
    { rating: 5, rKey: 'r5', label: '5★' },
  ]

  const histData: RatingDatum[] = allSlots
    .filter(({ rating }) => countByRating.has(rating))
    .map(({ rating, rKey, label }) => ({
      x: label,
      count: countByRating.get(rating) ?? 0,
      rKey,
    }))

  const positiveBars = histData.map(({ x, rKey }) => ({
    key: rKey,
    label: x,
    color: ratingColor(rKey),
  }))

  return (
    <div ref={ref}>
      <ChartCard
        title="Rating Distribution"
        subtitle={`${data.total_final.toLocaleString()} total · ${data.total_published.toLocaleString()} published`}
        tooltip="Star-rating breakdown across all Final photos."
      >
        <Bars
          data={histData}
          width={width}
          height={195}
          chartId="rating-dist"
          getX={(d) => d.x}
          getValue={(d, key) => (d.rKey === key ? d.count : null)}
          positiveBars={positiveBars}
          leftAxis={{ domain: 'auto' }}
          formatValue={(v) => String(v)}
        />
      </ChartCard>
    </div>
  )
}

// ── Publish rate donut ────────────────────────────────────────────────────────

function PublishRateDonut() {
  const { data, isLoading } = useQuery(analyticsQueries.ratings())
  const { ref, width } = useChartWidth()

  if (isLoading) {
    return (
      <div ref={ref}>
        <Skeleton h={220} />
      </div>
    )
  }
  if (!data) return null

  const published = data.total_published
  const other = Math.max(data.total_final - published, 0)
  const rate =
    data.total_final > 0 ? `${Math.round((published / data.total_final) * 100)}%` : '—'
  const donutSize = Math.min(width, 180)

  return (
    <div ref={ref}>
      <ChartCard
        title="Published vs. Other"
        subtitle={`${rate} publish rate`}
        tooltip="Share of Final photos with rating ≥ 4 synced to the gallery."
      >
        <div style={{ display: 'flex', justifyContent: 'center', padding: '4px 0' }}>
          <Donut
            data={[
              { key: 'published', value: published },
              { key: 'other', value: other },
            ]}
            width={donutSize}
            height={donutSize}
            colorForKey={(k) => (k === 'published' ? VX.photo.published : alpha(VX.neutral, 0.45))}
            formatValue={(v) => v.toLocaleString()}
            seriesLabel={(k) => (k === 'published' ? 'Published' : 'Other')}
            centerLabel={rate}
            centerSubLabel="published"
          />
        </div>
        <ChartLegend
          items={[
            { key: 'published', label: 'Published (≥ 4★)', color: VX.photo.published, shape: 'bar' },
            { key: 'other', label: 'Other', color: alpha(VX.neutral, 0.45), shape: 'bar' },
          ]}
        />
      </ChartCard>
    </div>
  )
}

// ── Storage tiles ─────────────────────────────────────────────────────────────

const STORAGE_STAGES: Array<{ key: keyof StorageResponse; label: string; color: string }> = [
  { key: 'final', label: 'Final', color: VX.photo.final },
  { key: 'staging', label: 'Staging', color: VX.photo.staging },
  { key: 'raws', label: 'RAWs', color: VX.photo.raws },
  { key: 'videos', label: 'Videos', color: VX.photo.videos },
]

function StorageTiles() {
  const { data, isLoading } = useQuery(analyticsQueries.storage())

  if (isLoading) {
    return (
      <SimpleGrid cols={{ base: 2, sm: 4 }}>
        {STORAGE_STAGES.map((s) => (
          <Skeleton key={s.key} h={88} radius="md" />
        ))}
      </SimpleGrid>
    )
  }
  if (!data) return null

  return (
    <SimpleGrid cols={{ base: 2, sm: 4 }}>
      {STORAGE_STAGES.map(({ key, label, color }) => {
        const stage = data[key]
        return (
          <Card
            key={key}
            withBorder
            padding="sm"
            style={{ borderColor: VX.surface.border, opacity: stage.available ? 1 : 0.45 }}
          >
            <Text size="xs" c="dimmed" mb={4}>
              {label}
            </Text>
            <Text fw={600} size="lg" style={{ color }}>
              {stage.count.toLocaleString()} files
            </Text>
            <Text size="xs" c="dimmed">
              {formatBytes(stage.bytes)}
            </Text>
            {!stage.available && (
              <Text size="xs" style={{ color: VX.bad }} mt={2}>
                Not mounted
              </Text>
            )}
          </Card>
        )
      })}
    </SimpleGrid>
  )
}

// ── Camera settings ───────────────────────────────────────────────────────────

type SettingsDatum = { x: string; count: number }

function SettingsBarChart({
  title,
  tooltip,
  chartId,
  data,
  color,
  height = 155,
}: {
  title: string
  tooltip: string
  chartId: string
  data: SettingsDatum[]
  color: string
  height?: number
}) {
  const { ref, width } = useChartWidth()

  return (
    <div ref={ref}>
      <ChartCard title={title} tooltip={tooltip}>
        {data.length === 0 ? (
          <EmptyChart height={height} />
        ) : (
          <Bars
            data={data}
            width={width}
            height={height}
            chartId={chartId}
            getX={(d) => d.x}
            getValue={(d, key) => (key === 'count' ? d.count : null)}
            positiveBars={[{ key: 'count', label: 'Count', color }]}
            leftAxis={{ domain: 'auto' }}
            formatValue={(v) => String(v)}
            numTicksX={8}
          />
        )}
      </ChartCard>
    </div>
  )
}

function CameraSettingsSection() {
  const { data, isLoading } = useQuery(analyticsQueries.settings())

  if (isLoading) {
    return (
      <SimpleGrid cols={{ base: 1, sm: 2 }}>
        {Array.from({ length: 4 }).map((_, i) => (
          <Skeleton key={i} h={180} radius="md" />
        ))}
      </SimpleGrid>
    )
  }
  if (!data) return null

  const isoData: SettingsDatum[] = data.iso
    .filter((e): e is SettingsEntry & { value: number } => e.value !== null)
    .map((e) => ({ x: String(e.value), count: e.count }))

  const apertureData: SettingsDatum[] = data.aperture
    .filter((e): e is SettingsEntry & { value: number } => e.value !== null)
    .map((e) => ({ x: `f/${e.value.toFixed(1)}`, count: e.count }))

  const focalData: SettingsDatum[] = data.focal
    .filter((e): e is SettingsEntry & { value: number } => e.value !== null)
    .map((e) => ({ x: `${Math.round(e.value)}mm`, count: e.count }))

  const shutterData: SettingsDatum[] = data.shutter
    .filter((e) => e.value !== null)
    .map((e) => ({ x: e.label ?? String(e.value), count: e.count }))

  return (
    <SimpleGrid cols={{ base: 1, sm: 2 }}>
      <SettingsBarChart
        title="ISO"
        tooltip="Distribution of ISO sensitivity values across Final photos."
        chartId="exif-iso"
        data={isoData}
        color={VX.photo.camera}
      />
      <SettingsBarChart
        title="Aperture"
        tooltip="Distribution of aperture (f-stop) values across Final photos."
        chartId="exif-aperture"
        data={apertureData}
        color={VX.photo.staging}
      />
      <SettingsBarChart
        title="Focal Length"
        tooltip="Distribution of focal lengths across Final photos."
        chartId="exif-focal"
        data={focalData}
        color={VX.photo.raws}
      />
      <SettingsBarChart
        title="Shutter Speed"
        tooltip="Distribution of shutter speeds across Final photos."
        chartId="exif-shutter"
        data={shutterData}
        color={VX.photo.videos}
      />
    </SimpleGrid>
  )
}

// ── GPS scatter ───────────────────────────────────────────────────────────────

function GeoScatter({ data, width }: { data: MapPoint[]; width: number }) {
  const height = 200
  const PAD = 16

  const lats = data.map((p) => p.lat)
  const lngs = data.map((p) => p.lng)
  const latMin = Math.min(...lats)
  const latMax = Math.max(...lats)
  const lngMin = Math.min(...lngs)
  const lngMax = Math.max(...lngs)
  const latRange = latMax - latMin || 1
  const lngRange = lngMax - lngMin || 1

  const drawW = width - 2 * PAD
  const drawH = height - 2 * PAD

  const toX = (lng: number) => PAD + ((lng - lngMin) / lngRange) * drawW
  const toY = (lat: number) => height - PAD - ((lat - latMin) / latRange) * drawH

  return (
    <svg width={width} height={height} style={{ display: 'block' }}>
      <rect width={width} height={height} rx={4} fill={VX.surface.bg} />
      {data.map((p) => (
        <circle
          key={p.filename}
          cx={toX(p.lng)}
          cy={toY(p.lat)}
          r={4}
          fill={VX.photo.published}
          fillOpacity={0.65}
          stroke={VX.photo.published}
          strokeOpacity={0.9}
          strokeWidth={1}
        >
          <title>
            {p.filename}
            {p.date_taken ? ` · ${p.date_taken.slice(0, 10)}` : ''}
          </title>
        </circle>
      ))}
    </svg>
  )
}

function GeoScatterChart() {
  const { data, isLoading } = useQuery(analyticsQueries.map())
  const { ref, width } = useChartWidth()

  return (
    <div ref={ref}>
      <ChartCard
        title="Photo Locations"
        subtitle={data && data.length > 0 ? `${data.length} photos with GPS` : undefined}
        tooltip="GPS coordinates of Final photos. Full tile-map rendering is deferred — shown as a bounding-box scatter."
      >
        {isLoading ? (
          <Skeleton h={200} />
        ) : !data || data.length === 0 ? (
          <EmptyChart height={200} />
        ) : (
          <GeoScatter data={data} width={width} />
        )}
      </ChartCard>
    </div>
  )
}

// ── Page ──────────────────────────────────────────────────────────────────────

function AnalyticsPage() {
  return (
    <Stack gap="md">
      <Stack gap={4}>
        <Title order={2} style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
          <IconChartHistogram size={24} />
          Analytics
        </Title>
        <Text c="dimmed" size="sm">
          Library overview · Photos over time · Ratings · Camera settings · Storage · GPS
        </Text>
      </Stack>

      <SummaryTiles />
      <PhotosOverTimeChart />

      <SimpleGrid cols={{ base: 1, md: 2 }}>
        <RatingHistogramChart />
        <PublishRateDonut />
      </SimpleGrid>

      <StorageTiles />

      <Stack gap={4}>
        <Text fw={500} size="sm">
          Camera Settings
        </Text>
        <Text size="xs" c="dimmed">
          EXIF distributions across all Final photos
        </Text>
      </Stack>
      <CameraSettingsSection />

      <GeoScatterChart />
    </Stack>
  )
}
