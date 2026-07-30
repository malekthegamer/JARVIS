// JARVIS browser bridge.
//
// Why this exists: JARVIS cannot reach your everyday Chrome over the DevTools
// protocol. Chrome 136+ refuses --remote-debugging-port on the default profile
// (measured: Chrome 150 starts and silently ignores the flag), and copying the
// profile elsewhere loses every login because of App-Bound Encryption. An
// extension is the only route into the browser you actually use.
//
// MEASURED LESSONS — do not "simplify" any of these away:
//
// 1. RECONNECT MUST BE ALARM-DRIVEN. When the socket closes, the worker goes
//    idle, Chrome terminates it, and a pending setTimeout dies with it. A
//    setTimeout reconnect passes every test and then never reconnects in real
//    use. chrome.alarms is the only timer that WAKES a terminated worker.
//
// 2. THE SERVER HEARTBEATS US every 20s. Alarms fire at most once a minute, so
//    on their own the worker was dead ~50% of the time (measured: up 20s, down
//    30-60s, repeat). That intermittency is what made JARVIS fall back to
//    opening new windows and typing URLs by hand.
//
// 3. NOTHING IS IN-MEMORY ACROSS COMMANDS. The worker dies constantly, so the
//    tab JARVIS is working in lives in chrome.storage.session.
//
// USER-REPORTED BUGS THIS FILE NOW GUARDS (slice 42):
//   * "it opened YouTube in my PINNED tab"  -> isProtected() checks pinned
//   * "it opened Gmail OVER the YouTube tab it had just opened"
//       -> `open` now means tabs.create. tabs.update is reachable ONLY for
//          JARVIS's own tracked tab, and only when reuse was asked for.

// Pure logic (tab protection, element matching, page operations) lives in
// lib.js so it can be unit-tested and compared against Playwright's
// resolver. importScripts, not an ES import, because the SAME file must
// also be INJECTABLE into pages as a file — MV3's CSP blocks rebuilding it
// from source there.
// SIDE-EFFECT import: lib.js has no import/export syntax, it just assigns
// globalThis.JARVIS_LIB. That is deliberate and load-bearing — the SAME file
// must be importable by this MODULE worker AND injectable into pages as a
// classic file (MV3's CSP forbids rebuilding it from source there).
// importScripts was tried first and left the worker unable to connect at all.
import './lib.js';
// Read through accessors, NOT a top-level destructure. A destructure throws at
// load if lib.js has not populated the global yet, and a throw at worker load
// kills EVERYTHING — including connect(), so the extension goes silently dead
// rather than failing one command. Fail soft at the call site instead.
const L = () => globalThis.JARVIS_LIB || {};
const isHud = (t) => !!L().isHud && L().isHud(t);
const isProtected = (t) => (L().isProtected ? L().isProtected(t) : true);  // unknown -> refuse
const isReadable = (t) => !!L().isReadable && L().isReadable(t);

const WS_URL = "ws://127.0.0.1:8000/ws/browser";
const ALARM = "jarvis-reconnect";
const OWN_TAB_KEY = "jarvisTabId";
let ws = null;

function log(...a) { console.log("[JARVIS]", ...a); }

async function getOwnTabId() {
  try {
    const got = await chrome.storage.session.get(OWN_TAB_KEY);
    return got ? got[OWN_TAB_KEY] : null;
  } catch { return null; }
}

async function setOwnTabId(id) {
  try {
    if (id == null) await chrome.storage.session.remove(OWN_TAB_KEY);
    else await chrome.storage.session.set({ [OWN_TAB_KEY]: id });
  } catch { /* session storage is a convenience, never a hard dependency */ }
}

// JARVIS's own tab, or null. A closed tab is EXPECTED (the user can close
// anything) — clear it and let the caller open a fresh one.
async function ownTab() {
  const id = await getOwnTabId();
  if (id == null) return null;
  try {
    const tab = await chrome.tabs.get(id);
    return isProtected(tab) ? null : tab;
  } catch {
    await setOwnTabId(null);
    return null;
  }
}

async function activeTab() {
  // MEASURED BUG: {lastFocusedWindow: true} returns NOTHING when Chrome is not
  // the OS-focused application — which is the normal case, because the user is
  // typing into the JARVIS HUD or a terminal when they ask for something. Every
  // command then failed with "no web page to click in". Earlier probes only
  // passed because Chrome had just launched and happened to hold focus.
  let [t] = await chrome.tabs.query({ active: true, lastFocusedWindow: true });
  if (!t) [t] = await chrome.tabs.query({ active: true, currentWindow: true });
  if (!t) [t] = await chrome.tabs.query({ active: true });   // any window
  return t || null;
}

