// Vendored from argo/packages/charts — keep in sync manually.

export type LegendEntry = {
  key: string
  label: string
  color: string
  secondColor?: string
  strokeWidth?: number
  shape?: 'line' | 'bar' | 'split' | 'splitLine'
  dashed?: boolean
}

export function ChartLegend({
  items,
  highlighted = null,
  onHighlight,
}: {
  items: LegendEntry[]
  highlighted?: string | null
  onHighlight?: (key: string | null) => void
}) {
  return (
    <div
      style={{
        display: 'flex',
        gap: 18,
        justifyContent: 'center',
        padding: '8px 0 2px',
        fontSize: 13,
      }}
    >
      {items.map((item) => (
        <div
          key={item.key}
          style={{
            display: 'flex',
            alignItems: 'center',
            gap: 6,
            cursor: 'default',
            opacity: highlighted === null || highlighted === item.key ? 1 : 0.3,
            transition: 'opacity 0.15s',
          }}
          onMouseEnter={() => onHighlight?.(item.key)}
          onMouseLeave={() => onHighlight?.(null)}
        >
          {item.shape === 'splitLine' ? (
            <svg width={20} height={14} style={{ flexShrink: 0 }}>
              <line
                x1={0}
                y1={7}
                x2={10}
                y2={7}
                stroke={item.color}
                strokeWidth={item.strokeWidth ?? 2.5}
                strokeDasharray={item.dashed ? '3 2' : undefined}
              />
              <line
                x1={10}
                y1={7}
                x2={20}
                y2={7}
                stroke={item.secondColor}
                strokeWidth={item.strokeWidth ?? 2.5}
                strokeDasharray={item.dashed ? '3 2' : undefined}
              />
            </svg>
          ) : item.shape === 'split' ? (
            <svg width={14} height={14} style={{ flexShrink: 0 }}>
              <defs>
                <clipPath id={`split-top-${item.key}`}>
                  <polygon points="0,0 14,0 0,14" />
                </clipPath>
                <clipPath id={`split-bot-${item.key}`}>
                  <polygon points="14,0 14,14 0,14" />
                </clipPath>
              </defs>
              <rect
                width={14}
                height={14}
                rx={2}
                fill={item.color}
                clipPath={`url(#split-top-${item.key})`}
              />
              <rect
                width={14}
                height={14}
                rx={2}
                fill={item.secondColor}
                clipPath={`url(#split-bot-${item.key})`}
              />
            </svg>
          ) : item.shape === 'bar' ? (
            <span
              style={{
                width: 14,
                height: 14,
                borderRadius: 3,
                backgroundColor: item.color,
                opacity: 0.7,
                flexShrink: 0,
              }}
            />
          ) : (
            <svg width={20} height={14} style={{ flexShrink: 0 }}>
              <line
                x1={0}
                y1={7}
                x2={20}
                y2={7}
                stroke={item.color}
                strokeWidth={item.strokeWidth ?? 2.5}
                strokeDasharray={item.dashed ? '3 2' : undefined}
              />
            </svg>
          )}
          <span>{item.label}</span>
        </div>
      ))}
    </div>
  )
}
