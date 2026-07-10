"""cocktail_taxonomy.py — classify a menu cocktail into ROOT -> SUB-FAMILY -> COCKTAIL (+ base spirit).

Every mixed drink is a variation of a few ROOT ("mother") templates — Death & Co's Cocktail Codex:
Old Fashioned, Martini, Daiquiri, Sidecar, Highball, Flip (a ~4-family collapse: Spirit-forward, Sour,
Highball, Flip). Under each root sits a SUB-FAMILY (the canonical archetype: Margarita, Manhattan, Mule,
Cosmopolitan, ...), and the menu name is a specific COCKTAIL. This maps messy on-premise menu names to a
structured taxonomy so the NAOP pull can aggregate pours by family + base spirit.

    classify("Spicy Jalapeño Margarita", "tequila, lime, agave, jalapeño")
    -> {root:"Sour", sub:"Margarita", cocktail:"Spicy Jalapeño Margarita",
        base_spirit:"tequila", method:"deterministic"}

Deterministic-first (keyword match against the vetted sub-family table); anything unmatched is returned
method="needs_review" for an optional grounded LLM pass (self_heal-style) — never guessed silently.
"""
import re

# ROOT templates (the mother cocktails) — the 4-way collapse used as the top level.
ROOTS = {
    "Spirit-forward": "spirit + sugar/bitters or vermouth, stirred, boozy (Old Fashioned / Martini)",
    "Sour":           "spirit + citrus + sweet (+/- liqueur or egg) (Daiquiri / Sidecar)",
    "Highball":       "spirit + carbonation / bulk mixer, tall & refreshing",
    "Flip":           "spirit + sugar + whole egg or dairy, creamy/dessert",
}

# SUB-FAMILY (archetype) -> (root, default base spirit, match patterns). Order matters: more specific first.
# base spirit is a DEFAULT for the archetype; classify() overrides it when the name/desc names a spirit.
SUBFAMILIES = [
    ("Margarita",        "Sour",           "tequila",  r"margarita|marg\b"),
    ("Daiquiri",         "Sour",           "rum",      r"daiquiri|daquiri|dacquiri"),
    ("Piña Colada",      "Sour",           "rum",      r"pi[nñ]a colada|colada"),
    ("Cosmopolitan",     "Sour",           "vodka",    r"cosmopolitan|cosmo\b"),
    ("Whiskey Sour",     "Sour",           "whiskey",  r"whiskey sour|whisky sour|amaretto sour|vodka sour|gin sour|\bsour$"),
    ("Sidecar",          "Sour",           "brandy",   r"sidecar"),
    ("Gimlet",           "Sour",           "gin",      r"gimlet"),
    ("Lemon Drop",       "Sour",           "vodka",    r"lemon drop|kamikaze|woo ?woo"),
    ("Caipirinha",       "Sour",           "cachaca",  r"caipirinha|caipiroska"),
    ("Bramble",          "Sour",           "gin",      r"bramble"),
    ("Hurricane",        "Sour",           "rum",      r"hurricane"),
    ("Mai Tai",          "Sour",           "rum",      r"mai ?tai"),
    ("Manhattan",        "Spirit-forward", "whiskey",  r"manhattan|rob roy"),
    ("Negroni",          "Spirit-forward", "gin",      r"negroni|boulevardier"),
    ("Martini",          "Spirit-forward", "gin",      r"(?<!espresso )(?<!expresso )\bmartini\b|vesper|gibson"),
    ("Old Fashioned",    "Spirit-forward", "whiskey",  r"old ?fashioned|sazerac|rusty nail|godfather"),
    ("Moscow Mule",      "Highball",       "vodka",    r"mule\b|moscow mule|kentucky mule"),
    ("Paloma",           "Highball",       "tequila",  r"paloma|ranch water"),
    ("Cuba Libre",       "Highball",       "rum",      r"cuba libre|rum (?:and|&) coke|dark (?:and|&) stormy"),
    ("Gin & Tonic",      "Highball",       "gin",      r"gin (?:and|&) tonic|g ?& ?t\b|vodka tonic"),
    ("Collins",          "Highball",       "gin",      r"collins|tom collins"),
    ("Mojito",           "Highball",       "rum",      r"mojito"),
    ("Spritz",           "Highball",       "aperitif", r"spritz|aperol|hugo\b"),
    ("Sangria",          "Highball",       "wine",     r"sangria|sangr[ií]a"),
    ("Long Island",      "Highball",       "mixed",    r"long island|lit\b|adios|electric lemonade"),
    ("Screwdriver",      "Highball",       "vodka",    r"screwdriver|greyhound|sea breeze|bay breeze|madras"),
    ("Tequila Sunrise",  "Highball",       "tequila",  r"tequila sunrise|sunrise|tequila sunset"),
    ("Sex on the Beach", "Highball",       "vodka",    r"sex on the beach|fuzzy navel|hpnotiq"),
    ("Bloody Mary",      "Highball",       "vodka",    r"bloody mary|michelada|red snapper"),
    ("Mimosa",           "Highball",       "wine",     r"mimosa|bellini|poinsettia|kir royale"),
    ("Espresso Martini", "Flip",           "vodka",    r"espresso martini|espresso ?tini"),
    ("White Russian",    "Flip",           "vodka",    r"white russian|mudslide|brandy alexander|grasshopper"),
    ("Painkiller",       "Flip",           "rum",      r"painkiller|bushwacker|colada"),
]

