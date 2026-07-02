"""Planogram brain — the backend behind the shelf rep-count tool (apps/planogram-repcount.html).

Three jobs, all reached through the suite's /api/* proxy (mirrors the CloudFront routing):
  1. benchmark(market, fmt)  — the MARKET NORM to compare a shelf against. Deterministic, and
     FORMAT-AWARE (on-premise cocktail bars carry a different category mix than off-premise
     stores). This is the seam where real SipSource depletion norms plug in; until then it's a
     documented model, labeled as such in `provenance`.
  2. shelf_count(image) — Claude VISION reads a photo of the shelf/back-bar and returns facings
     per category × tier, so the rep doesn't count by hand. Structured JSON, numbers only.
  3. pitch(deltas)       — Claude narrates the computed gaps into a 2-3 sentence buyer pitch,
     using ONLY the figures passed in (numbers deterministic, wording generated).

LLM jobs are OFF unless ANTHROPIC_API_KEY is set (`anthropic` imported lazily), so the agent gains
no hard dependency. Every function returns a plain dict; errors are graceful {"error": ...} states.
"""
import json, os

MODEL = os.environ.get("AGENT_LLM_MODEL", "claude-opus-4-8")


def enabled():
    return bool(os.environ.get("ANTHROPIC_API_KEY"))


# ---------------------------------------------------------------- benchmark ----
# Off-premise category MIX by market (share of the spirits set). Ratios track the demo totals.
_MARKET_MIX = {
    "houston": {"name": "Houston, TX", "mix": {"Whiskey": 0.35, "Tequila": 0.30, "Vodka": 0.22, "Rum": 0.13}},
    "miami":   {"name": "Miami, FL",   "mix": {"Rum": 0.35, "Vodka": 0.30, "Tequila": 0.22, "Whiskey": 0.13}},
    "dallas":  {"name": "Dallas, TX",  "mix": {"Whiskey": 0.45, "Tequila": 0.27, "Vodka": 0.20, "Rum": 0.08}},
}
# On-premise cocktail programs tilt to agave + whiskey and carry less shelf vodka than a store.
_FORMAT_TILT = {
    "on":  {"Whiskey": 1.15, "Rum": 1.05, "Tequila": 1.25, "Vodka": 0.70},
    "off": {"Whiskey": 1.00, "Rum": 1.00, "Tequila": 1.00, "Vodka": 1.00},
}
_TIER_PROFILE = {"Whiskey": [0.28, 0.44, 0.28], "Tequila": [0.34, 0.42, 0.24],
                 "Vodka": [0.24, 0.34, 0.42], "Rum": [0.24, 0.30, 0.46]}
_FACING_BUDGET = 60   # nominal total facings a benchmark shelf holds


def _largest_remainder(shares, total):
    """Integer allocation of `total` across `shares` (dict) that sums exactly, no drift."""
    raw = {k: v * total for k, v in shares.items()}
    floor = {k: int(v) for k, v in raw.items()}
    rem = total - sum(floor.values())
    order = sorted(raw, key=lambda k: raw[k] - floor[k], reverse=True)
    for k in order[:rem]:
        floor[k] += 1
    return floor


def benchmark(market, fmt="off"):
    m = _MARKET_MIX.get((market or "").lower())
    if not m:
        return {"error": "unknown-market", "detail": market}
    fmt = "on" if fmt == "on" else "off"
    tilt = _FORMAT_TILT[fmt]
    tilted = {c: m["mix"].get(c, 0.0) * tilt.get(c, 1.0) for c in m["mix"]}
    s = sum(tilted.values()) or 1.0
    shares = {c: v / s for c, v in tilted.items()}
    totals = _largest_remainder(shares, _FACING_BUDGET)
    return {
        "market": (market or "").lower(), "market_name": m["name"], "format": fmt,
        "totals": totals, "tier_profile": _TIER_PROFILE,
        "provenance": ("Modeled market norm — %s · %s-premise (category mix; swap for SipSource "
                       "depletion norms when wired)." % (m["name"], fmt)),
    }


