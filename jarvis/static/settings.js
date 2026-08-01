/* JARVIS settings — reads/writes /api/settings; keys go to .env, masked after save.
   Salvaged from legacy/, trimmed to the rebuilt app's real providers (slice 23). */
(() => {
  const $ = (id) => document.getElementById(id);
  let state = null;   // full payload from /api/settings

  async function load() {
    state = await (await fetch("/api/settings")).json();
    const s = state.settings;

    // Brain (Gemini-only for now)
    $("brainActive").value = s.brain.active in { gemini: 1 } ? s.brain.active : "gemini";
    $("brainModel").value = (s.brain.models && s.brain.models.gemini) || "";
    $("brainKey").value = "";
    $("brainKey").placeholder = state.keys.gemini || "paste key — stored in .env, shown masked";
    const bok = state.availability.brain.gemini;
    $("brainStatus").textContent = bok ? "configured ✓" : "no key";
    $("brainStatus").className = bok ? "status-ok" : "status-no";

    // TTS
    $("ttsActive").value = s.tts.active;
    $("elevenKey").value = "";
    $("elevenKey").placeholder = state.keys.elevenlabs || "paste key to unlock voice picker";
    renderTTS();
    loadEdgeVoices(s.tts.edge_voice);
    if (state.availability.tts.elevenlabs) loadElevenVoices(s.tts.elevenlabs_voice_id);

    // STT
    $("sttActive").value = s.stt.active;
    $("whisperModel").value = s.stt.whisper_model || "medium";
    $("whisperDevice").value = s.stt.whisper_device || "cuda";
    renderSTT();
    loadMics(s.stt.mic_device_index);

    // General
    $("wakeEnabled").checked = !!s.wake.enabled;
    $("wakeThreshold").value = s.wake.threshold ?? 0.5;
    $("wakeThresholdVal").textContent = Number($("wakeThreshold").value).toFixed(2);
    $("autostart").checked = !!s.autostart;

    // Capabilities & safety
    for (const el of document.querySelectorAll(".cap")) {
      el.checked = !!getPath(s, el.dataset.path);
    }
    $("confirmTimeout").value = s.confirm.timeout_s;
    // Slice 58: which CONFIRM actions the user has chosen to auto-approve.
    const approved = new Set((s.confirm && s.confirm.auto_approve) || []);
    for (const el of document.querySelectorAll(".okverb")) {
      el.checked = approved.has(el.dataset.verb);
    }
    $("smoothCursor").checked = !!s.input.smooth_cursor;
    $("realBrowser").checked = (s.web && s.web.profile_mode === "real");
    $("allowActions").checked = !!(s.web && s.web.allow_actions);
  }

  const getPath = (obj, path) => path.split(".").reduce((o, k) => (o || {})[k], obj);

  // ---------- conditional rendering ----------
  function renderTTS() {
    const v = $("ttsActive").value;
    $("elevenKeyRow").classList.toggle("hidden", !(v === "elevenlabs" || v === "auto"));
    $("edgeVoiceRow").classList.toggle("hidden", v === "pyttsx3");
    $("elevenVoiceRow").classList.toggle("hidden",
      !state.availability.tts.elevenlabs || v === "pyttsx3" || v === "edge_tts");
  }
  function renderSTT() {
    $("whisperRow").classList.toggle("hidden", $("sttActive").value !== "local_whisper");
  }

  // ---------- pickers ----------
  async function loadEdgeVoices(current) {
    try {
      const data = await (await fetch("/api/voices?provider=edge_tts")).json();
      const sel = $("edgeVoice"); sel.innerHTML = "";
      for (const v of data.voices) {
        const o = document.createElement("option");
        o.value = v.id; o.textContent = v.label; sel.appendChild(o);
      }
      if (current) sel.value = current;
    } catch { $("edgeVoice").innerHTML = "<option value=''>couldn't load voices (offline?)</option>"; }
  }
  async function loadElevenVoices(current) {
    try {
      const data = await (await fetch("/api/voices?provider=elevenlabs")).json();
      const sel = $("elevenVoice"); sel.innerHTML = "";
      for (const v of data.voices) {
        const o = document.createElement("option");
        o.value = v.id; o.textContent = v.label; sel.appendChild(o);
      }
      if (current) sel.value = current;
      $("elevenVoiceRow").classList.remove("hidden");
    } catch {}
  }
  async function loadMics(current) {
    try {
      const data = await (await fetch("/api/mics")).json();
      const sel = $("micSelect");
      for (const d of data.devices.filter(d => d.real || d.index === current)) {
        const o = document.createElement("option");
        o.value = d.index; o.textContent = `[${d.index}] ${d.name}`;
        o.dataset.name = d.name;  // pin by name — indices shift on Windows
        sel.appendChild(o);
      }
      if (current !== null && current !== undefined) sel.value = String(current);
      if (data.auto) $("micHint").textContent = `auto-detected: [${data.auto.index}] ${data.auto.name}`;
    } catch { $("micHint").textContent = "mic list unavailable (PyAudio not loaded)"; }
  }

  // ---------- save ----------
  async function save() {
    const keys = {};
    if ($("brainKey").value.trim()) keys.gemini = $("brainKey").value.trim();
    if ($("elevenKey").value.trim()) keys.elevenlabs = $("elevenKey").value.trim();

    const caps = {};
    for (const el of document.querySelectorAll(".cap")) {
      const [sec, key] = el.dataset.path.split(".");
      (caps[sec] = caps[sec] || {})[key] = el.checked;
    }
    // real-browser mode lives in the same `web` section as web.enabled — set it
    // here so the `...caps` spread doesn't clobber it.
    (caps.web = caps.web || {}).profile_mode =
      $("realBrowser").checked ? "real" : "isolated";
    caps.web.allow_actions = $("allowActions").checked;

    const settings = {
      brain: { active: "gemini",
               models: { ...state.settings.brain.models, gemini: $("brainModel").value.trim() || state.settings.brain.models.gemini } },
      tts: {
        active: $("ttsActive").value,
        edge_voice: $("edgeVoice").value || state.settings.tts.edge_voice,
        elevenlabs_voice_id: $("elevenVoice")?.value || state.settings.tts.elevenlabs_voice_id,
      },
      stt: {
        active: $("sttActive").value,
        whisper_model: $("whisperModel").value,
        whisper_device: $("whisperDevice").value,
        mic_device_index: $("micSelect").value === "" ? null : Number($("micSelect").value),
        mic_device_name: $("micSelect").value === "" ? ""
          : ($("micSelect").selectedOptions[0]?.dataset.name || ""),
      },
      wake: { enabled: $("wakeEnabled").checked, threshold: Number($("wakeThreshold").value) },
      autostart: $("autostart").checked,
      confirm: {
        timeout_s: Number($("confirmTimeout").value),
        auto_approve: [...document.querySelectorAll(".okverb")]
          .filter((el) => el.checked).map((el) => el.dataset.verb),
      },
      input: { smooth_cursor: $("smoothCursor").checked },
      ...caps,
    };

    const note = $("saveNote");
    note.textContent = "Applying…"; note.className = "note";
    try {
      const resp = await fetch("/api/settings", {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ keys, settings }),
      });
      if (!resp.ok) throw new Error(await resp.text());
      note.textContent = "Saved — applied live."; note.className = "note ok";
      await load();
    } catch (e) { note.textContent = `Save failed: ${e.message}`; }
  }

  // ---------- wiring ----------
  $("ttsActive").addEventListener("change", renderTTS);
  $("sttActive").addEventListener("change", renderSTT);
  $("wakeThreshold").addEventListener("input", () =>
    $("wakeThresholdVal").textContent = Number($("wakeThreshold").value).toFixed(2));
  $("saveBtn").addEventListener("click", save);
  $("ttsTestBtn").addEventListener("click", async () => {
    $("ttsTestBtn").textContent = "Speaking…";
    try { await fetch("/api/tts_test", { method: "POST", headers: { "Content-Type": "application/json" }, body: "{}" }); }
    finally { setTimeout(() => $("ttsTestBtn").textContent = "Speak a test line", 2500); }
  });

  load();
})();
