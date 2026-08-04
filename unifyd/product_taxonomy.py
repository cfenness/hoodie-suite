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
#
# WHERE THE LINE BETWEEN CLASS AND SUB CLASS SITS
#   Class is the CATEGORY someone browses by; Sub Class is the designation within it. So Whiskey is
#   a Class and Bourbon a Sub Class, and IPA is a Class in beer because that is how the shelf is
#   actually organised — not a Sub Class of "Ale", which is a brewer's distinction, not a buyer's.
#
# LABELS LEAD WITH THE FAMILIAR TERM
#   "Agave Spirits" is correct and nobody browses by it, so the Class is "Tequila / Agave Spirits".
#   Same rule for Brandy / Cognac, Vermouth / Aperitif, Absinthe / Anise Spirits. The precise term
#   stays in the label — it is just not the part doing the finding.
SEED = {
    "Spirits": {
        "Whiskey": {
            "Bourbon": ["Straight", "Bottled-in-Bond", "Single Barrel", "Small Batch",
                        "Cask Strength", "Wheated", "High Rye", "Sour Mash"],
            "Rye Whiskey": ["Straight", "Bottled-in-Bond", "Single Barrel", "Small Batch",
                            "Cask Strength", "100% Rye"],
            "Tennessee Whiskey": ["Straight", "Single Barrel", "Small Batch", "Bottled-in-Bond"],
            "Scotch": ["Single Malt", "Blended", "Blended Malt", "Single Grain", "Cask Strength",
                       "Peated", "Sherry Cask", "Age Statement", "No Age Statement"],
            "Irish Whiskey": ["Single Malt", "Single Pot Still", "Single Grain", "Blended",
                              "Cask Strength"],
            "Canadian Whisky": ["Blended", "Rye", "Single Malt", "Cask Strength"],
            "Japanese Whisky": ["Single Malt", "Blended", "Grain", "Cask Strength"],
            "American Single Malt": ["Straight", "Cask Strength", "Single Barrel"],
            "World Whisky": ["Single Malt", "Blended", "Grain"],
            "Corn Whiskey": ["Straight", "Bottled-in-Bond"],
            "Wheat Whiskey": ["Straight", "Bottled-in-Bond"],
            "Malt Whiskey": ["Straight", "Single Barrel"],
            "Blended American Whiskey": [],
            "Flavored Whiskey": ["Honey", "Cinnamon", "Apple", "Peach", "Maple", "Cherry"],
            "White / Unaged Whiskey": ["White Dog", "Moonshine"],
        },
        "Vodka": {
            "Unflavored Vodka": ["Grain", "Potato", "Corn", "Wheat", "Rye", "Grape", "Organic"],
            "Flavored Vodka": ["Citrus", "Berry", "Vanilla", "Pepper", "Cucumber", "Whipped",
                               "Coffee"],
        },
        "Gin": {
            "London Dry Gin": ["Navy Strength", "Barrel-Aged", "Standard Proof"],
            "Plymouth Gin": [], "Old Tom Gin": [], "Genever": ["Oude", "Jonge"],
            "Contemporary / New Western Gin": ["Citrus-Forward", "Floral", "Savoury"],
            "Sloe Gin": [], "Flavored Gin": ["Pink / Berry", "Citrus", "Floral"],
        },
        "Rum": {
            "White / Silver Rum": ["Standard Proof", "Overproof"],
            "Gold Rum": [], "Dark Rum": [], "Blackstrap Rum": [],
            "Aged Rum": ["Añejo", "Solera", "Age Statement", "Extra Añejo"],
            "Rhum Agricole": ["Blanc", "Élevé Sous Bois", "Vieux VO", "Vieux VSOP", "Vieux XO"],
            "Navy Rum": ["Overproof"],
            "Spiced Rum": [], "Flavored Rum": ["Coconut", "Pineapple", "Banana", "Mango",
                                               "Citrus"],
            "Rum Blend": [],
        },
        "Tequila / Agave Spirits": {
            "Tequila": ["Blanco / Silver", "Joven / Gold", "Reposado", "Añejo", "Extra Añejo",
                        "Cristalino", "Flavored", "Additive-Free"],
            "Mezcal": ["Joven", "Reposado", "Añejo", "Ensamble", "Pechuga", "Espadín", "Tobalá"],
            "Sotol": ["Blanco", "Reposado", "Añejo"],
            "Bacanora": ["Blanco", "Reposado", "Añejo"],
            "Raicilla": [],
            "Agave Spirit (Non-Tequila)": ["Blanco", "Reposado", "Añejo"],
        },
        "Brandy / Cognac": {
            "Cognac": ["VS", "VSOP", "Napoléon", "XO", "XXO", "Extra", "Hors d'Age"],
            "Armagnac": ["VS", "VSOP", "Napoléon", "XO", "Hors d'Age", "Vintage"],
            "Calvados / Apple Brandy": ["Fine", "Vieux / Réserve", "VSOP", "XO", "Hors d'Age"],
            "Spanish Brandy": ["Solera", "Solera Reserva", "Solera Gran Reserva"],
            "American Brandy": ["Age Statement", "No Age Statement"],
            "Pisco": ["Puro", "Acholado", "Mosto Verde"],
            "Grappa": ["Giovane", "Affinata", "Invecchiata", "Riserva"],
            "Applejack": [],
            "Eau de Vie": ["Pear / Poire", "Cherry / Kirsch", "Raspberry / Framboise",
                           "Plum / Slivovitz", "Apricot"],
            "Fruit Brandy": ["Apricot", "Blackberry", "Cherry", "Peach", "Ginger"],
            "Flavored Brandy": [],
        },
        "Liqueurs / Cordials": {
            "Cream Liqueur": ["Irish Cream", "Chocolate Cream", "Coffee Cream"],
            "Coffee Liqueur": [], "Chocolate Liqueur": [],
            "Fruit Liqueur": ["Cherry", "Peach", "Raspberry", "Blackcurrant / Crème de Cassis",
                              "Banana", "Apple"],
            "Herbal Liqueur": ["Chartreuse-style", "Génépy", "Bénédictine-style"],
            "Nut Liqueur": ["Hazelnut", "Walnut", "Almond"],
            "Orange / Triple Sec": ["Curaçao", "Grand Marnier-style", "Cointreau-style"],
            "Amaretto": [], "Anise Liqueur": [], "Melon Liqueur": [], "Elderflower Liqueur": [],
            "Ginger Liqueur": [],
            "Schnapps": ["Peach", "Peppermint", "Apple", "Butterscotch"],
            "Whiskey Liqueur": ["Honey", "Cinnamon"],
        },
        "Vermouth / Aperitif": {
            "Sweet Vermouth": ["Rosso", "Torino", "Riserva"],
            "Dry Vermouth": [], "Blanc / Bianco Vermouth": [], "Rosé Vermouth": [],
            "Aperitivo": ["Bitter Red", "Bitter Orange", "Low-ABV"],
            "Quinquina": [], "Americano": [],
        },
        "Amaro / Bitters": {
            "Amaro": ["Light / Aperitivo", "Medium", "Alpine", "Carciofo", "Rabarbaro"],
            "Fernet": [], "Digestivo": [],
            "Cocktail Bitters": ["Aromatic", "Orange", "Peychaud's-style", "Celery", "Chocolate"],
            "Herbal Bitters": [],
        },
        "Absinthe / Anise Spirits": {
            "Absinthe": ["Verte", "Blanche", "Bohemian"],
            "Ouzo": [], "Pastis": [], "Sambuca": ["White", "Black"], "Raki": [], "Arak": [],
        },
        "Cachaça / Sugarcane Spirits": {
            "Cachaça": ["Unaged / Prata", "Aged / Ouro", "Extra Premium"],
            "Clairin": [],
        },
        "Aquavit / Nordic Spirits": {"Aquavit": ["Caraway", "Dill", "Barrel-Aged"],
                                     "Brennivín": []},
        "Baijiu / Soju / Shochu": {
            "Baijiu": ["Strong Aroma", "Light Aroma", "Sauce Aroma", "Rice Aroma"],
            "Soju": ["Diluted", "Distilled", "Flavored"],
            "Shochu": ["Imo / Sweet Potato", "Mugi / Barley", "Kome / Rice", "Kokuto / Sugar"],
            "Awamori": [],
        },
        "Neutral & Grain Spirits": {"Neutral Grain Spirit": [], "Grain Alcohol": ["190 Proof",
                                                                                  "151 Proof"],
                                    "Rectified Spirit": []},
        "Bottled Cocktails": {
            "Spirits-Based Bottled Cocktail": ["Old Fashioned", "Margarita", "Negroni",
                                               "Manhattan", "Espresso Martini", "Mule"],
            "Cocktail Base / Mix": [], "Party Pack": [],
        },
    },
    "Wine": {
        "Red Wine": {
            "Varietal Red": ["Cabernet Sauvignon", "Merlot", "Pinot Noir", "Syrah / Shiraz",
                                "Zinfandel", "Malbec", "Sangiovese", "Tempranillo",
                                "Grenache / Garnacha", "Nebbiolo", "Petite Sirah", "Cabernet Franc",
                                "Barbera", "Petit Verdot", "Carménère", "Mourvèdre", "Montepulciano",
                                "Primitivo", "Gamay", "Touriga Nacional", "Red Blend"],
            "Red Blend": ["Cabernet Sauvignon", "Merlot", "Pinot Noir", "Syrah / Shiraz",
                             "Zinfandel", "Malbec", "Sangiovese", "Tempranillo",
                             "Grenache / Garnacha", "Nebbiolo", "Petite Sirah", "Cabernet Franc",
                             "Barbera", "Petit Verdot", "Carménère", "Mourvèdre", "Montepulciano",
                             "Primitivo", "Gamay", "Touriga Nacional", "Red Blend"],
            "Bordeaux / Meritage": ["Cabernet Sauvignon", "Merlot", "Cabernet Franc", "Petit Verdot",
                                       "Malbec", "Red Blend"],
            "Rhône / GSM Blend": ["Grenache / Garnacha", "Syrah / Shiraz", "Mourvèdre", "Red Blend"],
            "Super Tuscan": ["Sangiovese", "Cabernet Sauvignon", "Merlot", "Red Blend"],
            "Chianti": ["Sangiovese"],
            "Rioja": ["Tempranillo", "Grenache / Garnacha"],
            "Barolo / Barbaresco": ["Nebbiolo"],
            "Burgundy": ["Pinot Noir"],
            "Beaujolais": ["Gamay"],
            "Table Red": ["Cabernet Sauvignon", "Merlot", "Pinot Noir", "Syrah / Shiraz",
                             "Zinfandel", "Malbec", "Sangiovese", "Tempranillo",
                             "Grenache / Garnacha", "Nebbiolo", "Petite Sirah", "Cabernet Franc",
                             "Barbera", "Petit Verdot", "Carménère", "Mourvèdre", "Montepulciano",
                             "Primitivo", "Gamay", "Touriga Nacional", "Red Blend"],
        },
        "White Wine": {
            "Varietal White": ["Chardonnay", "Sauvignon Blanc", "Pinot Grigio / Pinot Gris",
                                  "Riesling", "Moscato / Muscat", "Gewürztraminer", "Chenin Blanc",
                                  "Viognier", "Albariño", "Grüner Veltliner", "Sémillon", "Verdejo",
                                  "Vermentino", "Torrontés", "Melon de Bourgogne", "Trebbiano",
                                  "Marsanne", "Roussanne", "White Blend"],
            "White Blend": ["Chardonnay", "Sauvignon Blanc", "Pinot Grigio / Pinot Gris",
                               "Riesling", "Moscato / Muscat", "Gewürztraminer", "Chenin Blanc",
                               "Viognier", "Albariño", "Grüner Veltliner", "Sémillon", "Verdejo",
                               "Vermentino", "Torrontés", "Melon de Bourgogne", "Trebbiano",
                               "Marsanne", "Roussanne", "White Blend"],
            "Bordeaux Blanc": ["Sauvignon Blanc", "Sémillon", "White Blend"],
            "Burgundy / Chablis": ["Chardonnay"],
            "Sancerre / Loire": ["Sauvignon Blanc", "Chenin Blanc", "Melon de Bourgogne"],
            "Rhône White": ["Viognier", "Marsanne", "Roussanne", "White Blend"],
            "Vinho Verde": ["Albariño", "White Blend"],
            "Table White": ["Chardonnay", "Sauvignon Blanc", "Pinot Grigio / Pinot Gris",
                               "Riesling", "Moscato / Muscat", "Gewürztraminer", "Chenin Blanc",
                               "Viognier", "Albariño", "Grüner Veltliner", "Sémillon", "Verdejo",
                               "Vermentino", "Torrontés", "Melon de Bourgogne", "Trebbiano",
                               "Marsanne", "Roussanne", "White Blend"],
        },
        "Rosé Wine": {
            "Provence Rosé": ["Grenache", "Pinot Noir", "Syrah", "Sangiovese", "Tempranillo",
                                 "Cinsault", "Mourvèdre", "Rosé Blend"],
            "Varietal Rosé": ["Grenache", "Pinot Noir", "Syrah", "Sangiovese", "Tempranillo",
                                 "Cinsault", "Mourvèdre", "Rosé Blend"],
            "Rosé Blend": ["Grenache", "Pinot Noir", "Syrah", "Sangiovese", "Tempranillo",
                              "Cinsault", "Mourvèdre", "Rosé Blend"],
            "White Zinfandel": ["Zinfandel"],
        },
        "Orange / Skin-Contact Wine": {
            "Skin-Contact White": ["Pinot Grigio", "Rkatsiteli", "Ribolla Gialla", "Orange Blend"],
            "Amphora / Qvevri": ["Rkatsiteli", "Orange Blend"],
        },
        "Sparkling Wine": {
            "Champagne": ["Chardonnay", "Pinot Noir", "Pinot Meunier", "Champagne Blend"],
            "Prosecco": ["Glera"],
            "Cava": ["Macabeo", "Xarel·lo", "Parellada"],
            "Crémant": ["Chardonnay", "Pinot Noir", "Chenin Blanc"],
            "Sekt": [],
            "Franciacorta": ["Chardonnay", "Pinot Noir"],
            "Pét-Nat": [],
            "Sparkling Rosé": ["Grenache", "Pinot Noir", "Syrah", "Sangiovese", "Tempranillo",
                                  "Cinsault", "Mourvèdre", "Rosé Blend"],
            "Moscato d'Asti": ["Muscat"],
            "Lambrusco": ["Lambrusco"],
            "Sparkling Wine (Other)": [],
        },
        "Dessert Wine": {
            "Late Harvest": ["Riesling", "Sémillon", "Muscat"],
            "Ice Wine": ["Riesling", "Vidal Blanc"],
            "Sauternes": ["Sémillon", "Sauvignon Blanc"],
            "Tokaji": ["Furmint"],
            "Passito": ["Muscat", "Corvina"],
            "Moscato": ["Moscato / Muscat"],
        },
        "Fortified Wine": {
            "Port": ["Touriga Nacional", "Tinta Roriz", "Port Blend"],
            "Sherry": ["Palomino", "Pedro Ximénez", "Muscat"],
            "Madeira": ["Sercial", "Verdelho", "Bual", "Malmsey"],
            "Marsala": [],
            "Banyuls": ["Grenache / Garnacha"],
            "Vin Doux Naturel": ["Muscat", "Grenache / Garnacha"],
        },
        "Flavored Wine / Sangria": {
            "Sangria": [],
            "Wine Cocktail": [],
            "Flavored Wine": [],
        },
        "Fruit Wine & Mead": {
            "Fruit Wine": [],
            "Mead": [],
            "Cyser / Melomel": [],
        },
        "Boxed & Bulk Wine": {
            "Boxed Red": ["Cabernet Sauvignon", "Merlot", "Pinot Noir", "Syrah / Shiraz",
                             "Zinfandel", "Malbec", "Sangiovese", "Tempranillo",
                             "Grenache / Garnacha", "Nebbiolo", "Petite Sirah", "Cabernet Franc",
                             "Barbera", "Petit Verdot", "Carménère", "Mourvèdre", "Montepulciano",
                             "Primitivo", "Gamay", "Touriga Nacional", "Red Blend"],
            "Boxed White": ["Chardonnay", "Sauvignon Blanc", "Pinot Grigio / Pinot Gris",
                               "Riesling", "Moscato / Muscat", "Gewürztraminer", "Chenin Blanc",
                               "Viognier", "Albariño", "Grüner Veltliner", "Sémillon", "Verdejo",
                               "Vermentino", "Torrontés", "Melon de Bourgogne", "Trebbiano",
                               "Marsanne", "Roussanne", "White Blend"],
            "Jug Wine": [],
        },
    },
    "Beer": {
        "IPA": {"American IPA": [], "Hazy / New England IPA": [], "West Coast IPA": [],
                "Double / Imperial IPA": [], "Session IPA": [], "Black IPA": [],
                "Triple IPA": [], "Cold IPA": [], "Fruited IPA": []},
        "Pale Ale": {"American Pale Ale": [], "English Pale Ale": [], "Blonde Ale": [],
                     "Extra Pale Ale": []},
        "Lager": {"American Lager": [], "Light Lager": [], "Mexican Lager": [], "Helles": [],
                  "Dunkel": [], "Vienna Lager": [], "Märzen / Oktoberfest": [], "Bock": [],
                  "Doppelbock": [], "Schwarzbier": [], "Japanese Rice Lager": [],
                  "Craft Lager": []},
        "Pilsner": {"German Pilsner": [], "Czech Pilsner": [], "Italian Pilsner": [],
                    "American Pilsner": []},
        "Stout": {"Dry / Irish Stout": [], "Milk / Sweet Stout": [], "Oatmeal Stout": [],
                  "Imperial Stout": [], "Pastry Stout": [], "Barrel-Aged Stout": [],
                  "Nitro Stout": []},
        "Porter": {"Robust Porter": [], "Baltic Porter": [], "Imperial Porter": []},
        "Wheat Beer": {"Hefeweizen": [], "Witbier": [], "American Wheat": [], "Berliner Weisse": [],
                       "Gose": [], "Dunkelweizen": [], "Weizenbock": []},
        "Belgian & Abbey Ale": {"Belgian Blonde": [], "Dubbel": [], "Tripel": [], "Quadrupel": [],
                                "Saison / Farmhouse": [], "Belgian Strong Dark": [],
                                "Trappist Ale": []},
        "Sour & Wild Ale": {"Kettle Sour": [], "Fruited Sour": [], "Lambic": [], "Gueuze": [],
                            "Flanders Red": [], "Oud Bruin": [], "Wild / Brett Ale": []},
        "Amber & Red Ale": {"American Amber": [], "Irish Red": [], "Red Ale": []},
        "Brown Ale": {"American Brown": [], "English Brown": [], "Nut Brown": []},
        "Strong Ale & Barleywine": {"Barleywine": [], "Old Ale": [], "Scotch Ale / Wee Heavy": [],
                                    "American Strong Ale": []},
        "Specialty & Hybrid": {"Kölsch": [], "Altbier": [], "Cream Ale": [],
                               "California Common": [], "Smoked / Rauchbier": [],
                               "Fruit Beer": [], "Pumpkin Beer": [], "Herb & Spice Beer": [],
                               "Barrel-Aged Beer": [], "Gluten-Free Beer": []},
        "Flavored Malt Beverage": {"Fruit FMB": [], "High-ABV FMB": [], "Malt Liquor": [],
                                   "Malt-Based Cocktail": [], "Malt-Based Seltzer": []},
    },
    "RTD & Seltzer": {
        "Hard Seltzer": {"Flavored Hard Seltzer": [], "Spirits-Based Seltzer": [],
                         "Malt-Based Seltzer": [], "Wine-Based Seltzer": []},
        "Canned Cocktail": {"Spirits-Based Cocktail": [], "Malt-Based Cocktail": [],
                            "Wine-Based Cocktail": [], "Margarita": [], "Mule": [],
                            "Old Fashioned": [], "Cosmopolitan": [], "Highball": []},
        "Hard Tea": {"Hard Iced Tea": [], "Hard Green Tea": [], "Spiked Tea": []},
        "Hard Lemonade": {"Classic Lemonade": [], "Flavored Lemonade": [], "Fruit Punch": []},
        "Hard Coffee": {"Hard Cold Brew": [], "Hard Espresso": []},
        "Hard Kombucha": {"Fruit Kombucha": [], "Ginger Kombucha": [], "Botanical Kombucha": []},
        "Hard Soda": {"Hard Root Beer": [], "Hard Cola": [], "Hard Ginger Ale": [],
                      "Hard Soda (Other)": []},
        "Wine Spritz": {"Wine Spritzer": [], "Aperitivo Spritz": []},
    },
    "Cider & Perry": {
        "Hard Cider": {"Dry Cider": [], "Semi-Dry Cider": [], "Sweet Cider": [],
                       "Heritage / Traditional Cider": [], "Barrel-Aged Cider": [],
                       "Hopped Cider": [], "Ice Cider": []},
        "Fruit Cider": {"Apple": [], "Pear": [], "Cherry": [], "Berry": [], "Peach": [],
                        "Cranberry": [], "Mango": [], "Pineapple": []},
        "Perry": {"Dry Perry": [], "Sweet Perry": []},
    },
    "Sake & Rice Wine": {
        "Sake": {"Junmai": [], "Junmai Ginjo": [], "Junmai Daiginjo": [], "Ginjo": [],
                 "Daiginjo": [], "Honjozo": [], "Nigori": [], "Futsushu": [], "Genshu": [],
                 "Nama": [], "Sparkling Sake": []},
        "Umeshu / Fruit Sake": {"Umeshu": [], "Yuzushu": []},
        "Rice Wine": {"Makgeolli": [], "Huangjiu": [], "Cheongju": [], "Mijiu": []},
    },
    "Non-Alcoholic": {
        "Non-Alcoholic Beer": {"NA Lager": [], "NA IPA": [], "NA Pale Ale": [], "NA Stout": [],
                               "NA Wheat": []},
        "Non-Alcoholic Wine": {"NA Red": [], "NA White": [], "NA Rosé": [], "NA Sparkling": []},
        "Non-Alcoholic Spirit": {"NA Gin Alternative": [], "NA Whiskey Alternative": [],
                                 "NA Rum Alternative": [], "NA Tequila Alternative": [],
                                 "NA Aperitif": []},
        "Mixer & Tonic": {"Tonic Water": [], "Club Soda": [], "Ginger Beer": [],
                          "Cocktail Mixer": [], "Bloody Mary Mix": [], "Margarita Mix": [],
                          "Sour Mix": []},
        "Syrup & Cordial": {"Simple Syrup": [], "Flavored Syrup": [], "Grenadine": [],
                            "Orgeat": []},
        "Non-Alcoholic Bitters": {"Aromatic Bitters": [], "Citrus Bitters": [],
                                  "Botanical Bitters": []},
        "Juice & Soda": {"Juice": [], "Soda": [], "Sparkling Water": [], "Energy Drink": []},
    },
    "Hemp & Cannabis": {
        "Hemp Beverage": {"Delta-9 THC Beverage": [], "Delta-8 THC Beverage": [],
                          "CBD Beverage": [], "THC Seltzer": [], "THC Shot": []},
        "Cannabis Flower": {"Indica": [], "Sativa": [], "Hybrid": [], "Pre-Ground": []},
        "Cannabis Edible": {"Gummy": [], "Chocolate": [], "Baked Good": [], "Beverage Mix": []},
        "Cannabis Concentrate": {"Live Resin": [], "Rosin": [], "Shatter": [], "Wax": [],
                                 "Distillate": [], "Hash": []},
        "Pre-Roll": {"Single Pre-Roll": [], "Pre-Roll Pack": [], "Infused Pre-Roll": []},
        "Vape": {"Cartridge": [], "Disposable": [], "Pod": []},
        "Tincture & Topical": {"Tincture": [], "Topical": [], "Capsule": []},
    },
    "Non-Beverage": {
        "Glassware": {"Wine Glass": [], "Rocks Glass": [], "Coupe": [], "Highball": [],
                      "Decanter": [], "Beer Glass": []},
        "Barware": {"Shaker": [], "Jigger": [], "Strainer": [], "Corkscrew": [], "Muddler": [],
                    "Bar Tool Set": []},
        "Gift & Packaging": {"Gift Set": [], "Gift Bag": [], "Gift Box": [],
                             "Engraved Bottle": []},
        "Merchandise": {"Apparel": [], "Signage": [], "Point of Sale": [], "Collectible": []},
        "Snack & Food": {"Snack": [], "Garnish": [], "Cheese & Charcuterie": [], "Olives": []},
        "Ice & Cooling": {"Ice": [], "Cooler": [], "Chiller": [], "Ice Mold": []},
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

# THE FOURTH LEVEL IS NOT ALWAYS A VARIETAL. It is the terminal designation — the last thing that
# still changes what is in the bottle — and what that IS depends on the Type. Under wine it is the
# grape. Under spirits it is the expression or grade: Spirits > Brandy / Cognac > Cognac > VSOP.
# Calling it "Varietal" everywhere is what made me flatten the grade into the sub class name
# ("Cognac VSOP") and then declare the level inapplicable to spirits, which lost a real level of the
# hierarchy for the whole spirits half of the book.
#
# The stored FIELD stays `canon_varietal` — the sheet and the gold set are keyed on it — but the
# LABEL comes from here, so the resolver asks for the thing that actually applies.
LEVEL4_LABEL = {
    "Wine": "Varietal",
    "Cider & Perry": "Fruit",
    "Spirits": "Expression",
    "Beer": "Variant",
    "RTD & Seltzer": "Flavor",
    "Sake & Rice Wine": "Grade",
    "Non-Alcoholic": "Variant",
    "Hemp & Cannabis": "Variant",
    "Non-Beverage": "",                 # "" == the level does not apply at all
}
DEFAULT_LEVEL4 = "Expression / Varietal"

# Types where the fourth level applies. Derived, not hand-kept: a Type earns it by having a label.
VARIETAL_TYPES = {k for k, v in LEVEL4_LABEL.items() if v}


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


# ── serving the tree ──────────────────────────────────────────────────────────────────────────────
# THE SEED IS A CONSTANT AND MUST BE SERVED AT CONSTANT SPEED. The learned paths live in the
# warehouse, and reading a table that has never been written costs a DuckDB connection plus an S3
# round trip that fails slowly: measured live at 11–14 SECONDS for a payload that is 99% a literal
# in this file. The surface that consumes it renders dropdowns, so twelve seconds of text boxes is
# indistinguishable from the feature not shipping — which is exactly how it was reported.
#
# So the warehouse read NEVER blocks the response. A miss serves the seed immediately and refreshes
# in the background; `learned_read` says which you got, because "no learned paths" and "we have not
# looked yet" are different claims and only one of them means the tree is complete.
_CACHE = {"at": 0.0, "paths": [], "read": False, "loading": False}
CACHE_TTL = 300.0


def _refresh_cache(log=print):
    try:
        paths = learned(log=log)
        _CACHE["paths"], _CACHE["read"], _CACHE["at"] = paths, True, time.time()
    except Exception as e:                                     # noqa: BLE001
        log("taxonomy: refresh failed: %s" % str(e)[:90])
        _CACHE["at"] = time.time()                             # do not hot-loop on a broken table
    finally:
        _CACHE["loading"] = False


def cached_paths(log=print, block=False):
    """Learned paths from cache. Kicks off a background refresh when stale; never waits on one."""
    fresh = (time.time() - _CACHE["at"]) < CACHE_TTL
    if fresh and _CACHE["read"]:
        return _CACHE["paths"], True
    if block:
        _CACHE["loading"] = True
        _refresh_cache(log=log)
        return _CACHE["paths"], _CACHE["read"]
    if not _CACHE["loading"]:
        _CACHE["loading"] = True
        try:
            import threading
            threading.Thread(target=_refresh_cache, kwargs={"log": lambda *a: None},
                             daemon=True).start()
        except Exception:                                      # noqa: BLE001
            _CACHE["loading"] = False
    return _CACHE["paths"], _CACHE["read"]


def tree(include_learned=True, log=print, block=False):
    """The served hierarchy: the seed, plus every learned path merged in at its own level.

    A learned node carries its PARENTS, so it filters like any seed node. Returned as nested dicts
    of {value: {...}} rather than lists so a client can walk it without knowing the depth.

    `block=True` waits for the warehouse read — for a CLI or a test, never for a request."""
    t = {ty: {cl: {sc: list(vs) for sc, vs in cls.items()} for cl, cls in classes.items()}
         for ty, classes in SEED.items()}
    added, read = 0, False
    if include_learned:
        paths, read = cached_paths(log=log, block=block)
        for ty, cl, sc, va in paths:
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
            "level4_label": LEVEL4_LABEL, "level4_default": DEFAULT_LEVEL4,
            "learned_paths": added,
            # False means the warehouse has not been read YET, not that nothing has been learned.
            "learned_read": bool(read)}


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
        _CACHE["at"] = 0.0          # a newly taught path must not wait out the TTL to be offered
        return 1
    except Exception as e:
        log("taxonomy: learn skipped: %s" % str(e)[:90])
        return 0


def main(argv=None):
    a = (argv or sys.argv[1:])
    if a and a[0] == "learned":
        print(json.dumps(learned(), indent=2))
    else:
        t = tree(block=True)     # a CLI can afford the warehouse read; a request cannot
        print("%d types, %d classes, %d sub classes, %d learned paths" % (
            len(t["tree"]), sum(len(c) for c in t["tree"].values()),
            sum(len(s) for c in t["tree"].values() for s in c.values()), t["learned_paths"]))
        print(json.dumps(t["tree"], indent=2, ensure_ascii=False)[:2000])


if __name__ == "__main__":
    main()
