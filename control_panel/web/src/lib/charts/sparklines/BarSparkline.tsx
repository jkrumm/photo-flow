// Vendored from argo/packages/charts — keep in sync manually.
import { scaleLinear } from '@visx/scale'
import { useMemo } from 'react'
import { useVxTheme } from '../theme'

type BarSparklineProps = {
  data: number[]
  width: number
  height: number
  color?: string
}

export function BarSparkline({ data, width, height, color }: BarSparklineProps) {
  const { line } = useVxTheme()
  const fillColor = color ?? line

  const yScale = useMemo(() => {
    const max = Math.max(...data.filter((v) => isFinite(v)), 1)
    return scaleLinear<number>({ domain: [0, max], range: [0, height] })
  }, [data, height])

  if (!data.length) return <svg width={width} height={height} />

  const step = width / data.length
  const barWidth = Math.max(step - 1, 1)

  return (
    <svg width={width} height={height}>
      {data.map((v, i) => {
        const bh = Math.max(yScale(Math.max(v, 0)), 0)
        return (
          <rect
            key={i}
            x={i * step}
            y={height - bh}
            width={barWidth}
            height={bh}
            fill={fillColor}
            fillOpacity={0.75}
          />
        )
      })}
    </svg>
  )
}
