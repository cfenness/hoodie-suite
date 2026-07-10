"""chains.py — the bev-alc CHAINS registry: the top retail + on-premise chains, each assessed for whether
we can get PRICING and/or INVENTORY, and how. Any chain reachable for one or both is a SOURCE candidate.

This is the seed of the "stuff we are and want to scrape" catalog. It lands in the warehouse as
`bevalc_chains` so the Sources/Catalog and the tracker read it. Feasibility method drives the connector:

  method            how we'd pull it                                        pricing  inventory
  bd:walmart        Bright Data walmart_product (built)                     yes      store (1 store today)
  bd:target         Bright Data target scraper                             yes      store
  bd:instacart      Bright Data instacart — the AGGREGATOR (many banners)  yes      store  ← key unlock
  api:kroger        Kroger Developer API (OAuth, products+locations+price) yes      store
  scrape:site       owned scraper of the chain's e-comm site               yes      chain/store
  abc_fws           our existing ABC FWS BigCommerce store tracker (built) yes      store
  socrata:<state>   control-state open-data (product + price)              yes      chain
  menu              scrape the on-premise menu (page or OCR the image)     sometimes assortment
  blocked           members-only / no public data                         no       no

`auth` = the chain's authorization model for assortment (mandatory / optional wording varies); tracked so
we can flag mandatory vs optional placements once assortment data lands. Locations are approximate.
"""
import os, sys, time
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import warehouse

