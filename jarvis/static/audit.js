/* JARVIS audit-log viewer (slice 28) — read-only, dependency-light.
   Fetches the plaintext envelope timeline; the encrypted args/result are
   fetched per-record only when the user clicks "reveal". All record-derived
   text is inserted via textContent / createElement — never innerHTML — so a
   tool/arg value can never inject markup. Never throws out of an event. */
(function () {
  "use strict";
  var $ = function (id) { return document.getElementById(id); };
  var records = [];               // envelopes from the last fetch (file order)

  function el(tag, cls, text) {
    var e = document.createElement(tag);
    if (cls) e.className = cls;
    if (text != null) e.textContent = text;
    return e;
  }

  function statusClass(s) {
    if (s === "ok") return "st-ok";
    if (s === "failed") return "st-failed";
    if (s === "cancelled") return "st-cancelled";
    return "st-other";
  }
  function tierClass(t) {
    return t === "confirm" ? "tier-confirm" : t === "blocked" ? "tier-blocked" : "tier-auto";
  }
  function fmtTs(ts) {
    // "2026-07-19T04:14:03.123456+00:00" -> "2026-07-19 04:14:03"
    return String(ts || "").replace("T", " ").replace(/\.\d+.*$/, "").replace(/[+-]\d\d:\d\d$/, "");
  }

  function applyFilters(rows) {
    var tier = $("fTier").value, status = $("fStatus").value;
    var tool = ($("fTool").value || "").trim().toLowerCase();
    return rows.filter(function (r) {
      if (tier && r.tier !== tier) return false;
      if (status && r.status !== status) return false;
      if (tool && String(r.tool || "").toLowerCase().indexOf(tool) === -1) return false;
      return true;
    });
  }

  function render() {
    var list = $("audit-list");
    list.textContent = "";
    var shown = applyFilters(records);
    $("count").textContent = shown.length + " of " + records.length + " shown";
    $("audit-empty").classList.toggle("hidden", records.length !== 0);
    shown.forEach(function (r) {
      var li = el("li", "audit-row");
      li.dataset.index = r.index;
      var main = el("div", "row-main");
      main.appendChild(el("span", "c-ts", fmtTs(r.ts)));

      var tool = el("span", "c-tool");
      tool.appendChild(el("span", null, r.tool || "?"));
      if (r.dry_run) tool.appendChild(el("span", "dry", "DRY"));
      main.appendChild(tool);

      var tierWrap = el("span", "c-tier");
      tierWrap.appendChild(el("span", "badge " + tierClass(r.tier), r.tier || "?"));
      main.appendChild(tierWrap);

      main.appendChild(el("span", "c-status " + statusClass(r.status), r.status || "?"));
      main.appendChild(el("span", "c-gate", r.gate || "—"));

      var revWrap = el("span", "c-rev");
      if (r.has_payload) {
        var btn = el("button", "reveal-btn", "reveal");
        btn.type = "button";
        btn.addEventListener("click", function () { reveal(li, r.index, btn); });
        revWrap.appendChild(btn);
      } else {
        revWrap.appendChild(el("span", "c-gate", "no data"));
      }
      main.appendChild(revWrap);

      li.appendChild(main);
      list.appendChild(li);
    });
  }

  function reveal(li, index, btn) {
    if (li.querySelector(".payload")) {                 // toggle off
      li.querySelector(".payload").remove();
      btn.textContent = "reveal";
      return;
    }
    btn.disabled = true;
    btn.textContent = "…";
    fetch("/api/audit/" + encodeURIComponent(index) + "/payload")
      .then(function (r) { return r.json(); })
      .then(function (data) { showPayload(li, data); })
      .catch(function () { showPayload(li, { payload: null, payload_error: "couldn't load this record." }); })
      .then(function () { btn.disabled = false; btn.textContent = "hide"; });
  }

  function showPayload(li, data) {
    var box = el("div", "payload");
    if (!data || data.payload == null) {
      box.appendChild(el("span", "plabel", "payload"));
      box.appendChild(el("span", "perror", (data && data.payload_error) || "unavailable."));
      li.appendChild(box);
      return;
    }
    var p = data.payload;
    box.appendChild(el("span", "plabel", "arguments"));
    var argEl = el("div", "pblock");
    try { argEl.textContent = JSON.stringify(p.args, null, 2); }
    catch (e) { argEl.textContent = String(p.args); }
    box.appendChild(argEl);
    box.appendChild(el("span", "plabel", "result"));
    box.appendChild(el("div", "pblock", p.result != null ? String(p.result) : ""));
    if (p.truncated) box.appendChild(el("div", "ptrunc", "…result truncated (" + p.result_len + " chars total)"));
    li.appendChild(box);
  }

  function load() {
    var tail = $("fTail").value || "200";
    $("count").textContent = "loading…";
    fetch("/api/audit?tail=" + encodeURIComponent(tail))
      .then(function (r) { return r.json(); })
      .then(function (data) { records = (data && data.records) || []; render(); })
      .catch(function () { records = []; render(); $("count").textContent = "couldn't load the audit log."; });
  }

  ["fTier", "fStatus", "fTool"].forEach(function (id) {
    $(id).addEventListener("input", render);
  });
  $("fTail").addEventListener("change", load);
  $("refresh").addEventListener("click", load);
  document.addEventListener("DOMContentLoaded", load);
  if (document.readyState !== "loading") load();
})();
