// Vendored from argo/packages/charts — keep in sync manually.
import { curveMonotoneX } from '@visx/curve'
import { GridRows } from '@visx/grid'
import { Group } from '@visx/group'
import { scaleLinear, scalePoint } from '@visx/scale'
import { AreaClosed, LinePath } from '@visx/shape'
import { Threshold } from '@visx/threshold'
import { useMemo, type ReactNode } from 'react'
import { AreaGradient, areaFillUrl } from '../primitives/AreaGradient'
import { AxisBottomDate, AxisLeftNumeric } from '../primitives/Axes'
import {
  ChartTooltip,
  TooltipBody,
  TooltipHeader,
  TooltipRow,
  useTooltipStyles,
} from '../primitives/ChartTooltip'
import { HoverOverlay } from '../primitives/HoverOverlay'
import { ZoneRects, type ZoneSpec } from '../primitives/ZoneRects'
import { useHoverSync } from '../hooks/useHoverSync'
import { useVxTheme } from '../theme'
import { VX } from '../tokens'
import { smartTicks } from '../utils/ticks'

/** @deprecated Use ZoneSpec from primitives/ZoneRects. Kept as an alias for back-compat. */
export type ZonedLineZone = ZoneSpec

export type ZonedLineThreshold = {
  value: number
  side: 'above' | 'below'
  fill: string
}

export type ZonedLineRefLine = {
  value: number
  color: string
  dashed?: boolean
}

export type ZonedLineTooltipLabel = {
  text: string
  color: string
}

export type ZonedLineProps<T> = {
  data: T[]
  width: number
  height: number
  chartId: string
  getX: (d: T) => string
  getY: (d: T) => number | null
  yDomain: [number, number] | 'auto'
  yAutoMaxFloor?: number
  yAutoMinCeil?: number
  yAutoPad?: number
  zones?: ZonedLineZone[]
  thresholds?: ZonedLineThreshold[]
  refLines?: ZonedLineRefLine[]
  numTicksY?: number
  numTicksX?: number
  tooltipLabel?: (d: T) => ZonedLineTooltipLabel | null
  seriesLabel: string
  formatValue: (v: number) => string
  renderExtraTooltipRows?: (d: T) => ReactNode
  areaFill?: string | boolean
}