# (name, premise, channel, banners, est_locations, website, pricing, inventory, method, auth, note)
_C = [
    # ── off-premise · mass / club ──
    ("Walmart", "off", "mass", "", 4600, "walmart.com", "yes", "store", "bd:walmart", "mandatory/optional", "built"),
    ("Target", "off", "mass", "", 1950, "target.com", "yes", "store", "bd:target", "mandatory/optional", ""),
    ("Costco", "off", "club", "", 600, "costco.com", "yes", "chain", "scrape:site", "optional", "members; some public"),
    ("Sam's Club", "off", "club", "", 600, "samsclub.com", "yes", "store", "scrape:site", "optional", ""),
    ("BJ's Wholesale", "off", "club", "", 245, "bjs.com", "yes", "store", "scrape:site", "optional", ""),
    # ── off-premise · grocery (national/regional) ──
    ("Kroger", "off", "grocery", "Ralphs;Fred Meyer;King Soopers;Harris Teeter;Fry's;QFC;Smith's;Dillons;Pick 'n Save", 2750, "kroger.com", "yes", "store", "api:kroger", "mandatory/optional", "official API"),
    ("Albertsons", "off", "grocery", "Safeway;Vons;Jewel-Osco;Acme;Shaw's;Star Market;Tom Thumb;Randalls;Pavilions", 2270, "albertsons.com", "yes", "store", "bd:instacart", "mandatory/optional", ""),
    ("Publix", "off", "grocery", "", 1400, "publix.com", "yes", "store", "bd:instacart", "mandatory/optional", ""),
    ("H-E-B", "off", "grocery", "Central Market", 435, "heb.com", "yes", "store", "scrape:site", "mandatory/optional", "TX"),
    ("Meijer", "off", "grocery", "", 260, "meijer.com", "yes", "store", "bd:instacart", "mandatory/optional", ""),
    ("Ahold Delhaize", "off", "grocery", "Stop & Shop;Giant;Food Lion;Hannaford;Giant Food", 2000, "stopandshop.com", "yes", "store", "bd:instacart", "optional", ""),
    ("Wegmans", "off", "grocery", "", 110, "wegmans.com", "yes", "store", "scrape:site", "optional", ""),
    ("Hy-Vee", "off", "grocery", "", 285, "hy-vee.com", "yes", "store", "bd:instacart", "optional", ""),
    ("WinCo Foods", "off", "grocery", "", 140, "wincofoods.com", "no", "no", "blocked", "n/a", "low online"),
    ("Giant Eagle", "off", "grocery", "GetGo", 470, "gianteagle.com", "yes", "store", "bd:instacart", "optional", ""),
    ("Aldi", "off", "grocery", "", 2400, "aldi.us", "yes", "store", "bd:instacart", "optional", "limited alc"),
    ("Sprouts", "off", "grocery", "", 400, "sprouts.com", "yes", "store", "bd:instacart", "optional", ""),
    ("Whole Foods", "off", "grocery", "", 530, "wholefoodsmarket.com", "yes", "store", "bd:instacart", "optional", "Amazon"),
    ("Trader Joe's", "off", "grocery", "", 560, "traderjoes.com", "no", "no", "blocked", "n/a", "no e-comm"),
    ("Hy-Vee", "off", "grocery", "", 285, "hy-vee.com", "yes", "store", "bd:instacart", "optional", ""),
    ("Raley's", "off", "grocery", "Bel Air;Nob Hill", 235, "raleys.com", "yes", "store", "bd:instacart", "optional", ""),
    ("Save Mart", "off", "grocery", "Lucky", 200, "savemart.com", "yes", "store", "bd:instacart", "optional", ""),
    ("Ingles Markets", "off", "grocery", "", 200, "ingles-markets.com", "yes", "store", "scrape:site", "optional", ""),
    ("Weis Markets", "off", "grocery", "", 200, "weismarkets.com", "yes", "store", "bd:instacart", "optional", ""),
    ("Price Chopper", "off", "grocery", "Market 32", 130, "pricechopper.com", "yes", "store", "bd:instacart", "optional", ""),
    ("Schnucks", "off", "grocery", "", 110, "schnucks.com", "yes", "store", "bd:instacart", "optional", ""),
    ("SpartanNash", "off", "grocery", "Family Fare;D&W", 145, "spartannash.com", "yes", "store", "bd:instacart", "optional", ""),
    ("Southeastern Grocers", "off", "grocery", "Winn-Dixie;Harveys;Fresco y Más", 420, "winndixie.com", "yes", "store", "bd:instacart", "optional", ""),
    ("Stater Bros", "off", "grocery", "", 170, "staterbros.com", "yes", "store", "bd:instacart", "optional", ""),
    ("Wakefern", "off", "grocery", "ShopRite;Price Rite", 360, "shoprite.com", "yes", "store", "bd:instacart", "optional", ""),
    ("Brookshire", "off", "grocery", "Super 1 Foods", 200, "brookshires.com", "yes", "store", "bd:instacart", "optional", ""),
    ("Wegmans", "off", "grocery", "", 110, "wegmans.com", "yes", "store", "scrape:site", "optional", ""),
    # ── off-premise · liquor / wine chains ──
    ("Total Wine & More", "off", "liquor", "", 270, "totalwine.com", "yes", "store", "scrape:site", "mandatory/optional", "priority target"),
    ("BevMo!", "off", "liquor", "", 160, "bevmo.com", "yes", "store", "scrape:site", "optional", "Gopuff-owned"),
    ("ABC Fine Wine & Spirits", "off", "liquor", "", 145, "abcfws.com", "yes", "store", "abc_fws", "optional", "built"),
    ("Spec's", "off", "liquor", "", 200, "specsonline.com", "yes", "store", "scrape:site", "optional", "TX"),
    ("Binny's Beverage Depot", "off", "liquor", "", 45, "binnys.com", "yes", "store", "scrape:site", "optional", "IL"),
    ("Twin Liquors", "off", "liquor", "", 90, "twinliquors.com", "yes", "chain", "scrape:site", "optional", "TX"),
    ("Liquor Barn", "off", "liquor", "", 25, "liquorbarn.com", "yes", "store", "scrape:site", "optional", "KY"),
    ("Bottle King", "off", "liquor", "", 40, "bottleking.com", "yes", "store", "scrape:site", "optional", "NJ"),
    ("Wine.com", "off", "liquor", "", 0, "wine.com", "yes", "chain", "scrape:site", "n/a", "e-comm only"),
    ("Drizly", "off", "liquor", "", 0, "drizly.com", "yes", "store", "scrape:site", "n/a", "Uber; delivery aggregator"),
    ("Gopuff", "off", "liquor", "", 0, "gopuff.com", "yes", "store", "scrape:site", "n/a", "delivery"),
    ("Cost Plus World Market", "off", "liquor", "", 240, "worldmarket.com", "yes", "chain", "scrape:site", "optional", ""),
    ("Applejack", "off", "liquor", "", 1, "applejack.com", "yes", "chain", "scrape:site", "n/a", "CO"),
    ("Hi-Time Wine Cellars", "off", "liquor", "", 1, "hitimewine.net", "yes", "chain", "scrape:site", "n/a", "CA"),
    ("Astor Wines & Spirits", "off", "liquor", "", 1, "astorwines.com", "yes", "chain", "scrape:site", "n/a", "NY"),
    ("K&L Wine Merchants", "off", "liquor", "", 3, "klwines.com", "yes", "store", "scrape:site", "n/a", "CA"),
    ("Gary's Wine", "off", "liquor", "", 4, "garyswine.com", "yes", "chain", "scrape:site", "n/a", "NJ"),
    # ── off-premise · convenience / fuel ──
    ("7-Eleven", "off", "convenience", "Speedway;Stripes", 13000, "7-eleven.com", "yes", "store", "bd:instacart", "optional", ""),
    ("Circle K", "off", "convenience", "", 7000, "circlek.com", "no", "no", "menu", "optional", "limited online"),
    ("Casey's", "off", "convenience", "", 2600, "caseys.com", "yes", "store", "scrape:site", "optional", ""),
    ("Wawa", "off", "convenience", "", 1050, "wawa.com", "no", "no", "menu", "optional", ""),
    ("Sheetz", "off", "convenience", "", 700, "sheetz.com", "no", "no", "menu", "optional", ""),
    ("QuikTrip", "off", "convenience", "", 1000, "quiktrip.com", "no", "no", "menu", "optional", ""),
    ("Kwik Trip", "off", "convenience", "", 850, "kwiktrip.com", "no", "no", "menu", "optional", ""),
    ("RaceTrac", "off", "convenience", "", 800, "racetrac.com", "no", "no", "menu", "optional", ""),
    ("Murphy USA", "off", "convenience", "", 1700, "murphyusa.com", "no", "no", "blocked", "optional", ""),
    ("Cumberland Farms", "off", "convenience", "", 600, "cumberlandfarms.com", "no", "no", "menu", "optional", ""),
    # ── off-premise · drug / dollar ──
    ("CVS", "off", "drug", "", 9000, "cvs.com", "yes", "store", "bd:instacart", "optional", "beer/wine some states"),
    ("Walgreens", "off", "drug", "", 8500, "walgreens.com", "yes", "store", "bd:instacart", "optional", ""),
    ("Rite Aid", "off", "drug", "", 1700, "riteaid.com", "yes", "store", "bd:instacart", "optional", ""),
    ("Dollar General", "off", "dollar", "", 20000, "dollargeneral.com", "yes", "store", "scrape:site", "optional", ""),
    # ── off-premise · control states (open data) ──
    ("PA Fine Wine & Good Spirits", "off", "control", "", 600, "finewineandgoodspirits.com", "yes", "store", "scrape:site", "mandatory", "PLCB"),
    ("NC ABC", "off", "control", "", 430, "abc.nc.gov", "yes", "store", "socrata:nc", "mandatory", ""),
    ("Virginia ABC", "off", "control", "", 400, "abc.virginia.gov", "yes", "store", "scrape:site", "mandatory", ""),
    ("Utah DABS", "off", "control", "", 50, "abs.utah.gov", "yes", "store", "scrape:site", "mandatory", ""),
    ("Ohio (OHLQ)", "off", "control", "", 480, "ohlq.com", "yes", "store", "scrape:site", "mandatory", ""),
    ("Oregon OLCC", "off", "control", "", 280, "oregonliquorsearch.com", "yes", "store", "scrape:site", "mandatory", ""),
    ("NH Liquor & Wine Outlet", "off", "control", "", 65, "liquorandwineoutlets.com", "yes", "store", "scrape:site", "mandatory", ""),
    ("Idaho ISLD", "off", "control", "", 170, "idaholiquor.com", "yes", "chain", "scrape:site", "mandatory", ""),
    ("Montana", "off", "control", "", 95, "mtrevenue.gov", "yes", "chain", "socrata:mt", "mandatory", ""),
    ("Alabama ABC", "off", "control", "", 220, "alabcstore.com", "yes", "chain", "scrape:site", "mandatory", ""),
    ("Mississippi ABC", "off", "control", "", 0, "dor.ms.gov", "yes", "chain", "scrape:site", "mandatory", "warehouse"),
    ("Iowa ABD", "off", "control", "", 0, "abd.iowa.gov", "yes", "chain", "socrata:ia", "mandatory", "wholesale data (built)"),
    # ── on-premise · casual dining chains (menus) ──
    ("Applebee's", "on", "casual-dining", "", 1500, "applebees.com", "sometimes", "assortment", "menu", "optional", ""),
    ("Chili's", "on", "casual-dining", "", 1100, "chilis.com", "sometimes", "assortment", "menu", "optional", ""),
    ("Olive Garden", "on", "casual-dining", "", 900, "olivegarden.com", "sometimes", "assortment", "menu", "optional", "Darden"),
    ("Texas Roadhouse", "on", "casual-dining", "", 740, "texasroadhouse.com", "sometimes", "assortment", "menu", "optional", ""),
    ("Buffalo Wild Wings", "on", "sports-bar", "", 1200, "buffalowildwings.com", "sometimes", "assortment", "menu", "optional", ""),
    ("Outback Steakhouse", "on", "casual-dining", "", 700, "outback.com", "sometimes", "assortment", "menu", "optional", "Bloomin"),
    ("LongHorn Steakhouse", "on", "casual-dining", "", 570, "longhornsteakhouse.com", "sometimes", "assortment", "menu", "optional", "Darden"),
    ("Red Lobster", "on", "casual-dining", "", 650, "redlobster.com", "sometimes", "assortment", "menu", "optional", ""),
    ("TGI Fridays", "on", "casual-dining", "", 460, "tgifridays.com", "sometimes", "assortment", "menu", "optional", ""),
    ("The Cheesecake Factory", "on", "casual-dining", "", 210, "thecheesecakefactory.com", "sometimes", "assortment", "menu", "optional", ""),
    ("BJ's Restaurant & Brewhouse", "on", "brewpub", "", 215, "bjsrestaurants.com", "sometimes", "assortment", "menu", "optional", ""),
    ("Yard House", "on", "sports-bar", "", 85, "yardhouse.com", "sometimes", "assortment", "menu", "optional", "Darden; huge tap"),
    ("Dave & Buster's", "on", "entertainment", "", 220, "daveandbusters.com", "sometimes", "assortment", "menu", "optional", ""),
    ("Twin Peaks", "on", "sports-bar", "", 115, "twinpeaksrestaurant.com", "sometimes", "assortment", "menu", "optional", ""),
    ("Hooters", "on", "sports-bar", "", 300, "hooters.com", "sometimes", "assortment", "menu", "optional", ""),
    ("Cooper's Hawk", "on", "casual-dining", "", 60, "coopershawkwinery.com", "yes", "assortment", "scrape:site", "mandatory", "own wine club"),
    ("Miller's Ale House", "on", "sports-bar", "", 100, "millersalehouse.com", "sometimes", "assortment", "menu", "optional", ""),
    ("On The Border", "on", "casual-dining", "", 120, "ontheborder.com", "sometimes", "assortment", "menu", "optional", ""),
    ("Chuy's", "on", "casual-dining", "", 100, "chuys.com", "sometimes", "assortment", "menu", "optional", ""),
    ("Bar Louie", "on", "bar", "", 60, "barlouie.com", "sometimes", "assortment", "menu", "optional", ""),
    ("World of Beer", "on", "bar", "", 40, "worldofbeer.com", "sometimes", "assortment", "menu", "optional", ""),
    ("Old Chicago", "on", "brewpub", "", 25, "oldchicago.com", "sometimes", "assortment", "menu", "optional", ""),
    # ── on-premise · upscale / steakhouse ──
    ("Ruth's Chris Steak House", "on", "fine-dining", "", 155, "ruthschris.com", "yes", "assortment", "menu", "optional", "wine list"),
    ("Fleming's", "on", "fine-dining", "", 65, "flemingssteakhouse.com", "yes", "assortment", "menu", "optional", "Bloomin"),
    ("The Capital Grille", "on", "fine-dining", "", 65, "thecapitalgrille.com", "yes", "assortment", "menu", "optional", "Darden"),
    ("Morton's The Steakhouse", "on", "fine-dining", "", 70, "mortons.com", "yes", "assortment", "menu", "optional", ""),
    ("Del Frisco's", "on", "fine-dining", "", 25, "delfriscos.com", "yes", "assortment", "menu", "optional", ""),
    ("Eddie V's", "on", "fine-dining", "", 30, "eddiev.com", "yes", "assortment", "menu", "optional", "Darden"),
    ("STK Steakhouse", "on", "fine-dining", "", 25, "stksteakhouse.com", "yes", "assortment", "menu", "optional", ""),
    ("Fogo de Chão", "on", "fine-dining", "", 60, "fogodechao.com", "yes", "assortment", "menu", "optional", ""),
    ("Seasons 52", "on", "fine-dining", "", 40, "seasons52.com", "yes", "assortment", "menu", "optional", "Darden"),
    # ── on-premise · hotels / casinos (F&B groups) ──
    ("Marriott", "on", "hotel", "", 6000, "marriott.com", "no", "assortment", "menu", "optional", "F&B outlets"),
    ("Hilton", "on", "hotel", "", 5500, "hilton.com", "no", "assortment", "menu", "optional", ""),
    ("Hyatt", "on", "hotel", "", 1100, "hyatt.com", "no", "assortment", "menu", "optional", ""),
    ("IHG", "on", "hotel", "Holiday Inn;Kimpton", 6000, "ihg.com", "no", "assortment", "menu", "optional", ""),
    ("Caesars Entertainment", "on", "casino", "", 50, "caesars.com", "sometimes", "assortment", "menu", "optional", ""),
    ("MGM Resorts", "on", "casino", "", 30, "mgmresorts.com", "sometimes", "assortment", "menu", "optional", ""),
    ("Boyd Gaming", "on", "casino", "", 28, "boydgaming.com", "sometimes", "assortment", "menu", "optional", ""),
    ("Penn Entertainment", "on", "casino", "", 40, "pennentertainment.com", "sometimes", "assortment", "menu", "optional", ""),
    # ── supplier TRADE RESOURCE pages (brand-owner portfolios) — after the retail chains. Not
    #    pricing/inventory, but the canonical CATALOG + bottle-shot IMAGES + spec/sell sheets straight
    #    from the supplier. Public portfolio pages are open; the full trade centers gate on a trade acct.
    #    method supplier:trade. (Gallo, Diageo, etc. — a lot of suppliers run one.)
    ("E&J Gallo", "supplier", "portfolio", "Barefoot;Apothic;La Marca;Josh;High Noon;New Amsterdam", 0, "gallo.com/our-wines", "no", "no", "supplier:trade", "trade acct for full", "portfolio + bottle shots + specs"),
    ("Constellation Brands", "supplier", "portfolio", "Modelo;Corona;Robert Mondavi;Kim Crawford;Meiomi;High West", 0, "cbrands.com/brands", "no", "no", "supplier:trade", "trade acct for full", "portfolio + images"),
    ("Diageo", "supplier", "portfolio", "Smirnoff;Crown Royal;Johnnie Walker;Captain Morgan;Don Julio;Casamigos;Guinness", 0, "diageo.com/en/our-brands", "no", "no", "supplier:trade", "MyDiageo trade portal", "portfolio + images; trade resources gated"),
    ("Pernod Ricard", "supplier", "portfolio", "Jameson;Absolut;Malibu;Kahlua;Jacob's Creek;The Glenlivet", 0, "pernod-ricard.com/en/our-brands", "no", "no", "supplier:trade", "trade acct for full", "portfolio + images"),
    ("Brown-Forman", "supplier", "portfolio", "Jack Daniel's;Woodford Reserve;Old Forester;Herradura;Korbel", 0, "brown-forman.com/brands", "no", "no", "supplier:trade", "trade acct for full", "portfolio + images"),
    ("Bacardi", "supplier", "portfolio", "Bacardi;Grey Goose;Patron;Bombay Sapphire;Dewar's;Martini", 0, "bacardilimited.com/our-brands", "no", "no", "supplier:trade", "trade acct for full", "portfolio + images"),
    ("Suntory Global Spirits", "supplier", "portfolio", "Jim Beam;Maker's Mark;Basil Hayden;Hornitos;Sauza;Roku", 0, "suntoryglobalspirits.com/brands", "no", "no", "supplier:trade", "trade acct for full", "ex-Beam Suntory; portfolio + images"),
    ("Sazerac", "supplier", "portfolio", "Buffalo Trace;Fireball;Southern Comfort;Wheatley;Eagle Rare", 0, "sazerac.com/our-brands", "no", "no", "supplier:trade", "trade acct for full", "portfolio + images"),
    ("Campari Group", "supplier", "portfolio", "Campari;Aperol;Wild Turkey;Espolon;Grand Marnier;SKYY", 0, "camparigroup.com/en/brands", "no", "no", "supplier:trade", "trade acct for full", "portfolio + images"),
    ("Moet Hennessy", "supplier", "portfolio", "Hennessy;Moet & Chandon;Veuve Clicquot;Dom Perignon;Belvedere", 0, "moethennessy.com/en/our-maisons", "no", "no", "supplier:trade", "trade acct for full", "LVMH; portfolio + images"),
    ("Treasury Wine Estates", "supplier", "portfolio", "19 Crimes;Sterling;Beringer;Penfolds;Matua;Frank Family", 0, "twe-marketing.com", "no", "no", "supplier:trade", "trade marketing portal", "sell sheets + POS + images (trade)"),
    ("The Wine Group", "supplier", "portfolio", "Cupcake;Franzia;Chloe;Benziger;Concannon", 0, "thewinegroup.com/brands", "no", "no", "supplier:trade", "trade acct for full", "portfolio + images"),
    ("Deutsch Family", "supplier", "portfolio", "Josh Cellars;Yellow Tail;Fleur de Mer;[yellow tail]", 0, "deutschfamily.com/brands", "no", "no", "supplier:trade", "trade acct for full", "importer portfolio + images"),
    ("Anheuser-Busch InBev", "supplier", "portfolio", "Budweiser;Michelob Ultra;Stella Artois;Cutwater;Nutrl", 0, "anheuser-busch.com/brands", "no", "no", "supplier:trade", "trade acct for full", "beer+RTD portfolio + images"),
    ("Molson Coors", "supplier", "portfolio", "Coors;Miller;Blue Moon;Topo Chico;Simply Spiked", 0, "molsoncoors.com/our-brands", "no", "no", "supplier:trade", "trade acct for full", "beer+RTD portfolio + images"),
]

