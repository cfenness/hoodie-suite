/* ============================================================
   Hoodie Spine — the shared backbone for the suite.
   One small file, two roles:
     • In the shell  : HoodieSpine.host({ hierarchy, context })
     • In an app     : HoodieSpine.connect({ name, onContext })
   It carries two things every surface needs and should not re-solve:
     1. the canonical hierarchy  (portfolio → brandFamily → brand → productFamily → sku)
     2. the shared context       (current scope, account, date basis, metric)
   Transport is window.postMessage so iframed apps stay decoupled.
   Backend on-ramp: today host() is handed an inline hierarchy; later it
   fetches /api/hierarchy and the message contract is unchanged.
   ============================================================ */
(function (root) {
  "use strict";
  var TAG = "hoodie:spine", VERSION = "0.1";

  function defaultContext() {
    return {
      scope: null,                 // { id, level, name } — a node in the hierarchy
      account: null,               // { id, name } | null
      dateRange: { basis: "YoY" }, // YoY | YTD | MAT | Latest
      metric: "depletions"
    };
  }

  var role = null;
  var context = defaultContext();
  var hierarchy = null;
  var listeners = { context: [], nav: [], ready: [] };

  function emit(evt, data) {
    (listeners[evt] || []).forEach(function (fn) { try { fn(data); } catch (e) {} });
  }
  function send(win, kind, payload) {
    if (win) win.postMessage({ __spine: TAG, kind: kind, payload: payload, v: VERSION }, "*");
  }

  /* ---------------- HOST (the shell) ---------------- */
  function host(opts) {
    role = "host";
    opts = opts || {};
    hierarchy = opts.hierarchy || null;
    if (opts.context) context = Object.assign(defaultContext(), opts.context);
    window.addEventListener("message", function (e) {
      var d = e.data; if (!d || d.__spine !== TAG) return;
      if (d.kind === "ready") {                 // an app announced itself
        send(e.source, "context", { context: context, hierarchy: hierarchy });
        emit("ready", d.payload);
      } else if (d.kind === "setContext") {     // an app changed shared context
        context = Object.assign({}, context, d.payload);
        broadcast(); emit("context", context);
      } else if (d.kind === "nav") {            // an app requested a cross-app jump
        emit("nav", d.payload);                 // { app, scope }
      }
    });
    return hostApi;
  }
  function broadcast() {
    var frames = document.querySelectorAll("iframe");
    for (var i = 0; i < frames.length; i++) {
      try { send(frames[i].contentWindow, "context", { context: context, hierarchy: hierarchy }); } catch (e) {}
    }
  }
  var hostApi = {
    setContext: function (patch) { context = Object.assign({}, context, patch); broadcast(); emit("context", context); },
    getContext: function () { return context; },
    setHierarchy: function (h) { hierarchy = h; broadcast(); },
    pushTo: function (frameWin) { send(frameWin, "context", { context: context, hierarchy: hierarchy }); },
    on: function (evt, fn) { (listeners[evt] = listeners[evt] || []).push(fn); }
  };

  /* ---------------- APP (an iframe) ---------------- */
  function connect(opts) {
    role = "app";
    opts = opts || {};
    if (opts.onContext) listeners.context.push(opts.onContext);
    window.addEventListener("message", function (e) {
      var d = e.data; if (!d || d.__spine !== TAG) return;
      if (d.kind === "context") {
        context = d.payload.context; hierarchy = d.payload.hierarchy;
        emit("context", context);
      }
    });
    send(parent, "ready", { name: opts.name || document.title });  // handshake
    return appApi;
  }
  var appApi = {
    getContext: function () { return context; },
    getHierarchy: function () { return hierarchy; },
    setContext: function (patch) { send(parent, "setContext", patch); },     // update shared context
    navigate: function (app, scope) { send(parent, "nav", { app: app, scope: scope }); }, // jump to another app
    on: function (evt, fn) { (listeners[evt] = listeners[evt] || []).push(fn); }
  };

  /* ---------------- hierarchy helpers (shared) ---------------- */
  var util = {
    levels: ["portfolio", "brandFamily", "brand", "productFamily", "sku"],
    find: function (root, id) {
      if (!root) return null;
      var stack = root.children ? root.children.slice() : [root];
      while (stack.length) {
        var n = stack.pop();
        if (n.id === id) return n;
        if (n.children) stack = stack.concat(n.children);
      }
      return null;
    },
    path: function (root, id) {  // breadcrumb from top to node
      function walk(node, trail) {
        var t = trail.concat(node);
        if (node.id === id) return t;
        if (node.children) for (var i = 0; i < node.children.length; i++) {
          var r = walk(node.children[i], t); if (r) return r;
        }
        return null;
      }
      if (!root || !root.children) return [];
      for (var i = 0; i < root.children.length; i++) {
        var r = walk(root.children[i], []); if (r) return r;
      }
      return [];
    }
  };

  root.HoodieSpine = { host: host, connect: connect, util: util, VERSION: VERSION };
})(typeof window !== "undefined" ? window : this);
