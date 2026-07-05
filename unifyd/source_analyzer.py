"""source_analyzer.py — the generalized scraper.

Instead of hand-building one scraper per source, point this at a URL and Claude reads
the page and returns (a) what structured data it holds, (b) sample rows it can already
see, and (c) the info needed to scrape it going forward (repeating-item selector, per-field
selectors, pagination) plus robots/ToS caveats. The user reviews it, then it can be saved
as a connector.

Fetch: plain request with a browser UA; falls back to Bright Data Web Unlocker for
bot-walled / JS-rendered pages when a key is present. LLM analysis is gated on
ANTHROPIC_API_KEY; without it we return a structural heuristic (tables / JSON-LD) so the
tool still does something useful offline.
"""
import os, re, json, urllib.request, urllib.error

MODEL = os.environ.get("AGENT_LLM_MODEL", "claude-opus-4-8")
UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/124.0 Safari/537.36")
# A fuller browser header set — bare UA alone gets 403'd by some CDNs (Akamai/Cloudflare)
# that pass with realistic Accept/Sec-Fetch headers. identity encoding keeps urllib simple.
_HEADERS = {
    "User-Agent": UA,
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
    "Accept-Encoding": "identity",
    "Sec-Fetch-Dest": "document", "Sec-Fetch-Mode": "navigate",
    "Sec-Fetch-Site": "none", "Sec-Fetch-User": "?1",
    "Upgrade-Insecure-Requests": "1", "Connection": "close",
}
# Markers of a bot-challenge / block page served with a 200 — treat as blocked, not content.
_BLOCK_SIGNS = ("just a moment", "cf-browser-verification", "checking your browser",
                "attention required", "access denied", "request unsuccessful",
                "px-captcha", "perimeterx", "/_incapsula", "incapsula", "distil",
                "bot detection", "are you a robot", "captcha-delivery", "enable javascript to")


def llm_enabled():
    return bool(os.environ.get("ANTHROPIC_API_KEY"))


def _looks_blocked(html):
    if not html:
        return False
    head = html[:4000].lower()
    return len(html) < 1500 and any(s in head for s in _BLOCK_SIGNS)


def fetch_detail(url):
    """(html_or_None, via, attempts) — try a realistic direct request, then Bright Data.
    `attempts` is a per-step trace ({step, status, ok, note}) so a failure surfaces its REAL
    cause (403, timeout, bot wall, Bright-Data-not-configured) instead of a bare 'failed'."""
    attempts = []
    # 1) direct
    try:
        req = urllib.request.Request(url, headers=_HEADERS)
        with urllib.request.urlopen(req, timeout=12) as r:   # fail fast to the Bright Data fallback
            html = r.read().decode("utf-8", "replace")
            code = r.getcode()
        if _looks_blocked(html):
            attempts.append({"step": "direct", "status": code, "ok": False,
                             "note": "bot-challenge page (%d bytes) — escalating to Bright Data" % len(html)})
        elif len(html) > 500:
            attempts.append({"step": "direct", "status": code, "ok": True, "note": "%d bytes" % len(html)})
            return html, "direct", attempts
        else:
            attempts.append({"step": "direct", "status": code, "ok": False,
                             "note": "thin response (%d bytes) — escalating" % len(html)})
    except urllib.error.HTTPError as e:
        attempts.append({"step": "direct", "status": e.code, "ok": False, "note": "HTTP %d %s" % (e.code, e.reason)})
    except Exception as e:
        attempts.append({"step": "direct", "status": None, "ok": False, "note": type(e).__name__ + ": " + str(e)[:140]})
    # 2) Bright Data Web Unlocker
    try:
        import brightdata
        if not brightdata.enabled():
            attempts.append({"step": "bright-data", "status": None, "ok": False,
                             "note": "not configured — set BRIGHTDATA_API_KEY (or run `bdata login`) to unlock bot-walled chains"})
        else:
            html = brightdata.fetch(url, data_format="html", timeout=60)
            if html and len(html) > 500 and not _looks_blocked(html):
                attempts.append({"step": "bright-data", "status": 200, "ok": True,
                                 "note": "unlocked %d bytes (zone %s)" % (len(html), brightdata.zone())})
                return html, "bright-data", attempts
            attempts.append({"step": "bright-data", "status": 200, "ok": False,
                             "note": "returned %d bytes, still blocked/thin" % len(html or "")})
    except urllib.error.HTTPError as e:
        body = ""
        try: body = e.read().decode("utf-8", "replace")[:160]
        except Exception: pass
        attempts.append({"step": "bright-data", "status": e.code, "ok": False,
                         "note": ("HTTP %d %s " % (e.code, e.reason)) + body})
    except Exception as e:
        attempts.append({"step": "bright-data", "status": None, "ok": False, "note": type(e).__name__ + ": " + str(e)[:160]})
    return None, "failed", attempts


