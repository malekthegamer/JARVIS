/* JARVIS chat dashboard — WebSocket client. */
(() => {
  const $ = (id) => document.getElementById(id);
  const messages = $("messages"), input = $("input"), arc = $("arc"), statusWord = $("statusWord");
  const feed = $("feed"), notifs = $("notifs");
  let speakReplies = false;
  let pendingEl = null;
  let ws;

  // ---------- websocket ----------
  function connect() {
    ws = new WebSocket(`ws://${location.host}/ws`);
    ws.onmessage = (ev) => handle(JSON.parse(ev.data));
    ws.onclose = () => { setStatus("offline"); setTimeout(connect, 1500); };
    ws.onopen = () => setStatus("idle");
  }

  function handle(msg) {
    switch (msg.type) {
      case "status": setStatus(msg.state); break;
      case "user": addMsg("user", msg.text); showPending(); break;
      case "assistant": clearPending(); addMsg("jarvis", msg.text); break;
      case "audit": addAudit(msg.entry); break;
      case "notification": addNotif(msg.item); break;
      case "confirm_request": showConfirm(msg.id, msg.description); break;
      case "reset": messages.querySelectorAll(".msg").forEach(el => el.remove()); break;
    }
  }

  // ---------- status arc ----------
  function setStatus(state) {
    const word = state.startsWith("executing:") ? state.slice(10) : state;
    statusWord.textContent = word;
    arc.className = "arc";
    if (state === "listening") arc.classList.add("listening");
    else if (state === "thinking" || state.startsWith("executing")) arc.classList.add("thinking");
    else if (state === "speaking") arc.classList.add("speaking");
  }

  // ---------- messages ----------
  function addMsg(who, text) {
    $("emptyHint")?.remove();
    const el = document.createElement("div");
    el.className = `msg ${who === "user" ? "user" : ""}`;
    el.innerHTML = `<div class="who"></div><div class="body"></div>`;
    el.querySelector(".who").textContent = who === "user" ? "you" : "jarvis";
    el.querySelector(".body").textContent = text;
    messages.appendChild(el);
    messages.scrollTop = messages.scrollHeight;
    return el;
  }
  function showPending() {
    clearPending();
    pendingEl = addMsg("jarvis", "…");
    pendingEl.classList.add("pending");
  }
  function clearPending() { pendingEl?.remove(); pendingEl = null; }

  // ---------- rail ----------
  function addAudit(e) {
    feed.querySelector(".empty")?.remove();
    const el = document.createElement("div");
    el.className = "entry" + (e.result === "denied" ? " denied" : "");
    const time = new Date(e.ts).toLocaleTimeString();
    el.innerHTML = `<span class="t"></span> <span class="a"></span>`;
    el.querySelector(".t").textContent = `${time} ${e.skill}.${e.action}`;
    el.querySelector(".a").textContent = e.result !== "ok" ? `→ ${e.result}` : "";
    feed.prepend(el);
    while (feed.children.length > 100) feed.lastChild.remove();
  }
  function addNotif(n) {
    const el = document.createElement("div");
    el.className = "item";
    el.innerHTML = `<div class="title"></div><div class="body"></div>`;
    el.querySelector(".title").textContent = n.title;
    el.querySelector(".body").textContent = n.message;
    notifs.prepend(el);
  }

  // ---------- confirm modal ----------
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

  // ---------- composer ----------
  $("composer").addEventListener("submit", (ev) => {
    ev.preventDefault();
    const text = input.value.trim();
    if (!text || !ws || ws.readyState !== 1) return;
    ws.send(JSON.stringify({ type: "chat", text, speak: speakReplies }));
    input.value = "";
  });
  $("speakToggle").addEventListener("click", () => {
    speakReplies = !speakReplies;
    $("speakToggle").textContent = `🔊 voice replies: ${speakReplies ? "on" : "off"}`;
    $("speakToggle").classList.toggle("on", speakReplies);
  });
  $("resetBtn").addEventListener("click", () => ws.send(JSON.stringify({ type: "reset" })));

  // ---------- initial load ----------
  async function bootstrap() {
    try {
      const s = await (await fetch("/api/status")).json();
      $("pBrain").textContent = `${s.brain.name}${s.brain.configured ? "" : " (no key)"}`;
      $("pStt").textContent = s.stt;
      $("pTts").textContent = s.tts;
    } catch {}
    try {
      const h = await (await fetch("/api/history")).json();
      h.messages.forEach(m => addMsg(m.role === "user" ? "user" : "jarvis", m.text));
    } catch {}
    try {
      const a = await (await fetch("/api/audit?n=30")).json();
      a.entries.forEach(addAudit);
    } catch {}
    try {
      const n = await (await fetch("/api/notifications")).json();
      n.items.forEach(addNotif);
    } catch {}
  }

  bootstrap();
  connect();
})();
