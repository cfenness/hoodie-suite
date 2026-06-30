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
  var PAIR_CAP = 4000;     // rows scanned for O(cols²) pairwise checks (mirror/redundancy/collision)
  var PAIR_COLS = 60;      // max columns entered into a pairwise check (bounds C(n,2))
  var DQ_ROW_CAP = 20000;  // per-column profiling row sample for large files (kept responsive)

  // ---- CONFIG (grain-first protocol; tune per source, never bury inline) ----
  var CANDIDATE_KEY_DISTINCT_TARGET = 0.99;  // min distinctness for an inferred key
  var MISFIRE_ROW_FRACTION          = 0.05;  // detector firing on >this share of eligible rows…
  var MISFIRE_REQUIRES_UNIFORM_CONF = true;  // …at flat confidence ⇒ suppress + meta-card
  var UNIT_RATIO_TOLERANCE          = 0.15;  // cluster ratio within 15% of a known factor ⇒ mixed units
  var CONTINUITY_THRESHOLD          = 0.6;   // avg entity coverage ≥ this ⇒ continuity panel; else event grain
  var KEY_COMBO_COLS                = 25;    // cap columns entering the 2-col candidate-key search
  var KEY_SEARCH_CAP                = 20000; // bound the combinatorial key SEARCH; reported distinctness is recomputed full-table
  var SEP_CHAR = String.fromCharCode(1);     // cell separator so adjacent values cannot merge in a signature

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
    var nums = [], numericNonblank = 0, nonblank = 0, intCount = 0, nonNumericCount = 0, typeTokens = Object.create(null);
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
      } else { nonNumericCount++; if (typeTokens[s] === undefined && Object.keys(typeTokens).length < 8) typeTokens[s] = 1; }   // FIX 3 — capture non-numeric tokens for type-violation testing
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
      nums: nums, freq: freq, fixedWidth: nonblank > 1 && sameLen, caseFrag: caseFrag, wsHits: wsHits,
      numericFrac: +pct(numericNonblank, nonblank).toFixed(4), nonNumericCount: nonNumericCount, typeTokens: Object.keys(typeTokens).slice(0, 5)
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

  // FIX 3 — TYPE VIOLATION: test VALUE conformance to the (de-facto) numeric type, never trust a tag.
  // A majority-numeric column carrying non-numeric tokens ('error', '#REF!', 'N/A', '--') is a type
  // violation — those rows are silently dropped by naive numeric parsing. Deterministic, Tier 1.
  // Family is "type" (not "measure") so the certified-role muter never suppresses it.
  function typeViolationCheck(st, out) {
    if (st.numericFrac >= 0.5 && st.numericFrac < 1 && st.nonNumericCount > 0) {
      var ex = (st.typeTokens || []).map(function (t) { return "'" + t + "'"; }).join(", ");
      out.push(F("type.violation", "column", [st.name], DETERMINISTIC, "high",
        "Type violation: non-numeric tokens in a numeric column",
        st.nonNumericCount + " of " + st.nonblank + " values are non-numeric (" + ex + ") in an otherwise-numeric column — naive parsing silently drops these rows.",
        { rows_affected: st.nonNumericCount, tokens: st.typeTokens, numeric_frac: st.numericFrac }));
    }
  }

  function measureChecks(st, role, out) {
    var c = [st.name];
    // Range plausibility — negatives in a field whose name implies a non-negative quantity.
    var nonNeg = RE_QTY.test(st.name) || RE_MONEY.test(st.name) || /age|height|weight|minutes|km|speed/i.test(st.name);
    if (nonNeg && st.negatives > 0)
      out.push(F("measure.range", "column", c, DETERMINISTIC, "high",
        "Negative values in a should-be-non-negative field",
        st.negatives + " of " + st.nonblank + " values are negative.",
        { rows_affected: st.negatives, negatives: st.negatives, min: st.min, max: st.max }));
    // Percent out of range
    if (/pct|percent|rate|share|accuracy/i.test(st.name) && st.max != null && st.max > 100 && st.max <= 100000) {
      var overN = st.nums.filter(function (x) { return x > 100; }).length;
      out.push(F("measure.range_pct", "column", c, DETERMINISTIC, "medium",
        "Percent-like field exceeds 100", overN + " values exceed 100 (max = " + st.max + ")", { rows_affected: overN, over_100: overN, max: st.max }));
    }

    // Cap / sentinel — mass piled at one exact value, or a thin band hugging the max.
    var modeVal = null, modeN = 0;
    for (var v in st.freq) if (st.freq[v] > modeN) { modeN = st.freq[v]; modeVal = v; }
    if (st.nonblank >= 20 && pct(modeN, st.nonblank) >= 0.15 && st.n_distinct > 3)
      out.push(F("measure.cap_sentinel", "column", c, DETERMINISTIC, "medium",
        "Mass piled at a single value (cap/sentinel)",
        Math.round(pct(modeN, st.nonblank) * 100) + "% of values equal " + modeVal + " — likely a cap or sentinel.",
        { rows_affected: modeN, value: modeVal, count: modeN, fraction: +pct(modeN, st.nonblank).toFixed(3) }));
    else if (st.nonblank >= 30 && st.max != null && st.min != null && st.max > st.min) {
      var band = st.max - (st.max - st.min) * 0.02, near = 0;
      for (var i = 0; i < st.nums.length; i++) if (st.nums[i] >= band) near++;
      if (pct(near, st.nums.length) >= 0.12)
        out.push(F("measure.cap_band", "column", c, DETERMINISTIC, "medium",
          "Values bunch in a thin band at the maximum (likely capped)",
          Math.round(pct(near, st.nums.length) * 100) + "% of values sit within 2% of the max (" + st.max + ").",
          { rows_affected: near, max: st.max, near: near, fraction: +pct(near, st.nums.length).toFixed(3) }));
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
            { rows_affected: Math.min(loN, hiN), gap_decades: +gap.toFixed(2), lower_n: loN, upper_n: hiN }));
      }
    }

    // Null density (exact, full file)
    if (st.null_rate >= 0.2)
      out.push(F("measure.nulls", "column", c, DETERMINISTIC, st.null_rate >= 0.5 ? "high" : "medium",
        Math.round(st.null_rate * 100) + "% missing",
        st.missing + " of " + st.n + " rows are blank.", { rows_affected: st.missing, missing: st.missing, null_rate: st.null_rate }));

    // Zero-inflation (exact) — any mean is diluted.
    if (st.nonblank >= 20 && pct(st.zeros, st.nonblank) >= 0.3)
      out.push(F("measure.zero_inflation", "column", c, DETERMINISTIC, "medium",
        Math.round(pct(st.zeros, st.nonblank) * 100) + "% of values are exactly 0",
        "Most values are zero — any average of this field is diluted; report a non-zero mean separately.",
        { rows_affected: st.zeros, zeros: st.zeros, fraction: +pct(st.zeros, st.nonblank).toFixed(3) }));
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
        st.wsHits + " values carry surrounding whitespace (e.g. \"Spain \" ≠ \"Spain\").", { rows_affected: st.wsHits, affected: st.wsHits }));
    if (st.caseFrag > 0)
      out.push(F("dimension.casing", "column", c, DETERMINISTIC, "medium",
        st.caseFrag + " value(s) appear under multiple casings",
        "The same value occurs with different capitalization — a group-by will split them.", { rows_affected: st.caseFrag, groups: st.caseFrag }));

    // Identity / collision integrity — if it looks like a key, does it actually identify rows?
    if (st.distinct_rate >= 0.9 && st.nonblank > 20) {
      var coll = keyCollisions(rows, st.idx, header);
      if (coll.dupedKeys > 0)
        out.push(F("dimension.collision", "column", c, DETERMINISTIC, "high",
          "Key-like field has collisions with conflicting attributes",
          coll.dupedKeys + " value(s) recur with DIFFERING other-column values (e.g. " + coll.example + ") — not a clean key.",
          { rows_affected: coll.dupedKeys, duped_keys: coll.dupedKeys, conflicting_column: coll.col }));
    }
  }

  // does value-of-col uniquely identify a row, or recur with conflicting other-column values?
  function keyCollisions(rows, idx, header) {
    var seen = Object.create(null), dupedKeys = 0, example = "", col = "";
    var probe = header.length, capRows = Math.min(rows.length, PAIR_CAP);
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
  function datasetChecks(header, rows, stats, roles, out, prov, profile, dedup) {
    // Pairwise (O(cols^2)) checks scan a row SAMPLE so wide/large files do not freeze the page.
    var n = rows.length, capRows = Math.min(n, PAIR_CAP), pairProv = n > PAIR_CAP ? "sampled" : "full";
    // Exact duplicate rows (DET) - canonical FULL-table count from computeDedup (FIX 1b); identical to
    // the grain header number, never recomputed on a sample.
    var dups = dedup ? dedup.exact_dupes : 0, rawN = dedup ? dedup.raw_n : n;
    if (dups > 0)
      out.push(F("dataset.exact_duplicates", "dataset", [], DETERMINISTIC, dups > rawN * 0.01 ? "high" : "medium",
        dups.toLocaleString() + " exact duplicate rows", dups.toLocaleString() + " rows are byte-identical to an earlier row (full-table count over " + rawN.toLocaleString() + " rows).",
        { rows_affected: dups, exact_duplicates: dups, raw_n: rawN }, null, "full"));

    // UNIQUENESS — test the inferred candidate KEY only (Stage 0). A foreign key or dimension
    // repeating in a fact table is correct, not a defect — those are NEVER flagged here.
    if (profile && profile.candidate_key && profile.key_distinct_rate != null && profile.key_distinct_rate < 0.999)
      out.push(F("dataset.key_not_unique", "dataset", profile.candidate_key, DETERMINISTIC, "high",
        "Candidate key (" + profile.candidate_key.join(", ") + ") has " + (profile.key_collisions || 0).toLocaleString() + " collisions",
        (profile.key_collisions || 0).toLocaleString() + " distinct rows share a key with a differing row (same key, different values) - NOT exact duplicates. Either the true grain is finer or these are conflicting records.",
        { rows_affected: profile.key_collisions || 0, candidate_key: profile.candidate_key, key_distinct_rate: profile.key_distinct_rate, key_collisions: profile.key_collisions || 0 }, null, "full"));

    // Redundancy / co-variation: two low-card dimensions that map 1:1 (summing across both double-counts).
    var dimIdx = [];   // grouping dims only — exclude near-unique keys (they co-vary 1:1 trivially)
    roles.forEach(function (rl, i) { if (rl.role === "dimension" && stats[i].n_distinct >= 2 && stats[i].n_distinct <= 200 && stats[i].distinct_rate < 0.9) dimIdx.push(i); }); dimIdx = dimIdx.slice(0, PAIR_COLS);
    for (var a = 0; a < dimIdx.length; a++) for (var b = a + 1; b < dimIdx.length; b++) {
      if (coVary1to1(rows, dimIdx[a], dimIdx[b], capRows))
        out.push(F("dataset.redundancy", "column", [header[dimIdx[a]], header[dimIdx[b]]], DETERMINISTIC, "medium",
          "\"" + header[dimIdx[a]] + "\" and \"" + header[dimIdx[b]] + "\" co-vary 1:1 (redundant)",
          "These two dimensions are perfectly aligned — treating them as separate cuts double-counts.",
          { columns: [header[dimIdx[a]], header[dimIdx[b]]] }, null, pairProv));
    }

    // Cross-field coherence: duplicate / mirror numeric columns (A==B, or A==-B). (DET)
    var numIdx = []; stats.forEach(function (s, i) { if (s.numeric) numIdx.push(i); }); numIdx = numIdx.slice(0, PAIR_COLS);
    for (var x = 0; x < numIdx.length; x++) for (var y = x + 1; y < numIdx.length; y++) {
      var rel = mirrorRelation(rows, numIdx[x], numIdx[y], capRows);
      if (rel) out.push(F("dataset.coherence", "column", [header[numIdx[x]], header[numIdx[y]]], DETERMINISTIC, "medium",
        "\"" + header[numIdx[x]] + "\" and \"" + header[numIdx[y]] + "\" are " + rel,
        rel === "identical" ? "Two columns hold the same values — one is redundant."
          : "Columns mirror each other — summing across both cancels or double-counts.",
        { relation: rel }, null, pairProv));
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

  // ---------------- STAGE 0 — grain profiler (runs before any detector) ----------------
  var RE_EVENT = /(match|game|event|order|txn|transaction|fixture|booking|invoice|session|trip|visit)/i;
  // A→B functional dependency over the sample: every A-value maps to exactly one B-value.
  function detectDeps(rows, detIdx, header, n) {
    var deps = [];
    for (var c = 0; c < header.length; c++) {
      if (c === detIdx) continue;
      var map = Object.create(null), ok = true, seen = 0;
      for (var r = 0; r < n; r++) {
        var dv = clean(rows[r][detIdx]), cv = clean(rows[r][c]); if (dv === "" || cv === "") continue;
        if (map[dv] === undefined) { map[dv] = cv; seen++; } else if (map[dv] !== cv) { ok = false; break; }
      }
      if (ok && seen > 1) deps.push(header[c]);
    }
    return deps;
  }
  // FIX 1b - ONE canonical exact-duplicate operation on the FULL table, shared by the grain resolver
  // and the Tier-1 dup finding so no two surfaces report different "duplicate" counts. EXACT duplicate =
  // byte-identical row; KEY collisions (same key, differing values) are counted separately below.
  function computeDedup(rows) {
    var seen = Object.create(null), distinctRows = [];
    for (var r = 0; r < rows.length; r++) {
      var row = rows[r], sig = ""; for (var c = 0; c < row.length; c++) sig += clean(row[c]) + SEP_CHAR;
      if (seen[sig] === undefined) { seen[sig] = 1; distinctRows.push(row); }
    }
    return { raw_n: rows.length, distinct_n: distinctRows.length, exact_dupes: rows.length - distinctRows.length, distinctRows: distinctRows };
  }
  function profileGrain(header, rows, roles, stats, dedup) {
    var n = rows.length;                  // rows == sampled work, used only for FD/entity scans
    // FIX 1/1b - dedup computed ONCE on the FULL table (computeDedup, in assess) and shared, so the count
    // here equals the Tier-1 exact-duplicate finding. The combinatorial key SEARCH is bounded to
    // KEY_SEARCH_CAP rows; the REPORTED distinctness is recomputed on the full deduped set.
    var drows = dedup.distinctRows, nd = dedup.distinct_n, dupes_removed = dedup.exact_dupes;
    function distinctCount(idxs, src, m) { var st = Object.create(null), d = 0; for (var r = 0; r < m; r++) { var k = ""; for (var jj = 0; jj < idxs.length; jj++) k += clean(src[r][idxs[jj]]) + SEP_CHAR; if (st[k] === undefined) { st[k] = 1; d++; } } return d; }
    function distinctRate(idxs, src, m) { return m ? distinctCount(idxs, src, m) / m : 0; }
    var srows = (nd > KEY_SEARCH_CAP) ? drows.slice(0, KEY_SEARCH_CAP) : drows, searchN = srows.length;
    var keyish = [];   // key-eligible: not a measure (by role OR name), complete, >1 distinct
    header.forEach(function (h, i) { if (roles[i].role !== "measure" && !RE_QTY.test(h) && !RE_MONEY.test(h) && stats[i].missing === 0 && stats[i].n_distinct > 1) keyish.push(i); });

    // candidate key — smallest set reaching the distinct target ON THE DEDUPED SET. No lone *_id assumption.
    var key = null;
    for (var i = 0; i < keyish.length; i++) if (distinctRate([keyish[i]], srows, searchN) >= CANDIDATE_KEY_DISTINCT_TARGET) { key = [keyish[i]]; break; }
    if (!key) {
      var cand = keyish.filter(function (idx) { return stats[idx].distinct_rate >= 0.02; })
                       .sort(function (a, b) { return stats[b].distinct_rate - stats[a].distinct_rate; }).slice(0, KEY_COMBO_COLS);
      outer:
      for (var a = 0; a < cand.length; a++) for (var b = a + 1; b < cand.length; b++) {
        if (distinctRate([cand[a], cand[b]], srows, searchN) >= CANDIDATE_KEY_DISTINCT_TARGET) { key = [cand[a], cand[b]]; break outer; }
      }
    }
    if (!key) return {
      grain: "ambiguous", ambiguous: true, candidate_key: null, entity: null, axis: null, axis_is_event: false,
      entity_group: null, fds: {}, column_roles: {}, dupes_removed: dupes_removed, exact_dupes: dupes_removed, key_collisions: null,
      grain_statement: "Grain is ambiguous — no column set of ≤2 uniquely identifies rows. Grain-dependent detectors (uniqueness, coverage) are withheld; value-integrity checks still run."
    };

    // functional dependencies for the key parts (match_date ← match_id; team ← player_id …)
    var fds = {}; key.forEach(function (ki) { fds[header[ki]] = detectDeps(rows, ki, header, n); });

    // entity vs axis for a composite key: the axis is the time/event part (period/date name, or
    // determines a date, or event-like name); the entity is the other.
    var entity = null, axis = null, axis_is_event = false, event_date = null, entity_group = null;
    if (key.length >= 2) {
      key.forEach(function (ki) {
        var name = header[ki], deps = fds[name] || [];
        var depDate = deps.filter(function (dn) { return /date|time/.test(dn.toLowerCase()); })[0];
        var timeLike = /(date|year|season|period|month|quarter|week|day|time)/i.test(name);
        var eventLike = RE_EVENT.test(name) || !!depDate;
        if ((timeLike || eventLike) && !axis) { axis = name; axis_is_event = eventLike && !timeLike ? true : (eventLike ? true : false); if (depDate) event_date = depDate; }
      });
      if (!axis) { axis = header[key[1]]; }
      entity = key.map(function (ki) { return header[ki]; }).filter(function (nm) { return nm !== axis; })[0] || header[key[0]];
      // entity_group: the coarser dimension the entity determines that PARTITIONS events —
      // i.e. fewest distinct values per event (team ≈ 2 per match; nationality ≈ many). Not just
      // lowest overall cardinality.
      var edeps = fds[entity] || [], entCard = stats[header.indexOf(entity)].n_distinct, aIdx = header.indexOf(axis), best = Infinity;
      edeps.forEach(function (dn) {
        var s = stats[header.indexOf(dn)];
        if (!s || s.n_distinct <= 1 || s.n_distinct >= entCard || roleByName(roles, dn) === "measure" || /date|time/i.test(dn)) return;
        var di = header.indexOf(dn), per = Object.create(null);
        for (var r = 0; r < n; r++) { var ev = clean(rows[r][aIdx]), gv = clean(rows[r][di]); if (ev === "" || gv === "") continue; (per[ev] = per[ev] || Object.create(null))[gv] = 1; }
        var ks = Object.keys(per), sum = 0; ks.forEach(function (k) { sum += Object.keys(per[k]).length; });
        var avg = ks.length ? sum / ks.length : Infinity;
        if (avg < best) { best = avg; entity_group = dn; }
      });
    }

    // column roles: PK-part | foreign-key | timeseries | measure | dimension
    var column_roles = {};
    header.forEach(function (h, i) {
      var inKey = key.indexOf(i) >= 0;
      if (inKey) column_roles[h] = "PK-part";
      else if (roles[i].role === "measure") column_roles[h] = "measure";
      else if (RE_ID.test(h) || RE_NUMID.test(h)) column_roles[h] = "foreign-key";
      else if (/date|time|year|season|period|month/i.test(h)) column_roles[h] = "timeseries";
      else column_roles[h] = "dimension";
    });

    var keyNames = key.map(function (ki) { return header[ki]; });
    // FIX 1b - distinct KEY tuples on the FULL deduped set; rawRate / dedupRate / key_collisions all
    // derive from this ONE count, so every reported number reconciles with the dedup arithmetic.
    var keyDistinctCount = distinctCount(key, drows, nd);
    var dedupRate = +(nd ? keyDistinctCount / nd : 0).toFixed(4);
    var rawRate = +(dedup.raw_n ? keyDistinctCount / dedup.raw_n : 0).toFixed(4);
    var key_collisions = nd - keyDistinctCount;
    var resolvedPostDedup = dupes_removed > 0 && rawRate < CANDIDATE_KEY_DISTINCT_TARGET && dedupRate >= CANDIDATE_KEY_DISTINCT_TARGET;
    var stmt = "one row per " + keyNames.map(function (k) { return k.replace(/_(id|key|code|no)$/i, ""); }).join(" per ");
    if (dupes_removed > 0) stmt += ", resolved after removing " + dupes_removed.toLocaleString() + " exact-duplicate row" + (dupes_removed === 1 ? "" : "s");
    if (key_collisions > 0) stmt += (dupes_removed > 0 ? ". " : ", with ") + key_collisions.toLocaleString() + " key collision" + (key_collisions === 1 ? "" : "s") + " remaining (same key, differing values) - see Tier 2";
    if (resolvedPostDedup) stmt += " (raw distinctness " + (rawRate * 100).toFixed(1) + "% -> " + (dedupRate * 100).toFixed(1) + "% deduped)";
    return {
      grain: key.length >= 2 ? "fact" : "entity", ambiguous: false, candidate_key: keyNames,
      entity: entity, axis: axis, axis_is_event: axis_is_event, event_date: event_date, entity_group: entity_group,
      fds: fds, column_roles: column_roles, key_distinct_rate: dedupRate, key_distinct_rate_raw: rawRate,
      dupes_removed: dupes_removed, exact_dupes: dupes_removed, key_collisions: key_collisions,
      grain_resolved_post_dedup: resolvedPostDedup, grain_statement: stmt
    };
  }
  function roleByName(roles, name) { for (var i = 0; i < roles.length; i++) if (roles[i].name === name) return roles[i].role; return null; }
  function keyDistinct(rows, key, n) {
    var seen = Object.create(null), d = 0;
    for (var r = 0; r < n; r++) { var k = key.map(function (i) { return clean(rows[r][i]); }).join(""); if (seen[k] === undefined) { seen[k] = 1; d++; } }
    return +pct(d, n).toFixed(4);
  }

  // ---------------- STAGE 2 — misfire meta-guard ----------------
  // After a detector runs: if it fired on > MISFIRE_ROW_FRACTION of eligible rows (and, when
  // required, at flat confidence), suppress its instances and emit ONE meta-card. Volume is
  // never evidence — one mis-specified detector can outproduce every real finding combined.
  function misfireGuard(findings, eligibleN, grainStmt) {
    // Only INSTANCE-grained detectors (scope "cell" — one finding per row/cell) can flood against
    // the row count. Per-column / per-dataset SUMMARIES (cardinality, dupes, key, …) are bounded
    // by column count and pass through untouched.
    var out = findings.filter(function (f) { return f.scope !== "cell"; });
    var byDet = {};
    findings.forEach(function (f) { if (f.scope !== "cell") return; var d = f.id || "?"; (byDet[d] = byDet[d] || []).push(f); });
    Object.keys(byDet).forEach(function (det) {
      var inst = byDet[det], N = inst.length;
      var frac = eligibleN ? N / eligibleN : 0;
      var confs = {}; inst.forEach(function (f) { confs[f.tag === INFERENCE ? (f.confidence || 0) : "det"] = 1; });
      var uniform = Object.keys(confs).length === 1;
      var misfired = N > 3 && frac > MISFIRE_ROW_FRACTION && (!MISFIRE_REQUIRES_UNIFORM_CONF || uniform);
      if (misfired) {
        out.push(F(det + ".misfire", "dataset", inst[0].columns || [], INFERENCE, "high",
          "Detector ‘" + det + "’ withheld — likely mis-specified for this grain",
          "Detector ‘" + det + "’ flagged " + N + " rows (" + Math.round(frac * 100) + "% of eligible) at " +
          (uniform ? "flat" : "varied") + " confidence. This indicates the detector is mis-specified for this grain, not " +
          N + " distinct defects. Review its config against grain = " + (grainStmt || "?") + ". Instances withheld.",
          { detector: det, flagged: N, fraction: +frac.toFixed(3), instances_withheld: N }, 0.85));
      } else { out = out.concat(inst); }
    });
    return out;
  }

  // ---------------- main ----------------
  function assess(header, rows, opts) {
    opts = opts || {}; header = header || []; rows = rows || [];
    var nTotal = rows.length;
    // For large files, profile a row SAMPLE so the page stays responsive — labeled "sampled".
    var cap = opts.rowCap || DQ_ROW_CAP;
    var work = (nTotal > cap) ? rows.slice(0, cap) : rows, nWork = work.length;
    var provenance = { n_total: nTotal, n_analyzed: nWork, sampled: nTotal > cap };
    var stats = header.map(function (h, i) { return colStats(h, work, i, nWork); });
    var roles = header.map(function (h, i) { return assignRole(h, stats[i]); });
    // STAGE 0: profile grain BEFORE any detector. Downstream reads this, not column names.
    var dedup = computeDedup(rows);   // FIX 1b - ONE canonical full-table exact-dup result, shared by grain + dataset checks
    var profile = profileGrain(header, work, roles, stats, dedup);

    var findings = [];
    header.forEach(function (h, i) {
      typeViolationCheck(stats[i], findings);                                // FIX 3 — verify values vs numeric type, before profiling
      if (roles[i].role === "measure") measureChecks(stats[i], roles[i], findings);
      else dimensionChecks(stats[i], work, header, roles, findings);
      // Role uncertainty is itself a finding — surfaced, never silently propagated.
      if (roles[i].needs_review)
        findings.push(F("role.review", "column", [h], INFERENCE, "medium",
          "Role needs confirmation: " + roles[i].role + (roles[i].disagreement ? " (name/value conflict)" : roles[i].ambiguous ? " (intent-dependent)" : ""),
          roles[i].reasons.join("; "), { role: roles[i].role }, roles[i].confidence));
    });
    datasetChecks(header, work, stats, roles, findings, provenance, profile, dedup);
    // honest provenance: when the file was sampled, no finding may claim full-file exactness
    if (provenance.sampled) findings.forEach(function (f) { if (f.provenance === "full" && f.id !== "dataset.exact_duplicates" && f.id !== "dataset.key_not_unique") f.provenance = "sampled"; });
    // STAGE 2: misfire meta-guard — collapse any detector that over-fires for this grain.
    findings = misfireGuard(findings, nWork, profile.grain_statement);

    return { roles: roles, findings: findings, provenance: provenance, stats: stats, profile: profile };
  }

  var API = { assess: assess, assignRole: assignRole, colStats: colStats, profileGrain: profileGrain, misfireGuard: misfireGuard,
              reconcileSampleSizes: reconcileSampleSizes, DETERMINISTIC: DETERMINISTIC, INFERENCE: INFERENCE, VERSION: "2",
              CONFIG: { CANDIDATE_KEY_DISTINCT_TARGET: CANDIDATE_KEY_DISTINCT_TARGET, MISFIRE_ROW_FRACTION: MISFIRE_ROW_FRACTION, MISFIRE_REQUIRES_UNIFORM_CONF: MISFIRE_REQUIRES_UNIFORM_CONF, UNIT_RATIO_TOLERANCE: UNIT_RATIO_TOLERANCE, CONTINUITY_THRESHOLD: CONTINUITY_THRESHOLD } };
  if (typeof window !== "undefined") window.DQ = API;
  if (typeof module !== "undefined" && module.exports) module.exports = API;
})();
