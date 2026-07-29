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

const WS_URL = "ws://127.0.0.1:8000/ws/browser";
const ALARM = "jarvis-reconnect";
const OWN_TAB_KEY = "jarvisTabId";
let ws = null;

function log(...a) { console.log("[JARVIS]", ...a); }

// The JARVIS HUD lives on 127.0.0.1:8000. Navigating THAT tab away destroys
// the conversation transcript — the v1.0.2 bug, in a new costume.
const isHud = (t) => !!t && /^https?:\/\/(127\.0\.0\.1|localhost):8000(\/|$)/.test(t.url || "");
const isWebPage = (t) => !!t && /^https?:/.test(t.url || "");

// ONE predicate for "may JARVIS navigate this tab away?".
//
// The pinned bug happened because this was an inline expression that simply
// forgot a case. Everything that decides destructively now goes through here.
//
// NOT protected, and deliberately so — say what this does NOT cover rather
// than implying the list is complete: tab groups, audible/playing tabs, and
// tabs with unsaved form input. Only the tracked-own-tab rule keeps those
// safe, which it does, because we never update a tab we did not open.
function isProtected(tab) {
  if (!tab) return true;                 // unknown -> refuse
  if (tab.pinned) return true;           // the user deliberately kept it
  if (isHud(tab)) return true;           // the HUD's transcript lives in it
  if (!isWebPage(tab)) return true;      // chrome://, Web Store, PDFs
  return false;
}

// Reading is NOT destructive, so a pinned tab is fine to read — only
// navigation is restricted. Keeping these separate stops "protected" from
// quietly meaning "invisible".
const isReadable = (t) => isWebPage(t) && !isHud(t);

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
  const [t] = await chrome.tabs.query({ active: true, lastFocusedWindow: true });
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

const COMMANDS = {
  // Keepalive. Cheap on purpose: the TRAFFIC is the point, not the payload.
  async ping() { return { ok: true, pong: true }; },

  async status() {
    const tab = await readTarget();
    return { ok: true, url: tab ? tab.url : null, title: tab ? tab.title : null };
  },

  async read(msg) {
    const tab = await readTarget();
    if (!tab) {
      return { ok: false, message:
        "I couldn't find a normal web page to read — Chrome blocks extensions " +
        "on browser pages like chrome://. Open a website and try again." };
    }
    const max = msg.max_chars || 5000;
    const text = await inPage(tab.id, (n) => document.body.innerText.slice(0, n), [max]);
    return { ok: true, url: tab.url, title: tab.title, text: text || "" };
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
        return { ok: true, url: after.url, title: after.title, reused: true };
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
    return { ok: true, url: after.url, title: after.title, new_tab: true };
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
