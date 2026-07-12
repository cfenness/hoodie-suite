"""precleanse.py — normalize raw staged rows BEFORE matching. The matcher only collapses IDENTICAL keys, so
the same product from different sources has to be made to look the same first. This is that input-cleaning stage
(the "precleanse"), and it's the highest-leverage matching fix: national brands sit in 10-13 of our sources but
don't corroborate because their strings differ. Two deterministic passes:

  1. BRAND canonicalization — collapse brand strings that mean the same brand to ONE canonical display, keyed by
     an aggressive normalization (lowercase; strip possessive + plural 's; punctuation; generic suffixes like
     Co/Distillery/Wines). Fixes the #1 corroboration killer: "TITO HANDMADE" and "Tito's Handmade" landing in
     two different brand blocks, so the same 750ml vodka never corroborates across sources.
  2. NAME cleansing — Title-case ALL-CAPS names, drop pure-dash + format/pack noise, and de-duplicate the brand
     when it is repeated at the head of the product name ("TITO HANDMADE TEXAS VODKA" -> "Texas Vodka").

Size extraction is owned by build_product_master. Ambiguous same-brand name variants that survive this
("Texas Vodka" vs "Vodka") are for the Claude adjudication tier, not deterministic rules.
"""
import collections
import html
import re
import unicodedata


def unescape(s):
    """HTML-unescape source text — the TTB COLA feed stores names entity-encoded (D&#39;AQUINO, Ch&acirc;teau,
    Domaine &amp; Fils), which otherwise turns `&#39;` into a stray '39' token and shatters the brand. Applied
    to BOTH display and match keys (it fixes the actual characters)."""
    return html.unescape(str(s or ""))


def deaccent(s):
    """Fold accents/diacritics to ASCII for MATCHING keys only (Château->Chateau, José->Jose, Rémy->Remy), so
    accented and un-accented spellings of the same brand/product corroborate across sources. HTML-unescapes
    first (so &acirc; -> â -> a). Display names keep their accents — this is applied to the match key."""
    return unicodedata.normalize("NFKD", unescape(s)).encode("ascii", "ignore").decode("ascii")

# generic corporate/category tokens that don't distinguish a brand — stripped from the brand MATCH key so
# "Tito's Handmade Vodka" == "Tito Handmade". Corporate suffixes + the spirit/beer category words (a category
# is not a brand). NB kept conservative: no varietals, so wine producers aren't over-merged.
_GENERIC = {"the", "co", "inc", "llc", "ltd", "company", "distillery", "distillers", "distilling", "brands",
            "brand", "winery", "wineries", "vineyards", "vineyard", "wine", "wines", "spirits", "imports",
            "importing", "cellars", "estate", "estates", "of",
            "vodka", "rum", "whiskey", "whisky", "gin", "tequila", "mezcal", "bourbon", "rye", "scotch",
            "brandy", "cognac", "liqueur", "liquor", "ale", "lager", "ipa", "stout", "pilsner", "beer",
            "cider", "seltzer", "champagne", "prosecco", "sparkling"}
# container/format/pack words — pure noise in a product name (safe to drop from display)
_FORMAT_NOISE = {"glass", "bottle", "bottles", "btl", "can", "cans", "pet", "plastic", "gift", "box", "boxed",
                 "boxset", "bag", "each", "ea", "nr", "pack", "packs", "aluminum"}


def _is_allcaps(s):
    return bool(s) and s.upper() == s and any(c.isalpha() for c in s)


def _titlecase(s):
    def fix(w):
        return w if (len(w) > 1 and w[1:].lower() != w[1:] and not _is_allcaps(w)) else w.capitalize()
    return " ".join(fix(w) for w in (s or "").split())


def nbrand(s):
    """Aggressive brand match-key: de-accent, lowercase, drop possessive + plural 's, punctuation, generic suffixes."""
    s = deaccent(s).lower()
    s = re.sub(r"'s\b", "", s)                      # possessive: tito's -> tito
    s = re.sub(r"[^a-z0-9]+", " ", s)
    toks = [t for t in s.split() if t not in _GENERIC and len(t) > 1]
    if toks and toks[-1].endswith("s") and len(toks[-1]) > 3:
        toks[-1] = toks[-1][:-1]                     # trailing plural s (daniels -> daniel)
    return " ".join(toks)


def build_brand_canon(rows, log=print):
    """nbrand-key -> canonical DISPLAY brand. Among the variants of a key, prefer a mixed-case string (not
    ALL-CAPS), then the most frequent, then the shortest — so 'Tito's Handmade' wins over 'TITO HANDMADE'."""
    cnt = collections.Counter(r.get("brand") for r in rows if r.get("brand"))
    groups = collections.defaultdict(list)
    for b in cnt:
        k = nbrand(b)
        if k:
            groups[k].append(b)
    canon = {}
    for k, variants in groups.items():
        best = sorted(variants, key=lambda b: (_is_allcaps(b), -cnt[b], len(b)))[0]
        canon[k] = _titlecase(best) if _is_allcaps(best) else best
    log("[precleanse] brand canon: %d brand strings -> %d canonical (-%d merged)"
        % (len(cnt), len(groups), len(cnt) - len(groups)))
    return canon


def clean_name(name, brand):
    """Title-case ALL-CAPS names, drop pure-dash + format/pack noise, tidy whitespace/#SKU refs. We deliberately
    DON'T strip the brand out of the name (it orphans tokens like 'Jack Daniel's' -> 'Daniel's'); brand
    alignment + the within-brand canonicalizer + Claude handle the residual name variance."""
    if not name:
        return name
    name = unescape(name)                               # D&#39;Aquino -> D'Aquino (fix the display too)
    s = _titlecase(name) if _is_allcaps(name) else name
    toks = [t for t in re.split(r"\s+", s.strip())
            if t and (set(t) - set("-–")) and t.lower() not in _FORMAT_NOISE]
    s = re.sub(r"#\d+\b\s*", "", " ".join(toks))
    s = re.sub(r"\s{2,}", " ", s).strip(" -–")
    return s or name


def precleanse(staged, log=print):
    """Canonicalize brand + cleanse name on every staged row, in place. Returns the rows."""
    canon = build_brand_canon(staged, log)
    b_changed = n_changed = 0
    for r in staged:
        b = r.get("brand")
        if b:
            cb = canon.get(nbrand(b))
            if cb and cb != b:
                r["brand"] = cb
                b_changed += 1
        nm = r.get("product_name")
        cn = clean_name(nm, r.get("brand"))
        if cn != nm:
            r["product_name"] = cn
            n_changed += 1
    log("[precleanse] canonicalized %d brand values, cleansed %d names" % (b_changed, n_changed))
    return staged