def _raw_fetch(url):
    """(html, via) — back-compat shape; the detailed trace is via fetch_detail()."""
    html, via, _ = fetch_detail(url)
    return html, via


_CFG_SIGNALS = ("ld+json", "siteid", "searchspring", "algolia", "__next_data__", "__initial",
                "__preloaded", "__apollo", "window.__", "apikey", "api_key", "\"products\"",
                "'products'", "collection", "catalog", "resultsperpage", "graphql", "/api/",
                "bgfilter", "shopify", "bigcommerce")

# Known integration config values — pulled out explicitly so Claude gets the REAL siteId/keys.
_CFG_PATTERNS = [
    ("searchspring_siteId", r"siteId['\"\s:=]{1,5}['\"]?([a-z0-9]{4,10})\b"),
    ("searchspring_siteId", r"snapui\.searchspring\.io/([a-z0-9]{4,10})"),
    ("searchspring_siteId", r"cdn\.searchspring\.net/[^\"'<> ]*?/([a-z0-9]{5,10})\.js"),
    ("algolia_appId",       r"(?:applicationID|application_id|algoliaAppId|x-algolia-application-id)['\"\s:=]{1,5}['\"]?([A-Z0-9]{8,12})"),
    ("algolia_apiKey",      r"(?:apiKey|searchApiKey|x-algolia-api-key)['\"\s:=]{1,5}['\"]?([a-f0-9]{28,40})"),
    ("platform",            r"(cdn\d*\.bigcommerce\.com|cdn\.shopify\.com|/_next/)"),
]

_CFG_NOISE = {"siteid", "apikey", "appid", "application", "searchapikey", "true", "false",
              "null", "undefined", "function", "default"}

def _config_hints(html):
    out, seen = [], set()
    for label, pat in _CFG_PATTERNS:
        for m in re.finditer(pat, html, re.I):
            v = (m.group(1) or "").strip()
            key = (label, v.lower())
            if v and v.lower() not in _CFG_NOISE and key not in seen:
                seen.add(key); out.append(label + " = " + v)
            if len(out) >= 14:
                break
    return out

_STORE_SIGNALS = ("available_variant_values", "select your store", "select a store", "store-selector",
                  "store_selector", "choose your store", "in stock at", "store_id", "storeid",
                  "pickup at", "your local store", "find a store", "change store", "shop your store")

def _store_hint(html):
    low = (html or "").lower()
    if not any(s in low for s in _STORE_SIGNALS):
        return None
    m = re.search(r"(\d{2,4})\s*(?:stores|locations)", low)
    return {"scoped": True, "count": (int(m.group(1)) if m else None),
            "how": "Per-store availability detected (store picker / store options). Enumerate the stores, then query stock for each.",
            "note": "The catalog lists what the chain CARRIES; in-stock-at-a-store needs a per-store query."}

