#!/usr/bin/env node
/* Pre-deploy guard: syntax-check every inline <script> in index.html + apps/*.html.
 *
 * A single syntax error in the launcher's inline APPS script silently blanks the whole
 * app grid (it fails to parse, no console error) — that shipped once as a "site down".
 * This catches that class of bug before it deploys. Run locally or in CI:  node scripts/check_html_js.js
 */
const fs = require("fs");
const path = require("path");

const root = path.resolve(__dirname, "..");
const files = ["index.html"];
const appsDir = path.join(root, "apps");
if (fs.existsSync(appsDir)) {
  for (const f of fs.readdirSync(appsDir)) if (f.endsWith(".html")) files.push("apps/" + f);
}

let failures = 0, checked = 0;
const reScript = /<script(?![^>]*\bsrc=)([^>]*)>([\s\S]*?)<\/script>/g;

for (const rel of files) {
  const abs = path.join(root, rel);
  let html;
  try { html = fs.readFileSync(abs, "utf8"); } catch { continue; }
  let m, idx = 0;
  while ((m = reScript.exec(html))) {
    const attrs = m[1] || "";
    // only check ACTUAL JavaScript: absent type, or text/application javascript. Skip non-JS script blocks —
    // importmap + application/json are JSON, and `type=module` can't be validated by `new Function` (import/export).
    const tm = /type\s*=\s*["']?([^"'\s>]+)/i.exec(attrs);
    const type = tm ? tm[1].toLowerCase() : "";
    if (!["", "text/javascript", "application/javascript"].includes(type)) { idx++; continue; }
    const code = m[2];
    if (!code.trim()) { idx++; continue; }
    checked++;
    try {
      new Function(code); // throws SyntaxError on a parse error — exactly the launcher-blanking bug
    } catch (e) {
      const line = html.slice(0, m.index).split("\n").length;
      console.error(`✗ ${rel}  (inline <script> #${idx}, starts ~line ${line}): ${e.message}`);
      failures++;
    }
    idx++;
  }
}

if (failures) {
  console.error(`\n${failures} inline-script syntax error(s) across ${files.length} HTML files — refusing to proceed.`);
  process.exit(1);
}
console.log(`✓ ${checked} inline HTML scripts across ${files.length} files parse cleanly`);
