"""category_tree.py — category-conditional discriminator trees: the UPC-FREE backbone of item identity.

Why not just UPC: a UPC is a strong confirmation but its coverage is BIASED. Retailers/chains scan at POS so
their barcodes are accurate — but the supplier & distributor tier (the book that matters) barely uses UPC, so
the ~88% of records with no barcode are concentrated exactly where identity matters most. Identity therefore
can't lean on UPC; it has to fall out of ATTRIBUTES.

Why per-category: the attributes that isolate an item aren't universal — they BRANCH by category. You don't
distinguish a scotch (distillery → single malt? → age → cask strength) the way you distinguish a wine (varietal
→ region → vintage). So each category owns an ordered DECISION TREE of the discriminators that isolate an item
within it. Route a record down its category's tree → a category-aware discriminator VECTOR.

Matching rule (tri-state, honest about what we don't know):
  • SAME       — agree at every node BOTH records reach (and share family + brand).
  • DIFFERENT  — conflict at any one discriminating node (Macallan 12 ≠ 18; Patrón Silver ≠ Reposado).
  • AMBIGUOUS  — a discriminating node is present on one side only → hand off to the other redundant paths
                 (UPC, price band [[price-clustering-signal]], image [[image-match-signal]]).

This is ONE of several redundant match paths — the one that still works when there is no UPC.
"""
import re

# ── shared extractors (parse from the product NAME; fields/dicts refine upstream) ───────────────────────
def age_statement(name):
    m = re.search(r"\b(\d{1,2})\s*[- ]?\s*(?:year|yr|yo|y\.o\.)s?\b", name or "", re.I)
    return int(m.group(1)) if m else None


def cask_strength(name):
    return bool(re.search(r"\bcask[-\s]?strength|barrel[-\s]?(?:proof|strength)|full[-\s]?proof\b", name or "", re.I))


def abv_pct(name):
    m = re.search(r"(\d{2}(?:\.\d)?)\s*%\s*(?:abv|alc)", name or "", re.I)
    if m:
        return float(m.group(1))
    p = re.search(r"\b(\d{2,3}(?:\.\d)?)\s*proof\b", name or "", re.I)   # proof → ABV
    return round(float(p.group(1)) / 2, 1) if p else None


def size_ml(name):
    m = re.search(r"(\d+(?:\.\d+)?)\s*(ml|l|liter|litre)\b", name or "", re.I)
    if not m:
        return None
    v = float(m.group(1))
    return v if m.group(2).lower() == "ml" else v * 1000


def pack(name):
    m = re.search(r"\b(\d{1,2})\s*[- ]?\s*(?:pack|pk|ct|count|cans?|bottles?)\b", name or "", re.I)
    return int(m.group(1)) if m else 1


def container(name):
    n = (name or "").lower()
    if "can" in n:
        return "can"
    if re.search(r"\bbox\b|bag[-\s]?in[-\s]?box", n):
        return "box"
    if "bottle" in n:
        return "bottle"
    return None


def whisky_malt(name):
    n = (name or "").lower()
    for kw, v in (("single malt", "single_malt"), ("single grain", "single_grain"),
                  ("blended malt", "blended_malt"), ("blend", "blended")):
        if kw in n:
            return v
    return None


def whisky_style(name, cat):
    s = ((name or "") + " " + (cat or "")).lower()
    for kw, v in (("scotch", "scotch"), ("bourbon", "bourbon"), ("rye", "rye"), ("irish", "irish"),
                  ("tennessee", "tennessee"), ("canadian", "canadian"), ("japanese", "japanese")):
        if kw in s:
            return v
    return None


_TEQ = [("extra anejo", "extra_anejo"), ("extra añejo", "extra_anejo"), ("cristalino", "cristalino"),
        ("anejo", "anejo"), ("añejo", "anejo"), ("reposado", "reposado"), ("joven", "joven"),
        ("blanco", "blanco"), ("plata", "blanco"), ("silver", "blanco")]


def agave_class(name):
    n = (name or "").lower()
    for kw, v in _TEQ:
        if kw in n:
            return v
    return None


def wine_type(name, cat):
    n = ((name or "") + " " + (cat or "")).lower()
    if re.search(r"sparkling|champagne|prosecco|\bcava\b|spumante|\bbrut\b", n):
        return "sparkling"
    if "rosé" in n or re.search(r"\bros[eé]\b", n):
        return "rose"
    if re.search(r"\bwhite\b|chardonnay|sauvignon blanc|pinot grigio|riesling|moscato|albari", n):
        return "white"
    if re.search(r"\bred\b|cabernet|merlot|pinot noir|shiraz|syrah|malbec|zinfandel|tempranillo|sangiovese", n):
        return "red"
    return None


