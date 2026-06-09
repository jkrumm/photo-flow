# Group 8: SPA scaffold — Vite + React 19 + Mantine + TanStack, strict tooling, typed API client

## What You're Doing

Create the `control_panel/web/` Vite React SPA with **strict TypeScript and lint from line one**
(greenfield — strict now, no cascade later), the Mantine app shell, TanStack Router + Query, a
typed API client generated from FastAPI's OpenAPI, and PWA support. It renders a working shell that
talks to `/health` and `/status`. Charts/vendoring come in Group 9.

This is the strictness-baseline group for the frontend — establish the rules before any feature code.

---

## Research & Exploration First

1. Read `control_panel/PRD.md` → "Frontend" section (stack + decisions).
2. Read argo's frontend setup for patterns to mirror (read the real files):
   `/Users/johannes.krumm/SourceRoot/argo/apps/dashboard/`: `vite.config.ts`, `tsconfig.app.json`,
   `package.json`, `src/main.tsx`, `src/theme.ts`, `src/lib/query-client.ts`, `src/lib/store.ts`,
   `src/components/app-shell/*`, `routes/__root.tsx`, `styles/native.css`, `index.html`
   (anti-flash scheme script).
3. Research `openapi-typescript` + `openapi-fetch` usage (Context7/web) — generate types from a
   running FastAPI's `/openapi.json`.

---

## What to Implement

### 1. Vite project (`control_panel/web/`)

- React 19 + `@vitejs/plugin-react` (+ React Compiler via `babel-plugin-react-compiler`),
  TanStack Router plugin, `vite-plugin-pwa`. Match argo's versions (see its package.json).
- **Strict `tsconfig`**: `strict: true`, `noUncheckedIndexedAccess`, `noUnusedLocals/Parameters`,
  `exactOptionalPropertyTypes`. Lint: use the same linter argo uses (read its config) — copy it.
- `npm run build` (= `tsc && vite build`), `npm run lint`, `npm run dev` scripts. `--strictPort`,
  dev server on a fixed port (e.g. 7721) with a proxy: `/api`, `/events`, `/health`, `/status`,
  `/ops`, `/jobs`, `/analytics`, `/index` → `http://127.0.0.1:7720`.

### 2. Typed API client

- Add a script `gen:api` running `openapi-typescript http://127.0.0.1:7720/openapi.json -o
  src/lib/api-types.ts` (FastAPI must be running; document this in a README). Commit a generated
  snapshot so the build doesn't require a live server.
- `src/lib/api.ts`: thin typed `fetch`/`openapi-fetch` client (base `/` same-origin or
  `VITE_API_URL`). `src/lib/query-client.ts` + `src/lib/queries/` factory pattern (copy argo's).

### 3. App shell + theme

- Copy argo's Mantine theme (`theme.ts`, Blueprint reskin) and app-shell components
  (`app-shell/*`, `__root.tsx`, `native.css`, anti-flash `index.html` script). Adapt routes to:
  **Pipeline**, **Operations**, **Analytics**, **Library** (empty placeholders for now).
- `src/main.tsx` provider wiring (Mantine → ErrorBoundary → Notifications → Modals → QueryClient →
  Router). Add `framer-motion` to deps now (used in Group 10).
- A working **Status** indicator in the shell that polls `GET /status` every ~3s (TanStack Query
  `refetchInterval`) and shows camera/SSD connected dots + staging count.

---

## Validation

```bash
cd control_panel/web && npm install && npm run build && npm run lint
```
(The build is the typecheck gate — strict TS must pass.) No backend needed for the build if the
generated `api-types.ts` snapshot is committed. Manually confirm `npm run dev` proxies to the API.

---

## Commit

```
feat(web): scaffold strict Vite/React/Mantine SPA with typed API client and app shell
```

---

## Done

Append notes (versions pinned, linter chosen, proxy ports), then:
```
RALPH_TASK_COMPLETE: Group 8
```
