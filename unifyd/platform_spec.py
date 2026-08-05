"""platform_spec.py — ONE declaration per platform; sites x phases, expanded at import.

WHY THIS EXISTS. UberEats and Postmates are the same Uber BFF on two domains, served by four
modules that already take `--site` as a parameter. The registry re-hardcoded that parameterisation
as eight hand-typed entries whose only real content is an inline Python string. The cost is not
verbosity — it is that **a difference between two hand-typed strings is indistinguishable from a
typo**, and the registry cannot tell you which it is.

Two live drifts prove the point. Both were invisible until the entries were laid side by side:

  1. ENRICH TOPOLOGY. `ubereats` passes `--no-enrich` and runs a separate `ubereats-enrich` job;
     `postmates` passes no such flag and enriches inline. Settled (docs/PIPELINE-DESIGN.md §4):
     inline is CORRECT — there is no `postmates-enrich` entry, so inline is the only path by which
     Postmates ever gets UPC/GTIN, and `known_items()` already skips resolved items. The UberEats
     split is the anomaly, and it is what produced the broken landing signal.

  2. SHARDING — A SILENT 87.5% COVERAGE LOSS. Both sites' code reads
     `os.environ.get('UE_SHARD', '0/8')`, but only `ubereats` declares `shards=8`.
     `dispatch_ephemeral.py:271` spawns a fleet only when `shards > 1`, so Postmates gets ONE
     machine with no `UE_SHARD` set -> the string default `'0/8'` applies -> it processes
     **shard 0 of 8, ~12.5% of the store universe**, every day, and reports success.
     (`ue_catalog.py:995`'s own default is `--shard 0/1` = the whole universe; the registry string
     overrides that sane default with a sharded one.)

     ** FIXED: `postmates` now declares `shards=8`, matching ubereats and matching the '0/8' its
     own code already assumes. ** The migration that introduced this module preserved the bug
     deliberately so the collapse could be proved behaviour-neutral; the fix landed immediately
     after as its own reviewable change. `platform_spec_test.DELIBERATE_DEVIATIONS` records it as
     the one intended difference from the pre-migration golden snapshot — which is why a coverage
     change of this size is a line someone had to write, not a silent diff.

     Operational note: the dispatcher now spawns 8 machines for this source instead of 1 (4GB each,
     daily cadence). `MAX_SPAWN` caps SOURCES per tick, not machines, so the per-tick source budget
     is unchanged.

WHAT THIS BUYS. A per-site difference must be written down as an override, so it can be reviewed.
The dependency graph is derived rather than hand-typed. Adding a third Uber-family site is one
line, not five copied entries that will drift the same way.

-------------------------------------------------------------------------------------------------
THE RATIONALE BELOW MOVED HERE VERBATIM FROM source_registry.py when these entries collapsed into
this file. It is hard-won and expensive to re-derive; it travels with the entries it explains.
-------------------------------------------------------------------------------------------------

THE UBEREATS SOURCE. Headless, list-driven, sharded — replaces the headful zone crawler that ran
max_stores=1000 against a 502,212-store universe (0.2%, with the cap hidden in the registry). Both
the catalog (getStoreV1) and the per-item UPC/detail (getMenuItemV1) answer COLD to plain
curl_cffi — proven live from a Fly datacenter IP — so no browser, no proxy, no Bright Data, $0.
One pass does BOTH layers because the item's section context is only in hand while we hold the
catalog; a second sweep for UPC would repeat the abc-catalog mistake. Sharding is the day budget:
--shard i/N splits the universe by stable hash, one ephemeral machine per shard. Start at 8; the
run logs the observed rate and the shard-hours the universe needs, so the count is set by
measurement rather than guesswork.

SWEEP AND ENRICH ARE DIFFERENT JOBS ON DIFFERENT CLOCKS. The sweep is ONE request per store:
502,212 requests, ~30 minutes across the fleet. Enrichment is one request per NEW item — measured
at ~82 items/store, so inline it turns a 502k-request job into a ~41.7M-request job, and it ran
SERIALLY inside each store's thread (~18.5s/store, matching the observed rate exactly). That is a
30-minute pull wearing a 46-hour coat. They are separable because they answer different questions:
UPC/brand/size/ABV are STATIC per item (fetch once, ever), while price and stock are volatile and
come from the catalog call we already make. So the sweep runs fast and complete on a daily clock,
and enrichment drains the backlog of genuinely-new items continuously — converging, then costing
almost nothing in steady state. `shards` makes the SCHEDULER dispatch the fleet too; without it an
unattended run was one machine. (That last sentence is exactly the Postmates bug named above.)

LADDER_MAX_RUNG=impersonate forbids ladder.py from auto-escalating this recipe to the `browser`
rung. Grounded in the 2026-07-29 incident ladder.py's own docstring documents: UberEats escalated
to `browser` on an isolated (datacenter) Fly machine — a rung only proven on a residential exit —
and 6+ concurrent Chromium instances also exhausted the machine's memory, causing an
SSH-unresponsive stall. Setting the env var in the entry (not just on a hand-run machine) matters
because ladder.current() PERSISTS its rung choice in the warehouse across processes: a fresh
ephemeral dispatch that never sets this cap would read back a previously-persisted `browser` choice
and boot straight into it, silently undoing the fix the moment a normal dispatcher tick spawns a
headless (non-browser-capable) machine. All three catalog/enrich entries share the same cold
getstore.py fetch path, so all three need the cap.

session_budget: requests one primed cookie may serve before re-priming. Measured ~50 on this source
(collapse tracked request COUNT, not time, across three runs); 40 leaves margin, and sessions.py
corrects it from observed burns. Session lifecycle is a per-DOMAIN property like the parser and the
rate policy, so it belongs in the playbook, not hard-coded in a fetcher.

impersonate: measured 2026-07-29 — this target blocks the desktop-Chrome TLS family specifically
(chrome/chrome124/chrome131 all challenged) while safari/firefox/edge/android all returned real
catalogs on the same IPs at the same moment. The costume is a per-domain property, like the parser.

THE FULL CRAWL (ue_crawl.py: getStoreV1+getMenuItemV1, full per-item UPC/price/recipe) is bounded
to 5 major metros + capped stores/items so ONE run finishes in hours, not the multi-day national
sweep the crawler is capable of. NO proxy: ue_crawl.py was proven from the operator's HOME
residential IP; its own UE_PROXY=1 option routes through resi._session_url — the METERED per-GB
tier — so we never set it. RESI_ISP_ONLY=1 is belt-and-suspenders (hard-forbids per-GB globally
even if something downstream reached for it) — worst case this runs on the bare Fly IP with zero
proxy, which is exactly the open question this run is meant to answer. enabled=False: manual
trigger only, never joins the automatic hourly scan, until a real run proves it's not degraded
(near-zero merchants is the known failure signature of a flagged/foreign exit IP).

THE SHARDS CANNOT MERGE. write_accumulate is read-modify-write with no lock, so eight concurrent
shards silently drop each other's rows (seen live: ubereats_products fluctuating wildly while the
fleet ran). Shards therefore append parts, and the fold is the SINGLE writer that folds them into
the canonical catalog — latest observation per (store_uuid, item_uuid) wins. Without it the parts
accumulate and the catalog never updates, so it is a registered build, not a manual step.

NOTHING-TO-DO IS NOT A FAILURE. Folding ubereats' 451,821 part rows succeeded while postmates
simply had no parts yet, and the build reported `incomplete` — a green job reading as broken
teaches you to ignore the colour, which is the same trust defect as a broken job reading green.
The fold reports the total folded so the run is graded on what it actually did.
"""

