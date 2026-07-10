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
    ("Whiskey Sour",     "Sour",           "whiskey",  r"whiskey sour|whisky sour|amaretto sour|sour\b"),
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


if __name__ == "__main__":
    for n, d in [("Spicy Jalapeño Margarita", "tequila, lime, agave, jalapeño"),
                 ("Kentucky Mule", "bourbon, ginger beer, lime"),
                 ("Smoked Maple Manhattan", "rye, sweet vermouth, bitters"),
                 ("Strawberry Daiquiri", ""), ("Aperol Spritz", ""), ("Espresso Martini", "vodka, coffee liqueur, espresso"),
                 ("Death Flip", "mezcal, yellow chartreuse, jägermeister, egg")]:
        print("%-26s -> %s" % (n, classify(n, d)))
