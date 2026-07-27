"""self_heal.py — AI-assisted parse re-derivation for ANY scraper (Layer 2 self-healing).

Site drift is the one failure mode retry-with-backoff CANNOT fix: the pull "succeeds" but the layout
changed, so a column/field the deterministic parser expected is gone. When a scraper detects that (it
`self-reports degraded`), it asks an LLM to RE-DERIVE the mapping from the live response + a sample, then
retries the parse — no human in the loop. This was TTB-COLA-only; it's now source-agnostic, so any degraded
scraper can heal itself. Two shapes cover what scrapers actually parse:

  • heal_columns(header, sample, needed, hints, context)  — TABULAR: field → 0-based column index.
  • heal_fields(sample_record, needed, hints, context)    — KEYED/JSON: field → source key in the record.

`propose_mapping(...)` stays as the TTB back-compat wrapper (ttb_cola_scraper calls it unchanged).

OFF by default. Enable with:
    AGENT_SELF_HEAL=1                 turn the LLM re-derivation on
    ANTHROPIC_API_KEY=...             read by the SDK
    AGENT_LLM_MODEL=claude-opus-4-8   optional override (default: claude-opus-4-8)
When off (no key / flag) the scrapers run purely deterministically and import no Anthropic dependency.
The proposal is a MAPPING only — the scraper still validates + applies it, so a bad LLM guess can't land
garbage (a wrong index/key just yields empty/failed fields the caller already handles).
"""
import json
import os
import re

SELF_HEAL = os.environ.get("AGENT_SELF_HEAL", "").lower() in ("1", "true", "yes")
MODEL = os.environ.get("AGENT_LLM_MODEL", "claude-opus-4-8")

# TTB COLA field hints (the original caller). Other scrapers pass their own `hints`.
_TTB_HINTS = {
    "permit": "the permit or plant registry number", "serial": "the serial number",
    "completed": "the completed / approved date", "brand": "the brand name",
    "origin": "the origin (a US state or country)", "classtype": "the class / type of the product",
}
_cache = {}   # (kind, signature) -> mapping


def enabled():
    return SELF_HEAL


def _ask(prompt, want_int):
    """One LLM round-trip → parsed JSON mapping. `want_int` keeps only non-negative int values (column
    indices); else keeps non-empty string values (source keys). Raises on transport error (caller catches)."""
    import anthropic   # lazy — only when self-heal is on
    resp = anthropic.Anthropic().messages.create(
        model=MODEL, max_tokens=400, messages=[{"role": "user", "content": prompt}])
    text = "".join(b.text for b in resp.content if getattr(b, "type", None) == "text")
    m = re.search(r"\{.*\}", text, re.S)
    data = json.loads(m.group(0)) if m else {}
    if want_int:
        return {k: int(v) for k, v in data.items() if isinstance(v, int) and v >= 0}
    return {k: str(v) for k, v in data.items() if isinstance(v, str) and v}


def heal_columns(header_cells, sample_cells, needed, hints=None, context=None):
    """TABULAR re-derivation: {field: 0-based column index} for the `needed` fields the deterministic
    header match missed. `hints` = {field: human description}; `context` = what/where broke. Cached per
    (header, needed). {} when disabled or on any error."""
    if not (SELF_HEAL and needed and header_cells):
        return {}
    hints = hints or {}
    sig = ("cols", tuple(header_cells), tuple(needed))
    if sig in _cache:
        return _cache[sig]
    try:
        cols = [{"index": i, "header": h, "sample": sample_cells[i] if i < len(sample_cells) else ""}
                for i, h in enumerate(header_cells)]
        prompt = (
            "A web scraper's column mapping broke after a layout change%s. Here are the table's columns "
            "(0-based), each with its header text and a sample value from the first data row:\n\n"
            % (" (%s)" % context if context else "")
            + json.dumps(cols, ensure_ascii=False, indent=2)
            + "\n\nMap each of these fields to the best-matching column index (use -1 if none fits):\n"
            + "\n".join("- %s: %s" % (k, hints.get(k, k)) for k in needed)
            + '\n\nRespond with ONLY a JSON object of field -> index, e.g. {"brand": 5}. No prose.')
        out = {k: v for k, v in _ask(prompt, want_int=True).items() if k in needed}
        _cache[sig] = out
        return out
    except Exception:
        return {}


def heal_fields(sample_record, needed, hints=None, context=None):
    """KEYED/JSON re-derivation: {field: source key} — which key in a sample record (dict) holds each
    `needed` field, after an API/JSON shape change. For the many scrapers that parse dicts, not tables.
    The returned key is guaranteed to exist in the sample. {} when disabled or on any error."""
    if not (SELF_HEAL and needed and isinstance(sample_record, dict) and sample_record):
        return {}
    hints = hints or {}
    sig = ("fields", tuple(sorted(sample_record.keys())), tuple(needed))
    if sig in _cache:
        return _cache[sig]
    try:
        sample = {k: (str(v)[:80] if not isinstance(v, (dict, list)) else type(v).__name__)
                  for k, v in list(sample_record.items())[:60]}
        prompt = (
            "A web scraper's field mapping broke after an API/JSON shape change%s. Here is one sample "
            "record (key -> a short sample of its value):\n\n"
            % (" (%s)" % context if context else "")
            + json.dumps(sample, ensure_ascii=False, indent=2)
            + "\n\nFor each field below, give the record KEY that holds it (use \"\" if none fits):\n"
            + "\n".join("- %s: %s" % (k, hints.get(k, k)) for k in needed)
            + '\n\nRespond with ONLY a JSON object of field -> key, e.g. {"brand": "brandName"}. No prose.')
        out = {k: v for k, v in _ask(prompt, want_int=False).items()
               if k in needed and v in sample_record}
        _cache[sig] = out
        return out
    except Exception:
        return {}


def propose_mapping(header_cells, sample_cells, needed):
    """TTB COLA back-compat wrapper (ttb_cola_scraper calls this) — heal_columns with the TTB hints."""
    return heal_columns(header_cells, sample_cells, needed, hints=_TTB_HINTS, context="TTB COLA results table")
