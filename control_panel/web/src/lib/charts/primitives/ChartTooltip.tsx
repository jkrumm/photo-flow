// Vendored from argo/packages/charts — keep in sync manually.
import { useMemo, type CSSProperties, type ReactNode, type RefObject } from 'react'
import { useVxTheme } from '../theme'
import { VX } from '../tokens'
import { alpha } from '../utils/color'
import { fmtTooltipDate } from '../utils/format'

export function useTooltipStyles(): CSSProperties {
  const { tooltipBg, tooltipText, tooltipBorder, tooltipShadow } = useVxTheme()
  return useMemo(
    () => ({
      position: 'fixed' as const,
      pointerEvents: 'none' as const,
      zIndex: 9999,
      backgroundColor: tooltipBg,
      borderRadius: 6,
      padding: '0',
      fontSize: 12,
      lineHeight: '18px',
      color: tooltipText,
      border: tooltipBorder,
      boxShadow: tooltipShadow,
      minWidth: 140,
    }),
    [tooltipBg, tooltipText, tooltipBorder, tooltipShadow],
  )
}

export function ChartTooltip({
  tip,
  tooltipRef,
  styles,
  children,
}: {
  tip: { x: number; y: number } | null
  tooltipRef?: RefObject<HTMLDivElement | null>
  styles: CSSProperties
  children: ReactNode
}) {
  if (!tip) return null
  return (
    <div ref={tooltipRef} style={{ ...styles, left: tip.x, top: tip.y }}>
      {children}
    </div>
  )
}

export function TooltipHeader({
  date,
  label,
  labelColor,
}: {
  date: string
  label?: string
  labelColor?: string
}) {
  const { tooltipMuted } = useVxTheme()
  return (
    <div
      style={{
        display: 'flex',
        justifyContent: 'space-between',
        alignItems: 'center',
        gap: 16,
        padding: '6px 10px',
        borderBottom: `1px solid ${alpha(VX.neutral, 0.2)}`,
      }}
    >
      <span style={{ fontSize: 11, color: tooltipMuted }}>{fmtTooltipDate(date)}</span>
      {label !== undefined && (
        <span style={{ fontSize: 11, fontWeight: 500, color: labelColor }}>{label}</span>
      )}
    </div>
  )
}

export function TooltipRow({
  color,
  label,
  value,
  valueColor,
  shape,
  strokeWidth,
  dashed,
}: {
  color: string
  label: string
  value: string
  valueColor?: string
  shape?: 'dot' | 'line' | 'bar'
  strokeWidth?: number
  dashed?: boolean
}) {
  return (
    <div
      style={{
        display: 'flex',
        justifyContent: 'space-between',
        alignItems: 'center',
        gap: 16,
        padding: '0 10px',
      }}
    >
      <span style={{ display: 'flex', alignItems: 'center', gap: 6 }}>
        {shape === 'line' ? (
          <svg width={12} height={10} style={{ flexShrink: 0 }}>
            <line
              x1={0}
              y1={5}
              x2={12}
              y2={5}
              stroke={color}
              strokeWidth={strokeWidth ?? 2}
              strokeDasharray={dashed ? '3 2' : undefined}
            />
          </svg>
        ) : (
          <span
            style={{
              width: 8,
              height: 8,
              borderRadius: 2,
              backgroundColor: color,
              flexShrink: 0,
            }}
          />
        )}
        {label}
      </span>
      <span style={{ fontWeight: 400, color: valueColor }}>{value}</span>
    </div>
  )
}

export function TooltipBody({ children }: { children: ReactNode }) {
  return <div style={{ padding: '5px 0' }}>{children}</div>
}
