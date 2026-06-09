// Vendored from argo/packages/charts — keep in sync manually.
import { Group } from '@visx/group'
import { Pie } from '@visx/shape'
import { useMemo, useState } from 'react'
import { ChartTooltip, TooltipBody, TooltipRow, useTooltipStyles } from '../primitives/ChartTooltip'
import { useVxTheme } from '../theme'
import { VX } from '../tokens'

export type DonutDatum = { key: string; value: number }

export type DonutProps = {
  data: DonutDatum[]
  width: number
  height: number
  colorForKey: (key: string) => string
  formatValue: (v: number) => string
  seriesLabel?: (key: string) => string
  centerLabel?: string
  centerSubLabel?: string
  innerRatio?: number
  padAngle?: number
}

export function Donut(props: DonutProps) {
  const {
    data,
    width,
    height,
    colorForKey,
    formatValue,
    seriesLabel = (k) => k,
    centerLabel,
    centerSubLabel,
    innerRatio = 0.6,
    padAngle = 0.01,
  } = props

  const theme = useVxTheme()
  const tooltipStyles = useTooltipStyles()

  const [hoveredKey, setHoveredKey] = useState<string | null>(null)
  const [tip, setTip] = useState<{ x: number; y: number; datum: DonutDatum } | null>(null)

  const radius = Math.min(width, height) / 2 - 4
  const innerRadius = radius * innerRatio
  const centerX = width / 2
  const centerY = height / 2

  const total = useMemo(() => data.reduce((sum, d) => sum + d.value, 0), [data])

  return (
    <div style={{ position: 'relative' }}>
      <svg width={width} height={height}>
        <Group left={centerX} top={centerY}>
          <Pie<DonutDatum>
            data={data}
            pieValue={(d) => d.value}
            pieSortValues={() => 0}
            outerRadius={radius}
            innerRadius={innerRadius}
            padAngle={padAngle}
            cornerRadius={2}
          >
            {(pie) =>
              pie.arcs.map((arc) => {
                const key = arc.data.key
                return (
                  <g
                    key={key}
                    onMouseEnter={(event) => {
                      setHoveredKey(key)
                      setTip({ x: event.clientX + 12, y: event.clientY - 12, datum: arc.data })
                    }}
                    onMouseMove={(event) => {
                      setTip({ x: event.clientX + 12, y: event.clientY - 12, datum: arc.data })
                    }}
                    onMouseLeave={() => {
                      setHoveredKey(null)
                      setTip(null)
                    }}
                    style={{ cursor: 'pointer' }}
                  >
                    <path
                      d={pie.path(arc) || ''}
                      fill={colorForKey(key)}
                      stroke={theme.tooltipBg}
                      strokeWidth={1.5}
                      opacity={hoveredKey === null || hoveredKey === key ? 1 : 0.4}
                    />
                  </g>
                )
              })
            }
          </Pie>

          {centerLabel && (
            <text
              textAnchor="middle"
              dominantBaseline="middle"
              y={centerSubLabel ? -8 : 0}
              fill={theme.tooltipText}
              fontSize={14}
              fontWeight={600}
            >
              {centerLabel}
            </text>
          )}
          {centerSubLabel && (
            <text
              textAnchor="middle"
              dominantBaseline="middle"
              y={centerLabel ? 10 : 0}
              fill={theme.tooltipText}
              fontSize={11}
              opacity={0.75}
            >
              {centerSubLabel}
            </text>
          )}
        </Group>
      </svg>

      {tip && (
        <ChartTooltip tip={{ x: tip.x, y: tip.y }} styles={tooltipStyles}>
          <TooltipBody>
            <TooltipRow
              color={colorForKey(tip.datum.key)}
              label={seriesLabel(tip.datum.key)}
              value={formatValue(tip.datum.value)}
              shape="bar"
            />
            <TooltipRow
              color={VX.grid}
              label="Share"
              value={`${total > 0 ? Math.round((tip.datum.value / total) * 100) : 0}%`}
              shape="bar"
            />
          </TooltipBody>
        </ChartTooltip>
      )}
    </div>
  )
}