SITES = ("ubereats", "postmates")

# Shared by every phase of this platform. Anything a phase or site does differently is stated as an
# override below — that is the whole point.
# NOTE cost_class is NOT here: the sitemap phase carries no cost_class today, and this migration is
# proved behaviour-neutral, so it is declared per phase rather than inherited. (Both phases are in
# fact free; adding it to sitemap is a real — if harmless — change, so it is not made silently.)
_COMMON = dict(caps=["curl_cffi"], klass="headless")

_ZONES = "New York, NY;Los Angeles, CA;Chicago, IL;Miami, FL;Houston, TX"


# ---------------------------------------------------------------------------------------------
# Per-site overrides, gathered so drift is visible in ONE place
# ---------------------------------------------------------------------------------------------
_CATALOG = {
    "ubereats": dict(
        label="Uber Eats store catalog (sharded)",
        priority=10,
        inline_enrich=False,        # -> passes --no-enrich; the separate ubereats-enrich job does it
        shards=8,
        session_budget=40,
        impersonate="safari17_0",
        note="COLD getStoreV1 + getMenuItemV1 over the 502k-store sitemap universe; shardable "
             "(UE_SHARD=i/N), resumable, no caps. Headful ubereats.py archived as the zone crawler."),
    "postmates": dict(
        label="Postmates (catalog + UPC, sharded)",
        priority=11,
        inline_enrich=True,         # correct shape per PIPELINE-DESIGN §4 — no postmates-enrich exists
        # FIXED (was absent). Without `shards` the dispatcher spawned ONE machine with no UE_SHARD, so
        # the code's own '0/8' default applied and this source covered 1/8 of the universe daily while
        # reporting success. 8 matches ubereats and matches the '0/8' the code already assumes.
        shards=8,
        note="same cold Uber BFF recipe as ubereats, postmates.com domain"),
}

