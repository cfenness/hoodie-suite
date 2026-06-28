"""self_heal.py — AI-assisted column remapping for the COLA parser (Layer 2).

When the deterministic header match (ttb_cola_scraper.header_map) can't recognize one
of the expected columns, the scraper asks an LLM to re-derive that column's index from
the live page's header row + a sample data row, then retries the parse. This catches
structural changes the header heuristics miss — without a human in the loop.

OFF by default. Enable with:
    AGENT_SELF_HEAL=1                 turn the LLM remap on
    ANTHROPIC_API_KEY=...             read by the SDK
    AGENT_LLM_MODEL=claude-opus-4-8   optional override (default: claude-opus-4-8)

When off (no key / flag), the scraper runs purely deterministically and imports no AWS
or Anthropic dependencies.
"""
import json, os, re

SELF_HEAL = os.environ.get("AGENT_SELF_HEAL", "").lower() in ("1", "true", "yes")
MODEL     = os.environ.get("AGENT_LLM_MODEL", "claude-opus-4-8")

# Human descriptions of each field, to orient the model.
_HINTS = {
    "permit":    "the permit or plant registry number",
    "serial":    "the serial number",
    "completed": "the completed / approved date",
    "brand":     "the brand name",
    "origin":    "the origin (a US state or country)",
    "classtype": "the class / type of the product",
}
_cache = {}   # (header tuple, needed tuple) -> {key: index}


def enabled():
    return SELF_HEAL


def propose_mapping(header_cells, sample_cells, needed):
    """Return {field: 0-based column index} for the `needed` fields the deterministic
    matcher missed, by asking the LLM. Cached per (header, needed). Returns {} when
    disabled or on any error — the caller falls back to positional indices."""
    if not (SELF_HEAL and needed and header_cells):
        return {}
    sig = (tuple(header_cells), tuple(needed))
    if sig in _cache:
        return _cache[sig]
    try:
        import anthropic   # lazy — only when self-heal is on
        client = anthropic.Anthropic()
        cols = [{"index": i, "header": h,
                 "sample": sample_cells[i] if i < len(sample_cells) else ""}
                for i, h in enumerate(header_cells)]
        prompt = (
            "A web scraper's column mapping for the TTB COLA results table broke after a "
            "layout change. Here are the table's columns (0-based), each with its header "
            "text and a sample value from the first data row:\n\n"
            + json.dumps(cols, ensure_ascii=False, indent=2)
            + "\n\nMap each of these fields to the best-matching column index (use -1 if "
              "no column fits):\n"
            + "\n".join(f"- {k}: {_HINTS.get(k, k)}" for k in needed)
            + "\n\nRespond with ONLY a JSON object of field -> index, e.g. "
              '{"brand": 5, "origin": 6}. No prose.'
        )
        resp = client.messages.create(
            model=MODEL, max_tokens=400,
            messages=[{"role": "user", "content": prompt}],
        )
        text = "".join(b.text for b in resp.content if getattr(b, "type", None) == "text")
        m = re.search(r"\{.*\}", text, re.S)
        data = json.loads(m.group(0)) if m else {}
        out = {k: int(v) for k, v in data.items()
               if k in needed and isinstance(v, int) and v >= 0}
        _cache[sig] = out
        return out
    except Exception:
        return {}
