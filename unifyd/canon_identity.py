"""canon_identity.py — the coverage-aware canon-identity overlay for serving (S4 slice 2).

The matching-convergence cutover (MATCHING-CONVERGENCE.md): hoodie-canon computes the authoritative
identity and lands it as `item_identity` (source, product_id -> canon_item_id) via
`ingest_canon_identity.py`. Canon's identity has far better recall than unifyd's md5 `item_key` (measured
head-to-head: R 0.269 -> 1.000 on the UPC gold), so any surface that GROUPS by identity — corroboration
("sold at N sources"), "other SKUs of this item" — gets a real lift by grouping on `canon_item_id`.

But canon only covers the sources it has ingested (9 retail sources / ~90k SKUs today); unifyd's xwalk
spans 1.33M incl. TTB / distributor / UPC-less sources canon hasn't reached. So this is a COVERAGE-AWARE
OVERLAY, never a wholesale swap: the resolved identity is

    COALESCE(canon_item_id, item_key)

— canon where it reaches, unifyd's `item_key` everywhere else. As canon's source coverage grows the
overlay strengthens automatically; nothing regresses where canon is absent.

This module is the reusable PRIMITIVE (pure warehouse reads, no serving-hot-path coupling): the serving
layer and the master build both call it. It changes no existing value on its own — a caller opts in.

    from canon_identity import identity_map, corroboration
"""

import warehouse

_TABLE = "item_identity"


def available():
    """True if the canon `item_identity` table exists and is non-empty — the overlay is inert otherwise
    (pre-ingest, or a source canon hasn't reached), so callers degrade cleanly to unifyd's item_key."""
    try:
        return warehouse.row_count(_TABLE) > 0
    except Exception:
        return False


def identity_map():
    """``(source, product_id) -> canon_item_id`` for every SKU canon resolves. Empty dict if the table is
    absent (caller then falls back to item_key for everything). The COALESCE lives in :func:`resolve`."""
    try:
        return {(r["source"], str(r["product_id"])): int(r["canon_item_id"])
                for r in warehouse.query(_TABLE, "SELECT source, product_id, canon_item_id FROM t")}
    except Exception:
        return {}


def resolve(source, product_id, item_key, idmap=None):
    """The coverage-aware identity for one SKU: canon's ``canon_item_id`` if canon covers it, else the
    unifyd ``item_key`` fallback. ``idmap`` is an optional prebuilt :func:`identity_map` (pass it in a loop
    to avoid rebuilding). Canon ids are returned as ``"c:<id>"`` so a canon id can never collide with an
    md5 item_key in the same grouping space."""
    m = idmap if idmap is not None else identity_map()
    cid = m.get((source, str(product_id)))
    return ("c:%d" % cid) if cid is not None else item_key


def corroboration():
    """``canon_item_id -> {n_sources, source_list}`` — the LIFTED "sold at N sources" over canon's covered
    universe (distinct sources per canon item). This is the numerator the serving overlay COALESCEs onto
    unifyd's precomputed per-item ``sources`` where canon covers the item; elsewhere the unifyd value
    stands. Returns {} if the table is absent."""
    out = {}
    try:
        rows = warehouse.query(
            _TABLE,
            "SELECT canon_item_id, count(DISTINCT source) AS n_sources, "
            "string_agg(DISTINCT source, ',' ORDER BY source) AS source_list "
            "FROM t GROUP BY canon_item_id")
    except Exception:
        return {}
    for r in rows:
        out[int(r["canon_item_id"])] = {
            "n_sources": int(r["n_sources"]),
            "source_list": r["source_list"] or "",
        }
    return out
