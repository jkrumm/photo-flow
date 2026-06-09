// Vendored from argo/packages/charts — keep in sync manually.
import { curveMonotoneX } from '@visx/curve'
import { GridRows } from '@visx/grid'
import { Group } from '@visx/group'
import { scaleLinear, scalePoint } from '@visx/scale'
import { AreaStack } from '@visx/shape'
import { useMemo } from 'react'
import { AxisBottomDate, AxisLeftNumeric } from '../primitives/Axes'
import {
  ChartTooltip,
  TooltipBody,
  TooltipHeader,
  TooltipRow,
  useTooltipStyles,
} from '../primitives/ChartTooltip'
import { HoverOverlay } from '../primitives/HoverOverlay'
import { useHoverSync } from '../hooks/useHoverSync'
import { VX } from '../tokens'
import { smartTicks } from '../utils/ticks'

export type StackedAreaProps<T> = {
  data: T[]
  width: number
  height: number
  chartId: string
  getX: (d: T) => string
  groups: string[]
  getValue: (d: T, group: string) => number
  colorForGroup: (group: string) => string
  seriesLabel?: (group: string) => string
  formatValue: (v: number) => string
  numTicksY?: number
  numTicksX?: number
  yAutoMaxFloor?: number
}

export function StackedArea<T>(props: StackedAreaProps<T>) {
  const {
    data,
    width,
    height,
    chartId,
    getX,
    groups,
    getValue,
    colorForGroup,
    seriesLabel = (g) => g,
    formatValue,
    numTicksY = 5,
    numTicksX,
    yAutoMaxFloor,
  } = props

  const MARGIN = VX.margin
  const xMax = width - MARGIN.left - MARGIN.right
  const yMax = height - MARGIN.top - MARGIN.bottom

  const xScale = useMemo(
    () =>
      scalePoint<string>({
        domain: data.map(getX),
        range: [0, xMax],
        padding: 0,
      }),
    [data, xMax, getX],
  )

  const yScale = useMemo(() => {
    let maxTotal = 0
    for (const d of data) {
      let total = 0
      for (const g of groups) {
        total += getValue(d, g) ?? 0
      }
      if (total > maxTotal) maxTotal = total
    }
    const upper = Math.max(maxTotal, yAutoMaxFloor ?? maxTotal) * 1.1
    return scaleLinear<number>({ domain: [0, upper], range: [yMax, 0], nice: true })
  }, [data, groups, getValue, yMax, yAutoMaxFloor])

  const tooltipStyles = useTooltipStyles()
  const { tip, tooltipRef, syncedPoint, isDirectHover, handleMouse, handleLeave } = useHoverSync<T>(
    {
      data,
      chartId,
      getX,
      xScale,
      marginLeft: MARGIN.left,
    },
  )

  const tickValues = useMemo(
    () =>
      numTicksX ? smartTicksEvery(data.map(getX), numTicksX) : smartTicks(data.map(getX), xMax),
    [data, xMax, getX, numTicksX],
  )

  return (
    <div style={{ position: 'relative' }}>
      <svg width={width} height={height}>
        <Group left={MARGIN.left} top={MARGIN.top}>
          <GridRows scale={yScale} width={xMax} stroke={VX.grid} numTicks={numTicksY} />

          <AreaStack<T>
            data={data}
            keys={groups}
            x={(d) => xScale(getX(d.data)) ?? 0}
            y0={(d) => yScale(d[0]) ?? 0}
            y1={(d) => yScale(d[1]) ?? 0}
            value={(d, key) => getValue(d, key as string) ?? 0}
            curve={curveMonotoneX}
          >
            {({ stacks, path }) =>
              stacks.map((stack) => (
                <path
                  key={`stack-${stack.key}`}
                  d={path(stack) || ''}
                  fill={colorForGroup(String(stack.key))}
                  stroke="transparent"
                />
              ))
            }
          </AreaStack>

          {syncedPoint && (
            <line
              x1={xScale(getX(syncedPoint)) ?? 0}
              x2={xScale(getX(syncedPoint)) ?? 0}
              y1={0}
              y2={yMax}
              stroke={VX.crosshair}
              strokeWidth={1}
            />
          )}

          <AxisLeftNumeric scale={yScale} numTicks={numTicksY} tickFormat={formatValue} />
          <AxisBottomDate top={yMax} scale={xScale} tickValues={tickValues} />

          <HoverOverlay width={xMax} height={yMax} onMove={handleMouse} onLeave={handleLeave} />
        </Group>
      </svg>
      <ChartTooltip tip={isDirectHover ? tip : null} tooltipRef={tooltipRef} styles={tooltipStyles}>
        {tip && isDirectHover && (
          <>
            <TooltipHeader date={getX(tip.data)} />
            <TooltipBody>
              {groups.toReversed().map((g) => {
                const v = getValue(tip.data, g) ?? 0
                return (
                  <TooltipRow
                    key={g}
                    color={colorForGroup(g)}
                    label={seriesLabel(g)}
                    value={formatValue(v)}
                    shape="bar"
                  />
                )
              })}
            </TooltipBody>
          </>
        )}
      </ChartTooltip>
    </div>
  )
}

function smartTicksEvery(dates: string[], count: number): string[] {
  if (dates.length === 0) return []
  if (dates.length <= count) return dates
  const step = Math.ceil(dates.length / count)
  return dates.filter((_, i) => i % step === 0 || i === dates.length - 1)
}
