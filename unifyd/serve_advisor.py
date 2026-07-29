#!/usr/bin/env python3
"""serve_advisor.py — "a new take on the Old Fashioned" — Claude recommending off the REAL menus.

`serve_locator.py` answers "who pours this near me" and returns rows. This layer answers the question
a person actually asks in a bar — *which one should I go to, and why is it different* — because the
menus carry the BUILD:

    Old Fashioned                | bourbon, demerara, ango & orange bitters
    Million Dollar Old Fashion   | rye, black walnut bitters, luxardo
    Espresso Martini             | vodka, espresso liqueur, cold brew

That `description` is the grounding. "Chris's bar makes his with Bacardi 8 and peach simple syrup" is
exactly what a description states, so a recommendation that says *this one swaps rye for aged rum and
sweetens with peach* is READ OFF THE MENU, not invented.

WHAT THIS DELIBERATELY CANNOT DO — the other half of the idea, verbatim: "the reviews speak for
themselves." We hold no review text. Google Places is ToS-limited to a place_id and a live rating with
no bodies; the 1.4M UberEats payloads carry retail catalog fields only (`endorsementAnalyticsTag` is
the sole social-proof key, and it is a tag, not a sentence). So the model is told, flatly, that it has
no popularity or quality evidence and must not imply any. A recommendation is about the BUILD and the
PRICE — the two things we can prove.

THE GUARD — this is the load-bearing part. An LLM writing prose about bars will name a bar. So the
model does not get to emit free text about a venue: it must return the INDEX of the serve it is
describing, and every returned index is re-resolved against the deterministic rows here. Anything that
does not resolve is DROPPED and counted, never shown. The venue, drink, price and city a user reads
always come from the warehouse row — the model supplies only the `why`.

    ANTHROPIC_API_KEY=... python3 unifyd/serve_advisor.py "old fashioned"
"""
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

_MODEL = os.environ.get("SERVE_LLM_MODEL", "claude-opus-5")
_MAX_SERVES = int(os.environ.get("SERVE_ADVISOR_MAX", "40"))

_TOOL = {
    "name": "serve_picks",
    "description":
        "Pick the most interesting serves from a list of REAL cocktails on REAL menus and say what "
        "makes each one different. Ground every claim in the build (the description) or the price. "
        "You have NO reviews, ratings, popularity or quality data — never imply you do.",
    "input_schema": {"type": "object", "properties": {
        "picks": {"type": "array", "description":
                  "2-4 serves worth going out of the way for, best first. Skip the list entirely "
                  "rather than pad it with a serve you have nothing to say about.",
                  "items": {"type": "object", "properties": {
                      "index": {"type": "integer",
                                "description": "the `#` of the serve from the list you were given"},
                      "angle": {"type": "string", "description":
                                "3-6 words naming the twist, e.g. 'aged rum instead of rye' or "
                                "'classic build, demerara'"},
                      "why": {"type": "string", "description":
                              "One or two sentences on how this build differs from the classic and "
                              "who would like it. Cite ingredients that appear in the description. "
                              "If the description is empty say so plainly rather than guessing."}},
                      "required": ["index", "angle", "why"]}},
        "classic": {"type": ["string", "null"], "description":
                    "One sentence on the canonical build of the drink searched for, so the twists "
                    "have something to be twists FROM. General bartending knowledge is fine here — "
                    "this is the only field not read off the menus."}},
        "required": ["picks"]},
}


def available():
    """(bool, reason). Never returns a bare False — a capability that goes quiet when it is switched
    off is indistinguishable from one that is broken."""
    if os.environ.get("SERVE_NO_LLM") == "1":
        return False, "recommendations disabled (SERVE_NO_LLM=1)"
    if not os.environ.get("ANTHROPIC_API_KEY", "").strip():
        return False, "recommendations unavailable — ANTHROPIC_API_KEY is not set"
    try:
        import anthropic  # noqa: F401
    except ImportError:
        return False, "recommendations unavailable — the `anthropic` package is not installed"
    return True, ""


def render(serves, cap=None):
    """The serves as the model sees them: numbered, and ONLY the fields we can stand behind."""
    lines = []
    for i, s in enumerate(serves[:cap or _MAX_SERVES]):
        price = "$%.2f" % s["price"] if s.get("price") is not None else "price not listed"
        where = ", ".join(x for x in (s.get("city"), s.get("state")) if x)
        lines.append("#%d  %s — %s | %s | %s%s\n     build: %s"
                     % (i, s.get("venue") or "?", s.get("drink") or "?", price, where or "?",
                        (" | %.1f mi" % s["distance_mi"]) if s.get("distance_mi") is not None else "",
                        (s.get("description") or "").strip() or "(no build listed on the menu)"))
    return "\n".join(lines)


