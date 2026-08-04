#!/usr/bin/env python3
"""product_taxonomy.py — the canonical Type -> Class -> Sub Class -> Varietal hierarchy.

WHY THIS IS DATA AND NOT A DROPDOWN IN A PAGE
  The four levels are a HIERARCHY, so a Class is only meaningful under its Type and a Sub Class only
  under its Class. Held as four independent free-text fields they drift immediately — "Bourbon" turns
  up as a Type on one row and a Sub Class on the next, and nothing in the data says which is right.
  Held as a tree, the level a term belongs to is a fact about the term.

WHERE THE LEVELS COME FROM, AND WHERE THEY DO NOT
  Spirits classes track 27 CFR 5.22's classes (whisky, gin, brandy, rum, agave spirits, cordials and
  liqueurs); wine tracks 27 CFR 4.21 (still / sparkling / carbonated / fortified / dessert). Those two
  are REGULATORY and stable. Beer styles are not: TTB regulates malt beverage labelling but does not
  define "IPA" or "Hazy Pale Ale", so the beer branch is TRADE CONVENTION, and RTD/seltzer more so.
  `basis` records which is which per Type, because a taxonomy that presents a trade habit and a
  federal class as the same kind of fact invites someone to argue with the wrong one.

VARIETAL IS NOT UNIVERSAL
  A varietal is a grape. It is meaningful under wine and (loosely) cider, and meaningless under
  spirits — "Reposado" is an age statement, not a varietal. Levels that do not apply return an EMPTY
  list and the surface says "not applicable", which is a different statement from "we have no values
  for this yet". Confusing those two is how a taxonomy acquires a Varietal column full of ageing
  terms.

THE TREE GROWS FROM USE
  The seed cannot be complete — 2,000 suppliers ship terms nobody enumerated. So a labeller can type
  a value at any level, and `learn()` records the WHOLE PATH (type/class/subclass/varietal) into
  `xsource_taxonomy`, not just the leaf. Recording the leaf alone was the obvious shortcut and it is
  useless: a Sub Class with no parent cannot be filtered under anything, so it would come back on
  every Class forever. Learned nodes are merged into the served tree and marked `learned` so the
  seed stays distinguishable from what a human added.
"""
import json
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

TABLE = "xsource_taxonomy"
FIELDS = ["path_key", "canon_type", "canon_class", "canon_subclass", "canon_varietal",
          "times", "first_seen", "last_seen"]

