/* suite-export.js — shared PNG/PDF export for Hoodie Suite surfaces.
 *
 * One utility used everywhere (My Dashboard, Hoodie Pulls, …) so exports look and behave
 * the same. HoodieExport.toPDF(el, opts) / .toPNG(el, opts) — loads html2canvas + jsPDF
 * from CDN on demand (reusing them if already present) and renders `el` to a download.
 *
 * Fixes the mirrored-export bug: html2canvas can't render CSS 3D (preserve-3d). The
 * dashboard's flip cards put their data-table face on a rotateY(180deg) back, hidden in
 * the browser by backface-visibility — which html2canvas ignores, so it painted the
 * 180°-rotated (mirrored) back over the front and the whole PDF came out backwards. We
 * capture a sanitized CLONE (front faces only, flip transforms stripped) via the onclone
 * hook, leaving the live DOM untouched.
 */
(function () {
  "use strict";
  var H2C = "https://cdnjs.cloudflare.com/ajax/libs/html2canvas/1.4.1/html2canvas.min.js";
  var PDF = "https://cdnjs.cloudflare.com/ajax/libs/jspdf/2.5.1/jspdf.umd.min.js";

  function jsPDFCtor() { return (window.jspdf && window.jspdf.jsPDF) || window.jsPDF || null; }

  function ensure(needPdf, cb) {
    var need = [];
    if (!window.html2canvas) need.push(H2C);
    if (needPdf && !jsPDFCtor()) need.push(PDF);
    if (!need.length) { cb(true); return; }
    var loaded = 0, failed = false;
    need.forEach(function (src) {
      var s = document.createElement("script");
      s.src = src;
      s.onload = function () { loaded++; if (loaded === need.length && !failed) cb(true); };
      s.onerror = function () { if (!failed) { failed = true; cb(false); } };
      document.head.appendChild(s);
    });
  }

  // Neutralize CSS 3D flip cards in the CLONED document so only the (unmirrored) front
  // face renders. Covers the dashboard's card flips and the QBR flip cards; a no-op on
  // surfaces that don't use them (e.g. Hoodie Pulls).
  function sanitize(doc) {
    try {
      doc.querySelectorAll(".flipped").forEach(function (e) { e.classList.remove("flipped"); });
      doc.querySelectorAll(".mydash-card-back,.flip-back,.qbr-flip-back").forEach(function (e) {
        if (e.parentNode) e.parentNode.removeChild(e);
      });
      doc.querySelectorAll(".mydash-card-inner,.flip-card,.qbr-flip-card").forEach(function (e) {
        e.style.transform = "none"; e.style.transformStyle = "flat";
      });
      // generic safety net: any inline rotateY / horizontal flip left over
      doc.querySelectorAll('[style*="rotateY"],[style*="scaleX(-"]').forEach(function (e) {
        e.style.transform = "none";
      });
    } catch (_) {}
  }

  function bgOf(el) {
    try {
      var c = getComputedStyle(el).backgroundColor;
      if (c && c !== "rgba(0, 0, 0, 0)" && c !== "transparent") return c;
    } catch (_) {}
    return "#ffffff";
  }

  function capture(el, ok, err) {
    window.html2canvas(el, {
      backgroundColor: bgOf(el), scale: 2, useCORS: true, logging: false,
      onclone: function (doc) { sanitize(doc); }
    }).then(ok).catch(err || function () {});
  }

  function download(url, name) {
    var a = document.createElement("a");
    a.href = url; a.download = name; document.body.appendChild(a); a.click();
    setTimeout(function () { try { URL.revokeObjectURL(url); a.remove(); } catch (_) {} }, 150);
  }

  function fname(opts) { return (opts && opts.filename) || "hoodie-export"; }

  var HoodieExport = {
    sanitize: sanitize,   // exposed so existing in-page exporters can share the fix
    available: function () { return true; },

    toPNG: function (el, opts) {
      opts = opts || {};
      if (!el) { opts.onerror && opts.onerror("no-element"); return; }
      ensure(false, function (ok) {
        if (!ok || !window.html2canvas) { opts.onerror && opts.onerror("libs"); return; }
        capture(el, function (canvas) {
          canvas.toBlob(function (blob) {
            if (!blob) { opts.onerror && opts.onerror("blob"); return; }
            download(URL.createObjectURL(blob), fname(opts) + ".png");
            opts.ondone && opts.ondone();
          }, "image/png");
        }, function () { opts.onerror && opts.onerror("render"); });
      });
    },

    toPDF: function (el, opts) {
      opts = opts || {};
      if (!el) { opts.onerror && opts.onerror("no-element"); return; }
      ensure(true, function (ok) {
        if (!ok || !window.html2canvas) { opts.onerror && opts.onerror("libs"); return; }
        capture(el, function (canvas) {
          try {
            var JsPDF = jsPDFCtor();
            if (!JsPDF) { opts.onerror && opts.onerror("pdflib"); return; }
            var orient = canvas.width >= canvas.height ? "l" : "p";
            var pdf = new JsPDF({ orientation: orient, unit: "pt", format: "a4" });
            var pw = pdf.internal.pageSize.getWidth(), ph = pdf.internal.pageSize.getHeight(), m = 24;
            var ratio = Math.min((pw - m * 2) / canvas.width, (ph - m * 2) / canvas.height);
            var w = canvas.width * ratio, h = canvas.height * ratio;
            pdf.addImage(canvas.toDataURL("image/png"), "PNG", (pw - w) / 2, (ph - h) / 2, w, h);
            pdf.save(fname(opts) + ".pdf");
            opts.ondone && opts.ondone();
          } catch (e) { opts.onerror && opts.onerror("pdf"); }
        }, function () { opts.onerror && opts.onerror("render"); });
      });
    }
  };

  window.HoodieExport = HoodieExport;
})();
