// Vendored from argo/packages/charts — keep in sync manually.

const areaStop = (color: string, edge: 'top' | 'bottom'): string =>
  `color-mix(in srgb, ${color} var(--vx-area-${edge}), transparent)`

export function AreaGradient({ id, color }: { id: string; color: string }) {
  return (
    <linearGradient id={id} x1="0" x2="0" y1="0" y2="1">
      <stop offset="0%" stopColor={areaStop(color, 'top')} />
      <stop offset="100%" stopColor={areaStop(color, 'bottom')} />
    </linearGradient>
  )
}

export const areaFillUrl = (id: string): string => `url(#${id})`