def vintage(name):
    m = re.search(r"\b(19[5-9]\d|20[0-4]\d)\b", name or "")
    return int(m.group(1)) if m else None      # None ⇒ non-vintage (itself a discriminator vs a dated bottle)


def non_alc(name):
    return bool(re.search(r"non[-\s]?alcoholic|alcohol[-\s]?free|\b0\.0\b|zero[-\s]?proof|dealcohol", name or "", re.I))


def flavor(name, brand):
    core = re.sub(re.escape(brand or ""), "", name or "", flags=re.I)
    m = re.search(r"\b(raspberry|vanilla|citrus|peach|mango|pineapple|coconut|lime|orange|grape|"
                  r"cucumber|watermelon|cherry|apple|honey|cinnamon|espresso|caramel)\b", core, re.I)
    return m.group(1).lower() if m else None


def _brand_norm(b):
    """Light brand normalization for node comparison. FULL brand resolution (store-prefix stripping, the
    dictionary crosswalk) is an UPSTREAM layer ([[dictionary-layer]]); here we only defang trivial variants
    ("The Macallan" == "Macallan") so a resolved brand compares cleanly."""
    if not b:
        return None
    b = re.sub(r"^the\s+", "", str(b).strip().lower())
    b = re.sub(r"[^a-z0-9 ]+", "", b)
    return re.sub(r"\s+", " ", b).strip() or None


def family(category, class_type=None, name=""):
    """Route a record to its category FAMILY (which tree to walk)."""
    s = " ".join(x for x in (class_type, category, name) if x).lower()
    if re.search(r"tequila|mezcal", s):
        return "agave"
    if re.search(r"scotch|whisky|whiskey|bourbon|\brye\b|single malt", s):
        return "whisky"
    if re.search(r"\bwine\b|cabernet|chardonnay|pinot|merlot|riesling|ros[eé]|sparkling|champagne|moscato", s):
        return "wine"
    if re.search(r"beer|lager|\bipa\b|\bale\b|stout|pilsner|seltzer|cider", s):
        return "beer"
    if re.search(r"vodka", s):
        return "vodka"
    if re.search(r"\bgin\b", s):
        return "gin"
    if re.search(r"\brum\b", s):
        return "rum"
    return "other"


# A node = (key, value, discriminating?). Missing (None) at a discriminating node ⇒ AMBIGUOUS, not DIFFERENT.
def vector(name, brand=None, category=None, class_type=None, fields=None):
    """Walk a record down its family's tree → an ordered list of (key, value, discriminating) nodes."""
    f = family(category, class_type, name)
    fl = fields or {}
    br = _brand_norm(brand or fl.get("brand"))
    sz = size_ml(name) or fl.get("size_ml")
    nodes = [("family", f, True), ("brand", br, True)]
    if f == "whisky":
        nodes += [("style", whisky_style(name, category) or fl.get("style"), True),
                  ("malt", whisky_malt(name), True),
                  ("age", age_statement(name) or fl.get("age"), True),
                  ("cask_strength", cask_strength(name), True),
                  ("abv", abv_pct(name) or fl.get("abv"), True),
                  ("size_ml", sz, True)]
    elif f == "agave":
        nodes += [("class", agave_class(name), True),
                  ("abv", abv_pct(name) or fl.get("abv"), False),
                  ("size_ml", sz, True)]
    elif f == "wine":
        nodes += [("wine_type", wine_type(name, category), True),
                  ("varietal", (fl.get("varietal") or "").lower() or None, True),
                  ("region", (fl.get("region") or fl.get("appellation") or "").lower() or None, False),
                  ("vintage", vintage(name) or fl.get("vintage"), True),
                  ("size_ml", sz, True)]
    elif f == "beer":
        nodes += [("non_alc", non_alc(name), True),
                  ("pack", pack(name), True),
                  ("container", container(name), True),
                  ("size_ml", sz, True)]
    else:  # vodka / gin / rum / other neutral+ spirits
        nodes += [("flavor", flavor(name, brand), True),
                  ("abv", abv_pct(name) or fl.get("abv"), True),
                  ("size_ml", sz, True),
                  ("pack", pack(name), False)]
    return nodes


def _norm(v):
    if isinstance(v, str):
        return v.strip().lower()
    if isinstance(v, float):
        return round(v, 1)
    return v


