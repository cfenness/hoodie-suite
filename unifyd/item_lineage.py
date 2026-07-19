#!/usr/bin/env python3
"""item_lineage.py — the append-only, event-sourced item master for the daily engine.

HARD schema rules (never violated):
  • Nothing is ever deleted or overwritten. Every observation/change is an EVENT appended to `item_events`;
    the current view is DERIVED from the event stream. A retailer fixing a country-of-origin is a visible
    `attr` event, right or wrong — model fuel, not a silent update.
  • A new UPC is a NEW item, minted with orphan=true. If we can link it to a predecessor that just vanished in
    the same store/section/name slot, we ADD a `supersedes` event (old.superseded_by = new) — but the orphan
    birth-flag stays. orphan and supersedes are complementary facts, both retained.
  • Price + discount are stored EACH day (time series), even when unchanged is skipped — only transitions append.

Two phases, both pure functions (testable without network):
  diff_catalog(store_id, prior, observed, enriched_uuids)  — getStore diff → price/discount/delist events +
      the getMenuItem QUEUE for every genuinely-new item_uuid (the ones we must enrich).
  ingest_detail(detail, store_id, seen_upcs, slot_index)   — after getMenuItemV1 returns UPC/recipe: mint the
      item-master record, tag orphan, and resolve supersedes against a vanished predecessor in the same slot.
"""
import json
import re
import time

EVENT_FIELDS = ["event_id", "ts", "day", "source", "store_id", "item_uuid", "upc", "name", "section",
                "event_type", "price", "list_price", "on_promo", "discount", "orphan", "supersedes", "detail"]
# event_type ∈ new_item | enrich_pending | enriched | price | discount | attr | delist | supersedes


def _day(ts):
    return time.strftime("%Y-%m-%d", time.gmtime(ts))


def _eid(*parts):
    import hashlib
    return hashlib.sha1("|".join(str(p) for p in parts).encode()).hexdigest()[:16]


def _norm(name):
    """Normalized name key for slot matching (supersedes lineage) — lowercase, strip size/punct noise."""
    s = (name or "").lower()
    s = re.sub(r"\b\d+(\.\d+)?\s?(ml|l|oz|pk|pack|ct|count|g|kg|lb)\b", " ", s)
    s = re.sub(r"[^a-z0-9 ]", " ", s)
    return re.sub(r"\s+", " ", s).strip()


def _ev(store_id, source, it, event_type, ts, **kw):
    d = {k: None for k in EVENT_FIELDS}
    d.update(ts=ts, day=_day(ts), source=source, store_id=store_id,
             item_uuid=it.get("item_uuid"), upc=it.get("upc"), name=it.get("name"),
             section=it.get("section"), event_type=event_type,
             price=it.get("price"), list_price=it.get("list_price"),
             on_promo=it.get("on_promo"), discount=it.get("discount"))
    d.update(kw)
    d["event_id"] = _eid(store_id, it.get("item_uuid"), event_type, d.get("day"), kw.get("upc") or it.get("upc"),
                         d.get("price"), d.get("discount"))
    if isinstance(d.get("detail"), (dict, list)):
        d["detail"] = json.dumps(d["detail"], separators=(",", ":"))
    return d


def diff_catalog(store_id, prior, observed, enriched_uuids, source="ubereats", ts=None):
    """PHASE A — getStore catalog diff.
      prior: {item_uuid: {name,section,price,list_price,on_promo,discount,upc}} last-known state for this store
      observed: [ {item_uuid,name,section,price,list_price,on_promo,discount,upc?}, … ] current getStore items
      enriched_uuids: set of item_uuids already getMenuItem-enriched (globally)
    Returns (events, menuitem_queue). menuitem_queue = item dicts for genuinely-new items to getMenuItemV1."""
    ts = ts or int(time.time())
    events, queue = [], []
    obs_by_uuid = {it["item_uuid"]: it for it in observed if it.get("item_uuid")}

    for uuid, it in obs_by_uuid.items():
        p = prior.get(uuid)
        if p is None:
            # genuinely new item at this store → record the appearance + queue enrichment (UPC/recipe)
            events.append(_ev(store_id, source, it, "new_item", ts))
            if uuid not in enriched_uuids:
                events.append(_ev(store_id, source, it, "enrich_pending", ts))
                queue.append({"store_id": store_id, "source": source, **it})
            continue
        # known item — append only the transitions (price / discount / attribute)
        if it.get("price") != p.get("price") or it.get("list_price") != p.get("list_price"):
            events.append(_ev(store_id, source, it, "price", ts))
        if bool(it.get("on_promo")) != bool(p.get("on_promo")) or it.get("discount") != p.get("discount"):
            events.append(_ev(store_id, source, it, "discount", ts))
        changed = {k: [p.get(k), it.get(k)] for k in ("name", "section")
                   if it.get(k) is not None and it.get(k) != p.get(k)}
        if changed:
            events.append(_ev(store_id, source, it, "attr", ts, detail=changed))

    # items present before, gone now → delist event (record NEVER deleted)
    for uuid, p in prior.items():
        if uuid not in obs_by_uuid:
            events.append(_ev(store_id, source, {**p, "item_uuid": uuid}, "delist", ts))
    return events, queue


