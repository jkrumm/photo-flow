# Group 9: Vendor the argo charts package + Blueprint token system

## What You're Doing

Copy argo's `@argo/charts` visx package and its Blueprint CSS-variable token system into the SPA,
wire the Mantine↔charts bridge, and prove a sample chart renders in both light and dark mode. This
is a self-contained vendoring group (different files than Group 8's shell) — it gives Groups 10–12
the chart primitives + token palette to build on.

---

## Research & Exploration First

1. Read `control_panel/PRD.md` → "Frontend" (vendoring note) and the project's visx-charts rule
   conventions (`~/.claude/rules/visx-charts.md` if available — ChartCard/ChartLegend/ChartTooltip,
   tokens/palette CSS-var architecture, `alpha()` not `rgba`).
2. Read the real argo source to copy:
   `/Users/johannes.krumm/SourceRoot/argo/packages/charts/src/` (whole tree: `primitives/`,
   `hooks/`, `kinds/`, `sparklines/`, `utils/`, `tokens.ts`, `palette.ts`, `theme-vars.ts`,
   `theme.tsx`, `index.ts`) and `apps/dashboard/src/charts-bridge.tsx` (`VxBridge`).

---

## What to Implement

1. **Vendor the package** into `control_panel/web/src/charts/` (or a local workspace
   `packages/charts` if you prefer — simplest is an in-app `src/charts/` folder). Copy the files
   verbatim, then adapt:
   - Keep the CSS-var architecture (`palette.ts` → `theme-vars.ts` emits `--vx-*` →
     `tokens.ts` `VX` refs). The only external coupling is the `[data-mantine-color-scheme]`
     selector — keep it (you kept Mantine in Group 8).
   - Add a header comment to each vendored file: `// Vendored from argo/packages/charts —
     keep in sync manually.`
2. **Bridge**: copy `charts-bridge.tsx` (`VxBridge`) — reads `useMantineColorScheme()`, injects
   `<style>{PALETTE_CSS}</style>` once, wraps children in `VxThemeProvider`. Mount it in `main.tsx`
   (the only file allowed to import both `@mantine/*` and the charts package).
3. **Lint guard** (per the visx rule): add the tiny raw-hex/`rgb()`/`hsl()` guard script over the
   charts + app source (exempt the palette/token files) and wire it into `npm run lint`.
4. **Prove it**: render one sample chart (e.g. a `Bars` or `ZonedLine` kind with dummy data) on the
   Analytics placeholder route; verify it themes correctly when toggling Mantine color scheme.

---

## Validation

```bash
cd control_panel/web && npm run build && npm run lint
```
The lint guard must pass (no raw hex in chart usage). Build (strict TS) must pass with the vendored
package. Manually toggle dark/light and confirm the sample chart restyles via CSS vars (no JS
branch on scheme).

---

## Commit

```
feat(web): vendor argo visx charts + Blueprint token system with theme bridge
```

---

## Done

Append notes (where you put the package, the hex-guard script), then:
```
RALPH_TASK_COMPLETE: Group 9
```
