#!/usr/bin/env python3
"""vip_brandbuilder_sellsheets.py — read Brand Builder sell sheets for the package/UPC family
the JSON API doesn't expose.

WHY THIS EXISTS
  vtinfo_bbs.py's /products response gives ONE package variant per item (whatever VIP itself
  tracks a dist_item_code for). Verified live 2026-08-03 on a real sell sheet (Abita "30° 90°"):
  the PDF's second page lists the FULL package family for that SKU — Can, Can 6-Pack, 1/4 BBL
  Draft, 1/2 BBL Draft, Can Mother Carton (case) — each with its own real UPC-A barcode. The API
  only ever landed the Can. The other four package/UPC pairs exist ONLY on the sell sheet.

FREE-FIRST, checked before building this: `pdftotext` already extracts the descriptive text and
package NAMES for free (no API cost) — but the barcode digit strings render as part of a
graphic, not as selectable text, so free extraction alone cannot recover the UPCs. `zbarimg`
(barcode decode, genuinely free) was the next tier tried and would be preferred, but it crashed
on every image tested (a broken local install) and isn't present at all on the Fly image (no
poppler-utils, no zbar CLI/bindings). Vision is the fallback that's actually available. Scope
makes this a reasonable spend: 2,427 DISTINCT sell sheets across 616k landed items (median 3
products share one sheet, one shared by 630) — not one call per item.

WHAT IT DOES
  For each distinct sell_sheet URL: send it to Claude natively (PDF via the API's document block,
  PNG/JPG via an image block — no local rendering needed) and force a tool call listing every
  package/UPC pair legible on the sheet. Anything not clearly a UPC-A/EAN digit string is
  dropped, never guessed — same discipline as label_vision.py's "null over guess" rule.

  Lands to `vip_brandbuilder_sellsheet_packages`, keyed (sell_sheet_url, package_name) so a
  re-run doesn't duplicate. Each row carries which product_ids/distributor the sheet came from,
  so it joins back to vip_brandbuilder_items — but this is a SEPARATE table, not a silent merge
  into the API-sourced rows: the sell sheet is a different evidentiary source and gets its own
  provenance, per this repo's append-only/never-clobber rule.

    python vip_brandbuilder_sellsheets.py --csv /path/to/vip_brandbuilder_full_export.csv
"""
import argparse
import base64
import csv
import json
import os
import sys
import time
import urllib.parse
import urllib.request
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

try:
    import warehouse
except Exception:
    warehouse = None

MODEL = os.environ.get("SELLSHEET_VISION_MODEL", "claude-opus-4-8")

_TOOL = {
    "name": "packages",
    "description": "List every distinct package/pack configuration shown on this sell sheet, each with its "
                    "printed UPC/EAN barcode digit string if one is legible.",
    "input_schema": {
        "type": "object",
        "properties": {
            "packages": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "package_name": {"type": "string",
                                         "description": "the pack/format label as printed (e.g. 'Can', "
                                                         "'Can 6 Pack', '1/4 BBL Draft', 'Can Mother Carton')"},
                        "upc": {"type": ["string", "null"],
                               "description": "the printed UPC/EAN digit string for THIS package, digits only, "
                                               "no spaces — null if no barcode/digits are legible for it"},
                    },
                    "required": ["package_name", "upc"],
                },
            },
        },
        "required": ["packages"],
    },
}
_PROMPT = ("This is a distributor sell sheet for one beverage product. List EVERY distinct package/pack "
          "configuration shown (can, bottle, multi-pack, draft keg sizes, case/mother carton, etc.) via the "
          "`packages` tool. For each one, read the printed UPC/EAN digit string beneath its barcode if legible "
          "— digits only, no spaces or dashes. Use null for the upc if no barcode is shown or the digits are "
          "not clearly legible. Never guess a digit you can't read.")

FIELDS = ["sell_sheet_url", "package_name", "upc", "distributor_ids", "product_ids", "n_source_rows",
          "extracted_at"]

_client = None


def _cl():
    global _client
    if _client is None:
        import anthropic
        _client = anthropic.Anthropic()
    return _client


def _safe_url(url):
    """Real sell sheet filenames carry literal spaces/parens ('Crisp Apple_SRS_0_0.pdf',
    'SRS-SAM-BeersOfSummerVP-2025_50352 (1)_c0m69fb2.pdf') — urllib.request rejects a raw space as a
    control character outright (669/2427 sheets failed on exactly this live). quote() with
    safe="/%" re-encodes the space/paren WITHOUT double-encoding sequences already percent-escaped
    elsewhere in the path."""
    parts = urllib.parse.urlsplit(url)
    return urllib.parse.urlunsplit(parts._replace(path=urllib.parse.quote(parts.path, safe="/%")))