async function readTarget() {
  const active = await activeTab();
  if (isReadable(active)) return active;          // what the user is looking at
  const own = await ownTab();
  if (own) return own;                            // else the tab JARVIS opened
  const tabs = await chrome.tabs.query(
    active ? { windowId: active.windowId } : {});
  return tabs.find(isReadable) || null;
}

async function inPage(tabId, func, args) {
  const res = await chrome.scripting.executeScript({
    target: { tabId }, func, args: args || [],
  });
  return res && res[0] ? res[0].result : null;
}

// Resolve when the tab has actually landed, so the reply describes the NEW
// page rather than the old one. Measured: this tracks real page-load time
// (69ms for example.com, ~1.6s for YouTube) — the wait is the page, not us.
function waitForLoad(tabId, capMs) {
  return new Promise((resolve) => {
    const done = (id, info) => {
      if (id === tabId && info.status === "complete") {
        chrome.tabs.onUpdated.removeListener(done);
        resolve(true);
      }
    };
    chrome.tabs.onUpdated.addListener(done);
    setTimeout(() => {
      chrome.tabs.onUpdated.removeListener(done);
      resolve(false);
    }, capMs || 15000);
  });
}


// lib.js runs in the SERVICE WORKER, but the resolver has to run in the PAGE.
// chrome.scripting.executeScript SERIALISES its function, so it cannot close
// over an import. So: fetch lib.js's own source once and hand it to the page,
// which rebuilds the functions there. One copy of the matching logic — a second
// hand-written copy in an injected function is exactly the drift that would put
// a WRONG TIER in front of the user (Stage 0 measured 7/7 tier agreement
// against Playwright's resolver; that only stays true with one source).
// Inject lib.js as a FILE, then call one of its NAMED ops. No eval anywhere:
// MV3's CSP blocks `new Function` in the isolated world, and a second
// hand-written copy of the resolver would be the exact drift that puts a WRONG
// TIER in front of the user.
// WHICH TAB? Explicitly, or not at all.
//
// MEASURED: re-deriving the target on every command made behaviour
// NONDETERMINISTIC — the same test file gave "1 failed" twice then "9 failed",
// because both sources of truth are unstable: the active-tab queries depend on
// Chrome holding OS FOCUS (it usually does not — the user is typing in the HUD),
// and the tracked id depends on storage.session surviving a worker restart. It
// also meant classification and the action could resolve DIFFERENT tabs, which
// makes a computed tier meaningless.
//
// So: navigate returns its tab id, JARVIS passes it back, and every later
// command uses exactly that tab. Falling back to a guess only when JARVIS has
// no tab of its own yet.
async function resolveTab(msg) {
  const wanted = msg && msg.tab_id;
  if (wanted != null) {
    try {
      const tab = await chrome.tabs.get(wanted);
      if (tab && !isProtected(tab)) return tab;
    } catch { /* the user closed it — expected, fall through */ }
  }
  return readTarget();
}

async function inPageOp(tabId, op, arg) {
  await chrome.scripting.executeScript({ target: { tabId }, files: ["lib.js"] });
  const res = await chrome.scripting.executeScript({
    target: { tabId },
    func: (op, arg) => {
      const lib = globalThis.JARVIS_LIB;
      if (!lib || !lib.ops || !lib.ops[op]) return { __libMissing: op };
      return lib.ops[op](arg || {});
    },
    args: [op, arg || {}],
  });
  return res && res[0] ? res[0].result : null;
}

// Wait for a click's navigation to actually commit, bounded.
//
// A fixed sleep was wrong in both directions: too short and the reply says
// "(no navigation)" for a click that DID navigate — which also silently
// suppressed the cross-host JS-jump warning, the one thing that click path
// exists to report. Poll for a real URL change instead, and if it never comes,
// say so honestly.
async function waitForUrlChange(tabId, before, capMs) {
  const deadline = Date.now() + (capMs || 3000);
  while (Date.now() < deadline) {
    await new Promise((r) => setTimeout(r, 100));
    try {
      const t = await chrome.tabs.get(tabId);
      if (t && t.url && t.url !== before) return t;
    } catch { return null; }   // tab closed by the navigation
  }
  try { return await chrome.tabs.get(tabId); } catch { return null; }
}

