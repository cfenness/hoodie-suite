"""class_type.py — the TTB<->retail matching bridge.

TTB/registry and retail speak different languages: TTB keeps the regulatory class/type in a CATEGORY field
("STRAIGHT BOURBON WHISKY") and leaves the name as just the brand ("Buffalo Trace"); retail bakes it into the
name string ("Buffalo Trace Kentucky Straight Bourbon Whiskey, 750ml"). Same product, different keys -> the
matcher misses. This normalizes BOTH sides:

  classify(name, category) -> (class_type, class_code, core)
    class_type : canonical type  ("bourbon", "cabernet-sauvignon", "ipa")   — the shared vocabulary
    class_code : 3-char code for the mnemonic (BRB, CAB, IPA)               — so type is IN the match key
    core       : the name with the class/type vocabulary + generic qualifiers STRIPPED — the brand+edition
                 that actually aligns across TTB and retail.

Match key becomes brand + class_code + core, so "Buffalo Trace" (TTB, category=bourbon) and "Buffalo Trace
Kentucky Straight Bourbon Whiskey" (retail) resolve to the SAME thing, while "Old Overholt Bourbon" and
"Old Overholt Rye" stay distinct (same brand+core, different class_code).
"""
import re

# ordered specific -> generic. (regex, canonical class_type, 3-char code). First match wins.
_TYPES = [
    # ── spirits ──
    (r"straight bourbon|\bbourbon\b", "bourbon", "BRB"),
    (r"\brye\b", "rye", "RYE"),
    (r"tennessee whisk", "tennessee-whiskey", "TNW"),
    (r"single malt|\bscotch\b", "scotch", "SCO"),
    (r"irish whisk", "irish-whiskey", "IRW"),
    (r"canadian whisk", "canadian-whisky", "CDW"),
    (r"\bwhisk(e)?y\b", "whiskey", "WHK"),
    (r"\bvodka\b", "vodka", "VOD"),
    (r"\bgin\b", "gin", "GIN"),
    (r"\btequila\b|\bblanco\b|reposado|\banejo\b|añejo", "tequila", "TEQ"),
    (r"\bmezcal\b|\bmescal\b", "mezcal", "MZC"),
    (r"\brum\b|\brhum\b|\bron\b", "rum", "RUM"),
    (r"\bcognac\b", "cognac", "COG"),
    (r"\bbrandy\b|\barmagnac\b", "brandy", "BRA"),
    (r"liqueur|cordial|schnapps|\bcreme\b|\bcream\b", "liqueur", "LIQ"),
    (r"\bamaro\b|aperitif|\bvermouth\b|\bbitters\b", "aperitif", "APR"),
    # ── beer / RTD ──
    (r"\bipa\b|india pale", "ipa", "IPA"),
    (r"\bpilsner\b|\bpils\b", "pilsner", "PIL"),
    (r"\blager\b", "lager", "LAG"),
    (r"\bstout\b|imperial stout", "stout", "STO"),
    (r"\bporter\b", "porter", "POR"),
    (r"hard seltzer|\bseltzer\b", "seltzer", "SLZ"),
    (r"\bcider\b", "cider", "CID"),
    (r"canned cocktail|ready.to.drink|\brtd\b", "rtd", "RTD"),
    (r"\bale\b", "ale", "ALE"),
    (r"\bbeer\b|\bmalt\b", "beer", "BER"),
    # white dog / moonshine = unaged whiskey — must beat the wine "white" rule below
    (r"white dog|\bmoonshine\b|unaged|new make", "whiskey", "WHK"),
    # ── wine (varietal = identity; keep as class) ──
    (r"cabernet sauvignon|\bcab sauv\b", "cabernet-sauvignon", "CAB"),
    (r"pinot noir", "pinot-noir", "PNR"),
    (r"pinot grigio|pinot gris", "pinot-grigio", "PNG"),
    (r"sauvignon blanc", "sauvignon-blanc", "SAV"),
    (r"\bchardonnay\b", "chardonnay", "CHR"),
    (r"\bmerlot\b", "merlot", "MER"),
    (r"\briesling\b", "riesling", "RIE"),
    (r"\bmalbec\b", "malbec", "MLB"),
    (r"\bzinfandel\b|\bzin\b", "zinfandel", "ZIN"),
    (r"\bsyrah\b|\bshiraz\b", "syrah", "SYR"),
    (r"\bmoscato\b|\bmuscat\b", "moscato", "MOS"),
    (r"\bprosecco\b", "prosecco", "PRO"),
    (r"champagne|sparkling", "sparkling", "SPK"),
    (r"\bros[eé]\b", "rose", "ROS"),
    (r"\bport\b|\bsherry\b|\bmadeira\b|fortified", "fortified", "FOR"),
    # generic wine color requires wine/blend/table context — bare "red"/"white" hit spirit editions
    # ("Red Label", "White Dog") so they are deliberately NOT matched here.
    (r"red (table )?wine|red blend", "red-wine", "RED"),
    (r"white (table )?wine|white blend", "white-wine", "WHT"),
    (r"\bwine\b", "wine", "WIN"),
    # ── hemp / cannabis ──
    (r"\bhemp\b|\bcbd\b|delta[\s-]?[89]|\bthc\b", "hemp", "HMP"),
]
_TYPES = [(re.compile(p, re.I), t, c) for p, t, c in _TYPES]

