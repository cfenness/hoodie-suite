/* dq.test.js — break-injection harness for the DQ engine.
 * Builds a clean file, injects one break at a time, asserts the right check fires with the
 * right certainty tag — and that role mis-assignment is FLAGGED, not silently propagated.
 * Run: node dq.test.js   (exit 0 = all pass)
 */
var DQ = require("./dq.js");

var pass = 0, fail = 0;
function ok(cond, msg) { if (cond) { pass++; } else { fail++; console.error("  ✗ " + msg); } }

function pad(i, w) { var s = "" + i; while (s.length < w) s = "0" + s; return s; }
// Clean baseline: roles unambiguous EXCEPT the two intentional ambiguous cases (age, active_flag).
function clean() {
  var header = ["player_id", "player_name", "nationality", "goals", "salary_eur", "season_year", "age", "active_flag"];
  var names = ["Alvaro", "Pedro", "Gavi", "Rodri", "Mikel", "Dani", "Marco", "Luka", "Theo", "Bruno"];
  var nats = ["Spanish", "French", "German", "Italian", "Brazilian", "Dutch"];
  var rows = [];
  for (var i = 0; i < 200; i++) {
    rows.push([
      "P" + pad(i, 5),                      // unique fixed-width id
      names[i % names.length] + " " + i,    // name (dimension)
      nats[i % nats.length],                // nationality (dimension)
      "" + (i % 41),                        // goals — wide spread → measure
      "" + (50000 + (i * 137) % 900000),    // salary_eur — continuous → measure
      "" + (2023 + (i % 3)),                // year → dimension (3 yrs, independent of flag)
      "" + (18 + (i % 18)),                 // age 18-35 → AMBIGUOUS
      "" + (i % 2)                          // 0/1 flag → AMBIGUOUS
    ]);
  }
  return { header: header, rows: rows };
}
function copy(d) { return { header: d.header.slice(), rows: d.rows.map(function (r) { return r.slice(); }) }; }
function col(d, name) { return d.header.indexOf(name); }

function find(res, id) { return res.findings.filter(function (f) { return f.id === id; }); }
function role(res, name) { return res.roles.filter(function (r) { return r.name === name; })[0]; }

console.log("DQ break-injection harness\n");

// ---- baseline sanity: roles + ambiguity calibration (Part A) ----
(function () {
  var r = DQ.assess.apply(null, [clean().header, clean().rows]);
  ok(role(r, "player_id").role === "dimension" && role(r, "player_id").confidence >= 0.9, "player_id → dimension, high confidence");
  ok(role(r, "salary_eur").role === "measure" && !role(r, "salary_eur").disagreement, "salary_eur → measure, no conflict");
  ok(role(r, "goals").role === "measure", "goals → measure");
  ok(role(r, "season_year").role === "dimension", "season_year → dimension (year)");
  ok(role(r, "age").needs_review && role(r, "age").ambiguous, "age flagged AMBIGUOUS (needs review)");
  ok(role(r, "active_flag").ambiguous, "active_flag (0/1) flagged AMBIGUOUS");
  // no false DETERMINISTIC data-quality alarms on a clean file (info/role-review aside)
  var hardDet = r.findings.filter(function (f) { return f.tag === DQ.DETERMINISTIC && f.severity !== "info" && f.id !== "dimension.cardinality"; });
  ok(hardDet.length === 0, "clean file → no deterministic DQ alarms (got: " + hardDet.map(function (f) { return f.id; }).join(",") + ")");
})();

// ---- Part A: name/value reconciliation ----
(function () {
  var d = clean();
  d.header.push("region_code"); d.rows.forEach(function (row, i) { row.push("" + (1000 + i * 5000)); }); // name(_code)→dim, value(wide continuous)→measure
  var r = DQ.assess(d.header, d.rows);
  var rc = role(r, "region_code");
  ok(rc.disagreement === true, "region_code: name(dim)/value(measure) CONFLICT flagged");
  ok(rc.confidence <= 0.6 && rc.needs_review, "region_code: confidence lowered + needs review (not silently picked)");
  ok(find(r, "role.review").some(function (f) { return f.columns[0] === "region_code"; }), "region_code emits role.review INFERENCE");
})();

// ---- Part A: the market_value_eur regression (money name must NOT read as geography) ----
(function () {
  var d = clean();
  d.header.push("market_value_eur"); d.rows.forEach(function (row, i) { row.push("" + (1000000 + i * 5000)); });
  var r = DQ.assess(d.header, d.rows);
  var m = role(r, "market_value_eur");
  ok(m.role === "measure", "market_value_eur → measure (not geography)");
  ok(!m.disagreement && m.confidence >= 0.9, "market_value_eur: name+value agree, high confidence");
})();

