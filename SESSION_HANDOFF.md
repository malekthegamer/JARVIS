# JARVIS Rebuild — Session Handoff

> Paste this into a new Claude Code session to continue the build with full context.
> Last updated: 2026-07-11, after **Slice 11 (email compose + send)**. See `git log --oneline` for the tip.

---

## 0. TL;DR for the next session

You are continuing a **from-scratch rebuild of JARVIS** — a voice-driven agent that controls a Windows 11 PC. The single source of truth for **what to build** is **`JARVIS_Spec_v1.md`** (read it first). **How to build** is now codified in **`CLAUDE.md`** (auto-loaded — the discipline below runs by default, no need to type `/fable-mode`) and **`HARNESS.md`** (the concrete techniques with examples).

- **Built & working (slices 1–11):** voice loop, reactive HUD with a live **Action Log + telemetry**, PC-control primitives (launch/close/read-screen/click/type/press), a fail-closed **CONFIRM** gate + hard **BLOCKED** tier, a **vision fallback** for icon-only controls, real **multi-step agentic chains** (visible plan, replan, retry guards), **wider primitives** (browser tab list/close, caged file search, volume/media/brightness), **`run_shell`** (denylist + verbatim-command confirm + tree-kill timeout), **encrypted long-term memory**, and **`send_email`** (Gmail API `gmail.send`-scope OAuth, verbatim-message confirm, caged attachments). **364 tests passing (0 failed, 0 skipped).**
- **Live app right now:** `python run.py` serves a HUD at `http://127.0.0.1:8000`. Brain = Gemini `gemini-3.1-flash-lite`. Configured secrets: `GEMINI_API_KEY`, `TEST_SELF_EMAIL` (live-email-test recipient), plus the Gmail OAuth artifacts under `data/email/`.
- **The 4 spec acceptance scripts (§1.6):** #1 Spotify→Discover Weekly ✅, #2 close tabs except YouTube ✅, #3 find invoice→email Sam ✅ (slice 11, live-verified), #4 volume+brightness+DND ⚠ (volume/media ✅, brightness honestly unsupported on this monitor, **DND deferred — no clean Windows API**). Status tracked in `REGRESSION_CHECKPOINT.md`.
- **Not built yet:** inbox reading/triage, DND/Focus Assist, web/browser automation beyond tab-close, wake word (real engine), tray app, and memory refinements (semantic retrieval, pinned prefs). See §7.

---

## 1. The goal (from JARVIS_Spec_v1.md)

JARVIS is **not a chatbot that answers — it is an agent that acts.** The heart is a **perceive → plan → act → verify → correct** loop. Foundations:

1. **PC control** — a library of primitives (the "verbs") composed by an LLM "brain". Accessibility-first (Windows UI Automation) with a **vision fallback**. Every primitive carries a **safety tier**: **AUTO** (runs immediately), **CONFIRM** (pause → show exactly what it will do → wait for yes), or **BLOCKED** (refused outright — implemented in slice 9).
2. **Reactive HUD** — an Iron-Man-style UI: a central orb reflecting state (IDLE/LISTENING/THINKING/EXECUTING/SPEAKING/CONFIRMING), a transcript, a chain **plan strip**, an **Action Log**, and **telemetry** (CPU/RAM/GPU/window/clock).
3. **Memory** — durable, encrypted, cross-session facts the user explicitly asks to keep.

---

## 2. What we built, slice by slice

Each slice = staged commits, tests-first, ending in a live end-to-end verification. `git log --oneline` is clean and readable.