# generic qualifiers that are NOISE for matching (in retail names, absent from TTB) — stripped from the core.
# Editions that DISTINGUISH products (reserve, single barrel, small batch…) are deliberately NOT here.
_STRIP = re.compile(
    r"\b(kentucky|straight|tennessee|canadian|irish|scotch|blended|blend|table|fine|premium|"
    r"whisky|whiskey|bourbon|rye|vodka|gin|tequila|mezcal|rum|rhum|ron|cognac|brandy|liqueur|cordial|"
    r"spirit|spirits|liquor|beer|ale|lager|ipa|pilsner|pils|stout|porter|seltzer|cider|"
    r"wine|red|white|ros[eé]|sparkling|champagne|prosecco|moscato|cabernet|sauvignon|blanc|"
    r"pinot|noir|grigio|gris|chardonnay|merlot|riesling|malbec|zinfandel|zin|syrah|shiraz|"
    r"distillery|winery|vineyards?|cellars?|estate|company|\bco\b|\binc\b|\bllc\b|imported)\b", re.I)
_WS = re.compile(r"\s{2,}")
# vintage years + size/pack tokens: retail bakes these into the name, TTB doesn't — noise for the product core.
_NOISE = re.compile(r"\b(?:19|20)\d{2}\b|\b\d[\d.]*\s?(?:ml|cl|l|liter|litre|oz|pk|pack|ct|count)\b|"
                    r"\b\d+\s?x\s?\d+\b|\b\d{2,3}\s?proof\b", re.I)


def classify(name, category=""):
    """Return (class_type, class_code, core). Reads class from category/varietal first (TTB's home for it),
    then the name (retail's). Core = name with class/type + generic qualifiers + vintage/size stripped -> the
    brand+edition that aligns across TTB and retail."""
    hay = ((category or "") + " " + (name or "")).strip()
    ct, cc = "", ""
    for rx, t, c in _TYPES:
        if rx.search(hay):
            ct, cc = t, c
            break
    core = _NOISE.sub(" ", name or "")
    core = _STRIP.sub(" ", core)
    core = _WS.sub(" ", re.sub(r"[^A-Za-z0-9'&.\- ]", " ", core)).strip(" -&.,")
    return ct, cc, core


if __name__ == "__main__":
    for nm, cat in [("Buffalo Trace Kentucky Straight Bourbon Whiskey, 750ml", ""),
                    ("Buffalo Trace", "STRAIGHT BOURBON WHISKY"),
                    ("Old Overholt Straight Rye Whiskey", ""), ("Old Overholt Bourbon", ""),
                    ("Oyster Bay Sauvignon Blanc 2023", ""), ("Oyster Bay", "TABLE WINE WHITE"),
                    ("Sierra Nevada Torpedo Extra IPA", "IPA")]:
        ct, cc, core = classify(nm, cat)
        print("%-52s -> class=%-18s code=%s core=%r" % (nm[:52], ct, cc, core))
