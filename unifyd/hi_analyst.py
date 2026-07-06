"""hi_analyst.py — the real Claude analyst behind Hoodie Intelligence's Q&A.

The front-end's deterministic engine (rbComputeMeasure) already answers the questions
its intent-parser can classify. This handles everything it CAN'T — arbitrary, multi-part,
oddly-phrased analytical questions — by handing Claude the user's question plus a table of
EXACT COMPUTED FIGURES from the book and asking it to interpret, never fabricate.

Same contract as analyze.py: deterministic Python owns the numbers, the LLM owns the
interpretation + phrasing. OFF unless ANTHROPIC_API_KEY is set (anthropic imported lazily),
so it adds no hard dependency and degrades to the front-end's deterministic path.
"""
import json, os

MODEL = os.environ.get("AGENT_LLM_MODEL", "claude-opus-4-8")


def enabled():
    return bool(os.environ.get("ANTHROPIC_API_KEY"))


SYSTEM = (
    "You are Hoodie Intelligence, a sharp beverage-alcohol distributor market analyst. "
    "You are given a user QUESTION and a table of EXACT COMPUTED FIGURES (`facts`) drawn from "
    "the anonymized book, plus the available VOCAB (categories, regions, channels, account "
    "classes, measures). Rules:\n"
    "1. Use ONLY numbers that appear in `facts`. NEVER invent or estimate a figure. If the "
    "question needs a number that isn't in `facts`, say plainly what you'd need rather than guessing.\n"
    "2. Lead with the answer. Be decisive and concise — a distributor rep is reading this between "
    "accounts, not a 10-page report.\n"
    "3. Frame real openings as opportunity ('good news — there's demand you're leaving uncaptured'), "
    "not as a scold. Keep the numbers exact; only the wording is encouraging.\n"
    "4. For each stat card and each cited figure, echo the exact value string from `facts` and the "
    "`measure`+`dims` it came from, so the app can drill through to lineage.\n"
    "Return JSON per the schema: a short headline, 2-4 stat cards, a 1-3 short-paragraph narrative "
    "(HTML <p> tags), and 3 concise follow-up questions."
)

SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "required": ["headline", "stats", "narrative", "followups"],
    "properties": {
        "headline": {"type": "string", "description": "Short answer title, <= 90 chars"},
        "stats": {
            "type": "array", "minItems": 1, "maxItems": 4,
            "items": {
                "type": "object", "additionalProperties": False,
                "required": ["label", "value"],
                "properties": {
                    "label": {"type": "string"},
                    "value": {"type": "string", "description": "exact value string from facts"},
                    "delta": {"type": "string"},
                    "dir": {"type": "string", "enum": ["pos", "neg", "flat", ""]},
                    "measure": {"type": "string"},
                    "dims": {"type": "object", "additionalProperties": {"type": "string"}},
                },
            },
        },
        "narrative": {"type": "string", "description": "1-3 short paragraphs as HTML <p>...</p>"},
        "followups": {"type": "array", "maxItems": 3, "items": {"type": "string"}},
    },
}


def ask(question, facts, vocab=None, _client=None):
    """Answer a free-form question grounded in `facts`. Returns the validated answer dict,
    or {"error": ...} so the caller can fall back to the deterministic synthesizer."""
    if not enabled():
        return {"error": "llm-disabled", "detail": "ANTHROPIC_API_KEY not set"}
    if not question or not isinstance(facts, list) or not facts:
        return {"error": "no-grounding", "detail": "question + non-empty facts required"}
    payload = json.dumps({
        "question": question,
        "vocab": vocab or {},
        "facts": facts[:1200],   # bound the grounding table
    }, ensure_ascii=False, default=str)
    try:
        client = _client
        if client is None:
            import anthropic  # lazy — no hard dep
            client = anthropic.Anthropic()
        with client.messages.stream(
            model=MODEL, max_tokens=4000,
            system=[{"type": "text", "text": SYSTEM, "cache_control": {"type": "ephemeral"}}],
            thinking={"type": "adaptive"},
            output_config={"effort": "medium",
                           "format": {"type": "json_schema", "schema": SCHEMA}},
            messages=[{"role": "user", "content":
                       "Answer this question from the figures. Return the JSON object.\n\n" + payload}],
        ) as s:
            msg = s.get_final_message()
        text = "".join(b.text for b in msg.content if getattr(b, "type", None) == "text")
        data = json.loads(text) if text.strip().startswith("{") else _loose_json(text)
        if not isinstance(data, dict) or "narrative" not in data:
            return {"error": "parse-failed", "raw": (text or "")[:300]}
        return data
    except Exception as e:
        return {"error": "ask-failed", "detail": str(e)[:200]}


def _loose_json(text):
    a, b = text.find("{"), text.rfind("}")
    if a >= 0 and b > a:
        try:
            return json.loads(text[a:b + 1])
        except Exception:
            return None
    return None