FIELDS = ["chain", "premise", "channel", "banners", "est_locations", "website",
          "pricing", "inventory", "method", "auth", "note",
          "is_source", "yields", "family"]


def _rows():
    seen, out = set(), []
    for t in _C:
        name = t[0]
        if name in seen:            # de-dupe accidental repeats
            continue
        seen.add(name)
        pricing, inventory = t[6], t[7]
        family = t[8].split(":")[0]
        yields_list = [x for x, ok in (("pricing", pricing not in ("", "no")),
                                       ("inventory", inventory not in ("", "no"))) if ok]
        if family == "supplier":            # trade pages yield catalog + bottle-shot images, not price/inv
            yields_list += ["catalog", "images"]
        elif t[8] == "menu":
            yields_list += ["assortment"]
        is_source = bool(yields_list)       # any useful yield makes it a source candidate
        yields = ",".join(yields_list)
        out.append(dict(chain=name, premise=t[1], channel=t[2], banners=t[3], est_locations=t[4],
                        website=t[5], pricing=pricing, inventory=inventory, method=t[8], auth=t[9],
                        note=t[10], is_source=is_source, yields=yields, family=family))
    return out


def build():
    rows = _rows()
    warehouse.write_parquet("bevalc_chains", rows)
    srcs = [r for r in rows if r["is_source"]]
    print("bevalc_chains: %d chains (%d source-reachable) -> warehouse" % (len(rows), len(srcs)))
    from collections import Counter
    print("  by method:", dict(Counter(r["method"] for r in rows)))
    print("  by premise:", dict(Counter(r["premise"] for r in rows)))
    return len(rows), len(srcs)


if __name__ == "__main__":
    build()