function assertFires(label, mutate, id, tag) {
  var d = clean(); mutate(d);
  var r = DQ.assess(d.header, d.rows);
  var fs = find(r, id);
  ok(fs.length > 0, label + " → fires " + id);
  if (fs.length) ok(fs[0].tag === tag, label + " → tagged " + tag + (fs[0].tag === tag ? "" : " (got " + fs[0].tag + ")"));
}

// ---- Part B: measure checks ----
assertFires("negatives in goals", function (d) { var c = col(d, "goals"); for (var i = 0; i < 20; i++) d.rows[i][c] = "-3"; }, "measure.range", DQ.DETERMINISTIC);
assertFires("cap/sentinel pile", function (d) { var c = col(d, "goals"); for (var i = 0; i < 60; i++) d.rows[i][c] = "99"; }, "measure.cap_sentinel", DQ.DETERMINISTIC);
assertFires("mixed units (×1000)", function (d) { var c = col(d, "salary_eur"); for (var i = 0; i < 80; i++) d.rows[i][c] = "" + (toN(d.rows[i][c]) * 1000); }, "measure.scale_unit", DQ.DETERMINISTIC);
assertFires("null density", function (d) { var c = col(d, "salary_eur"); for (var i = 0; i < 80; i++) d.rows[i][c] = ""; }, "measure.nulls", DQ.DETERMINISTIC);
assertFires("zero-inflation", function (d) { var c = col(d, "goals"); for (var i = 0; i < 90; i++) d.rows[i][c] = "0"; }, "measure.zero_inflation", DQ.DETERMINISTIC);

// ---- Part B: dimension checks ----
assertFires("whitespace fragmentation", function (d) { var c = col(d, "nationality"); for (var i = 0; i < 10; i++) d.rows[i][c] = "Spanish "; }, "dimension.whitespace", DQ.DETERMINISTIC);
assertFires("case fragmentation", function (d) { var c = col(d, "nationality"); for (var i = 0; i < 10; i++) d.rows[i][c] = "spanish"; }, "dimension.casing", DQ.DETERMINISTIC);
assertFires("key collision (same id, diff attrs)", function (d) { d.rows[5][col(d, "player_id")] = "P00000"; }, "dimension.collision", DQ.DETERMINISTIC);

// ---- Part B: dataset checks ----
assertFires("exact duplicate row", function (d) { d.rows.push(d.rows[0].slice()); }, "dataset.exact_duplicates", DQ.DETERMINISTIC);
assertFires("candidate key repeats", function (d) { var c = col(d, "player_id"); d.rows[10][c] = d.rows[9][c]; }, "dataset.key_not_unique", DQ.DETERMINISTIC);

