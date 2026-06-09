# Group 12: Analytics + Library health UI

## What You're Doing

Build the Analytics screen over the Group 7 endpoints using the vendored visx charts, plus a
Library-health view. Data-dense, beautiful, dark-mode-first — the kind of "insights" dashboard the
user wants: when photos were taken, how many, rating distribution, camera-setting habits, storage,
and a map of where photos were shot.

---

## Research & Exploration First

1. Read `control_panel/PRD.md` → "Screens" #3 (Analytics) and #4 (Library health).
2. Read the Group 7 endpoints: `/analytics/over-time`, `/ratings`, `/settings`, `/storage`, `/map`,
   `/summary`; `/backup/availability`; the orphaned-RAW info.
3. Read the vendored charts (`src/charts/`): which `kinds/` exist (`Bars`, `ZonedLine`,
   `StackedArea`, `Donut`) + `ChartCard`/`ChartLegend`/`ChartTooltip`. Build on these, not raw visx.
   Add a new "kind" only if a chart is the 2nd instance of a pattern (Rule of Three).
4. For the GPS map, research a lightweight React map option that fits (e.g. an existing dep or a
   simple tile map); if it adds heavy deps, render a simpler geo-scatter and note the map as
   deferred — don't bloat the bundle.

---

## What to Implement

- **Summary tiles** (argo `HeroCard` pattern): total photos, published, this-month, avg rating, date range.
- **Photos over time**: bar or stacked-area by day/week/month/year (bucket toggle) with a
  rating-band overlay (`<4` vs `≥4`). Use `Bars`/`StackedArea` kind + `AxisBottomDate`.
- **Rating distribution**: bar/donut of 0–5, Final-total vs published.
- **Camera settings**: ISO / aperture / focal-length / shutter distributions (small-multiple bars).
- **Storage by stage**: Final/Staging/RAWs/Videos sizes + counts.
- **Map**: photos with GPS plotted (or geo-scatter fallback).
- **Library health**: orphaned RAWs, backup freshness (local vs remote counts from
  `/backup/availability`), last sync/backup info. Actionable (link cleanup into Group 11's flow).
- All charts: `ChartCard` wrappers, `VX` tokens only, `useVxTheme`/CSS-var theming, `alpha()` for
  opacity. Respect the visx-charts rule conventions. Loading skeletons; empty states for a fresh index.

---

## Validation

```bash
cd control_panel/web && npm run build && npm run lint
```
Strict TS + lint (incl. hex guard) pass. Manually (`npm run dev` against the API with a populated
index): charts render correct shapes, bucket toggle works, dark/light both clean, no raw-hex lint
violations. Component tests for the data-shaping helpers (bucketing/normalizing API → chart props)
where practical.

---

## Commit

```
feat(web): analytics dashboard + library health over the metadata index
```

---

## Done

Append notes (any deferred map work), then:
```
RALPH_TASK_COMPLETE: Group 12
```
