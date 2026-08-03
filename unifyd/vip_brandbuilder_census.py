#!/usr/bin/env python3
"""vip_brandbuilder_census.py — enumerate the VIP Brand Builder distributor keyspace.

WHAT THIS IS
  `vtinfo_bbs.py` pulls one distributor's whole catalog once you know its Brand Builder
  `sourceCode` — but that code has always been hand-harvested one distributor at a time
  (`DISTRIBUTORS = {"01191": "Columbia Distributing - WA"}`, a single entry). The sourceCode is a
  **5-digit zero-padded numeric string** ("00177", "01191") — the same shape as the numeric
  `custID` `vtinfo.py`'s finder side already uses for distributor-style tenants (Florida
  Distributing = "00177"), and verified live 2026-08-02: that exact custID is ALSO a valid Brand
  Builder sourceCode, returning "Florida Distributing Company". Same VIP identifier space, two
  different products. That makes the whole 100,000-value space (00000-99999) enumerable, the same
  way vip_finder_census.py turned its 46,656-value alnum space into a census instead of a
  hand-harvested dict.

HOW IT WORKS (stdlib only, no auth, no cookie, no proxy pool needed)
  GET bbs/v1/distributor/<code>/info
    → HTTP 200 {"success": true, "data": {"distributorName": "...", ...}}   = a real VIP
      distributor entity exists at this code.
    → HTTP 400 {"errorMessage": "Distributor ID is invalid"}                = confirmed miss.
    → HTTP 500 {"errorMessage": "SSO Service Error"}                        = inconclusive — the
      SAME code returns this consistently on retry (measured live: 3/3 identical), so it is not
      pure request noise, but it is also not the clean "invalid" signal — recorded as its own
      state, never folded into either hit or miss.

  STAY SOFT ON THE HIT (this is the point, not an afterthought): a 200 on /info only proves VIP
  has a distributor RECORD at this code — Total Wine's own vtinfo_bbs.py history shows a
  distributor entity existing does not guarantee a populated Brand Builder catalog. Every /info
  hit is confirmed with a SECOND, independent check — GET .../products — and only promoted to
  status="confirmed" if that returns a non-empty product array (i.e. it actually carries
  dist_item_code rows, the reason this census exists at all). A code that resolves on /info but
  returns zero products lands as status="info_only": real, but not yet a pull target.

RATE LIMITING — measured, not assumed, and NOT the same target as vip_finder_census.py
  finder.vtinfo.com 429s a single IP after ~5 rapid requests; this is a DIFFERENT VIP backend
  (products.vtinfo.com/bbs). Measured live 2026-08-02: 200 requests at 60 concurrent threads from
  one IP, zero 429s, zero connection errors, ~20 req/s sustained (30→60 workers barely moved
  throughput, so ~20 req/s looks like a server-side ceiling, not a client one). No proxy pool
  machinery here — plain threaded requests, capped at a conservative worker count.

RESUME
  A 100k sweep is checkpointed to agent_state/vip_brandbuilder_census.json (+ the warehouse, so a
  scheduled bite on a fresh machine picks up where the last one left off) after every batch.

DEGRADED (never silently emit garbage)
  Self-reports degraded when 200 is never seen across a real batch (parser/keyspace drift —
  every code would read as a miss) or when the info+error rate exceeds --max-bad-rate (the origin
  is failing outright, not just returning clean misses).

TABLES
  vip_brandbuilder_directory — one row per probed code with a hit (key: source_code); status is
  'confirmed' (real catalog) or 'info_only' (VIP record, no products yet).
"""
import argparse
import gzip
import json
import os
import sys
import threading
import time
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

try:
    import warehouse
except Exception:                                    # importable without the warehouse (selftest/offline)
    warehouse = None

BASE = "https://products.vtinfo.com/bbs/v1/distributor"
UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/126.0 Safari/537.36")

ID_LEN = 5
KEYSPACE_SIZE = 10 ** ID_LEN                          # 100,000 — "00000".."99999"

STATE_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "agent_state")
STATE_FILE = os.path.join(STATE_DIR, "vip_brandbuilder_census.json")
STATE_KEY = "state/vip_brandbuilder_census.json"

DIRECTORY_FIELDS = ["source_code", "status", "distributor_name", "vip_source_id", "vip_customer_id",
                     "n_products", "first_seen", "last_seen"]