# ── the seed ──────────────────────────────────────────────────────────────────────────────────────
# type -> class -> sub class -> [varietals]. A leaf list is EMPTY where the level does not apply.
SEED = {
    "Spirits": {
        "Whiskey": {
            "Bourbon": [], "Straight Bourbon": [], "Rye Whiskey": [], "Tennessee Whiskey": [],
            "Single Malt Scotch": [], "Blended Scotch": [], "Irish Whiskey": [],
            "Canadian Whisky": [], "Japanese Whisky": [], "American Single Malt": [],
            "Corn Whiskey": [], "Wheat Whiskey": [], "Blended Whiskey": [],
            "Flavored Whiskey": [], "White Whiskey": [],
        },
        "Vodka": {"Unflavored Vodka": [], "Flavored Vodka": []},
        "Gin": {"London Dry Gin": [], "Old Tom Gin": [], "Genever": [],
                "Contemporary Gin": [], "Sloe Gin": []},
        "Rum": {"White Rum": [], "Gold Rum": [], "Dark Rum": [], "Aged Rum": [],
                "Spiced Rum": [], "Flavored Rum": [], "Rhum Agricole": [], "Cachaça": [],
                "Overproof Rum": []},
        "Agave Spirits": {"Blanco Tequila": [], "Reposado Tequila": [], "Añejo Tequila": [],
                          "Extra Añejo Tequila": [], "Cristalino Tequila": [],
                          "Flavored Tequila": [], "Mezcal": [], "Raicilla": [], "Sotol": [],
                          "Bacanora": []},
        "Brandy": {"Cognac": [], "Armagnac": [], "American Brandy": [], "Pisco": [],
                   "Grappa": [], "Calvados": [], "Applejack": [], "Fruit Brandy": []},
        "Liqueurs & Cordials": {"Cream Liqueur": [], "Coffee Liqueur": [], "Fruit Liqueur": [],
                                "Herbal Liqueur": [], "Nut Liqueur": [], "Schnapps": [],
                                "Orange Liqueur": [], "Amaretto": [], "Anise Liqueur": []},
        "Aperitif & Bitters": {"Amaro": [], "Vermouth": [], "Aperitivo": [],
                               "Cocktail Bitters": [], "Absinthe": []},
        "Other Spirits": {"Baijiu": [], "Soju": [], "Shochu": [], "Aquavit": [],
                          "Ouzo": [], "Arak": [], "Neutral Grain Spirit": [], "Moonshine": []},
    },
    "Wine": {
        "Still Wine": {
            "Red Wine": ["Cabernet Sauvignon", "Merlot", "Pinot Noir", "Syrah / Shiraz",
                         "Zinfandel", "Malbec", "Sangiovese", "Tempranillo", "Grenache",
                         "Nebbiolo", "Petite Sirah", "Cabernet Franc", "Barbera", "Petit Verdot",
                         "Carménère", "Red Blend"],
            "White Wine": ["Chardonnay", "Sauvignon Blanc", "Pinot Grigio / Pinot Gris",
                           "Riesling", "Moscato / Muscat", "Gewürztraminer", "Chenin Blanc",
                           "Viognier", "Albariño", "Grüner Veltliner", "Sémillon", "Verdejo",
                           "Vermentino", "White Blend"],
            "Rosé Wine": ["Grenache", "Pinot Noir", "Syrah", "Sangiovese", "Tempranillo",
                          "Rosé Blend"],
            "Orange Wine": [],
        },
        "Sparkling Wine": {
            "Champagne": ["Chardonnay", "Pinot Noir", "Pinot Meunier", "Champagne Blend"],
            "Prosecco": ["Glera"],
            "Cava": ["Macabeo", "Xarel·lo", "Parellada"],
            "Crémant": [], "Sekt": [],
            "Sparkling Rosé": [], "Sparkling Wine (Other)": [], "Moscato d'Asti": ["Muscat"],
            "Lambrusco": ["Lambrusco"],
        },
        "Fortified Wine": {"Port": [], "Sherry": [], "Madeira": [], "Marsala": [],
                           "Vermouth": [], "Fortified Wine (Other)": []},
        "Dessert Wine": {"Late Harvest": [], "Ice Wine": [], "Sauternes": [],
                         "Dessert Wine (Other)": []},
        "Flavored & Other Wine": {"Sangria": [], "Wine Cocktail": [], "Fruit Wine": [],
                                  "Mead": []},
    },
    "Beer": {
        "Ale": {"IPA": [], "Hazy / New England IPA": [], "Double / Imperial IPA": [],
                "Pale Ale": [], "Amber / Red Ale": [], "Brown Ale": [], "Stout": [],
                "Imperial Stout": [], "Porter": [], "Wheat Beer": [], "Belgian Ale": [],
                "Saison": [], "Sour & Wild Ale": [], "Barleywine": []},
        "Lager": {"Pilsner": [], "Helles": [], "Märzen / Oktoberfest": [], "Bock": [],
                  "Doppelbock": [], "Dunkel": [], "Vienna Lager": [], "American Lager": [],
                  "Light Lager": [], "Mexican Lager": []},
        "Hybrid & Specialty": {"Kölsch": [], "Altbier": [], "Cream Ale": [],
                               "California Common": [], "Smoked Beer": [], "Fruit Beer": []},
        "Malt Beverage": {"Flavored Malt Beverage": [], "Malt Liquor": []},
    },
    "RTD & Seltzer": {
        "Hard Seltzer": {"Flavored Hard Seltzer": [], "Spirits-Based Seltzer": []},
        "Canned Cocktail": {"Spirits-Based Cocktail": [], "Malt-Based Cocktail": [],
                            "Wine-Based Cocktail": []},
        "Hard Tea & Lemonade": {"Hard Tea": [], "Hard Lemonade": [], "Hard Kombucha": []},
        "Hard Soda": {"Hard Root Beer": [], "Hard Cola": [], "Hard Soda (Other)": []},
    },
    "Cider & Perry": {
        "Hard Cider": {"Dry Cider": [], "Semi-Dry Cider": [], "Sweet Cider": [],
                       "Flavored Cider": [], "Heritage Cider": []},
        "Perry": {"Pear Cider": []},
    },
    "Sake & Rice Wine": {
        "Sake": {"Junmai": [], "Ginjo": [], "Daiginjo": [], "Honjozo": [], "Nigori": [],
                 "Futsushu": [], "Sparkling Sake": []},
        "Rice Wine": {"Makgeolli": [], "Huangjiu": []},
    },
    "Non-Alcoholic": {
        "Non-Alcoholic Beer": {"NA Lager": [], "NA IPA": [], "NA Stout": []},
        "Non-Alcoholic Wine": {"NA Red": [], "NA White": [], "NA Sparkling": []},
        "Non-Alcoholic Spirit": {"NA Gin Alternative": [], "NA Aperitif": [],
                                 "NA Whiskey Alternative": []},
        "Mixer & Mocktail": {"Tonic": [], "Soda & Mixer": [], "Syrup": [], "Juice": [],
                             "Bitters (Non-Alcoholic)": []},
    },
    "Hemp & Cannabis": {
        "Hemp Beverage": {"Delta-9 THC Beverage": [], "Delta-8 THC Beverage": [],
                          "CBD Beverage": []},
        "Cannabis": {"Flower": [], "Edible": [], "Concentrate": [], "Pre-Roll": [],
                     "Vape": [], "Tincture": [], "Topical": []},
    },
    "Non-Beverage": {
        "Accessories": {"Glassware": [], "Barware": [], "Gift Set": [], "Merchandise": []},
        "Food & Snack": {"Snack": [], "Mixer & Garnish": []},
    },
}

