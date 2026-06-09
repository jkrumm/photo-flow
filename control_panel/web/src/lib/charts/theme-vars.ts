/**
 * theme-vars.ts — emits CSS custom properties for VX tokens.
 *
 * Vendored from argo/packages/charts/src/theme-vars.ts, adapted for photo-flow series.
 * The dashboard injects PALETTE_CSS once via charts-bridge.tsx.
 */
import { PHOTO, SEMANTIC, STATUS, NEUTRAL, SURFACE, type ColorPair } from './palette'

type Side = 'light' | 'dark'

const decl = (name: string, value: string) => `  --vx-${name}: ${value};`

const group = (prefix: string, g: Record<string, ColorPair>, side: Side): string =>
  Object.entries(g)
    .map(([k, v]) => decl(`${prefix}${k}`, v[side]))
    .join('\n')

function primitives(side: Side): string {
  const n = NEUTRAL
  const s = SEMANTIC
  const su = SURFACE
  return [
    group('photo-', PHOTO as Record<string, ColorPair>, side),
    group('status-', STATUS as Record<string, ColorPair>, side),
    decl('goodSolid', s.good[side]),
    decl('badSolid', s.bad[side]),
    decl('warnSolid', s.warn[side]),
    decl('neutral', n.neutral[side]),
    decl('shadowCard', side === 'dark' ? 'none' : '0 1px 3px rgba(17,20,24,0.06)'),
    decl('line', n.line[side]),
    decl('line2', n.line2[side]),
    decl('axis', n.axis[side]),
    decl('axisStroke', n.axisStroke[side]),
    decl('grid', n.grid[side]),
    decl('crosshair', n.crosshair[side]),
    decl('dotStroke', n.dotStroke[side]),
    decl('tooltipBg', n.tooltipBg[side]),
    decl('tooltipText', n.tooltipText[side]),
    decl('tooltipMuted', n.tooltipMuted[side]),
    decl('tooltipBorder', n.tooltipBorder[side]),
    decl('tooltipShadow', n.tooltipShadow[side]),
    decl('legendText', side === 'dark' ? 'rgba(255,255,255,0.92)' : 'rgba(17,20,24,0.82)'),
    decl('surface-bg', su.bg[side]),
    decl('surface-panel', su.panel[side]),
    decl('surface-elevated', su.elevated[side]),
    decl('surface-border', su.border[side]),
  ].join('\n')
}

const DERIVED = [
  decl('area-top', '22%'),
  decl('area-bottom', '1%'),
  decl('good', 'color-mix(in srgb, var(--vx-goodSolid) 18%, transparent)'),
  decl('goodSoft', 'color-mix(in srgb, var(--vx-goodSolid) 8%, transparent)'),
  decl('bad', 'color-mix(in srgb, var(--vx-badSolid) 18%, transparent)'),
  decl('warn', 'color-mix(in srgb, var(--vx-warnSolid) 8%, transparent)'),
  decl('goodRef', 'color-mix(in srgb, var(--vx-goodSolid) 30%, transparent)'),
  decl('badRef', 'color-mix(in srgb, var(--vx-badSolid) 30%, transparent)'),
  decl('warnRef', 'color-mix(in srgb, var(--vx-warnSolid) 20%, transparent)'),
  decl('optimalZone', 'color-mix(in srgb, var(--vx-goodSolid) 10%, transparent)'),
].join('\n')

export const PALETTE_CSS = `:root {
${DERIVED}
}
:root,
html[data-mantine-color-scheme='dark'] {
${primitives('dark')}
}
html[data-mantine-color-scheme='light'] {
${primitives('light')}
}`
