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

  // ---------- chain strip (slice 6): declared plan + live progress ----------
  const chainStrip = document.getElementById("chain-strip");
  let chainSteps = []; // [{text, status: pending|current|done|failed|cancelled}]
  let chainFadeTimer = null;

  function renderChain() {
    chainStrip.innerHTML = "";
    for (const s of chainSteps) {
      const li = document.createElement("li");
      li.dataset.status = s.status;
      li.textContent = s.text;
      chainStrip.appendChild(li);
    }
    chainStrip.classList.toggle("hidden", chainSteps.length === 0);
  }

  function showChain(steps) {
    clearTimeout(chainFadeTimer);
    chainStrip.classList.remove("fade");
    chainSteps = steps;
    renderChain();
  }

  function chainMark(current, endStatus) {
    // indices < current-1 done, current-1 gets endStatus (or "current")
    chainSteps.forEach((s, i) => {
      if (i < current - 1) s.status = "done";
      else if (i === current - 1) s.status = endStatus || "current";
      else s.status = "pending";
    });
    renderChain();
  }

  function hideChain(afterMs) {
    clearTimeout(chainFadeTimer);
    chainFadeTimer = setTimeout(() => {
      chainStrip.classList.add("fade");
      chainFadeTimer = setTimeout(() => {
        chainSteps = [];
        renderChain();
        chainStrip.classList.remove("fade");
      }, 450); // matches the CSS opacity transition
    }, afterMs);
  }

  function onChainEvent(event) {
    if (event.type === "plan") {
      showChain(event.steps.map((t) => ({ text: t, status: "pending" })));
      chainMark(1);
    } else if (event.type === "step") {
      if (!event.total || !chainSteps.length) return; // undeclared chain: label counter covers it
      const bad = event.status === "failed" || event.status === "cancelled";
      chainMark(event.step, bad ? event.status : null);
    } else if (event.type === "chain_end") {
      if (event.status === "done") {
        chainSteps.forEach((s) => (s.status = "done"));
      } else if (event.status !== "cancelled") {
        // budget / exhausted / error: the step we were on did not finish
        const cur = chainSteps.find((s) => s.status === "current");
        if (cur) cur.status = "failed";
      } // cancelled: the step is already marked by its step event
      renderChain();
      hideChain(event.status === "done" ? 2000 : 3500);
    } else if (event.type === "chain") {
      // snapshot replay on (re)connect
      const steps = (event.steps || []).map((t, i) => {
        let status = "pending";
        if (i < event.cursor) status = "done";
        else if (i === event.cursor)
          status = event.aborted
            ? (event.aborted === "cancelled" ? "cancelled" : "failed")
            : "current";
        return { text: t, status };
      });
      showChain(steps);
    }
  }

  // ---------- CONFIRM modal (fail closed: only Approve sends true) ----------
  const backdrop = document.getElementById("confirm-backdrop");
  const confirmDesc = document.getElementById("confirm-desc");
  const confirmCount = document.getElementById("confirm-count");
  let confirmId = null;
  let countdownTimer = null;

  function showConfirm(event) {
    confirmId = event.id;
    confirmDesc.textContent = event.description;
    backdrop.classList.remove("hidden");
    let remaining = Math.round(event.timeout_s || 30);
    confirmCount.textContent = remaining;
    clearInterval(countdownTimer);
    countdownTimer = setInterval(() => {
      remaining -= 1;
      confirmCount.textContent = Math.max(remaining, 0);
      if (remaining <= 0) clearInterval(countdownTimer); // server cancels; modal clears on confirm_resolved
    }, 1000);
    document.getElementById("confirm-no").focus(); // safe default focus
  }

  function hideConfirm() {
    confirmId = null;
    clearInterval(countdownTimer);
    backdrop.classList.add("hidden");
  }

  function answerConfirm(approved) {
    if (!confirmId || !ws || ws.readyState !== WebSocket.OPEN) return;
    ws.send(JSON.stringify({ type: "confirm_response", id: confirmId, approved }));
    // The modal clears on the server's confirm_resolved broadcast (all tabs).
  }

  document.getElementById("confirm-yes").addEventListener("click", () => answerConfirm(true));
  document.getElementById("confirm-no").addEventListener("click", () => answerConfirm(false));
  document.addEventListener("keydown", (e) => {
    if (e.key === "Escape" && confirmId) answerConfirm(false);
    // Deliberately NO keyboard shortcut for Approve — explicit click only.
  });

  function connect() {
    ws = new WebSocket(`ws://${location.host}/ws`);
    ws.onopen = () => {
      retryMs = 500;
      body.classList.add("online");
      connText.textContent = "online";
    };
    ws.onmessage = (msg) => handleEvent(JSON.parse(msg.data));
    ws.onclose = () => {
      body.classList.remove("online");
      connText.textContent = "reconnecting";
      setState("offline");
      hideConfirm(); // a dead socket can't answer; server replays on reconnect
      showChain([]); // stale plan is worse than none; server replays on reconnect
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

  function handleEvent(event) {
    if (event.type === "state") setState(event.state, event.detail);
    else if (event.type === "transcript") addLine(event.who, event.text);
    else if (event.type === "confirm_request") showConfirm(event);
    else if (event.type === "confirm_resolved") hideConfirm();
    else if (event.type === "plan" || event.type === "step" ||
             event.type === "chain_end" || event.type === "chain")
      onChainEvent(event);
    else if (event.type === "error" && event.message === "busy")
      flashHint("One moment — still working on the last one.");
  }

  // Test hooks: let verification tooling render any state deterministically.
  window.__hudSetState = setState;
  window.__hudEvent = handleEvent;

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