def _clean(html):
    html = html or ""
    hints = _config_hints(html)
    # Keep inline scripts carrying data/config (siteId, keys, initial state, JSON-LD) + the loader
    # <script src> URLs (siteId often lives in the path) so Claude can build a callable API url.
    keep = []
    for m in re.finditer(r"(?is)<script([^>]*)>(.*?)</script>", html):
        attrs, body = m.group(1) or "", m.group(2) or ""
        probe = (attrs + " " + body[:800]).lower()
        if any(s in probe for s in _CFG_SIGNALS):
            src = re.search(r"""src=['"]([^'"]+)['"]""", attrs, re.I)
            snippet = body.strip()[:4500] or (("[external] " + src.group(1)) if src else "")
            if snippet:
                keep.append(snippet)
        if len(keep) >= 12:
            break
    h = re.sub(r"(?is)<(script|style|noscript|svg|head)[^>]*>.*?</\1>", " ", html)
    h = re.sub(r"(?is)<!--.*?-->", " ", h)
    h = re.sub(r"(?is)<[^>]+>", lambda mm: mm.group(0) if re.match(r"(?i)</?(table|tr|td|th|ul|ol|li|a|h[1-6]|div|span|article|section)\b", mm.group(0)) else " ", h)
    h = re.sub(r"[ \t]+", " ", h)
    h = re.sub(r"\n\s*\n+", "\n", h)
    out = h.strip()[:38000]
    if hints:
        out += "\n\n<<< RESOLVED CONFIG (use these EXACT values in the API url) >>>\n" + "\n".join(hints)
    if keep:
        out += "\n\n<<< PAGE DATA / CONFIG SCRIPTS (real siteId / keys / params here) >>>\n" + ("\n---\n".join(keep))[:14000]
    return out


def _json_rows(text):
    """If text is JSON, dig out the primary array of records (products/results/items/hits/…)."""
    t = (text or "").lstrip()
    if not t or t[0] not in "[{":
        return None
    try:
        obj = json.loads(t)
    except Exception:
        return None
    if isinstance(obj, list):
        d = [x for x in obj if isinstance(x, dict)]
        return d or None
    if isinstance(obj, dict):
        keys = ("products", "results", "items", "hits", "records", "rows", "data", "catalog", "docs")
        # one or two levels deep
        for k in keys:
            v = obj.get(k)
            if isinstance(v, list) and v and isinstance(v[0], dict):
                return v
            if isinstance(v, dict):
                for k2 in keys:
                    vv = v.get(k2)
                    if isinstance(vv, list) and vv and isinstance(vv[0], dict):
                        return vv
        best = None
        for v in obj.values():
            if isinstance(v, list) and v and isinstance(v[0], dict) and (best is None or len(v) > len(best)):
                best = v
        return best
    return None


def _page_url(url, n):
    """Best-effort pagination: bump an existing page-ish param, else append page=n."""
    if n <= 1:
        return url
    m = re.search(r"([?&](?:page|p|pg|pageNumber|page_number)=)(\d+)", url, re.I)
    if m:
        return url[:m.start(2)] + str(n) + url[m.end(2):]
    sep = "&" if "?" in url else "?"
    return url + sep + "page=" + str(n)


def _default_prompt(names):
    fl = ", ".join(names) if names else "every field in the primary dataset"
    return ("Extract every row of the primary dataset from this page's HTML. For each row return an object with "
            "these fields: " + fl + ". Return ONLY a JSON array of objects — no prose. Use null for a missing field.")


