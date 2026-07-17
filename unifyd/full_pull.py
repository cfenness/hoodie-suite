#!/usr/bin/env python3
"""full_pull.py — run every CONFIRMED, no-BD/no-cookie source at FULL scale (crawl_all / cap=None / limit=None),
NOT a sample. Sequential so tables don't collide; each step guarded so one failure doesn't stop the rest.

NOT here (they need a warmed cookie / residential browser → run on the Mac/hybrid, not this batch):
  Total Wine's getProduct counts, 7NOW, Walmart, the aggregators, Target/Publix/DoorDash (BD).
Total Wine's mobile-UA CATALOG crawl IS included (it's the confirmed direct recipe; PX may still bite from here).

Multi-hour job — run in the background. Prints a per-table row-count summary at the end.
"""
import os, sys, time
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
os.environ["SPECS_QTY"] = "0"                    # full catalog: skip the per-(store,upc) count fan-out (~2M calls)
import kroger_api; kroger_api._load_creds()
import warehouse

OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "full_out")


def _o(n):
    p = os.path.join(OUT, n); os.makedirs(p, exist_ok=True); return p


def step(name, fn):
    t = time.time()
    try:
        fn(); print("  OK  %-24s %6.0fs" % (name, time.time() - t), file=sys.stderr, flush=True)
    except Exception as e:
        print("  ERR %-24s %s" % (name, str(e)[:130]), file=sys.stderr, flush=True)


def _count(t):
    try:
        return warehouse.query(t, "SELECT count(*) c FROM t")[0]["c"]
    except Exception:
        return None


def run():
    import binnys_scraper, specs_scraper, haskells, abc_facets, abc_fws_scraper
    import control_state, census, winebow, off_premise, total_wine
    print("== full_pull start %s ==" % time.strftime("%Y-%m-%d %H:%M"), file=sys.stderr, flush=True)
    step("binnys (full index)",  lambda: binnys_scraper.pull(crawl_all=True, out=_o("binnys"), state_dir=_o("binnys")))
    step("specs (full sitemap)", lambda: specs_scraper.pull(crawl_all=True, out=_o("specs"), state_dir=_o("specs")))
    step("abc_facets (full)",    lambda: abc_facets.pull(cap=None))
    step("abc_fws (full stores)",lambda: abc_fws_scraper.pull(crawl_all=True, out=_o("abc"), state_dir=_o("abc")))
    step("haskells (full)",      lambda: haskells.run(limit=None))
    step("control_states (all)", lambda: control_state.build_all())
    step("census",               lambda: census.pull())
    step("winebow (portfolio)",  lambda: winebow.pull())
    step("offprem census",       lambda: off_premise.run_census())
    step("total_wine (mUA cat)", lambda: total_wine.crawl_land(cap=200000))

    tables = ["binnys_products", "specs_products", "abc_products", "abc_catalog", "haskells_products",
              "census_acs", "winebow_brands", "offprem_products", "total_wine_products"]
    print("\n== row counts after full pull ==", file=sys.stderr, flush=True)
    for t in tables:
        c = _count(t)
        print("  %-26s %s" % (t, "{:,}".format(c) if c is not None else "—"), file=sys.stderr, flush=True)


if __name__ == "__main__":
    run()
