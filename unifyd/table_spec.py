"""table_spec.py — ONE declaration per table: fields, types, key, stage.

WHY THIS EXISTS. A table's identity was spread across four places that could disagree:

  * `write_partition(name, part, rows, fields, dtypes)`  — schema comes from the CALL SITE
  * `write_accumulate(name, rows, key, fields)`          — key comes from a caller lambda
  * the bucket manifest                                  — holds key_cols + hex_len
  * `ue_catalog.consolidate`                             — hardcodes (store_uuid, item_uuid)

Because the schema belonged to the call site, 32 of 43 `write_partition` calls simply omit
`dtypes`. Each partition then infers its own schema from whatever happened to be in that batch,
and a `union_by_name` read across partitions CORRUPTS rather than fails (an invalid-UTF8 decode
error, not a clean type error). That has already made two tables unreadable:
`ubereats_products_parts` (5 schemas, 2026-07-30) and `retail_observations` (13 schemas across
3,622 partitions / 51.7M rows, 2026-08-03).

The fix is not "remember to pass dtypes". It is that **schema belongs to the table, not to the
caller** (contract C2). A writer that names a spec'd table inherits its schema whether or not it
remembers to ask.

STRINGS, NOT pyarrow TYPES. Types are declared as plain strings so this module imports with no
third-party dependency — it is read by tests, by `tools/data_inventory.py` (ast-only, no creds),
and by `warehouse.py`, which is `sys.path`-imported cross-repo by hoodie-backend. Call
`arrow_dtypes()` to materialise pyarrow types, which imports pyarrow lazily.

COMPUTED NAMES. 30% of write call sites build their table name at run time
(`"%s_products_parts" % site`). A spec keyed only by literal string would miss exactly those, so
families declare a `{site}` pattern and `spec_for()` resolves it.

ADDING A TABLE. Declare it here. `table_spec_test.py` is the ratchet: a `write_partition` call
that neither passes `dtypes` nor names a spec'd table fails the test with file:line.
"""

# Type vocabulary. Keep this small and explicit — every value must map in arrow_dtypes().
STRING, F64, I64, BOOL = "string", "float64", "int64", "bool"

SITES = ("ubereats", "postmates")


class TableSpec:
    """What a table IS, independent of who writes it."""

    def __init__(self, name, stage, fields, dtypes=None, key_cols=(), hex_len=None, note=""):
        self.name = name
        self.stage = stage                      # 0 discover 1 capture 2 consolidate 3 normalize 4 master 5 facts
        self.fields = list(fields)
        # default every declared field to STRING; only deviations need stating
        self.dtypes = {f: (dtypes or {}).get(f, STRING) for f in self.fields}
        self.key_cols = tuple(key_cols)          # identity for merge/dedupe (stage 2+)
        self.hex_len = hex_len                   # bucket width; None = warehouse default
        self.note = note

    def __repr__(self):
        return "TableSpec(%s, stage=%s, %d fields)" % (self.name, self.stage, len(self.fields))


# ---------------------------------------------------------------------------------------------
# Uber platform (UberEats + Postmates are the SAME BFF on two domains — one schema, two sites)
# ---------------------------------------------------------------------------------------------
# Mirrors ue_catalog.PRODUCT_FIELDS minus raw_json ("lean"). Declared here rather than imported so
# this module stays dependency-free; table_spec_test.py asserts the two never drift apart.
_UBER_PRODUCT_FIELDS = [
    "store_uuid", "store_name", "source", "item_uuid", "name", "brand", "upc", "gtin",
    "price", "list_price", "promo", "size", "abv", "in_stock", "stock_label", "category",
    "section", "subsection", "section_name", "subsection_name", "category_path",
]
# price/list_price/abv are numeric; in_stock is boolean; promo stays STRING here (it carries the
# retailer's promo TEXT at this grain — retail_observations splits it into a number + text).
_UBER_PRODUCT_DTYPES = {"price": F64, "list_price": F64, "abv": F64, "in_stock": BOOL}


