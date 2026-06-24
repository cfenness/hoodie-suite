# The Spine — integration contract

The spine is the one thing every app stops carrying for itself: a **shared
hierarchy** and a **shared context**, hosted by the shell, broadcast to every app.
Design principle, line one: **absorb the complexity at the spine so each app can be
only itself.**

```
                         ┌──────────────── shell (index.html) ────────────────┐
                         │  hosts the spine · owns the context bar             │
   spine/spine.js  ──►   │  HoodieSpine.host({ hierarchy })                    │
                         │     • holds canonical hierarchy + shared context    │
                         │     • broadcasts context to every iframe            │
                         │     • routes cross-app "navigate" requests          │
                         └───────────────▲───────────────────┬────────────────┘
                                         │ context            │ context
                              setContext │ / nav              ▼ (postMessage)
                         ┌───────────────┴─────────┐   ┌──────────────────────┐
                         │ app (iframe)            │   │ app (iframe)         │
                         │ HoodieSpine.connect()   │   │ HoodieSpine.connect()│
                         └─────────────────────────┘   └──────────────────────┘
```

## The two shared objects

**1 · The canonical hierarchy** (`spine/hierarchy.sample.json`)
The single tree every surface reads. MDM writes it, forecasting decomposes along it,
the dashboard and catman read it.

```
portfolio → brandFamily → brand → productFamily → sku
```

Node shape:
```json
{ "id": "sku-blanco-750", "level": "sku", "name": "Blanco 750ml",
  "parentId": "prf-blanco", "attrs": { "size_ml": 750, "abv": 40, "gtin": "…" },
  "children": [] }
```
`level` is one of `portfolio | brandFamily | brand | productFamily | sku`. The sample
file uses a generic taxonomy (House Alpha / Agave / Silver Tequila / Blanco / 750ml).

**2 · The shared context** — what scope/account/date everyone is looking at:
```js
{
  scope:     { id, level, name } | null,   // current node in the hierarchy
  account:   { id, name } | null,
  dateRange: { basis: "YoY" | "YTD" | "MAT" | "Latest" },
  metric:    "depletions"
}
```

## The message protocol (postMessage)

Every message is `{ __spine:"hoodie:spine", kind, payload }`.

| kind | direction | meaning |
|---|---|---|
| `ready` | app → shell | app announced itself; shell replies with `context` |
| `context` | shell → app | current context + hierarchy (on connect, and on every change) |
| `setContext` | app → shell | app asks to change shared context; shell rebroadcasts to all |
| `nav` | app → shell | app requests a cross-app jump: `{ app, scope }` |

## Joining the spine — the whole adapter

Each app drops this in once. ~10 lines. After that it inherits shared scope,
account, and date basis automatically, and can push changes or deep-link siblings.

```html
<script src="../spine/spine.js"></script>
<script>
  const spine = HoodieSpine.connect({
    name: "Hoodie Intelligence",
    onContext: (ctx) => applyContext(ctx)   // filter your views to ctx.scope / ctx.account / ctx.dateRange
  });

  // push a change up to everyone:   spine.setContext({ scope: {...} })
  // jump to a sibling app at a node: spine.navigate("mdm", ctx.scope)
</script>
```

`apps/spine-adapter.html` is a live reference: open it in the suite, change the
context bar, and watch it receive updates. That is the entire integration, proven
without touching any of the heavy apps.

### Adoption order (incremental — nothing breaks meanwhile)
Apps that haven't adopted the adapter simply ignore the messages. Suggested order,
highest payoff first:
1. **Dashboard** — read `scope` + `dateRange` to filter the active view.
2. **Item MDM** — read `scope` to focus the item set; this is the app that should
   eventually *write* the hierarchy.
3. **CRM** — read `account`.
4. The rest as useful.

## The backend on-ramp

Today the shell hands `host()` an inline hierarchy and holds context in memory. The
contract above does not change when the backend arrives — only the data source:

- **Hierarchy:** `host()` fetches `/api/hierarchy` instead of the sample file. MDM's
  writes go to `/api/hierarchy` (POST), making the item master the system of record
  for the tree the whole suite decomposes along.
- **Context-filtered data:** apps keep calling `applyContext(ctx)`; inside, they fetch
  `/api/<entity>?scope=<id>&basis=<YoY>` instead of reading inline data. The app code
  that *consumes* context is identical; only the fetch target moves.
- **Why this matters:** the apps are already architected to receive absorbed
  complexity. The spine is the joint. When `/api/*` exists (API Gateway + Lambda,
  same CloudFront domain via a `/api/*` behavior), it plugs into joints already
  designed for it — no rewrite, just a feed.

This is the seam where "six apps that share a sidebar" becomes "one platform with a
spine." The hierarchy is the backbone; MDM writes it; forecasting decomposes along
it; every app reads it; and a bad item record will, by construction, propagate into a
visible forecasting consequence — because they are finally looking at the same tree.