def _heuristic(html):
    fields, samples = [], []
    jr = _json_rows(html)
    if jr:
        names = list(jr[0].keys())[:16]
        return {"source_type": "json-api", "summary": "JSON endpoint — parsed the records directly (%d in this response)." % len(jr),
                "available_fields": [{"name": n, "example": str(jr[0].get(n))[:40]} for n in names],
                "sample_rows": [{n: r.get(n) for n in names} for r in jr[:5]],
                "list_container": None, "item_fields": [], "pagination": "append &page=N (Shopify + most APIs)",
                "robots_note": "Check robots.txt + ToS.", "confidence": "heuristic",
                "scrape_prompt": _default_prompt(names),
                "data_api": None}   # the URL already IS the API — extract runs against it (lastUrl) + paginates
    try:
        from bs4 import BeautifulSoup
        soup = BeautifulSoup(html or "", "html.parser")
        # JSON-LD?
        ld = soup.find("script", type="application/ld+json")
        # first sizeable table
        for t in soup.find_all("table"):
            heads = [th.get_text(" ", strip=True) for th in t.find_all("th")][:12]
            rows = t.find_all("tr")
            if heads and len(rows) > 1:
                fields = [{"name": h, "example": ""} for h in heads if h]
                for tr in rows[1:6]:
                    cells = [td.get_text(" ", strip=True) for td in tr.find_all(["td", "th"])]
                    if cells:
                        samples.append(dict(zip([f["name"] for f in fields], cells)))
                names = [f["name"] for f in fields]
                return {"source_type": "html-table", "summary": "Detected an HTML table (structural heuristic — no LLM key).",
                        "available_fields": fields, "sample_rows": samples,
                        "list_container": "table tr", "item_fields": [{"name": f["name"], "selector": "td:nth-child(?)"} for f in fields],
                        "pagination": None, "robots_note": "Check the site's robots.txt + ToS before scraping.",
                        "confidence": "heuristic", "jsonld": bool(ld),
                        "scrape_prompt": _default_prompt(names)}
    except Exception:
        pass
    return {"source_type": "unknown", "summary": "No obvious table/list found by the heuristic. Set ANTHROPIC_API_KEY for Claude to read the page.",
            "available_fields": [], "sample_rows": [], "list_container": None, "item_fields": [],
            "pagination": None, "robots_note": "Check robots.txt + ToS.", "confidence": "heuristic"}


def _balance(s):
    """Close any open strings / brackets in a (possibly truncated) JSON string."""
    stack, instr, esc = [], False, False
    for ch in s:
        if instr:
            if esc: esc = False
            elif ch == "\\": esc = True
            elif ch == '"': instr = False
        elif ch == '"': instr = True
        elif ch in "{[": stack.append(ch)
        elif ch in "}]":
            if stack: stack.pop()
    out = s + ('"' if instr else "")
    for ch in reversed(stack):
        out += "}" if ch == "{" else "]"
    return out


def _loads_lenient(raw):
    """json.loads that salvages a truncated model response: parse the JSON object if
    it's whole, else close the open string/brackets and, if needed, trim back to a
    comma boundary and retry — so a slightly-too-long answer still yields usable
    structure instead of dropping to the heuristic. Returns a dict or None."""
    if not raw:
        return None
    i = raw.find("{")
    if i < 0:
        return None
    s = raw[i:]
    m = re.search(r"\{[\s\S]*\}", s)          # greedy whole object (well-formed case)
    for cand in ([m.group(0)] if m else []) + [s]:
        try:
            v = json.loads(cand)
            if isinstance(v, dict):
                return v
        except Exception:
            pass
    t = s                                      # repair a truncated tail
    for _ in range(8):
        try:
            v = json.loads(_balance(t))
            if isinstance(v, dict):
                return v
        except Exception:
            pass
        cut = t.rfind(",")
        if cut <= 0:
            break
        t = t[:cut]                            # drop the incomplete trailing element
    return None


