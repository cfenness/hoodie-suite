/* fullread.js — the deterministic ("measured") layer of Full Read.
 *
 * Pure, in-browser profiling of a parsed table. NOTHING here goes through a model — every
 * value is computed from the actual bytes, so it works with Claude turned off and never
 * produces a hallucinated number. Shared by "Overlay your data" and "Hoodie Intelligence".
 *
 *   FullRead.profile(header, rows)   -> deterministic profile (R-console + the model payload)
 *   FullRead.ruleClassify(profile)   -> rule-based default classification (model-off fallback)
 *
 * `rows` is an array of arrays (cells), `header` an array of column names. Distributions are
 * sampled to ~120k rows; row counts are exact.
 */
(function () {
  "use strict";
  var SAMPLE_CAP = 120000;
  var TOPK = 12;

  var NUM_RE   = /^-?\$?\s?\d{1,3}(,\d{3})*(\.\d+)?%?$|^-?\$?\s?\d+(\.\d+)?%?$/;
  var INT_RE   = /^-?\d+$/;
  var DATE_RE  = /^\d{4}[-/]\d{1,2}([-/]\d{1,2})?([ T]\d{1,2}:\d{2})?/;   // 2026-06, 2026-06-29, +time
  var USDATE_RE= /^\d{1,2}[-/]\d{1,2}[-/]\d{2,4}$/;                       // 06/29/2026
  var BOOLSET  = { "true":1,"false":1,"yes":1,"no":1,"t":1,"f":1,"y":1,"n":1,"0":1,"1":1 };

  function clean(v) { return v == null ? "" : String(v).trim(); }
  function toNum(v) {
    var s = clean(v).replace(/[$,%\s]/g, "");
    if (s === "" || isNaN(s)) return null;
    return parseFloat(s);
  }
  function isDateStr(s) { return (DATE_RE.test(s) || USDATE_RE.test(s)) && !isNaN(Date.parse(s.replace(" ", "T"))); }

  function valDtype(s) {
    if (s === "") return "empty";
    if (NUM_RE.test(s)) {
      if (/%$/.test(s)) return "percent";
      if (/^\$|\$\s?\d/.test(s)) return "currency";
      return INT_RE.test(s.replace(/[,]/g, "")) ? "integer" : "decimal";
    }
    if (isDateStr(s)) return /\d:\d/.test(s) ? "datetime" : "date";
    if (BOOLSET[s.toLowerCase()]) return "boolean";
    return "string";
  }

  function quantile(sorted, q) {
    if (!sorted.length) return null;
    var pos = (sorted.length - 1) * q, base = Math.floor(pos), rest = pos - base;
    return sorted[base + 1] !== undefined ? sorted[base] + rest * (sorted[base + 1] - sorted[base]) : sorted[base];
  }

  function roleGuess(name, dtype, samples) {
    var n = (name || "").toLowerCase();
    if (/(^|_)(id|uuid|guid)$|_id$|number$|^no$|^num$/.test(n)) return "identifier";
    if (/upc|gtin|sku|barcode|isbn/.test(n)) return "identifier";
    if (/email|e-mail/.test(n)) return "email";
    if (/phone|tel|mobile/.test(n)) return "phone";
    if (/zip|postal/.test(n)) return "zip";
    if (/state|province|city|county|region|country|territory|division|market/.test(n)) return "geography";
    if (/date|day|month|year|week|time|timestamp|period|_at$/.test(n)) return "date";
    if (/price|cost|amount|revenue|sales|spend|msrp|\$|usd|dollar/.test(n)) return "money";
    if (/qty|quantity|units|count|volume|cases|9l|servings|on_hand|stock/.test(n)) return "quantity";
    if (/product|item|brand|sku_name|description|category|varietal|flavor/.test(n)) return "product";
    if (/account|outlet|store|customer|retailer|vendor|supplier/.test(n)) return "account";
    if (dtype === "currency" || dtype === "percent") return "money";
    return null;
  }

  function profileColumn(name, vals) {
    var n = vals.length, nonblank = [], freq = Object.create(null), distinct = 0;
    var dcount = {};   // dtype histogram over non-blank
    for (var i = 0; i < n; i++) {
      var s = clean(vals[i]);
      if (s === "") continue;
      nonblank.push(s);
      if (freq[s] === undefined) { freq[s] = 0; distinct++; }
      freq[s]++;
      var dt = valDtype(s); dcount[dt] = (dcount[dt] || 0) + 1;
    }
    var nb = nonblank.length, n_missing = n - nb;
    // dominant dtype (ignore 'empty'); flag mixed if the runner-up is non-trivial
    var dts = Object.keys(dcount).sort(function (a, b) { return dcount[b] - dcount[a]; });
    var dtype = dts[0] || "empty";
    var mixed = dts.length > 1 && dcount[dts[1]] / Math.max(1, nb) > 0.1
                && !(/(integer|decimal|currency|percent)/.test(dtype) && /(integer|decimal|currency|percent)/.test(dts[1]));

    var col = {
      name: name, dtype: dtype, mixed: mixed,
      n: n, n_missing: n_missing, complete_rate: n ? +(nb / n).toFixed(4) : 0,
      n_distinct: distinct, distinct_rate: nb ? +(distinct / nb).toFixed(4) : 0,
      is_unique: nb > 0 && distinct === nb && n_missing === 0,
      stats: null, top_values: null, rare_count: 0, date: null,
      examples: nonblank.slice(0, 3),
      role_guess: roleGuess(name, dtype, nonblank.slice(0, 20)),
    };

    var numeric = /(integer|decimal|currency|percent)/.test(dtype);
    if (numeric) {
      var nums = [];
      for (var j = 0; j < nb && j < SAMPLE_CAP; j++) { var x = toNum(nonblank[j]); if (x !== null) nums.push(x); }
      nums.sort(function (a, b) { return a - b; });
      if (nums.length) {
        var p25 = quantile(nums, .25), p75 = quantile(nums, .75), iqr = p75 - p25;
        var lo = p25 - 1.5 * iqr, hi = p75 + 1.5 * iqr, out = 0;
        for (var k = 0; k < nums.length; k++) if (nums[k] < lo || nums[k] > hi) out++;
        col.stats = {
          min: nums[0], p25: +p25.toFixed(4), median: +quantile(nums, .5).toFixed(4),
          mean: +(nums.reduce(function (a, b) { return a + b; }, 0) / nums.length).toFixed(4),
          p75: +p75.toFixed(4), max: nums[nums.length - 1], n_outliers: out,
        };
      }
    }
    if (dtype === "date" || dtype === "datetime") {
      var ts = [];
      for (var d = 0; d < nb && d < SAMPLE_CAP; d++) { var t = Date.parse(nonblank[d].replace(" ", "T")); if (!isNaN(t)) ts.push(t); }
      ts.sort(function (a, b) { return a - b; });
      if (ts.length) {
        var span = ts[ts.length - 1] - ts[0], DAY = 864e5;
        var gran = span > 1100 * DAY ? "year" : span > 90 * DAY ? "month" : span > 21 * DAY ? "week" : "day";
        // refine by string format: YYYY-MM only -> month, YYYY only -> year
        if (/^\d{4}$/.test(nonblank[0])) gran = "year";
        else if (/^\d{4}[-/]\d{1,2}$/.test(nonblank[0])) gran = "month";
        col.date = { min: new Date(ts[0]).toISOString().slice(0, 10), max: new Date(ts[ts.length - 1]).toISOString().slice(0, 10), granularity: gran };
      }
    }
    // categorical top-k for non-numeric (or low-cardinality numeric like codes)
    if (!numeric || col.distinct_rate < 0.5) {
      var pairs = Object.keys(freq).map(function (v) { return { v: v, count: freq[v] }; })
        .sort(function (a, b) { return b.count - a.count; });
      col.top_values = pairs.slice(0, TOPK);
      col.rare_count = pairs.filter(function (p) { return p.count === 1; }).length;
    }
    return col;
  }

  function profile(header, rows, opts) {
    opts = opts || {};
    header = header || [];
    rows = rows || [];
    var n_rows = rows.length, n_cols = header.length;
    // transpose to columns (sampled for per-value work, exact for counts)
    var cols = header.map(function (h, ci) {
      var vals = new Array(n_rows);
      for (var r = 0; r < n_rows; r++) vals[r] = (rows[r] && rows[r][ci] !== undefined) ? rows[r][ci] : "";
      return profileColumn(h || ("col" + ci), vals);
    });

    // candidate keys: single unique+complete columns, else smallest 2-combo among high-cardinality cols
    var keys = cols.filter(function (c) { return c.is_unique; }).map(function (c) { return [c.name]; });
    if (!keys.length && n_rows > 1) {
      var hi = cols.filter(function (c) { return c.distinct_rate > 0.3; })
                   .sort(function (a, b) { return b.distinct_rate - a.distinct_rate; }).slice(0, 5);
      outer:
      for (var a = 0; a < hi.length; a++) for (var b = a + 1; b < hi.length; b++) {
        var ia = header.indexOf(hi[a].name), ib = header.indexOf(hi[b].name), seen = {}, uniq = true;
        for (var r2 = 0; r2 < n_rows && r2 < SAMPLE_CAP; r2++) {
          var key = clean(rows[r2][ia]) + "" + clean(rows[r2][ib]);
          if (seen[key]) { uniq = false; break; }
          seen[key] = 1;
        }
        if (uniq) { keys.push([hi[a].name, hi[b].name]); break outer; }
      }
    }

    // duplicate rows + cell density (sampled)
    var seenRow = {}, dup = 0, filled = 0, cap = Math.min(n_rows, SAMPLE_CAP);
    for (var r3 = 0; r3 < cap; r3++) {
      var sig = rows[r3].map(clean).join("");
      if (seenRow[sig]) dup++; else seenRow[sig] = 1;
      for (var c3 = 0; c3 < n_cols; c3++) if (clean(rows[r3][c3]) !== "") filled++;
    }
    return {
      n_rows: n_rows, n_cols: n_cols,
      cell_density: cap && n_cols ? +(filled / (cap * n_cols)).toFixed(4) : 0,
      duplicate_rows: Math.round(dup * (n_rows / Math.max(1, cap))),
      candidate_keys: keys,
      columns: cols,
      tibble: "tibble: " + n_rows.toLocaleString() + " × " + n_cols,
    };
  }

  // Rule-based default classification — the model-off fallback (and seed for the model).
  function ruleClassify(prof) {
    return prof.columns.map(function (c) {
      var cls, why, conf = 0.75;
      var numeric = /(integer|decimal|currency|percent)/.test(c.dtype);
      var idName = c.role_guess === "identifier" || /upc|gtin|sku|zip|phone|_id|number/.test((c.name || "").toLowerCase());
      if (c.dtype === "empty" || c.n_distinct <= 1) { cls = "ignore"; why = "empty or constant"; conf = .95; }
      else if (c.dtype === "date" || c.dtype === "datetime") { cls = "timeseries"; why = "date/datetime column"; conf = .9; }
      else if (c.is_unique && (idName || numeric)) { cls = "identifier"; why = "unique, id-like — not aggregatable"; conf = .85; }
      else if (idName) { cls = numeric && c.distinct_rate > 0.9 ? "identifier" : "dimension"; why = "id/code by name (numeric ≠ measure)"; conf = .8; }
      else if (numeric) { cls = "measure"; why = "numeric, aggregatable"; conf = .7; }
      else { cls = "dimension"; why = "categorical/descriptive"; conf = .7; }
      return { name: c.name, classification: cls, is_primary_timeseries: false, confidence: conf, rationale: why };
    });
  }

  var API = { profile: profile, ruleClassify: ruleClassify, valDtype: valDtype, VERSION: "1" };
  if (typeof window !== "undefined") window.FullRead = API;
  if (typeof module !== "undefined" && module.exports) module.exports = API;   // headless tests
})();
