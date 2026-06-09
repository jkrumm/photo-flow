// Vendored from argo/packages/charts — keep in sync manually.
import { type ReactNode } from 'react'
import { VX } from '../tokens'
import { alpha } from '../utils/color'

function InfoIcon({ title }: { title: string }) {
  return (
    <span
      title={title}
      aria-label={title}
      style={{
        cursor: 'help',
        marginLeft: 6,
        color: alpha(VX.neutral, 0.55),
        display: 'inline-flex',
        alignItems: 'center',
      }}
    >
      <svg width={11} height={11} viewBox="0 0 24 24" fill="currentColor" aria-hidden="true">
        <path d="M12 0C5.373 0 0 5.373 0 12s5.373 12 12 12 12-5.373 12-12S18.627 0 12 0zm0 4a1.5 1.5 0 1 1 0 3 1.5 1.5 0 0 1 0-3zm0 5a1 1 0 0 1 1 1v7a1 1 0 1 1-2 0v-7a1 1 0 0 1 1-1z" />
      </svg>
    </span>
  )
}

const cardStyle = {
  border: `1px solid ${VX.surface.border}`,
  borderRadius: 8,
  marginBottom: 16,
  overflow: 'hidden' as const,
  backgroundColor: VX.surface.panel,
  boxShadow: VX.shadowCard,
}
const headerStyle = {
  display: 'flex',
  justifyContent: 'space-between',
  alignItems: 'center',
  padding: '8px 12px',
  borderBottom: `1px solid ${alpha(VX.neutral, 0.14)}`,
}
const titleColumnStyle = {
  display: 'inline-flex',
  flexDirection: 'column' as const,
  lineHeight: 1.15,
}
const titleStyle = { display: 'inline-flex', alignItems: 'center', fontWeight: 500, fontSize: 14 }
const subtitleStyle = {
  fontSize: 11,
  fontWeight: 400,
  color: alpha(VX.neutral, 0.75),
  marginTop: 2,
}

export function ChartCard({
  title,
  subtitle,
  tooltip,
  extra,
  children,
}: {
  title: string
  subtitle?: string
  tooltip: string
  extra?: ReactNode
  children: ReactNode
}) {
  return (
    <div style={cardStyle}>
      <div style={headerStyle}>
        <span style={titleColumnStyle}>
          <span style={titleStyle}>
            {title}
            <InfoIcon title={tooltip} />
          </span>
          {subtitle !== undefined && <span style={subtitleStyle}>{subtitle}</span>}
        </span>
        {extra !== undefined && <span>{extra}</span>}
      </div>
      <div style={{ padding: '8px 12px' }}>{children}</div>
    </div>
  )
}