def ingest_detail(detail, store_id, seen_upcs, vanished=None, source="ubereats", ts=None):
    """PHASE B — after getMenuItemV1 returns full detail (incl. UPC/recipe) for a new item.
      detail: {item_uuid,name,section,upc,price,...,recipe?}
      seen_upcs: set of every UPC ever recorded (globally). MUTATED: a new UPC is added.
      vanished: {norm_name: {upc,item_uuid,name,section}} predecessors that just delisted in THIS store/section
                (from the same run's delist events) — used to resolve supersedes lineage.
    Returns (events, master_upsert). master_upsert is the derived item-master row (orphan/supersedes stamped)."""
    ts = ts or int(time.time())
    upc = (detail.get("upc") or "").strip()
    it = {k: detail.get(k) for k in ("item_uuid", "name", "section", "price", "list_price", "on_promo", "discount")}
    it["upc"] = upc
    events = [_ev(store_id, source, it, "enriched", ts, detail={k: detail.get(k) for k in detail
                  if k not in ("price", "list_price")})]

    is_new_upc = bool(upc) and upc not in seen_upcs
    supersedes = None
    if is_new_upc:
        seen_upcs.add(upc)
        # supersedes: did a DIFFERENT-UPC item with the same normalized name just vanish here? then this new UPC
        # is that item's successor (retailer changed the UPC / repack) — link lineage, keep the orphan birth-flag.
        cand = (vanished or {}).get(_norm(detail.get("name")))
        if cand and cand.get("upc") and cand["upc"] != upc:
            supersedes = cand["upc"]
            events.append(_ev(store_id, source, it, "supersedes", ts, upc=upc, supersedes=supersedes,
                              detail={"superseded_upc": cand["upc"], "superseded_item": cand.get("item_uuid")}))
        # mint the item-master record: a new UPC is always a NEW item, born orphan (append-only fact)
        events.insert(0, _ev(store_id, source, it, "new_item", ts, upc=upc, orphan=True, supersedes=supersedes))

    master = {"upc": upc or None, "item_uuid": detail.get("item_uuid"), "store_id": store_id, "source": source,
              "name": detail.get("name"), "section": detail.get("section"), "first_seen": ts, "last_seen": ts,
              "orphan": bool(is_new_upc), "supersedes": supersedes,
              "status": "superseded_predecessor" if supersedes else ("orphan" if is_new_upc else "active")}
    return events, master


if __name__ == "__main__":
    # ── self-test: no network. Exercise new item, price move, discount, delist, orphan, supersedes ──
    t0 = 1_700_000_000
    prior = {
        "u_wine": {"name": "Josh Cabernet 750ml", "section": "Wine", "price": 1299, "list_price": 1299,
                   "on_promo": False, "discount": 0, "upc": "0000001"},
        "u_beer": {"name": "Modelo 12pk", "section": "Beer", "price": 1599, "list_price": 1599,
                   "on_promo": False, "discount": 0, "upc": "0000002"},
    }
    observed = [
        {"item_uuid": "u_wine", "name": "Josh Cabernet 750ml", "section": "Wine", "price": 1099,
         "list_price": 1299, "on_promo": True, "discount": 200},                       # price + discount move
        {"item_uuid": "u_newrum", "name": "Bacardi Superior 750ml", "section": "Spirits", "price": 1499},  # NEW
        # (u_beer absent → delist)
    ]
    evs, queue = diff_catalog("store1", prior, observed, enriched_uuids=set())
    print("PHASE A events:")
    for e in evs:
        print("  ", e["event_type"], e["item_uuid"], "price=%s" % e["price"], "disc=%s" % e["discount"])
    print("getMenuItem queue:", [q["item_uuid"] for q in queue])

    # a predecessor vanished (Modelo delisted) — set up the supersedes slot by name
    vanished = {_norm("Bacardi Superior 750ml"): {"upc": "0000009", "item_uuid": "u_oldrum",
                                                   "name": "Bacardi Superior 750ml", "section": "Spirits"}}
    seen = {"0000001", "0000002", "0000009"}
    # detail for the new rum returns a brand-new UPC that SUPERSEDES the vanished one
    evs2, master = ingest_detail({"item_uuid": "u_newrum", "name": "Bacardi Superior 750ml", "section": "Spirits",
                                  "upc": "0000123", "price": 1499, "recipe": None}, "store1", seen, vanished)
    print("\nPHASE B events:")
    for e in evs2:
        print("  ", e["event_type"], "upc=%s" % e["upc"], "orphan=%s" % e["orphan"], "supersedes=%s" % e["supersedes"])
    print("master:", master)

    # a truly new UPC with NO predecessor → pure orphan
    evs3, master3 = ingest_detail({"item_uuid": "u_x", "name": "Weird New Seltzer", "section": "RTD",
                                   "upc": "0000777", "price": 999}, "store1", seen, vanished={})
    print("\nORPHAN case master:", {k: master3[k] for k in ("upc", "orphan", "supersedes", "status")})
