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


def _default_prompt(names):
    fl = ", ".join(names) if names else "every field in the primary dataset"
    return ("Extract every row of the primary dataset from this page's HTML. For each row return an object with "
            "these fields: " + fl + ". Return ONLY a JSON array of objects — no prose. Use null for a missing field.")


def _heuristic(html):
    fields, samples = [], []
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
                  '"scrape_prompt":string}. '
                  "sample_rows must be real values read from THIS html. Keep it to the primary dataset on the page. "
                  "scrape_prompt is a REUSABLE instruction you would hand an LLM on every future run to extract this "
                  "source's rows from its page HTML — name the exact fields to pull and say to return a JSON array of "
                  "objects; make it specific to this source but not tied to values from this one snapshot.")
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


def extract(url, prompt, limit=200):
    """Run the generated scrape_prompt against a fresh fetch of the URL — feed it to Claude
    and return the extracted rows. This is the actual scrape: prompt in, JSON rows out."""
    url = (url or "").strip()
    prompt = (prompt or "").strip()
    if not re.match(r"^https?://", url):
        return {"error": "give a full http(s) URL"}
    if not prompt:
        return {"error": "no scrape prompt — analyze the source first"}
    html, via = _raw_fetch(url)
    if not html:
        return {"error": "couldn't fetch the page (blocked or unreachable)", "via": via}
    if not llm_enabled():
        h = _heuristic(html)
        return {"rows": (h.get("sample_rows") or [])[:limit], "via": via, "engine": "heuristic",
                "note": "No LLM key — returned the table rows the heuristic could read (set ANTHROPIC_API_KEY to run the prompt through Claude)."}
    try:
        import anthropic
        client = anthropic.Anthropic()
        sysmsg = ("Extract structured data from the page HTML per the user's instructions. "
                  "Return ONLY a JSON array of objects — no prose, no markdown fences.")
        usr = prompt + "\n\nPAGE URL: " + url + "\n\nHTML:\n" + _clean(html)
        msg = client.messages.create(model=MODEL, max_tokens=4000, system=sysmsg,
                                     messages=[{"role": "user", "content": usr}])
        raw = "".join(getattr(b, "text", "") for b in msg.content)
        m = re.search(r"\[[\s\S]*\]", raw)
        rows = json.loads(m.group(0)) if m else []
        return {"rows": rows[:limit], "via": via, "engine": "claude"}
    except Exception as e:
        return {"error": "extraction failed: " + str(e)[:120], "via": via}