# base-spirit overrides — if the name/desc names one of these, it wins over the archetype default.
_SPIRIT = [
    ("tequila", r"tequila|margarita|paloma"), ("mezcal", r"mezcal|mezcal"),
    ("rum", r"\brum\b|bacardi|captain morgan|malibu|daiquiri|mojito|colada"),
    ("vodka", r"vodka|tito'?s|absolut|smirnoff|grey goose|deep eddy|kettle one|ketel"),
    ("gin", r"\bgin\b|tanqueray|bombay|hendrick"),
    ("whiskey", r"whiskey|whisky|bourbon|\brye\b|scotch|jack daniel|jameson|crown royal|makers|maker'?s|woodford|jim beam"),
    ("brandy", r"brandy|cognac|hennessy|remy|pisco"),
    ("wine", r"prosecco|champagne|wine|sangria|aperol|campari|vermouth"),
]


def _spirit(text):
    for name, pat in _SPIRIT:
        if re.search(pat, text, re.I):
            return name
    return ""


def classify(name, description=""):
    """name + optional description -> {root, sub, cocktail, base_spirit, method}."""
    text = ("%s %s" % (name or "", description or "")).lower()
    for sub, root, base, pat in SUBFAMILIES:
        if re.search(pat, text, re.I):
            return {"root": root, "sub": sub, "cocktail": (name or "").strip(),
                    "base_spirit": _spirit(text) or base, "method": "deterministic"}
    sp = _spirit(text)
    return {"root": "", "sub": "", "cocktail": (name or "").strip(),
            "base_spirit": sp, "method": "needs_review"}          # -> optional grounded LLM pass


# Liqueurs / cordials — the sweet stuff that hides in DESSERTS and after-dinner items (Applebee's spiked
# shakes, tiramisu, bananas foster, Irish coffee, bread pudding w/ bourbon sauce, boozy floats). Scanning
# desserts too catches brand pours a cocktail-only sweep would miss.
_LIQUEURS = [
    ("Baileys", r"baileys|bailey'?s|irish cream"), ("Kahlúa", r"kahl[uú]a|coffee liqueur"),
    ("Amaretto", r"amaretto|disaronno"), ("RumChata", r"rum ?chata|horchata liqueur"),
    ("Grand Marnier", r"grand marnier"), ("Cointreau", r"cointreau"), ("Triple Sec", r"triple sec|curacao|curaçao"),
    ("Chambord", r"chambord"), ("Frangelico", r"frangelico"), ("Limoncello", r"limoncello"),
    ("Godiva", r"godiva"), ("St-Germain", r"st.?-?germain|elderflower liqueur"), ("Sambuca", r"sambuca"),
    ("Crème de", r"cr[eè]me de (?:menthe|cacao|cassis|banane)"), ("Marsala", r"marsala"),
    ("Sherry", r"\bsherry\b"), ("Port", r"\bport wine\b"), ("Aperol/Campari", r"aperol|campari"),
    ("Irish whiskey", r"irish coffee|jameson"),
]
# dessert / after-dinner alcohol signals even when no brand is named
_DESSERT_ALC = (r"spiked|boozy|drunken|adult (?:shake|milkshake|float)|hard (?:shake|float)|nightcap|"
                r"bourbon (?:sauce|glaze|caramel|pecan|bread pudding)|rum cake|bananas foster|"
                r"tiramisu|irish coffee|affogato|espresso martini|liqueur|with (?:kahl|bail|amaretto|rum|bourbon)")


