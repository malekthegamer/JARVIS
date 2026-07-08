/* JARVIS HUD client — WS state/transcript events in, chat + push-to-talk out.
 * The backend owns all state; this file only renders what it's told. */
(() => {
  const orb = window.Orb.mount(document.getElementById("orb"));
  const body = document.body;
  const stateLabel = document.getElementById("state-label");
  const transcript = document.getElementById("transcript");
  const connText = document.getElementById("conn-text");
  const hint = document.querySelector(".hint");
  const form = document.getElementById("chat-form");
  const input = document.getElementById("chat-input");

  let ws = null;
  let retryMs = 500;

  function setState(name, detail) {
    body.dataset.state = name;
    stateLabel.textContent =
      name.toUpperCase() + (detail ? ` · ${detail.toUpperCase()}` : "");
    orb.setState(name);
  }

  function addLine(who, text) {
    const empty = document.getElementById("empty-line");
    if (empty) empty.remove();
    const p = document.createElement("p");
    p.className = "line" + (who === "jarvis" ? " jarvis" : "");
    const label = document.createElement("span");
    label.className = "who";
    label.textContent = who === "jarvis" ? "JARVIS > " : "YOU > ";
    p.appendChild(label);
    p.appendChild(document.createTextNode(text));
    transcript.appendChild(p);
    while (transcript.children.length > 40) transcript.firstChild.remove();
    transcript.scrollTop = transcript.scrollHeight;
  }

  function flashHint(text) {
    const original = hint.dataset.original || (hint.dataset.original = hint.innerHTML);
    hint.textContent = text;
    hint.classList.add("flash");
    setTimeout(() => {
      hint.innerHTML = original;
      hint.classList.remove("flash");
    }, 1800);
  }

  function connect() {
    ws = new WebSocket(`ws://${location.host}/ws`);
    ws.onopen = () => {
      retryMs = 500;
      body.classList.add("online");
      connText.textContent = "online";
    };
    ws.onmessage = (msg) => {
      const event = JSON.parse(msg.data);
      if (event.type === "state") setState(event.state, event.detail);
      else if (event.type === "transcript") addLine(event.who, event.text);
      else if (event.type === "error" && event.message === "busy")
        flashHint("One moment — still working on the last one.");
    };
    ws.onclose = () => {
      body.classList.remove("online");
      connText.textContent = "reconnecting";
      setState("offline");
      setTimeout(connect, retryMs);
      retryMs = Math.min(retryMs * 2, 8000);
    };
  }

  async function pushToTalk() {
    if (body.dataset.state !== "idle") {
      flashHint("One moment — still working on the last one.");
      return;
    }
    try {
      await fetch("/api/listen", { method: "POST" });
      // States and transcripts arrive over the WebSocket; nothing to do here.
    } catch {
      flashHint("Couldn't reach the server.");
    }
  }

  document.getElementById("orb-button").addEventListener("click", pushToTalk);
  document.addEventListener("keydown", (e) => {
    if (e.code === "Space" && document.activeElement !== input) {
      e.preventDefault();
      pushToTalk();
    }
  });

  form.addEventListener("submit", (e) => {
    e.preventDefault();
    const text = input.value.trim();
    if (!text || !ws || ws.readyState !== WebSocket.OPEN) return;
    ws.send(JSON.stringify({ type: "chat", text }));
    input.value = "";
  });

  connect();
})();