### Slices 1–5 (foundations — earlier sessions)
1. **Walking skeleton** — old app archived to `legacy/`; new code in `jarvis/`. `state.py` broadcaster (THE UI seam), Gemini brain (`google-genai`, echoes 3.x `thought_signature`), salvaged voice (`voice/capture.py` — never rewrite), FastAPI+WS server, minimal HUD.
2. **PC-control primitives** — `screen.py`, `ui_tree.py` (pywinauto **uia**), `apps.py`; the **executor** (`primitives/__init__.py`) wraps every call in act→observe→verify. Intent routing = Gemini's own tool-calling.
3. **CONFIRM gate** — `core/confirmations.py` (fail-closed, single-flight, every abnormal path denies); `delete_file` (caged to `data/agent_files/`), `close_window`; amber HUD modal; **WS chat is fire-and-forget** (deadlock fix).
4. **Input synthesis** — `input.py` click/type_text/press_keys; **tier decided from the RESOLVED element name, not the model's paraphrase**; resolve_target **fails with candidates, never guess-clicks**. Live bugs fixed: pydirectinput drops uppercase (use pyautogui); focus-theft re-verify-before-each-keystroke.
5. **Vision fallback** — `vision.py`, engages ONLY when the fast text path can't name an element; a **second entrance to the SAME gate, never a bypass**. Live findings: Gemini boxes are `[ymin,xmin,ymax,xmax]` 0–1000; the model **confabulates controls at confidence 1.0** (defense is the gate + `from_point` guard, not model honesty).