def _liqueurs(text):
    return [nm for nm, pat in _LIQUEURS if re.search(pat, text, re.I)]


def detect_alcohol(name, description=""):
    """Does ANY menu item (dessert, coffee, entrée, drink) contain alcohol — and which brands/spirits?
    Runs the whole menu, not just the cocktail list. -> {is_alcoholic, form, spirits[], liqueurs[], cocktail}."""
    text = ("%s %s" % (name or "", description or "")).lower()
    c = classify(name, description)
    is_cocktail = c["method"] == "deterministic"
    spirits = [s for s, pat in _SPIRIT if re.search(pat, text, re.I)]
    liqs = _liqueurs(text)
    dessert_hit = bool(re.search(_DESSERT_ALC, text, re.I))
    is_alc = is_cocktail or bool(spirits) or bool(liqs) or dessert_hit
    if is_cocktail:
        form = "cocktail"
    elif re.search(r"shake|float|malt|sundae", text):
        form = "spiked shake/float"
    elif re.search(r"coffee|latte|cappuccino|affogato|espresso", text):
        form = "after-dinner coffee"
    elif re.search(r"cake|tiramisu|pudding|foster|pie|brownie|cheesecake|gelato|dessert|cobbler", text):
        form = "boozy dessert"
    else:
        form = "drink" if is_alc else ""
    if not is_alc:
        form = ""                                              # non-alcoholic items carry no form
    return {"is_alcoholic": is_alc, "form": form, "spirits": spirits, "liqueurs": liqs,
            "cocktail": c if is_cocktail else None}


# ── Mocktails / zero-proof — a fast-growing category worth capturing on its own (a Virgin Margarita is
# still a Margarita, just non-alcoholic). And BEER — brand + style + format (draft/bottle/can).
_MOCKTAIL = re.compile(r"mocktail|virgin|zero[- ]?proof|non[- ]?alcoholic|spirit[- ]?free|alcohol[- ]?free|"
                       r"seedlip|\b0\.0\b|no[- ]?alcohol|nolo\b|shirley temple|roy rogers", re.I)