export function ZonedLine<T>(props: ZonedLineProps<T>) {
  const {
    data,
    width,
    height,
    chartId,
    getX,
    getY,
    yDomain,
    yAutoPad = 1.1,
    yAutoMaxFloor,
    yAutoMinCeil = 0,
    zones = [],
    thresholds = [],
    refLines = [],
    numTicksY = 5,
    numTicksX,
    tooltipLabel,
    seriesLabel,
    formatValue,
    renderExtraTooltipRows,
    areaFill,
  } = props

  const { line } = useVxTheme()
  const MARGIN = VX.margin
  const xMax = width - MARGIN.left - MARGIN.right
  const yMax = height - MARGIN.top - MARGIN.bottom

  const showArea = areaFill !== undefined && areaFill !== false
  const areaColor = typeof areaFill === 'string' ? areaFill : line
  const areaId = `${chartId}-area`

  type Valid = T & { __y: number }
  const valid = useMemo<Valid[]>(() => {
    const out: Valid[] = []
    for (const d of data) {
      const y = getY(d)
      if (y !== null && y !== undefined && !Number.isNaN(y)) {
        out.push(Object.assign({}, d, { __y: y }) as Valid)
      }
    }
    return out
  }, [data, getY])

  const xScale = useMemo(
    () =>
      scalePoint<string>({
        domain: data.map(getX),
        range: [0, xMax],
        padding: 0.3,
      }),
    [data, xMax, getX],
  )

  const yScale = useMemo(() => {
    if (yDomain === 'auto') {
      const ys = valid.map((d) => d.__y)
      const dataMax = ys.length ? Math.max(...ys) : 0
      const dataMin = ys.length ? Math.min(...ys) : 0
      const upper = Math.max(dataMax, yAutoMaxFloor ?? dataMax) * yAutoPad
      const lower = Math.min(dataMin, yAutoMinCeil) * yAutoPad
      return scaleLinear<number>({ domain: [lower, upper], range: [yMax, 0], nice: true })
    }
    return scaleLinear<number>({ domain: yDomain, range: [yMax, 0] })
  }, [valid, yDomain, yMax, yAutoPad, yAutoMaxFloor, yAutoMinCeil])

  const tooltipStyles = useTooltipStyles()
  const { tip, tooltipRef, syncedPoint, isDirectHover, handleMouse, handleLeave } =
    useHoverSync<Valid>({
      data: valid,
      chartId,
      getX,
      xScale,
      marginLeft: MARGIN.left,
    })

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

          <ZoneRects zones={zones} width={xMax} leftScale={yScale} />

          {thresholds.map((t, i) => (
            <Threshold<Valid>
              key={`thr-${i}`}
              id={`${chartId}-thr-${i}`}
              data={valid}
              x={(d) => xScale(getX(d)) ?? 0}
              y0={() => yScale(t.value)}
              y1={(d) => yScale(d.__y)}
              clipAboveTo={0}
              clipBelowTo={yMax}
              curve={curveMonotoneX}
              belowAreaProps={{ fill: t.side === 'above' ? t.fill : 'transparent' }}
              aboveAreaProps={{ fill: t.side === 'below' ? t.fill : 'transparent' }}
            />
          ))}

          {showArea && (
            <>
              <defs>
                <AreaGradient id={areaId} color={areaColor} />
              </defs>
              <AreaClosed<Valid>
                data={valid}
                x={(d) => xScale(getX(d)) ?? 0}
                y={(d) => yScale(d.__y)}
                yScale={yScale}
                curve={curveMonotoneX}
                fill={areaFillUrl(areaId)}
              />
            </>
          )}

          {refLines.map((r, i) => (
            <line
              key={`ref-${i}`}
              x1={0}
              x2={xMax}
              y1={yScale(r.value)}
              y2={yScale(r.value)}
              stroke={r.color}
              strokeDasharray={r.dashed === false ? undefined : '4 4'}
            />
          ))}

          <LinePath<Valid>
            data={valid}
            x={(d) => xScale(getX(d)) ?? 0}
            y={(d) => yScale(d.__y)}
            stroke={line}
            strokeWidth={VX.lineWidth}
            curve={curveMonotoneX}
          />

          {syncedPoint && (
            <>
              <line
                x1={xScale(getX(syncedPoint)) ?? 0}
                x2={xScale(getX(syncedPoint)) ?? 0}
                y1={0}
                y2={yMax}
                stroke={VX.crosshair}
                strokeWidth={1}
              />
              <circle
                cx={xScale(getX(syncedPoint)) ?? 0}
                cy={yScale(syncedPoint.__y)}
                r={VX.dotR}
                fill={line}
                stroke={VX.dotStroke}
                strokeWidth={2}
              />
            </>
          )}

          <AxisLeftNumeric scale={yScale} numTicks={numTicksY} />
          <AxisBottomDate top={yMax} scale={xScale} tickValues={tickValues} />

          <HoverOverlay width={xMax} height={yMax} onMove={handleMouse} onLeave={handleLeave} />
        </Group>
      </svg>
      <ChartTooltip tip={isDirectHover ? tip : null} tooltipRef={tooltipRef} styles={tooltipStyles}>
        {tip && isDirectHover && (
          <>
            <TooltipHeader
              date={getX(tip.data)}
              {...(() => {
                const lbl = tooltipLabel?.(tip.data) ?? null
                return lbl !== null ? { label: lbl.text, labelColor: lbl.color } : {}
              })()}
            />
            <TooltipBody>
              <TooltipRow
                color={line}
                label={seriesLabel}
                value={formatValue(tip.data.__y)}
                shape="line"
              />
              {renderExtraTooltipRows?.(tip.data)}
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
