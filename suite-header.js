/* ============================================================
   suite-header.js — the shared in-app suite header strip.

   Carries the launcher's identity (the "hoodie suite" mark, the app's
   monogram, name, and status) INTO each app, so an app opened on its own
   never looks like an orphaned, unrelated product.

   Behavior:
     • Embedded in the shell iframe  → renders nothing. The shell's own
       top bar already shows identity, so the strip stays out of the way.
     • Standalone ("Open full ↗")    → stands in as a top banner with a
       link back to the suite.

   It is inserted in normal document flow as <body>'s first child, so it
   never fights an app's own sticky/fixed header — it simply scrolls away
   and the app's chrome takes over.

   Usage (paste once, just before </body>):
     <script src="../suite-header.js"
             data-id="crm" data-mark="HR"
             data-name="Hoodie Relations" data-status="beta"></script>
   Styling rides on suite.css tokens (with hard fallbacks).
   ============================================================ */
(function () {
  if (window.self !== window.top) return;          // embedded → the shell bar covers identity
  if (document.getElementById('suite-strip')) return;
  var me = document.currentScript;
  var d = (me && me.dataset) || {};
  var mark = d.mark || '', name = d.name || document.title, status = d.status || '', id = d.id || '';

  var css =
    '#suite-strip{display:flex;align-items:center;gap:11px;min-height:var(--bar,48px);' +
      'padding:0 16px;background:var(--ink,#1B1A17);color:var(--paper,#EEEDE8);' +
      'font-family:var(--font-display),ui-sans-serif,system-ui,sans-serif;' +
      'border-bottom:1px solid rgba(0,0,0,.25);}' +
    '#suite-strip .ss-home{display:flex;align-items:center;gap:8px;text-decoration:none;color:inherit;cursor:pointer;}' +
    '#suite-strip .ss-logo{width:23px;height:23px;border-radius:4px;background:var(--amber-bright,#DCA138);' +
      'color:var(--ink,#1B1A17);display:flex;align-items:center;justify-content:center;font-weight:700;font-size:13px;}' +
    '#suite-strip .ss-suite{font-family:var(--font-mono,ui-monospace,monospace);font-size:11px;letter-spacing:.16em;' +
      'text-transform:uppercase;color:#C9C2B4;}' +
    '#suite-strip .ss-home:hover .ss-suite{color:var(--paper,#EEEDE8);}' +
    '#suite-strip .ss-sep{color:#4A463F;}' +
    '#suite-strip .ss-mark{width:23px;height:23px;border-radius:4px;background:var(--amber,#B07012);color:#fff;' +
      'display:flex;align-items:center;justify-content:center;font-weight:700;font-size:10.5px;flex:0 0 auto;}' +
    '#suite-strip .ss-name{font-weight:600;font-size:14px;letter-spacing:-.01em;}' +
    '#suite-strip .ss-status{margin-left:auto;font-family:var(--font-mono,ui-monospace,monospace);font-size:9.5px;' +
      'letter-spacing:.09em;text-transform:uppercase;padding:3px 9px;border-radius:999px;' +
      'border:1px solid rgba(201,194,180,.32);color:#C9C2B4;}' +
    '#suite-strip .ss-signout{font-family:var(--font-mono,ui-monospace,monospace);font-size:10px;' +
      'letter-spacing:.06em;text-transform:uppercase;text-decoration:none;color:#C9C2B4;cursor:pointer;' +
      'padding:3px 10px;border-radius:999px;border:1px solid rgba(201,194,180,.32);}' +
    '#suite-strip .ss-signout:hover{color:var(--paper,#EEEDE8);border-color:rgba(201,194,180,.6);}';
  var style = document.createElement('style');
  style.textContent = css;
  document.head.appendChild(style);

  function span(cls, text) { var s = document.createElement('span'); s.className = cls; if (text != null) s.textContent = text; return s; }

  var strip = document.createElement('div');
  strip.id = 'suite-strip';

  var home = document.createElement('a');
  home.className = 'ss-home';
  home.href = '../index.html' + (id ? '#' + id : '');
  home.title = 'Back to the Hoodie Suite';
  home.appendChild(span('ss-logo', 'h'));
  home.appendChild(span('ss-suite', 'hoodie suite'));
  strip.appendChild(home);

  strip.appendChild(span('ss-sep', '/'));
  if (mark) strip.appendChild(span('ss-mark', mark));
  strip.appendChild(span('ss-name', name));
  if (status) strip.appendChild(span('ss-status', status));

  document.body.insertBefore(strip, document.body.firstChild);

  // Sign-out — shown only when a Google login gate is active. If there's no status pill
  // to hold the right edge, the link claims it (margin-left:auto).
  fetch('/auth/me').then(function (r) { return r.json(); }).then(function (m) {
    if (!(m && m.gated && m.email)) return;
    var out = document.createElement('a');
    out.className = 'ss-signout';
    out.href = '/auth/logout';
    out.textContent = 'Sign out';
    out.title = 'Signed in as ' + m.email + ' — sign out';
    if (!status) out.style.marginLeft = 'auto';
    else out.style.marginLeft = '10px';
    strip.appendChild(out);
  }).catch(function () {});
})();
