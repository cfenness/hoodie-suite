"""ttb_enrich.py — enrich the TTB COLA index with per-record detail + label images + UPC.

The index scrape (ttb_cola_scraper.py) captures the search-results spine. The rich fields
and the label ARTWORK live on each COLA's detail pages — one-to-a-few fetches PER RECORD —
so this is a separate, resumable pass over a SCOPED slice of the index (per-record cost makes
"all 23 years" a multi-day/week job; scope by --since year and optional --classes).

Validated live against the current (USWDS) TTB site:
  fields  <- viewColaDetails.do?action=publicDisplaySearchBasic&ttbid=<id>   (<strong>Label:</strong> value)
  labels  <- viewColaDetails.do?action=publicFormDisplay&ttbid=<id>          (publicViewAttachment.do?filename=..&filetype=l)
  UPC     <- barcode-decode a downloaded label (ttb_cola_labels.extract_upc_from_label)

For each in-scope TTB ID it appends an enriched row to --out (resumable — done IDs are skipped),
and with --labels saves each label image under --labels-dir/<ttbid>__<name>.

Run on the Mac AFTER the index finishes, e.g.:
    python unifyd/ttb_enrich.py --index ~/hoodie-cola/cola_labels.csv --since 2022 \
        --out ~/hoodie-cola/cola_enriched.csv --labels --labels-dir ~/hoodie-cola/labels \
        --ocr --resume --delay 0.3
Fields-only (fastest, 1 fetch/record) = drop --labels/--ocr.
"""
import argparse, csv, os, re, sys, time
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import ttb_cola_scraper as cola

VIEW = "https://www.ttbonline.gov/colasonline/viewColaDetails.do"
ATTACH = "https://www.ttbonline.gov/colasonline/publicViewAttachment.do"

# Enriched schema: the detail fields as TTB labels them, + UPC + saved label files.
DETAIL_FIELDS = ["Status", "Vendor Code", "Serial #", "Class/Type Code", "Origin Code",
                 "Brand Name", "Fanciful Name", "Type of Application", "For Sale In",
                 "Total Bottle Capacity", "Wine Vintage", "Formula", "Approval Date",
                 "Qualifications", "Contact Information"]
OUT_HEADER = ["TTB ID"] + DETAIL_FIELDS + ["UPC", "upc_raw", "upc_status",
                                           # GS1 2D carrier (Sunrise 2027) read off the label art. Item-grain
                                           # only: the Digital Link URI + which symbol we read. The instance-grain
                                           # AIs a 2D code can carry (batch/lot, expiry, serial) are per PHYSICAL
                                           # UNIT and are deliberately NOT persisted here — see upc.parse_2d.
                                           "gs1_digital_link", "code_type",
                                           "alcohol_content", "abv", "proof", "label_files"]


def parse_detail_fields(html):
    """<strong>Label:</strong> value — value is the sibling text up to the next <strong>."""
    from bs4 import BeautifulSoup
    soup = BeautifulSoup(html, "html.parser")
    out = {}
    for st in soup.find_all("strong"):
        lab = st.get_text(" ", strip=True).rstrip(":").strip()
        if not lab or len(lab) > 40:
            continue
        parts = []
        for sib in st.next_siblings:
            if getattr(sib, "name", None) == "strong":
                break
            t = sib.get_text(" ", strip=True) if hasattr(sib, "get_text") else str(sib).strip()
            if t and "help" not in t.lower():
                parts.append(t)
        val = re.sub(r'^[\s?:]+', '', " ".join(parts)).strip()
        if val:
            out[lab] = val
    return out


LABEL_RE = re.compile(r'publicViewAttachment\.do\?filename=([^&"\']+)&filetype=l', re.I)
def label_filenames(html):
    seen, out = set(), []
    for fn in LABEL_RE.findall(html):
        fn = fn.replace("&amp;", "&").strip()
        if fn and fn not in seen:
            seen.add(fn); out.append(fn)
    return out


