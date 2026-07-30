// Pure logic for the JARVIS bridge — no Chrome APIs, no I/O.
//
// WHY THIS FILE EXISTS. Two reasons, both learned the hard way:
//
// 1. TESTABILITY. Playwright cannot reach an MV3 extension's service worker
//    (context.service_workers is empty for extensions), so anything living
//    only in background.js can be tested by static text-matching at best. The
//    pinned-tab bug got shipped because "may I touch this tab?" was an inline
//    expression nobody could unit-test. Pure functions here can be loaded into
//    a page and tested against fabricated inputs.
//
// 2. ANTI-DRIFT. `matchClickable` is a port of web.py's `_match_clickable`, and
//    the CONFIRM tier is computed from the element name it resolves. Two
//    independent copies of that scoring would drift silently, and a drifted
//    name means a WRONG TIER on the user's real logged-in accounts. Keeping it
//    in one loadable file means the eval harness can run this exact code
//    against Playwright's and score the agreement.

// The JARVIS HUD lives on 127.0.0.1:8000. Navigating THAT tab away destroys the
// conversation transcript (the v1.0.2 bug, in a new costume).
const isHud = (t) =>
  !!t && /^https?:\/\/(127\.0\.0\.1|localhost):8000(\/|$)/.test(t.url || "");

const isWebPage = (t) => !!t && /^https?:/.test(t.url || "");

// ONE predicate for "may JARVIS navigate this tab away?".
//
// NOT protected, stated rather than implied: tab groups, audible/playing tabs,
// and tabs with unsaved form input. Those stay safe only because JARVIS never
// navigates a tab it did not open — which is the tracked-own-tab rule.
function isProtected(tab) {
  if (!tab) return true;            // unknown -> refuse
  if (tab.pinned) return true;      // the user deliberately kept it
  if (isHud(tab)) return true;      // its transcript lives in the page
  if (!isWebPage(tab)) return true; // chrome://, Web Store, PDFs
  return false;
}

// Reading is NOT destructive, so a pinned tab is fine to READ. Keeping these
// separate stops "protected" from quietly meaning "invisible".
const isReadable = (t) => isWebPage(t) && !isHud(t);

const CLICKABLE_SELECTOR =
  "a, button, input[type=submit], input[type=button], [role=button]";

// Mirrors web.py's _describe() — which is already this same JavaScript.
function describeEl(el) {
  const name = (el.getAttribute("aria-label") || el.innerText || el.value
                || el.getAttribute("title") || "").trim();
  // Only anchors expose a destination inspectable BEFORE clicking; '' for
  // everything else, and for anchors with no real href (# / none).
  const href = el.tagName.toLowerCase() === "a" ? (el.href || "") : "";
  return { name, href,
           kind: el.tagName.toLowerCase() === "a" ? "link" : "button" };
}

// Collect candidates, piercing OPEN shadow roots. Playwright's
// query_selector_all pierces shadow DOM and a plain querySelectorAll does not,
// so without this the two resolvers would disagree on component-heavy sites.
// (Measured on YouTube: 0 open shadow hosts there, so it changed nothing — but
// the divergence is real in principle and cheap to close.)
function collectClickables(root, out) {
  out = out || [];
  try {
    out.push(...root.querySelectorAll(CLICKABLE_SELECTOR));
    for (const el of root.querySelectorAll("*")) {
      if (el.shadowRoot) collectClickables(el.shadowRoot, out);
    }
  } catch { /* a detached or cross-origin root — skip it, never throw */ }
  return out;
}

// Port of web.py's _match_clickable scoring. Named matches win; a submit-ish
// target falls back to the first button, which may be NAMELESS — that is the
// deliberate fail-closed case (classify_web_click confirms an actionable
// element with no name).
function matchClickable(root, target) {
  const targetN = (target || "").trim().toLowerCase();
  const described = collectClickables(root).map((el) => {
    let d;
    try { d = describeEl(el); }
    catch { d = { name: "", kind: "button", href: "" }; }
    return { el, ...d };
  });

  let best = null, bestScore = 0;
  for (const cand of described) {
    if (!cand.name) continue;
    const n = cand.name.toLowerCase();
    let score = 0;
    if (n === targetN) score = 3;
    else if (targetN && (n.includes(targetN) || targetN.includes(n))) score = 2;
    else if (targetN && targetN.split(/\s+/).every((w) => n.includes(w))) score = 1;
    if (score > bestScore) { best = cand; bestScore = score; }
  }
  if (best) {
    return { found: true, name: best.name, kind: best.kind,
             href: best.href, el: best.el };
  }
  // Submit-ish target with no named match: first button, possibly nameless.
  if (/submit|send|go|search|ok|confirm/.test(targetN)) {
    const btn = described.find((c) => c.kind === "button");
    if (btn) {
      return { found: true, name: btn.name, kind: btn.kind,
               href: btn.href, el: btn.el };
    }
  }
  return { found: false, name: "", kind: "", href: "", el: null };
}