### Slice 6 — Multi-step agentic chains (spec §1.3)
- `core/chain.py` `ChainTracker` (one per `think()`): broadcasts `plan`/`step`/`chain_end` events through the state broadcaster; `plan_steps` is a brain-level meta-tool (declares/revises the visible plan). EXECUTING detail = `k/N · tool`.
- **Mechanical guards (never trust the model):** identical-retry breaker (exact repeat after FAILED → BLOCKED, no exec), failure budget (3 → abort), CANCELLED mid-chain → mechanical abort of the rest, honest `MAX_TOOL_ROUNDS` exhaustion.
- **`MAX_TOOL_ROUNDS` raised 8→12** after the live Spotify acceptance (script #1 needs ~10 rounds). Two primitive bugs found live & fixed: Spotify has no App Paths key (Start-Menu `.lnk` resolution) and **retitles its window to the playing track** (matching now falls through exact-title → substring → owning-process).

### Slice 7 — HUD Action Log + telemetry (spec §2.3)
- **Action Log panel** renders the slice-6 feed: one row per tool call (marker `▸`/`✓`/`✕`/`⊘` + tool + args + verdict `note`), plan-revision separators, upsert-by-n, 80-row cap, snapshot hydration on refresh. Writes are never silent (memory/actions show here).
- **Telemetry panel**: CPU/RAM (psutil), GPU (`nvidia-smi`, every 3rd tick, cached), foreground window (win32gui), client clock. `_telemetry_forever()` samples ONLY while a HUD is connected and **bypasses the broadcaster** (state `seq` stream stays pure). Window titles rendered via `textContent` (injection-safe).
- Committed visual harness: `tests/harness_hud_visual.py` (Playwright DOM+screenshot).

### Slice 8 — Wider primitives (unblocks scripts #2, #4)
- `tabs.py` — `list_tabs` (AUTO) + `close_tabs` (**CONFIRM, gates ONCE per batch**; the modal names the resolved count/kept/samples; "keeping NONE" warns the window will close; execution re-resolves and skips vanished tabs). UIA on the running browser's tab strip; windows identified by owning process (chrome/msedge/brave).
- `files.search_files` (AUTO, name/ext/age, re-passes the two-belt cage).
- `system.py` — volume/mute (pycaw; this pycaw's `GetSpeakers()` returns an `AudioDevice` wrapper → use `.EndpointVolume`), media keys (VK codes, unknown key fails closed), brightness (sbc — **success requires a get readback**; `sbc.set` silently no-ops on unaddressable displays; honestly unsupported on this monitor).

### Slice 9 — `run_shell` (highest-risk verb)
- Three controls: **(1) narrow named denylist** (`root_recursive_delete` / `disk_format_or_wipe` / `fork_bomb`, target-anchored) → returns the spec's third tier **BLOCKED**; `execute()` returns before any modal, never runs. Explicitly a **backstop, not a boundary** — a test proves base64-obfuscated `rm -rf /` reaches CONFIRM (we don't over-claim). **(2) CONFIRM on the VERBATIM command** — a monospace amber box in the modal, **no model summary** (a summary by a possibly-injected model is anti-safety). **(3) honest execution** — cmd.exe, `communicate(timeout=shell.timeout_s=30)` + `taskkill /T /F` **process-tree** kill; exit 0 is the ONLY success; never raises.
- `shell.enabled` kill switch (default True) withholds it from the schema when off. No approval leaks across a chain (each call = fresh gate, proven). `chain.status_from_result` maps `BLOCKED`→failed. User confirmed cmd.exe (not PowerShell) + enabled-by-default.

### Slice 10 — Long-term, cross-session memory (spec §1.5)
- `core/dpapi.py` (Windows DPAPI — keyless, user-bound encryption at rest) + `core/memory.py` `MemoryStore` (singleton `memory_store`): DPAPI-encrypted JSON at `data/memory/memories.bin` (gitignored). **Honest degradation** — missing/corrupt/foreign store → empty, never crash; write **REFUSES** when encryption unavailable (never plaintext).
- **Design decisions (user-approved):** (a) **EXPLICIT-INTENT writes only** — the model calls `remember` only when asked; the prompt forbids inferred/silent saves. (b) Encryption THIS slice (DPAPI). (c) **Relevance-gated retrieval** — lexical content-token overlap ≥ threshold, top-k; an unrelated message injects **no memory block** (anti-pollution + the structural defense against surfacing sensitive facts unprompted). (d) Visibility+delete in scope. (e) `forget` **NEVER guesses** — >1 match deletes nothing and lists candidates (like slice-4 resolve_target).
- Brain: `system_prompt(memory_block="")`, retrieval computed ONCE per message in `_think_inner` (wrapped → "" on any error, never breaks `think()`); `remember`/`recall`/`forget` are brain-level meta-tools (like `plan_steps`, show in the Action Log). In-session `brain.history` is separate and unchanged — this slice persists **only explicit facts, never transcripts**.

---

### Slice 11 — Email compose + send (spec §1.6 script #3)
- `primitives/email.py` — `send_email` (CONFIRM): the **first outward-reaching, irreversible verb**, treated like run_shell. Validation fails closed BEFORE the modal (single RFC-plausible recipient, CR/LF header-injection refusal, empty-message refusal, attachment caged to `data/agent_files/` via `files._contained`). The modal's mono box shows a **mechanically-built verbatim block** — To / Subject / exact resolved attachment path + size / FULL body, never truncated, **no model summary** (slice-9 doctrine; reused the `command` confirm field — hud.css needed nothing, the slice-9 box was already pre-wrap + scroll).
- **Transport:** Gmail API, **`gmail.send` scope only** (least privilege — user-approved over SMTP app-password), OAuth token **DPAPI-encrypted** at `data/email/token.bin`; runtime NEVER opens a consent browser mid-chain (no token → honest FAILED naming the setup: put the OAuth client at `data/email/credentials.json`, run `python -m jarvis.primitives.email` once). The runner **re-validates after approval** (vanished attachment → clean FAILED, nothing sent). `email.enabled` kill switch withholds the tool from the schema. Success = "**accepted** by the server (message id …)" — never "delivered".
- **Binding test rule:** live tests send ONLY to `TEST_SELF_EMAIL` (from `.env`, never hardcoded); the chain test's auto-approver declines any modal whose To: differs. Script #3 live E2E passed: model → search_files → CONFIRM (block named recipient + exact attachment path) → Gmail accepted (id returned) → chain `done`. Prompt gained: never guess/invent an address (+ fixed a stale "not yet wired up" claim about shell/system-settings).

## 3. Architecture & repo map

```
e:\J.A.R.V.I.S\
  JARVIS_Spec_v1.md         ← SOURCE OF TRUTH (what to build)
  CLAUDE.md                 ← HOW to build (auto-loaded; the discipline runs by default)
  HARNESS.md                ← concrete techniques + test-suite methods
  SESSION_HANDOFF.md        ← this file
  REGRESSION_CHECKPOINT.md  ← the 4 spec scripts' live status + baseline
  run.py                    ← entry: python run.py  (--no-open to skip browser)
  data/settings.json        ← live settings (git-ignored): brain/tts/stt/confirm/vision/telemetry/shell/memory
  data/agent_files/         ← the ONLY file sandbox (delete_file, search_files)
  data/memory/memories.bin  ← DPAPI-encrypted long-term memory (git-ignored)
  .env                      ← secrets (git-ignored). Only GEMINI_API_KEY.
  legacy/                   ← the ENTIRE old app. Salvage source only. NOT live.

  jarvis/
    state.py                ← AgentState enum + broadcaster (THE UI seam); emit() for chain/telemetry
    brain.py                ← JarvisBrain orchestrator. Gemini tool-calling loop, MAX_TOOL_ROUNDS=12.
                              plan_steps + remember/recall/forget meta-tools. memory retrieval in
                              _think_inner. system_prompt(memory_block). never-crash contract.
    server.py               ← FastAPI + WS. Fire-and-forget chat. One ordered queue. _telemetry_forever.
    config.py               ← paths, .env, get_api_key(), BASE_DIR/DATA_DIR
    core/
      confirmations.py      ← fail-closed CONFIRM gate; optional verbatim `command` field (slice 9)
      chain.py              ← ChainTracker: plan/step/chain_end, retry breaker, failure budget, args+note
      memory.py             ← MemoryStore (DPAPI-encrypted), relevance-gated retrieve, forget-never-guesses
      dpapi.py              ← win32crypt protect/unprotect + available()
      settings_store.py     ← DEFAULT_SETTINGS + hot-reload
      errors.py             ← ProviderError + classify_exception
    primitives/             ← the "verbs" + the executor
      __init__.py           ← PRIMITIVES registry + execute() (tier: auto|confirm|blocked) + _gate + tools_schema
      screen.py ui_tree.py apps.py files.py windows.py input.py vision.py   (slices 2–5)
      tabs.py               ← list_tabs (AUTO) / close_tabs (CONFIRM)          (slice 8)
      system.py             ← volume/mute/media/brightness (AUTO)              (slice 8)
      shell.py              ← run_shell + denylist + classify (BLOCKED/CONFIRM) (slice 9)
      email.py              ← send_email: validate/classify + verbatim block + Gmail (slice 11)
                              also the one-time OAuth setup: python -m jarvis.primitives.email
    providers/              ← self-registering: brain/gemini, stt/google, tts/edge_tts+pyttsx3
    voice/                  ← capture.py (HARD-WON, DO NOT rewrite), playback.py, voice_manager.py
    static/                 ← the HUD (vanilla JS): index.html, hud.css, hud.js, orb.js, fonts/
                              chain strip, Action Log + telemetry panels, monospace shell-confirm box

  tests/                    ← 364 tests. pytest. Live/model tests gated on GEMINI_API_KEY
                              (+ TEST_SELF_EMAIL & the Gmail token for email-live).
    harness_hud_visual.py   ← Playwright DOM+screenshot HUD checker (slice 7+)
    harness_email_modal.py  ← email CONFIRM modal vision harness (slice 11)
    harness_iconpad.py      ← Tk icon surface for the vision path (slice 5)
    test_email.py test_email_live.py
    test_memory.py test_memory_live.py test_shell.py test_tabs.py test_system.py test_chain.py
    test_chain_live.py test_agent_loop.py test_vision.py test_input.py test_confirmations.py
    test_confirm_primitives.py test_primitives.py test_server.py test_state.py test_brain.py
    test_tts.py test_mic.py test_smoke.py conftest.py
```

### Request lifecycle
1. HUD sends chat over WS (or push-to-talk → `/api/listen` → STT).
2. `server._run_chat` (fire-and-forget) → `jarvis_brain.think(text)`.
3. Brain: THINKING → retrieve relevant memory into the system prompt → Gemini tool-calling loop (ChainTracker tracks each call). Plain text = conversation; tool call = action.
4. Tool call → meta-tool (`plan_steps`/`remember`/`recall`/`forget`) OR `primitives.execute(name, args)`:
   - `_decide_tier`: `blocked` → refuse, no gate, no run; `confirm` → CONFIRMING → `confirmations.request(desc, command=…)` → wait for the user; `auto` → run.
   - EXECUTING → `_run_*` wrapper → act + **verify** (screen-diff / UIA readback / window presence / exit code) → evidence string.
5. Reply → transcript + spoken (SPEAKING) → IDLE. Action Log + strip render throughout.

### The safety model (protect above all)
- **Tiers from ground truth, not the model's words** (resolved UIA name, vision's pixel label, the literal shell command).
- **Fail closed everywhere.** Unknown combo → CONFIRM. Vision uncertain → CONFIRM. Gate error/timeout → cancel. Denylisted shell → BLOCKED. Encryption unavailable → refuse to store.
- **CONFIRM shows ground truth, never a model paraphrase/summary.** Denylist is a **backstop, not a boundary** (documented + tested).
- Residual risks are **documented and pinned by tests**, not hidden (§5).