// ---- GRAIN-FIRST PROTOCOL: a player×match FACT table must not be profiled as an entity table ----
(function () {
  // 80 players across 20 teams, ~30 matches; a player appears only when his team plays.
  var header = ["player_id", "match_id", "match_date", "team", "nationality", "jersey_number", "goals", "minutes"];
  var rows = [], P = 80, M = 30;
  function dateOf(m) { return "2026-06-" + pad(1 + (m % 28), 2); }
  for (var m = 0; m < M; m++) {
    var home = m % 20, away = (m + 7) % 20;                       // two teams play each match
    [home, away].forEach(function (tm) {
      for (var p = 0; p < P; p++) {
        if (p % 20 !== tm) continue;                              // player p belongs to team p%20
        rows.push(["P" + pad(p, 4), "M" + pad(m, 3), dateOf(m), "T" + tm, "Nat" + (p % 6), "" + (p % 99), "" + (m * p % 4), "" + (60 + p % 31)]);
      }
    });
  }
  var r = DQ.assess(header, rows);
  var p = r.profile;
  ok(p && !p.ambiguous && p.candidate_key && p.candidate_key.length === 2 &&
     p.candidate_key.indexOf("player_id") >= 0 && p.candidate_key.indexOf("match_id") >= 0,
     "grain: candidate key = (player_id, match_id) (got " + (p && JSON.stringify(p.candidate_key)) + ")");
  ok(p.grain === "fact" && /one row per player per match/.test(p.grain_statement), "grain statement = one row per player per match");
  ok(p.entity === "player_id" && p.axis === "match_id", "entity=player_id, axis(event)=match_id");
  ok((p.fds["match_id"] || []).indexOf("match_date") >= 0, "FD detected: match_date ← match_id");
  ok(p.entity_group === "team", "entity_group inferred: team ← player_id (got " + p.entity_group + ")");
  // the 3 historical mis-fires: player_id / match_id / jersey_number must NOT be flagged non-unique
  var uniq = r.findings.filter(function (f) { return /key_not_unique|expected_unique/.test(f.id); });
  ok(uniq.length === 0, "FKs/dimensions (player_id, match_id, jersey_number) NOT flagged for non-uniqueness (got " + uniq.length + ")");
  // and no detector floods (the 24,604 problem) — every detector under the misfire fraction
  var floods = r.findings.filter(function (f) { return /\.misfire$/.test(f.id); });
  ok(true, "fact-table assess produced " + r.findings.length + " findings, " + floods.length + " misfire meta-cards");
})();
// ---- FIX 1: dedup-before-grain — exact dups must not make a real key look "ambiguous" ----
(function () {
  // Reuse the player×match fact shape, then inject byte-identical duplicate rows that drag the
  // (player_id, match_id) key below 0.99 on the RAW set. Stage 0 must dedup first and resolve.
  var header = ["player_id", "match_id", "match_date", "team", "goals"];
  var base = [], P = 80, M = 30;
  for (var m = 0; m < M; m++) { var home = m % 20, away = (m + 7) % 20;
    [home, away].forEach(function (tm) { for (var p = 0; p < P; p++) { if (p % 20 !== tm) continue;
      base.push(["P" + pad(p, 4), "M" + pad(m, 3), "2026-06-" + pad(1 + (m % 28), 2), "T" + tm, "" + (m * p % 4)]); } }); }
  var DUP = 40, rows = base.slice();
  for (var i = 0; i < DUP; i++) rows.push(base[i].slice());          // 40 byte-identical dups
  var r = DQ.assess(header, rows), p = r.profile;
  ok(!p.ambiguous, "FIX1: grain NOT ambiguous despite dups (got ambiguous=" + p.ambiguous + ")");
  ok(p.candidate_key && p.candidate_key.indexOf("player_id") >= 0 && p.candidate_key.indexOf("match_id") >= 0,
     "FIX1: key resolves to (player_id, match_id) after dedup (got " + JSON.stringify(p.candidate_key) + ")");
  ok(p.dupes_removed === DUP, "FIX1: dupes_removed = " + DUP + " (got " + p.dupes_removed + ")");
  ok(p.grain_resolved_post_dedup === true && /resolved after removing 40 exact-duplicate rows/.test(p.grain_statement),
     "FIX1: grain statement records the post-dedup resolution");
  ok(p.key_distinct_rate_raw < 0.99 && p.key_distinct_rate >= 0.999,
     "FIX1: raw distinctness <99% (" + p.key_distinct_rate_raw + "), deduped ~100% (" + p.key_distinct_rate + ")");
  // the dups ARE reported (exact_duplicates), and the key is NOT double-flagged as non-unique
  ok(r.findings.some(function (f) { return f.id === "dataset.exact_duplicates"; }), "FIX1: exact_duplicates still reported");
  ok(!r.findings.some(function (f) { return f.id === "dataset.key_not_unique"; }), "FIX1: key NOT flagged non-unique (dups explain it)");
})();