def compare(vec_a, vec_b):
    """Tri-state identity verdict from two discriminator vectors. Only DISCRIMINATING nodes count.
    Returns (verdict, detail): 'different' (first conflicting node), 'ambiguous' (nodes missing on one
    side), or 'same'. Non-discriminating nodes (region, secondary abv) never force a split."""
    a = {k: (val, disc) for k, val, disc in vec_a}
    b = {k: (val, disc) for k, val, disc in vec_b}
    missing = []
    for k in set(a) | set(b):
        va, da = a.get(k, (None, False))
        vb, db = b.get(k, (None, False))
        if not (da or db):
            continue
        na, nb = _norm(va), _norm(vb)
        if na is not None and nb is not None:
            if na != nb:
                return "different", "%s: %r != %r" % (k, va, vb)
        elif na is not None or nb is not None:
            missing.append(k)                     # present one side only → can't confirm on attributes alone
    return ("ambiguous", missing) if missing else ("same", None)


import hashlib


def key_from_vector(vec):
    """(md5, canon) from an already-computed discriminator vector — joins the DISCRIMINATING nodes that carry
    a value (missing node omitted → conservative, never merges on absence). Split out so callers that already
    hold the vector don't recompute it."""
    parts = []
    for k, v, disc in vec:
        nv = _norm(v)
        if disc and nv is not None and nv != "":
            parts.append("%s=%s" % (k, nv))
    canon = "|".join(parts)
    if not canon:
        return None, ""
    return hashlib.md5(canon.encode()).hexdigest(), canon


def key(name, brand=None, category=None, class_type=None, fields=None):
    """Deterministic UPC-FREE identity key from the category discriminator vector — the match key for the
    ~88% of records with no barcode. Same discriminators across sources → same key → a corroborated match with
    no UPC in sight. Returns (md5_hash, human_canon). Empty canon (nothing extracted) → (None, '')."""
    return key_from_vector(vector(name, brand=brand, category=category, class_type=class_type, fields=fields))


def _selftest():
    def V(n, **kw):
        return vector(n, brand=kw.pop("brand", None), category=kw.pop("category", None), **kw)
    # scotch: distillery same, age splits
    mac12 = V("The Macallan 12 Year Old Sherry Oak Single Malt Scotch Whisky 750ml", brand="The Macallan")
    mac12b = V("Macallan 12 Yr Single Malt Scotch 750 mL", brand="Macallan")   # supplier-style mangled string
    mac18 = V("The Macallan 18 Year Old Single Malt Scotch Whisky 750ml", brand="The Macallan")
    assert compare(mac12, mac12b)[0] == "same", compare(mac12, mac12b)
    assert compare(mac12, mac18)[0] == "different"
    # single malt vs blend (same-ish shelf, different item)
    laga = V("Lagavulin 16 Year Old Single Malt Scotch 750ml", brand="Lagavulin")
    jw = V("Johnnie Walker Black Label Blended Scotch Whisky 750ml", brand="Johnnie Walker")
    assert compare(laga, jw)[0] == "different"
    # tequila class splits
    ps = V("Patron Silver Tequila 750ml", brand="Patron", category="Tequila")
    pr = V("Patron Reposado Tequila 750ml", brand="Patron", category="Tequila")
    assert compare(ps, pr)[0] == "different"
    # wine vintage splits; same producer+varietal+vintage merges across sources
    m19 = V("Meiomi Pinot Noir 2019 750ml", brand="Meiomi", category="Red Wine", fields={"varietal": "Pinot Noir"})
    m19b = V("Meiomi Pinot Noir Red Wine 750 mL 2019", brand="Meiomi", fields={"varietal": "pinot noir"})
    m20 = V("Meiomi Pinot Noir 2020 750ml", brand="Meiomi", category="Red Wine", fields={"varietal": "Pinot Noir"})
    assert compare(m19, m19b)[0] == "same"
    assert compare(m19, m20)[0] == "different"
    # ambiguous: age stated on one side only → defer to redundant paths, don't force same/different
    mac_na = V("Macallan Single Malt Scotch 750ml", brand="Macallan")
    assert compare(mac12, mac_na)[0] == "ambiguous"
    # size splits (different shelf facing)
    ps175 = V("Patron Silver Tequila 1.75L", brand="Patron", category="Tequila")
    assert compare(ps, ps175)[0] == "different"
    # key(): same discriminators across mangled strings → same key; a real discriminator change → different key
    k12a = key("The Macallan 12 Year Old Single Malt Scotch Whisky 750ml", brand="The Macallan")[0]
    k12b = key("Macallan 12 Yr Single Malt Scotch 750 mL", brand="Macallan")[0]
    k18 = key("The Macallan 18 Year Old Single Malt Scotch 750ml", brand="The Macallan")[0]
    assert k12a and k12a == k12b and k12a != k18
    print("category_tree self-test OK")


if __name__ == "__main__":
    _selftest()
