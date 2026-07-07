/* JARVIS settings page — reads/writes /api/settings; keys go to .env, masked after save. */
(() => {
  const $ = (id) => document.getElementById(id);
  let state = null;          // full payload from /api/settings
  const KEYED_BRAINS = { gemini: "gemini", openai: "openai", claude: "claude", openrouter: "openrouter" };
  const enteredKeys = {};    // provider -> newly typed key (only sent if user typed)

  async function load() {
    state = await (await fetch("/api/settings")).json();
    const s = state.settings;

    // Brain
    const brainSel = $("brainActive");
    brainSel.innerHTML = "";
    for (const name of state.provider_names.brain) {
      const opt = document.createElement("option");
      opt.value = name;
      opt.textContent = `${name}${state.availability.brain[name] ? " ✓" : ""}`;
      brainSel.appendChild(opt);
    }
    brainSel.value = s.brain.active;
    renderBrain();

    // TTS
    $("ttsActive").value = s.tts.active;
    $("elevenKey").placeholder = state.keys.elevenlabs || "paste key to unlock voice picker";
    renderTTS();
    loadEdgeVoices(s.tts.edge_voice);
    if (state.availability.tts.elevenlabs) loadElevenVoices(s.tts.elevenlabs_voice_id);

    // STT
    $("sttActive").value = s.stt.active;
    $("whisperModel").value = s.stt.whisper_model;
    $("whisperDevice").value = s.stt.whisper_device;
    $("openaiKey").placeholder = state.keys.openai || "shared with the OpenAI brain";
    renderSTT();
    loadMics(s.stt.mic_device_index);

    // General
    $("wakeWord").value = s.wake_word;
    $("autostart").checked = !!s.autostart;
  }

  // ---------- conditional rendering ----------
  function renderBrain() {
    const name = $("brainActive").value;
    const s = state.settings;
    $("brainModel").value = s.brain.models[name] || "";
    $("brainModelRow").classList.toggle("hidden", name === "ollama");
    $("ollamaModelRow").classList.toggle("hidden", name !== "ollama");
    $("brainKeyRow").classList.toggle("hidden", !(name in KEYED_BRAINS));
    if (name in KEYED_BRAINS) {
      $("brainKey").value = "";
      $("brainKey").placeholder = state.keys[name] || "paste key — stored in .env, shown masked";
    }
    const ok = state.availability.brain[name];
    $("brainStatus").textContent = ok ? "configured ✓" : (name === "ollama" ? "server not reachable" : "no key");
    $("brainStatus").className = ok ? "status-ok" : "status-no";
    if (name === "ollama") {
      const sel = $("ollamaModel");
      sel.innerHTML = "";
      if (state.ollama_models.length) {
        for (const m of state.ollama_models) {
          const opt = document.createElement("option");
          opt.value = m; opt.textContent = m;
          sel.appendChild(opt);
        }
        sel.value = state.ollama_models.includes(s.brain.models.ollama) ? s.brain.models.ollama : state.ollama_models[0];
        $("ollamaHint").textContent = "";
      } else {
        $("ollamaHint").textContent = "No local models found — start Ollama and `ollama pull llama3.1:8b`.";
      }
    }
  }

  function renderTTS() {
    const v = $("ttsActive").value;
    $("elevenKeyRow").classList.toggle("hidden", !(v === "elevenlabs" || v === "auto"));
    $("edgeVoiceRow").classList.toggle("hidden", v === "pyttsx3");
    $("elevenVoiceRow").classList.toggle("hidden", !state.availability.tts.elevenlabs || v === "pyttsx3" || v === "edge_tts");
  }

  function renderSTT() {
    const v = $("sttActive").value;
    $("whisperRow").classList.toggle("hidden", v !== "local_whisper");
    $("openaiKeyRow").classList.toggle("hidden", v !== "openai_whisper");
  }

  // ---------- pickers ----------
  async function loadEdgeVoices(current) {
    try {
      const data = await (await fetch("/api/voices?provider=edge_tts")).json();
      const sel = $("edgeVoice");
      sel.innerHTML = "";
      for (const v of data.voices) {
        const opt = document.createElement("option");
        opt.value = v.id; opt.textContent = v.label;
        sel.appendChild(opt);
      }
      if (current) sel.value = current;
    } catch { $("edgeVoice").innerHTML = "<option value=''>couldn't load voices (offline?)</option>"; }
  }

  async function loadElevenVoices(current) {
    try {
      const data = await (await fetch("/api/voices?provider=elevenlabs")).json();
      const sel = $("elevenVoice");
      sel.innerHTML = "";
      for (const v of data.voices) {
        const opt = document.createElement("option");
        opt.value = v.id; opt.textContent = v.label;
        sel.appendChild(opt);
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
        const opt = document.createElement("option");
        opt.value = d.index; opt.textContent = `[${d.index}] ${d.name}`;
        opt.dataset.name = d.name;  // pin by name too — indices shift on Windows
        sel.appendChild(opt);
      }
      if (current !== null && current !== undefined) sel.value = String(current);
      if (data.auto) $("micHint").textContent = `auto-detected: [${data.auto.index}] ${data.auto.name}`;
    } catch { $("micHint").textContent = "mic list unavailable (PyAudio not loaded)"; }
  }

  // ---------- save ----------
  async function save() {
    const brainName = $("brainActive").value;
    const models = { ...state.settings.brain.models };
    if (brainName === "ollama") {
      if ($("ollamaModel").value) models.ollama = $("ollamaModel").value;
    } else {
      models[brainName] = $("brainModel").value.trim() || models[brainName];
    }
    const keys = {};
    if ($("brainKey").value.trim()) keys[brainName] = $("brainKey").value.trim();
    if ($("elevenKey").value.trim()) keys.elevenlabs = $("elevenKey").value.trim();
    if ($("openaiKey").value.trim()) keys.openai = $("openaiKey").value.trim();

    const payload = {
      keys,
      settings: {
        brain: { active: brainName, models },
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
        wake_word: $("wakeWord").value.trim() || "jarvis",
        autostart: $("autostart").checked,
      },
    };
    const note = $("saveNote");
    note.textContent = "Applying…"; note.className = "note";
    try {
      const resp = await fetch("/api/settings", {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify(payload),
      });
      if (!resp.ok) throw new Error(await resp.text());
      note.textContent = "Saved — providers hot-swapped."; note.className = "note ok";
      await load();
    } catch (e) {
      note.textContent = `Save failed: ${e.message}`;
    }
  }

  // ---------- wiring ----------
  $("brainActive").addEventListener("change", renderBrain);
  $("ttsActive").addEventListener("change", renderTTS);
  $("sttActive").addEventListener("change", renderSTT);
  $("saveBtn").addEventListener("click", save);
  $("ttsTestBtn").addEventListener("click", async () => {
    $("ttsTestBtn").textContent = "Speaking…";
    try { await fetch("/api/tts_test", { method: "POST", headers: { "Content-Type": "application/json" }, body: "{}" }); }
    finally { setTimeout(() => $("ttsTestBtn").textContent = "Speak a test line", 2500); }
  });

  load();
})();