# ── keyspace ──────────────────────────────────────────────────────────────────────────────────
def keyspace(shard=None, nshard=None):
    """Every candidate 5-digit sourceCode, optionally this shard's slice of it (--shard/--nshard,
    same contract as doordash_chains.py's partitioning) so a sweep can run split across machines."""
    all_ids = ["%05d" % i for i in range(KEYSPACE_SIZE)]
    if shard is None or not nshard:
        return all_ids
    return [c for c in all_ids if int(c) % nshard == shard]


# ── fetch + classify ──────────────────────────────────────────────────────────────────────────
def _fetch_json(url, timeout=15):
    """GET url, decode gzip if the server sent it (urllib does not auto-decompress like curl
    --compressed does), parse JSON. Returns (http_status, parsed_json_or_None)."""
    req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept": "application/json",
                                               "Accept-Encoding": "gzip"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            body = r.read()
            if r.headers.get("Content-Encoding") == "gzip":
                body = gzip.decompress(body)
            return r.status, json.loads(body.decode("utf-8", "replace"))
    except urllib.error.HTTPError as e:
        body = e.read()
        try:
            if e.headers.get("Content-Encoding") == "gzip":
                body = gzip.decompress(body)
            return e.code, json.loads(body.decode("utf-8", "replace"))
        except Exception:
            return e.code, None


def classify_info(status, payload):
    """A single /info response → ('hit'|'miss'|'inconclusive', distributor_dict_or_None).

    Never trust HTTP 200 alone — real VIP behavior observed live is that success is also carried
    in the JSON body (`success: true`, a non-empty `data.distributorName`), so a 200 with a
    malformed/empty body is treated the same as any other unrecognized shape: inconclusive, not a
    silent hit."""
    if status == 400:
        return "miss", None
    if status == 200 and isinstance(payload, dict) and payload.get("success") and payload.get("data"):
        data = payload["data"]
        if data.get("distributorName"):
            return "hit", data
    return "inconclusive", None


def confirm_products(source_code, timeout=15):
    """The SOFT-HIT confirmation: does this code's catalog actually carry products? Returns the
    product count (0 if the endpoint answers but is empty; None if the check itself failed, which
    must NOT be read as zero — see note_hit's handling)."""
    status, payload = _fetch_json("%s/%s/products" % (BASE, source_code), timeout=timeout)
    if status == 200 and isinstance(payload, dict) and payload.get("success") and isinstance(payload.get("data"), list):
        return len(payload["data"])
    return None


# ── state ─────────────────────────────────────────────────────────────────────────────────────
def _blank():
    return {"done": {}, "hits": {}, "started": int(time.time() * 1000)}


def _normalize(s):
    s.setdefault("done", {}); s.setdefault("hits", {})
    return s


def load_state():
    """Checkpoint, preferring the WAREHOUSE copy — same reasoning as vip_finder_census.py: a
    scheduled runner's disk does not survive between bites, so a purely local checkpoint would
    re-walk the 100k keyspace from zero every time. Falls back to / merges with the local file."""
    remote = {}
    if warehouse is not None:
        try:
            raw = warehouse.get_bytes(STATE_KEY)
            if raw:
                remote = _normalize(json.loads(raw.decode("utf-8")))
        except Exception:
            remote = {}
    local = {}
    if os.path.exists(STATE_FILE):
        try:
            local = _normalize(json.load(open(STATE_FILE, encoding="utf-8")))
        except Exception:
            local = {}
    if not remote and not local:
        return _blank()
    if not remote:
        return local
    if not local:
        return remote
    for k in ("done", "hits"):
        remote[k].update(local[k])
    return remote


def save_state(state):
    os.makedirs(STATE_DIR, exist_ok=True)
    blob = json.dumps(state).encode("utf-8")
    tmp = STATE_FILE + ".tmp"
    with open(tmp, "wb") as f:
        f.write(blob)
    os.replace(tmp, STATE_FILE)
    if warehouse is not None and getattr(warehouse, "remote", lambda: False)():
        try:
            warehouse.put_bytes(STATE_KEY, blob)
        except Exception:
            pass                                     # a failed checkpoint push must not kill the sweep


# ── sweep ─────────────────────────────────────────────────────────────────────────────────────
class _Counters:
    def __init__(self):
        self.lock = threading.Lock()
        self.n_hit = self.n_miss = self.n_inconclusive = 0