BEER_STYLES = [
    ("IPA", r"\bipa\b|india pale ale|hazy|dipa|new england ale|neipa"),
    ("Pale Ale", r"pale ale|\bapa\b"), ("Pilsner", r"pilsner|pilsener|\bpils\b"),
    ("Lager", r"lager|helles|vienna lager|dortmunder|light beer|\blite\b"),
    ("Stout", r"stout|imperial stout"), ("Porter", r"porter"),
    ("Wheat", r"wheat|hefeweizen|\bhefe\b|witbier|white ale|blanche"),
    ("Amber/Red", r"amber|red ale|m[aä]rzen|oktoberfest"), ("Brown Ale", r"brown ale"),
    ("Sour", r"sour ale|\bgose\b|berliner|lambic|kettle sour"), ("Saison", r"saison|farmhouse ale"),
    ("Kölsch", r"k[oö]lsch"), ("Bock", r"\bbock\b|doppelbock|maibock"),
    ("Blonde/Golden", r"blonde ale|golden ale|cream ale"),
    ("Cider", r"\bcider\b|angry orchard|strongbow|stella cidre"),
    ("Hard Seltzer", r"hard seltzer|white claw|\btruly\b|high noon|nutrl|vizzy|bon (?:&|and) viv"),
    ("FMB/Hard RTD", r"twisted tea|mike'?s hard|smirnoff ice|four ?loko|hard (?:tea|lemonade|punch)|surfside|cutwater|ranch water"),
]
_BEER_HINT = re.compile(
    r"\bbeer\b|\bipa\b|\bale\b|lager|pilsner|stout|porter|hefe|saison|k[oö]lsch|draft|draught|on tap|\bpint\b|"
    r"bud ?light|budweiser|coors|miller|michelob|modelo|corona|heineken|stella|guinness|blue moon|dos equis|"
    r"pacifico|yuengling|sam adams|sierra nevada|lagunitas|goose island|\bpbr\b|pabst|shiner|founders|"
    r"new belgium|sculpin|voodoo ranger|hazy|montucky|natural light|busch\b|"
    r"hard seltzer|white claw|\btruly\b|high noon|nutrl|vizzy|\bcider\b|angry orchard|strongbow|"
    r"twisted tea|mike'?s hard|smirnoff ice|four ?loko|hard (?:tea|lemonade|cider|punch)|surfside|cutwater", re.I)


def beer_style(text):
    for s, pat in BEER_STYLES:
        if re.search(pat, text, re.I):
            return s
    return ""


def classify_beverage(name, description=""):
    """Unified NAOP drinks classifier → {category, is_alcoholic, name, + type fields}.
    category ∈ cocktail | mocktail | beer | dessert (boozy) | other. Cocktails carry root/sub/base_spirit,
    beer carries style/format. A mocktail signal on a cocktail flips it non-alcoholic but keeps the archetype."""
    text = ("%s %s" % (name or "", description or "")).lower()
    mock = bool(_MOCKTAIL.search(text))
    c = classify(name, description)
    is_cocktail = c["method"] == "deterministic"
    if is_cocktail:
        return {"category": "mocktail" if mock else "cocktail", "is_alcoholic": not mock,
                "name": (name or "").strip(), "root": c["root"], "sub": c["sub"],
                "base_spirit": "" if mock else c["base_spirit"]}
    soda = re.search(r"root beer|\bginger beer\b|cream soda|birch beer|ginger ale", text)
    if _BEER_HINT.search(text) and not soda:                   # root/ginger beer are non-alc sodas
        fmt = ("draft" if re.search(r"draft|draught|on tap|\bpint\b", text) else
               ("can" if "can" in text else ("bottle" if "bottle" in text else "")))
        return {"category": "beer", "is_alcoholic": not mock, "name": (name or "").strip(),
                "beer_style": beer_style(text), "format": fmt}
    if mock:            # a named mocktail that isn't a recognized cocktail shape
        return {"category": "mocktail", "is_alcoholic": False, "name": (name or "").strip(),
                "root": "", "sub": "", "base_spirit": ""}
    a = detect_alcohol(name, description)
    if a["is_alcoholic"]:
        return {"category": "dessert" if "dessert" in a["form"] or "shake" in a["form"] else "drink",
                "is_alcoholic": True, "name": (name or "").strip(), "form": a["form"],
                "spirits": a["spirits"], "liqueurs": a["liqueurs"]}
    return {"category": "other", "is_alcoholic": False, "name": (name or "").strip()}


if __name__ == "__main__":
    for n, d in [("Spicy Jalapeño Margarita", "tequila, lime, agave, jalapeño"),
                 ("Kentucky Mule", "bourbon, ginger beer, lime"),
                 ("Smoked Maple Manhattan", "rye, sweet vermouth, bitters"),
                 ("Strawberry Daiquiri", ""), ("Aperol Spritz", ""), ("Espresso Martini", "vodka, coffee liqueur, espresso"),
                 ("Death Flip", "mezcal, yellow chartreuse, jägermeister, egg")]:
        print("%-26s -> %s" % (n, classify(n, d)))
