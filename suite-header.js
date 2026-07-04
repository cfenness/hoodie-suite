/* ============================================================
   suite-header.js — the shared in-app suite header strip.

   Carries the launcher's identity (the hoodie mark, the app's monogram,
   name, and status) INTO each app, so an app opened on its own never looks
   like an orphaned, unrelated product. It mirrors the shell's top bar —
   same mark, same "Hoodie · Suite" wordmark, same control styling — so the
   standalone view and the launcher read as one product.

   Behavior:
     • Embedded in the shell iframe  → renders nothing. The shell's own
       top bar already shows identity, so the strip stays out of the way.
     • Standalone ("Open full ↗")    → stands in as the top bar, with a
       breadcrumb back into the suite and an "Open the suite" control.

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

  // the shell's hoodie mark — kept in lockstep with index.html's top-bar glyph
  var HOODIE = '<svg viewBox="0 0 24 24" width="15" height="15" fill="none" stroke="currentColor" ' +
    'stroke-width="1.9" stroke-linecap="round" stroke-linejoin="round">' +
    '<path d="M4.2 11.8 C6 6, 18 6, 19.8 11.8"/><circle cx="8.2" cy="15.2" r="3.6"/>' +
    '<circle cx="15.8" cy="15.2" r="3.6"/><path d="M11.8 15.2 H12.2"/></svg>';

  var css =
    '#suite-strip{display:flex;align-items:center;gap:11px;min-height:var(--bar,48px);' +
      'padding:0 14px;background:var(--bar-bg,#FFFFFF);color:var(--bar-fg,#243330);' +
      'font-family:var(--font-display),"Space Grotesk",ui-sans-serif,system-ui,sans-serif;' +
      'border-bottom:1px solid var(--bar-hair,#E1E6E4);}' +
    '#suite-strip .ss-home{display:flex;align-items:center;gap:9px;text-decoration:none;color:inherit;cursor:pointer;}' +
    '#suite-strip .ss-logo{width:26px;height:26px;border-radius:4px;background:var(--amber-bright,#34A392);' +
      'color:#fff;display:flex;align-items:center;justify-content:center;flex:0 0 auto;' +
      'transition:transform .14s;}' +
    '#suite-strip .ss-home:hover .ss-logo{transform:rotate(-6deg);}' +
    '#suite-strip .ss-brand{font-weight:600;font-size:14.5px;letter-spacing:-.01em;color:var(--bar-fg,#243330);}' +
    '#suite-strip .ss-brand .sfx{color:var(--bar-mut,#7E8B88);font-weight:400;}' +
    '#suite-strip .ss-sep{color:var(--hair,#D7DDDB);font-family:var(--font-mono,ui-monospace,monospace);font-size:12px;}' +
    '#suite-strip .ss-mark{width:24px;height:24px;border-radius:4px;background:var(--amber,#2C8C7C);color:#fff;' +
      'display:flex;align-items:center;justify-content:center;font-weight:700;font-size:10.5px;flex:0 0 auto;' +
      'font-family:var(--font-display),"Space Grotesk",sans-serif;}' +
    '#suite-strip .ss-name{font-weight:600;font-size:14px;letter-spacing:-.01em;}' +
    '#suite-strip .ss-status{font-family:var(--font-mono,ui-monospace,monospace);font-size:9px;' +
      'letter-spacing:.09em;text-transform:uppercase;padding:2px 8px;border-radius:3px;font-weight:600;' +
      'border:1px solid var(--bar-hair,#E1E6E4);color:var(--bar-mut,#7E8B88);}' +
    '#suite-strip .ss-sp{flex:1;}' +
    '#suite-strip .ss-tbtn{display:flex;align-items:center;gap:6px;font-family:var(--font-mono,ui-monospace,monospace);' +
      'font-size:11px;font-weight:600;letter-spacing:.03em;text-decoration:none;color:var(--amber,#2C8C7C);' +
      'padding:6px 11px;border-radius:4px;border:1px solid var(--bar-hair,#E1E6E4);cursor:pointer;transition:background .12s;}' +
    '#suite-strip .ss-tbtn:hover{background:var(--panel,#EAEEED);}' +
    '#suite-strip .ss-signout{font-family:var(--font-mono,ui-monospace,monospace);font-size:10px;' +
      'letter-spacing:.06em;text-transform:uppercase;text-decoration:none;color:var(--bar-mut,#7E8B88);cursor:pointer;' +
      'padding:5px 10px;border-radius:4px;border:1px solid var(--bar-hair,#E1E6E4);}' +
    '#suite-strip .ss-signout:hover{color:var(--bar-fg,#243330);border-color:var(--ink-soft,#5E6B68);}' +
    '@media (max-width:620px){#suite-strip .ss-brand .sfx{display:none;}#suite-strip .ss-tbtn .lbl{display:none;}}';
  var style = document.createElement('style');
  style.textContent = css;
  document.head.appendChild(style);

  function span(cls, text) { var s = document.createElement('span'); s.className = cls; if (text != null) s.textContent = text; return s; }

  var strip = document.createElement('div');
  strip.id = 'suite-strip';

  // identity: hoodie mark + "Hoodie · Suite" wordmark → back to the launcher
  var home = document.createElement('a');
  home.className = 'ss-home';
  home.href = '../index.html' + (id ? '#' + id : '');
  home.title = 'Back to the Hoodie Suite';
  var logo = span('ss-logo'); logo.innerHTML = HOODIE; home.appendChild(logo);
  var brand = span('ss-brand'); brand.innerHTML = 'Hoodie<span class="sfx"> · Suite</span>'; home.appendChild(brand);
  strip.appendChild(home);

  // breadcrumb: / [MARK] App name [status]
  strip.appendChild(span('ss-sep', '/'));
  if (mark) strip.appendChild(span('ss-mark', mark));
  strip.appendChild(span('ss-name', name));
  if (status) strip.appendChild(span('ss-status', status));

  strip.appendChild(span('ss-sp'));

  // right controls: open the full suite (mirrors the shell's top-bar affordances)
  var open = document.createElement('a');
  open.className = 'ss-tbtn';
  open.href = '../index.html';
  open.title = 'Open the full Hoodie Suite';
  open.innerHTML = '<svg viewBox="0 0 24 24" width="13" height="13" fill="none" stroke="currentColor" ' +
    'stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><rect x="3" y="3" width="7" height="7" rx="1"/>' +
    '<rect x="14" y="3" width="7" height="7" rx="1"/><rect x="3" y="14" width="7" height="7" rx="1"/>' +
    '<rect x="14" y="14" width="7" height="7" rx="1"/></svg><span class="lbl">Open the suite</span>';
  strip.appendChild(open);

  document.body.insertBefore(strip, document.body.firstChild);

  // Sign-out — shown only when a Google login gate is active.
  fetch('/auth/me').then(function (r) { return r.json(); }).then(function (m) {
    if (!(m && m.gated && m.email)) return;
    var out = document.createElement('a');
    out.className = 'ss-signout';
    out.href = '/auth/logout';
    out.textContent = 'Sign out';
    out.title = 'Signed in as ' + m.email + ' — sign out';
    strip.appendChild(out);
  }).catch(function () {});
})();