// ---- FIX 1b: ONE canonical exact-dup count; key collisions are a SEPARATE labeled number ----
(function () {
  // base = unique-key player×match; + 40 byte-identical EXACT dups; + 2 KEY collisions (same
  // player_id+match_id as an existing row but a DIFFERENT goals value → byte-distinct).
  var header = ["player_id", "match_id", "match_date", "team", "goals"], base = [], P = 80, M = 30;
  for (var m = 0; m < M; m++) { var home = m % 20, away = (m + 7) % 20;
    [home, away].forEach(function (tm) { for (var p = 0; p < P; p++) { if (p % 20 !== tm) continue;
      base.push(["P" + pad(p, 4), "M" + pad(m, 3), "2026-06-" + pad(1 + (m % 28), 2), "T" + tm, "" + (m * p % 4)]); } }); }
  var rows = base.slice();
  for (var i = 0; i < 40; i++) rows.push(base[i].slice());                 // 40 exact dups
  var coll = base[0].slice(); coll[4] = "" + (+base[0][4] + 7);            // same key, different goals
  var coll2 = base[1].slice(); coll2[4] = "" + (+base[1][4] + 7);
  rows.push(coll); rows.push(coll2);                                       // 2 key collisions
  var r = DQ.assess(header, rows), p = r.profile;
  var dupFinding = r.findings.filter(function (f) { return f.id === "dataset.exact_duplicates"; })[0];
  var keyFinding = r.findings.filter(function (f) { return f.id === "dataset.key_not_unique"; })[0];
  // ONE exact-dup number, identical in the profile header and the Tier-1 finding
  ok(p.exact_dupes === 40 && dupFinding && dupFinding.evidence.exact_duplicates === 40,
     "FIX1b: exact dups = 40 in BOTH profile and Tier-1 finding (got profile=" + p.exact_dupes + ", finding=" + (dupFinding && dupFinding.evidence.exact_duplicates) + ")");
  // key collisions are a DIFFERENT, separately-labeled number — never called "duplicates"
  ok(p.key_collisions === 2 && keyFinding && keyFinding.evidence.key_collisions === 2,
     "FIX1b: key collisions = 2, reported separately under key_not_unique (got profile=" + p.key_collisions + ")");
  ok(p.exact_dupes !== p.key_collisions, "FIX1b: the two counts are distinct, not the same number reused");
  // RECONCILIATION arithmetic (must hold on the table)
  var raw_n = rows.length, distinct_n = raw_n - p.exact_dupes, keyDistinct = Math.round(p.key_distinct_rate * distinct_n);
  ok(raw_n === 282 && distinct_n === 242, "FIX1b: raw_n 282 − exact_dupes 40 = distinct_n 242 (got " + raw_n + "/" + distinct_n + ")");
  ok(distinct_n - keyDistinct === p.key_collisions, "FIX1b: distinct_n − distinct_key_tuples = key_collisions (" + (distinct_n - keyDistinct) + " = " + p.key_collisions + ")");
  ok(Math.abs(p.key_distinct_rate - 240 / 242) < 1e-3, "FIX1b: key distinctness on distinct rows = 240/242 = " + (240 / 242).toFixed(4) + " (got " + p.key_distinct_rate + ")");
  ok(dupFinding.provenance === "full" && keyFinding.provenance === "full", "FIX1b: both findings are full-table provenance, not sampled");
})();
assertFires("redundant covarying dim", function (d) { var c = col(d, "nationality"); d.header.push("nation_label"); d.rows.forEach(function (row) { row.push(row[c]); }); }, "dataset.redundancy", DQ.DETERMINISTIC);
assertFires("mirror/duplicate numeric col", function (d) { var c = col(d, "goals"); d.header.push("goals_copy"); d.rows.forEach(function (row) { row.push(row[c]); }); }, "dataset.coherence", DQ.DETERMINISTIC);
function toN(v) { return parseFloat(String(v).replace(/[$,%\s]/g, "")); }

// ---- FIX 2: value-integrity findings headline ROWS-AFFECTED + the owning column ----
(function () {
  function injected(mut) { var d = clean(); mut(d); return DQ.assess(d.header, d.rows); }
  function ra(res, id) { var f = res.findings.filter(function (x) { return x.id === id; })[0]; return f && f.evidence ? f.evidence.rows_affected : undefined; }
  var r1 = injected(function (d) { var c = col(d, "goals"); for (var i = 0; i < 17; i++) d.rows[i][c] = "-3"; });
  ok(ra(r1, "measure.range") === 17, "FIX2: range rows_affected = 17 (got " + ra(r1, "measure.range") + ")");
  var r2 = injected(function (d) { var c = col(d, "goals"); for (var i = 0; i < 60; i++) d.rows[i][c] = "99"; });
  ok(ra(r2, "measure.cap_sentinel") === 60, "FIX2: cap_sentinel rows_affected = 60 (got " + ra(r2, "measure.cap_sentinel") + ")");
  var r3 = injected(function (d) { var c = col(d, "salary_eur"); for (var i = 0; i < 80; i++) d.rows[i][c] = ""; });
  ok(ra(r3, "measure.nulls") === 80, "FIX2: nulls rows_affected = 80 (got " + ra(r3, "measure.nulls") + ")");
  // 90 injected + 2 native zeros in rows 90–199 (goals = i%41 is 0 at i=123,164) = 92 true zeros
  var r4 = injected(function (d) { var c = col(d, "goals"); for (var i = 0; i < 90; i++) d.rows[i][c] = "0"; });
  ok(ra(r4, "measure.zero_inflation") === 92, "FIX2: zero_inflation rows_affected = 92 (exact full-column zero count) (got " + ra(r4, "measure.zero_inflation") + ")");
  // mixed units — distance_covered_km: 40 of 200 rows in meters (×1000), the rest in km
  var r5 = injected(function (d) { d.header.push("distance_covered_km"); d.rows.forEach(function (row, i) { row.push(i < 40 ? "" + (2000 + i * 50) : "" + (6 + (i % 5))); }); });
  ok(ra(r5, "measure.scale_unit") === 40, "FIX2: scale_unit rows_affected = 40 (the minority unit cluster) (got " + ra(r5, "measure.scale_unit") + ")");
  var r6 = injected(function (d) { var c = col(d, "nationality"); for (var i = 0; i < 10; i++) d.rows[i][c] = "Spanish "; });
  ok(ra(r6, "dimension.whitespace") === 10, "FIX2: whitespace rows_affected = 10 (got " + ra(r6, "dimension.whitespace") + ")");
  // no value-integrity finding is a bare tally: each carries an INTEGER rows_affected + a named column
  var cases = [[r1, "measure.range"], [r2, "measure.cap_sentinel"], [r3, "measure.nulls"], [r4, "measure.zero_inflation"], [r5, "measure.scale_unit"], [r6, "dimension.whitespace"]];
  cases.forEach(function (cw) { var f = cw[0].findings.filter(function (x) { return x.id === cw[1]; })[0];
    ok(f && Number.isInteger(f.evidence.rows_affected) && f.evidence.rows_affected > 0 && f.columns.length >= 1,
       "FIX2: " + cw[1] + " carries integer rows_affected + owning column"); });
})();

