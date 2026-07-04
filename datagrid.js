/* ============================================================
   datagrid.js — the shared power data-grid for the Hoodie suite.

   The backbone of the design alignment: one editable, status-aware grid every page reuses
   (Product Catalog, CRM, MDM, Assortment, Pulls…). Dependency-free, themeable via CSS vars
   (Tableau-crisp defaults), drop-in like spine.js.

     HoodieGrid.mount(el, {
       columns: [{key,label,type,sortable,align,width,format}],   // type: text|num|status|completeness|tags|date
       rows: [{id, ...cellsByKey}],
       idKey:"id", selectable:true, search:true, columnChooser:true,
       rowActions:[{id,label,danger}], bulkActions:[{id,label,danger}],
       onRowAction(id,row), onBulkAction(id,rows), onSort, statusTones:{Label:"good|warn|crit|muted|info"},
     }) -> controller { setRows, getSelected, clearSelection, refresh, root }

   Status pills + completeness meters are first-class cell types — semantic color, separate
   from the brand accent. Numbers are tabular + right-aligned. All input is escaped.
   ============================================================ */
(function (global) {
  var STYLE_ID = "hoodie-grid-style";
  var css =
    ".hg{--hg-ink:#2B3138;--hg-2:#4A525B;--hg-mut:#7A828B;--hg-line:#E4E7EB;--hg-line2:#D3D8DE;" +
      "--hg-grid:#EDF0F2;--hg-surface:#fff;--hg-accent:#4E79A7;--hg-accent-ink:#3B5F86;" +
      "--hg-good:#2E7D57;--hg-good-bg:#E7F2EC;--hg-warn:#B5811F;--hg-warn-bg:#F6EEDD;" +
      "--hg-crit:#C0492F;--hg-crit-bg:#F7E7E3;--hg-info:#4E79A7;--hg-info-bg:#E7EEF5;--hg-mut-bg:#EEF1F4;" +
      "--hg-font:-apple-system,BlinkMacSystemFont,'Segoe UI',system-ui,sans-serif;" +
      "--hg-mono:ui-monospace,'SF Mono',Menlo,monospace;" +
      "font-family:var(--hg-font);color:var(--hg-ink);font-size:14px;display:flex;flex-direction:column;min-height:0}" +
    ".hg *{box-sizing:border-box}" +
    ".hg-bar{display:flex;align-items:center;gap:9px;flex-wrap:wrap;padding:0 0 12px}" +
    ".hg-search{flex:1;min-width:170px;max-width:340px;position:relative}" +
    ".hg-search input{width:100%;padding:8px 12px 8px 32px;border:1px solid var(--hg-line2);border-radius:7px;" +
      "font:inherit;font-size:13.5px;outline:none;background:var(--hg-surface)}" +
    ".hg-search input:focus{border-color:var(--hg-accent)}" +
    ".hg-search svg{position:absolute;left:10px;top:9px;width:14px;height:14px;color:var(--hg-mut)}" +
    ".hg-btn{display:inline-flex;align-items:center;gap:6px;padding:7px 12px;border:1px solid var(--hg-line2);" +
      "border-radius:7px;background:var(--hg-surface);font:inherit;font-size:13px;font-weight:600;color:var(--hg-2);cursor:pointer}" +
    ".hg-btn:hover{border-color:var(--hg-accent);color:var(--hg-ink)}" +
    ".hg-btn.danger{color:var(--hg-crit);border-color:#E4C1B8}" +
    ".hg-btn.danger:hover{background:var(--hg-crit-bg)}" +
    ".hg-count{margin-left:auto;font-family:var(--hg-mono);font-size:12px;color:var(--hg-mut)}" +
    ".hg-scroll{overflow:auto;border:1px solid var(--hg-line);border-radius:10px;background:var(--hg-surface)}" +
    "table.hg-t{border-collapse:separate;border-spacing:0;width:100%;font-size:13.5px}" +
    ".hg-t th,.hg-t td{text-align:left;padding:9px 14px;border-bottom:1px solid var(--hg-grid);white-space:nowrap}" +
    ".hg-t thead th{position:sticky;top:0;z-index:2;background:#EEF2F5;border-bottom:1px solid var(--hg-line);" +
      "font-family:var(--hg-mono);font-size:10.5px;letter-spacing:.06em;text-transform:uppercase;color:var(--hg-mut);font-weight:600}" +
    ".hg-t th.sortable{cursor:pointer;user-select:none}" +
    ".hg-t th.sortable:hover{color:var(--hg-ink)}" +
    ".hg-t th .ar{color:var(--hg-accent);margin-left:4px}" +
    ".hg-t td.num,.hg-t th.num{text-align:right;font-variant-numeric:tabular-nums;font-family:var(--hg-mono);font-size:13px}" +
    ".hg-t tbody tr:hover{background:var(--hg-grid)}" +
    ".hg-t tbody tr.sel{background:var(--hg-info-bg)}" +
    ".hg-t tbody tr:last-child td{border-bottom:0}" +
    ".hg-check{width:34px;text-align:center!important}" +
    ".hg-check input{width:15px;height:15px;accent-color:var(--hg-accent);cursor:pointer;vertical-align:middle}" +
    ".hg-pill{display:inline-flex;align-items:center;gap:5px;font-size:12px;font-weight:700;padding:2px 9px;border-radius:999px;border:1px solid}" +
    ".hg-pill::before{content:'';width:6px;height:6px;border-radius:50%;background:currentColor}" +
    ".hg-pill.good{color:var(--hg-good);background:var(--hg-good-bg);border-color:#BFDDCB}" +
    ".hg-pill.warn{color:var(--hg-warn);background:var(--hg-warn-bg);border-color:#E4CE9C}" +
    ".hg-pill.crit{color:var(--hg-crit);background:var(--hg-crit-bg);border-color:#E4C1B8}" +
    ".hg-pill.info{color:var(--hg-info);background:var(--hg-info-bg);border-color:#BFD0E2}" +
    ".hg-pill.muted{color:var(--hg-mut);background:var(--hg-mut-bg);border-color:var(--hg-line2)}" +
    ".hg-comp{display:inline-flex;align-items:center;gap:8px}" +
    ".hg-comp .track{width:64px;height:6px;border-radius:4px;background:var(--hg-grid);overflow:hidden}" +
    ".hg-comp .fill{height:100%;border-radius:4px}" +
    ".hg-comp .pct{font-family:var(--hg-mono);font-size:12px;color:var(--hg-2);min-width:34px;text-align:right}" +
    ".hg-tag{display:inline-block;font-size:11.5px;font-weight:600;color:var(--hg-2);background:var(--hg-grid);" +
      "border:1px solid var(--hg-line2);border-radius:5px;padding:1px 7px;margin:1px 3px 1px 0}" +
    ".hg-rowbtn{border:0;background:transparent;color:var(--hg-mut);cursor:pointer;font-size:17px;line-height:1;padding:2px 6px;border-radius:5px}" +
    ".hg-rowbtn:hover{background:var(--hg-line);color:var(--hg-ink)}" +
    ".hg-menu{position:absolute;z-index:30;background:var(--hg-surface);border:1px solid var(--hg-line2);" +
      "border-radius:9px;box-shadow:0 8px 26px rgba(43,49,56,.16);padding:5px;min-width:150px}" +
    ".hg-menu button{display:block;width:100%;text-align:left;border:0;background:transparent;font:inherit;font-size:13px;" +
      "padding:7px 10px;border-radius:6px;cursor:pointer;color:var(--hg-2)}" +
    ".hg-menu button:hover{background:var(--hg-grid);color:var(--hg-ink)}" +
    ".hg-menu button.danger{color:var(--hg-crit)}" +
    ".hg-empty{padding:44px 20px;text-align:center;color:var(--hg-mut);font-size:14px}" +
    ".hg-delta{font-family:var(--hg-mono);font-size:12.5px;font-weight:700;white-space:nowrap}" +
    ".hg-delta.up{color:var(--hg-good)} .hg-delta.down{color:var(--hg-crit)} .hg-delta.flat{color:var(--hg-mut)}" +
    ".hg-thumb{display:inline-flex;align-items:center;gap:9px;min-width:0}" +
    ".hg-thumb-img{width:30px;height:30px;flex:0 0 auto;border-radius:6px;background:var(--hg-grid);background-size:cover;background-position:center;" +
      "display:flex;align-items:center;justify-content:center;font-weight:700;color:var(--hg-mut);font-size:12px}" +
    ".hg-thumb-txt{min-width:0;display:flex;flex-direction:column;line-height:1.25}" +
    ".hg-thumb-txt .tt{font-weight:600;color:var(--hg-ink);overflow:hidden;text-overflow:ellipsis;white-space:nowrap}" +
    ".hg-thumb-txt .ts{font-size:11.5px;color:var(--hg-mut)}" +
    ".hg-cc{position:relative}";

  function ensureStyle() {
    if (document.getElementById(STYLE_ID)) return;
    var s = document.createElement("style"); s.id = STYLE_ID; s.textContent = css;
    document.head.appendChild(s);
  }
  function esc(v) { return String(v == null ? "" : v).replace(/[&<>"]/g, function (c) {
    return { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]; }); }

  var FMT = {
    usd: function (v) { var a = Math.abs(+v || 0);
      if (a >= 1e6) return "$" + (v / 1e6).toFixed(1) + "M";
      if (a >= 1e3) return "$" + (v / 1e3).toFixed(1) + "K";
      return "$" + Math.round(v); },
    num: function (v) { return (+v || 0).toLocaleString(); },
    pct: function (v) { return (+v || 0).toFixed(1) + "%"; },
    dec: function (v) { return (+v || 0).toFixed(1); },
  };

  function toneFor(cfg, val) {
    if (val && typeof val === "object" && val.tone) return val.tone;
    var t = (cfg.statusTones || {})[val];
    return t || "muted";
  }
  function compColor(pct) {
    return pct >= 80 ? "var(--hg-good)" : pct >= 50 ? "var(--hg-warn)" : "var(--hg-crit)";
  }

  function mount(el, cfg) {
    ensureStyle();
    cfg = cfg || {};
    var idKey = cfg.idKey || "id";
    var cols = cfg.columns.map(function (c) { return Object.assign({ hidden: false }, c); });
    var rows = (cfg.rows || []).slice();
    var sort = cfg.sort || null;                 // {key, dir}
    var sel = new Set();
    var query = "";
    var openMenu = null;

    var root = el; root.classList.add("hg"); root.innerHTML = "";
    var bar = document.createElement("div"); bar.className = "hg-bar";
    var scroll = document.createElement("div"); scroll.className = "hg-scroll";
    root.appendChild(bar); root.appendChild(scroll);

    function visibleCols() { return cols.filter(function (c) { return !c.hidden; }); }

    function filtered() {
      var out = rows;
      if (query) {
        var q = query.toLowerCase();
        out = out.filter(function (r) {
          return cols.some(function (c) {
            var v = r[c.key];
            if (v == null) return false;
            if (Array.isArray(v)) return v.join(" ").toLowerCase().indexOf(q) >= 0;
            if (typeof v === "object") v = v.label;
            return String(v).toLowerCase().indexOf(q) >= 0;
          });
        });
      }
      if (sort) {
        var c = cols.find(function (x) { return x.key === sort.key; }) || {};
        var numeric = c.type === "num" || c.type === "completeness" || c.type === "delta" || c.type === "heatmap";
        out = out.slice().sort(function (a, b) {
          var av = a[sort.key], bv = b[sort.key];
          if (av && av.value != null) av = av.value; if (bv && bv.value != null) bv = bv.value;
          if (av && av.label != null) av = av.label; if (bv && bv.label != null) bv = bv.label;
          if (numeric) { av = +av || 0; bv = +bv || 0; return sort.dir === "asc" ? av - bv : bv - av; }
          av = String(av == null ? "" : av); bv = String(bv == null ? "" : bv);
          return sort.dir === "asc" ? av.localeCompare(bv) : bv.localeCompare(av);
        });
      }
      return out;
    }

    function cell(r, c) {
      var v = r[c.key];
      if (c.type === "status") {
        if (v == null || v === "") return "";
        var label = v && v.label != null ? v.label : v;
        return '<span class="hg-pill ' + toneFor(cfg, v) + '">' + esc(label) + "</span>";
      }
      if (c.type === "completeness") {
        var p = Math.max(0, Math.min(100, +v || 0));
        return '<span class="hg-comp"><span class="track"><span class="fill" style="width:' + p +
          "%;background:" + compColor(p) + '"></span></span><span class="pct">' + p + "%</span></span>";
      }
      if (c.type === "tags") {
        return (Array.isArray(v) ? v : []).map(function (t) { return '<span class="hg-tag">' + esc(t) + "</span>"; }).join("");
      }
      if (c.type === "num") {
        var f = FMT[c.format] || FMT.num; return f(v);
      }
      if (c.type === "delta") {
        if (v == null || v === "") return "";
        var n = (v && v.value != null) ? v.value : v, up = +n > 0, dn = +n < 0;
        var df = FMT[c.format] || FMT.dec;
        var arrow = up ? "▲" : dn ? "▼" : "→";
        return '<span class="hg-delta ' + (up ? "up" : dn ? "down" : "flat") + '">' + arrow + " " + df(Math.abs(+n)) + "</span>";
      }
      if (c.type === "heatmap") {
        return '<span>' + (FMT[c.format] || FMT.num)(v) + "</span>";   // shading applied on the cell
      }
      if (c.type === "thumbnail") {
        var t = v || {};
        var img = t.src ? ' style="background-image:url(' + esc(t.src) + ')"' : "";
        return '<span class="hg-thumb"><span class="hg-thumb-img"' + img + ">" +
          (t.src ? "" : esc(String(t.title || "?").charAt(0).toUpperCase())) + "</span>" +
          '<span class="hg-thumb-txt"><span class="tt">' + esc(t.title || "") + "</span>" +
          (t.sub ? '<span class="ts">' + esc(t.sub) + "</span>" : "") + "</span></span>";
      }
      return esc(v);
    }

    function heatStyle(c, v) {
      if (c.type !== "heatmap" || v == null) return "";
      var n = +v || 0, mid = c.heatMid == null ? 100 : c.heatMid, max = c.heatMax || mid * 2;
      if (n < mid || max <= mid) return "";
      var p = Math.min(1, (n - mid) / (max - mid));
      return ' style="background:rgba(46,125,87,' + (0.10 + 0.55 * p).toFixed(2) + ')"';
    }

    function renderBar() {
      var h = "";
      if (cfg.search !== false)
        h += '<div class="hg-search"><svg viewBox="0 0 24 24" fill="none"><circle cx="11" cy="11" r="7" stroke="currentColor" stroke-width="2"/><path d="M20 20l-3-3" stroke="currentColor" stroke-width="2" stroke-linecap="round"/></svg>' +
          '<input type="search" placeholder="Search…" value="' + esc(query) + '"></div>';
      if (cfg.columnChooser !== false)
        h += '<div class="hg-cc"><button class="hg-btn" data-cc>⊞ Columns</button></div>';
      // bulk actions appear when rows are selected
      if (cfg.selectable && sel.size && (cfg.bulkActions || []).length) {
        h += (cfg.bulkActions).map(function (a) {
          return '<button class="hg-btn ' + (a.danger ? "danger" : "") + '" data-bulk="' + esc(a.id) + '">' + esc(a.label) + "</button>";
        }).join("");
      }
      h += '<span class="hg-count">' + filtered().length + " row" + (filtered().length !== 1 ? "s" : "") +
        (sel.size ? " · " + sel.size + " selected" : "") + "</span>";
      bar.innerHTML = h;
      var si = bar.querySelector(".hg-search input");
      // Re-render only the rows on each keystroke — NOT the bar (which holds this very
      // input), so typing stays live and focus/caret are preserved.
      if (si) si.oninput = function () { query = si.value; renderTableBody(); };
      var cc = bar.querySelector("[data-cc]");
      if (cc) cc.onclick = function (e) { e.stopPropagation(); toggleColumnChooser(cc.parentNode); };
      bar.querySelectorAll("[data-bulk]").forEach(function (b) {
        b.onclick = function () {
          var picked = rows.filter(function (r) { return sel.has(r[idKey]); });
          if (cfg.onBulkAction) cfg.onBulkAction(b.getAttribute("data-bulk"), picked);
        };
      });
    }
    function syncBarCount() {
      var c = bar.querySelector(".hg-count");
      if (c) c.textContent = filtered().length + " row" + (filtered().length !== 1 ? "s" : "") +
        (sel.size ? " · " + sel.size + " selected" : "");
    }

    function toggleColumnChooser(anchor) {
      var ex = anchor.querySelector(".hg-menu"); if (ex) { ex.remove(); return; }
      var m = document.createElement("div"); m.className = "hg-menu"; m.style.top = "38px"; m.style.left = "0";
      m.innerHTML = cols.map(function (c, i) {
        return '<button data-i="' + i + '">' + (c.hidden ? "☐ " : "☑ ") + esc(c.label) + "</button>";
      }).join("");
      m.querySelectorAll("button").forEach(function (b) {
        b.onclick = function (e) { e.stopPropagation(); var c = cols[+b.getAttribute("data-i")]; c.hidden = !c.hidden;
          b.textContent = (c.hidden ? "☐ " : "☑ ") + c.label; renderTable(); };
      });
      anchor.appendChild(m);
    }

    function renderTable() { renderTableBody(); renderBar(); }
    function renderTableBody() {
      var vis = visibleCols();
      var data = filtered();
      var allSel = cfg.selectable && data.length && data.every(function (r) { return sel.has(r[idKey]); });
      var thead = "<tr>";
      if (cfg.selectable) thead += '<th class="hg-check"><input type="checkbox" data-all ' + (allSel ? "checked" : "") + "></th>";
      var numish = function (c) { return c.type === "num" || c.type === "delta" || c.type === "heatmap"; };
      thead += vis.map(function (c) {
        var cls = (numish(c) ? "num " : "") + (c.sortable ? "sortable" : "");
        var ar = sort && sort.key === c.key ? '<span class="ar">' + (sort.dir === "asc" ? "▲" : "▼") + "</span>" : "";
        return '<th class="' + cls + '"' + (c.sortable ? ' data-sort="' + esc(c.key) + '"' : "") + ">" + esc(c.label) + ar + "</th>";
      }).join("");
      if ((cfg.rowActions || []).length) thead += "<th></th>";
      thead += "</tr>";

      var body = data.map(function (r) {
        var id = r[idKey], on = sel.has(id);
        var tds = "";
        if (cfg.selectable) tds += '<td class="hg-check"><input type="checkbox" data-id="' + esc(id) + '" ' + (on ? "checked" : "") + "></td>";
        tds += vis.map(function (c) { return '<td class="' + (numish(c) ? "num" : "") + '"' + heatStyle(c, r[c.key]) + ">" + cell(r, c) + "</td>"; }).join("");
        if ((cfg.rowActions || []).length) tds += '<td style="text-align:right"><button class="hg-rowbtn" data-row="' + esc(id) + '">⋯</button></td>';
        return '<tr class="' + (on ? "sel" : "") + '">' + tds + "</tr>";
      }).join("");

      scroll.innerHTML = data.length
        ? '<table class="hg-t"><thead>' + thead + "</thead><tbody>" + body + "</tbody></table>"
        : '<div class="hg-empty">' + esc(cfg.emptyText || "Nothing here yet.") + "</div>";
      wireTable();
      syncBarCount();
    }

    function wireTable() {
      scroll.querySelectorAll("th[data-sort]").forEach(function (th) {
        th.onclick = function () {
          var k = th.getAttribute("data-sort");
          sort = sort && sort.key === k ? { key: k, dir: sort.dir === "asc" ? "desc" : "asc" } : { key: k, dir: "asc" };
          if (cfg.onSort) cfg.onSort(sort); renderTable();
        };
      });
      var all = scroll.querySelector("[data-all]");
      if (all) all.onchange = function () {
        var data = filtered();
        if (all.checked) data.forEach(function (r) { sel.add(r[idKey]); });
        else data.forEach(function (r) { sel.delete(r[idKey]); });
        renderTable();
      };
      scroll.querySelectorAll("[data-id]").forEach(function (cb) {
        cb.onchange = function () { var id = cb.getAttribute("data-id");
          if (cb.checked) sel.add(id); else sel.delete(id);
          cb.closest("tr").classList.toggle("sel", cb.checked); renderBar(); syncBarCount(); };
      });
      scroll.querySelectorAll("[data-row]").forEach(function (b) {
        b.onclick = function (e) { e.stopPropagation(); openRowMenu(b, b.getAttribute("data-row")); };
      });
    }

    function openRowMenu(btn, id) {
      if (openMenu) { openMenu.remove(); openMenu = null; }
      var row = rows.find(function (r) { return String(r[idKey]) === String(id); });
      var m = document.createElement("div"); m.className = "hg-menu";
      m.innerHTML = (cfg.rowActions || []).map(function (a) {
        return '<button class="' + (a.danger ? "danger" : "") + '" data-a="' + esc(a.id) + '">' + esc(a.label) + "</button>";
      }).join("");
      var rect = btn.getBoundingClientRect();
      m.style.position = "fixed"; m.style.top = rect.bottom + 4 + "px"; m.style.left = Math.max(8, rect.right - 160) + "px";
      m.querySelectorAll("button").forEach(function (b) {
        b.onclick = function () { m.remove(); openMenu = null; if (cfg.onRowAction) cfg.onRowAction(b.getAttribute("data-a"), row); };
      });
      document.body.appendChild(m); openMenu = m;
    }
    document.addEventListener("click", function () { if (openMenu) { openMenu.remove(); openMenu = null; }
      var ccm = bar.querySelector(".hg-menu"); if (ccm) ccm.remove(); });

    renderBar(); renderTable();

    return {
      root: root,
      setRows: function (r) { rows = r.slice(); sel.forEach(function (id) {
        if (!rows.some(function (x) { return x[idKey] === id; })) sel.delete(id); }); renderTable(); },
      getSelected: function () { return rows.filter(function (r) { return sel.has(r[idKey]); }); },
      clearSelection: function () { sel.clear(); renderTable(); },
      refresh: renderTable,
    };
  }

  global.HoodieGrid = { mount: mount, formatters: FMT };
})(window);
