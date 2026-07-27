/* session-guard.js — auto-logout on an expired/invalid session.
 *
 * Every /api/* call 401s with {ok:false, error:"unauthorized"} once the Google OIDC
 * session cookie (unifyd/auth_gate.py) or the AGENT_TOKEN gate rejects a request. Left
 * alone, that just shows up as an inline error in whatever app made the call (e.g. Order
 * Hub's upload rows say "could not parse" / "unauthorized") while the rest of the suite
 * quietly stops working. Patching fetch here means ANY app that includes this script
 * bounces the user straight back to sign-in the moment a session goes stale, instead of
 * leaving them staring at a pile of misleading per-request errors.
 *
 * Include with: <script src="spine/session-guard.js"></script> (adjust the relative path
 * for apps nested outside apps/). Safe to include from inside an iframe — it redirects
 * window.top so the whole suite re-authenticates, not just the iframed app.
 */
(function () {
  if (window.__hoodieSessionGuard) return;
  window.__hoodieSessionGuard = true;

  var redirecting = false;
  var origFetch = window.fetch;
  if (typeof origFetch !== "function") return;

  window.fetch = function (input, init) {
    return origFetch.apply(this, arguments).then(function (res) {
      try {
        if (res.status === 401 && !redirecting) {
          var raw = typeof input === "string" ? input : (input && input.url) || "";
          var path = new URL(raw, location.href).pathname;
          if (path.slice(0, 5) === "/api/") {
            redirecting = true;
            (window.top || window).location.href = "/auth/logout";
          }
        }
      } catch (e) { /* malformed URL, cross-origin request, etc. — never block the real response */ }
      return res;
    });
  };
})();
