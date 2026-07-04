"""territories.py — named account groups (the Territory Builder's data).

A territory is a named set of accounts (dim_account). Hoodie's territories power the
target-vs-competition comparison reports, so this is the join those will read. For now
territories are DETERMINISTIC rules over the accounts on the canonical model (city / channel
/ chain status); persisted user-drawn territories are the next step (a dim_territory bridge
in the warehouse). Answers gracefully (empty) before the book is seeded.
"""
import warehouse


def _accounts():
    try:
        return warehouse.query("dim_account")
    except Exception:
        return []


# deterministic named territories over dim_account (id, name, membership rule)
_DEFS = [
    {"id": "orlando-core", "name": "Orlando Core",
     "rule": lambda a: (a.get("city") or "") == "Orlando"},
    {"id": "winter-park", "name": "Winter Park & Maitland",
     "rule": lambda a: (a.get("city") or "") in ("Winter Park", "Maitland")},
    {"id": "on-premise", "name": "On-Premise Accounts",
     "rule": lambda a: a.get("channel") == "on"},
    {"id": "off-premise", "name": "Off-Premise Accounts",
     "rule": lambda a: a.get("channel") == "off"},
    {"id": "chains", "name": "Chain Stores",
     "rule": lambda a: a.get("chain_status") == "Chain"},
    {"id": "independents", "name": "Independents",
     "rule": lambda a: a.get("chain_status") == "Independent"},
]


def _members(d, accts):
    return [a for a in accts if d["rule"](a)]


def list_territories():
    accts = _accounts()
    out = []
    for d in _DEFS:
        mem = _members(d, accts)
        names = [m.get("name") or m.get("account_id") for m in mem[:2]]
        preview = ", ".join(names)
        if len(mem) > 2:
            preview += " + %d more" % (len(mem) - 2)
        out.append({"id": d["id"], "name": d["name"], "count": len(mem), "preview": preview})
    return out


def members(tid):
    d = next((x for x in _DEFS if x["id"] == tid), None)
    if not d:
        return {"id": tid, "name": None, "members": []}
    rows = []
    for a in _members(d, _accounts()):
        rows.append({
            "account_id": a.get("account_id"),
            "name": a.get("name"),
            "banner": a.get("chain_status"),
            "address": (a.get("zip") or ""),          # street addr arrives with the real POS feed
            "city": a.get("city"), "state": "FL", "country": "USA",
            "channel": a.get("channel"), "subchannel": a.get("subchannel"),
        })
    return {"id": tid, "name": d["name"], "members": rows}