# Enrich exists for UberEats ONLY. Declared as a mapping rather than a per-site loop so the absence
# is explicit: Postmates does this inline (see _CATALOG), it is not an oversight.
# SIZED FROM MEASUREMENT, NOT INSTINCT. Peak RSS of one shard's work-list, measured on the Fly image
# 2026-08-05 (fresh process per reading — ru_maxrss is a process-lifetime high-water mark, so two
# readings in one process report the same number and the second is meaningless):
#
#     shard 0/8   2,366,292 rows -> 6,047 MB
#     shard 0/32    592,042 rows -> 3,349 MB
#
# Those two points fit ~2,449 MB FIXED + ~1.52 KB/row. The fixed part is the DuckDB scan of all
# 3,832 parts, and EVERY shard pays it in full however narrow its slice — so sharding buys the
# per-row term only, and there is a floor no shard count goes below. At 32 shards that is 3,349 MB,
# which is 82% of a 4,096 MB machine: too tight to schedule. 8,192 MB is Fly's hard ceiling
# ("cannot exceed 8192 MiB"), and 3,349 against it is 41% — real headroom.
#
# THE FLOOR GROWS WITH PART COUNT, so this is a stopgap, not a fix. The durable answer is fewer
# parts to scan: stamp rows with landed_at/run_id (see fold.py — the parts carry no timestamp, which
# is also why compaction is currently lossy) and then compact. Until then, do not reduce `mem` here
# without re-measuring; the number that matters is the part count, not the row count.
_ENRICH = {
    "ubereats": dict(
        label="Uber Eats item UPC/GTIN backfill (sharded)",
        priority=11, shards=32, mem=8192,
        note="separate clock from the sweep: static per-item attributes, fetched once ever. "
             "32 shards x 8gb sized from measured peak RSS (~2.4gb fixed scan + ~1.5kb/row)"),
}

_FULL = {
    "ubereats": dict(
        label="Uber Eats — bounded full-detail crawl",
        note="ONE bounded run (5 metros, capped stores/items), NO proxy (RESI_ISP_ONLY=1 forbids "
             "metered spend) — validates the bare Fly IP before any wider run. Manual trigger only."),
    "postmates": dict(
        label="Postmates — bounded full-detail crawl",
        note="Postmates twin of ubereats-full — same bounds, same $0/no-proxy posture, manual "
             "trigger only."),
}

_SITEMAP = {
    "ubereats": dict(
        label="UberEats store universe",
        note="$0 US UberEats universe from its gzipped sitemaps (~285k) → src_outlets (the coverage "
             "book). Canonical UberEats harvester (ubereats_sitemap.py archived). accumulate into "
             "995k src_outlets → 8gb"),
    "postmates": dict(
        label="Postmates store universe",
        note="$0 US Postmates universe from its sitemaps → src_outlets (coverage book)"),
}


# ---------------------------------------------------------------------------------------------
# Phase builders — the code string is DERIVED from parameters, never hand-typed per site
# ---------------------------------------------------------------------------------------------
def _catalog_entry(site):
    o = _CATALOG[site]
    args = "['--site','%s','--shard',os.environ.get('UE_SHARD','0/8')%s]" % (
        site, "" if o["inline_enrich"] else ",'--no-enrich'")
    e = dict(_COMMON, cost_class="free",
             id=site, label=o["label"], cadence="daily", enabled=True,
             code="import os; os.environ['LADDER_MAX_RUNG']='impersonate'; import ue_catalog as m; "
                  "m.main(%s)" % args,
             tables=["%s_products_parts" % site],      # stage 1: appends parts; the fold owns stage 2
             item_col="item_uuid", store_col="store_uuid",
             mem=4096, timeout=21600, priority=o["priority"], note=o["note"])
    if o.get("shards"):
        e["shards"] = o["shards"]
    if o.get("session_budget"):
        e["session_budget"] = o["session_budget"]
    if o.get("impersonate"):
        e["impersonate"] = o["impersonate"]
    return e


