"""Self-reinforcing scrape recipes — the memory that lets the Source Analyzer
improve with every run instead of re-solving each source from scratch.

The idea (see the RECIPE-STORE roadmap card):
  - Prove a source once → persist its config as a *recipe* keyed by host.
  - Run recipe-first (deterministic, no LLM) when a recipe is proven.
  - Validate every run against the recipe's baseline (row-count / fill / schema).
  - On drift, self-heal (re-analyze) and write the fix back into the recipe.
  - Promote candidate → proven; demote proven → drifting → broken.

This module is PURE: it operates on a `book` dict (host -> recipe) and never
does I/O. The server owns persistence (disk / Tigris via load()/_save_json) and
orchestration (calling analyze()/extract() and the self-heal re-analyze). That
split keeps the logic fully unit-testable — see recipes_test.py.
"""
import re

# ── tuning ──────────────────────────────────────────────────────────────────
DROP_PCT   = 0.40   # a run with >40% fewer rows than baseline is drift
FILL_DROP  = 0.30   # a field whose fill-rate falls >30pts vs baseline is drift
MIN_CLEAN  = 2      # clean runs needed to promote candidate -> proven
RUN_HIST   = 20     # per-recipe run history kept

STATUSES = ("candidate", "proven", "drifting", "broken")
_EMPTY = ("", "none", "null", "nan", "-", "—")


def host_of(url):
    """Normalize a URL to a recipe key: the registrable host, sans leading www."""
    u = (url or "").strip()
    m = re.match(r"^[a-z]+://([^/:?#]+)", u, re.I)
    host = (m.group(1) if m else u).lower()
    return re.sub(r"^www\.", "", host)


def platform_of(analysis):
    """Best-effort platform tag so one proven store can hint every store on the
    same platform. Read from the resolved data_api url + source_type."""
    a = analysis or {}
    api = a.get("data_api") or {}
    u = api.get("url", "") if isinstance(api, dict) else ""
    blob = (str(u) + " " + str(a.get("source_type") or "")).lower()
    for name, pat in (("searchspring", r"searchspring"),
                      ("algolia",      r"algolia"),
                      ("shopify",      r"shopify|/products\.json"),
                      ("bigcommerce",  r"bigcommerce"),
                      ("woocommerce",  r"woocommerce|wp-json"),
                      ("nextjs",       r"/_next/|__next")):
        if re.search(pat, blob):
            return name
    st = re.sub(r"[^a-z0-9]+", "-", str(a.get("source_type") or "").lower()).strip("-")
    return st or "generic"


def _config(analysis):
    """The deterministic, reusable slice of an analysis — what a future run needs."""
    a = analysis or {}
    return {
        "data_api":      a.get("data_api"),
        "scrape_prompt": a.get("scrape_prompt") or "",
        "fields":        [f.get("name") for f in (a.get("available_fields") or [])
                          if isinstance(f, dict) and f.get("name")],
        "pagination":    a.get("pagination"),
        "store_level":   a.get("store_level"),
        "filters":       a.get("filters"),
    }


def _new(host, ts, platform=None, config=None):
    return {"key": host, "host": host, "platform": platform, "config": config or {},
            "status": "candidate", "clean_runs": 0, "baseline": None, "runs": [],
            "created_at": ts, "last_run": None, "last_verified": None}


def _signal(result):
    """Extract the validation signal from an extract() result: row count, the
    columns present, and per-column fill-rate over a sample."""
    rows = (result.get("rows") if isinstance(result, dict) else None) or []
    sample = [r for r in rows[:50] if isinstance(r, dict)]
    cols = sorted(set().union(*[set(r.keys()) for r in sample])) if sample else []
    fill = {}
    if sample:
        for c in cols:
            nonempty = sum(1 for r in sample
                           if str(r.get(c, "")).strip().lower() not in _EMPTY)
            fill[c] = round(nonempty / len(sample), 3)
    ok = isinstance(result, dict) and "error" not in result
    return {"rows": len(rows), "cols": cols, "fill": fill,
            "engine": (result.get("engine") if isinstance(result, dict) else None), "ok": ok}


