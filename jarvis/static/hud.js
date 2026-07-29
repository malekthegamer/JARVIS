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

  // ---------- Action Log panel (slice 7): the chain feed, persistent ----------
  const logBody = document.getElementById("action-log");
  const LOG_CAP = 80;
  let logRowsByN = new Map(); // current chain's n -> <li> (cleared per chain)
  let logRowCount = 0;

  function logStick() {
    return logBody.scrollTop + logBody.clientHeight >= logBody.scrollHeight - 12;
  }
  function logAppend(li) {
    const stick = logStick();
    logBody.appendChild(li);
    while (logRowCount > LOG_CAP) {
      const first = logBody.firstChild;
      if (first.classList.contains("log-row")) logRowCount -= 1;
      first.remove();
    }
    if (stick) logBody.scrollTop = logBody.scrollHeight;
  }

  function logPlan(e) {
    const li = document.createElement("li");
    li.className = "log-plan";
    li.textContent = `PLAN · REV ${e.revision} · ${e.steps.length} STEPS`;
    li.title = e.steps.join("  →  ");
    logAppend(li);
  }

  function logStep(e) {
    let li = logRowsByN.get(e.n);
    if (!li) {
      li = document.createElement("li");
      li.className = "log-row";
      for (const cls of ["log-tool", "log-args", "log-note"]) {
        const s = document.createElement("span");
        s.className = cls;
        li.appendChild(s);
        li.appendChild(document.createTextNode(" "));
      }
      logRowsByN.set(e.n, li);
      logRowCount += 1;
      logAppend(li);
    }
    li.dataset.status = e.status === "start" ? "running" : e.status;
    li.querySelector(".log-tool").textContent = e.tool || "";
    li.querySelector(".log-args").textContent = e.args || "";
    if (e.note) li.querySelector(".log-note").textContent = "· " + e.note;
    li.title = `${e.tool} ${e.args || ""} ${e.note || ""}`.trim();
  }

  function logChainEnd(e) {
    // a chain can die with a step still "running" (crash/abort) — restyle
    for (const li of logRowsByN.values()) {
      if (li.dataset.status === "running")
        li.dataset.status = e.status === "done" ? "ok"
          : e.status === "cancelled" ? "cancelled" : "failed";
    }
    logRowsByN = new Map(); // next chain appends fresh rows (history stays)
  }

  function logHydrate(e) {
    // WS connect snapshot of the live chain — rebuild its rows
    logRowsByN = new Map();
    if (e.revision) logPlan({ revision: e.revision, steps: e.steps || [] });
    for (const c of e.calls || [])
      logStep({ n: c.n, tool: c.tool, args: c.args, status: c.status, note: c.note });
  }

  // ---------- telemetry panel (slice 7, spec §2.3) ----------
  function telSet(id, text, barId, pct) {
    document.getElementById(id).textContent = text;
    if (barId) {
      const bar = document.getElementById(barId);
      bar.style.width = Math.max(0, Math.min(100, pct || 0)) + "%";
      bar.classList.toggle("hot", (pct || 0) >= 90);
    }
  }

  function renderTelemetry(e) {
    if (typeof e.cpu === "number")
      telSet("tel-cpu", e.cpu.toFixed(0) + "%", "tel-cpu-bar", e.cpu);
    if (typeof e.ram === "number")
      telSet("tel-ram", `${e.ram.toFixed(0)}% · ${e.ram_used_gb}/${e.ram_total_gb}G`,
             "tel-ram-bar", e.ram);
    if (typeof e.gpu === "number")
      telSet("tel-gpu", `${e.gpu.toFixed(0)}% · ${e.gpu_mem_used_gb}/${e.gpu_mem_total_gb}G`,
             "tel-gpu-bar", e.gpu);
    else if (document.getElementById("tel-gpu").textContent === "—")
      telSet("tel-gpu", "—", "tel-gpu-bar", 0);
    // window titles are untrusted strings — textContent only, never HTML
    document.getElementById("tel-window").textContent = e.window || "";
  }

  const telClock = document.getElementById("tel-clock");
  function tickClock() {
    telClock.textContent = new Date().toLocaleTimeString("en-GB");
  }
  tickClock();
  setInterval(tickClock, 1000);

  // ---------- CONFIRM modal (fail closed: only Approve sends true) ----------
  const backdrop = document.getElementById("confirm-backdrop");
  const confirmDesc = document.getElementById("confirm-desc");
  const confirmCount = document.getElementById("confirm-count");
  let confirmId = null;
  let countdownTimer = null;

  function showConfirm(event) {
    confirmId = event.id;
    confirmDesc.textContent = event.description;
    // Slice 9: a verbatim shell command renders in a monospace box, never
    // prose — textContent only (the command is untrusted input).
    const cmdBox = document.getElementById("confirm-command");
    if (event.command) {
      cmdBox.textContent = event.command;
      cmdBox.classList.remove("hidden");
    } else {
      cmdBox.textContent = "";
      cmdBox.classList.add("hidden");
    }
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
    else if (event.type === "telemetry") {
      renderTelemetry(event);
      renderBrowserBadge(event.browser_mode, event.browser_connected);
    }
    else if (event.type === "plan" || event.type === "step" ||
             event.type === "chain_end" || event.type === "chain") {
      onChainEvent(event); // the strip (transient, current plan)
      if (event.type === "plan") logPlan(event);           // the log (persistent)
      else if (event.type === "step") logStep(event);
      else if (event.type === "chain_end") logChainEnd(event);
      else logHydrate(event);
    }
    else if (event.type === "error" && event.message === "busy")
      flashHint("One moment — still working on the last one.");
  }

  // ---------- slice 37: first-run setup ----------
  // A fresh install has no API key. Without this the HUD looks fine and every
  // message fails, which reads as "it's broken" rather than "it needs a key".
  const setupBackdrop = document.getElementById("setup-backdrop");
  const setupKey = document.getElementById("setup-key");
  const setupSave = document.getElementById("setup-save");
  const setupError = document.getElementById("setup-error");

  function showSetupError(message) {
    setupError.textContent = message;
    setupError.classList.remove("hidden");
  }

  async function checkSetup() {
    try {
      const r = await fetch("/api/setup_state");
      if (!r.ok) return;
      const state = await r.json();
      setupBackdrop.classList.toggle("hidden", state.brain_key === true);
      if (state.brain_key !== true) setupKey.focus();
    } catch { /* offline/never-block: the HUD still works without this */ }
  }

  async function saveSetupKey() {
    const key = setupKey.value.trim();
    if (!key) { showSetupError("Paste the key first."); return; }
    setupSave.disabled = true;
    setupError.classList.add("hidden");
    try {
      const r = await fetch("/api/settings", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ keys: { gemini: key } }),
      });
      if (!r.ok) { showSetupError(`Could not save (HTTP ${r.status}).`); return; }
      const after = await (await fetch("/api/setup_state")).json();
      if (after.brain_key === true) {
        setupBackdrop.classList.add("hidden");
        setupKey.value = "";
      } else {
        showSetupError("That key was not accepted. Check you copied all of it.");
      }
    } catch (err) {
      showSetupError("Could not reach JARVIS to save the key.");
    } finally {
      setupSave.disabled = false;
    }
  }

  setupSave.addEventListener("click", saveSetupKey);
  setupKey.addEventListener("keydown", (e) => {
    if (e.key === "Enter") { e.preventDefault(); saveSetupKey(); }
  });
  checkSetup();

  // Test hooks: let verification tooling render any state deterministically.
  window.__hudSetState = setState;
  window.__hudEvent = handleEvent;
  window.__hudCheckSetup = checkSetup;

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

  // --- slice 42: live browser health on the badge -------------------------
  // The extension dying used to be invisible: JARVIS just started opening new
  // windows and typing URLs by hand with nothing on screen to explain it.
  // Telemetry already arrives every ~2s, so the state rides that.
  function renderBrowserBadge(mode, connected) {
    const badge = document.getElementById("realbrowser");
    const text = document.getElementById("realbrowser-text");
    if (!badge || !text) return;
    if (mode === "extension") {
      badge.classList.remove("hidden");
      badge.classList.toggle("offline", !connected);
      text.textContent = connected ? "your browser" : "browser reconnecting…";
      badge.title = connected
        ? "JARVIS is connected to your everyday Chrome and can open and read pages in it."
        : "The JARVIS extension isn't connected right now. Chrome may have paused it; "
          + "it reconnects on its own, which can take up to a minute.";
      return;
    }
    if (mode === "real") {
      badge.classList.remove("hidden", "offline");
      text.textContent = "dedicated chrome";
      badge.title = "JARVIS is driving its own signed-in Chrome, not your everyday browser.";
      return;
    }
    badge.classList.add("hidden");
  }
  window.__hudBrowserBadge = renderBrowserBadge;

  // --- slice 39: show when JARVIS is driving the user's REAL browser -------
  // Two stacked opt-ins, and the difference matters: "reading" can only look,
  // "acting" can click and type on accounts the user is signed into.
  async function checkRealBrowser() {
    const badge = document.getElementById("realbrowser");
    const text = document.getElementById("realbrowser-text");
    if (!badge || !text) return;
    try {
      const r = await fetch("/api/settings");
      if (!r.ok) return;
      const s = await r.json();
      const web = (s && s.web) || {};
      const real = web.profile_mode === "real";
      badge.classList.toggle("hidden", !real);
      if (!real) return;
      const acting = web.allow_actions === true;
      text.textContent = acting ? "your browser · acting" : "your browser · reading";
      badge.title = acting
        ? "JARVIS is driving your signed-in Chrome and may click and type on it. Committal actions still ask first."
        : "JARVIS can open and read pages in your signed-in Chrome, but cannot click or type.";
    } catch { /* never block the HUD on this */ }
  }
  window.__hudCheckRealBrowser = checkRealBrowser;
  checkRealBrowser();

  connect();
})();