def _fetch_bytes(url, timeout=30):
    req = urllib.request.Request(_safe_url(url), headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read(), (r.headers.get("Content-Type") or "")


def _content_block(url):
    """A PDF -> a native `document` block; an image -> an `image` block. Content-Type from the response
    decides, not the URL suffix — the '.pdf'-suffixed vs extensionless URLs both appear in real data."""
    data, ctype = _fetch_bytes(url)
    b64 = base64.b64encode(data).decode()
    if "pdf" in ctype.lower() or url.lower().split("?")[0].endswith(".pdf"):
        return {"type": "document", "source": {"type": "base64", "media_type": "application/pdf", "data": b64}}
    media = "image/png" if (("png" in ctype.lower()) or url.lower().split("?")[0].endswith(".png")) else "image/jpeg"
    return {"type": "image", "source": {"type": "base64", "media_type": media, "data": b64}}


def _clean_upc(u):
    digits = "".join(c for c in str(u or "") if c.isdigit())
    return digits if len(digits) in (8, 12, 13, 14) else None    # UPC-E/UPC-A/EAN-13/GTIN-14 lengths only


def extract(url):
    """One sell sheet -> [{package_name, upc}], upc already digit-cleaned/length-validated. Raises on
    fetch/API failure (caller handles)."""
    msg = _cl().messages.create(
        model=MODEL, max_tokens=1024, tools=[_TOOL], tool_choice={"type": "tool", "name": "packages"},
        messages=[{"role": "user", "content": [_content_block(url), {"type": "text", "text": _PROMPT}]}])
    for b in msg.content:
        if b.type == "tool_use":
            out = []
            for p in (b.input or {}).get("packages") or []:
                name = (p.get("package_name") or "").strip()
                if name:
                    out.append({"package_name": name, "upc": _clean_upc(p.get("upc"))})
            return out
    return []


# ── sheet -> (distributor_ids, product_ids) index, from an already-verified CSV export ──────────
def sheets_from_csv(path):
    """distinct sell_sheet URL -> ({distributor_ids}, {product_ids}) it was seen attached to."""
    idx = defaultdict(lambda: [set(), set(), 0])
    with open(path, newline="", encoding="utf-8") as f:
        for row in csv.DictReader(f):
            u = (row.get("product_sell_sheet") or "").strip()
            if not u:
                continue
            idx[u][0].add(row.get("distributor_id", ""))
            idx[u][1].add(row.get("product_id", ""))
            idx[u][2] += 1
    return idx


def run(csv_path, limit=None, workers=8, log=print):
    idx = sheets_from_csv(csv_path)
    urls = list(idx.keys())
    if limit:
        urls = urls[:limit]
    log("vip-brandbuilder-sellsheets: %d distinct sell sheets to read (workers=%d)" % (len(urls), workers))

    now = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    rows, n_failed, n_empty = [], 0, 0

    def one(url):
        try:
            return url, extract(url)
        except Exception as e:
            return url, ("__error__", str(e)[:150])

    t0 = time.time()
    with ThreadPoolExecutor(max_workers=workers) as ex:
        for i, (url, result) in enumerate(ex.map(one, urls), 1):
            dist_ids, prod_ids, n_src = idx[url]
            if isinstance(result, tuple) and result and result[0] == "__error__":
                n_failed += 1
                log("  FAILED %s: %s" % (url[-60:], result[1]))
                continue
            if not result:
                n_empty += 1
                continue
            for pkg in result:
                rows.append({"sell_sheet_url": url, "package_name": pkg["package_name"], "upc": pkg["upc"] or "",
                            "distributor_ids": "|".join(sorted(d for d in dist_ids if d)),
                            "product_ids": "|".join(sorted(p for p in prod_ids if p)),
                            "n_source_rows": n_src, "extracted_at": now})
            if i % 100 == 0:
                log("  … %d/%d sheets (%d package rows so far, %d failed, %d empty) · %.0fs"
                    % (i, len(urls), len(rows), n_failed, n_empty, time.time() - t0))

    log("vip-brandbuilder-sellsheets: %d sheets read, %d package rows, %d failed, %d had nothing legible · %ds"
        % (len(urls), len(rows), n_failed, n_empty, time.time() - t0))
    return rows


def land(rows, log=print):
    if warehouse is None:
        log("warehouse unavailable — not landing"); return 0
    if not rows:
        return 0
    warehouse.write_accumulate("vip_brandbuilder_sellsheet_packages", rows,
                               key=lambda r: (r["sell_sheet_url"], r["package_name"]), fields=FIELDS)
    log("landed %d rows -> vip_brandbuilder_sellsheet_packages" % len(rows))
    return len(rows)


def main(argv=None):
    ap = argparse.ArgumentParser(description="Extract package/UPC families from Brand Builder sell sheets.")
    ap.add_argument("--csv", required=True, help="the verified full-field export (sheets_from_csv reads "
                    "product_sell_sheet/distributor_id/product_id from it)")
    ap.add_argument("--limit", type=int, help="probe at most N distinct sheets (smoke test)")
    ap.add_argument("--workers", type=int, default=8)
    ap.add_argument("--no-land", action="store_true")
    a = ap.parse_args(argv if argv is not None else sys.argv[1:])
    rows = run(a.csv, limit=a.limit, workers=a.workers)
    if not a.no_land:
        land(rows)
    out_csv = os.path.splitext(a.csv)[0] + "_sellsheet_packages.csv"
    if rows:
        with open(out_csv, "w", newline="", encoding="utf-8") as f:
            w = csv.DictWriter(f, fieldnames=FIELDS)
            w.writeheader()
            w.writerows(rows)
        print("also wrote %s" % out_csv)
    return 0


if __name__ == "__main__":
    sys.exit(main())
