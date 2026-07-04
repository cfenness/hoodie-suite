"""relations.py — Hoodie Relations extras: per-account delivery day + goals, and
LLM goal-matching against the canonical master (dim_account / dim_product).

Goal matching: given a free-text goal, match it to master entities (account, brand)
and structure it (metric, target, timeframe) so it's trackable + reportable. Uses
Claude when ANTHROPIC_API_KEY is set; otherwise a deterministic keyword heuristic so
it still works offline. Either way the user double-checks the match before saving.
"""
import os, re, json
import warehouse

MODEL = os.environ.get("AGENT_LLM_MODEL", "claude-opus-4-8")


def llm_enabled():
    return bool(os.environ.get("ANTHROPIC_API_KEY"))


def _master():
    accts, prods = [], []
    try:
        accts = [a.get("name") for a in warehouse.query("dim_account") if a.get("name")]
    except Exception:
        pass
    try:
        for p in warehouse.query("dim_product"):
            for k in ("brand", "name", "product"):
                if p.get(k):
                    prods.append(p[k]); break
    except Exception:
        pass
    return sorted(set(accts)), sorted(set(prods))


def _heuristic(text, accounts, products, account_hint):
    t = (text or "").lower()

    def best(cands):
        for c in sorted([c for c in cands if c], key=len, reverse=True):
            if c.lower() in t:
                return c
        return None

    acct = account_hint or best(accounts)
    brand = best(products)
    m = re.search(r"([+\-]?\d+(?:\.\d+)?)\s*(%|pts?|percent|cases?|skus?|points?|facings?)?", t)
    target = m.group(1) if m else None
    unit = (m.group(2) or "").rstrip("s") if m and m.group(2) else None
    metric = None
    for kw, mm in [("facing", "facings"), ("sku", "SKU count"), ("distribut", "distribution"),
                   ("case", "cases"), ("acv", "%ACV"), ("price", "price"), ("share", "share"),
                   ("revenue", "revenue"), ("velocity", "velocity"), ("carr", "distribution")]:
        if kw in t:
            metric = mm; break
    tf = None
    mt = re.search(r"\b(q[1-4]|h[12]|eoy|end of year|by \w+|next (?:month|quarter|year)|\d{4})\b", t)
    if mt:
        tf = mt.group(1)
    return {"account": acct, "brand": brand, "metric": metric, "target": target, "unit": unit,
            "timeframe": tf, "confidence": "heuristic",
            "rationale": "Matched by keyword against the master (no LLM key set — verify before saving)."}


def match_goal(text, account_hint=None):
    text = (text or "").strip()
    if not text:
        return {"error": "empty goal"}
    accounts, products = _master()
    if not llm_enabled():
        return _heuristic(text, accounts, products, account_hint)
    try:
        import anthropic
        client = anthropic.Anthropic()
        sysmsg = ("You structure a free-text B2B beverage-alcohol sales goal against master data. "
                  "Return ONLY JSON (no prose): "
                  '{"account":string|null,"brand":string|null,"metric":string|null,'
                  '"target":string|null,"unit":string|null,"timeframe":string|null,'
                  '"confidence":"high"|"medium"|"low","rationale":string}. '
                  "Pick account/brand ONLY from the provided candidate lists (return the exact string) or null.")
        usr = ("GOAL: " + text +
               "\n\nACCOUNT CANDIDATES: " + ", ".join(accounts[:200]) +
               "\n\nBRAND CANDIDATES: " + ", ".join(products[:200]) +
               (("\n\nHINT: the goal concerns account '" + account_hint + "'.") if account_hint else ""))
        msg = client.messages.create(model=MODEL, max_tokens=600,
                                     system=sysmsg, messages=[{"role": "user", "content": usr}])
        raw = "".join(getattr(b, "text", "") for b in msg.content)
        mm = re.search(r"\{[\s\S]*\}", raw)
        out = json.loads(mm.group(0)) if mm else {}
        out.setdefault("confidence", "medium")
        out.setdefault("rationale", "Matched by Claude against the master.")
        return out
    except Exception as e:
        h = _heuristic(text, accounts, products, account_hint)
        h["rationale"] = "LLM error (" + str(e)[:80] + ") — fell back to keyword match."
        return h