# Field #13 "ALCOHOL CONTENT" on the publicFormDisplay page → e.g. "45% ABV", "13.5% Alc/Vol",
# "90 Proof". Same page we fetch for labels, so ABV is ~free during enrichment.
ALC_RE = re.compile(r'class="label"[^>]*>[^<]*ALCOHOL\s*CONTENT[^<]*</div>\s*<div\s+class="data"[^>]*>(.*?)</div>', re.I | re.S)
SPIRIT_RE = re.compile(r'whisk|vodka|\brum\b|\bgin\b|tequila|brandy|cognac|liqueur|spirit|cordial|mezcal|bourbon|scotch|schnapps|\brye\b|absinthe|aquavit', re.I)
def parse_alcohol(html, cls=""):
    """Return {content, abv, proof} from the form's ALCOHOL CONTENT field. Parses whichever unit
    is stated; derives proof=2×abv only for distilled spirits (proof is meaningless for wine/beer)."""
    m = ALC_RE.search(html or "")
    raw = re.sub(r"\s+", " ", (m.group(1) if m else "")).replace("&quot;", '"').replace("&amp;", "&").strip()
    abv = proof = ""
    if raw:
        mp = re.search(r"([\d.]+)\s*%", raw)
        if mp: abv = mp.group(1)
        mpr = re.search(r"([\d.]+)\s*proof", raw, re.I)
        if mpr: proof = mpr.group(1)
        try:
            if abv and not proof and SPIRIT_RE.search(cls or raw): proof = "%g" % (float(abv) * 2)
            if proof and not abv: abv = "%g" % (float(proof) / 2)
        except Exception:
            pass
    return {"content": raw[:80], "abv": abv, "proof": proof}


def year_of(*vals):
    for s in vals:
        s = (s or "").strip()
        m = re.search(r'(19|20)\d{2}', s)
        if m:
            return int(m.group(0))
    return 0


def safe(name):
    return re.sub(r'[^A-Za-z0-9._-]+', '_', name)[:80]