def validate(recipe, sig):
    """Compare a run's signal against the recipe baseline. Returns
    {status: ok|baseline|drift|broken|empty, reason}."""
    base = recipe.get("baseline")
    if not sig["ok"]:
        return {"status": "broken", "reason": "fetch / extract failed"}
    if sig["rows"] == 0:
        if base and base.get("rows", 0) > 0:
            return {"status": "broken", "reason": "zero rows (baseline had %d)" % base["rows"]}
        return {"status": "empty", "reason": "no rows and no baseline yet"}
    if not base:
        return {"status": "baseline", "reason": "first clean run — baseline set (%d rows)" % sig["rows"]}
    br = base.get("rows", 0)
    if br > 0 and sig["rows"] < br * (1 - DROP_PCT):
        return {"status": "drift",
                "reason": "rows %d vs baseline %d (down %d%%)" % (sig["rows"], br, round(100 * (1 - sig["rows"] / br)))}
    for c, fv in (base.get("fill") or {}).items():
        nv = sig["fill"].get(c)
        if nv is not None and (fv - nv) > FILL_DROP:
            return {"status": "drift", "reason": "field '%s' fill %d%% vs %d%%" % (c, round(nv * 100), round(fv * 100))}
    bcols = set(base.get("cols") or [])
    if bcols:
        missing = bcols - set(sig["cols"])
        if len(missing) > max(1, len(bcols) // 2):
            return {"status": "drift", "reason": "missing columns: " + ", ".join(sorted(missing))[:80]}
    return {"status": "ok", "reason": "matches baseline (%d rows)" % sig["rows"]}


def record_run(book, url, result, ts):
    """Fold one extract() result into the book: validate, update baseline/status,
    promote/demote. Returns (book, verdict). verdict.needs_heal signals the caller
    to re-analyze and apply_heal()."""
    h = host_of(url)
    rec = book.get(h) or _new(h, ts)
    book[h] = rec
    sig = _signal(result)
    v = validate(rec, sig)
    rec["last_run"] = ts
    rec["runs"] = (rec.get("runs") or [])[-(RUN_HIST - 1):] + [
        {"ts": ts, "rows": sig["rows"], "engine": sig["engine"], "status": v["status"], "reason": v["reason"]}]
    promoted = False
    if v["status"] in ("ok", "baseline"):
        rec["baseline"] = {"rows": sig["rows"], "cols": sig["cols"], "fill": sig["fill"]}
        rec["last_verified"] = ts
        rec["clean_runs"] = rec.get("clean_runs", 0) + 1
        if rec["status"] in ("candidate", "drifting") and rec["clean_runs"] >= MIN_CLEAN:
            rec["status"] = "proven"; promoted = True
        elif rec["status"] == "broken":
            rec["status"] = "candidate"          # recovered — re-proving
    else:
        rec["clean_runs"] = 0
        rec["status"] = "broken" if v["status"] == "broken" else \
                        ("drifting" if v["status"] == "drift" else rec["status"])
    return book, {"host": h, "platform": rec.get("platform"), "status": rec["status"],
                  "run": v["status"], "reason": v["reason"], "rows": sig["rows"],
                  "clean_runs": rec["clean_runs"], "promoted": promoted,
                  "needs_heal": v["status"] in ("drift", "broken")}


def save_config(book, url, analysis, ts):
    """Stash the config from a fresh analyze() as a candidate recipe. A proven
    recipe keeps its stable config (only self-heal overwrites it), but always
    refreshes the platform tag."""
    h = host_of(url)
    cfg, plat = _config(analysis), platform_of(analysis)
    rec = book.get(h)
    if not rec:
        book[h] = _new(h, ts, plat, cfg)
    else:
        rec["platform"] = plat or rec.get("platform")
        if rec.get("status") != "proven":
            rec["config"] = cfg
    return book


def apply_heal(book, url, analysis, ts):
    """Self-heal write-back: overwrite the recipe's config from a fresh analysis
    and reset it to candidate so it must re-prove."""
    h = host_of(url)
    rec = book.get(h)
    if not rec:
        return save_config(book, url, analysis, ts)
    rec["config"] = _config(analysis)
    rec["platform"] = platform_of(analysis) or rec.get("platform")
    rec["status"] = "candidate"
    rec["clean_runs"] = 0
    rec["healed_at"] = ts
    return book


def get(book, url):
    return (book or {}).get(host_of(url))


def by_platform(book, platform, exclude_host=None, status="proven"):
    """Every recipe on a given platform (optionally filtered to a status) —
    the basis for reuse: one proven SearchSpring/BigCommerce store's config is a
    strong prior for the next store on the same platform."""
    if not platform:
        return []
    return [r for r in (book or {}).values()
            if r.get("platform") == platform and r.get("host") != exclude_host
            and (status is None or r.get("status") == status)]


def platform_proven_on(book, url, analysis):
    """Hosts where THIS url's platform is already proven — so the UI can say
    'known platform, proven on X' for a brand-new store."""
    plat = platform_of(analysis)
    if plat in (None, "generic"):
        return []
    return [r["host"] for r in by_platform(book, plat, exclude_host=host_of(url))]


def stats(book):
    vals = list((book or {}).values())
    by = {s: 0 for s in STATUSES}
    for r in vals:
        by[r.get("status", "candidate")] = by.get(r.get("status", "candidate"), 0) + 1
    return {"total": len(vals), "by_status": by,
            "platforms": sorted({r.get("platform") for r in vals if r.get("platform")})}
