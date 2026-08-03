#!/usr/bin/env python3
"""metro_deck.py — generate a per-metro market report (HTML) from the warehouse.

One self-contained page per metro, built off metro_analytics: resident alcohol demand, the mapped
account universe, and the NEIGHBOURHOOD breakdown that is the actual story — which ZIPs carry demand
and how thinly they are served.

These pages are written to be shown OUTSIDE the building (press, LinkedIn, prospect conversations),
which sets the honesty bar higher, not lower. Two rules are enforced in the markup itself:

  1. Every figure is tagged LANDED (observed in the warehouse) or DERIVED (modelled). Demand is
     modelled — ACS income distribution x BLS CEX spend per bracket — and never presented as sales.
  2. Account counts are OBSERVED COVERAGE, a floor. The page says so next to the number rather than
     implying a complete licence census, and names the sources behind it.

    python3 tools/metro_deck.py --top 20 --out docs/metros
    python3 tools/metro_deck.py --metro 35620 --out docs/metros
"""
import argparse
import html
import os
import sys
import time

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "unifyd"))
import metro_analytics as ma

CSS = """
html,body{margin:0}
:root{--paper:#F5F7FA;--card:#FFF;--ink:#1A2430;--muted:#5C6775;--faint:#8A94A2;--line:#E3E8EF;
--line2:#EDF1F6;--accent:#4E79A7;--accent-soft:#4E79A71e;--good:#3F8F63;--good-soft:#3F8F6316;
--warn:#B8862E;--warn-soft:#B8862E18;--bad:#C1544A;--bad-soft:#C1544A16;
--shadow:0 1px 2px #1a243008,0 4px 16px #1a243010;
--mono:ui-monospace,"SF Mono",Menlo,Consolas,monospace;
--sans:ui-sans-serif,system-ui,-apple-system,"Segoe UI",Roboto,Helvetica,Arial}
@media (prefers-color-scheme:dark){:root{--paper:#0F141A;--card:#171E27;--ink:#E7ECF3;--muted:#95A1B1;
--faint:#68727F;--line:#26303B;--line2:#1E2731;--accent:#6E97C4;--accent-soft:#6E97C426;
--good:#5AAE7C;--good-soft:#5AAE7c22;--warn:#D0A445;--warn-soft:#d0a44522;--bad:#D96A5F;
--bad-soft:#d96a5f22;--shadow:0 1px 2px #0006,0 6px 20px #0004}}
*{box-sizing:border-box}
body{background:var(--paper);color:var(--ink);font-family:var(--sans);line-height:1.45;
-webkit-font-smoothing:antialiased}
.wrap{max-width:1120px;margin:0 auto;padding:22px 20px 68px}
.top{display:flex;align-items:center;gap:12px;flex-wrap:wrap}
.logo{width:30px;height:30px;border-radius:8px;background:linear-gradient(135deg,var(--accent),#2f5e8e);
display:grid;place-items:center;color:#fff;font-weight:800;font-size:15px}
h1{font-size:16px;font-weight:750;letter-spacing:-.2px;margin:0}
h1 .sub{font-weight:400;color:var(--faint);font-size:12px;margin-left:8px}
.spacer{flex:1}
.livebadge{font-family:var(--mono);font-size:10px;font-weight:700;color:var(--good);
background:var(--good-soft);border:1px solid color-mix(in srgb,var(--good) 40%,var(--line));
border-radius:20px;padding:4px 10px;display:flex;align-items:center;gap:6px}
.livebadge .d{width:7px;height:7px;border-radius:50%;background:var(--good)}
.verdict{display:flex;gap:12px;align-items:flex-start;background:var(--accent-soft);
border:1px solid var(--accent);border-radius:13px;padding:14px 16px;margin:16px 0}
.verdict .vi{flex:none;width:34px;height:34px;border-radius:9px;background:var(--accent);color:#fff;
display:grid;place-items:center;font-size:17px;font-weight:800}
.verdict b{font-weight:700;font-size:14px}
.verdict div.t{font-size:12.5px;color:var(--muted);margin-top:3px;line-height:1.55}
.grid{display:grid;grid-template-columns:repeat(2,1fr);gap:13px}
@media(max-width:760px){.grid{grid-template-columns:1fr}}
.panel{background:var(--card);border:1px solid var(--line);border-radius:13px;padding:15px 16px;
box-shadow:var(--shadow);min-width:0}
.panel.wide{grid-column:1/-1}
.ph{display:flex;align-items:center;gap:9px;margin-bottom:12px}
.ph h3{font-size:13.5px;margin:0;font-weight:700;letter-spacing:-.1px}
.ph .bk{font-size:9.5px;color:var(--faint);text-transform:uppercase;letter-spacing:.06em;font-weight:600}
.tag{margin-left:auto;font-family:var(--mono);font-size:9.5px;font-weight:700;text-transform:uppercase;
letter-spacing:.04em;padding:3px 9px;border-radius:20px;display:flex;align-items:center;gap:5px}
.tag .d{width:7px;height:7px;border-radius:50%}
.tag.landed{background:var(--good-soft);color:var(--good)}.tag.landed .d{background:var(--good)}
.tag.derived{background:var(--accent-soft);color:var(--accent)}.tag.derived .d{background:var(--accent)}
.stat{display:flex;gap:18px;flex-wrap:wrap;margin-bottom:4px}
.stat .s b{display:block;font-size:20px;font-weight:750;letter-spacing:-.4px;font-variant-numeric:tabular-nums}
.stat .s span{font-size:10.5px;color:var(--muted);text-transform:uppercase;letter-spacing:.03em;font-weight:600}
.note{margin-top:12px;font-size:12px;color:var(--muted);background:var(--paper);
border:1px dashed var(--line);border-radius:8px;padding:9px 12px;line-height:1.55}
.note.blue{border-style:solid;border-color:color-mix(in srgb,var(--accent) 32%,var(--line));
background:var(--accent-soft)}
.note b{color:var(--ink)}
.src{margin-top:12px;padding-top:10px;border-top:1px dashed color-mix(in srgb,var(--line) 75%,transparent);
font-family:var(--mono);font-size:10px;color:var(--faint)}
.scroll{overflow-x:auto;-webkit-overflow-scrolling:touch}
table.demand{width:100%;border-collapse:collapse;font-size:12.5px;font-variant-numeric:tabular-nums}
table.demand th{font-size:10px;color:var(--faint);text-transform:uppercase;letter-spacing:.05em;
text-align:right;padding:6px 8px;border-bottom:1px solid var(--line);white-space:nowrap}
table.demand th:first-child{text-align:left}
table.demand td{padding:7px 8px;text-align:right;
border-bottom:1px dashed color-mix(in srgb,var(--line) 70%,transparent);white-space:nowrap}
table.demand td:first-child{text-align:left;font-weight:600}
table.demand tr.total td{border-top:1.5px solid var(--line);border-bottom:0;font-weight:700;
background:var(--good-soft)}
.idx{display:inline-flex;align-items:center;gap:6px;justify-content:flex-end}
.idx .track{width:52px;height:7px;background:var(--paper);border:1px solid var(--line2);
border-radius:4px;overflow:hidden;display:inline-block}
.idx .fill{height:100%;background:var(--accent);display:block}
.bars{display:flex;flex-direction:column;gap:6px}
.bar{display:flex;align-items:center;gap:9px;font-size:12px}
.bar .bl{width:84px;color:var(--muted);font-variant-numeric:tabular-nums;overflow:hidden;
text-overflow:ellipsis;white-space:nowrap}
.bar .bt{flex:1;height:11px;background:var(--paper);border:1px solid var(--line2);border-radius:5px;
overflow:hidden}
.bar .bf{display:block;height:100%;background:var(--accent);border-radius:5px}
.bar .bn{width:62px;text-align:right;font-variant-numeric:tabular-nums;font-weight:600}
.chips2{display:flex;flex-wrap:wrap;gap:6px;margin-top:12px}
.c2{font-size:11px;font-family:var(--mono);padding:3px 9px;border-radius:20px;background:var(--paper);
border:1px solid var(--line);color:var(--muted)}
.c2 b{color:var(--ink)}
dl{margin:0;display:grid;grid-template-columns:auto 1fr;gap:8px 14px;font-size:13px}
dl dt{color:var(--muted);font-size:12px}
dl dd{margin:0;text-align:right;font-weight:500;font-variant-numeric:tabular-nums}
.foot{margin-top:22px;font-size:11px;color:var(--faint);text-align:center;line-height:1.7}
.foot code{font-family:var(--mono);color:var(--muted)}
"""