# Which levels are federally defined vs. trade convention. Presenting both as one kind of fact is
# how a style argument turns into an argument about the regulation.
BASIS = {
    "Spirits": "27 CFR 5.22 classes; sub classes are trade-standard designations",
    "Wine": "27 CFR 4.21 classes; varietals are the grape names of 27 CFR 4.23",
    "Beer": "trade convention — TTB regulates malt beverage labelling but defines no styles",
    "RTD & Seltzer": "trade convention",
    "Cider & Perry": "trade convention; cider ≥7% ABV is regulated as wine",
    "Sake & Rice Wine": "trade convention",
    "Non-Alcoholic": "trade convention",
    "Hemp & Cannabis": "state-regulated; no federal beverage class",
    "Non-Beverage": "not a beverage class — retail catalogs carry these rows and they need a home",
}

# Varietal applies where a grape (or fruit) is the identity. Everywhere else the level is EMPTY on
# purpose, and the surface must say "not applicable" rather than showing a blank list.
VARIETAL_TYPES = {"Wine", "Cider & Perry"}


def _norm(s):
    return " ".join(str(s or "").strip().split()).lower()


def _path_key(t, c, s, v):
    return "|".join(_norm(x) for x in (t, c, s, v))


def learned(log=print):
    """Paths a labeller has taught, as (type, class, subclass, varietal) tuples."""
    try:
        import warehouse
        rows = warehouse.query(TABLE, "SELECT * FROM t")
    except Exception as e:
        log("taxonomy: no learned paths (%s)" % str(e).split("\n")[0][:70])
        return []
    out = []
    for r in rows:
        d = dict(r)
        out.append((d.get("canon_type") or "", d.get("canon_class") or "",
                    d.get("canon_subclass") or "", d.get("canon_varietal") or ""))
    return out


def tree(include_learned=True, log=print):
    """The served hierarchy: the seed, plus every learned path merged in at its own level.

    A learned node carries its PARENTS, so it filters like any seed node. Returned as nested dicts
    of {value: {...}} rather than lists so a client can walk it without knowing the depth."""
    t = {ty: {cl: {sc: list(vs) for sc, vs in cls.items()} for cl, cls in classes.items()}
         for ty, classes in SEED.items()}
    added = 0
    if include_learned:
        for ty, cl, sc, va in learned(log=log):
            if not ty:
                continue                      # a node with no Type cannot be placed — skip, loudly
            branch = t.setdefault(ty, {})
            if not cl:
                continue
            cls = branch.setdefault(cl, {})
            if not sc:
                continue
            vs = cls.setdefault(sc, [])
            if va and va not in vs:
                vs.append(va)
            added += 1
    return {"tree": t, "basis": BASIS, "varietal_types": sorted(VARIETAL_TYPES),
            "learned_paths": added}


def learn(canon, log=print):
    """Record the WHOLE path a resolution used, so a typed term is filterable next time.

    Landing only the leaf was the shortcut, and it does not work: a Sub Class with no parent cannot
    be filtered under any Class, so it would surface under every Class forever. A path with no Type
    is dropped for the same reason — there is nothing to hang it from."""
    ty = (canon.get("canon_type") or "").strip()
    cl = (canon.get("canon_class") or "").strip()
    sc = (canon.get("canon_subclass") or "").strip()
    va = (canon.get("canon_varietal") or "").strip()
    if not ty or not (cl or sc or va):
        return 0                              # a Type alone teaches no hierarchy
    now = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    row = {"path_key": _path_key(ty, cl, sc, va), "canon_type": ty, "canon_class": cl,
           "canon_subclass": sc, "canon_varietal": va, "times": 1,
           "first_seen": now, "last_seen": now}
    try:
        import warehouse
        warehouse.write_accumulate(TABLE, [row], key="path_key", fields=FIELDS, coverage=False)
        return 1
    except Exception as e:
        log("taxonomy: learn skipped: %s" % str(e)[:90])
        return 0


def main(argv=None):
    a = (argv or sys.argv[1:])
    if a and a[0] == "learned":
        print(json.dumps(learned(), indent=2))
    else:
        t = tree()
        print("%d types, %d classes, %d sub classes, %d learned paths" % (
            len(t["tree"]), sum(len(c) for c in t["tree"].values()),
            sum(len(s) for c in t["tree"].values() for s in c.values()), t["learned_paths"]))
        print(json.dumps(t["tree"], indent=2, ensure_ascii=False)[:2000])


if __name__ == "__main__":
    main()
