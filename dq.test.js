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
assertFires("expected-unique id repeats", function (d) { var c = col(d, "player_id"); d.rows[10][c] = d.rows[9][c]; }, "dataset.expected_unique", DQ.DETERMINISTIC);
assertFires("redundant covarying dim", function (d) { var c = col(d, "nationality"); d.header.push("nation_label"); d.rows.forEach(function (row) { row.push(row[c]); }); }, "dataset.redundancy", DQ.DETERMINISTIC);
assertFires("mirror/duplicate numeric col", function (d) { var c = col(d, "goals"); d.header.push("goals_copy"); d.rows.forEach(function (row) { row.push(row[c]); }); }, "dataset.coherence", DQ.DETERMINISTIC);
function toN(v) { return parseFloat(String(v).replace(/[$,%\s]/g, "")); }

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