def esc(s):
    return html.escape(str(s if s is not None else ""))


def usd(v):
    v = float(v or 0)
    if v >= 1e9:
        return "$%.2fB" % (v / 1e9)
    if v >= 1e6:
        return "$%.1fM" % (v / 1e6)
    if v >= 1e3:
        return "$%.0fk" % (v / 1e3)
    return "$%.0f" % v


def num(v):
    return "{:,}".format(int(v or 0))


def _idx_cell(v):
    if not v:
        return "<td>—</td>"
    w = max(2, min(100, v / 2.0))
    return ('<td><span class="idx"><span class="track"><span class="fill" style="width:%.0f%%"></span>'
            "</span>%.0f</span></td>" % (w, v))


def build(cbsa, out_dir, log=print):
    s = ma.metro_summary(cbsa)
    if not s:
        log("[deck] %s — no data, skipped" % cbsa)
        return None
    hoods = ma.neighborhoods(cbsa)
    ranked = [h for h in hoods if h.get("demand_per_account")]
    white = sorted(ranked, key=lambda r: -r["demand_per_account"])[:12]
    dense = sorted(ranked, key=lambda r: r["demand_per_account"])[:12]

    short = s["cbsa_name"].split("-")[0].split(",")[0].strip()
    on_pct = 100.0 * s["demand_away"] / s["demand_total"] if s["demand_total"] else 0
    covered = [h for h in hoods if h["accounts"]]

    # Percentages are taken over the accounts whose attributes we actually KNOW, never over the whole
    # book. Dividing a chain/independent split by every mapped account silently treats "we have no
    # flag for this row" as "independent" — which produced a "96% independent" claim for New York off
    # a book that is mostly unflagged DoorDash rows.
    known_chain = s["chain"] + s["independent"]
    indie_pct = (100.0 * s["independent"] / known_chain) if known_chain else 0
    unknown = s.get("chain_unknown", 0)

    # The headline claim is deliberately the one we can defend hardest: a modelled demand number
    # built from two public federal sources, and a mapped-account count that is observed.
    verdict = (
        "%s residents spend an estimated <b>%s a year</b> on alcohol — %.0f%% of it on-premise. "
        "Against that demand we hold <b>%s accounts mapped to %d ZIP codes</b> in this metro, "
        "%s of which already carry a confirmed beer, wine or spirits signal."
        % (esc(short), usd(s["demand_total"]), on_pct, num(s["accounts"]), s["zips"],
           num(s["alcohol_accounts"])))

    srcs = "".join('<span class="c2"><b>%s</b> %s</span>' % (esc(k), num(v))
                   for k, v in list(s["sources"].items())[:8])

    top_rows = ""
    for h in hoods[:25]:
        top_rows += (
            "<tr><td>%s</td><td>%s</td><td>%s</td><td>%s</td><td>%s</td>%s</tr>"
            % (esc(h["zcta"]), num(h["households"]), usd(h["demand_total"]),
               usd(h["demand_at_home"]), num(h["accounts"]),
               _idx_cell(h.get("index_vs_metro"))))

    def bars(rows, hi=True):
        if not rows:
            return '<div class="note">Not enough resolved accounts in this metro to rank neighbourhoods.</div>'
        mx = max(r["demand_per_account"] for r in rows) or 1
        out = '<div class="bars">'
        for r in rows:
            w = 100.0 * r["demand_per_account"] / mx
            out += ('<div class="bar"><span class="bl">%s</span><span class="bt">'
                    '<span class="bf" style="width:%.0f%%%s"></span></span>'
                    '<span class="bn">%s</span></div>'
                    % (esc(r["zcta"]), w, ";background:var(--good)" if hi else "",
                       usd(r["demand_per_account"])))
        return out + "</div>"

    ts = time.strftime("%Y-%m-%d", time.gmtime())
    doc = """<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<meta name="robots" content="noindex,nofollow">
<title>Hoodie Intelligence · %(short)s Market Report</title><style>%(css)s</style></head><body>
<div class="wrap">
 <div class="top"><div class="logo">H</div>
  <h1>Hoodie Intelligence · %(name)s<span class="sub">resident demand · account universe · neighbourhood coverage</span></h1>
  <div class="spacer"></div><div class="livebadge"><span class="d"></span>LIVE · WAREHOUSE DATA</div></div>

 <div class="verdict"><div class="vi">◆</div><div><b>The market in one line</b>
  <div class="t">%(verdict)s</div></div></div>

 <div class="grid">
  <div class="panel">
   <div class="ph"><h3>Resident alcohol demand</h3><span class="bk">BLS CEX × ACS</span>
    <span class="tag derived"><span class="d"></span>Derived</span></div>
   <div class="stat">
    <div class="s"><b>%(demand)s</b><span>per year</span></div>
    <div class="s"><b>%(hh)s</b><span>households</span></div>
    <div class="s"><b>$%(perhh).0f</b><span>per household</span></div>
   </div>
   <dl style="margin-top:14px">
    <dt>On-premise (bars, restaurants)</dt><dd>%(away)s</dd>
    <dt>Off-premise (retail)</dt><dd>%(home)s</dd>
    <dt>Counties in metro</dt><dd>%(counties)d</dd>
   </dl>
   <div class="note">Built bracket-by-bracket from each county's income distribution (ACS 2022)
    × national mean spend per income bracket (BLS CEX 2023). A modelled estimate of resident
    spending — <b>not</b> a sales feed.</div>
  </div>

  <div class="panel">
   <div class="ph"><h3>Account universe</h3><span class="bk">observed coverage</span>
    <span class="tag landed"><span class="d"></span>Landed</span></div>
   <div class="stat">
    <div class="s"><b>%(accounts)s</b><span>accounts mapped</span></div>
    <div class="s"><b>%(alc)s</b><span>alcohol-flagged</span></div>
    <div class="s"><b>%(zips)d</b><span>ZIP codes</span></div>
   </div>
   <dl style="margin-top:14px">
    <dt>Independent <span style="color:var(--faint)">(of classified)</span></dt><dd>%(indie)s (%(indiepct).0f%%)</dd>
    <dt>Chain <span style="color:var(--faint)">(of classified)</span></dt><dd>%(chain)s</dd>
    <dt>Not yet classified</dt><dd>%(unknown)s</dd>
    <dt>ZIPs with at least one account</dt><dd>%(covzips)d</dd>
   </dl>
   <div class="chips2">%(srcs)s</div>
   <div class="note"><b>This is a floor, not a census.</b> These are the accounts we hold and have
    resolved to this metro by point-in-polygon — coverage varies by source, so read it as observed
    reach rather than every licensed door in the market. The chain/independent split is taken over
    the <b>%(known)s accounts carrying a chain flag</b>, not over the whole book; the rest are
    reported as unclassified rather than assumed.</div>
  </div>

  <div class="panel wide">
   <div class="ph"><h3>Neighbourhood breakdown — top 25 ZIPs by resident demand</h3>
    <span class="bk">ZCTA grain</span>
    <span class="tag derived"><span class="d"></span>Demand derived · accounts landed</span></div>
   <div class="scroll"><table class="demand">
    <tr><th>ZIP</th><th>Households</th><th>Demand / yr</th><th>Off-premise</th><th>Accounts</th>
     <th>Demand per door<br>vs metro median</th></tr>
    %(toprows)s
   </table></div>
   <div class="note">Index compares each ZIP's demand-per-mapped-account against this metro's own
    <b>median</b>, not its mean — a handful of ZIPs with a single account and large demand would drag
    a mean upward and make ordinary neighbourhoods look under-served. Above 100 = more resident
    dollars per door we can see than the typical neighbourhood here.</div>
   <div class="src">outlet_geography (TIGER 2023 point-in-polygon) ⋈ trade_area_demand · geo_level=zcta</div>
  </div>

  <div class="panel">
   <div class="ph"><h3>Thinnest coverage</h3><span class="bk">most demand per door</span>
    <span class="tag derived"><span class="d"></span>Derived</span></div>
   %(white)s
   <div class="note">Highest resident demand per mapped account. Read as <b>where to look next</b> —
    either genuine white space, or a neighbourhood our collection under-covers. Both are actionable;
    they are not the same claim.</div>
  </div>

  <div class="panel">
   <div class="ph"><h3>Densest coverage</h3><span class="bk">least demand per door</span>
    <span class="tag derived"><span class="d"></span>Derived</span></div>
   %(dense)s
   <div class="note">The most heavily-served neighbourhoods in the metro — most competition per
    resident dollar.</div>
  </div>
 </div>

 <div class="foot">
  Hoodie Intelligence · %(name)s · generated %(ts)s<br>
  Every figure computed from the warehouse. <code>demand</code> = ACS 2022 household income
  distribution × BLS CEX 2023 mean spend per bracket (derived).
  <code>accounts</code> = distinct outlets resolved to this metro via Census TIGER 2023
  point-in-polygon (observed).<br>
  Account counts are observed coverage and are a floor, not a licensed-premises census.
 </div>
</div></body></html>""" % {
        "css": CSS, "short": esc(short), "name": esc(s["cbsa_name"]), "verdict": verdict,
        "demand": usd(s["demand_total"]), "hh": num(s["households"]), "perhh": s["demand_per_hh"],
        "away": usd(s["demand_away"]), "home": usd(s["demand_at_home"]), "counties": s["counties"],
        "accounts": num(s["accounts"]), "alc": num(s["alcohol_accounts"]), "zips": s["zips"],
        "indie": num(s["independent"]), "indiepct": indie_pct, "chain": num(s["chain"]),
        "unknown": num(unknown), "known": num(known_chain),
        "covzips": len(covered), "srcs": srcs, "toprows": top_rows,
        "white": bars(white, hi=True), "dense": bars(dense, hi=False), "ts": ts,
    }

    os.makedirs(out_dir, exist_ok=True)
    slug = "".join(c if c.isalnum() else "-" for c in short.lower()).strip("-")
    path = os.path.join(out_dir, "%s.html" % slug)
    with open(path, "w") as f:
        f.write(doc)
    log("[deck] %-42s %8s accounts  %10s demand  %3d ZIPs -> %s"
        % (short, num(s["accounts"]), usd(s["demand_total"]), s["zips"], path))
    return {"path": path, "summary": s, "slug": slug}


