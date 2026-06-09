// Vendored from argo/packages/charts — keep in sync manually.
import { curveMonotoneX } from '@visx/curve'
import { Group } from '@visx/group'
import { scaleLinear } from '@visx/scale'
import { LinePath } from '@visx/shape'
import { useMemo } from 'react'
import { useVxTheme } from '../theme'

type LineSparklineProps = {
  data: number[]
  width: number
  height: number
  color?: string
}

export function LineSparkline({ data, width, height, color }: LineSparklineProps) {
  const { line } = useVxTheme()
  const strokeColor = color ?? line

  const xScale = useMemo(
    () => scaleLinear<number>({ domain: [0, Math.max(data.length - 1, 1)], range: [0, width] }),
    [data.length, width],
  )

  const yScale = useMemo(() => {
    const vals = data.filter((v) => isFinite(v))
    if (!vals.length) return scaleLinear<number>({ domain: [0, 1], range: [height, 0] })
    const min = Math.min(...vals)
    const max = Math.max(...vals)
    const pad = (max - min) * 0.1 || 1
    return scaleLinear<number>({ domain: [min - pad, max + pad], range: [height, 0] })
  }, [data, height])

  if (data.length < 2) return <svg width={width} height={height} />

  const indexed = data.map((v, i) => ({ v, i }))
  const last = indexed[indexed.length - 1]

  return (
    <svg width={width} height={height}>
      <Group>
        <LinePath<{ v: number; i: number }>
          data={indexed}
          x={(d) => xScale(d.i)}
          y={(d) => yScale(d.v)}
          stroke={strokeColor}
          strokeWidth={1.5}
          curve={curveMonotoneX}
        />
        {last !== undefined && (
          <circle cx={xScale(last.i)} cy={yScale(last.v)} r={2.5} fill={strokeColor} />
        )}
      </Group>
    </svg>
  )
}