# ---------------------------------------------------------------- shelf vision ----
_SHELF_SYSTEM = (
    "You count bottle FACINGS on a photo of a retail liquor shelf or a bar back-bar. A facing is a "
    "front-row bottle visible to a shopper; count fronts only, not depth. Assign each facing to one "
    "of the given categories by bottle silhouette/label; ignore bottles outside those categories and "
    "non-bottle items. Split by shelf tier top-to-bottom using the given tier names (map visible "
    "shelves onto them; if there are more physical shelves than tiers, group sensibly; if fewer, use "
    "zeros for the missing tier). Return ONLY the JSON grid — integers, no prose, no guessing beyond "
    "what is visible."
)


def _shelf_schema(n_tiers, n_cats):
    return {
        "type": "object", "additionalProperties": False, "required": ["counts"],
        "properties": {
            "counts": {"type": "array", "minItems": n_tiers, "maxItems": n_tiers,
                       "items": {"type": "array", "minItems": n_cats, "maxItems": n_cats,
                                 "items": {"type": "integer", "minimum": 0}}},
            "notes": {"type": "string"},
            "confidence": {"type": "string", "enum": ["high", "medium", "low"]},
        },
    }


def shelf_count(image_b64, media_type="image/jpeg", categories=None, tiers=None, _client=None):
    """Vision: photo -> counts[tier][category]. Graceful {"error": ...} on any failure."""
    if not enabled():
        return {"error": "llm-disabled", "detail": "ANTHROPIC_API_KEY not set"}
    categories = categories or ["Whiskey", "Rum", "Tequila", "Vodka"]
    tiers = tiers or ["Stretch", "Eye Level", "Reach"]
    if not image_b64:
        return {"error": "need-image"}
    prompt = ("Categories (column order): %s\nTiers top-to-bottom (row order): %s\n"
              "Return counts as a %d-row x %d-column grid of integers (counts[tier][category])."
              % (json.dumps(categories), json.dumps(tiers), len(tiers), len(categories)))
    try:
        client = _client
        if client is None:
            import anthropic  # lazy
            client = anthropic.Anthropic()
        msg = client.messages.create(
            model=MODEL, max_tokens=1500,
            system=_SHELF_SYSTEM,
            output_config={"format": {"type": "json_schema",
                                      "schema": _shelf_schema(len(tiers), len(categories))}},
            messages=[{"role": "user", "content": [
                {"type": "image", "source": {"type": "base64", "media_type": media_type, "data": image_b64}},
                {"type": "text", "text": prompt},
            ]}],
        )
        text = "".join(getattr(b, "text", "") for b in msg.content if getattr(b, "type", None) == "text")
        data = json.loads(text) if text else None
        if not data or "counts" not in data:
            return {"error": "parse-failed", "raw": (text or "")[:300]}
        data["categories"] = categories
        data["tiers"] = tiers
        return data
    except Exception as e:
        return {"error": "vision-failed", "detail": str(e)[:200]}


# ---------------------------------------------------------------- pitch ----
_PITCH_SYSTEM = (
    "You are a beverage-alcohol distributor sales rep. Write a 2-3 sentence pitch to a retail buyer "
    "using ONLY the figures in the JSON. Name the most over-spaced category and the biggest void "
    "(most negative gap). Confident, spoken, no preamble, no lists, no invented numbers."
)
_PITCH_SCHEMA = {"type": "object", "additionalProperties": False, "required": ["pitch"],
                 "properties": {"pitch": {"type": "string"}}}


def pitch(account, market, fmt, deltas, _client=None):
    if not enabled():
        return {"error": "llm-disabled", "detail": "ANTHROPIC_API_KEY not set"}
    payload = {"account": account, "market": market,
               "format": "on-premise" if fmt == "on" else "off-premise", "deltas": deltas}
    try:
        client = _client
        if client is None:
            import anthropic  # lazy
            client = anthropic.Anthropic()
        msg = client.messages.create(
            model=MODEL, max_tokens=600,
            system=_PITCH_SYSTEM,
            output_config={"format": {"type": "json_schema", "schema": _PITCH_SCHEMA}},
            messages=[{"role": "user", "content": json.dumps(payload, ensure_ascii=False)}],
        )
        text = "".join(getattr(b, "text", "") for b in msg.content if getattr(b, "type", None) == "text")
        data = json.loads(text) if text else None
        if not data or not data.get("pitch"):
            return {"error": "parse-failed", "raw": (text or "")[:300]}
        return {"pitch": data["pitch"].strip()}
    except Exception as e:
        return {"error": "pitch-failed", "detail": str(e)[:200]}
