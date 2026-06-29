# Delivery aggregators — the official partner-API path (#2)

The delivery platforms (Instacart, DoorDash, Uber Eats) all **prohibit scraping in their
ToS** and run the heaviest bot protection in retail (DataDome / strict Cloudflare). We've
wired **Instacart via Bright Data's managed dataset** as the pragmatic, sanctioned-vendor
route (connId `instacart` — see `instacart_scraper.py`; it's paid + ToS-gray and the vendor
carries the protection/compliance).

This doc is the **clean alternative**: the official partner/developer APIs. They're
sanctioned, stable, and not a ToS gray area — but **gated behind merchant/partner approval**.
Pursue these if Hoodie (or a brand client) holds, or can obtain, partner status.

---

## TL;DR — what each requires

| Platform | Program | Gets you | Access requirement |
|---|---|---|---|
| **Instacart** | Instacart Developer Platform / Connect | Catalog, items, pricing, store availability | Developer account + approved app; some data is partner/retailer-gated |
| **DoorDash** | DoorDash Developer — Marketplace / Drive | Store catalog + menu/item + availability integrations | Business approval + a technical account manager certifying the integration |
| **Uber Eats** | Uber Eats Marketplace API | Store, menu/items, availability, prices | Uber Eats merchant/integration partner approval (OAuth client) |

Common thread: **you must be (or represent) a merchant/partner**, not an arbitrary third
party. That's the trade vs. scraping — clean + durable, but you need the relationship.

---

## Instacart — Developer Platform / Connect

- **Docs:** https://docs.instacart.com/developer_platform_api/
- **What's available:** product/catalog lookups, recipe→ingredient, shopping-list
  creation; deeper retailer catalog + per-store pricing/availability is gated to
  retailer/partner integrations (Instacart Connect).
- **Auth:** API key per approved app (`Authorization: Bearer …`), REST/JSON.
- **To pursue:** create a developer account, register an app, accept the Developer Platform
  Terms (https://docs.instacart.com/developer_platform_api/guide/terms_and_policies/developer_terms/),
  request the scopes you need. For full per-store catalog you likely need a retailer/partner
  arrangement.
- **What Hoodie needs:** a developer account; for store-level catalog, a partner/retailer
  relationship (or a client who has one).

## DoorDash — Developer (Marketplace / Drive)

- **Docs:** https://developer.doordash.com/ (Marketplace retail integrations:
  https://developer.doordash.com/en-US/docs/marketplace/retail/)
- **What's available:** catalog/menu + order integrations for partnered stores; the retail
  program covers item/inventory/price sync.
- **Auth:** JWT signed with a developer key pair; production access requires DoorDash to
  approve and a technical account manager to certify the integration (testing starts with a
  partner-provided test catalog).
- **What Hoodie needs:** a DoorDash developer account + a business case / merchant
  relationship to get production catalog access.

## Uber Eats — Marketplace API

- **Docs:** https://developer.uber.com/docs/eats (Menu / Store / Order APIs)
- **What's available:** store, menu/items (with prices + availability), order webhooks —
  for integrated merchants.
- **Auth:** OAuth 2.0 client credentials; requires an approved Uber Eats integration
  partner / merchant.
- **What Hoodie needs:** Uber Eats partner approval and an OAuth client.

---

## How this wires into the engine (when access exists)

Same shape as the other connectors — only the fetch changes:

1. A new module per platform (e.g. `instacart_api.py`) that calls the official API with the
   partner credentials from env (`INSTACART_API_KEY`, `DOORDASH_JWT_KEY`, `UBEREATS_OAUTH_*`).
2. Map the response to the **store-level cell** shape the others use:
   snapshot keyed `sku|storeId` → `{price, instock (and qty if exposed), name, store}`.
3. Reuse `abc_fws.diff_snapshots` (or Binny's `diff_store` for numeric qty) → the same
   per-store price-move / in-out / units-moved signal.
4. Add a `*_pull()` in `server.py`, a connId in `/api/health` + `_SRC_LABEL` + the `/api/run`
   dispatch, and a source row in `apps/pulls.html`. Gated on the partner credentials so it's
   inert until configured.

These official APIs are the **durable** store-level path — no bot-wall fragility, no ToS
gray area. The Bright Data managed-dataset route (`instacart` connId) is the bridge until a
partner relationship is in place.

---

## Recommendation

- **Now:** use the Bright Data managed Instacart dataset (`instacart` connId) for store-level
  grocery/liquor product + price — set `BRIGHTDATA_API_KEY` + `BRIGHTDATA_INSTACART_DATASET`
  + `BRIGHTDATA_INSTACART_URLS` (see `instacart_scraper.py`).
- **Durable:** if Hoodie or a brand client can get Instacart/DoorDash/Uber Eats **partner
  status**, switch that platform to its official API (above) — cleaner and not ToS-gray.
- **Don't:** hand-roll scrapers that evade DataDome/Cloudflare on these platforms.
