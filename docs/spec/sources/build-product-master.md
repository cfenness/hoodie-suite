# Product master (dim_sku chain) — `build-product-master`

> BUILD (derives from tables we already hold)

## 1. The contract

|  |  |
|---|---|
| Registry id | `build-product-master` |
| Runs | `import build_product_master as m; m.build()` |
| Module | `unifyd/build_product_master.py` — 868 lines |
| Cadence | every 12h |
| Enabled | **yes** |
| Executor class | `build` |
| Cost class | — |
| Memory / timeout | 16384 MB / — s |
| Shards | 1 |
| Credentials required | none |
| Capabilities | none |
| Unit test | **none** |


**Registry note.** brand dict → stage → shred to dim_brand/product/item/sku + xwalk/coherence/identity clusters


## 2. Transport

_No literal endpoint constant in `build_product_master.py`._ The transport is either inherited from a shared fetcher or built at run time — read the module.


**Depends on** `category_tree`, `class_type`, `dict_apply`, `hoodie_ids`, `identity_resolve`, `master_apply`, `normalize`, `placeholders`, `precleanse`, `price_signal`, `sku_match`, `upc`, `warehouse`, `wb_views`


## 3. What it lands


### `dim_sku`

1,180,839 rows · 14 columns


| column | type |
|---|---|
| `sku_key` | `VARCHAR` |
| `item_key` | `VARCHAR` |
| `pack` | `BIGINT` |
| `upc` | `VARCHAR` |
| `gtin` | `INTEGER` |
| `vintage` | `VARCHAR` |
| `edition` | `INTEGER` |
| `resolved_id` | `VARCHAR` |
| `source_rows` | `BIGINT` |
| `sources` | `BIGINT` |
| `source_list` | `VARCHAR[]` |
| `master_created_at` | `INTEGER` |
| `master_updated_at` | `INTEGER` |
| `updated_by` | `VARCHAR` |


## 4. `build_product_master.py` — the module's own account

> Verbatim from the source. This is the design note, not a summary of it.


```text
build_product_master.py — the canonical product-master build (brand-dictionary quality).

The generic mapping engine (master_apply.build over field_mappings.json) can't do a longest-match brand
lookup in SQL, so the price-list descriptions ("COTE ROTIE - DOMAINE …") get mangled brands. This builder
adds the quality the master needs:
  1. Build a BRAND DICTIONARY from the sources that HAVE a real brand column (Kroger/Walmart/NC/Binny's/
     Target/Spec's/ABC-inventory) — distinct brands seen >=2x, indexed by first token, longest-first.
  2. For every source, resolve the brand: the clean brand column if present, else the LONGEST dictionary
     brand the description starts with, else the first 1-2 words. Strip size/proof from product_name.
     Alcohol-filter each catalog. Land _stage_product, then shred via master_apply.resolve_hierarchy into
     dim_brand / dim_product / dim_item / dim_sku.

    python build_product_master.py        # rebuilds the product master in the warehouse
```


## 5. Raw source fields

**No raw-field inventory exists for this source.** `unifyd/source_spec.py` documents the verbatim fields a source emits and which of them we promote — it covers 13 of the 74 sources. Until this one is added, the landed columns above are what we know we keep, and what the source offers that we DROP is unrecorded.
