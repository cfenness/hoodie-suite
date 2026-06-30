/* dq_frontier.test.js — harness for the frontier modules. Scores PRECISION, not just recall:
 * a coverage check that flags the real gap ALONGSIDE false positives FAILS.
 * Run: node dq_frontier.test.js   (exit 0 = all pass)
 */
var DQF = require("./dq_frontier.js");

var pass = 0, fail = 0;
function ok(cond, msg) { if (cond) pass++; else { fail++; console.error("  ✗ " + msg); } }

// Complete panel: 4 entities × 10 seasons, all present (+ a measure for volume weighting).
function panel(entities, seasons) {
  entities = entities || ["Alpha", "Bravo", "Charlie", "Delta"];
  seasons = seasons || [2015, 2016, 2017, 2018, 2019, 2020, 2021, 2022, 2023, 2024];
  var header = ["team", "season", "goals"], rows = [];
  entities.forEach(function (e, ei) { seasons.forEach(function (s) { rows.push([e, "" + s, "" + (10 + ei * 5 + (s % 7))]); }); });
  return { header: header, rows: rows };
}
function drop(d, team, season) { d.rows = d.rows.filter(function (r) { return !(r[0] === team && r[1] === "" + season); }); return d; }
function gapsFor(res) { return res.findings.filter(function (f) { return f.id === "coverage.interior_gap"; }); }

console.log("DQ frontier harness\n");

// ---- entity/period detection ----
(function () {
  var d = panel();
  var det = DQF.detectEntityPeriod(d.header, d.rows, require("./dq.js").assess(d.header, d.rows).roles, {});
  ok(det.entity === "team" && det.period === "season", "detect: entity=team, period=season (got " + det.entity + "/" + det.period + ")");
  var ov = DQF.detectEntityPeriod(d.header, d.rows, require("./dq.js").assess(d.header, d.rows).roles, { entity: "goals" });
  ok(ov.entity === "goals", "user override of entity respected");
})();

// ---- Module 1: interior gap MUST be caught, with precision 1.0 ----
(function () {
  var d = drop(panel(), "Alpha", 2019);           // Alpha present 2018 & 2020, absent 2019 (interior)
  var res = DQF.coverageContinuity(d.header, d.rows);
  var g = gapsFor(res);
  var hit = g.filter(function (f) { return f.evidence.entity === "Alpha" && f.evidence.missing_period === "2019"; });
  var recall = hit.length >= 1 ? 1 : 0;
  var precision = g.length ? hit.length / g.length : 0;
  ok(recall === 1, "interior gap caught (Alpha/2019)");
  ok(precision === 1, "PRECISION 1.0 — only the real gap flagged (got " + g.length + " flags total)");
  ok(g.length && g[0].tag === "INFERENCE", "coverage gap tagged INFERENCE (not fact)");
  ok(hit.length && hit[0].evidence.present_before === "2018" && hit[0].evidence.present_after === "2020", "bracketing evidence: present 2018 & 2020");
})();

// ---- Module 1: tail-only short span MUST NOT be flagged (+ precision under a mixed file) ----
(function () {
  // Complete panel for 4 teams, drop Alpha/2019 (real interior gap), AND add Echo active only
  // 2015-2018 (legitimately shorter span — its missing 2019-2024 is a TAIL, must not flag).
  var d = panel();
  drop(d, "Alpha", 2019);
  [2015, 2016, 2017, 2018].forEach(function (s) { d.rows.push(["Echo", "" + s, "" + (5 + (s % 4))]); });
  var res = DQF.coverageContinuity(d.header, d.rows);
  var g = gapsFor(res);
  var echo = g.filter(function (f) { return f.evidence.entity === "Echo"; });
  ok(echo.length === 0, "Echo's missing tail (2019-2024) NOT flagged — it's a shorter span, not a gap");
  // precision: still exactly the one real interior gap, no flood
  var hit = g.filter(function (f) { return f.evidence.entity === "Alpha" && f.evidence.missing_period === "2019"; });
  ok(g.length === 1 && hit.length === 1, "PRECISION holds with a short-span entity present: exactly 1 flag (got " + g.length + ")");
})();

// ---- Module 1: not-applicable when there's no panel (honest, no false output) ----
(function () {
  var header = ["player_id", "name", "salary"], rows = [];
  for (var i = 0; i < 50; i++) rows.push(["P" + i, "n" + i, "" + (1000 + i)]);
  var res = DQF.coverageContinuity(header, rows);
  ok(res.applicable === false && res.findings.length === 0, "no entity×period panel → applicable:false, no findings");
})();

