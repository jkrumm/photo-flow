// Vendored from argo/packages/charts — keep in sync manually.
import type { MouseEventHandler } from 'react'

export function HoverOverlay({
  width,
  height,
  onMove,
  onLeave,
}: {
  width: number
  height: number
  onMove: MouseEventHandler<SVGRectElement>
  onLeave: MouseEventHandler<SVGRectElement>
}) {
  return (
    <rect
      width={width}
      height={height}
      fill="transparent"
      onMouseMove={onMove}
      onMouseLeave={onLeave}
    />
  )
}