def main():
    ap = argparse.ArgumentParser(description="Enrich the TTB COLA index with detail + labels + UPC.")
    ap.add_argument("--index", required=True, help="cola_labels.csv from the index scrape")
    ap.add_argument("--out", required=True, help="enriched CSV to append to (resumable)")
    ap.add_argument("--since", type=int, default=0, help="keep records with year >= this")
    ap.add_argument("--until", type=int, default=9999)
    ap.add_argument("--classes", default="", help="comma substrings; keep rows whose Class/Type matches any")
    ap.add_argument("--labels", action="store_true", help="download + save label images")
    ap.add_argument("--labels-dir", default="", help="where to save labels (default: <out>_labels)")
    ap.add_argument("--ocr", action="store_true", help="barcode-decode labels -> UPC (implies --labels fetch; needs pyzbar)")
    ap.add_argument("--alcohol", action="store_true", help="also capture ALCOHOL CONTENT (ABV/proof) from the form page")
    ap.add_argument("--resume", action="store_true")
    ap.add_argument("--delay", type=float, default=0.3)
    ap.add_argument("--limit", type=int, default=0, help="stop after N (0 = all)")
    a = ap.parse_args()

    with open(a.index, newline="", encoding="utf-8") as f:
        rows = list(csv.DictReader(f))
    classes = [c.strip().lower() for c in a.classes.split(",") if c.strip()]
    def keep(r):
        y = year_of(r.get("Completed Date"), r.get("Approval Date"))
        if a.since and y and y < a.since: return False
        if y and y > a.until: return False
        if classes and not any(c in (r.get("Class/Type", "").lower()) for c in classes): return False
        return True
    todo = [r for r in rows if keep(r)]
    print("index rows: %d | in scope: %d (since=%s classes=%s)" % (len(rows), len(todo), a.since or "-", classes or "-"))

    done = set()
    out_exists = os.path.exists(a.out)
    if a.resume and out_exists:
        with open(a.out, newline="", encoding="utf-8") as f:
            for r in csv.DictReader(f):
                done.add(r.get("TTB ID", ""))
        print("resume: %d already enriched" % len(done))

    want_labels = a.labels or a.ocr
    need_form = want_labels or a.alcohol     # the form page carries labels AND the ABV/proof field
    ldir = a.labels_dir or (a.out.rsplit(".", 1)[0] + "_labels")
    if want_labels:
        os.makedirs(ldir, exist_ok=True)

    fout = open(a.out, "a", newline="", encoding="utf-8")
    w = csv.writer(fout)
    if not out_exists:
        w.writerow(OUT_HEADER)

    s = cola.make_session()
    s.get(cola.SEARCH_PAGE, timeout=60)          # session cookie
    labels = None
    if a.ocr:
        try:
            import ttb_cola_labels as labels
        except Exception:
            labels = None

    n = 0
    for r in todo:
        tid = (r.get("TTB ID") or "").strip()
        if not tid or tid in done:
            continue
        rec = {"TTB ID": tid}
        # --- detail fields ---
        try:
            html = s.get(VIEW, params={"action": "publicDisplaySearchBasic", "ttbid": tid}, timeout=90).text
            f = parse_detail_fields(html)
            for k in DETAIL_FIELDS:
                rec[k] = f.get(k, "")
        except Exception as e:
            pass
        # --- form page (fetched once) → ABV/proof + labels + UPC ---
        saved, upc_raw, upc_status = [], "", "none"
        digital_link, code_type = "", ""
        alc = {"content": "", "abv": "", "proof": ""}
        if need_form:
            try:
                fhtml = s.get(VIEW, params={"action": "publicFormDisplay", "ttbid": tid}, timeout=90).text
                alc = parse_alcohol(fhtml, rec.get("Class/Type Code", ""))
                for fn in (label_filenames(fhtml) if want_labels else []):
                    try:
                        img = s.get(ATTACH, params={"filename": fn, "filetype": "l"}, timeout=90).content
                    except Exception:
                        continue
                    if not img or img[:3] != b"\xff\xd8\xff":     # JPEG magic
                        continue
                    if a.labels:
                        p = os.path.join(ldir, tid + "__" + safe(fn))
                        try:
                            open(p, "wb").write(img); saved.append(os.path.basename(p))
                        except Exception:
                            pass
                    if labels and upc_status != "valid":
                        try:
                            # decode_any reads 1D *and* 2D (QR / DataMatrix). The UPC promotion rule is
                            # unchanged; we additionally keep the Digital Link + which symbol carried it.
                            d = labels.decode_any(img)
                            raw, st = d["payload"] if d["code_type"] in labels._1D else d["gtin"], d["status"]
                            if raw and (upc_status == "none" or st == "valid"):
                                upc_raw, upc_status = raw, st
                            if d["digital_link"] and not digital_link:
                                digital_link = d["digital_link"]
                            if d["code_type"] and not code_type:
                                code_type = d["code_type"]
                        except Exception:
                            pass
                    time.sleep(a.delay * 0.4)
            except Exception:
                pass
        rec["UPC"] = upc_raw if upc_status == "valid" else ""
        rec["upc_raw"] = upc_raw
        rec["upc_status"] = upc_status
        rec["gs1_digital_link"] = digital_link
        rec["code_type"] = code_type
        rec["alcohol_content"] = alc["content"]
        rec["abv"] = alc["abv"]
        rec["proof"] = alc["proof"]
        rec["label_files"] = ";".join(saved)
        w.writerow([rec.get(h, "") for h in OUT_HEADER]); fout.flush()
        n += 1
        if n % 100 == 0:
            print("  enriched %d/%d …" % (n, len(todo)))
        if a.limit and n >= a.limit:
            break
        time.sleep(a.delay)
    fout.close()
    print("done. enriched %d records -> %s%s" % (n, a.out, (" + labels in " + ldir) if want_labels else ""))


if __name__ == "__main__":
    main()