def build_index(built, out_dir):
    rows = ""
    for b in built:
        s = b["summary"]
        rows += ('<tr><td><a href="%s.html">%s</a></td><td>%s</td><td>%s</td><td>%s</td><td>%s</td></tr>'
                 % (esc(b["slug"]), esc(s["cbsa_name"]), num(s["households"]), usd(s["demand_total"]),
                    num(s["accounts"]), num(s["alcohol_accounts"])))
    doc = """<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<meta name="robots" content="noindex,nofollow">
<title>Hoodie Intelligence · Metro Market Reports</title><style>%s</style></head><body><div class="wrap">
<div class="top"><div class="logo">H</div><h1>Hoodie Intelligence · Metro Market Reports
<span class="sub">%d markets · resident demand vs mapped account coverage</span></h1></div>
<div class="panel wide" style="margin-top:16px">
<div class="scroll"><table class="demand">
<tr><th>Metro</th><th>Households</th><th>Demand / yr</th><th>Accounts mapped</th><th>Alcohol-flagged</th></tr>
%s</table></div></div>
<div class="foot">Generated %s · demand derived (ACS × BLS CEX) · accounts observed (TIGER point-in-polygon)</div>
</div></body></html>""" % (CSS, len(built), rows, time.strftime("%Y-%m-%d", time.gmtime()))
    p = os.path.join(out_dir, "index.html")
    with open(p, "w") as f:
        f.write(doc)
    return p


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--top", type=int, default=20)
    ap.add_argument("--metro", default=None)
    ap.add_argument("--out", default="docs/metros")
    a = ap.parse_args()

    targets = [a.metro] if a.metro else [m["cbsa_code"] for m in ma.top_metros(a.top)]
    built = []
    for c in targets:
        r = build(c, a.out)
        if r:
            built.append(r)
    if len(built) > 1:
        print("[deck] index -> %s" % build_index(built, a.out))
    print("[deck] %d reports written to %s" % (len(built), a.out))
