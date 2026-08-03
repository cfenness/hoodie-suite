"""dist_xwalk.py — build `dist_item_xwalk`, the Tier-3 match spine (OVERLAY-DESIGN §6).

The files people upload to Overlay are DISTRIBUTOR-SHAPED: their own item numbers, a supplier
code, sometimes a retail UPC. Tier 1 (UPC) only reaches the rows that carry a good UPC. Tier 3 is
the one that lands hardest with a distributor buyer, because we match on *their own item numbers* —
and it's buildable today from catalogs we already pull:

    distributor_id | distributor_name | dist_item_code | retail_upc | canon_item_id | source

  · `vip_brandbuilder_items` (vtinfo_bbs.py)  — VIP Brand Builder, ~300 distributors reachable,
        package-grain with `dist_item_code` + a zero-padded retail UPC. The richest Tier-3 source.
  · `bbg_products` (bbg_salsify.py)           — Salsify tenant catalogs (Breakthru today; the recipe
        is parameterized by SITE, so each new tenant lands in this same grain).

`canon_item_id` is resolved the same way `build_item_identity` resolves it — the digits-only UPC as
a bigint — so a Tier-3 hit and a Tier-1 hit land on the SAME identity by construction rather than by
a second join. A crosswalk row with no resolvable UPC is still written: the distributor's item code
and name are real, and Tier 3 can return a *display* even where identity is not yet resolvable. That
is the honest half-match, and it's marked as such (`canon_item_id` NULL) rather than dropped.

    python dist_xwalk.py            # rebuild dist_item_xwalk from the landed distributor catalogs
"""
import argparse
import sys

import warehouse

TABLE = "dist_item_xwalk"

FIELDS = ["distributor_id", "distributor_name", "dist_item_code", "dist_item_key",
          "retail_upc", "canon_item_id", "brand", "product_name", "size_raw", "source"]

# One entry per landed distributor catalog. Adding a Salsify tenant or a VIP sourceCode is a data
# change upstream, not a code change here — both land in their own table and this reads the table.
_SOURCES = [
    dict(table="vip_brandbuilder_items", source="vip-brandbuilder",
         sql="""SELECT CAST(distributor_id AS VARCHAR)   AS distributor_id,
                       CAST(max(distributor_name) AS VARCHAR) AS distributor_name,
                       CAST(dist_item_code AS VARCHAR)    AS dist_item_code,
                       CAST(max(retail_upc) AS VARCHAR)   AS retail_upc,
                       CAST(max(brand) AS VARCHAR)        AS brand,
                       CAST(max(product_name) AS VARCHAR) AS product_name,
                       CAST(max(package_name) AS VARCHAR) AS size_raw
                FROM t
                WHERE dist_item_code IS NOT NULL AND CAST(dist_item_code AS VARCHAR) <> ''
                GROUP BY 1, 3"""),
    dict(table="bbg_products", source="salsify",
         sql="""SELECT CAST(coalesce(wholesaler, 'bbg') AS VARCHAR) AS distributor_id,
                       CAST(max(coalesce(wholesaler, 'Breakthru Beverage Group')) AS VARCHAR) AS distributor_name,
                       CAST(id AS VARCHAR)                AS dist_item_code,
                       CAST(max(upc) AS VARCHAR)          AS retail_upc,
                       CAST(max(brand) AS VARCHAR)        AS brand,
                       CAST(max(title) AS VARCHAR)        AS product_name,
                       CAST(max(dimensions) AS VARCHAR)   AS size_raw
                FROM t
                WHERE id IS NOT NULL AND CAST(id AS VARCHAR) <> ''
                GROUP BY 1, 3"""),
]


def item_key(code):
    """The join key an uploaded item code is matched on: digits and letters, uppercased, with the
    punctuation distributors sprinkle through their own codes removed ('A-1234 ' == 'a1234').
    Deliberately NOT aggressive beyond that — dropping leading zeros here would merge genuinely
    different codes, and a distributor's item number is an exact identifier, not a fuzzy string."""
    s = "".join(ch for ch in str(code or "") if ch.isalnum()).upper()
    return s if len(s) >= 3 else ""


def canon_id(upc_value):
    """Digits-only UPC as a bigint — the SAME resolution build_item_identity applies, so Tier 3 and
    Tier 1 land on one identity. Returns None when the code is too short to be one."""
    d = "".join(ch for ch in str(upc_value or "") if ch.isdigit())
    if len(d) < 11:
        return None
    try:
        return int(d)
    except ValueError:
        return None


def build(log=print):
    """Read every landed distributor catalog and (re)write `dist_item_xwalk`."""
    rows, per_source = [], {}
    for spec in _SOURCES:
        try:
            got = warehouse.query(spec["table"], spec["sql"])
        except Exception as e:
            log("  [dist_xwalk] %s unavailable (skipped): %s" % (spec["table"], str(e)[:70]))
            per_source[spec["source"]] = 0
            continue
        n = 0
        for r in got:
            key = item_key(r.get("dist_item_code"))
            if not key:
                continue
            rows.append({
                "distributor_id": str(r.get("distributor_id") or ""),
                "distributor_name": str(r.get("distributor_name") or ""),
                "dist_item_code": str(r.get("dist_item_code") or ""),
                "dist_item_key": key,
                "retail_upc": str(r.get("retail_upc") or ""),
                "canon_item_id": canon_id(r.get("retail_upc")),
                "brand": str(r.get("brand") or ""),
                "product_name": str(r.get("product_name") or ""),
                "size_raw": str(r.get("size_raw") or ""),
                "source": spec["source"],
            })
            n += 1
        per_source[spec["source"]] = n
        log("  [dist_xwalk] %s: %d item codes" % (spec["table"], n))

    if not rows:
        # An empty write is a failed read, not a rebuild (the never-clobber-a-catalog rule).
        log("[dist_xwalk] no distributor catalogs readable — leaving %s untouched" % TABLE)
        return {"records": 0, "distributors": 0, "identified": 0, "per_source": per_source,
                "skipped": True}

    warehouse.write_parquet(TABLE, rows, FIELDS)
    out = {"records": len(rows),
           "distributors": len({r["distributor_id"] for r in rows}),
           "identified": sum(1 for r in rows if r["canon_item_id"] is not None),
           "per_source": per_source, "skipped": False}
    out["identified_pct"] = round(100.0 * out["identified"] / max(1, out["records"]), 1)
    log("[dist_xwalk] %d item codes across %d distributors; %d (%s%%) resolve to a canon identity"
        % (out["records"], out["distributors"], out["identified"], out["identified_pct"]))
    return out


def coverage():
    """Tier-3 coverage, for the workbook's provenance sheet. Cheap enough to call per run."""
    try:
        r = warehouse.query(TABLE, "SELECT count(*) AS n, count(DISTINCT distributor_id) AS d, "
                                   "count(canon_item_id) AS ident FROM t")
        row = r[0] if r else {}
        return {"rows": int(row.get("n") or 0), "distributors": int(row.get("d") or 0),
                "identified": int(row.get("ident") or 0), "available": True}
    except Exception as e:
        return {"available": False, "why": str(e)[:100]}


def main():
    argparse.ArgumentParser(description="Build dist_item_xwalk (the Tier-3 match spine).").parse_args()
    build()
    return 0


if __name__ == "__main__":
    sys.exit(main())
