"""Resolve a spoken/garbled product name to the item master.

WHY THIS EXISTS: Hoodie Relations captures visit notes by voice, and speech-to-text has
no product vocabulary — it spells brand names phonetically. Measured live:

    "Chivas 18"   -> "Chivas shoes"
    "Mer Soleil"  -> "immersely" / "Mar Soleil"
    "Rittenhouse" -> "Rittenhouse Riot"
    "Absolut"     -> "absolute calm"

Those mangled strings become the product tags on a visit, so without correction every
downstream link (goals, placements, distribution gaps) inherits the corruption.

An LLM can sometimes guess these from context, but it does so INCONSISTENTLY — the same
input corrected on one run and not the next. A rep cannot trust a tagging layer that fixes
a name on Tuesday and not on Wednesday. This is deterministic: same input, same output,
every time, matched against the real master rather than against plausibility.

TWO GRAINS, deliberately. "Bacardi" is a BRAND family; "Bacardi Ocho" is one ITEM within
it, a sibling of Superior and Reserva. They are not the same thing and resolve against
different tables — collapsing them would attribute a whole family's mention to one SKU.

stdlib only (difflib + a small phonetic key). No new dependency.
"""
import difflib
import re

# ---------------------------------------------------------------- normalisation

_NOISE = re.compile(r"\b(the|a|an|of|and|by|brand|wine|spirits|co|company|inc|ltd)\b")
_PUNCT = re.compile(r"[^a-z0-9 ]+")
_WS = re.compile(r"\s+")

# Age statements survive normalisation as digits: "Chivas 18" vs "Chivas 12" are DIFFERENT
# products, and dropping the number would happily match one to the other.
_NUMWORD = {
    "eight": "8", "ten": "10", "twelve": "12", "fifteen": "15", "eighteen": "18",
    "twenty": "20", "twentyone": "21", "twentyfive": "25", "thirty": "30",
    "ocho": "8", "diez": "10", "doce": "12",
}


def norm(s):
    """Lowercase, strip punctuation and filler, fold spelled-out numbers to digits."""
    s = (s or "").lower().strip()
    s = _PUNCT.sub(" ", s)
    s = _WS.sub(" ", s)
    toks = [_NUMWORD.get(t, t) for t in s.split()]
    s = " ".join(toks)
    s = _NOISE.sub(" ", s)
    return _WS.sub(" ", s).strip()


def phon(s):
    """A crude phonetic key — enough to bridge ASR's usual substitutions.

    Not Soundex: Soundex keeps the first letter, which breaks exactly the cases we care
    about ("absolute calm" vs "absolut"). This folds the sounds that speech-to-text most
    often swaps and drops vowels after the first, so "chivas"/"chevas" and
    "rittenhouse"/"ridenhouse" collapse together.
    """
    s = norm(s)
    if not s:
        return ""
    out = []
    for word in s.split():
        if word.isdigit():
            out.append(word)  # numbers are identity, never phonetic
            continue
        w = word
        w = re.sub(r"[aeiou]+", "a", w)      # all vowels alike
        w = re.sub(r"(ph|f)", "f", w)
        w = re.sub(r"(ck|k|q|c)", "k", w)
        w = re.sub(r"(s|z|x)", "s", w)
        w = re.sub(r"(d|t)", "t", w)
        w = re.sub(r"(g|j)", "j", w)
        w = re.sub(r"(v|b)", "v", w)         # "chivas"/"chibas"
        w = re.sub(r"(.)\1+", r"\1", w)      # collapse doubles
        out.append(w)
    return " ".join(out)


# ---------------------------------------------------------------- scoring

def _tok_overlap(a, b):
    ta, tb = set(a.split()), set(b.split())
    if not ta or not tb:
        return 0.0
    return len(ta & tb) / float(len(ta | tb))


def score(query, candidate):
    """0..1 similarity. Deterministic and explainable — every term is inspectable.

    A NUMBER MISMATCH IS FATAL. "Chivas 18" must never resolve to "Chivas 12" no matter
    how similar the rest reads; an age statement is the product's identity, not a detail.
    """
    q, c = norm(query), norm(candidate)
    if not q or not c:
        return 0.0
    if q == c:
        return 1.0

    qnums = {t for t in q.split() if t.isdigit()}
    cnums = {t for t in c.split() if t.isdigit()}
    if qnums and cnums and not (qnums & cnums):
        return 0.0

    lit = difflib.SequenceMatcher(None, q, c).ratio()
    tok = _tok_overlap(q, c)
    ph = difflib.SequenceMatcher(None, phon(query), phon(candidate)).ratio()

    # Weighted so the phonetic term can rescue a badly-heard name, but never on its own —
    # unrelated products often sound vaguely alike, and a confident wrong link is the
    # worst outcome here.
    s = (0.40 * lit) + (0.25 * tok) + (0.35 * ph)

    # A DISTINCTIVE TOKEN MATCHING EXACTLY is the strongest evidence there is. When ASR
    # mangles a product it usually wrecks one word and leaves the rest: "Rittenhouse Riot"
    # keeps "rittenhouse" intact, "Chivas shoes" keeps "chivas". A rare long token is
    # nearly identifying on its own, and the character-level terms above badly
    # under-weight it because the surrounding words differ so much.
    shared = set(q.split()) & set(c.split())
    longest = max([len(t) for t in shared if not t.isdigit()] or [0])
    if longest >= 5:
        s = max(s, min(0.60 + 0.03 * (longest - 4), 0.88))

    # A query that is a clean prefix/subset of the candidate is usually right: a rep says
    # "Chivas 18", the master says "Chivas Regal 18 Year Blended Scotch".
    if q in c or set(q.split()) <= set(c.split()):
        s = max(s, 0.90 if qnums <= cnums else 0.82)
    return round(min(s, 0.999), 4)


# ---------------------------------------------------------------- resolution

MIN_SCORE = 0.55       # below this we report nothing rather than a bad guess
AMBIGUOUS_GAP = 0.05   # top two this close is NOT a match — hand back both


def resolve(query, candidates, limit=5):
    """Rank `candidates` against `query`.

    candidates: [{"id":..., "name":..., "grain":"brand"|"item", ...}]
    Returns (ranked, verdict) where verdict is exact | fuzzy | ambiguous | none.

    AMBIGUITY IS AN ANSWER. Two near-tied candidates return `ambiguous` with both, never
    the marginal winner — a wrong item link is worse than no link, because everything
    downstream would silently be about the wrong product with nothing appearing broken.
    """
    scored = []
    for c in candidates or []:
        sc = score(query, c.get("name") or "")
        if sc >= MIN_SCORE:
            scored.append(dict(c, score=sc))
    scored.sort(key=lambda r: (-r["score"], r.get("name") or ""))
    top = scored[:limit]

    if not top:
        return [], "none"
    if top[0]["score"] >= 0.98:
        return top, "exact"
    if len(top) > 1 and (top[0]["score"] - top[1]["score"]) < AMBIGUOUS_GAP:
        return top, "ambiguous"
    return top, "fuzzy"
