/* JARVIS dashboard client — WebSocket + orb state binding + rail toggle. */
(() => {
  const $ = (id) => document.getElementById(id);
  const conversation = $("conversation"), input = $("input");
  const feed = $("feed"), notifs = $("notifs");
  // Voice replies default ON (persisted) — JARVIS should sound like JARVIS.
  let speakReplies = localStorage.getItem("jarvis.speakReplies") !== "off";
  let pendingEl = null, ws, unread = 0;

  const orb = window.Orb.mount($("orb"));

  const STATE_LABELS = {
    idle: "v2.0 // standby", listening: "listening…", thinking: "processing…",
    speaking: "speaking…", offline: "connection lost",
  };

  // ---------- websocket ----------
  function connect() {
    ws = new WebSocket(`ws://${location.host}/ws`);
    ws.onmessage = (ev) => handle(JSON.parse(ev.data));
    ws.onclose = () => { setState("offline"); setTimeout(connect, 1500); };
    ws.onopen = () => setState("idle");
  }

  function handle(msg) {
    switch (msg.type) {
      case "status": setState(msg.state); break;
      case "user": addMsg("user", msg.text); showPending(); break;
      case "assistant": clearPending(); addMsg("jarvis", msg.text); break;
      case "audit": addAudit(msg.entry); break;
      case "notification": addNotif(msg.item); break;
      case "confirm_request": showConfirm(msg.id, msg.description); break;
      case "reset": conversation.innerHTML = ""; break;
    }
  }

  // ---------- state -> orb + HUD ----------
  function setState(state) {
    const base = state.startsWith("executing") ? "thinking" : state;
    orb.setState(base);
    $("hudDot").className = "orbdot " + base;
    $("sysStatus").textContent = state === "offline" ? "OFFLINE" : "OPTIMAL";
    $("stateLabel").textContent = state.startsWith("executing")
      ? state.slice(10).replace(/_/g, " ") + "…"
      : (STATE_LABELS[base] || base);
  }

  // ---------- messages ----------
  function addMsg(who, text) {
    $("promptHint").style.opacity = "0";
    const el = document.createElement("div");
    el.className = `msg ${who}`;
    el.innerHTML = `<div class="who"></div><div class="body"></div>`;
    el.querySelector(".who").textContent = who === "user" ? "you" : "jarvis";
    el.querySelector(".body").textContent = text;
    conversation.appendChild(el);
    conversation.scrollTop = conversation.scrollHeight;
    return el;
  }
  function showPending() { clearPending(); pendingEl = addMsg("jarvis", "…"); pendingEl.classList.add("pending"); }
  function clearPending() { pendingEl?.remove(); pendingEl = null; }

  // ---------- rail ----------
  function addAudit(e) {
    feed.querySelector(".empty")?.remove();
    const el = document.createElement("div");
    el.className = "entry" + (e.result === "denied" ? " denied" : "");
    const time = new Date(e.ts).toLocaleTimeString();
    el.innerHTML = `<span class="t"></span> <span class="a"></span>`;
    el.querySelector(".t").textContent = `${time} · ${e.skill}.${e.action}`;
    el.querySelector(".a").textContent = e.result !== "ok" ? `→ ${e.result}` : "";
    feed.prepend(el);
    while (feed.children.length > 120) feed.lastChild.remove();
    bumpBadge();
  }
  function addNotif(n) {
    notifs.querySelector(".empty")?.remove();
    const el = document.createElement("div");
    el.className = "item";
    el.innerHTML = `<div class="title"></div><div class="body"></div>`;
    el.querySelector(".title").textContent = n.title;
    el.querySelector(".body").textContent = n.message;
    notifs.prepend(el);
    bumpBadge();
  }
  function bumpBadge() {
    if ($("rail").classList.contains("open")) return;
    unread++;
    const b = $("railBadge");
    b.textContent = unread; b.classList.remove("hidden");
  }
  function openRail() {
    $("rail").classList.add("open"); $("scrim").classList.add("show");
    $("rail").setAttribute("aria-hidden", "false");
    unread = 0; $("railBadge").classList.add("hidden");
  }
  function closeRail() {
    $("rail").classList.remove("open"); $("scrim").classList.remove("show");
    $("rail").setAttribute("aria-hidden", "true");
  }

  // ---------- confirm ----------
  function showConfirm(id, description) {
    $("confirmDesc").textContent = description;
    $("confirmVeil").classList.remove("hidden");
    $("allowBtn").onclick = () => answer(id, true);
    $("denyBtn").onclick = () => answer(id, false);
  }
  function answer(id, approved) {
    ws.send(JSON.stringify({ type: "confirm_response", id, approved }));
    $("confirmVeil").classList.add("hidden");
  }

  // ---------- events ----------
  $("composer").addEventListener("submit", (ev) => {
    ev.preventDefault();
    const text = input.value.trim();
    if (!text || !ws || ws.readyState !== 1) return;
    ws.send(JSON.stringify({ type: "chat", text, speak: speakReplies }));
    input.value = "";
  });
  function renderSpeakToggle() {
    $("speakToggle").classList.toggle("on", speakReplies);
    $("speakToggle").title = `Voice replies: ${speakReplies ? "on" : "off"}`;
  }
  $("speakToggle").addEventListener("click", () => {
    speakReplies = !speakReplies;
    localStorage.setItem("jarvis.speakReplies", speakReplies ? "on" : "off");
    renderSpeakToggle();
  });
  renderSpeakToggle();
  $("railToggle").addEventListener("click", openRail);
  $("railClose").addEventListener("click", closeRail);
  $("scrim").addEventListener("click", closeRail);
  document.querySelectorAll(".tab").forEach((tab) => {
    tab.addEventListener("click", () => {
      document.querySelectorAll(".tab").forEach((t) => t.classList.remove("active"));
      document.querySelectorAll(".tabpane").forEach((p) => p.classList.remove("active"));
      tab.classList.add("active");
      $(`pane-${tab.dataset.tab}`).classList.add("active");
    });
  });
  document.addEventListener("keydown", (e) => { if (e.key === "Escape") { closeRail(); } });

  // ---------- bootstrap ----------
  async function bootstrap() {
    try {
      const s = await (await fetch("/api/status")).json();
      $("sysBrain").textContent = `${s.brain.name}${s.brain.configured ? "" : " ⚠"}`.toUpperCase();
    } catch {}
    try {
      const h = await (await fetch("/api/history")).json();
      h.messages.forEach((m) => addMsg(m.role === "user" ? "user" : "jarvis", m.text));
    } catch {}
    try {
      const a = await (await fetch("/api/audit?n=40")).json();
      a.entries.forEach(addAudit);
      unread = 0; $("railBadge").classList.add("hidden");
    } catch {}
    try {
      const n = await (await fetch("/api/notifications")).json();
      n.items.forEach(addNotif);
      unread = 0; $("railBadge").classList.add("hidden");
    } catch {}
  }

  bootstrap();
  connect();
})();
