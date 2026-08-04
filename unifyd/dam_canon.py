#!/usr/bin/env python3
"""dam_canon.py — resolve a DAM brand literal to the CANON brand, so `brand_events` is canon-keyed.

WHY THIS IS A SEPARATE STEP AND NOT A LOOKUP INSIDE derive_events
  Everything else in the DAM pipeline is pure: hand it a folder tree and it produces rows, with no
  dependency on the rest of the warehouse. Canon resolution is the one place that reaches into
  `dim_brand`, so it is isolated here — the fact feed still lands in full when the master is
  unreachable, it just lands UNRESOLVED and says so. A pipeline that refuses to produce facts because
  a join was unavailable would be trading the whole output for one column.

THE MATCH IS DETERMINISTIC OR IT DOESN'T HAPPEN
  One tier: `overlay_match.brand_key()` applied to BOTH sides — the same normalization the overlay
  uses to match uploaded files against the master, so a DAM brand and a master brand are keyed
  identically ("DEWAR'S" / "Dewars" / "dewar's" all collapse to `dewar`). An exact key match is a
  DETERMINISTIC claim. Anything short of that is `unresolved`, and the event keeps its vendor-scoped
  slug with `brand_resolution="unresolved"` on the row.

  There is deliberately NO fuzzy tier. A wrong `hoodie_brand_id` is worse than an absent one: it
  silently attributes a competitor's launch to your brand in every downstream roll-up, and nothing
  about the row would look wrong. Fuzzy brand identity is hoodie-canon's cascade
  ([[matching-convergence]]), not a regex in a media-centre connector.

DEGRADES LOUDLY
  `resolve_brands` returns a report — resolved / unresolved / `available` — and the connector turns
  an unreachable master or a zero-hit run into a run warning. "Every event is unresolved" and "the
  master wasn't readable" are different problems and must not look the same.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

BRAND_TABLE = "dim_brand"


class KeyUnavailable(Exception):
    """`overlay_match.brand_key` could not be imported, so we cannot key a brand the way the master
    is keyed."""


def _key(name):
    """THE shared brand match key — `overlay_match.brand_key`, and only that.

    There is deliberately NO local fallback. The obvious one (de-accent, lowercase, drop
    possessives, singularize) looks equivalent and is not: `precleanse.nbrand` also drops GENERIC
    tokens, so the real key for "Grey Goose Vodka" is `grey goose` while the lookalike produces
    `grey goose vodka`. A fallback that keys differently doesn't degrade the match — it silently
    produces a DIFFERENT match set than production, resolving some brands and mis-missing others
    with nothing in the output to say which rules ran. Refusing to resolve is the honest failure."""
    try:
        import overlay_match
    except Exception as e:
        raise KeyUnavailable(str(e)[:120])
    return overlay_match.brand_key(name or "")


def brand_index(log=print):
    """{brand_key: {brand_key, hoodie_id, canon_brand}} from dim_brand, or None when the master is
    not readable. None and {} mean different things — None is "we could not look", {} is "we looked
    and the master is empty" — and both are reported rather than collapsed into 'no matches'."""
    try:
        _key("probe")                      # the shared key function, or we do not resolve at all
    except KeyUnavailable as e:
        log("dam_canon: brand_key unavailable (%s) — events land UNRESOLVED rather than being "
            "matched by a different rule than the master uses" % e)
        return None
    try:
        import warehouse
        rows = warehouse.query(
            BRAND_TABLE,
            "SELECT brand_key, brand, hoodie_id FROM t WHERE brand IS NOT NULL")
    except Exception as e:
        log("dam_canon: %s unreadable (%s) — events land UNRESOLVED" % (BRAND_TABLE, str(e)[:100]))
        return None
    idx = {}
    for r in rows or []:
        k = _key(r.get("brand"))
        if not k:
            continue
        # First writer wins, so a stable master produces a stable mapping run to run. Collisions
        # (two dim_brand rows normalizing to one key) are the master's business, not ours.
        idx.setdefault(k, {"brand_key": r.get("brand_key"), "hoodie_id": r.get("hoodie_id"),
                           "canon_brand": r.get("brand")})
    return idx


def resolve_brands(names, idx=None, log=print):
    """names → ({name: match-or-None}, report). `idx=None` loads it; pass one to resolve in bulk
    without re-reading the master."""
    if idx is None:
        idx = brand_index(log=log)
    available = idx is not None
    out = {}
    for n in set(names or []):
        try:
            out[n] = (idx or {}).get(_key(n)) if available else None
        except KeyUnavailable:
            out[n], available = None, False
    resolved = sum(1 for v in out.values() if v)
    report = {"available": available, "brands_seen": len(out), "brands_resolved": resolved,
              "brands_unresolved": len(out) - resolved,
              "master_brands": (len(idx) if available else 0)}
    return out, report


def apply_to_events(events, log=print):
    """Stamp canon identity onto brand_events in place. Returns the report.

    Resolved  → hoodie_brand_id = the master's hoodie_id, brand_key set, provenance DETERMINISTIC.
    Unresolved→ the vendor-scoped slug stays, brand_resolution="unresolved", and the provenance for
                hoodie_brand_id is REMOVED — we make no canon claim we cannot support."""
    import json
    import dam
    names = [e.get("brand") for e in events if e.get("brand")]
    matches, report = resolve_brands(names, log=log)
    for e in events:
        m = matches.get(e.get("brand"))
        prov = json.loads(e.get("field_provenance") or "{}")
        if m and m.get("hoodie_id"):
            e["hoodie_brand_id"] = str(m["hoodie_id"])
            e["brand_key"] = m.get("brand_key")
            e["canon_brand"] = m.get("canon_brand")
            e["brand_resolution"] = "canon"
            prov["hoodie_brand_id"] = dam.DETERMINISTIC
            prov["brand_key"] = dam.DETERMINISTIC
        else:
            e["brand_key"] = None
            e["canon_brand"] = None
            e["brand_resolution"] = "unresolved" if report["available"] else "master-unavailable"
            prov.pop("hoodie_brand_id", None)
        e["field_provenance"] = json.dumps(prov)
    log("dam_canon: %d/%d brand(s) resolved to canon%s"
        % (report["brands_resolved"], report["brands_seen"],
           "" if report["available"] else " (MASTER UNAVAILABLE — nothing could be resolved)"))
    return report


def main(argv=None):
    import argparse
    ap = argparse.ArgumentParser(description="Resolve brand literals against the canon dim_brand.")
    ap.add_argument("brands", nargs="*", help="brand names to resolve")
    a = ap.parse_args(argv)
    matches, report = resolve_brands(a.brands or [])
    for n, m in sorted(matches.items()):
        print("  %-22s -> %s" % (n, ("%s / %s" % (m["hoodie_id"], m["canon_brand"])) if m else "UNRESOLVED"))
    print(report)


if __name__ == "__main__":
    main()
