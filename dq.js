/* dq.js — deterministic data-quality / file-diagnosis engine.
 *
 * Ingests a parsed table (header + rows-of-arrays), assigns each column a ROLE
 * (measure vs dimension) with calibrated confidence, then runs a role-specific battery of
 * checks and emits a structured "DQ read".
 *
 * GOVERNING RULE — every finding is tagged exactly one of:
 *   DETERMINISTIC — a computation that fires or doesn't; rule + threshold inspectable; stated as fact.
 *   INFERENCE     — a heuristic guess; carries a confidence (0..1) and is user-overridable.
 * A heuristic must NEVER present as a deterministic fact. When uncertain, surface LESS certainty.
 *
 * Pure + dependency-free. Works in the browser (window.DQ) and headless (module.exports) for tests.
 */
(function () {
  "use strict";

  var DETERMINISTIC = "DETERMINISTIC", INFERENCE = "INFERENCE";
  var SAMPLE_CAP = 120000;

  function clean(v) { return v == null ? "" : String(v).trim(); }
  function toNum(v) {
    var s = clean(v).replace(/[$,%\s]/g, "");
    if (s === "" || isNaN(s)) return null;
    return parseFloat(s);
  }
  function isIntStr(s) { return /^-?\d+$/.test(s); }
  function pct(a, b) { return b ? a / b : 0; }
  function median(sorted) { if (!sorted.length) return null; var m = Math.floor(sorted.length / 2); return sorted.length % 2 ? sorted[m] : (sorted[m - 1] + sorted[m]) / 2; }

  // ---------------- per-column single pass over the FULL file (exact counts) ----------------
  // The cheap counts (n, missing, zeros, distinct, min/max, negatives) are computed over EVERY
  // row → provenance "full". Distribution work (bimodality) runs on a sample → "sampled".
  function colStats(name, rows, idx, nTotal) {
    var n = nTotal, missing = 0, zeros = 0, negatives = 0;
    var freq = Object.create(null), distinct = 0;
    var nums = [], numericNonblank = 0, nonblank = 0, intCount = 0;
    var min = Infinity, max = -Infinity, fixedWidthHits = 0, firstLen = null, sameLen = true;
    var caseFold = Object.create(null), wsHits = 0;
    for (var r = 0; r < n; r++) {
      var raw = rows[r] && rows[r][idx] !== undefined ? rows[r][idx] : "";
      var s = clean(raw);
      if (s === "") { missing++; continue; }
      nonblank++;
      if (String(raw) !== s) wsHits++;                                  // had surrounding whitespace
      if (freq[s] === undefined) { freq[s] = 0; distinct++; } freq[s]++;
      var lc = s.toLowerCase();
      if (caseFold[lc] === undefined) caseFold[lc] = {}; caseFold[lc][s] = 1;
      if (firstLen === null) firstLen = s.length; else if (s.length !== firstLen) sameLen = false;
      var x = toNum(s);
      if (x !== null) {
        numericNonblank++; nums.push(x);
        if (x === 0) zeros++; if (x < 0) negatives++;
        if (x < min) min = x; if (x > max) max = x;
        if (isIntStr(s.replace(/,/g, ""))) intCount++;
      }
    }
    var numeric = nonblank > 0 && pct(numericNonblank, nonblank) >= 0.9;
    var isInt = numeric && pct(intCount, numericNonblank) >= 0.95;
    // case/whitespace fragmentation: a normalized value with >1 distinct raw forms
    var caseFrag = 0;
    for (var k in caseFold) { var forms = Object.keys(caseFold[k]); if (forms.length > 1) caseFrag++; }
    return {
      name: name, idx: idx, n: n, nonblank: nonblank, missing: missing,
      null_rate: +pct(missing, n).toFixed(4),
      n_distinct: distinct, distinct_rate: +pct(distinct, nonblank).toFixed(4),
      numeric: numeric, isInt: isInt, zeros: zeros, negatives: negatives,
      min: numeric ? min : null, max: numeric ? max : null,
      nums: nums, freq: freq, fixedWidth: nonblank > 1 && sameLen, caseFrag: caseFrag, wsHits: wsHits
    };
  }

  // ---------------- Part A — ROLE ASSIGNMENT (an INFERENCE, always) ----------------
  var RE_ID    = /(_id|_key|_code|_no|_uuid|_guid)$|^id$|^uuid$|sku|upc|gtin|barcode|isbn/i;
  var RE_NUMID = /(_id|number|_no|_key)$/i;            // "...number" etc.
  var RE_ZIP   = /(zip|postal)/i;
  var RE_YEAR  = /(^|_)year$|^yr$/i;
  var RE_GEO   = /(^|_)(state|province|city|county|region|country|territory|division|nation|nationality)(_|$)/i;
  var RE_MONEY = /(price|cost|amount|revenue|sales|spend|salary|fee|margin|msrp|value)|_eur$|_usd$|_gbp$/i;
  var RE_QTY   = /(qty|quantity|count|units|goals|assists|points|score|minutes|shots|passes|tackles|saves|distance|wins|losses|total|sum|num_|_num)/i;

  function nameSignal(name) {
    var n = (name || "").toLowerCase();
    if (RE_YEAR.test(n)) return { role: "dimension", reason: "year field (name)" };
    if (RE_ZIP.test(n))  return { role: "dimension", reason: "zip/postal code (name)" };
    if (RE_ID.test(n) || RE_NUMID.test(n)) return { role: "dimension", reason: "id/code/key suffix (name)" };
    // money beats geography on a collision (e.g. market_value_eur: "market" is geo-ish but _eur/value wins)
    if (RE_MONEY.test(n)) return { role: "measure", reason: "money-like name" };
    if (RE_GEO.test(n))  return { role: "dimension", reason: "geography (name)" };
    if (RE_QTY.test(n))  return { role: "measure", reason: "quantity-like name" };
    return null;
  }
  function valueSignal(st) {
    if (!st.nonblank) return { role: "dimension", reason: "all blank", weak: true };
    if (!st.numeric)  return { role: "dimension", reason: "non-numeric values" };
    // numeric. (Pure-numeric identifiers are rare without an id-like NAME — the spec's
    // examples are prefixed/non-numeric like P00055, caught above; so a near-unique pure
    // number reads as a measure here, and a conflicting id-name is flagged in reconciliation.)
    var allYears = st.isInt && st.min >= 1900 && st.max <= 2100 && st.n_distinct <= 200;
    if (allYears) return { role: "dimension", reason: "values look like years" };
    var only01 = st.isInt && st.n_distinct <= 2 && st.min >= 0 && st.max <= 1;
    if (only01) return { role: "dimension", reason: "0/1 boolean flag", ambiguous: true };
    if (st.isInt && st.n_distinct <= 25)
      return { role: "dimension", reason: "small discrete integer set (code or bucket)", ambiguous: true };
    return { role: "measure", reason: "continuous numeric spread" };
  }

  function assignRole(name, st) {
    var ns = nameSignal(name), vs = valueSignal(st);
    var role, confidence, reasons = [], ambiguous = false, disagreement = false;
    if (ns) reasons.push("name → " + ns.role + " (" + ns.reason + ")");
    reasons.push("values → " + vs.role + " (" + vs.reason + ")");

    if (ns && ns.role === vs.role) {                       // both agree
      role = ns.role; confidence = 0.92;
    } else if (ns && ns.role !== vs.role) {                // CONFLICT — never silently pick
      // numeric value-signal usually trumps a name collision, but we drop confidence + flag.
      role = st.numeric ? vs.role : ns.role;
      confidence = 0.5; disagreement = true;
      reasons.push("⚠ name and values disagree — flagged for review");
    } else {                                                // only value signal
      role = vs.role; confidence = vs.weak ? 0.4 : 0.7;
    }
    if (vs.ambiguous) { ambiguous = true; confidence = Math.min(confidence, 0.5);
      reasons.push("intent-dependent (averageable measure OR categorical) — needs confirmation"); }

    var needs_review = ambiguous || disagreement || confidence < 0.6;
    return {
      name: name, role: role, datatype: dtypeOf(st), tag: INFERENCE,
      confidence: +confidence.toFixed(2), ambiguous: ambiguous, disagreement: disagreement,
      needs_review: needs_review, name_signal: ns, value_signal: vs, reasons: reasons, overridable: true
    };
  }
  function dtypeOf(st) {
    if (!st.nonblank) return "empty";
    if (!st.numeric) return "string";
    return st.isInt ? "integer" : "decimal";
  }

  // ---------------- Part B — role-specific check battery ----------------
  function F(id, scope, columns, tag, severity, title, detail, evidence, confidence, provenance) {
    var f = { id: id, scope: scope, columns: columns, tag: tag, severity: severity,
              title: title, detail: detail, evidence: evidence || {}, provenance: provenance || "full" };
    if (tag === INFERENCE) f.confidence = (confidence == null ? 0.6 : confidence);
    return f;
  }

  function measureChecks(st, role, out) {
    var c = [st.name];
    // Range plausibility — negatives in a field whose name implies a non-negative quantity.
    var nonNeg = RE_QTY.test(st.name) || RE_MONEY.test(st.name) || /age|height|weight|minutes|km|speed/i.test(st.name);
    if (nonNeg && st.negatives > 0)
      out.push(F("measure.range", "column", c, DETERMINISTIC, "high",
        "Negative values in a should-be-non-negative field",
        st.negatives + " of " + st.nonblank + " values are negative.",
        { negatives: st.negatives, min: st.min, max: st.max }));
    // Percent out of range
    if (/pct|percent|rate|share|accuracy/i.test(st.name) && st.max != null && st.max > 100 && st.max <= 100000)
      out.push(F("measure.range_pct", "column", c, DETERMINISTIC, "medium",
        "Percent-like field exceeds 100", "max = " + st.max, { max: st.max }));

    // Cap / sentinel — mass piled at one exact value, or a thin band hugging the max.
    var modeVal = null, modeN = 0;
    for (var v in st.freq) if (st.freq[v] > modeN) { modeN = st.freq[v]; modeVal = v; }
    if (st.nonblank >= 20 && pct(modeN, st.nonblank) >= 0.15 && st.n_distinct > 3)
      out.push(F("measure.cap_sentinel", "column", c, DETERMINISTIC, "medium",
        "Mass piled at a single value (cap/sentinel)",
        Math.round(pct(modeN, st.nonblank) * 100) + "% of values equal " + modeVal + " — likely a cap or sentinel.",
        { value: modeVal, count: modeN, fraction: +pct(modeN, st.nonblank).toFixed(3) }));
    else if (st.nonblank >= 30 && st.max != null && st.min != null && st.max > st.min) {
      var band = st.max - (st.max - st.min) * 0.02, near = 0;
      for (var i = 0; i < st.nums.length; i++) if (st.nums[i] >= band) near++;
      if (pct(near, st.nums.length) >= 0.12)
        out.push(F("measure.cap_band", "column", c, DETERMINISTIC, "medium",
          "Values bunch in a thin band at the maximum (likely capped)",
          Math.round(pct(near, st.nums.length) * 100) + "% of values sit within 2% of the max (" + st.max + ").",
          { max: st.max, near: near, fraction: +pct(near, st.nums.length).toFixed(3) }));
    }

    // Scale / unit consistency — bimodality across a ~100x or ~1000x gap (mixed units / decimal shift).
    var pos = st.nums.filter(function (x) { return x > 0; }).map(function (x) { return Math.log10(x); }).sort(function (a, b) { return a - b; });
    if (pos.length >= 30) {
      var gap = 0, at = -1;
      for (var j = 1; j < pos.length; j++) { var g = pos[j] - pos[j - 1]; if (g > gap) { gap = g; at = j; } }
      if (gap >= 1.7) {                                   // ~50x+ jump between two clusters
        var loN = at, hiN = pos.length - at;
        if (pct(Math.min(loN, hiN), pos.length) >= 0.05)  // both clusters non-trivial
          out.push(F("measure.scale_unit", "column", c, DETERMINISTIC, "high",
            "Two value clusters ~" + Math.round(Math.pow(10, gap)) + "× apart (mixed units / shifted decimal)",
            "Distribution is bimodal across a " + gap.toFixed(1) + "-decade gap — likely mixed units or a misplaced decimal.",
            { gap_decades: +gap.toFixed(2), lower_n: loN, upper_n: hiN }));
      }
    }

    // Null density (exact, full file)
    if (st.null_rate >= 0.2)
      out.push(F("measure.nulls", "column", c, DETERMINISTIC, st.null_rate >= 0.5 ? "high" : "medium",
        Math.round(st.null_rate * 100) + "% missing",
        st.missing + " of " + st.n + " rows are blank.", { missing: st.missing, null_rate: st.null_rate }));

    // Zero-inflation (exact) — any mean is diluted.
    if (st.nonblank >= 20 && pct(st.zeros, st.nonblank) >= 0.3)
      out.push(F("measure.zero_inflation", "column", c, DETERMINISTIC, "medium",
        Math.round(pct(st.zeros, st.nonblank) * 100) + "% of values are exactly 0",
        "Most values are zero — any average of this field is diluted; report a non-zero mean separately.",
        { zeros: st.zeros, fraction: +pct(st.zeros, st.nonblank).toFixed(3) }));
  }

  function dimensionChecks(st, rows, header, roles, out) {
    var c = [st.name];
    // Cardinality (DETERMINISTIC count) + an INFERENCE on what it implies.
    out.push(F("dimension.cardinality", "column", c, DETERMINISTIC, "info",
      st.n_distinct + " distinct values",
      st.n_distinct + " distinct across " + st.nonblank + " non-blank rows.",
      { n_distinct: st.n_distinct, distinct_rate: st.distinct_rate }));
    if (st.distinct_rate >= 0.98 && st.nonblank > 20)
      out.push(F("dimension.implication", "column", c, INFERENCE, "info",
        "Looks like a row identifier", "Nearly one distinct value per row — likely an ID, not a grouping field.",
        { distinct_rate: st.distinct_rate }, 0.8));

    // Value consistency — whitespace + case fragmentation that silently splits a group-by. (DET)
    if (st.wsHits > 0)
      out.push(F("dimension.whitespace", "column", c, DETERMINISTIC, "medium",
        "Leading/trailing whitespace fragments values",
        st.wsHits + " values carry surrounding whitespace (e.g. \"Spain \" ≠ \"Spain\").", { affected: st.wsHits }));
    if (st.caseFrag > 0)
      out.push(F("dimension.casing", "column", c, DETERMINISTIC, "medium",
        st.caseFrag + " value(s) appear under multiple casings",
        "The same value occurs with different capitalization — a group-by will split them.", { groups: st.caseFrag }));

    // Identity / collision integrity — if it looks like a key, does it actually identify rows?
    if (st.distinct_rate >= 0.9 && st.nonblank > 20) {
      var coll = keyCollisions(rows, st.idx, header);
      if (coll.dupedKeys > 0)
        out.push(F("dimension.collision", "column", c, DETERMINISTIC, "high",
          "Key-like field has collisions with conflicting attributes",
          coll.dupedKeys + " value(s) recur with DIFFERING other-column values (e.g. " + coll.example + ") — not a clean key.",
          { duped_keys: coll.dupedKeys, conflicting_column: coll.col }));
    }
  }

  // does value-of-col uniquely identify a row, or recur with conflicting other-column values?
  function keyCollisions(rows, idx, header) {
    var seen = Object.create(null), dupedKeys = 0, example = "", col = "";
    var probe = header.length, capRows = Math.min(rows.length, SAMPLE_CAP);
    for (var r = 0; r < capRows; r++) {
      var key = clean(rows[r][idx]); if (key === "") continue;
      var sig = rows[r].map(clean).join("");
      if (seen[key] === undefined) { seen[key] = sig; continue; }
      if (seen[key] !== sig) {                                  // same key, different row → find a conflicting column
        dupedKeys++;
        if (!example) {
          var a = seen[key].split(""), b = sig.split("");
          for (var ci = 0; ci < header.length; ci++) if (ci !== idx && a[ci] !== b[ci]) { col = header[ci]; example = key + ": " + header[ci] + " " + a[ci] + " vs " + b[ci]; break; }
        }
      }
    }
    return { dupedKeys: dupedKeys, example: example, col: col };
  }

  // ---------------- dataset-level checks ----------------
  function datasetChecks(header, rows, stats, roles, out, prov) {
    var n = rows.length, capRows = Math.min(n, SAMPLE_CAP);
    // Exact duplicate rows (DET)
    var seen = Object.create(null), dups = 0;
    for (var r = 0; r < capRows; r++) { var sig = rows[r].map(clean).join(""); if (seen[sig]) dups++; else seen[sig] = 1; }
    if (dups > 0)
      out.push(F("dataset.exact_duplicates", "dataset", [], DETERMINISTIC, dups > n * 0.01 ? "high" : "medium",
        dups + " exact duplicate row(s)", dups + " rows are byte-identical to an earlier row.",
        { duplicates: dups }, null, n > SAMPLE_CAP ? "sampled" : "full"));

    // Should-be-unique-but-isn't: name says id/key, but values repeat. (DET)
    header.forEach(function (h, i) {
      if ((RE_ID.test(h) || RE_NUMID.test(h)) && stats[i].nonblank > 0 && stats[i].distinct_rate < 0.999)
        out.push(F("dataset.expected_unique", "column", [h], DETERMINISTIC, "high",
          "Field named like a key is not unique",
          "\"" + h + "\" looks like an identifier but only " + Math.round(stats[i].distinct_rate * 100) + "% of rows are distinct.",
          { distinct_rate: stats[i].distinct_rate }));
    });

    // Redundancy / co-variation: two low-card dimensions that map 1:1 (summing across both double-counts).
    var dimIdx = [];   // grouping dims only — exclude near-unique keys (they co-vary 1:1 trivially)
    roles.forEach(function (rl, i) { if (rl.role === "dimension" && stats[i].n_distinct >= 2 && stats[i].n_distinct <= 200 && stats[i].distinct_rate < 0.9) dimIdx.push(i); });
    for (var a = 0; a < dimIdx.length; a++) for (var b = a + 1; b < dimIdx.length; b++) {
      if (coVary1to1(rows, dimIdx[a], dimIdx[b], capRows))
        out.push(F("dataset.redundancy", "column", [header[dimIdx[a]], header[dimIdx[b]]], DETERMINISTIC, "medium",
          "\"" + header[dimIdx[a]] + "\" and \"" + header[dimIdx[b]] + "\" co-vary 1:1 (redundant)",
          "These two dimensions are perfectly aligned — treating them as separate cuts double-counts.",
          { columns: [header[dimIdx[a]], header[dimIdx[b]]] }, null, n > SAMPLE_CAP ? "sampled" : "full"));
    }

    // Cross-field coherence: duplicate / mirror numeric columns (A==B, or A==-B). (DET)
    var numIdx = []; stats.forEach(function (s, i) { if (s.numeric) numIdx.push(i); });
    for (var x = 0; x < numIdx.length; x++) for (var y = x + 1; y < numIdx.length; y++) {
      var rel = mirrorRelation(rows, numIdx[x], numIdx[y], capRows);
      if (rel) out.push(F("dataset.coherence", "column", [header[numIdx[x]], header[numIdx[y]]], DETERMINISTIC, "medium",
        "\"" + header[numIdx[x]] + "\" and \"" + header[numIdx[y]] + "\" are " + rel,
        rel === "identical" ? "Two columns hold the same values — one is redundant."
          : "Columns mirror each other — summing across both cancels or double-counts.",
        { relation: rel }, null, n > SAMPLE_CAP ? "sampled" : "full"));
    }

    // Grain (INFERENCE) — what one row represents, from a candidate single-column key.
    var keyCol = null;
    for (var i2 = 0; i2 < stats.length; i2++) if (stats[i2].distinct_rate >= 0.999 && stats[i2].missing === 0) { keyCol = header[i2]; break; }
    out.push(F("dataset.grain", "dataset", keyCol ? [keyCol] : [], INFERENCE, "info",
      keyCol ? "Grain: one row per \"" + keyCol + "\"" : "Grain is unclear (no single-column key)",
      keyCol ? "\"" + keyCol + "\" is unique + complete, so each row appears to be one " + keyCol + "."
        : "No single column uniquely identifies rows — the grain may be composite or mixed.",
      { candidate_key: keyCol }, keyCol ? 0.75 : 0.55));
  }

  function coVary1to1(rows, ia, ib, capRows) {
    var ab = Object.create(null), ba = Object.create(null);
    for (var r = 0; r < capRows; r++) {
      var va = clean(rows[r][ia]), vb = clean(rows[r][ib]); if (va === "" || vb === "") continue;
      if (ab[va] === undefined) ab[va] = vb; else if (ab[va] !== vb) return false;
      if (ba[vb] === undefined) ba[vb] = va; else if (ba[vb] !== va) return false;
    }
    return Object.keys(ab).length > 1;   // genuinely 1:1 across >1 value
  }
  function mirrorRelation(rows, ix, iy, capRows) {
    var eq = 0, neg = 0, cnt = 0;
    for (var r = 0; r < capRows; r++) {
      var a = toNum(rows[r][ix]), b = toNum(rows[r][iy]); if (a === null || b === null) continue;
      cnt++; if (a === b) eq++; if (a === -b) neg++;
    }
    if (cnt < 20) return null;
    if (eq === cnt) return "identical";
    if (neg === cnt && eq !== cnt) return "negatives of each other";
    return null;
  }

  // ---------------- Part C — provenance + sample-size reconciliation ----------------
  // Every stated sample size in a write-up must reconcile to ONE value. Throws otherwise —
  // used as a build/test assertion (the "3,000 vs 500" bug).
  function reconcileSampleSizes(sizes) {
    var distinct = {}; (sizes || []).forEach(function (s) { if (s != null) distinct[s] = 1; });
    var keys = Object.keys(distinct);
    if (keys.length > 1) throw new Error("DQ provenance: conflicting sample sizes referenced: " + keys.join(", "));
    return keys.length ? +keys[0] : null;
  }

  // ---------------- main ----------------
  function assess(header, rows, opts) {
    opts = opts || {}; header = header || []; rows = rows || [];
    var nTotal = rows.length;
    var provenance = { n_total: nTotal, n_analyzed: Math.min(nTotal, SAMPLE_CAP), sampled: nTotal > SAMPLE_CAP };
    var stats = header.map(function (h, i) { return colStats(h, rows, i, nTotal); });
    var roles = header.map(function (h, i) { return assignRole(h, stats[i]); });

    var findings = [];
    header.forEach(function (h, i) {
      if (roles[i].role === "measure") measureChecks(stats[i], roles[i], findings);
      else dimensionChecks(stats[i], rows, header, roles, findings);
      // Role uncertainty is itself a finding — surfaced, never silently propagated.
      if (roles[i].needs_review)
        findings.push(F("role.review", "column", [h], INFERENCE, "medium",
          "Role needs confirmation: " + roles[i].role + (roles[i].disagreement ? " (name/value conflict)" : roles[i].ambiguous ? " (intent-dependent)" : ""),
          roles[i].reasons.join("; "), { role: roles[i].role }, roles[i].confidence));
    });
    datasetChecks(header, rows, stats, roles, findings, provenance);

    return { roles: roles, findings: findings, provenance: provenance, stats: stats };
  }

  var API = { assess: assess, assignRole: assignRole, colStats: colStats,
              reconcileSampleSizes: reconcileSampleSizes, DETERMINISTIC: DETERMINISTIC, INFERENCE: INFERENCE, VERSION: "1" };
  if (typeof window !== "undefined") window.DQ = API;
  if (typeof module !== "undefined" && module.exports) module.exports = API;
})();
