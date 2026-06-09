/**
 * VX tokens — CSS-variable refs for photo-flow charts.
 *
 * Vendored from argo/packages/charts/src/tokens.ts, adapted for photo-flow series.
 * Use VX.* in chart files — never raw hex values.
 */
export const VX = {
  lineWidth: 2.5,
  line2Width: 2,
  axisFont: 11,
  dotR: 5,

  line2Dark: 'var(--vx-line2)',

  good: 'var(--vx-good)',
  goodSoft: 'var(--vx-goodSoft)',
  bad: 'var(--vx-bad)',
  warn: 'var(--vx-warn)',
  goodSolid: 'var(--vx-goodSolid)',
  badSolid: 'var(--vx-badSolid)',
  warnSolid: 'var(--vx-warnSolid)',
  goodRef: 'var(--vx-goodRef)',
  badRef: 'var(--vx-badRef)',
  warnRef: 'var(--vx-warnRef)',
  optimalZone: 'var(--vx-optimalZone)',

  line: 'var(--vx-line)',
  grid: 'var(--vx-grid)',
  crosshair: 'var(--vx-crosshair)',
  dotStroke: 'var(--vx-dotStroke)',
  legendText: 'var(--vx-legendText)',
  neutral: 'var(--vx-neutral)',

  surface: {
    bg: 'var(--vx-surface-bg)',
    panel: 'var(--vx-surface-panel)',
    elevated: 'var(--vx-surface-elevated)',
    border: 'var(--vx-surface-border)',
  },
  shadowCard: 'var(--vx-shadowCard)',

  status: {
    excellent: 'var(--vx-status-excellent)',
    good: 'var(--vx-status-good)',
    warn: 'var(--vx-status-warn)',
    bad: 'var(--vx-status-bad)',
    neutral: 'var(--vx-status-neutral)',
  },

  /** Photo-flow pipeline stage identity colors. */
  photo: {
    camera: 'var(--vx-photo-camera)',
    staging: 'var(--vx-photo-staging)',
    final: 'var(--vx-photo-final)',
    published: 'var(--vx-photo-published)',
    raws: 'var(--vx-photo-raws)',
    videos: 'var(--vx-photo-videos)',
    rating1: 'var(--vx-photo-rating1)',
    rating2: 'var(--vx-photo-rating2)',
    rating3: 'var(--vx-photo-rating3)',
    rating4: 'var(--vx-photo-rating4)',
    rating5: 'var(--vx-photo-rating5)',
  },

  margin: { top: 12, right: 16, bottom: 30, left: 44 },
  minPxPerTick: 55,
} as const