// ---- Module 2: claims surfaced + MANDATORY empty-hook disclaimer ----
function claimsFile() {
  // year constant 2022, host cities that (per world knowledge) belong to a different event year.
  var header = ["event_year", "host_city", "match"], rows = [];
  var cities = ["Inglewood", "Arlington", "Kansas City"];
  for (var i = 0; i < 60; i++) rows.push(["2022", cities[i % 3], "M" + i]);
  return { header: header, rows: rows };
}
(function () {
  var d = claimsFile();
  var res = DQF.claimsCoherence(d.header, d.rows);   // NO ruleset
  var disc = res.findings.filter(function (f) { return f.id === "claims.no_ruleset"; });
  ok(disc.length === 1, "empty hook → exactly one no_ruleset disclaimer");
  ok(disc.length && disc[0].detail === DQF.NO_RULESET_SENTENCE, "disclaimer uses the mandatory sentence verbatim");
  var combos = res.findings.filter(function (f) { return f.id === "claims.combinations"; });
  ok(combos.length === 1, "claim combinations SURFACED for review (when × where)");
  ok(res.whenCols.indexOf("event_year") >= 0 && res.whereCols.indexOf("host_city") >= 0, "when/where fields detected");
  // honesty: the engine does NOT assert a contradiction on its own
  ok(!res.findings.some(function (f) { return /contradic|wrong|invalid/i.test(f.title) && f.tag === "DETERMINISTIC"; }),
     "no deterministic 'contradiction' asserted without a ruleset");
})();

// ---- Module 2: with a domain ruleset, the external contradiction IS caught ----
(function () {
  var d = claimsFile();
  var ruleset = { source: "WC schedule", locationsByPeriod: { "2022": ["Doha", "Lusail", "Al Khor"] } };
  var res = DQF.claimsCoherence(d.header, d.rows, { ruleset: ruleset });
  var v = res.findings.filter(function (f) { return f.id === "claims.ruleset_location"; });
  ok(v.length >= 1, "ruleset loaded → location-not-valid-for-period contradiction flagged");
  ok(v.length && v[0].tag === "DETERMINISTIC", "ruleset violation is DETERMINISTIC (rule+value inspectable)");
  ok(!res.findings.some(function (f) { return f.id === "claims.no_ruleset"; }), "disclaimer suppressed when a ruleset is loaded");
})();

// ---- Module 1 on a FACT table (the 24,604 bug): event grain, NOT calendar gaps ----
function pad(i, w) { var s = "" + i; while (s.length < w) s = "0" + s; return s; }
function factTable() {
  // 60 players over 20 teams (player p → team p%20); 24 matches, two teams each. A player
  // appears in a match iff his team plays it — sparse fact grain, not a continuity panel.
  var header = ["player_id", "match_id", "match_date", "team", "goals"], rows = [], P = 60, M = 24;
  for (var m = 0; m < M; m++) { var home = m % 20, away = (m + 5) % 20; [home, away].forEach(function (tm) {
    for (var p = 0; p < P; p++) if (p % 20 === tm) rows.push(["P" + pad(p, 3), "M" + pad(m, 2), "2026-06-" + pad(1 + m % 28, 2), "T" + tm, "" + (m * p % 4)]);
  }); }
  return { header: header, rows: rows };
}
(function () {
  var d = factTable(), r = DQF.coverageContinuity(d.header, d.rows);
  ok(r.grain === "event", "fact table → EVENT grain (not a continuity panel) — avg coverage " + r.avg_coverage);
  ok(r.findings.filter(function (f) { return f.id === "coverage.interior_gap"; }).length === 0, "NO calendar/interior gaps on a fact table (rest days ≠ gaps)");
  ok(r.findings.filter(function (f) { return f.id === "coverage.event_gap"; }).length === 0, "clean fact table → 0 event gaps (every expected player present)");
})();
(function () {
  var d = factTable(), removed = false;
  d.rows = d.rows.filter(function (row) { if (!removed && row[0] === "P000" && row[3] === "T0") { removed = true; return false; } return true; });
  var r = DQF.coverageContinuity(d.header, d.rows);
  var eg = r.findings.filter(function (f) { return f.id === "coverage.event_gap"; });
  ok(eg.length >= 1 && eg.some(function (f) { return f.evidence.entity === "P000"; }), "injected event gap caught (P000 absent from a match T0 played)");
  ok(eg.length && eg[0].tag === "INFERENCE", "event gap is INFERENCE");
})();
(function () {
  // dense rosters (4 teams × 15 players × 30 matches), then remove ~half of each player's
  // appearances while always keeping ≥3 teammates per match (so the team still participates).
  // This SHOULD produce a flood of event gaps — which the misfire guard must collapse to one card.
  var header = ["player_id", "match_id", "team", "goals"], rows = [], T = 4, PER = 15, M = 30;
  for (var m = 0; m < M; m++) { var home = m % T, away = (m + 1) % T; if (home === away) away = (home + 2) % T;
    [home, away].forEach(function (tm) { for (var j = 0; j < PER; j++) { var p = tm * PER + j;
      if (j >= 3 && (j + m) % 2 === 0) continue;                       // drop ~half of j≥3; keep j<3
      rows.push(["P" + pad(p, 3), "M" + pad(m, 2), "T" + tm, "" + (m * p % 4)]); } }); }
  var r = DQF.coverageContinuity(header, rows);
  var meta = r.findings.filter(function (f) { return /misfire/.test(f.id); });
  var inst = r.findings.filter(function (f) { return f.id === "coverage.event_gap"; });
  ok(meta.length === 1 && inst.length === 0, "flood → ONE misfire meta-card, instances withheld (meta=" + meta.length + ", inst=" + inst.length + ")");
})();

console.log("\n" + pass + " passed, " + fail + " failed");
process.exit(fail ? 1 : 0);
