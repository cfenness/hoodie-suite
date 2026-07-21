---
name: smoke
description: Suite smoke test — prove every registered app loads clean. Static layer (tools/smoke_check.py) + runtime browser pass (console errors, blank renders, composite tabs). Run before "ship it" and after any shell/spine/app change.
---

# Suite smoke test

Two layers, in order. The static layer is deterministic and always runs in full; the runtime layer
uses the Browser pane and scales its depth to what changed. **Never report "smoke passed" unless both
layers actually ran — and report exactly what was and wasn't covered.**

## Layer 1 — static (always, first)

```bash
python3 tools/smoke_check.py
```

Proves: every `APPS` entry's file exists, no duplicate ids, groups are declared, every registered page
serves over HTTP with real content, every local src/href/iframe reference resolves, the spine serves.
Exit 1 = failures; each names the exact file and reference. Fix failures before proceeding — the runtime
layer assumes the wiring is sound. Orphan warnings are report-only (surface them, don't block).

## Layer 2 — runtime (browser pane)

Static checks can't see JS errors, failed fetches, or a page that serves 200 but renders blank.

1. `preview_start` with `{name: "suite-static"}` (plain static server on :8130 — no engine needed).
   Use `{name: "unifyd-local"}` instead when the change touches `/api/*` behavior (mdm, data-console,
   connectors need the agent for live data — on suite-static they must still RENDER, with their
   fallback/empty states, not error).
2. Pick the pass depth:
   - **Targeted** (an app or two changed): the changed apps + the shell + any composite that hosts them.
   - **Full** (shell, spine, or CSS-token change): every entry in the `APPS` array in `index.html`,
     plus the composite surfaces `sources.html` and `mdm.html` (click through their tabs — lazy-mounted
     iframes only fail when actually mounted).
3. For each page: `navigate` to it, then
   - `read_console_messages` with `onlyErrors: true` — any error = a finding. Ignore only errors that
     are provably environmental (e.g. `/api/*` connection-refused on suite-static); say so explicitly.
   - `read_page` — confirm real content rendered (a header/nav alone with an empty body = blank-render
     finding).
   - Apps loaded via the shell (`index.html#<id>`) must also show the shell chrome intact.
4. Anything found: diagnose in source, fix, re-run BOTH layers on the affected pages.

## Report

One table: page · static · console · render · verdict. Below it: findings (file:line + what broke),
what was fixed, and — honestly — anything not covered (e.g. "api-backed tabs checked in fallback mode
only; agent wasn't running"). A page that wasn't checked is reported as NOT CHECKED, never implied clean.
