"""dict_apply.py — the DATA-DICTIONARY layer underpinning the deterministic matcher.

The normalization VALUES (noise words, descriptor synonyms, size/format tokens) live as EDITABLE DATA in the
warehouse table `match_dict` (kind, match, value, mode) — NOT hardcoded in _core_key / class_type / resolve_brand.
The build consults them; the MDM Dictionary tab edits them; Claude can PROPOSE new entries (cheap) instead of
adjudicating every pair (expensive). Exhausting dictionary-driven normalization is the FREE tier of the
matching/adjudication layer — only what the dictionaries can't resolve goes to Claude.

  norm_token(tok)     per-token exact map: '' drops the token (noise/size/format), else the canonical synonym.
  load(kind)          {match_lower: value} for a kind ('noise' | 'descriptor' | 'size' | ...), cached.

Seeded once (seed()); thereafter grown by humans (Dictionary tab) or Claude proposals.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import warehouse

FIELDS = ["kind", "match", "value", "mode"]
_CACHE = {}

# initial vocabulary — the deterministic wins observed on real fragmentation (Bacardi Superior, etc.).
SEED = {
    # NOISE — channel / placeholder / format tokens that never distinguish a product. value '' => drop the token.
    "noise": ["online", "in-store", "instore", "pickup", "delivery", "web", "various", "assorted", "misc",
              "notspecified", "specified", "default", "sample", "miniature", "mini", "travel", "trv", "duty",
              "free", "pet", "plastic", "case", "single", "each", "unit", "new", "item", "product", "store"],
    # SIZE/format remnants that leak past the regex strip (e.g. "1.75lit", "ltr"). value '' => drop.
    "size": ["ltr", "lit", "liter", "litre", "ml", "cl", "oz", "gallon", "gal", "pk", "pack", "ct", "count"],
    # DESCRIPTOR SYNONYMS — the SAME expression written different ways collapses to one canonical token.
    "descriptor": {"blanco": "white", "plata": "white", "silver": "white", "platinum": "white",
                   "anejo": "anejo", "añejo": "anejo", "reposado": "reposado", "rye": "rye",
                   "extra": "", "smooth": "", "original": "", "classic": "", "premium": "", "genuine": ""},
    # CLASS derivation (CONTAINS) — a brand/keyword -> class, to FILL what the regex classifier misses (a beer
    # with no style word). Longest match wins. Keep entries UNAMBIGUOUS (avoid bare words that overlap types).
    "class": {"budweiser": "beer", "bud light": "beer", "coors": "beer", "miller lite": "beer",
              "miller high life": "beer", "michelob": "beer", "\bbusch": "beer", "modelo": "beer",
              "corona extra": "beer", "corona premier": "beer", "heineken": "beer", "stella artois": "beer",
              "guinness": "beer", "samuel adams": "beer", "sam adams": "beer", "spaten": "beer", "pabst": "beer",
              "yuengling": "beer", "blue moon": "beer", "dos equis": "beer", "pacifico": "beer",
              "natural light": "beer", "natty": "beer", "keystone": "beer", "rolling rock": "beer",
              "shiner": "beer", "lagunitas": "beer", "new belgium": "beer", "founders": "beer",
              "goose island": "beer", "bell's": "beer", "victoria": "beer",
              "white claw": "seltzer", "truly": "seltzer", "bud light seltzer": "seltzer",
              "twisted tea": "rtd", "mike's hard": "rtd", "smirnoff ice": "rtd", "high noon": "rtd",
              "montecristo": "cigar", "arturo fuente": "cigar", "rocky patel": "cigar", "romeo y julieta": "cigar",
              "cohiba": "cigar", "macanudo": "cigar", "la aroma de cuba": "cigar", "perla del mar": "cigar",
              "1 stick": "cigar", " maduro": "cigar", "acid kuba": "cigar", "don tomas": "cigar",
              "gatorade": "non-alc", "powerade": "non-alc", "master of mixes": "non-alc", "hop wtr": "non-alc",
              "mentos": "non-alc", "0.0 na": "non-alc", " n/a ": "non-alc"},
}


def load(kind=None):
    if "m" not in _CACHE:
        try:
            rows = warehouse.query("match_dict", "SELECT kind, match, value FROM t")
        except Exception:
            rows = []
        m = {}
        for r in rows:
            m.setdefault(r.get("kind"), {})[(r.get("match") or "").lower()] = (r.get("value") or "")
        _CACHE["m"] = m
    return _CACHE["m"].get(kind, {}) if kind is not None else _CACHE["m"]


def classify(text, kind="class"):
    """CONTAINS-mode lookup: the longest dictionary `match` that appears in `text` -> its value. Fills what the
    regex classifier misses (a beer BRAND with no style word: "Budweiser 25.4Z" -> beer). '' if nothing matches."""
    t = (text or "").lower()
    best_v, best_len = "", 0
    for m, v in load(kind).items():
        if len(m) > best_len and m in t:
            best_v, best_len = v, len(m)
    return best_v


def norm_token(tok, kinds=("noise", "size", "descriptor")):
    """Apply the exact per-token dictionaries in order. Returns '' to DROP the token, else the canonical value
    (the synonym target, or the token unchanged when nothing matches)."""
    t = (tok or "").lower()
    for k in kinds:
        d = load(k)
        if t in d:
            return d[t]                         # '' drops; else the canonical value
    return tok


def seed(log=print):
    """Write the initial match_dict (idempotent full rewrite). Safe to re-run; humans/Claude grow it after."""
    rows = []
    for w in SEED["noise"]:
        rows.append({"kind": "noise", "match": w, "value": "", "mode": "exact"})
    for w in SEED["size"]:
        rows.append({"kind": "size", "match": w, "value": "", "mode": "exact"})
    for m, v in SEED["descriptor"].items():
        rows.append({"kind": "descriptor", "match": m, "value": v, "mode": "exact"})
    for m, v in SEED["class"].items():
        rows.append({"kind": "class", "match": m, "value": v, "mode": "contains"})
    warehouse.write_parquet("match_dict", rows, fields=FIELDS)
    _CACHE.clear()
    log("[dict_apply] seeded match_dict: %d entries (%d noise, %d size, %d descriptor, %d class)"
        % (len(rows), len(SEED["noise"]), len(SEED["size"]), len(SEED["descriptor"]), len(SEED["class"])))
    return len(rows)


if __name__ == "__main__":
    seed()
