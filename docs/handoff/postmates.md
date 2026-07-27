# Postmates — Scraper Handoff

> The **same Uber BFF** as Uber Eats — identical `getStoreV1` / `getMenuItemV1` schemas. Only the
> domain differs (`postmates.com`). The entire recipe, parser, and field set are reused.

| | |
|---|---|
| **Status** | Live (same engine as Uber Eats) |
| **Registry id** | `postmates` |
| **Entrypoint** | `import ubereats as m; m.main(['--site','postmates','--max-stores','1000'])` |
| **Class / cadence** | `mac` / daily |
| **Lands to** | `postmates_products` + `retail_observations` (channel=postmates) |
| **Inventory signal** | Bounded on-hand proxy (`max_qty`) |
| **Key files** | `ubereats.py` (shared), `ue_crawl.py` (shared, `--site postmates`) |

## What we can accomplish

Everything the [Uber Eats](uber-eats.md) file describes, on the Postmates footprint. Postmates runs
on the identical Uber first-party BFF; `SITES["postmates"]` swaps the domain, feed base, and the
`pl=` zone (the same base64 location works on both). Because they overlap heavily, treat Postmates as
a **second channel on the same engine**, not a separate build — the value is cross-channel price
comparison, not additional catalog breadth.

## Access, levels & fields

**Identical to Uber Eats** — see [uber-eats.md](uber-eats.md) for the full three-level pull
(`getFeedV1` → `getStoreV1` → `getMenuItemV1`), the complete field schema, and the sample record.
Every column and meaning is the same; records land keyed by `store_uuid` + `item_uuid` to
`postmates_products`.

## Traversal & scale

Same as Uber Eats, with `--site postmates`:
- `ue_sitemap.py postmates` → the Postmates account universe (`postmates_sitemap`).
- `ue_geofill.py postmates` → geocode from each store page's JSON-LD (`postmates_geo`).
- `ue_crawl.py --site postmates --coverage` / `--deep-stores` → zone-bound feed + deep crawl.

## Gotchas

- Every Uber Eats gotcha applies verbatim (headless-dead, real-Chrome, click-through, `x-uber-*` header replay, zone stickiness, home-IP-default, `max_qty` proxy).
- **Land to separate channel tables.** Uber Eats and Postmates share the BFF but must land to distinct `*_products` tables and carry distinct `channel=` values — the whole point is the per-channel markup delta.
- Coverage overlaps Uber Eats heavily; if compute is tight, prioritize Uber Eats and run Postmates as the price-comparison channel on the merchants that matter.

## Sample record

> **REPRESENTATIVE** — real field names & types, illustrative values. Same schema as Uber Eats; only store/channel differ. Shown abbreviated.

```json
{
  "item_uuid": "a72f...",
  "product_uuid": "3b81...",
  "store_uuid": "e4c0...",
  "store_name": "Total Wine & More",
  "name": "Josh Cellars Cabernet Sauvignon 750 ml",
  "upc": "083085918",
  "gtins": "083085918|00083085918",
  "price": 15.99,
  "list_price": 17.99,
  "on_promo": true,
  "discount": 2.0,
  "promo_tag": "11% off",
  "in_stock": true,
  "is_sold_out": false,
  "stock_label": "Many in stock",
  "max_qty": 100,
  "sold_by": "COUNT",
  "is_alcohol": true,
  "num_alcoholic": 1,
  "abv": 13.5,
  "item_size": "750 ml",
  "zone": "orlando",
  "raw_json": "{...}"
}
```


---
*Part of the Hoodie Suite scraper cutover pack. See [`README.md`](README.md) for the shared architecture, anti-bot infrastructure, and standing rules that apply to every source.*
