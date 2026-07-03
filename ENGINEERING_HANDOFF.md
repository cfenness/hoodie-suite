# Engineering Handoff — from static suite to a team-owned app

**Thesis:** the durable, valuable parts are *already* separated — the **backend service**
(`/api/*`), the **domain logic** (data-quality engine, the "book" model, the connectors,
the vision/pitch brains), and the **contracts** (the spine protocol, the `/api` schema).
What has to change to hand this to a team is the **frontend delivery**: single-file HTML
apps → a componentized codebase with build tooling, a shared design system, types, and
tests. **This is a migration, not a rewrite.** The API and domain layers carry over intact.

---

## 1. What exists today (what you're handing over)

- **Static suite** — `index.html` launcher + self-contained single-file HTML apps under
  `apps/*.html`. Zero build step, served static. The `APPS` array in `index.html` is the
  registry; every app is an independent, pluggable surface.
- **Spine** (`spine/spine.js`) — a `postMessage` protocol for cross-app context (scope,
  account, date basis, metric) + navigation. **This is the integration contract** (see
  `SPINE.md`).
- **Backend engine** (`unifyd/`, Flask on Fly) — already a real service. Endpoints:
  `/api/health`, `/api/analyze` (data-reader), `/api/prism` (the book model),
  `/api/benchmark` · `/api/shelf-vision` · `/api/pitch` (planogram brains),
  `/api/places` · `/api/places/enrich` (restaurant connector), `/api/hierarchy` ·
  `/api/datasets` · `/api/runs` · `/api/run` (MDM/pulls). Storage: Parquet on **Tigris**
  queried by **DuckDB** (`warehouse.py`); scrapers/connectors (`places.py`, `poi.py`, the
  chain scrapers). **Google OIDC** auth gate (`auth_gate.py`).
- **Deploy** — Fly all-in-one (static + `/api` same origin, no CORS), OIDC-gated.
- **Already-libraryish** — `dq.js`/`dq_frontier.js` (data-quality engine, **80+23 passing
  node tests**), the Prism model (`prism.py`), the connector stack.

**Strengths:** fast iteration, no toolchain, works today, demoable.
**Limits for a team:** single-file HTML doesn't scale to multiple engineers — merge
conflicts, no component reuse, no type safety, no UI test harness, duplicated design
tokens per file, no dependency management, hard to enforce consistency.

## 2. Target architecture

A monorepo (pnpm/turborepo):

```
apps/
  web/        Next.js (React) — the suite + desktop apps
  mobile/     Expo (React Native) — real iOS/Android apps
packages/
  ui/         design system: tokens + components (replaces copy-pasted CSS)
  domain/     dq engine, book model, formatting, spine client — TypeScript, tested
  api-client/ typed client for /api/* (generated from an OpenAPI schema)
server/       the Flask engine (unchanged short-term)
```

- **Backend stays.** The Flask engine is a real service; engineers *consume* `/api/*`, they
  don't rewrite it. Later, optionally, add an OpenAPI schema and consider FastAPI for
  end-to-end types.
- **Web:** Next.js/React. Each current single-file app becomes a route composed of shared
  components. `spine.js` becomes a small typed client in `packages/domain`.
- **Mobile:** Expo/React Native rendering the **same** `/api` contracts. The Prism mobile
  PWA already proved this — its value is the `/api/prism` contract, which RN reuses. The
  planogram "snap the shelf" camera flow is where native genuinely beats web.
- **Design system:** extract the recurring tokens (the ink/paper/amber palette + the
  Tableau-crisp variant from Prism, spacing, type) into `packages/ui`. Kills per-file CSS.
- **Types + tests:** TypeScript across packages; the dq node tests port directly.

## 3. Migration strategy — strangler-fig, no big-bang

The `APPS` array already treats every surface as independent. Exploit that:

1. **Stand up the monorepo shell** (Next web app) that embeds existing single-file apps via
   iframe — exactly like today's launcher. **Day 1 nothing breaks**; the new shell hosts the
   old apps. (The `apps/device-preview.html` tool already demonstrates iframe-hosting apps
   at arbitrary sizes.)
2. **Extract the design system** from the most-polished surfaces (Prism's Tableau theme is a
   good seed) into `packages/ui`.
3. **Migrate app-by-app**, highest-value first, from single-file HTML to React pages using
   `packages/ui` + `api-client`. Each migration is independent and shippable; the iframe
   fallback covers the not-yet-migrated apps.
4. **Extract domain logic** as you go — `dq.js` → `packages/domain`, `spine.js` → typed
   client. (The book model already lives server-side in `prism.py`.)
5. **Mobile in parallel** — Expo app on `/api`, starting with Prism (contract exists), then
   planogram camera.
6. **Retire the static suite** once parity is reached; keep the Flask engine.

## 4. The handoff packet

- This doc + `SPINE.md` (integration contract) + `PLACES.md` (connector) + `README.md` +
  the `/api` surface (§1). **First eng task: generate an OpenAPI spec for `/api/*`** →
  typed client for both web and mobile.
- **Auth:** Google OIDC gate is built (`auth_gate.py`). Web/PWA reuse the cookie session;
  for RN, add a token-based path (Expo AuthSession) against the same Google client.
- **Deploy:** Fly all-in-one today; can split web/api later without touching the contract.

## 5. Decisions (with recommendations)

| Decision | Recommendation | Why |
|---|---|---|
| Web framework | **Next.js (React)** | Ecosystem, routing, optional SSR, easy Fly/Vercel deploy |
| Mobile | **Expo (React Native)** | Faster than bare RN, OTA updates, simple builds; bare only if a native module demands it |
| Language | **TypeScript everywhere** | The domain logic (dq, book model) benefits most |
| Backend | **Keep Flask** short-term; FastAPI later | It works; move only when the team wants end-to-end types/OpenAPI |
| Data fetching | **TanStack Query** | Caching/retries for `/api`; light global state (Zustand) for spine context |
| Styling | Tailwind or vanilla-extract in `packages/ui` | Real theme instead of per-file CSS |

## 6. First two weeks for the eng team

1. Monorepo + Next shell that iframes existing apps (**parity day 1**).
2. OpenAPI spec for `/api/*` + generated typed client.
3. `packages/ui` seeded from Prism's theme; migrate **one** app (Prism-web or CRM) as the
   reference migration.
4. Expo skeleton rendering `/api/prism` (mobile Prism v2).
5. CI: typecheck + the ported dq tests.

## 7. What NOT to throw away

- The **Flask engine + connectors** (`places`/`warehouse`/`poi`/`planogram`/`analyze`) —
  real, working backend.
- The **dq engine + tests**, the **book model**, the **spine protocol** — port, don't rewrite.
- The **OIDC auth**, the **Fly deploy**, and the **design language** (the Tableau-crisp Prism
  theme is the seed of the design system).

The point: engineers get a codebase they *extend*, with the domain value — data quality,
the book model, the restaurant connector, the vision/pitch brains — intact.