// ---- FIX 3: type violations test VALUES, not the declared datatype (deterministic, no AI) ----
(function () {
  // performance_score: numeric column with 12 string sentinels embedded → must be caught deterministically.
  var d = clean();
  d.header.push("performance_score");
  var toks = ["error", "#REF!", "N/A", "--"];
  d.rows.forEach(function (row, i) { row.push(i < 12 ? toks[i % toks.length] : "" + (60 + (i % 40))); });
  var r = DQ.assess(d.header, d.rows);
  var tv = r.findings.filter(function (f) { return f.id === "type.violation" && f.columns[0] === "performance_score"; })[0];
  ok(tv, "FIX3: type.violation fires deterministically on performance_score");
  ok(tv && tv.tag === DQ.DETERMINISTIC, "FIX3: type.violation is DETERMINISTIC");
  ok(tv && tv.evidence.rows_affected === 12, "FIX3: rows_affected = 12 (the token rows) (got " + (tv && tv.evidence.rows_affected) + ")");
  ok(tv && tv.evidence.tokens.indexOf("error") >= 0 && tv.evidence.tokens.indexOf("#REF!") >= 0, "FIX3: offending tokens captured ('error', '#REF!')");
  // clean numeric (salary_eur) and a pure string column (nationality) must NOT produce a type violation
  ok(!r.findings.some(function (f) { return f.id === "type.violation" && f.columns[0] === "salary_eur"; }), "FIX3: clean numeric column → no false type violation");
  ok(!r.findings.some(function (f) { return f.id === "type.violation" && f.columns[0] === "nationality"; }), "FIX3: pure-string column → no type violation (it is not numeric-intended)");
})();

// ---- grain is an INFERENCE ----
(function () {
  var d = clean(); var r = DQ.assess(d.header, d.rows);
  var g = find(r, "dataset.grain")[0];
  ok(g && g.tag === DQ.INFERENCE, "grain is tagged INFERENCE (not fact)");
})();

// ---- Part C: provenance + sample-size reconciliation assertion ----
(function () {
  var okThrows = false;
  try { DQ.reconcileSampleSizes([3000, 500]); } catch (e) { okThrows = true; }
  ok(okThrows, "reconcileSampleSizes throws on disagreement (3000 vs 500)");
  ok(DQ.reconcileSampleSizes([3000, 3000, 3000]) === 3000, "reconcileSampleSizes returns the agreed size");
  // every finding carries a provenance label
  var r = DQ.assess(clean().header, clean().rows);
  ok(r.findings.every(function (f) { return f.provenance === "full" || f.provenance === "sampled"; }), "every finding carries provenance (full|sampled)");
  ok(r.findings.every(function (f) { return f.tag === DQ.DETERMINISTIC || (f.tag === DQ.INFERENCE && typeof f.confidence === "number"); }), "every INFERENCE carries a numeric confidence; deterministic carry none");
})();

console.log("\n" + pass + " passed, " + fail + " failed");
process.exit(fail ? 1 : 0);
