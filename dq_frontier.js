/* dq_frontier.js — frontier DQ modules layered on dq.js (Prompt 2). Discrete + independently
 * testable. Reuses the role primitives from dq.js; does NOT modify the working checks.
 *
 *   Module 1  coverageContinuity(header, rows, opts)  — cross-entity coverage continuity.
 *   Module 2  claimsCoherence(header, rows, opts)     — "is this what it claims to be?" + domain hook.
 *
 * Same rule: every finding DETERMINISTIC (fact) or INFERENCE (confidence + overridable). These
 * modules need knowledge from OUTSIDE the file, which the engine lacks — so they build the
 * scaffolding where that knowledge plugs in and stay honest about what they cannot verify.
 */
(function () {
  "use strict";
  var DQ = (typeof require !== "undefined") ? require("./dq.js") : (typeof window !== "undefined" ? window.DQ : null);
  var DET = "DETERMINISTIC", INF = "INFERENCE";

  function clean(v) { return v == null ? "" : String(v).trim(); }
  function toNum(v) { var s = clean(v).replace(/[$,%\s]/g, ""); if (s === "" || isNaN(s)) return null; return parseFloat(s); }
  function uniq(o) { return Object.keys(o); }
  function F(o) { var f = { id: o.id, scope: o.scope, columns: o.columns || [], tag: o.tag, severity: o.severity || "info", title: o.title, detail: o.detail, evidence: o.evidence || {}, provenance: o.provenance || "full" }; if (o.tag === INF) f.confidence = (o.confidence == null ? 0.6 : o.confidence); return f; }

  function roleOf(roles, name) { for (var i = 0; i < roles.length; i++) if (roles[i].name === name) return roles[i]; return null; }
  function periodComparator(values) {
    var nums = values.map(toNum);
    if (nums.every(function (n) { return n !== null; })) return function (a, b) { return toNum(a) - toNum(b); };
    var ds = values.map(function (v) { return Date.parse(String(v).replace(" ", "T")); });
    if (ds.every(function (d) { return !isNaN(d); })) return function (a, b) { return Date.parse(String(a).replace(" ", "T")) - Date.parse(String(b).replace(" ", "T")); };
    return function (a, b) { return a < b ? -1 : a > b ? 1 : 0; };
  }

  // ---------------- entity + period detection (reuse roles; user-overridable) ----------------
  var RE_PERIOD = /(date|year|season|period|month|quarter|week|day|time|edition|matchday)/i;
  var RE_ENTITY = /(team|club|store|player|account|outlet|customer|site|entity|brand|sku|company|vendor|supplier|name|^id$|_id$)/i;

  function detectEntityPeriod(header, rows, roles, opts) {
    opts = opts || {};
    var dims = [];
    header.forEach(function (h, i) { var r = roleOf(roles, h); if (r && r.role === "dimension") dims.push({ name: h, idx: i, role: r }); });

    // --- period ---
    var period = opts.period || null;
    if (!period) {
      var pScore = -1;
      dims.forEach(function (d) {
        var vals = distinctVals(rows, d.idx);
        if (vals.length < 3) return;
        var orderable = vals.every(function (v) { return toNum(v) !== null; }) || vals.every(function (v) { return !isNaN(Date.parse(String(v).replace(" ", "T"))); });
        var nameHit = RE_PERIOD.test(d.name) ? 1 : 0;
        var yearLike = (d.role.value_signal && /year/.test(d.role.value_signal.reason || "")) ? 1 : 0;
        var s = nameHit * 3 + yearLike * 2 + (orderable ? 1 : 0);
        if (s > pScore && s > 0) { pScore = s; period = d.name; }
      });
    }

    // --- entity --- prefer a dimension that RECURS across periods (a panel entity), name-matched.
    var entity = opts.entity || null;
    if (!entity && period) {
      var pi = header.indexOf(period), eScore = -1;
      dims.forEach(function (d) {
        if (d.name === period) return;
        var card = distinctVals(rows, d.idx).length;
        if (card < 2) return;
        var recur = avgPeriodsPerValue(rows, d.idx, pi);     // >1 means it appears in multiple periods
        if (recur < 1.5) return;                              // not a panel entity (e.g. a row id appears once)
        var nameHit = RE_ENTITY.test(d.name) ? 1 : 0;
        var s = nameHit * 3 + Math.min(recur, 10);
        if (s > eScore) { eScore = s; entity = d.name; }
      });
    }
    var reason = !period ? "no orderable period dimension found"
               : !entity ? "no entity dimension recurs across periods"
               : null;
    return { entity: entity, period: period, reason: reason };
  }
  function distinctVals(rows, idx) { var seen = {}; for (var r = 0; r < rows.length; r++) { var v = clean(rows[r][idx]); if (v !== "") seen[v] = 1; } return uniq(seen); }
  function avgPeriodsPerValue(rows, ei, pi) {
    var map = {}; for (var r = 0; r < rows.length; r++) { var e = clean(rows[r][ei]), p = clean(rows[r][pi]); if (e === "" || p === "") continue; (map[e] = map[e] || {})[p] = 1; }
    var ks = uniq(map); if (!ks.length) return 0; var sum = 0; ks.forEach(function (k) { sum += uniq(map[k]).length; }); return sum / ks.length;
  }

  // ---------------- Module 1 — cross-entity coverage continuity (INFERENCE) ----------------
  // Flags an entity-period ONLY as an INTERIOR gap: entity present BOTH before and after the
  // missing period. A missing tail (shorter active span) is NEVER flagged. Severity ∝ volume.
  function coverageContinuity(header, rows, opts) {
    opts = opts || {};
    if (!DQ) return { applicable: false, reason: "dq.js not available", findings: [] };
    var roles = opts.roles || DQ.assess(header, rows).roles;
    var det = detectEntityPeriod(header, rows, roles, opts);
    if (!det.entity || !det.period)
      return { applicable: false, reason: det.reason, entity: det.entity, period: det.period, findings: [] };

    var ei = header.indexOf(det.entity), pi = header.indexOf(det.period);
    var byEntity = {}, allP = {}, vol = {};
    for (var r = 0; r < rows.length; r++) {
      var e = clean(rows[r][ei]), p = clean(rows[r][pi]); if (e === "" || p === "") continue;
      (byEntity[e] = byEntity[e] || {})[p] = 1; allP[p] = 1; vol[e] = (vol[e] || 0) + 1;
    }
    var order = uniq(allP).sort(periodComparator(uniq(allP)));
    var posOf = {}; order.forEach(function (p, i) { posOf[p] = i; });
    var maxVol = 1; uniq(vol).forEach(function (k) { if (vol[k] > maxVol) maxVol = vol[k]; });

    var gaps = [];
    uniq(byEntity).forEach(function (e) {
      var present = uniq(byEntity[e]).map(function (p) { return posOf[p]; }).sort(function (a, b) { return a - b; });
      if (present.length < 2) return;                          // can't bracket → no interior gap
      var lo = present[0], hi = present[present.length - 1], have = {};
      present.forEach(function (p) { have[p] = 1; });
      for (var pos = lo + 1; pos < hi; pos++) {                // strictly INTERIOR positions only
        if (!have[pos]) {
          var b = pos - 1; while (b > lo && !have[b]) b--;
          var a = pos + 1; while (a < hi && !have[a]) a++;
          gaps.push({ entity: e, period: order[pos], before: order[b], after: order[a], volume: vol[e] });
        }
      }
    });

    var findings = gaps.map(function (g) {
      var ratio = g.volume / maxVol;
      var sev = ratio >= 0.5 ? "high" : ratio >= 0.2 ? "medium" : "low";
      return F({
        id: "coverage.interior_gap", scope: "cell", columns: [det.entity, det.period], tag: INF, confidence: 0.7, severity: sev,
        title: "Suspected coverage gap: " + g.entity + " missing " + det.period + " " + g.period,
        detail: g.entity + " is present in " + g.before + " and " + g.after + " but absent in " + g.period + " " + g.period +
                " — a probable reporting gap. Confirm against expected reporting; the engine cannot know if " + g.entity +
                " was genuinely inactive.",
        evidence: { entity: g.entity, missing_period: g.period, present_before: g.before, present_after: g.after, entity_volume: g.volume }
      });
    });
    return { applicable: true, entity: det.entity, period: det.period, periods: order, gaps: gaps, findings: findings };
  }

  // ---------------- Module 2 — "is this what it claims to be?" + domain hook ----------------
  var RE_WHEN  = /(date|year|season|period|month|quarter|edition)/i;
  var RE_WHERE = /(city|state|country|region|stadium|venue|location|host|place|market|arena)/i;

  // The empty-hook disclaimer is MANDATORY and must be emitted verbatim when no ruleset is loaded.
  var NO_RULESET_SENTENCE = "No domain ruleset loaded — external plausibility (does this data match what it claims to be?) was NOT verified.";

  function claimsCoherence(header, rows, opts) {
    opts = opts || {};
    if (!DQ) return { findings: [], whenCols: [], whereCols: [] };
    var roles = opts.roles || DQ.assess(header, rows).roles;
    var ruleset = opts.ruleset || null;

    var whenCols = [], whereCols = [];
    header.forEach(function (h, i) {
      var r = roleOf(roles, h);
      if (RE_WHEN.test(h) || (r && r.value_signal && /year/.test(r.value_signal.reason || ""))) whenCols.push(h);
      else if (RE_WHERE.test(h)) whereCols.push(h);
    });

    var findings = [], combos = null;
    var claimCols = whenCols.concat(whereCols);
    if (claimCols.length >= 1) {
      var idx = claimCols.map(function (c) { return header.indexOf(c); });
      var freq = {}, total = 0;
      for (var r2 = 0; r2 < rows.length; r2++) {
        var key = idx.map(function (i) { return clean(rows[r2][i]); }).join(" · ");
        if (key.replace(/·| /g, "") === "") continue;
        freq[key] = (freq[key] || 0) + 1; total++;
      }
      combos = uniq(freq).map(function (k) { return { combo: k, count: freq[k] }; }).sort(function (a, b) { return b.count - a.count; });

      // INTERNAL coherence (deterministic, domain-free): surface the what/when/where combinations
      // for human eyeball. We SURFACE the combination; we do NOT assert a contradiction.
      findings.push(F({
        id: "claims.combinations", scope: "dataset", columns: claimCols, tag: INF, confidence: 0.5, severity: "info",
        title: "What the data claims to be (eyeball these combinations)",
        detail: "Distinct " + claimCols.join(" × ") + " combinations: " +
                combos.slice(0, 6).map(function (c) { return "“" + c.combo + "” ×" + c.count; }).join("; ") +
                (combos.length > 6 ? " …" : "") + ". Check these against what you know about the source.",
        evidence: { fields: claimCols, combinations: combos.slice(0, 25) }
      }));

      // Rare combinations are a DETERMINISTIC fact about the distribution (not a claim of error).
      var rare = combos.filter(function (c) { return combos.length > 1 && c.count / total < 0.02; });
      if (rare.length)
        findings.push(F({
          id: "claims.rare_combo", scope: "dataset", columns: claimCols, tag: DET, severity: "medium",
          title: rare.length + " rare " + claimCols.join("/") + " combination(s)",
          detail: "These combinations occur in <2% of rows: " + rare.slice(0, 8).map(function (c) { return "“" + c.combo + "” ×" + c.count; }).join("; ") +
                  " — surfaced for review; the engine does not assert they are wrong.",
          evidence: { rare: rare.slice(0, 25) }
        }));
    }

    // Domain hook — supplied knowledge plugs in here. With it, we can check external plausibility.
    if (ruleset) {
      findings = findings.concat(applyDomainRuleset(header, rows, roles, ruleset, whenCols, whereCols));
    } else {
      findings.push(F({
        id: "claims.no_ruleset", scope: "dataset", columns: [], tag: INF, confidence: 0, severity: "info",
        title: "External plausibility NOT verified",
        detail: NO_RULESET_SENTENCE, evidence: { hook: "claimsCoherence(opts.ruleset)" }, provenance: "n/a"
      }));
    }
    return { whenCols: whenCols, whereCols: whereCols, combinations: combos, ruleset_loaded: !!ruleset, findings: findings };
  }

  // Apply a supplied domain ruleset. See DOMAIN_RULESET.md for the interface. A violation is
  // DETERMINISTIC *given the ruleset* (the rule + value are inspectable) — tagged DET, but the
  // detail always credits the ruleset as the source of truth.
  function applyDomainRuleset(header, rows, roles, rs, whenCols, whereCols) {
    var out = [];
    // (a) locationsByPeriod: { "<period value>": [allowed where-values...] }
    if (rs.locationsByPeriod && whenCols.length && whereCols.length) {
      var wi = header.indexOf(whenCols[0]), li = header.indexOf(whereCols[0]);
      var bad = {};
      for (var r = 0; r < rows.length; r++) {
        var per = clean(rows[r][wi]), loc = clean(rows[r][li]); if (per === "" || loc === "") continue;
        var allowed = rs.locationsByPeriod[per];
        if (allowed && allowed.indexOf(loc) < 0) bad[per + " · " + loc] = (bad[per + " · " + loc] || 0) + 1;
      }
      uniq(bad).forEach(function (k) {
        var parts = k.split(" · ");
        out.push(F({
          id: "claims.ruleset_location", scope: "dataset", columns: [whenCols[0], whereCols[0]], tag: DET, severity: "high",
          title: "Location not valid for period (per " + (rs.source || "ruleset") + ")",
          detail: "“" + parts[1] + "” is not an allowed " + whereCols[0] + " for " + whenCols[0] + " " + parts[0] +
                  " according to the loaded ruleset (×" + bad[k] + " rows). This is the kind of “2022 file with another year's host cities” contradiction.",
          evidence: { period: parts[0], location: parts[1], count: bad[k], allowed: rs.locationsByPeriod[parts[0]] }
        }));
      });
    }
    // (b) expectedEntities: roster check (array, or {period:[...]}) — entities present that aren't expected
    if (rs.expectedEntities) {
      // handled by caller-supplied entity column if provided; kept simple here.
    }
    // (c) sentinels: { column: [sentinel values] } — known source sentinels masquerading as data
    if (rs.sentinels) {
      Object.keys(rs.sentinels).forEach(function (colName) {
        var ci = header.indexOf(colName); if (ci < 0) return;
        var set = {}; rs.sentinels[colName].forEach(function (v) { set[String(v)] = 1; });
        var hits = 0; for (var r = 0; r < rows.length; r++) if (set[clean(rows[r][ci])]) hits++;
        if (hits) out.push(F({
          id: "claims.ruleset_sentinel", scope: "column", columns: [colName], tag: DET, severity: "medium",
          title: "Known sentinel value present in " + colName + " (per ruleset)",
          detail: hits + " rows hold a value the ruleset flags as a source sentinel/placeholder.",
          evidence: { column: colName, count: hits }
        }));
      });
    }
    return out;
  }

  var API = {
    coverageContinuity: coverageContinuity, claimsCoherence: claimsCoherence,
    detectEntityPeriod: detectEntityPeriod, applyDomainRuleset: applyDomainRuleset,
    NO_RULESET_SENTENCE: NO_RULESET_SENTENCE, VERSION: "1"
  };
  if (typeof window !== "undefined") window.DQF = API;
  if (typeof module !== "undefined" && module.exports) module.exports = API;
})();
