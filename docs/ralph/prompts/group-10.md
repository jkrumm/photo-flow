# Group 10: Pipeline hero — animated Camera → Staging → Final → Publish

## What You're Doing

Build the signature screen: a beautiful, data-dense, framer-motion-animated pipeline visualization
of Camera → Staging → Final → Publish (Homelab backup • photo website), with live counts at each
node (polled `/status` + `/status/pending` + `/backup/availability`), device-connected indicators,
and a "files flowing" animation while a job runs (driven by the SSE stream). Each node is a trigger
affordance into the Operations flow (Group 11).

---

## Research & Exploration First

1. Read `control_panel/PRD.md` → "Screens" #1 (Pipeline hero) and the workflow diagram in the
   project `CLAUDE.md` (Camera → Staging/RAWs/SSD → Final → Backup/Gallery).
2. Read the Group 8 status query + Group 9 token system (`VX.*` colors, `alpha()`).
3. Research `framer-motion` (now `motion`) layout/animation APIs you'll use (`motion.div`,
   `AnimatePresence`, `layout`, spring transitions) — verify the current package name/version.
4. Look at argo's `hero-stats.tsx` (`HeroCard`) for the metric-tile pattern to reuse for node counts.

---

## What to Implement

- A `PipelineHero` route/feature: four stage nodes (Camera, Staging, Final, Publish) connected by
  animated edges. Publish fans into two sub-targets (Homelab backup, photo website).
- **Live data**: each node shows its count — Camera = pending photos/raws/videos (`/status/pending`),
  Staging = `staging_files` (`/status`), Final = index total (`/analytics/summary`), Publish =
  gallery published count + backup freshness (`/backup/availability`). Device dots for camera/SSD
  connected from `/status` (poll ~3s; pending less often, ~15–30s).
- **Animated flow**: subtle particle/edge animation along an edge when its operation is selected or
  running; when an SSE job is active (Group 11 shares the active-job store), animate files moving
  through the relevant edge using the `file_done`/`transfer` events.
- Aesthetic bar is high (this is the hero): smart layout, tasteful motion (spring, not bouncy),
  data-dense but calm, dark-mode-first, uses `VX` tokens only (no raw hex). Concise labels,
  tabular numbers. Make it feel alive but professional.
- Clicking a node/edge opens the corresponding operation (deep-link/route into Group 11's flow).
  If Group 11 isn't built yet, wire the click to a route placeholder.

---

## Validation

```bash
cd control_panel/web && npm run build && npm run lint
```
Strict TS + lint (incl. hex guard) pass. Manually: counts update within polling interval when
status changes; dark/light both look right; no layout jank. Add a light component test if feasible
(render with mocked query data), but visual polish is verified by you running `npm run dev`.

---

## Commit

```
feat(web): animated pipeline hero with live stage counts and flow motion
```

---

## Done

Append notes (the active-job store contract Group 11 will share), then:
```
RALPH_TASK_COMPLETE: Group 10
```