const COMMANDS = {
  // ---- slice 43: acting in the user's real browser -----------------------
  // The TIER for each of these is decided in Python from the metadata below;
  // nothing here judges safety. Keeping that split is why the CONFIRM gate,
  // the cross-host gate and the slice-38 payload box all work unchanged.

  async find_clickable(msg) {
    const tab = await resolveTab(msg);
    if (!tab) return { ok: true, found: false, name: "", kind: "", href: "" };
    const r = await inPageOp(tab.id, "find", { target: msg.target });
    return { ok: true, ...(r || { found: false, name: "", kind: "", href: "" }) };
  },

  async click(msg) {
    const tab = await resolveTab(msg);
    if (!tab) return { ok: false, message: "no web page to click in" };
    const r = await inPageOp(tab.id, "click", { target: msg.target });
    if (!r || !r.found) {
      return { ok: false, message: `couldn't find '${msg.target}' on the page` };
    }
    const after = await waitForUrlChange(tab.id, r.before) || tab;
    return { ok: true, name: r.name, kind: r.kind, href: r.href,
             before: r.before, url: after.url, title: after.title,
             tab_id: tab.id };
  },

  async fill(msg) {
    const tab = await resolveTab(msg);
    if (!tab) return { ok: false, message: "no web page to type into" };
    const r = await inPageOp(tab.id, "fill", { field: msg.field, text: msg.text });
    if (!r || !r.found) {
      return { ok: false, message: `couldn't find a field matching '${msg.field}'` };
    }
    return { ok: true, readback: r.readback };
  },

  async key(msg) {
    const tab = await resolveTab(msg);
    if (!tab) return { ok: false, message: "no web page to press keys in" };
    const r = await inPageOp(tab.id, "key", { key: msg.key });
    const after = await waitForUrlChange(tab.id, (r && r.url) || "") || tab;
    return { ok: true, ...(r || {}), url: after.url, tab_id: tab.id };
  },

  async focused(msg) {
    const tab = await resolveTab(msg);
    if (!tab) return { ok: true, found: false };
    const r = await inPageOp(tab.id, "focused", {});
    return { ok: true, ...(r || { found: false }) };
  },

  // Keepalive. Cheap on purpose: the TRAFFIC is the point, not the payload.
  async ping() { return { ok: true, pong: true }; },

  async status(msg) {
    const tab = await resolveTab(msg);
    return { ok: true, url: tab ? tab.url : null, title: tab ? tab.title : null,
             tab_id: tab ? tab.id : null };
  },

  async read(msg) {
    const tab = await resolveTab(msg);
    if (!tab) {
      return { ok: false, message:
        "I couldn't find a normal web page to read — Chrome blocks extensions " +
        "on browser pages like chrome://. Open a website and try again." };
    }
    const max = msg.max_chars || 5000;
    const text = await inPage(tab.id, (n) => document.body.innerText.slice(0, n), [max]);
    return { ok: true, url: tab.url, title: tab.title, text: text || "",
             tab_id: tab.id };
  },

  // OPEN MEANS OPEN. A new tab in the CURRENT window, every time — unless
  // JARVIS explicitly asks to continue in the tab it already owns (walking
  // through one site without spawning a tab per step).
  async navigate(msg) {
    if (msg.reuse) {
      const own = await ownTab();
      if (own) {
        await chrome.tabs.update(own.id, { url: msg.url, active: true });
        await waitForLoad(own.id);
        const after = await chrome.tabs.get(own.id);
        return { ok: true, url: after.url, title: after.title, reused: true,
                 tab_id: own.id };
      }
      // fall through: our tab is gone, so open a fresh one rather than
      // hijacking whatever happens to be in front of the user
    }
    const active = await activeTab();
    const created = await chrome.tabs.create(
      active ? { url: msg.url, windowId: active.windowId, active: true }
             : { url: msg.url, active: true });
    await setOwnTabId(created.id);
    await waitForLoad(created.id);
    const after = await chrome.tabs.get(created.id);
    return { ok: true, url: after.url, title: after.title, new_tab: true,
             tab_id: created.id };
  },
};

function connect() {
  if (ws && (ws.readyState === WebSocket.OPEN ||
             ws.readyState === WebSocket.CONNECTING)) {
    return;
  }
  ws = new WebSocket(WS_URL);

  ws.onopen = () => {
    log("connected to JARVIS");
    ws.send(JSON.stringify({ type: "hello", id: chrome.runtime.id }));
  };

  ws.onmessage = async (ev) => {
    let msg;
    try { msg = JSON.parse(ev.data); } catch { return; }
    const handler = COMMANDS[msg.cmd];
    let reply;
    if (!handler) {
      reply = { ok: false, message: `unknown command: ${msg.cmd}` };
    } else {
      try {
        reply = await handler(msg);
      } catch (e) {
        // Never strand JARVIS: it blocks a worker thread until timeout.
        reply = { ok: false, message: String(e && e.message ? e.message : e) };
      }
    }
    reply.id = msg.id;
    try { ws.send(JSON.stringify(reply)); } catch (e) { log("send failed", e); }
  };

  ws.onclose = () => { log("disconnected — the alarm will retry"); ws = null; };
  ws.onerror = () => { ws = null; };
}

chrome.alarms.create(ALARM, { periodInMinutes: 1 });
chrome.alarms.onAlarm.addListener((a) => { if (a.name === ALARM) connect(); });
chrome.runtime.onStartup.addListener(connect);
chrome.runtime.onInstalled.addListener(connect);
connect();