def _uber_parts_spec(site):
    return TableSpec(
        "%s_products_parts" % site, stage=1,
        fields=_UBER_PRODUCT_FIELDS, dtypes=_UBER_PRODUCT_DTYPES,
        key_cols=("store_uuid", "item_uuid"),
        note="stage-1 append-only parts: one file per run x shard x batch. Written by BOTH "
             "ue_catalog._land and ue_enrich._flush — ue_enrich omitted dtypes, which is the "
             "2026-07-30 5-schema corruption. Never merged in place; the fold consumes it.")


def _uber_catalog_spec(site):
    return TableSpec(
        "%s_products" % site, stage=2,
        fields=_UBER_PRODUCT_FIELDS, dtypes=_UBER_PRODUCT_DTYPES,
        key_cols=("store_uuid", "item_uuid"),
        note="stage-2 aggregate. ONE writer: the fold (ue_catalog.consolidate). A zone crawl "
             "writing this directly is contract C1's failure mode — observed live 33,250 -> 8,798 "
             "rows via unlocked write_accumulate. It is also the COST control: known_items() reads "
             "this table to decide which items enrichment may skip, so rows lost here are re-fetched "
             "at ~30M requests.")


# ---------------------------------------------------------------------------------------------
# Cross-source facts
# ---------------------------------------------------------------------------------------------
# Canonical schema for retail_observations. NOTE: observe.record() — its ONLY production writer —
# already pins these types. Its 13 historical schemas are LEGACY FILES from before that pin, so its
# remedy is tools/repair_partitions.py (rewrite history), not a write-path change. Declared here so
# observe.py, repair_partitions.py and any reader share one definition instead of three copies.
_OBS = TableSpec(
    "retail_observations", stage=5,
    fields=["date", "observed_at", "source", "chain", "store", "store_id", "product_id", "upc",
            "gtin", "brand", "name", "price", "promo", "promo_text", "on_promo", "in_stock",
            "qty", "stock_level", "is_hemp"],
    dtypes={"observed_at": I64, "price": F64, "promo": F64, "qty": F64,
            "on_promo": BOOL, "in_stock": BOOL, "is_hemp": BOOL},
    key_cols=("date", "source", "store_id", "product_id"),
    note="STAGE 5, fed from stage 1 by observe.record() — NOT a per-source aggregate. A source must "
         "not declare it in tables=[...]: it moves whenever ANY source runs, so it makes that "
         "source's landing delta meaningless (this is why ubereats-enrich could never report ok).")


# ---------------------------------------------------------------------------------------------
# Registry
# ---------------------------------------------------------------------------------------------
SPECS = {_OBS.name: _OBS}
for _s in SITES:                                  # expand the platform families
    for _spec in (_uber_parts_spec(_s), _uber_catalog_spec(_s)):
        SPECS[_spec.name] = _spec


def spec_for(name):
    """The spec for a table name, or None if it has not been declared yet.

    Returns None rather than raising: adoption is incremental, and an undeclared table must keep
    working exactly as it does today. The ratchet test — not this function — is what stops NEW
    unpinned writes from landing.
    """
    return SPECS.get(name)


def arrow_dtypes(name_or_spec):
    """{field: pyarrow type} for a spec, or None if the table is not declared.

    pyarrow is imported lazily so this module stays importable in credential-free, dependency-free
    contexts (ast tooling, offline tests).
    """
    spec = name_or_spec if isinstance(name_or_spec, TableSpec) else spec_for(name_or_spec)
    if spec is None:
        return None
    import pyarrow as pa
    m = {STRING: pa.string(), F64: pa.float64(), I64: pa.int64(), BOOL: pa.bool_()}
    return {f: m[t] for f, t in spec.dtypes.items()}


def fields_for(name):
    """Declared field order for a table, or None if undeclared."""
    spec = spec_for(name)
    return list(spec.fields) if spec else None
