// Vendored from argo/packages/charts — keep in sync manually.
import type { ScaleLinear } from 'd3-scale'

export type ZoneSpec = {
  from: number
  to: number
  fill: string
  axisSide?: 'left' | 'right'
}

export function ZoneRects({
  zones,
  width,
  leftScale,
  rightScale,
}: {
  zones: ZoneSpec[]
  width: number
  leftScale: ScaleLinear<number, number>
  rightScale?: ScaleLinear<number, number> | null
}) {
  return (
    <>
      {zones.map((z, i) => {
        const scale = z.axisSide === 'right' && rightScale ? rightScale : leftScale
        const [domainMin, domainMax] = scale.domain() as [number, number]
        const zTo = z.to === Infinity ? domainMax : z.to
        const zFrom = z.from === -Infinity ? domainMin : z.from
        const yTop = scale(zTo)
        const yBottom = scale(zFrom)
        return (
          <rect
            key={`zone-${i}`}
            x={0}
            y={yTop}
            width={width}
            height={yBottom - yTop}
            fill={z.fill}
          />
        )
      })}
    </>
  )
}
