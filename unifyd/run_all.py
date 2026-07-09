"""run_all.py — one command to refresh every bev-alc source into the warehouse.

    python run_all.py                # fast: land local snapshots + run API/BD connectors + chains
    python run_all.py --scrape       # also RE-RUN the slow site scrapers first (abc/specs/binnys/shopify)
    python run_all.py --only walmart,kroger

This is the "easy way to pull all of these." The scheduled cloud runner calls the same entrypoint. Each
step is independent — a failure logs and moves on. Prints a warehouse summary of every chain source at the end.
"""
import argparse, importlib, os, subprocess, sys, time

_ROOT = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, _ROOT)


def log(*a): print(*a, file=sys.stderr, flush=True)


def _load_creds():
    for p in [os.environ.get("WH_ENV_FILE", ""),
              os.path.expanduser("~/Desktop/Desktop - Chris’s MacBook Pro/Projects/hoodie-backend/warehouse.env"),
              os.path.join(_ROOT, "warehouse.env")]:
        if p and os.path.exists(p):
            for line in open(p):
                line = line.strip()
                if line and not line.startswith("#") and "=" in line:
                    k, v = line.split("=", 1); k = k.strip(); v = v.strip().strip('"').strip("'")
                    if k and v and not os.environ.get(k):
                        os.environ[k] = v
            break


# site scrapers that write a local snapshot (re-run with --scrape, then land) — modules in unifyd/
SITE_SCRAPERS = {"abc": "abc_fws_scraper", "specs": "specs_scraper",
                 "binnys": "binnys_scraper", "shopify": "shopify_scraper"}


def step(name, fn):
    t = time.time()
    try:
        fn(); log("  ✓ %-10s (%.0fs)" % (name, time.time() - t))
    except Exception as e:
        log("  ✗ %-10s %s" % (name, str(e)[:90]))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--scrape", action="store_true", help="re-run the slow site scrapers before landing")
    ap.add_argument("--only", default="", help="comma list: chains,snapshots,walmart,kroger")
    a = ap.parse_args()
    only = {x.strip() for x in a.only.split(",") if x.strip()}
    want = lambda k: (not only) or k in only

    _load_creds()
    os.chdir(_ROOT)
    log("== run_all @ %s ==" % time.strftime("%Y-%m-%d %H:%M:%S"))

    if a.scrape:                                   # optional: refresh the local snapshots first (slow)
        for conn, mod in SITE_SCRAPERS.items():
            if want(conn) or want("snapshots"):
                step("scrape:" + conn, lambda m=mod: subprocess.run(
                    [sys.executable, os.path.join(_ROOT, m + ".py")], check=False, timeout=3600))

    if want("chains"):
        step("chains", lambda: importlib.import_module("chains").build())
    if want("snapshots"):
        step("snapshots", lambda: importlib.import_module("snapshot_land").land_all())
    if want("walmart"):
        step("walmart", lambda: subprocess.run(
            [sys.executable, os.path.join(_ROOT, "walmart_scraper.py"), "--per", "3", "--note", "run_all"], check=False))
    if want("kroger"):
        step("kroger", lambda: subprocess.run(
            [sys.executable, os.path.join(_ROOT, "kroger_api.py")], check=False))

    # summary — every chain source now in the warehouse
    try:
        import warehouse, time as _t
        now = _t.time()
        chains = ("walmart_products", "abc_products", "specs_products", "binnys_products",
                  "shopify_products", "kroger_products")
        ds = {d["name"]: d for d in warehouse.list_datasets()}
        log("\n== chain sources in the warehouse ==")
        for t in chains:
            d = ds.get(t)
            if d:
                mod = d.get("modified"); fresh = ("%dm ago" % ((now - mod) / 60)) if mod else "n/a"
                log("  %-18s %8s rows · %s" % (t, "{:,}".format(d.get("rows", 0)), fresh))
            else:
                log("  %-18s (none yet)" % t)
    except Exception as e:
        log("summary failed: %s" % str(e)[:80])


if __name__ == "__main__":
    main()
