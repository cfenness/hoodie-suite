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
import os, re, json, urllib.request

MODEL = os.environ.get("AGENT_LLM_MODEL", "claude-opus-4-8")
UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/124.0 Safari/537.36")


def llm_enabled():
    return bool(os.environ.get("ANTHROPIC_API_KEY"))


def _raw_fetch(url):
    """(html, via) — direct request first, Bright Data Web Unlocker on block/failure."""
    try:
        req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept": "text/html"})
        with urllib.request.urlopen(req, timeout=25) as r:
            html = r.read().decode("utf-8", "replace")
            if len(html) > 500:
                return html, "direct"
    except Exception:
        pass
    try:
        import brightdata
        if brightdata.enabled():
            return brightdata.fetch(url, data_format="html", timeout=90), "bright-data"
    except Exception:
        pass
    return None, "failed"


def _clean(html):
    h = re.sub(r"(?is)<(script|style|noscript|svg|head)[^>]*>.*?</\1>", " ", html or "")
    h = re.sub(r"(?is)<!--.*?-->", " ", h)
    h = re.sub(r"(?is)<[^>]+>", lambda m: m.group(0) if re.match(r"(?i)</?(table|tr|td|th|ul|ol|li|a|h[1-6]|div|span|article|section)\b", m.group(0)) else " ", h)
    h = re.sub(r"[ \t]+", " ", h)
    h = re.sub(r"\n\s*\n+", "\n", h)
    return h.strip()[:45000]


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


def analyze(url, goal=None):
    url = (url or "").strip()
    if not re.match(r"^https?://", url):
        return {"error": "give a full http(s) URL"}
    html, via = _raw_fetch(url)
    if not html:
        return {"error": "couldn't fetch the page (blocked or unreachable). A Bright Data key handles bot-walled sites.", "via": via}
    if not llm_enabled():
        out = _heuristic(html); out["via"] = via; return out
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
                  '"scrape_prompt":string}. '
                  "sample_rows must be real values read from THIS html. Keep it to the primary dataset on the page. "
                  "CRITICAL: if the page's data is loaded client-side from a JSON/XHR endpoint rather than being in the "
                  "static HTML (e.g. SearchSpring, Algolia, a Shopify /products.json, BigCommerce, a GraphQL or /api/ "
                  "endpoint) — infer the FULL data-source API in data_api: the exact request url, method, the key query "
                  "params (siteId, category/collection, resultsPerPage, etc.), and how to page it. Reconstruct a real, "
                  "callable url. Set data_api to null only if the rows are genuinely in the HTML. "
                  "scrape_prompt is a REUSABLE instruction to extract this source's rows on every future run: if data_api "
                  "is set it must say to fetch that API (and paginate it) and normalize its JSON to the fields; otherwise "
                  "to read the page HTML. Return a JSON array of objects; make it source-specific, not tied to this snapshot.")
        usr = (("GOAL: " + goal + "\n\n") if goal else "") + "PAGE URL: " + url + "\n\nHTML:\n" + _clean(html)
        msg = client.messages.create(model=MODEL, max_tokens=2000, system=sysmsg,
                                     messages=[{"role": "user", "content": usr}])
        raw = "".join(getattr(b, "text", "") for b in msg.content)
        m = re.search(r"\{[\s\S]*\}", raw)
        out = json.loads(m.group(0)) if m else {"error": "could not parse the model response"}
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
        pages = max(1, min(int(pages or 1), 25))
    except Exception:
        pages = 1
    try:
        limit = max(1, min(int(limit or 3000), 100000))
    except Exception:
        limit = 3000

    json_rows, html_blobs, via = [], [], None
    for n in range(1, pages + 1):
        purl = _page_url(url, n)
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
        return {"error": "couldn't fetch data (blocked or unreachable). If it's a JS store, extract from its data API instead.", "via": via}
    if not llm_enabled():
        h = _heuristic("\n".join(html_blobs))
        return {"rows": (h.get("sample_rows") or [])[:limit], "via": via, "engine": "heuristic",
                "note": "No LLM key — returned the table rows the heuristic could read. Set ANTHROPIC_API_KEY (or extract from the data API) for the full run."}
    try:
        rows = _claude_rows(prompt, "\n\n---PAGE---\n\n".join(html_blobs))
        return {"rows": rows[:limit], "via": via, "engine": "claude", "pages": pages}
    except Exception as e:
        return {"error": "extraction failed: " + str(e)[:120], "via": via}