def _enrich_entry(site):
    o = _ENRICH[site]
    # DERIVE the fallback shard from the declared count. Hardcoding it is what broke postmates: its
    # string said '0/8' while the entry declared no `shards`, so the scheduler ran ONE machine that
    # believed it was shard 0 of 8 and covered 1/8 of the universe, daily, reporting success. The
    # dispatcher sets UE_SHARD for every fleet member, so this default only applies to a hand-run —
    # but a default that disagrees with the declaration is a lie waiting for the next reader.
    n = int(o["shards"])
    return dict(_COMMON, cost_class="free",
                id="%s-enrich" % site, label=o["label"], cadence="daily", enabled=True,
                code="import os; os.environ['LADDER_MAX_RUNG']='impersonate'; import ue_enrich as m; "
                     "m.main(['--site','%s','--shard',os.environ.get('UE_SHARD','0/%d')])" % (site, n),
                tables=["%s_products_parts" % site],   # writes PARTS — declaring the aggregate is
                shards=n,                              # what made its landing delta always 0
                mem=int(o.get("mem", 4096)), timeout=21600,
                priority=o["priority"], note=o["note"])


def _full_entry(site):
    o = _FULL[site]
    # NOTE: this phase writes the stage-2 AGGREGATE directly (ue_crawl -> ubereats.land ->
    # write_accumulate), which is contract C1's violation and the 33,250 -> 8,798 loss. Recorded
    # here rather than silently corrected: the fix is a pipeline change, not a registry edit.
    return dict(id="%s-full" % site, label=o["label"], cadence="daily", enabled=False,
                code="import os; os.environ['RESI_ISP_ONLY']='1'; import ue_crawl as m; "
                     "m.main(['--zones','%s','--site','%s','--max-stores','60',"
                     "'--max-items-enrich','40'])" % (_ZONES, site),
                cost_class="free", klass="mac", tables=["%s_products" % site],
                mem=8192, timeout=10800, note=o["note"])


def _sitemap_entry(site):
    o = _SITEMAP[site]
    return dict(_COMMON,
                id="%s-sitemap" % site, label=o["label"], cadence="weekly", enabled=True,
                code="import ue_sitemap as m; m.pull('%s'); m.sitemap_to_src_outlets('%s')" % (site, site),
                tables=["%s_sitemap" % site, "src_outlets"],
                mem=8192, timeout=10800, note=o["note"])


def _fold_entry():
    """The stage-1 -> stage-2 promotion, now the INCREMENTAL fold (docs/PIPELINE-DESIGN.md step 3).

    Was `ue_catalog.consolidate`, which read the entire parts history into a Python dict on every
    run and pruned nothing — cost grew with history rather than with new data. `fold.run` reads only
    parts past the watermark, dedupes in DuckDB, and merges per COLUMN (most-recent non-empty) so
    the catalog sweep's price and the enrich pass's UPC no longer overwrite each other.

    NO `after=[...]`. That made the fold wait for an upstream to report `ok`, which failed four ways
    (a failed fold never retried; a source landing under a non-`ok` status never triggered; the list
    was hand-typed and omitted `ubereats-enrich`; builds shared MAX_SPAWN with sources). Contract C4
    says a stage advances on its OWN backlog, and the watermark now makes that backlog a number — so
    this runs on its interval and folds whatever is waiting. With nothing waiting it reports
    `current` at near-zero cost, which is only affordable BECAUSE the fold is incremental.

    NOTHING-TO-DO IS NOT A FAILURE, and neither is it success-with-work: `status` distinguishes
    ok / current / degraded instead of collapsing them, and the run is graded on rows it actually
    folded.
    """
    tables = ", ".join("'%s_products'" % s for s in SITES)
    return dict(id="build-ue-catalog",
                label="UberEats/Postmates catalog fold (parts → catalog, incremental)",
                code=("import json, fold; "
                      "rs=[fold.run(t) for t in (%s)]; "
                      "n=sum(r['rows'] for r in rs); p=sum(r['parts'] for r in rs); "
                      "st='degraded' if any(r['status']=='degraded' for r in rs) "
                      "else ('current' if all(r['status']=='current' for r in rs) else 'ok'); "
                      "print('HOODIE_RESULT '+json.dumps({'status':st,'items_done':n,"
                      "'items_total':n,'note':'%%d parts folded' %% p}))") % tables,
                tables=["%s_products" % s for s in SITES],
                klass="build", interval_h=6, enabled=True, mem=8192,
                note="incremental single-writer fold (fold.py): watermarked, set-based, per-column "
                     "merge. Shards append parts and must never merge (lost updates).")


def expand():
    """{'sources': [...], 'builds': [...]} — the platform's registry entries, derived."""
    sources = []
    for site in SITES:
        sources.append(_catalog_entry(site))
        if site in _ENRICH:
            sources.append(_enrich_entry(site))
        sources.append(_full_entry(site))
        sources.append(_sitemap_entry(site))
    return {"sources": sources, "builds": [_fold_entry()]}