def analyze(url, goal=None):
    url = (url or "").strip()
    if not re.match(r"^https?://", url):
        return {"error": "give a full http(s) URL"}
    html, via, attempts = fetch_detail(url)
    if not html:
        blocked = any(a["step"] == "bright-data" and not a["ok"] and a.get("status") not in (None,) for a in attempts) \
                  or any("not configured" in a.get("note", "") for a in attempts)
        return {"error": "couldn't fetch the page (blocked or unreachable). A Bright Data key handles bot-walled sites.",
                "via": via, "attempts": attempts, "blocked": blocked}
    if not llm_enabled():
        out = _heuristic(html); out["via"] = via
        out.setdefault("store_level", _store_hint(html))
        return out
    try:
        import anthropic
        client = anthropic.Anthropic()
        sysmsg = ("You are a web-scraping analyst. Given a page's (cleaned, truncated) HTML, identify the "
                  "structured data it holds and how to scrape it going forward. Return ONLY JSON: "
                  '{"source_type":string,"summary":string,'
                  '"available_fields":[{"name":string,"example":string}],'
                  '"sample_rows":[object],'   # up to 5 rows you can already read from the HTML
                  '"list_container":string|null,'   # CSS selector for the repeating item
                  '"item_fields":[{"name":string,"selector":string}],'
                  '"pagination":string|null,"robots_note":string,"confidence":"high"|"medium"|"low",'
                  '"data_api":{"url":string,"method":"GET"|"POST","params":object,"pagination":string,"note":string}|null,'
                  '"store_level":{"scoped":boolean,"count":number|null,"how":string,"note":string}|null,'
                  '"filters":[{"name":string,"param":string,"values":[string],"note":string}]|null,'
                  '"scrape_prompt":string}. '
                  "sample_rows must be real values read from THIS html. Keep it to the primary dataset on the page. "
                  "CRITICAL: if the page's data is loaded client-side from a JSON/XHR endpoint rather than being in the "
                  "static HTML (e.g. SearchSpring, Algolia, a Shopify /products.json, BigCommerce, a GraphQL or /api/ "
                  "endpoint) — infer the FULL data-source API in data_api: the exact request url, method, the key query "
                  "params (siteId, category/collection, resultsPerPage, etc.), and how to page it. The page's real "
                  "siteId / apiKey / collection / params are in the '<<< PAGE DATA / CONFIG SCRIPTS >>>' section — read "
                  "them and put the ACTUAL values into a directly-callable data_api.url — a COMPLETE url with ALL "
                  "required query params, not a bare endpoint. (SearchSpring example: "
                  "https://<siteId>.a.searchspring.io/api/search/search.json?siteId=<siteId>&resultsFormat=native&"
                  "resultsPerPage=100&page=1 plus any category filter like bgfilter.category_id=NN — substitute the real "
                  "siteId from RESOLVED CONFIG.) NEVER return placeholder tokens like <siteId>, {key} or YOUR_SITE_ID — "
                  "if you truly cannot find the real value, set data_api to null. "
                  "Set data_api to null only if the rows are genuinely in the HTML. "
                  "STORE-LEVEL: decide whether product AVAILABILITY / INVENTORY is per physical store — signals: a store "
                  "picker ('select your store', a store/location dropdown), a store/location query param, BigCommerce store "
                  "options / available_variant_values, per-location stock, 'in stock at N stores'. If so, set store_level "
                  "with scoped=true, the store count if visible, HOW to enumerate the stores (endpoint / param / list), and "
                  "HOW to get inventory for one store — the catalog/list usually shows what the chain CARRIES chain-wide, "
                  "while in-stock-at-a-store needs a per-store query. Set store_level.scoped=false (or null) if stock is "
                  "single/chain-wide. "
                  "FILTERS: detect the query params the API supports to NARROW results so a user can pull ONE brand or ONE "
                  "store instead of the whole catalog — brand/supplier, category, and especially store/location. For each "
                  "give the exact param name + a few example values (from the page facets / config if visible). "
                  "PHASING: the base catalog (product list, chain-wide) is one cheap pull; per-store INVENTORY is a "
                  "separate, expensive pass (one query per store) — reflect that in scrape_prompt (catalog first, then "
                  "loop stores for inventory only if asked). "
                  "scrape_prompt is a REUSABLE instruction to extract this source's rows on every future run: if data_api "
                  "is set it must say to fetch that API (and paginate it) and normalize its JSON to the fields; otherwise "
                  "to read the page HTML. Return a JSON array of objects; make it source-specific, not tied to this snapshot.")
        usr = (("GOAL: " + goal + "\n\n") if goal else "") + "PAGE URL: " + url + "\n\nHTML:\n" + _clean(html)
        msg = client.messages.create(model=MODEL, max_tokens=8000, system=sysmsg,
                                     messages=[{"role": "user", "content": usr}])
        raw = "".join(getattr(b, "text", "") for b in msg.content)
        out = _loads_lenient(raw)          # tolerant of a truncated/rough JSON tail
        if out is None:
            raise ValueError("could not parse the model response")
        out["via"] = via
        out.setdefault("confidence", "medium")
        return out
    except Exception as e:
        out = _heuristic(html); out["via"] = via
        out["summary"] = "LLM error (" + str(e)[:90] + ") — fell back to the structural heuristic."
        return out