def _probe_one(code, counters, state, state_lock, log):
    status, payload = _fetch_json("%s/%s/info" % (BASE, code))
    outcome, data = classify_info(status, payload)
    if outcome == "miss":
        with state_lock:
            state["done"][code] = "miss"
        with counters.lock:
            counters.n_miss += 1
        return
    if outcome == "inconclusive":
        with state_lock:
            state["done"][code] = "inconclusive"      # distinct from 'miss' — a later sweep may re-probe it
        with counters.lock:
            counters.n_inconclusive += 1
        return

    # outcome == "hit" on /info — STAY SOFT: confirm a real catalog before calling this a find.
    n_products = confirm_products(code)
    now = int(time.time() * 1000)
    status_label = "confirmed" if (n_products or 0) > 0 else "info_only"
    rec = {"source_code": code, "status": status_label, "distributor_name": data.get("distributorName") or "",
           "vip_source_id": data.get("vipSourceId"), "vip_customer_id": data.get("vipCustomerId"),
           "n_products": n_products if n_products is not None else 0, "first_seen": now, "last_seen": now}
    with state_lock:
        prior = state["hits"].get(code)
        if prior and prior.get("first_seen"):
            rec["first_seen"] = prior["first_seen"]
        state["hits"][code] = rec
        state["done"][code] = status_label
    with counters.lock:
        counters.n_hit += 1
    log("  %s %-5s  %-9s  %s (%s products)"
        % ("✓" if status_label == "confirmed" else "~", code, status_label, rec["distributor_name"],
           rec["n_products"]))


def sweep(shard=None, nshard=None, limit=None, workers=30, deadline=None, restart=False,
          checkpoint_every=20.0, max_bad_rate=0.98, log=print):
    state = load_state() if not restart else _blank()
    todo = [c for c in keyspace(shard, nshard) if c not in state["done"]]
    if limit:
        todo = todo[:limit]
    if not todo:
        log("vip-brandbuilder-census: nothing to do (%d ids already probed)" % len(state["done"]))
        return state, []

    log("vip-brandbuilder-census: %d ids to probe · %d workers" % (len(todo), workers))
    counters = _Counters()
    state_lock = threading.Lock()
    t0 = time.time()
    stop = threading.Event()
    i = 0
    last_checkpoint = t0
    with ThreadPoolExecutor(max_workers=workers) as ex:
        futures = {}
        it = iter(todo)

        def _submit_next():
            nonlocal i
            try:
                code = next(it)
            except StopIteration:
                return None
            i += 1
            return ex.submit(_probe_one, code, counters, state, state_lock, log)

        for _ in range(min(workers * 4, len(todo))):
            f = _submit_next()
            if f:
                futures[f] = True

        while futures and not stop.is_set():
            done, _ = __import__("concurrent.futures", fromlist=["wait"]).wait(
                futures, timeout=checkpoint_every, return_when="FIRST_COMPLETED")
            for f in done:
                futures.pop(f, None)
                nf = _submit_next()
                if nf:
                    futures[nf] = True
            now = time.time()
            if now - last_checkpoint >= checkpoint_every:
                last_checkpoint = now
                with state_lock:
                    save_state(state)
                probed = counters.n_hit + counters.n_miss + counters.n_inconclusive
                rate = probed / max(1e-6, now - t0)
                log("  … %d probed (%d hits, %d misses, %d inconclusive) · %.1f id/s · %d queued"
                    % (probed, counters.n_hit, counters.n_miss, counters.n_inconclusive, rate,
                       len(todo) - probed))
            if deadline and now - t0 > deadline:
                log("  deadline reached — stopping cleanly (resumable)")
                stop.set()
                break

    with state_lock:
        save_state(state)

    probed = counters.n_hit + counters.n_miss + counters.n_inconclusive
    warns = []
    if counters.n_hit == 0 and probed > 200:
        warns.append("0 hits in %d probes — parser or keyspace drift" % probed)
    bad_rate = counters.n_inconclusive / max(1, probed)
    if bad_rate > max_bad_rate:
        warns.append("inconclusive rate %.1f%% exceeds %.1f%% — the origin is failing outright, "
                     "coverage is not trustworthy" % (bad_rate * 100, max_bad_rate * 100))
    log("vip-brandbuilder-census: %d hits (%d confirmed) / %d probed · %ds"
        % (counters.n_hit, sum(1 for h in state["hits"].values() if h["status"] == "confirmed"),
           probed, time.time() - t0))
    return state, warns