// Field resolver for fill(). Mirrors web.py's fill() candidate order:
// placeholder first (only real inputs carry one), then label/aria-label, then
// contenteditable / role=textbox — which is how Claude and ChatGPT boxes are
// built and which Playwright's fill() had to special-case.
function matchField(root, field) {
  const want = (field || "").trim().toLowerCase();
  const hit = (s) => s && want && s.toLowerCase().includes(want);

  const inputs = Array.from(root.querySelectorAll(
    "input:not([type=hidden]):not([type=submit]):not([type=button]), textarea"));
  for (const el of inputs) {
    if (hit(el.getAttribute("placeholder")) || hit(el.getAttribute("aria-label"))
        || hit(el.getAttribute("name")) || hit(el.id)) {
      return { found: true, el, editable: "value" };
    }
  }
  // <label for=...> pointing at an input
  for (const lab of Array.from(root.querySelectorAll("label"))) {
    if (!hit(lab.innerText)) continue;
    const forId = lab.getAttribute("for");
    const el = forId ? root.getElementById?.(forId) || root.querySelector(`#${CSS.escape(forId)}`)
                     : lab.querySelector("input, textarea");
    if (el) return { found: true, el, editable: "value" };
  }
  // rich editors
  for (const el of Array.from(root.querySelectorAll(
      "[contenteditable=''], [contenteditable='true'], [role=textbox]"))) {
    if (hit(el.getAttribute("aria-label")) || hit(el.getAttribute("placeholder"))
        || !want) {
      return { found: true, el, editable: "text" };
    }
  }
  // single obvious input on the page
  if (inputs.length === 1) return { found: true, el: inputs[0], editable: "value" };
  return { found: false, el: null, editable: "" };
}

// ---- page-side operations, as NAMED FUNCTIONS -------------------------------
//
// These run inside the page. They are functions here rather than strings
// evaluated there because MV3's extension CSP forbids `new Function`/eval in an
// injected script's isolated world — the first attempt did exactly that, and
// executeScript silently returned undefined, which the fail-closed default
// turned into "found no element". A silent nothing is the worst failure mode.
//
// None of these judge safety. Every TIER is decided in Python from the metadata
// they return, which is what keeps the CONFIRM gate, the cross-host gate and the
// slice-38 payload box working unchanged.
const ops = {
  find(arg) {
    const m = matchClickable(document, arg.target);
    return { found: m.found, name: m.name, kind: m.kind, href: m.href };
  },

  click(arg) {
    const m = matchClickable(document, arg.target);
    if (!m.found || !m.el) return { found: false };
    const before = location.href;
    m.el.click();
    return { found: true, name: m.name, kind: m.kind, href: m.href, before };
  },

  fill(arg) {
    const f = matchField(document, arg.field);
    if (!f.found || !f.el) return { found: false };
    const el = f.el;
    el.focus();
    if (f.editable === "value") el.value = arg.text;
    else el.textContent = arg.text;
    // Frameworks listen for these; assigning .value alone is invisible to React.
    el.dispatchEvent(new Event("input", { bubbles: true }));
    el.dispatchEvent(new Event("change", { bubbles: true }));
    // READ IT BACK. Never report success from "the call returned" — a silent
    // no-op is the exact failure Playwright's fill() had before contenteditable
    // was special-cased.
    const readback = f.editable === "value" ? el.value : el.textContent;
    return { found: true, readback: String(readback || "") };
  },

  key(arg) {
    const el = document.activeElement || document.body;
    const opts = { key: arg.key, bubbles: true, cancelable: true };
    const prevented = !el.dispatchEvent(new KeyboardEvent("keydown", opts));
    el.dispatchEvent(new KeyboardEvent("keyup", opts));
    let submitted = false;
    // A synthetic keydown does NOT submit a form the way a real keypress does.
    // If nothing handled Enter and we are inside a form, submit explicitly
    // rather than silently doing nothing and reporting success.
    if (arg.key === "Enter" && !prevented) {
      const form = el.closest && el.closest("form");
      if (form) {
        if (form.requestSubmit) form.requestSubmit();
        else form.submit();
        submitted = true;
      }
    }
    return { handled: prevented, submitted, url: location.href };
  },

  focused() {
    const el = document.activeElement;
    if (!el || el === document.body) return { found: false };
    const v = (el.value !== undefined && el.value !== null)
              ? String(el.value) : String(el.innerText || "");
    return { found: true, isPassword: el.type === "password", value: v };
  },
};

// Exposed as a GLOBAL rather than ES exports, deliberately: this same file must
// be importScripts'd by the worker AND injected into pages as a FILE. As an ES
// module it could be neither injected nor rebuilt (see the CSP note above).
globalThis.JARVIS_LIB = {
  isHud, isWebPage, isProtected, isReadable,
  describeEl, collectClickables, matchClickable, matchField,
  CLICKABLE_SELECTOR, ops,
};
