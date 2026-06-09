// Vendored from argo/packages/charts — keep in sync manually.
import { curveMonotoneX } from '@visx/curve'
import { GridRows } from '@visx/grid'
import { Group } from '@visx/group'
import { scaleLinear, scalePoint } from '@visx/scale'
import { LinePath } from '@visx/shape'
import { useMemo, type ReactNode } from 'react'
import { AxisBottomDate, AxisLeftNumeric, AxisRightNumeric } from '../primitives/Axes'
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
import { VX } from '../tokens'
import { smartTicks } from '../utils/ticks'

export type BarsBar = {
  key: string
  label: string
  color: string
  formatValue?: (v: number) => string
  axisSide?: 'left' | 'right'
  weight?: number
}

export type BarsLine = {
  key: string
  label: string
  color: string
  axisSide?: 'left' | 'right'
  strokeWidth?: number
  dashed?: boolean
  formatValue?: (v: number) => string
}

/** @deprecated Use ZoneSpec from primitives/ZoneRects. Kept as an alias. */
export type BarsZone = ZoneSpec

export type BarsRefLine = {
  value: number
  color: string
  dashed?: boolean
  axisSide?: 'left' | 'right'
}

export type BarsAxisConfig = {
  domain: [number, number] | 'auto'
  autoPad?: number
  autoMinCeil?: number
  autoMaxFloor?: number
  formatTick?: (v: number) => string
  numTicks?: number
}

export type BarsProps<T> = {
  data: T[]
  width: number
  height: number
  chartId: string
  getX: (d: T) => string
  getValue: (d: T, key: string) => number | null
  positiveBars: BarsBar[]
  negativeBars?: BarsBar[]
  lines?: BarsLine[]
  zones?: BarsZone[]
  refLines?: BarsRefLine[]
  leftAxis: BarsAxisConfig
  rightAxis?: BarsAxisConfig
  barWidthRatio?: number
  barLayout?: 'stacked' | 'grouped'
  barOpacity?: (d: T, key: string) => number
  tooltipLabel?: (d: T) => { text: string; color: string } | null
  renderPrefixTooltipRows?: (d: T) => ReactNode
  renderExtraTooltipRows?: (d: T) => ReactNode
  hideBarTooltipRows?: boolean
  formatValue?: (v: number) => string
  numTicksX?: number
  marginLeft?: number
  highlightedKey?: string | null
}