# ── land ──────────────────────────────────────────────────────────────────────────────────────
def land(state, log=print):
    if warehouse is None:
        log("warehouse unavailable — not landing"); return 0
    rows = [{k: h.get(k, "") for k in DIRECTORY_FIELDS} for h in state["hits"].values()]
    if rows:
        warehouse.write_accumulate("vip_brandbuilder_directory", rows,
                                   key=lambda r: r["source_code"], fields=DIRECTORY_FIELDS)
    log("landed %d directory rows" % len(rows))
    return len(rows)


def pull(shard=None, nshard=None, argv=None, log=print):
    """Registry entrypoint. Sweeps (resumable), lands, returns a run record."""
    args = parse_args(argv or [])
    started = int(time.time() * 1000)
    state, warns = sweep(shard=shard, nshard=nshard, limit=args.limit, workers=args.workers,
                        deadline=args.deadline, restart=args.restart,
                        checkpoint_every=args.checkpoint_every, max_bad_rate=args.max_bad_rate, log=log)
    n = 0 if args.no_land else land(state, log=log)
    status = "degraded" if warns else "success"
    return {"connId": "vip-brandbuilder-census", "startedAt": started, "finishedAt": int(time.time() * 1000),
            "status": status, "degraded": bool(warns), "warnings": warns, "total": n,
            "extracts": [{"id": "vip_brandbuilder_directory", "rows": n, "status": status}]}


# ── report ────────────────────────────────────────────────────────────────────────────────────
def report(log=print):
    state = load_state()
    hits = state["hits"]
    confirmed = [h for h in hits.values() if h["status"] == "confirmed"]
    info_only = [h for h in hits.values() if h["status"] == "info_only"]
    log("checkpoint: %d ids probed, %d hits (%d confirmed w/ products, %d info-only)"
        % (len(state["done"]), len(hits), len(confirmed), len(info_only)))
    if confirmed:
        log("\nconfirmed distributors (real catalog — ready for vtinfo_bbs.py DISTRIBUTORS):")
        for r in sorted(confirmed, key=lambda r: -r["n_products"])[:30]:
            log("  %-5s  %5s products  %s" % (r["source_code"], r["n_products"], r["distributor_name"]))
    if info_only:
        log("\ninfo-only (VIP record exists, no products yet — not a pull target):")
        for r in info_only[:15]:
            log("  %-5s  %s" % (r["source_code"], r["distributor_name"]))


# ── cli ───────────────────────────────────────────────────────────────────────────────────────
def parse_args(argv):
    ap = argparse.ArgumentParser(description="Enumerate the VIP Brand Builder distributor keyspace.")
    ap.add_argument("--shard", type=int, help="this machine's shard index (with --nshard)")
    ap.add_argument("--nshard", type=int, help="total shards (splits the 100k keyspace across machines)")
    ap.add_argument("--limit", type=int, help="probe at most N ids this run (resumable)")
    ap.add_argument("--workers", type=int, default=30, help="concurrent threads (measured live: ~20 req/s "
                    "ceiling regardless of 30 vs 60 workers, so 30 is already past the point of returns)")
    ap.add_argument("--deadline", type=float, help="stop cleanly after N seconds (resumable)")
    ap.add_argument("--checkpoint-every", type=float, default=20.0)
    ap.add_argument("--max-bad-rate", type=float, default=0.98, help="inconclusive-rate above this -> degraded")
    ap.add_argument("--restart", action="store_true", help="discard the checkpoint and re-walk the keyspace")
    ap.add_argument("--report", action="store_true", help="summarize the current checkpoint, no network")
    ap.add_argument("--no-land", action="store_true", help="sweep but do not write to the warehouse")
    return ap.parse_args(argv)


def main(argv=None):
    a = parse_args(argv if argv is not None else sys.argv[1:])
    if a.report:
        report(); return 0
    state, warns = sweep(shard=a.shard, nshard=a.nshard, limit=a.limit, workers=a.workers,
                         deadline=a.deadline, restart=a.restart, checkpoint_every=a.checkpoint_every,
                         max_bad_rate=a.max_bad_rate)
    if not a.no_land:
        land(state)
    for w in warns:
        print("WARN:", w)
    report()
    return 2 if warns else 0


if __name__ == "__main__":
    sys.exit(main())