def _claude_rows(prompt, content, note=""):
    import anthropic
    client = anthropic.Anthropic()
    usr = prompt + note + "\n\nDATA:\n" + content[:150000]
    msg = client.messages.create(model=MODEL, max_tokens=8000,
                                 system="Extract/normalize the data per the user's instructions. Return ONLY a JSON array of objects — no prose, no markdown fences.",
                                 messages=[{"role": "user", "content": usr}])
    raw = "".join(getattr(b, "text", "") for b in msg.content)
    m = re.search(r"\[[\s\S]*\]", raw)
    return json.loads(m.group(0)) if m else []


def extract(url, prompt, pages=1, limit=3000):
    """Run the scrape against a target — the page OR its data API — and page through it.
    JSON endpoints (Shopify /products.json, SearchSpring, Algolia…) are parsed natively so
    a whole catalog comes back even without an LLM; HTML pages go through Claude."""
    url = (url or "").strip()
    prompt = (prompt or "").strip()
    if not re.match(r"^https?://", url):
        return {"error": "give a full http(s) URL"}
    if not prompt:
        return {"error": "no scrape prompt — analyze the source first"}
    try:
        pages = max(1, min(int(pages or 1), 400))   # high ceiling for full-catalog runs; the item cap + empty page still stop it
    except Exception:
        pages = 1
    try:
        limit = max(1, min(int(limit or 3000), 100000))
    except Exception:
        limit = 3000

    json_rows, html_blobs, via, attempts = [], [], None, []
    for n in range(1, pages + 1):
        purl = _page_url(url, n)
        if n == 1:
            body, v, attempts = fetch_detail(purl)   # keep the real trace for the first page
        else:
            body, v = _raw_fetch(purl)
        via = via or v
        if not body:
            break
        jr = _json_rows(body)
        if jr is not None:
            if not jr:            # empty page → end of the catalog
                break
            json_rows.extend(jr)
            if len(json_rows) >= limit:   # hit the cap → stop paging
                break
        else:
            html_blobs.append(_clean(body))
            if pages == 1:
                break

    # JSON-native path (APIs / Shopify) — works with or without a key
    if json_rows:
        if llm_enabled():
            try:
                rows = _claude_rows(prompt, json.dumps(json_rows[:min(limit, 1200)]),
                                    "\n\nThe DATA below is the raw JSON records; normalize each to the requested fields.")
                return {"rows": rows[:limit], "via": via, "engine": "claude+api", "pages": pages, "raw": len(json_rows)}
            except Exception:
                pass
        return {"rows": json_rows[:limit], "via": via, "engine": "api-json", "pages": pages, "raw": len(json_rows)}

    # HTML path
    if not html_blobs:
        return {"error": "couldn't fetch data (blocked or unreachable). If it's a JS store, extract from its data API instead.",
                "via": via, "attempts": attempts}
    if not llm_enabled():
        h = _heuristic("\n".join(html_blobs))
        return {"rows": (h.get("sample_rows") or [])[:limit], "via": via, "engine": "heuristic",
                "note": "No LLM key — returned the table rows the heuristic could read. Set ANTHROPIC_API_KEY (or extract from the data API) for the full run."}
    try:
        rows = _claude_rows(prompt, "\n\n---PAGE---\n\n".join(html_blobs))
        return {"rows": rows[:limit], "via": via, "engine": "claude", "pages": pages}
    except Exception as e:
        return {"error": "extraction failed: " + str(e)[:120], "via": via}
