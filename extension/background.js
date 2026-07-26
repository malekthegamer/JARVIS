// JARVIS browser bridge — slice 41.
//
// Why this exists: JARVIS cannot reach your everyday Chrome over the DevTools
// protocol. Chrome 136+ refuses --remote-debugging-port on the default profile
// (measured: Chrome 150 starts and silently ignores the flag), and copying the
// profile elsewhere loses every login because of App-Bound Encryption. An
// extension is the only route into the browser you actually use.
//
// HARD-WON MV3 LESSON (measured in the Stage-0 probe, do not "simplify" this):
// while the socket is open, WebSocket traffic keeps the service worker alive —
// a 100s ping test passed cleanly. But the moment the socket CLOSES, the
// worker goes idle, Chrome terminates it, and any pending setTimeout dies with
// it. A setTimeout-based reconnect therefore works in every test and then
// silently never reconnects in real use. chrome.alarms is the only timer that
// WAKES a terminated worker, so reconnection MUST be alarm-driven. The cost is
// real and unavoidable: MV3's minimum alarm period is 1 minute, so after a
// JARVIS restart this can take up to ~60s to come back.

const WS_URL = "ws://127.0.0.1:8000/ws/browser";
const ALARM = "jarvis-reconnect";
let ws = null;

function log(...a) { console.log("[JARVIS]", ...a); }

async function activeTab() {
  const [tab] = await chrome.tabs.query({ active: true, lastFocusedWindow: true });
  return tab || null;
}

// Chrome forbids script injection into chrome://, the Web Store and PDF
// viewers. Say so honestly rather than returning an empty page that reads like
// a successful but blank result.
function tabRefusal(tab) {
  if (!tab) return "no active tab";
  if (!/^https?:/.test(tab.url || "")) {
    return `I can't read ${tab.url || "that tab"} — Chrome blocks extensions ` +
           `on browser pages. Switch to a normal web page.`;
  }
  return null;
}

async function inPage(tabId, func, args) {
  const res = await chrome.scripting.executeScript({
    target: { tabId }, func, args: args || [],
  });
  return res && res[0] ? res[0].result : null;
}

// ---- the commands JARVIS sends -------------------------------------------
const COMMANDS = {
  async status() {
    const tab = await activeTab();
    return { ok: true, url: tab ? tab.url : null, title: tab ? tab.title : null };
  },

  async read(msg) {
    const tab = await activeTab();
    const refusal = tabRefusal(tab);
    if (refusal) return { ok: false, message: refusal };
    const max = msg.max_chars || 5000;
    const text = await inPage(tab.id, (n) => document.body.innerText.slice(0, n), [max]);
    return { ok: true, url: tab.url, title: tab.title, text: text || "" };
  },

  async navigate(msg) {
    const tab = await activeTab();
    if (!tab) return { ok: false, message: "no active tab to navigate" };
    await chrome.tabs.update(tab.id, { url: msg.url });
    // Wait for the load to settle so the reply reflects the NEW page, not the
    // old one — otherwise JARVIS reports the previous title as success.
    const settled = await new Promise((resolve) => {
      const done = (id, info) => {
        if (id === tab.id && info.status === "complete") {
          chrome.tabs.onUpdated.removeListener(done);
          resolve(true);
        }
      };
      chrome.tabs.onUpdated.addListener(done);
      setTimeout(() => { chrome.tabs.onUpdated.removeListener(done); resolve(false); }, 15000);
    });
    const after = await chrome.tabs.get(tab.id);
    return { ok: true, url: after.url, title: after.title, settled };
  },
};

function connect() {
  if (ws && (ws.readyState === WebSocket.OPEN ||
             ws.readyState === WebSocket.CONNECTING)) {
    return;                     // probe v1 bug: connected twice on startup
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
        // Never let an exception strand JARVIS waiting — it blocks a worker
        // thread until timeout.
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
