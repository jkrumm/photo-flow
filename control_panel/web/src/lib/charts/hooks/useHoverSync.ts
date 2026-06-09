// Vendored from argo/packages/charts — keep in sync manually.
import { localPoint } from '@visx/event'
import { useCallback, useContext } from 'react'
import { DEFAULT_NO_OP_SET_HOVER, HoverContext } from '../hover-context'
import { useChartTooltip } from './useChartTooltip'

type XScale = ((x: string) => number | undefined) | { (x: string): number | undefined }

let warnedMissingProvider = false

export function useHoverSync<T>({
  data,
  chartId,
  getX,
  xScale,
  marginLeft,
}: {
  data: T[]
  chartId: string
  getX: (d: T) => string
  xScale: XScale
  marginLeft: number
}) {
  const ctx = useContext(HoverContext)

  if (import.meta.env.DEV && ctx.setHover === DEFAULT_NO_OP_SET_HOVER && !warnedMissingProvider) {
    warnedMissingProvider = true
    // eslint-disable-next-line no-console
    console.warn(
      '[charts] useHoverSync used outside <HoverContext.Provider>. Cross-chart cursor sync will not work.',
    )
  }

  const { tip, show, hide, tooltipRef, lastDateRef } = useChartTooltip<T>()

  const handleMouse = useCallback(
    (event: React.MouseEvent<SVGRectElement>) => {
      const point = localPoint(event)
      if (!point || data.length === 0) return
      const px = point.x - marginLeft
      let closest: T = data[0] as T
      let minDist = Infinity
      for (const d of data) {
        const sx = xScale(getX(d)) ?? 0
        const dist = Math.abs(sx - px)
        if (dist < minDist) {
          minDist = dist
          closest = d
        }
      }
      show(closest, event)
      const date = getX(closest)
      if (lastDateRef.current !== date) {
        lastDateRef.current = date
        ctx.setHover(date, chartId)
      }
    },
    [data, xScale, getX, chartId, marginLeft, show, lastDateRef, ctx],
  )

  const handleLeave = useCallback(() => {
    hide()
    ctx.setHover(null, null)
  }, [hide, ctx])

  const syncedPoint = ctx.date ? (data.find((d) => getX(d) === ctx.date) ?? null) : null
  const isDirectHover = ctx.source === chartId

  return {
    tip,
    tooltipRef,
    syncedPoint,
    isDirectHover,
    handleMouse,
    handleLeave,
  }
}