export function Bars<T>(props: BarsProps<T>) {
  const {
    data,
    width,
    height,
    chartId,
    getX,
    getValue,
    positiveBars,
    negativeBars = [],
    lines = [],
    zones = [],
    refLines = [],
    leftAxis,
    rightAxis,
    barWidthRatio = 0.6,
    barLayout = 'stacked',
    barOpacity,
    tooltipLabel,
    renderPrefixTooltipRows,
    renderExtraTooltipRows,
    hideBarTooltipRows = false,
    formatValue = (v) => String(Math.round(v)),
    numTicksX,
    marginLeft,
    highlightedKey = null,
  } = props

  const dimOpacity = (key: string): number =>
    highlightedKey === null || highlightedKey === key ? 1 : 0.15

  const MARGIN = useMemo(
    () => ({
      ...VX.margin,
      left: marginLeft ?? VX.margin.left,
      right: rightAxis ? Math.max(VX.margin.right, 40) : VX.margin.right,
    }),
    [rightAxis, marginLeft],
  )
  const xMax = width - MARGIN.left - MARGIN.right
  const yMax = height - MARGIN.top - MARGIN.bottom

  const xScale = useMemo(
    () => scalePoint<string>({ domain: data.map(getX), range: [0, xMax], padding: 0.3 }),
    [data, xMax, getX],
  )

  const leftYScale = useMemo(() => {
    if (leftAxis.domain === 'auto') {
      const autoPad = leftAxis.autoPad ?? 1.1
      let maxSum = 0
      let minSum = 0
      if (barLayout === 'stacked') {
        for (const d of data) {
          let pos = 0
          for (const b of positiveBars) {
            const v = getValue(d, b.key)
            if (v !== null && !Number.isNaN(v) && v > 0) pos += v
          }
          let neg = 0
          for (const b of negativeBars) {
            const v = getValue(d, b.key)
            if (v !== null && !Number.isNaN(v) && v > 0) neg -= v
          }
          if (pos > maxSum) maxSum = pos
          if (neg < minSum) minSum = neg
        }
      } else {
        for (const d of data) {
          for (const b of positiveBars) {
            if ((b.axisSide ?? 'left') !== 'left') continue
            const v = getValue(d, b.key)
            if (v === null || Number.isNaN(v)) continue
            if (v > maxSum) maxSum = v
          }
        }
      }
      for (const ln of lines) {
        if ((ln.axisSide ?? 'left') !== 'left') continue
        for (const d of data) {
          const v = getValue(d, ln.key)
          if (v === null || Number.isNaN(v)) continue
          if (v > maxSum) maxSum = v
          if (v < minSum) minSum = v
        }
      }
      const upper = Math.max(maxSum, leftAxis.autoMaxFloor ?? maxSum) * autoPad
      const ceil = leftAxis.autoMinCeil ?? 0
      const lower = Math.min(minSum, ceil) * autoPad
      return scaleLinear<number>({ domain: [lower, upper], range: [yMax, 0], nice: true })
    }
    return scaleLinear<number>({ domain: leftAxis.domain, range: [yMax, 0] })
  }, [data, leftAxis, positiveBars, negativeBars, lines, getValue, yMax, barLayout])

  const rightYScale = useMemo(() => {
    if (!rightAxis) return null
    if (rightAxis.domain === 'auto') {
      const autoPad = rightAxis.autoPad ?? 1.1
      let max = -Infinity
      let min = Infinity
      if (barLayout === 'grouped') {
        for (const d of data) {
          for (const b of positiveBars) {
            if ((b.axisSide ?? 'left') !== 'right') continue
            const v = getValue(d, b.key)
            if (v === null || Number.isNaN(v)) continue
            if (v > max) max = v
            if (v < min) min = v
          }
        }
      }
      for (const ln of lines) {
        if ((ln.axisSide ?? 'left') !== 'right') continue
        for (const d of data) {
          const v = getValue(d, ln.key)
          if (v === null || Number.isNaN(v)) continue
          if (v > max) max = v
          if (v < min) min = v
        }
      }
      for (const z of zones) {
        if ((z.axisSide ?? 'left') !== 'right') continue
        if (Number.isFinite(z.to) && z.to > max) max = z.to
        if (Number.isFinite(z.from) && z.from < min) min = z.from
      }
      for (const r of refLines) {
        if ((r.axisSide ?? 'left') !== 'right') continue
        if (r.value > max) max = r.value
        if (r.value < min) min = r.value
      }
      const safeMax = Number.isFinite(max) ? max : 0
      const safeMin = Number.isFinite(min) ? min : 0
      const upper = Math.max(safeMax, rightAxis.autoMaxFloor ?? safeMax) * autoPad
      const ceil = rightAxis.autoMinCeil ?? 0
      const lower = Math.min(safeMin, ceil) * autoPad
      return scaleLinear<number>({ domain: [lower, upper], range: [yMax, 0], nice: true })
    }
    return scaleLinear<number>({ domain: rightAxis.domain, range: [yMax, 0] })
  }, [data, rightAxis, lines, zones, refLines, positiveBars, getValue, yMax, barLayout])

  const tooltipStyles = useTooltipStyles()
  const { tip, tooltipRef, syncedPoint, isDirectHover, handleMouse, handleLeave } = useHoverSync<T>(
    { data, chartId, getX, xScale, marginLeft: MARGIN.left },
  )

  const tickValues = useMemo(
    () =>
      numTicksX ? smartTicksEvery(data.map(getX), numTicksX) : smartTicks(data.map(getX), xMax),
    [data, xMax, getX, numTicksX],
  )

  const groupWidth = Math.max((xMax / Math.max(data.length, 1)) * barWidthRatio, 2)

  const groupedBarWidths = useMemo(() => {
    if (barLayout !== 'grouped') return [] as number[]
    const totalWeight = positiveBars.reduce((s, b) => s + (b.weight ?? 1), 0) || 1
    return positiveBars.map((b) => Math.max(groupWidth * ((b.weight ?? 1) / totalWeight), 1))
  }, [positiveBars, groupWidth, barLayout])

  const groupedBarOffsets = useMemo(() => {
    const out: number[] = []
    let cursor = 0
    for (const w of groupedBarWidths) {
      out.push(cursor)
      cursor += w
    }
    return out
  }, [groupedBarWidths])

  const scaleFor = (side: 'left' | 'right' | undefined) =>
    side === 'right' && rightYScale ? rightYScale : leftYScale

  const formatBar = (b: BarsBar, v: number) => (b.formatValue ?? formatValue)(v)
  const formatLine = (ln: BarsLine, v: number) => (ln.formatValue ?? formatValue)(v)

  return (
    <div style={{ position: 'relative' }}>
      <svg width={width} height={height}>
        <Group left={MARGIN.left} top={MARGIN.top}>
          <GridRows
            scale={leftYScale}
            width={xMax}
            stroke={VX.grid}
            numTicks={leftAxis.numTicks ?? 5}
          />

          <ZoneRects zones={zones} width={xMax} leftScale={leftYScale} rightScale={rightYScale} />

          {refLines.map((r, i) => {
            const scale = scaleFor(r.axisSide)
            return (
              <line
                key={`ref-${i}`}
                x1={0}
                x2={xMax}
                y1={scale(r.value)}
                y2={scale(r.value)}
                stroke={r.color}
                strokeDasharray={r.dashed === false ? undefined : '4 4'}
              />
            )
          })}

          {data.map((d) => {
            const cx = xScale(getX(d)) ?? 0
            const groupLeft = cx - groupWidth / 2

            const els: ReactNode[] = []

            if (barLayout === 'stacked') {
              let posOffset = 0
              for (const b of positiveBars) {
                const v = getValue(d, b.key)
                if (v === null || Number.isNaN(v) || v <= 0) continue
                const top = posOffset + v
                const yTop = leftYScale(top)
                const yBottom = leftYScale(posOffset)
                els.push(
                  <rect
                    key={`${getX(d)}-${b.key}`}
                    x={groupLeft}
                    y={yTop}
                    width={groupWidth}
                    height={yBottom - yTop}
                    fill={b.color}
                    fillOpacity={(barOpacity?.(d, b.key) ?? 0.85) * dimOpacity(b.key)}
                  />,
                )
                posOffset = top
              }
              let negOffset = 0
              for (const b of negativeBars) {
                const v = getValue(d, b.key)
                if (v === null || Number.isNaN(v) || v <= 0) continue
                const top = negOffset + v
                const yTop = leftYScale(-negOffset)
                const yBottom = leftYScale(-top)
                els.push(
                  <rect
                    key={`${getX(d)}-${b.key}-neg`}
                    x={groupLeft}
                    y={yTop}
                    width={groupWidth}
                    height={yBottom - yTop}
                    fill={b.color}
                    fillOpacity={(barOpacity?.(d, b.key) ?? 0.85) * dimOpacity(b.key)}
                  />,
                )
                negOffset = top
              }
            } else {
              positiveBars.forEach((b, i) => {
                const v = getValue(d, b.key)
                if (v === null || Number.isNaN(v) || v <= 0) return
                const scale = scaleFor(b.axisSide)
                const yTop = scale(v)
                const yBottom = scale(0)
                els.push(
                  <rect
                    key={`${getX(d)}-${b.key}`}
                    x={groupLeft + (groupedBarOffsets[i] ?? 0)}
                    y={yTop}
                    width={groupedBarWidths[i] ?? 0}
                    height={yBottom - yTop}
                    fill={b.color}
                    fillOpacity={(barOpacity?.(d, b.key) ?? 0.85) * dimOpacity(b.key)}
                  />,
                )
              })
            }

            return <g key={`bars-${getX(d)}`}>{els}</g>
          })}

          {lines.map((ln) => {
            const scale = scaleFor(ln.axisSide)
            type LinePt = { __d: T; __y: number }
            const valid: LinePt[] = []
            for (const d of data) {
              const v = getValue(d, ln.key)
              if (v !== null && !Number.isNaN(v)) valid.push({ __d: d, __y: v })
            }
            if (valid.length === 0) return null
            return (
              <LinePath<LinePt>
                key={`line-${ln.key}`}
                data={valid}
                x={(p) => xScale(getX(p.__d)) ?? 0}
                y={(p) => scale(p.__y)}
                stroke={ln.color}
                strokeWidth={ln.strokeWidth ?? VX.lineWidth}
                strokeDasharray={ln.dashed ? '4 4' : undefined}
                strokeOpacity={dimOpacity(ln.key)}
                curve={curveMonotoneX}
              />
            )
          })}

          {negativeBars.length > 0 && (
            <line
              x1={0}
              x2={xMax}
              y1={leftYScale(0)}
              y2={leftYScale(0)}
              stroke={VX.grid}
              strokeWidth={1}
            />
          )}

          {syncedPoint &&
            (() => {
              const sx = xScale(getX(syncedPoint)) ?? 0
              return (
                <>
                  <line x1={sx} x2={sx} y1={0} y2={yMax} stroke={VX.crosshair} strokeWidth={1} />
                  {lines.map((ln) => {
                    const v = getValue(syncedPoint, ln.key)
                    if (v === null || Number.isNaN(v)) return null
                    const scale = scaleFor(ln.axisSide)
                    return (
                      <circle
                        key={`dot-${ln.key}`}
                        cx={sx}
                        cy={scale(v)}
                        r={4}
                        fill={ln.color}
                        stroke={VX.dotStroke}
                        strokeWidth={2}
                      />
                    )
                  })}
                </>
              )
            })()}

          <AxisLeftNumeric
            scale={leftYScale}
            numTicks={leftAxis.numTicks ?? 5}
            {...(leftAxis.formatTick !== undefined && { tickFormat: leftAxis.formatTick })}
          />
          {rightYScale && rightAxis && (
            <AxisRightNumeric
              scale={rightYScale}
              left={xMax}
              numTicks={rightAxis.numTicks ?? 5}
              {...(rightAxis.formatTick !== undefined && { tickFormat: rightAxis.formatTick })}
            />
          )}
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
              {renderPrefixTooltipRows?.(tip.data)}
              {!hideBarTooltipRows &&
                positiveBars.map((b) => {
                  const v = getValue(tip.data, b.key)
                  if (v === null || Number.isNaN(v)) return null
                  return (
                    <TooltipRow
                      key={b.key}
                      color={b.color}
                      label={b.label}
                      value={formatBar(b, v)}
                      shape="bar"
                    />
                  )
                })}
              {!hideBarTooltipRows &&
                negativeBars.map((b) => {
                  const v = getValue(tip.data, b.key)
                  if (v === null || Number.isNaN(v)) return null
                  return (
                    <TooltipRow
                      key={b.key}
                      color={b.color}
                      label={b.label}
                      value={formatBar(b, v)}
                      shape="bar"
                    />
                  )
                })}
              {lines.map((ln) => {
                const v = getValue(tip.data, ln.key)
                if (v === null || Number.isNaN(v)) return null
                return (
                  <TooltipRow
                    key={ln.key}
                    color={ln.color}
                    label={ln.label}
                    value={formatLine(ln, v)}
                    shape="line"
                    {...(ln.strokeWidth !== undefined && { strokeWidth: ln.strokeWidth })}
                    {...(ln.dashed !== undefined && { dashed: ln.dashed })}
                  />
                )
              })}
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