_PROMPT = """A person searched for "%(q)s" and these are the actual cocktails on actual menus near them.

%(menu)s

Call `serve_picks` with the 2-4 most interesting ones and say what makes each different.

Rules, and they are not negotiable:
- Refer to a serve ONLY by its `#`. Do not retype venue names, drink names or prices — those are
  filled in from the source data, and anything you write about them is discarded.
- Every claim about a drink must come from its `build` line or its price. If a build is missing, say
  the menu does not list it — do not infer what is in the glass from the drink's name.
- You have NO reviews, ratings, popularity, awards or crowd data. Never write or imply "highly rated",
  "a local favourite", "people love", "known for", or anything else that would need evidence we do
  not hold.
- Prefer serves whose build actually DEPARTS from the classic. A menu of four identical builds is a
  fine thing to say plainly — return fewer picks rather than manufacturing distinctions."""


def recommend(query, serves, log=print):
    """{ok, picks, classic, reason, dropped} — picks carry the ORIGINAL serve row, not model prose.

    `dropped` counts picks that referenced a serve that does not exist. It is surfaced rather than
    swallowed: a non-zero count means the model is reaching, and that is worth seeing.
    """
    # Emptiness first: "there is nothing here to recommend" is true whether or not the model is
    # reachable, and it is the more useful thing to say. Reporting a missing API key when the real
    # answer is "we found no serves" sends someone to fix the wrong problem.
    if not serves:
        return {"ok": False, "picks": [], "reason": "no serves to recommend from", "dropped": 0}
    ok, reason = available()
    if not ok:
        return {"ok": False, "picks": [], "reason": reason, "dropped": 0}

    import anthropic
    try:
        msg = anthropic.Anthropic().messages.create(
            model=_MODEL, max_tokens=2000, tools=[_TOOL],
            tool_choice={"type": "tool", "name": "serve_picks"},
            messages=[{"role": "user", "content": _PROMPT % {"q": query, "menu": render(serves)}}])
    except Exception as e:                                    # noqa: BLE001
        log("[advisor] Claude call failed: %s" % str(e)[:120])
        return {"ok": False, "picks": [], "reason": "recommendation call failed: %s" % str(e)[:90],
                "dropped": 0}

    payload = next((b.input for b in msg.content if b.type == "tool_use"), None) or {}
    return verify(payload, serves)


def verify(payload, serves):
    """Re-attach every pick to its warehouse row; drop what does not resolve.

    Split out from `recommend` so it is testable without an API key — the guard is the part most
    worth a test, since it is what stands between a language model and a user driving somewhere.
    """
    picks, dropped = [], 0
    seen = set()
    for p in (payload.get("picks") or []):
        i = p.get("index")
        if not isinstance(i, int) or i < 0 or i >= len(serves) or i in seen:
            dropped += 1
            continue
        seen.add(i)
        row = dict(serves[i])
        row["angle"] = (p.get("angle") or "").strip()
        row["why"] = (p.get("why") or "").strip()
        if not row["why"]:
            dropped += 1
            continue
        picks.append(row)
    classic = (payload.get("classic") or "").strip() or None
    return {"ok": True, "picks": picks, "classic": classic, "reason": "", "dropped": dropped}


def main(argv):
    import serve_locator
    q = " ".join(argv) or "old fashioned"
    res = serve_locator.serves(q, state="FL")
    print("query: %r  (%d placed serves)" % (q, len(res["serves"])))
    rec = recommend(q, res["serves"])
    if not rec["ok"]:
        print("  %s" % rec["reason"])
        return 0
    if rec.get("classic"):
        print("  the classic: %s" % rec["classic"])
    for p in rec["picks"]:
        print("\n  %s — %s  %s"
              % (p["venue"], p["drink"], "$%.2f" % p["price"] if p.get("price") is not None else ""))
        print("    %s" % p["angle"])
        print("    %s" % p["why"])
    if rec["dropped"]:
        print("\n  %d pick(s) dropped — referenced a serve that isn't in the results" % rec["dropped"])
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