---

## 4. How to run & test

```powershell
cd e:\J.A.R.V.I.S
python run.py                 # serve HUD + open browser
python run.py --no-open       # serve only; open http://127.0.0.1:8000 yourself

python -m pytest tests/ -q    # full suite: 364 passed, 0 failed, 0 skipped (~5 min; launches/kills
                              # Notepad + a throwaway Chrome; needs a real desktop; live tests need the key)
python -m pytest tests/test_memory.py tests/test_shell.py -q   # inner loop: touched files only
```
- **Capture the real exit code** (piping to `tail` masks pytest's): `python -m pytest tests/ -q > run.log 2>&1; echo "EXIT=$?"; tail -3 run.log`.
- **Port 8000 stuck** (a stopped background server orphans it): `Get-NetTCPConnection -LocalPort 8000 -State Listen | ForEach-Object { Stop-Process -Id $_.OwningProcess -Force }`.
- **Visual check**: `python run.py --no-open` + Playwright via `window.__hudEvent(event)` / `window.__hudSetState(state, detail)` → screenshot → **`Read` the PNG and inspect** + DOM asserts (pattern in `tests/harness_hud_visual.py`).

---

## 5. Known gaps / limitations (honest, carried forward)

- **Vision** can misidentify a destructive control as safe; confabulates on blank targets (gate + `from_point` are the defense); English-only destructive vocab; click-only. Staleness: one call per click.
- **run_shell denylist is a BACKSTOP, not a boundary** — trivially defeated by obfuscation (tested). CONFIRM is the primary control. cmd.exe only (no PowerShell). No VM/sandbox isolation.
- **Memory** retrieval is lexical — misses pure paraphrase (embeddings = future); stable "always-on" preferences aren't surfaced unless the query overlaps (future `pinned` flag); over-eager `remember` is mitigated (explicit-intent prompt + Action-Log visibility + `forget`), not eliminated; DPAPI ties decryptability to this Windows account.
- **Brightness** genuinely unsupported on this monitor (honest failure is the shipped UX). **DND/Focus Assist** deferred — no clean Windows API.
- **Email**: "accepted by server" is the strongest verifiable claim (send-only scope can't check delivery). The verbatim modal is the ONLY control over a prompt-injected composition — it depends on the user reading it. Google test-mode OAuth refresh tokens expire after **7 days** unless the OAuth app is published to production. Send-only, one recipient, one caged attachment; no inbox reading (deliberate).
- **Script #4** needs DND.
- **Focus**: a fullscreen exclusive app can block input; the code aborts honestly rather than fire into the wrong window.
- **Flaky test note**: `test_chain_live.py::test_live_failing_step_hits_budget_not_infinite` is live-model-dependent; it accepts any bounded terminal state (budget/exhausted/done/cancelled). Re-run in isolation before calling any live test a regression.

---

## 6. How we operate (now the DEFAULT, via CLAUDE.md)

The four-stage discipline runs automatically every session — **you do not need to be told to "Fable it".** `CLAUDE.md` is the directive; `HARNESS.md` is the technique. In short:

- **Plan before code.** Plan mode → a plan with a literal Definition of Done, staged entry/exit criteria, a file map, a task-specific risk register, and **tests named before code**. No implementation before the user approves. **User-added approval conditions are binding** (turn each into a test — e.g. "forget must never guess").
- **Build tests-first per stage.** Write the named tests, watch them fail for the right reason, implement to green. Name deviations; amend the file map. (Red-check trick: `git stash` the impl to prove a trust-critical test fails without it.)
- **Self-test:** `python -m pytest tests/ -q` → 0 failed, 0 skipped. Paste real output. Re-run a failing live test in isolation and find the cause before calling it flaky.
- **Verify:** for visual goals, screenshot and **actually look**; for live behavior, verify by an independent signal (a window title, a readback), not the model's claim. Restore any state you changed.
- **Report honestly** (Objective/Status/Tests/Hostile/Deviations/**Known gaps**/Next) and **commit per stage** with the trailer `Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>`.

**Environment:** Windows 11, PowerShell (Bash tool also available), Python 3.12 (global installs, no venv). The machine may have a fullscreen app up (focus-steal) — prefer `window_hint` targeting. Long suites/servers: run in the **background**, don't poll. Salvage proven hardware-facing code from `legacy/`, don't rewrite it.

**Persistent memory:** a project memory file lives at `~/.claude/projects/e--J-A-R-V-I-S/memory/jarvis-rebuild-slice1.md` (keep it updated). Convention: **every finished slice ends with a "How to test this slice" section, unprompted** (`slice-test-guide-convention.md`).

---

## 7. Suggested next slices (not yet built)

Pick one and plan it. In rough priority:

1. **DND / Focus Assist** — unblocks script #4's last clause (the only spec script still ⚠). No clean public API — needs a deliberate approach (Focus Assist toggle via undocumented paths, or a scoped registry/notification approach); design carefully and flag the method.
2. **Web / browser automation beyond tab-close** — navigate, read page content, fill forms (Playwright driving the real browser, or reuse the vision loop in-page). Enables richer scripts.
3. **Wake word** — a real engine (Porcupine/openWakeWord), not substring matching. Then a **tray app** for always-on.
4. **Memory refinements** — semantic/embedding retrieval (lexical misses paraphrase); a `pinned` always-on preferences category; a HUD memory-manager panel; per-memory sensitivity tags.
5. **Vision hardening** — OCR/ensemble to cut confabulation & misidentification; i18n for the destructive vocab.
6. **PowerShell as a second shell** for `run_shell` (currently cmd.exe only); **undo / dry-run / persistent audit log**.
7. **Email widenings** (each a deliberate slice, not a default): multiple recipients/CC, attachments beyond the cage, inbox reading (a much larger privacy surface — treat like a new risk category again).

Also deferred: ElevenLabs/Claude/OpenAI/Ollama/Whisper providers (they sit in `legacy/` until their slice).

---

## 8. First moves in the new session
1. Read `JARVIS_Spec_v1.md`, this file, and `CLAUDE.md` (the discipline is already in force).
2. `git log --oneline -30` for the slice history; `python -m pytest tests/ -q` to confirm **364** green (0 failed, 0 skipped).
3. Skim `REGRESSION_CHECKPOINT.md` for the 4 acceptance scripts' live status.
4. Ask the user which slice is next (or they'll tell you), then plan it in plan mode.
