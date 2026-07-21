# JARVIS Rebuild — Session Handoff

> Paste this into a new Claude Code session to continue the build with full context.
> Last updated: 2026-07-20, after **Slice 32 (real-filesystem access — browse + delete-to-Recycle-Bin + create shortcut)**. See `git log --oneline` for the full slice history.

---

## 0. TL;DR for the next session

You are continuing a **from-scratch rebuild of JARVIS** — a voice-driven agent that controls a Windows 11 PC. The single source of truth for **what to build** is **`JARVIS_Spec_v1.md`** (read it first). **How to build** is codified in **`CLAUDE.md`** (auto-loaded — the plan→build→self-test→vision-check discipline runs by default, no need to type `/fable-mode`) and **`HARNESS.md`** (the concrete techniques with real examples).

**Capability set, built slice by slice (1–32), grouped by area:**
- **Core loop & HUD:** voice loop (push-to-talk + wake word), a reactive HUD (orb states, transcript, Action Log, telemetry, chain plan strip), a fail-closed **CONFIRM** gate + hard **BLOCKED** tier, real **multi-step agentic chains** (visible plan, replan, retry guards).
- **PC control:** launch/close/read-screen/click/type/press/**scroll** primitives (click supports `kind=single|double|right` — slice 29), a **vision fallback** for icon-only controls (measured accuracy + pre-click point verification so the control you approve is the control that gets clicked), **app discovery** (desktop shortcuts + Steam + Epic library URIs — not just registry App Paths), **smooth cursor motion**, and a **win32 latency fix** that cut a typical multi-step chain from 34.4 s to ~5.3 s.
- **Wider verbs:** browser tab list/close, **caged file authoring** (write/read/search/delete in `data/agent_files/` — write is AUTO-create/CONFIRM-overwrite and undoable; slice 30), **real-filesystem access** (slice 32: `list_directory`/`delete_path`/`create_shortcut` ANYWHERE on the PC — deletes go to the Recycle Bin, every mutation CONFIRM-gated on the verbatim path, catastrophic paths BLOCKED), **clipboard** (get/set — AUTO, content redacted from the audit log; slice 31), volume/media/brightness, **`run_shell`** (denylist + verbatim-confirm + tree-kill), **`send_email`** (Gmail API, verbatim-confirm, caged attachments), **`set_dnd`/`get_dnd`** (real Settings toggle + readback).
- **Memory:** DPAPI-encrypted long-term memory with **semantic (local embedding) retrieval + pinned always-on preferences**, explicit-intent writes only, forget-never-guesses.
- **Web:** an **isolated** sandbox browser (navigate/read/fill/click, untrusted-content boundary, cross-origin + committal-click gating) plus keyless **`web_search`** — AND, as of slices 24–25, an opt-in **real-browser mode**: JARVIS can drive a dedicated real Chrome logged into the user's own accounts, first navigate+read only, now (behind a second opt-in) able to **click/type/submit** on the user's real sites with committal actions CONFIRM-gated. As of slice 27, a **click that would leave the current host** is re-gated through the same cross-origin CONFIRM as a navigate (anchor destinations resolved pre-click; JS-driven jumps flagged post-click) — closing the slice-25 residual.
- **Trust & operability:** a **persistent audit log** (every action, including declined/BLOCKED, as DPAPI-encrypted JSONL) with a **read-only HUD viewer** (slice 28: `/audit` — an envelope-first records browser; verbatim args stay encrypted until you reveal a specific record) + a mechanical **dry-run mode** + **undo** (slice 26: `undo_last_action` walks back the newest reversible action — volume/mute/brightness/DND, a just-stored memory, a just-deleted workspace file, which now quarantines instead of unlinking; irreversible verbs are test-pinned as never-undoable); a **settings page** salvaged from the legacy app (`/settings`) covering brain/TTS/STT/wake/autostart/capability kill-switches + the new real-browser toggles, with ElevenLabs TTS and local-Whisper STT ported as working backends.
- **Ops discipline:** a fullscreen desktop guard (the full suite refuses to start over a game), a measured PC-control latency harness, and hard-won test-isolation lessons (see §5 and the "known gaps" entries below) baked into `CLAUDE.md`/`HARNESS.md`.

**Tests: 705 passed, 0 failed, 0 skipped is the baseline** (deterministic core is 100% reliable; live-model tests need a healthy Gemini quota — see §4 and §8). All this is proven live, not just unit-tested — every slice ends in a real end-to-end acceptance run, several with mechanical (not model-claimed) verification.

- **Two durable measurement harnesses exist for vision** (numbers you can re-run, not vibes): `tests/harness_vision_eval.py` (localization / confabulation / unsafe-AUTO) and `tests/harness_click_verify_eval.py` (catch / **false-refusal** / wrong-click). Plus `tests/harness_memory_eval.py` (retrieval recall) and `tests/harness_latency_eval.py` (per-seam wall-clock).
- **Live app right now:** `python run.py` serves the HUD at `http://127.0.0.1:8000` (push-to-talk); `/settings` is the settings page (gear icon in the HUD header). **`python -m jarvis.tray`** runs server + tray icon (Open HUD / toggle wake word / Quit). Brain = Gemini `gemini-3.1-flash-lite`. Configured secrets: `GEMINI_API_KEY`, `TEST_SELF_EMAIL`, Gmail OAuth artifacts under `data/email/`. Wake word needs no key (openWakeWord is local).
- **All 4 spec acceptance scripts (§1.6) pass:** #1 Spotify→Discover Weekly ✅, #2 close tabs except YouTube ✅, #3 find invoice→email Sam ✅, #4 brightness+DND ✅ (brightness honestly unsupported on this monitor — hardware, not code). Status tracked in `REGRESSION_CHECKPOINT.md`.
- **Not built yet:** multi-brain (OpenAI/Claude/Ollama — visibly disabled in settings, "not ported yet"), inbox reading/triage, committal desktop-native automation hardening beyond what `input.py` already does. **Spotify Web API was probed (2026-07-18) and is a policy dead-end without Premium** — Feb 2026 dev-mode rules require the app owner to hold Premium for ALL endpoints; script #1 stays on its proven GUI path. See §7.

---

## 1. The goal (from JARVIS_Spec_v1.md)

JARVIS is **not a chatbot that answers — it is an agent that acts.** The heart is a **perceive → plan → act → verify → correct** loop. Foundations:

1. **PC control** — a library of primitives (the "verbs") composed by an LLM "brain". Accessibility-first (Windows UI Automation) with a **vision fallback**. Every primitive carries a **safety tier**: **AUTO** (runs immediately), **CONFIRM** (pause → show exactly what it will do → wait for yes), or **BLOCKED** (refused outright — implemented in slice 9).
2. **Reactive HUD** — an Iron-Man-style UI: a central orb reflecting state (IDLE/LISTENING/THINKING/EXECUTING/SPEAKING/CONFIRMING), a transcript, a chain **plan strip**, an **Action Log**, and **telemetry** (CPU/RAM/GPU/window/clock).
3. **Memory** — durable, encrypted, cross-session facts the user explicitly asks to keep.

---

## 2. What we built, slice by slice

Each slice = staged commits, tests-first, ending in a live end-to-end verification. `git log --oneline` is clean and readable. This section is in **build order (1→25)** — read it top to bottom for the real history.

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

### Slice 11 — Email compose + send (spec §1.6 script #3)
- `primitives/email.py` — `send_email` (CONFIRM): the **first outward-reaching, irreversible verb**, treated like run_shell. Validation fails closed BEFORE the modal (single RFC-plausible recipient, CR/LF header-injection refusal, empty-message refusal, attachment caged to `data/agent_files/` via `files._contained`). The modal's mono box shows a **mechanically-built verbatim block** — To / Subject / exact resolved attachment path + size / FULL body, never truncated, **no model summary** (slice-9 doctrine; reused the `command` confirm field).
- **Transport:** Gmail API, **`gmail.send` scope only** (least privilege), OAuth token **DPAPI-encrypted** at `data/email/token.bin`; runtime NEVER opens a consent browser mid-chain (no token → honest FAILED naming the setup: put the OAuth client at `data/email/credentials.json`, run `python -m jarvis.primitives.email` once). The runner **re-validates after approval** (vanished attachment → clean FAILED, nothing sent). `email.enabled` kill switch. Success = "**accepted** by the server (message id …)" — never "delivered".
- **Binding test rule:** live tests send ONLY to `TEST_SELF_EMAIL` (from `.env`, never hardcoded). Script #3 live E2E passed.

### Slice 12 — DND / Focus Assist (spec §1.6 script #4's last clause)
- **Stage 0 gate earned its keep.** The plan's primary method (WNF write) *looks* like it works (NTSTATUS 0) but a semantic cross-check proved it **drives nothing the user sees** (the real Settings toggle still read 0). Method **pivoted (user-approved) to the public UI surface.**
- `primitives/system.py` — `set_dnd`/`get_dnd` drive the real `ms-settings:notifications` **"Do not disturb" ToggleSwitch** via UIA and **confirm by readback**. `_dnd_session()` opens Settings (closes it only if *we* launched it), matches by automation_id or visible name, re-resolves the element every call (UIA handles go stale). Not-found/no-pattern/UIA-raises → honest failure. **AUTO tier** (reversible, low-stakes) but visible (~2–4 s Settings flash + focus-steal) — the only silent path was proven dead.
- **Verified:** live script #4 through the real brain: `set_brightness` FAILED honestly → `set_dnd` OK readback-confirmed → chain `done`.

### Slice 13 — Wake word ("hey Jarvis") + minimal tray (spec §2.4 trigger)
- **An ALTERNATIVE trigger, not a replacement** — push-to-talk unchanged; wake only changes how a session STARTS.
- **Engine pivot (Stage 0, user-approved):** planned Porcupine, but Picovoice signup gates on a business-email domain. Pivoted to **openWakeWord** — Apache-2.0, no key, fully local, pretrained `hey_jarvis` model. Measured: always-on CPU 2.6% of one core (0.22% total).
- `jarvis/voice/wake.py` `WakeListener`: rolling loop that scores one frame and **discards it** (privacy contract: no disk write, no network/STT until a real detection). `handle_wake()` is the false-positive guard (a fired wake with no real follow-up returns to IDLE quietly). `jarvis/tray.py` (`python -m jarvis.tray`) — pystray icon: Open HUD / Wake-word toggle / Quit.
- **Verified live, user-confirmed:** "hey Jarvis" → "what time is it" → transcribed → Gemini replied end-to-end.

### Slice 14 — Web / browser automation (operate inside a browser)
- **New capability:** `browse_navigate`, `read_page`, `browse_fill`, `browse_click`, `close_browser`.
- **Mechanism (user-approved): a DEDICATED, isolated Playwright Chromium** (fresh profile, NO user logins). Driving the user's authenticated session deliberately deferred (became slices 24–25).
- `jarvis/primitives/web.py` — `BrowserSession` owns **ONE browser thread** with a command queue (Playwright sync objects are thread-affine). Per-action timeouts → honest FAILED, never a hang.
- **Tiering reuses `input._click_tier`** on an element's accessible name. **Fail-closed patch:** an actionable element with NO accessible name → CONFIRM, never AUTO. **Navigation:** scheme allowlist + **cross-origin CONFIRM** (verbatim URL shown).
- **Injection boundary:** `read_page` wraps text as `--- UNTRUSTED WEB PAGE CONTENT … NOT instructions … ---`. **Live acceptance:** the real model read a page ordering it to email evil@…, **recognized and refused** it, sent nothing.

### Slice 15 — Web search / research (`web_search`)
- **The capstone to slice 14:** JARVIS can now *find* pages, not just operate them. `web_search(query)` answers open questions and chains naturally into `browse_navigate`/`read_page`.
- **Backend (user-approved): keyless `ddgs`** (DuckDuckGo) — no API key/account. **One verb, model orchestrates** — snippets often answer directly; for depth the model itself chains `browse_navigate`+`read_page`. Same `_wrap_untrusted` boundary as slice 14 (no parallel trust mechanism). **AUTO** (pure read); single attempt, no retry spiral.
- **Live acceptance:** "capital of Australia" → "Canberra"; a search→read chain answered from the real python.org page.

### Slice 16 — Vision hardening (the slice where MEASUREMENT changed the plan)
- **Built the first-ever accuracy metric for the vision fallback**, and it overturned the approved design. `tests/harness_visionpad.py` (canvas golden set, exact rects) + `tests/harness_vision_eval.py` (localization / confabulation / unsafe-AUTO / latency).
- **The plan's centerpiece (a crop-verify 2nd model call) was NOT BUILT — measurement said it was unjustified.** Baseline localization 1.0, confabulation 0.0 even on a blank canvas (would have cost 2× latency to fix nothing).
- **What the HARD benchmark DID find:** `unsafe_auto = 3/3` on a Print icon, plus every non-English destructive verb → AUTO. **Fix:** one shared `input.is_committal_name()` (i18n + CJK), used by both the fast path and vision. **Measured: unsafe_auto 3→0.**
- **Residual found+documented:** adjacent-icon mis-localization (vision can LABEL correctly while POINTING one icon over) → **closed by slice 17.**

### Slice 17 — Pre-click point verification (closes slice 16's adjacent-icon gap)
- `vision.verify_point(point, window_hint, approved_label)` — the last guard before the mouse moves: (1) UIA name where one exists (free), (2) grounded crop re-read with a deliberately NON-LEADING question, (3) an independent risk cross-check via `is_committal_name` (a benign approval can never be waved onto a committal control).
- **Cannot launder a stale approval:** re-reads a fresh screenshot at click time; on mismatch refuses, never clicks, never auto-retries.
- **Measured (48 samples):** wrong-click rate 0.042 → 0.000 (catch rate 1.00), false-refusal 0.023, latency ~2× on the vision path only.

### Slice 18 — Persistent audit log + dry-run (spec §1.4's unbuilt second half)
- **Audit log:** ONE recording exit in `primitives.execute()` no return path can skip — approved/declined/timed-out/BLOCKED/unknown/crashed all land one JSONL line in `data/audit/`. **Privacy posture: split record** — plaintext envelope (ts/chain/tool/tier/gate/status/dry_run) + DPAPI-encrypted payload (verbatim args, capped result). Rotation at `audit.max_file_mb`, never auto-deletes. Dump CLI: `python -m jarvis.core.audit`.
- **Dry-run:** a leading `dry run:` prefix parsed at `server._respond()` sets `dry_run` on the tracker; `execute()` checks it BEFORE the gate and returns a narration. Never prompt-trusted (red-checked). Only argument-complete classifiers (`run_shell`, `send_email`) still classify in dry-run.
- **Verified:** 26 new tests incl. a gated live dry-run — real model, "dry run: open notepad and type hello" → no Notepad process appeared.

### Slice 19 — Semantic memory retrieval + pinned preferences (slice 10's two named gaps)
- **Metric BEFORE mechanism:** `tests/harness_memory_eval.py` — a frozen golden set. Baseline: lexical paraphrase recall **0.000**.
- **Mechanism (user-approved): local ONNX embeddings** — `jarvis/core/embedder.py`, all-MiniLM-L6-v2 (zero new pip deps). API embeddings rejected explicitly (network + quota + privacy regression). One-time `python -m jarvis.core.embedder --setup`.
- **Measured:** paraphrase recall **0.000 → 0.818**, false-surface rate unchanged at 0.067 (semantic added recall with zero new unrelated surfaces).
- **Pinned prefs:** `remember(pinned=true)` renders a STANDING PREFERENCES block on EVERY message; excluded from relevance top-k; `forget` unpins.

### Slice 20 — PC-control latency PROFILE (measurement only)
- The user reported PC control felt slow. Profiled before touching anything (slice-16/19 "metric before mechanism"): `tests/harness_latency_eval.py` monkeypatch-wraps timing around existing seams (all restored; no product change), drives real chains, attributes >99% of wall-clock.
- **Finding: the bottleneck is UIA window enumeration, NOT the model/audit/orchestration.** Notepad chain 34.4 s median: `win_resolve` 55%, launch-poll 14%, readback 14%, model only 11%. Committed harness = a re-runnable baseline. Fix deferred to slice 21 (measure first, then propose).

### Slice 21 — win32 window resolution (the latency fix)
- **Probe-justified (slice-12/13 Stage-0 doctrine):** `input._target_window` enumerated the whole desktop UIA tree *twice* per `type_text`/`press_keys` — measured ~1658 ms *per enumeration*. `win32gui.EnumWindows` returns the same windows in **0.3 ms** (~6000×), and `hwnd → Desktop(backend="uia").window(handle=hwnd).wrapper_object()` is 2.4 ms and functionally identical.
- **The fix — HWND is the token, not the title** (avoids a win32-vs-UIA title-string mismatch): `ui_tree._win32_windows() -> (title, pid, hwnd)` feeds `list_windows`/`window_present`/`window_present_for_process`; `windows.find_window(hint) -> (hwnd, title)` keeps the exact→substring→process-name precedence; `input._target_window` resolves via `find_window` then wraps the hwnd.
- **Measured: Notepad chain 34.4 s → ~5.3 s; `win_resolve` 55% → ~0%.** The model round-trips (~3 s) are now the largest single cost — as they should be.
- **Deliberately OUT of scope:** DND and browser-tabs window resolution stay on UIA (UWP/ApplicationFrameHost, not in the hot path). No within-chain window cache (at ~3 ms per resolve the redundancy is free).

### Slice 22 — App discovery (desktop/Steam/Epic) + smooth cursor
- **The bug:** `launch_app` couldn't find Rocket League or desktop-shortcut apps (no App Paths/PATH/Start-Menu trace — probe-confirmed, and the probe corrected the premise: this machine's RL is the **Epic** version, manifest AppName `"Sugar"`; Steam's real root is `e:/steam` via registry, not the default path).
- **`jarvis/primitives/app_discovery.py`:** desktop `.lnk` (file AND folder targets) + `.url` (Steam's own shortcuts carry `steam://` URIs); Steam registry root → `libraryfolders.vdf` → `appmanifest_*.acf` (normcase-deduped libraries); Epic ProgramData `*.item` manifests. **API-first:** launch specs are the launchers' documented URI protocols, not raw exes. `find()`: normalization strips ®/™; exact > prefix > substring; same normalized name across sources = same app (source-priority resolve), genuinely different names = candidates + **no launch** (never guess-launch). Runs only AFTER the fast ladder misses.
- **Executor:** game URIs get a `apps.game_window_wait_s` (20 s) window poll + honest-dispatch message when the window isn't up yet — never a false OK. Folders open via Explorer.
- **Live acceptance (mechanical):** "open rocket league" → RocketLeague.exe up 13 s after the request; "open ArtTuneDB" (old resolver: None) → real Explorer window.
- **Smooth cursor (cosmetic, bounded):** `input._move_cursor` eased glide (measured 152.5 ms real-screen), exact final `moveTo` always (landing pixel identical), kill switch `input.smooth_cursor`.
- **API-first audit delivered** (report, no code): the one true miss is **Spotify** (script #1 drives the desktop app; the Web API covers it but needs Premium + OAuth — future-slice candidate).

### Slice 23 — Settings page salvaged from legacy (+ ElevenLabs TTS, local-Whisper STT)
- The rebuilt app had **no settings surface**; the legacy app's full hot-applying page was salvaged and modernized. `/settings` (gear link in the HUD header) exposes every legacy feature working against a real backend, plus a new **"Capabilities & safety"** module the legacy app never had.
- **Providers ported** (`jarvis/providers/tts/elevenlabs_provider.py`, `stt/local_whisper_provider.py`): ElevenLabs (key-gated, voice picker + quota, degrades to edge on quota-out); local-Whisper (the hard-won Blackwell/sm_120 float16 note preserved; `find_spec` install check). New settings keys: `tts.elevenlabs_voice_id`, `stt.whisper_model/device`, top-level `autostart`.
- **Server API:** `GET/POST /api/settings`, `/api/voices`, `/api/mics`, `/api/tts_test`, `/settings`. GET **masks every key** (test-pinned: no raw secret in any payload); POST writes keys to `.env`, deep-merge-saves + hot-reloads providers, starts/stops the wake listener on toggle, syncs autostart, audits the save. `jarvis/core/autostart.py` (HKCU Run key → `tray_start.pyw` root launcher).
- **The page:** Brain is Gemini-only with openai/claude/ollama shown as **disabled "not ported yet"** (honest — multi-brain is a future slice). **Vision check passed.**

### Slice 24 — Real-browser mode (drive a real logged-in Chrome; navigate + read)
- Slice 14 deferred "driving the user's authenticated session" as its own slice — this is it. `web.profile_mode="real"` (default `"isolated"`) makes JARVIS operate a **dedicated real Chrome** logged into the user's accounts; "go to \<any site\>" opens it there. General navigation, not YouTube-specific.
- **S0 pivot gate earned its keep** (two mechanisms probed DEAD before any build): (1) `launch_persistent_context` on the real **Default** profile HANGS (Chrome self-relaunch breaks Playwright's pipe); (2) `--remote-debugging-port` on the **default dir** is BLOCKED by **Chrome 136+** (this machine runs 150) — Google deliberately prevents automating the literal Default profile. **VERIFIED working:** launch real Chrome on a **separate `--user-data-dir`** (`data/browser_profile/`) with the debug port, then `connect_over_cdp`. The user signs into each site **once**; it persists. JARVIS's Chrome **coexists** with the user's everyday Chrome.
- **`jarvis/primitives/web.py`:** `BrowserSession` gains a mode captured at launch. Teardown terminates **only our launched pid** (never a broad taskkill).
- **Safety (biggest risk expansion in the project at the time, bounded 4 ways):** real mode was **navigate + read ONLY** in this slice — `browse_click`/`browse_fill` withheld from `tools_schema` AND refused via classify (later unlocked, gated, in slice 25); cross-origin CONFIRM still gates host jumps; untrusted-content boundary still wraps reads; separate profile = only sites the user signed in are reachable.
- **Settings-page toggle** ("Use my real logged-in Chrome") — vision-checked. **Live acceptance:** launched JARVIS's dedicated Chrome, navigated youtube/example/wikipedia, read content, `close_browser` killed only our pid. Found+fixed a client-side-redirect race (also fixed isolated mode).

### Slice 25 — Act in the real browser (click/type/submit on logged-in sites, committal-gated)
- Unlocks the committal browser actions slice 24 deliberately withheld — the user wanted JARVIS to actually DO things ("search MrBeast, open a random video"; "open Claude, type a prompt"). Behind a SECOND default-off opt-in `web.allow_actions` (real mode stays navigate+read until it's on).
- **Gating (user-chosen): committal-only CONFIRM.** Typing + clicking videos/links = AUTO (smooth); committal actions (post/buy/send/delete/submit) CONFIRM via the reused `input._click_tier`/`is_committal_name`, and the confirm names the SITE ("Click 'Delete' on youtube.com"). Off (default) → `browse_click`/`browse_fill`/`browse_key` withheld from the schema AND refused via classify — both layers gate on `allow_actions`.
- **Hardened the primitives so the tasks actually run (`web.py`):** new **`browse_key`** (Enter/Tab/Escape/arrows — presses the `:focus` locator, not the global keyboard which didn't reach a focused search box); **contenteditable fill** (Claude/ChatGPT ProseMirror); **editability-aware fill** (`_first_editable` skips look-alikes — YouTube's search *button* shares the input's "Search" aria-label, the bug the live run exposed); **click awaits navigation**; read cap 40→60; **stale-Chrome reaper** in `_launch_real` (kills only the dedicated-profile pid — test-pinned it spares the user's Chrome).
- **Live acceptance (real brain + signed-in Chrome):** "go to YouTube, search MrBeast, open a video" → an actual `watch?v=…` page opened end-to-end; Claude's ProseMirror box proven fillable. Two live gaps found+fixed (the YouTube-button fill match; the harness lacking a cross-origin CONFIRM approver).
- Settings sub-toggle "Let JARVIS click & type on my sites" (bound to `allow_actions`, honest warning, vision-checked). Isolated + slice-24 navigate/read byte-identical.
- **Residuals (honest):** benign clicks/typing un-gated on the real account (the user's chosen trade); a click that itself triggers cross-host navigation isn't re-gated; rich editors vary by site (contenteditable is best-effort); `browse_key Enter` is AUTO (a committal submit should be a gated button click). Committal automation is the whole point of the CONFIRM line here.

### Slice 26 — Undo (spec §1.4's last unbuilt clause)
- **Stage-0 pivot (the Spotify finding):** this slice was planned after the user's requested Spotify Web API integration died at probe: **Feb 2026 Spotify policy requires the app owner to hold Premium for ALL Development-Mode endpoints** (not just playback — search and own-playlist reads too), and the account is free; Extended Quota Mode needs a registered business + 250k MAU. Same doctrine as slice 12's WNF no-op: the probe killed the plan before any code. Script #1 stays on its proven GUI path; revisit only if Premium is ever added.
- **Mechanism:** `jarvis/core/undo.py` — a bounded (5), process-scoped, in-memory LIFO (`UndoStack` singleton, like `memory_store`). Capture sites push an `UndoEntry` AFTER their action succeeds; `undo_fn`s call **mechanism-level** functions (`system.set_volume`, `files.restore_file`, `MemoryStore.delete_by_id`) — never the registry wrappers, so an undo can never recursively push undo entries. Pop-on-attempt: a failed undo reports honestly and is NOT kept (a permanently-failing entry would jam everything beneath it).
- **Undoable:** volume/mute/brightness (paired `get_*` pre-read in the `_run_set_*` wrappers; no pre-read → no entry), DND (`set_dnd` surfaces the pre-toggle state it already read — no second Settings open; no entry on the already-in-state no-op), `remember` (delete-by-id of the exact record just created — sidesteps `forget`'s ambiguity path entirely), `delete_file` (**quarantines** to `data/agent_trash/<time_ns-token>/<rel path>` instead of unlinking; trash lives OUTSIDE the cage so search/delete can't see it — a named plan amendment; restore refuses to overwrite a newer occupant; retention 20, oldest purged).
- **Deliberately NOT undoable (test-pinned):** media keys (edge-triggered, no state), close_tabs (titles only, no URLs — a reopen would be a guess), send_email/run_shell (categorically irreversible). The negative test is the boundary's pin.
- **`undo_last_action`** is a brain-level meta-tool (the remember/forget precedent): own schema, `_execute_tool` dispatch, own audit splice, dry-run narrates via peek without popping. Empty stack → honest "nothing to undo".
- **Live-proven (mechanical):** real brain set volume→25 then "undo that" → pycaw readback restored the exact pre-level (`test_undo_live.py`, in-suite); real brain deleted a workspace file (CONFIRM approved) then "undo that — bring the file back" → restored byte-identical.

### Slice 27 — Re-gate cross-host navigation from a browser click
- **Closes the slice-25 residual** "a click that itself triggers cross-host navigation isn't re-gated." The asymmetry: `classify_navigate` already CONFIRMs a cross-origin `browse_navigate` (verbatim URL), but `classify_web_click` reasoned only on the element's **name** — so a benign-looking link ("Read more") whose href points at a different host navigated away with no checkpoint. On the real logged-in Chrome that's how page content could walk JARVIS onto an unintended authenticated site.
- **Knowable case (anchors), pre-click gate:** `_describe`/`_match_clickable`/`find_clickable` now surface the anchor's absolute `href` (DOM `el.href`). A new shared `_cross_host(url)->str|None` (host comparison factored out of `classify_navigate`, which now reuses it — identical behaviour) lets `classify_web_click` CONFIRM when the resolved href leaves the current host, naming the destination host and putting the verbatim URL in the mono box — **in both isolated and real mode**, regardless of whether the name reads benign. Fires *before* the click.
- **Unknowable case (JS navigation), post-click honesty:** a button that sets `location` via JS exposes no pre-click destination (can't be pre-gated without heavyweight request interception — rejected as deadlock-risky and disproportionate). Instead `session.click()` compares host before/after and **flags** a cross-host move in its result ("Note: this moved to a different site (host)"). `go_back()` deliberately not done (page already loaded; any *action* on the new host is still gated). Residual narrowed to a *named, benign, JS-navigating* control — detected, never silent.
- **Scope:** safety gap ONLY (user-confirmed). Rich-editor `fill()` coverage and a HUD "acting-live" indicator remain separate future slices.
- **Live-proven:** real brain told to open a page and click a cross-host link → a CONFIRM naming the destination host fired while STILL on the origin host (auto-approver recorded host-at-confirm-time = the origin), proving pre-click gating (`test_web_live.py::test_live_cross_host_click_prompts_before_navigating`).

### Slice 28 — Audit-log HUD viewer (read-only records browser)
- **The trust payoff of slice 18.** The durable audit log (every `execute()` + memory mutation, DPAPI-encrypted, `data/audit/`) had no UI — only the decrypt CLI. This adds `/audit`: a read-only page to *see* what JARVIS did (tool / tier / gate / status / dry_run) and, on explicit request, the verbatim args.
- **Envelope-first / reveal-on-demand (user-chosen privacy model):** `GET /api/audit?tail=N` returns only the **plaintext envelope** timeline — the sensitive payload is NEVER decrypted for a browse (a test pins that a seeded secret-marker arg is absent from the list). `GET /api/audit/{index}/payload` is the sole path that decrypts ONE record, only when the user clicks "reveal".
- **`audit.py` gained two read primitives** (the sole owner of the file format keeps it): `read_envelopes()` (no-decrypt, tags each with its absolute line `index` + `has_payload`) and `read_payload(index)` (decrypt one; out-of-range → None; enc-null/undecryptable → `payload_error`). `read()` and the CLI are unchanged. Absolute line index is the record handle (appends don't shift it; a mid-session rotation → honest "not available").
- **Front-end:** `static/audit.{html,js,css}` reuses the settings-page shell + hud.css tokens; a filterable table (tier/status/tool + tail selector), per-row reveal into an amber mono box (the `.confirm-command` aesthetic), honest "no data" for enc-null rows and "no records" for an empty log. All record text rendered via `textContent` (injection-safe). One `🗎` link beside the settings gear in the HUD header. **No new mutation path — read-only, localhost-only.**
- **Testability seam:** a `JARVIS_AUDIT_FILE` env override on the `audit_log` singleton lets the visual harness point a real server at a seeded temp log without touching `data/audit/`.
- **Vision check PASSED** (`harness_audit_visual.py`): 12 DOM asserts (tier/status badges, dry marker, enc-null "no data", reveal shows the decrypted arg) + the screenshot Read claim-by-claim (badges visually distinct, revealed payload in the amber mono box).

### Slice 29 — Spec §1.2 input completion: scroll + double/right click
- **Closes a silently-missing spec gap.** §1.2's action list names `scroll`, `double_click`, `right_click` (plus `move_mouse`, `drag`) — none existed and none were documented as deferred. Impact was mechanical: the agent couldn't reach below-the-fold content, open items in Explorer, or use context menus.
- **`scroll(direction, amount, window)`** — new AUTO tool. `click` gained an optional **`kind = single|double|right`** (default single), threaded through BOTH the fast text path and the vision-point path; `_click_xy` maps kinds to pydirectinput/pyautogui `click`/`doubleClick`/`rightClick`. **`classify_click` is untouched — kind NEVER weakens the gate** (a right/double on a "Delete" target still CONFIRMs; the slice-17 vision `verify_point` still guards vision double/right). Test-pinned.
- **STAGE-0 MECHANISM PIVOT (the load-bearing lesson):** the first cut used a synthetic **mouse-wheel** (`pyautogui.scroll`) — it shipped mocked-green but was **live-dead**. Five probes proved synthetic wheel (`pyautogui.scroll` / `mouse_event WHEEL` / `WM_MOUSEWHEEL`) moves a real Win11 WinUI Notepad **at most once then never** (foreground confirmed intact). **Keyboard PageUp/PageDown into the focused control moved the view every press.** So scroll is **keyboard paging, vertical only** (left/right fail closed — no clean keyboard analog); `amount` = page steps, clamped 1..`input.scroll_max_notches` (20). Same "probe kills the planned mechanism, pivot to what works" doctrine as slices 12/24.
- **Verify by WINDOW-REGION diff:** a scroll changes only the target window, a small fraction of the full desktop (real page-scroll ≈ 0.5% whole-screen but ≈ 12% of the window region). `scroll()` returns the window `bbox`; `_run_scroll` diffs only that crop (threshold 0.02) — below-threshold reports honestly ("the view didn't change — likely already at the end"), never a false OK.
- **Live-proven (mechanical):** real Notepad scrolled via `_run_scroll` (region-diff VERIFIED; at-the-top scroll honestly reports no change); and a **real-brain chain** ("scroll down in this window") called `scroll` → the executor's region-diff independently confirmed a **13.1% view change**, not the model's word.
- **Deliberate deferrals (now documented, so they stop being *silent* gaps):** `drag` (rare, hard to verify honestly, genuinely riskier — sliders/selection/file-move; its own slice if ever needed), `move_mouse` (no user-visible value — cursor motion is already internal, slice 22), horizontal scroll (no reliable keyboard analog), `clipboard`/`wifi` from §1.2's system_control (clipboard a future candidate; wifi dubious value/risk). (Caged file authoring — the runner-up — shipped in slice 30.)

### Slice 30 — Caged file authoring: write_file + read_file
- **Closes the audit's #2 gap AND a shipped falsehood.** The workspace `README.md` (written by `files.py`) promised the user "Files JARVIS may manage (**create**/delete on request) live here" — but only delete+search existed. Now create/read/delete are all real, and the README text is corrected (a test pins that every verb the README claims is a registered tool — no shipped falsehood).
- **`write_file(name, content)`** — UTF-8 text into `data/agent_files/`, caged by the existing `_contained()` (reused verbatim). **Tier is dynamic (`classify_write_file`): AUTO to create a new file, CONFIRM to overwrite an existing one** (modal names it). Both are **undoable** (slice-26 quarantine): an overwrite quarantines the prior bytes first, and `undo_last_action` restores them over the new file (`restore_file(token, over=True)`); undoing a create deletes the created file. Size-capped (`files.max_write_kb`, 256). Parent subdirs created inside the cage.
- **`read_file(name)`** — AUTO, size-capped (`files.max_read_kb`), output wrapped in the untrusted-content boundary (`web._wrap_untrusted` — a workspace file may hold web content JARVIS saved, so its bytes are DATA, never instructions).
- **Reuse, not new machinery:** factored `_quarantine(target)->token` out of `delete_file` (both call it, byte-identical); added an **additive `over=` flag** to `restore_file` (default False keeps the delete-undo "won't clobber" safety, test-pinned; True powers overwrite-undo). No kill-switch — parity with delete/search, which have none; the cage + CONFIRM-on-overwrite + undo are the boundary.
- **Live-proven:** real brain "write '<marker>' to shopping.txt, then read it back" → the file on disk held the marker and the reply relayed it (verified from disk, not model-claimed). Composes with `send_email` (attachments already cage to the same folder).

### Slice 31 — Clipboard: get_clipboard + set_clipboard
- **Spec §1.2 system_control's last unbuilt verb.** The universal bridge to apps JARVIS can't drive well: `set_clipboard(text)` puts a draft on the clipboard for the user to paste (Ctrl+V) instead of fragile typing; `get_clipboard()` lets JARVIS consume what the user already copied ("map the address I just copied"). Backend = `pyperclip` (already a declared dep; Stage-0 probe confirmed it works). Process-global OS state → no window/focus targeting, far more reliable than the input primitives.
- **Both AUTO tier** (user-chosen; reads are non-destructive and only fire on explicit request — prompt uses the memory/remember explicit-intent framing). `set_clipboard` is **undoable** (restores the previous text via the slice-26 stack — only when there was non-empty prior text; a prior image/non-text clipboard can't be restored, documented honestly).
- **Privacy (user-chosen): content REDACTED from the audit log.** The clipboard can hold a password/2FA code, so a NEW general `redact_audit` registry flag makes `_audit_record` store the envelope (tool/tier/status) but a placeholder instead of the verbatim args/result. **Pinned:** a seeded secret marker is absent from the audit record; the seam is opt-in (normal verbs still log verbatim). The model still receives the real content for the task — only the durable log is redacted.
- **Untrusted boundary:** `get_clipboard` output goes through `web._wrap_untrusted("CLIPBOARD CONTENT", …)` — clipboard bytes may be copied off a hostile page, so they're DATA, never instructions (same discipline as read_file/read_page).
- **Live-proven:** real OS round-trip (set marker → get it back, prior clipboard restored in teardown) + a gated real-brain "put '<marker>' on my clipboard" → `pyperclip.paste()` equalled the marker (verified, not model-claimed). No kill-switch (parity with volume/media/DND, all ungated); cap `CLIPBOARD_MAX_CHARS`.

### Slice 32 — Real-filesystem access: browse + delete (Recycle Bin) + create shortcut
- **The big one — JARVIS now works on the user's WHOLE PC, not just `data/agent_files/`.** New `jarvis/primitives/fsaccess.py`: `list_directory` (browse any folder, AUTO), `delete_path` (delete anywhere → Recycle Bin), `create_shortcut` (make a `.lnk`, default Desktop). User explicitly asked for this + accepted the risk after the honest reframing below.
- **The security shift (surfaced to + chosen by the user):** this INVERTS the workspace allowlist into a **denylist** ("everything except dangerous"). A dangerous-path denylist can never be exhaustive — same lesson `run_shell` encodes ("backstop, not boundary"). So safety is **layered, not resting on the denylist**: (1) **the CONFIRM gate is the boundary** — every mutation shows the **verbatim resolved absolute path** and waits for approval (reuses the slice-9 `command` mono box); (2) a **BLOCKED denylist** refuses catastrophic targets outright (the Windows tree, Program Files/ProgramData/C:\Users roots, drive roots, the profile root, JARVIS's own dirs); (3) **deletes go to the Recycle Bin** (`SHFileOperation`+`FOF_ALLOWUNDO`) — recoverable, never a permanent unlink.
- **`classify_path_risk` runs on the RESOLVED path** (`.resolve()` follows `..` + symlinks) BEFORE the denylist check, so a traversal (`…\me\..\..\Windows\System32`) or a symlink can't smuggle a target past it — test-pinned. Case-insensitive (WindowsPath). "Ancestor of a protected anchor" is blocked too (deleting `C:\` or `C:\Users`).
- **Zero new deps** — Recycle Bin (`win32com.shell.SHFileOperation`), shortcuts (`WScript.Shell.CreateShortcut`), known folders (`SHGetFolderPath`) all via pywin32 (present). Stage-0 build probe recycled a temp file + made a `.lnk` before wiring. **Kill-switch `fs.enabled`** (default on) withholds all three verbs (most powerful surface → parity with shell/email/web).
- **Live-proven end to end (real brain):** created a Desktop shortcut to a temp folder (`.lnk` + TargetPath verified), deleted a temp file → gone (Recycle Bin), and **"delete System32" → refused, System32 intact**. Scope: browse+delete+shortcut; writing/moving/renaming anywhere is the deliberate next slice.

---

## 3. Architecture & repo map

```
e:\J.A.R.V.I.S\
  JARVIS_Spec_v1.md         ← SOURCE OF TRUTH (what to build)
  CLAUDE.md                 ← HOW to build (auto-loaded; the discipline runs by default)
  HARNESS.md                ← concrete techniques + test-suite methods
  SESSION_HANDOFF.md        ← this file
  REGRESSION_CHECKPOINT.md  ← the 4 spec scripts' live status + baseline
  run.py                    ← entry: python run.py  (--no-open to skip browser)
  tray_start.pyw            ← slice 23: 4-line root launcher the autostart Run key points at
  data/settings.json        ← live settings (git-ignored): brain/tts/stt/confirm/vision/telemetry/
                              shell/memory/web/apps/input/audit/autostart — see settings_store.py
  data/agent_files/         ← the ONLY file sandbox (delete_file, search_files)
  data/agent_trash/         ← slice 26: quarantined deletions (outside the cage on
                              purpose — search/delete can't see it; newest 20 kept)
  data/memory/memories.bin  ← DPAPI-encrypted long-term memory (git-ignored)
  data/audit/audit.jsonl    ← slice 18: persistent audit log (git-ignored)
  data/models/minilm/       ← slice 19: local embedding model (git-ignored; --setup to fetch)
  data/browser_profile/     ← slices 24-25: JARVIS's dedicated real-Chrome profile (git-ignored)
  .env                      ← secrets (git-ignored). GEMINI_API_KEY (+ optional ELEVENLABS_API_KEY etc).
  legacy/                   ← the ENTIRE old app. Salvage source only. NOT live.

  jarvis/
    state.py                ← AgentState enum + broadcaster (THE UI seam); emit() for chain/telemetry
    brain.py                ← JarvisBrain orchestrator. Gemini tool-calling loop, MAX_TOOL_ROUNDS=12.
                              plan_steps + remember/recall/forget meta-tools. memory retrieval in
                              _think_inner. system_prompt(memory_block). never-crash contract.
    server.py                ← FastAPI + WS. Fire-and-forget chat. One ordered queue. _telemetry_forever.
                              Settings API (slice 23): GET/POST /api/settings, /api/voices, /api/mics,
                              /api/tts_test, GET /settings.
    config.py                ← paths, .env, get_api_key(), BASE_DIR/DATA_DIR
    core/
      confirmations.py      ← fail-closed CONFIRM gate; optional verbatim `command` field (slice 9)
      chain.py              ← ChainTracker: plan/step/chain_end, retry breaker, failure budget, args+note
                              + chain_id (audit grouping) + dry_run flag (slice 18)
      audit.py              ← slice 18: AuditLog (durable JSONL, plaintext envelope +
                              DPAPI payload, rotation-never-delete) + dump CLI
                              (python -m jarvis.core.audit); records live in data/audit/
                              slice 28: read_envelopes() (no-decrypt timeline) +
                              read_payload(index) (decrypt ONE) power the /audit viewer;
                              JARVIS_AUDIT_FILE env override for the visual harness
                              slice 31: redact_audit registry flag → _audit_record stores the
                              envelope but a placeholder for content (clipboard is the first user)
      embedder.py            ← slice 19: local MiniLM sentence embeddings (onnxruntime;
                              no torch, no network, no key) + one-time setup CLI
                              (python -m jarvis.core.embedder --setup → data/models/minilm/)
      memory.py              ← MemoryStore (DPAPI-encrypted), semantic + relevance-gated retrieve,
                              pinned preferences (slice 19), forget-never-guesses,
                              delete_by_id (slice 26 — the undo path, no ambiguity)
      undo.py                 ← slice 26: bounded in-memory LIFO undo stack (UndoEntry +
                              UndoStack singleton); pushed by the set_* wrappers,
                              delete_file and remember; popped by undo_last_action
      dpapi.py                ← win32crypt protect/unprotect + available()
      autostart.py            ← slice 23: HKCU Run key -> tray_start.pyw (Windows startup toggle)
      settings_store.py       ← DEFAULT_SETTINGS + hot-reload
      errors.py               ← ProviderError + classify_exception
    primitives/               ← the "verbs" + the executor
      __init__.py              ← PRIMITIVES registry + execute() (tier: auto|confirm|blocked) + _gate
                              + tools_schema (real-mode/kill-switch withholding, slices 22-25)
      screen.py ui_tree.py apps.py files.py windows.py input.py vision.py   (slices 2–5)
                              ui_tree/windows/input window RESOLUTION is win32-fast (slice 21):
                              _win32_windows()/find_window() → hwnd → pywinauto wrap
                              (was a ~1.7s×2 UIA enum per keystroke-level action)
                              input.py also: is_committal_name (i18n+CJK, slice 16),
                              _move_cursor (smooth glide, slice 22),
                              scroll (KEYBOARD paging PageUp/Down, slice 29 — synthetic
                              wheel is dead on WinUI; verify via window-region diff) +
                              click kind=single|double|right (_click_xy, slice 29)
                              files.py: _contained cage + delete/search (slices 3/8),
                              quarantine+restore_file (slice 26), write_file/read_file +
                              _quarantine factored + classify_write_file (slice 30)
      fsaccess.py             ← slice 32: REAL-filesystem access (NOT caged). resolve_user_path
                              (aliases/env/~/.resolve), classify_path_risk (denylist BACKSTOP on
                              the RESOLVED path — Windows tree/roots/ancestors BLOCKED), verbs
                              list_directory/delete_path(→Recycle Bin, SHFileOperation+FOF_ALLOWUNDO)/
                              create_shortcut(WScript.Shell). CONFIRM-on-verbatim-path is the boundary
      app_discovery.py        ← slice 22: desktop .lnk/.url + Steam (vdf/acf) + Epic (*.item)
                              discovery fallback after apps.py's fast ladder misses
      tabs.py                 ← list_tabs (AUTO) / close_tabs (CONFIRM)          (slice 8)
      system.py                ← volume/mute/media/brightness (slice 8) + DND (slice 12, AUTO)
                              set_dnd/get_dnd drive the real Settings toggle via UIA + readback
                              get_clipboard/set_clipboard (slice 31, pyperclip; _clip_get/_clip_set
                              seams; AUTO; set is undoable; content redacted from audit)
      shell.py                 ← run_shell + denylist + classify (BLOCKED/CONFIRM) (slice 9)
      email.py                 ← send_email: validate/classify + verbatim block + Gmail (slice 11)
                              also the one-time OAuth setup: python -m jarvis.primitives.email
      web.py                   ← browser automation (slice 14): BrowserSession (own thread) +
                              navigate/read/click/fill/close; reuses input._click_tier; data boundary
                              + web_search (slice 15): keyless ddgs; reuses _wrap_untrusted boundary
                              + REAL-BROWSER MODE (slices 24-25): _launch_real (dedicated Chrome via
                              CDP), browse_key, contenteditable/editability-aware fill, stale-Chrome
                              reaper, allow_actions gating on classify_web_click/classify_web_fill
                              + CROSS-HOST CLICK GATE (slice 27): find_clickable surfaces the anchor
                              href, shared _cross_host() re-gates a click that leaves the host
                              (both modes), JS jumps flagged post-click in session.click()
    providers/                ← self-registering: brain/gemini, stt/google+local_whisper,
                              tts/edge_tts+pyttsx3+elevenlabs (whisper+elevenlabs ported slice 23)
    voice/                    ← capture.py (HARD-WON, DO NOT rewrite), playback.py, voice_manager.py
                              wake.py ← WakeListener "hey jarvis" (openWakeWord) + handle_wake (slice 13)
    tray.py                   ← system-tray app (pystray): Open HUD / toggle wake / Quit (slice 13)
                              launch: python -m jarvis.tray  (runs server + tray; run.py unchanged)
    static/                    ← the HUD (vanilla JS): index.html, hud.css, hud.js, orb.js, fonts/
                              chain strip, Action Log + telemetry panels, monospace shell-confirm box
                              settings.{html,css,js} ← the /settings page (slice 23, gear in HUD header;
                              real-Chrome + allow_actions toggles added slices 24-25)
                              audit.{html,css,js} ← the /audit viewer (slice 28, 🗎 in HUD header;
                              read-only records browser, envelope-first + reveal-on-demand)

  tests/                      ← 705 tests. pytest. Live/model tests gated on GEMINI_API_KEY
                              (+ TEST_SELF_EMAIL & the Gmail token for email-live). test_system
                              includes a live DND toggle (real Settings UI, restored after).
                              Wake/tray + deterministic web/search tests use fakes / local
                              fixtures / mocked ddgs (no internet); test_search_live hits the
                              real network (ddgs + a real site).
    harness_hud_visual.py     ← Playwright DOM+screenshot HUD checker (slice 7+)
    harness_email_modal.py    ← email CONFIRM modal vision harness (slice 11)
    harness_wake.py           ← self-paced live "hey jarvis" demo (slice 13; you run it, you speak)
    harness_iconpad.py        ← Tk icon surface for the vision path (slice 5)
    harness_visionpad.py      ← slice-16 GOLDEN SET: canvas controls w/ known rects
                              (easy | --blank | --hard dense toolbar + lookalikes)
    harness_vision_eval.py    ← slice-16 SCORER: localization / confabulation /
                              unsafe-AUTO / latency vs ground truth. THE vision metric.
    harness_click_verify_eval.py ← slice-17 SCORER: catch / FALSE-REFUSAL / wrong-click
                              rates for pre-click verification (both rates, always)
    test_audit.py              ← slice 18: store + splice tests (declined/timeout/blocked
                              all logged; write-failure loud-but-alive; DPAPI degradation)
                              + slice 28: read_envelopes (no-decrypt) / read_payload (by index)
    test_audit_api.py           ← slice 28: /api/audit envelope list (privacy-pinned: no
                              decrypted arg in the timeline) + /payload reveal + /audit page
    harness_audit_visual.py     ← slice 28: seeds a temp log (JARVIS_AUDIT_FILE), drives /audit,
                              reveals a record, screenshots (the vision check)
    test_dryrun.py              ← slice 18: mechanical dry-run guarantees + gated live dry-run
    test_desktop_guard.py       ← guard: full runs refuse to start over a fullscreen app
    harness_latency_eval.py     ← slice 20: PC-control latency profile (per-seam wall-clock;
                              proved UIA window-enum was the bottleneck → slice 21 fix)
    harness_memory_eval.py      ← slice 19: FROZEN retrieval golden set + scorer (paraphrase/
                              keyword recall, distractor top-1, FALSE-SURFACE, latency)
    test_app_discovery.py       ← slice 22: desktop/Steam/Epic discovery parsing + unique-match-
                              or-candidates safety
    test_settings_api.py        ← slice 23: /api/settings GET/POST, key masking, hot-reload
    harness_settings_visual.py  ← slice 23: settings-page Playwright DOM+screenshot checker
    harness_realbrowser_accept.py ← slice 24: announced live real-Chrome navigate+read acceptance
                              (--wait pauses for the user's one-time Google sign-in)
    harness_realbrowser_actions.py ← slice 25: announced live acceptance for click/type/submit
                              (the "search MrBeast, open a video" / "type into Claude" proof)
    test_web.py test_web_live.py test_search.py test_search_live.py
                              ← ALL pin web.profile_mode=isolated + allow_actions=False in an
                              autouse fixture — data/settings.json may have real mode persisted
                              from live testing, so this pin is REQUIRED for determinism
    test_undo.py                ← slice 26: stack semantics, every capture point, the
                              quarantine/restore honesty contract, and the
                              never-undoable negative (scope boundary pinned)
    test_undo_live.py           ← slice 26: gated live "set volume then undo" chain,
                              restore verified by pycaw readback
    test_wake.py test_tray.py
    test_email.py test_email_live.py
    test_memory.py test_memory_live.py test_shell.py test_tabs.py test_system.py test_chain.py
    test_chain_live.py test_agent_loop.py test_vision.py test_input.py test_confirmations.py
    test_confirm_primitives.py test_primitives.py test_server.py test_state.py test_brain.py
    test_files.py ← slice 30: write_file/read_file cage, overwrite-CONFIRM+undo,
                              untrusted-boundary read, no-shipped-falsehood pin, gated live
    test_clipboard.py ← slice 31: get/set seams mocked, undo, AUDIT-REDACTION pin
                              (secret absent from the record), real-OS roundtrip + gated live
    test_fsaccess.py ← slice 32: classify_path_risk denylist (blocked System32/roots/
                              ancestors, traversal+symlink resolved-then-blocked), blocked-never-
                              recycles, verbatim-path-in-modal, kill-switch, gated live (shortcut+
                              delete-to-recycle+refuse-System32)
    test_tts.py test_mic.py test_smoke.py conftest.py
```

### Request lifecycle
1. HUD sends chat over WS, or push-to-talk → `/api/listen` → STT, **or** the wake
   listener hears "hey jarvis" → `server._on_wake` → follow-up capture → STT.
   All three funnel into the same `_busy`-guarded `_respond` pipeline. (Web verbs
   run inside that pipeline like any other primitive, on the browser owner thread.)
2. `server._run_chat` (fire-and-forget) → `jarvis_brain.think(text)`.
3. Brain: THINKING → retrieve relevant memory into the system prompt → Gemini tool-calling loop (ChainTracker tracks each call). Plain text = conversation; tool call = action.
4. Tool call → meta-tool (`plan_steps`/`remember`/`recall`/`forget`) OR `primitives.execute(name, args)`:
   - `_decide_tier`: `blocked` → refuse, no gate, no run; `confirm` → CONFIRMING → `confirmations.request(desc, command=…)` → wait for the user; `auto` → run.
   - EXECUTING → `_run_*` wrapper → act + **verify** (screen-diff / UIA readback / window presence / exit code) → evidence string.
5. Reply → transcript + spoken (SPEAKING) → IDLE. Action Log + strip render throughout.

### The safety model (protect above all)
- **Tiers from ground truth, not the model's words** (resolved UIA name, vision's pixel label, the literal shell command, the resolved click target on a real web page).
- **Fail closed everywhere.** Unknown combo → CONFIRM. Vision uncertain → CONFIRM. Gate error/timeout → cancel. Denylisted shell → BLOCKED. Encryption unavailable → refuse to store. Real-browser committal action with acting not allowed → BLOCKED.
- **CONFIRM shows ground truth, never a model paraphrase/summary.** Denylist is a **backstop, not a boundary** (documented + tested). Real-browser committal confirms name the actual site.
- **High-risk capabilities are opt-in, layered, and default-OFF**: real-browser mode (`web.profile_mode`) and, beneath it, acting on real accounts (`web.allow_actions`) are two separate switches — the deliberate pattern for any future risk expansion.
- Residual risks are **documented and pinned by tests**, not hidden (§5).

---

## 4. How to run & test

```powershell
cd e:\J.A.R.V.I.S
python run.py                 # serve HUD + open browser (http://127.0.0.1:8000)
python run.py --no-open       # serve only; open the URL yourself. /settings is the settings page.

python -m pytest tests/ -q    # full suite: 705 passed, 0 failed, 0 skipped (~4-8 min; launches/kills
                              # Notepad + a throwaway Chrome, may launch JARVIS's dedicated real-browser
                              # Chrome if real mode is on; needs a real desktop; live tests need the key)
python -m pytest tests/test_memory.py tests/test_shell.py -q   # inner loop: touched files only
```
- **Capture the real exit code** (piping to `tail` masks pytest's): `python -m pytest tests/ -q > run.log 2>&1; echo "EXIT=$?"; tail -3 run.log`.
- **Port 8000 stuck** (a stopped background server orphans it): `Get-NetTCPConnection -LocalPort 8000 -State Listen | ForEach-Object { Stop-Process -Id $_.OwningProcess -Force }`.
- **Visual check**: `python run.py --no-open` + Playwright via `window.__hudEvent(event)` / `window.__hudSetState(state, detail)` → screenshot → **`Read` the PNG and inspect** + DOM asserts (pattern in `tests/harness_hud_visual.py`; the settings-page variant is `tests/harness_settings_visual.py`).
- **Announce full-suite runs and get an idle desktop (~6-8 min)** — live-UIA tests steal focus (the user may be gaming); the `conftest.py` fullscreen guard refuses to start a desktop-driving run while a fullscreen app is up, but announce anyway.
- **Never run two full live suites back-to-back** — free-tier Gemini quota (daily + per-minute) will 429 and rotate failures across live-model tests. Burst-probe (5 rapid calls) before believing quota is back; see §8.

---

## 5. Known gaps / limitations (honest, carried forward)

- **Vision (re-measured slice 16, gap closed slice 17):** confabulation on blank targets **did NOT reproduce**; localization 1.0 easy / 0.88 hard; destructive vocab is no longer English-only (i18n + CJK). Adjacent-icon mis-localization closed by slice-17 pre-click verification (wrong-click 0.042 → 0.000 at a 0.023 false-refusal cost, ~2× latency on the vision path). **Residual:** ~2% of legitimate icon clicks are refused (fails closed, retryable). Re-run `tests/harness_vision_eval.py` + `tests/harness_click_verify_eval.py` rather than trusting these numbers.
- **Destructive vocabulary is curated, not exhaustive** — an unlisted language/verb still classifies AUTO on the fast path.
- **run_shell denylist is a BACKSTOP, not a boundary** — trivially defeated by obfuscation (tested). CONFIRM is the primary control. cmd.exe only.
- **Memory (re-measured slice 19):** semantic + lexical-guarded retrieval, paraphrase recall 0.818 on the frozen golden set, but ~18% of paraphrases still miss (below the 0.35 cosine threshold); MiniLM is English-centric; needs the one-time model download (`python -m jarvis.core.embedder --setup`) else honest lexical fallback; a pinned memory is in EVERY prompt by design.
- **Brightness** genuinely unsupported on this monitor (hardware, not code).
- **DND (slice 12)**: uses the public UI surface, not a silent API — opens a Settings window (~2–4 s, focus-steal). Matches the toggle by automation_id/name; a Windows update renaming both → honest failure until the matcher is updated.
- **Email**: "accepted by server" is the strongest verifiable claim. Google test-mode OAuth refresh tokens expire after **7 days** unless the OAuth app is published to production. Send-only, one recipient, one caged attachment.
- **Web automation (isolated mode, slice 14)**: untrusted-content boundary is a mitigation, not a guarantee — the real backstop is CONFIRM on committal actions. Starts logged out (deliberate).
- **Web search (slice 15)**: keyless `ddgs` is unofficial — can throttle/return empty; single attempt, no retry spiral, no SLA.
- **Wake word (slice 13)**: opt-in, off by default. openWakeWord's false-positive rate is higher than a commercial engine's (the follow-up guard is the backstop); always-on cost ~0.2% total CPU.
- **Audit log (slice 18)**: process death mid-primitive leaves that action unrecorded (no write-ahead record); a write failure is loud but does NOT block the action; payload confidentiality is DPAPI's (user-account-bound).
- **Dry-run (slice 18)**: only a LEADING `dry run:` prefix is mechanically guaranteed. Perception-dependent verbs narrate a conditional tier, not the real one.
- **PC-control latency (slices 20-21)**: window resolution is fixed (34.4s→~5.3s typical chain). Remaining knobs: fixed post-action settles (0.3s after click/press — safety guards, not re-measured for trimming), and the vision path's two model calls (~13s, slice-17-justified).
- **App discovery (slice 22)**: covers desktop shortcuts + Steam + Epic only — no other launchers (GOG, Battle.net, etc.) unless they leave a desktop shortcut. Ambiguous names (two genuinely different apps sharing a search term) return candidates rather than guessing.
- **Settings page (slice 23)**: multi-brain (OpenAI/Claude/Ollama) is visibly present but disabled — "not ported yet" is honest UI, not a bug. Autostart targets `tray_start.pyw`; if the repo is moved, re-toggle autostart to refresh the Run-key path.
- **Real-browser mode (slices 24-25)**: cannot use the user's literal Default Chrome profile — Chrome 136+ blocks remote-debugging on it and app-bound cookie encryption resists copying its logins (this is Google's deliberate hardening, not a JARVIS limitation). JARVIS instead drives a **dedicated** Chrome profile (`data/browser_profile/`) that the user signs into once per site. Committal actions (click/type/submit) are OFF by default and, even when enabled, only committal ones (post/buy/send/delete/submit) CONFIRM — benign clicks/typing are un-gated on the real account (the user's chosen trade for smoothness). A click that leaves the current host via an **anchor** is now re-gated through the cross-origin CONFIRM (slice 27); the remaining residual is a *named, benign-looking* control that navigates cross-host via **JavaScript** (no inspectable href) — that is detected and flagged after the click, not pre-gated. Rich text editors (contenteditable) work best-effort, verified on Claude's ProseMirror box but not exhaustively tested across every site. `browse_key` presses only a fixed allow-list of navigation keys (Enter/Tab/Escape/arrows/etc.) — never arbitrary key combos.
- **Flaky test note**: (1) live-UIA/input tests (`test_input`, `test_tabs`) intermittently fail under load in a full run on real mouse/UIA/browser timing; (2) live-model tests accept any bounded/terminal chain state to absorb transient provider errors; (3) a recurring cross-session Win11-Notepad session-restore orphan can make `test_close_window_closes_notepad` fail (a genuinely unsaved leftover Notepad from a prior session — not a code bug, `close_window` correctly refuses to force it past its save dialog); (4) **never run two full live suites back-to-back** — free-tier Gemini quota exhausts and live tests fail in clusters (rotating failures across runs = the signature of throttling, not a regression). Always re-run the named test in isolation before calling anything a regression.
- **Undo (slice 26):** the stack is in-memory and process-scoped — a restart
  forgets what was undoable (same posture as the chain tracker, documented not
  hidden); depth 5, deletion-quarantine retention 20 (bounded windows,
  disclosed); pop-on-attempt (a failed undo is reported, not retried); undoing
  a DND change re-opens Settings briefly (the original action's same cost);
  tabs/media-keys/email/shell are categorically irreversible and test-pinned
  as never-undoable. Redo does not exist (undoing an undo is out of scope).
- **Recurring test-isolation lesson (slices 24-25):** `data/settings.json` can have `web.profile_mode="real"` persisted from live testing/actual use. Any test that classifies web clicks or drives the browser MUST pin `web.profile_mode="isolated"` + `web.allow_actions=False` in an autouse fixture, or it will see BLOCKED instead of the tier it expects. `test_web.py`, `test_web_live.py`, `test_search.py`, `test_search_live.py` all do this — follow the same pattern for any new web-adjacent test file.

---

## 6. How we operate (now the DEFAULT, via CLAUDE.md)

The four-stage discipline runs automatically every session — **you do not need to be told to "Fable it".** `CLAUDE.md` is the directive; `HARNESS.md` is the technique. In short:

- **Plan before code.** Plan mode → a plan with a literal Definition of Done, staged entry/exit criteria, a file map, a task-specific risk register, and **tests named before code**. No implementation before the user approves. **User-added approval conditions are binding** (turn each into a test — e.g. "forget must never guess").
- **Build tests-first per stage.** Write the named tests, watch them fail for the right reason, implement to green. Name deviations; amend the file map. (Red-check trick: `git stash` the impl to prove a trust-critical test fails without it.)
- **Self-test:** `python -m pytest tests/ -q` → 0 failed, 0 skipped. Paste real output. Re-run a failing live test in isolation and find the cause before calling it flaky.
- **Verify:** for visual goals, screenshot and **actually look**; for live behavior, verify by an independent signal (a window title, a readback, a process check), not the model's claim. Restore any state you changed.
- **Report honestly** (Objective/Status/Tests/Hostile/Deviations/**Known gaps**/Next) and **commit per stage** with the trailer `Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>`.
- **Probe before planning anything risky.** When a new slice depends on an unverified mechanism (a browser automation approach, a Windows API, a launcher's URI scheme), write a throwaway probe script FIRST and run it before writing the plan or any product code. Slices 12, 20, 21, and 24 all pivoted mid-plan because a Stage-0 probe found the approved mechanism didn't actually work — that's the system working, not a failure.
- **API-first for external services** (`CLAUDE.md` §1): before scoping GUI automation of a named service, check for an official API. State the finding even when the answer is "no API — GUI is the only path."

**Environment:** Windows 11, PowerShell (Bash tool also available), Python 3.12 (global installs, no venv). The machine may have a fullscreen app up (focus-steal) — prefer `window_hint` targeting. Long suites/servers: run in the **background**, don't poll. Salvage proven hardware-facing code from `legacy/`, don't rewrite it.

**Persistent memory:** a project memory file lives at `~/.claude/projects/e--J-A-R-V-I-S/memory/jarvis-rebuild-slice1.md` (keep it updated). Convention: **every finished slice ends with a "How to test this slice" section, unprompted** (`slice-test-guide-convention.md`).

---

## 7. Suggested next slices (not yet built)

All four spec §1.6 scripts pass; real-browser navigate+read AND committal actions both shipped (slices 24-25). Pick one and plan it. In rough priority:

1. **Multi-brain (OpenAI / Claude / Ollama)** — slice 23 salvaged the settings page and left these visibly-disabled ("not ported yet"). Each needs a tool-calling adapter mapping the 26-primitive schema + chain loop off Gemini specifics, per-provider live tests, and **re-verification of the tiering/CONFIRM safety behavior per brain** — realistically 2–3 slices, not one.
2. **A resilient/paid brain** — the free-tier `gemini-3.1-flash-lite` daily+RPM quota is the single biggest drag on the test gate (rate-limited runs at nearly every recent checkpoint). Either enable billing on the key (zero code) or build a brain fallback chain (flash-lite → flash, the TTS-chain pattern) so a 429 doesn't stall the agent.
3. **Real-browser mode, round 3** — cross-host click re-gating shipped in slice 27. Remaining round-2 residuals: broader rich-editor `fill()` coverage beyond Claude's ProseMirror box (Slate/Draft/Lexical/CodeMirror); a HUD indicator for when real-browser mode / allow_actions is live (user deprioritized 2026-07-19 — "if it's just an indicator there isn't really a need right now"); and the narrowed slice-27 JS-navigation residual (a named-benign button that navigates cross-host via JS is flagged post-click, not pre-gated — request-interception was considered and rejected as deadlock-risky).
4. **Memory refinements, round 3** — a HUD memory-manager panel, per-memory sensitivity tags, driving the residual ~18% paraphrase misses down (re-run `harness_memory_eval.py` first).
5. **Vision: drive the false-refusal rate toward zero** (slice 17 left it at ~2.3%) — tune `vision.verify_pad_px`, ask for a canonical action word, or a second opinion before refusing.
6. **Real-filesystem round 2 (the natural follow-up to slice 32):** write/create a file anywhere, plus **move / rename / copy** — all building on the same `fsaccess` path-safety core (`resolve_user_path` + `classify_path_risk` + the CONFIRM-verbatim-path boundary). Slice 32 deliberately scoped to browse+delete+shortcut; these are the obvious next verbs. Also: **read a file's CONTENT anywhere** (broaden `read_file` beyond the workspace via the same classifier). Other candidates: **PowerShell as a second `run_shell` backend** (cmd.exe only); quality/robustness (memory retrieval round 3, vision false-refusal reduction); new surfaces (multi-brain, inbox reading). (Undo shipped slice 26; audit viewer 28; scroll+double/right-click 29; caged file authoring 30; clipboard 31; real-FS access 32 — `drag`/`move_mouse`/`wifi`/horizontal-scroll remain documented *deliberate* deferrals.)
7. **Spotify via Web API — PROBED AND DEAD-ENDED (2026-07-18):** the Feb 2026 policy change requires the app owner to hold Premium for ALL Development-Mode endpoints (search and own-playlist reads included, not just playback), and this account is free; Extended Quota Mode needs a registered business + 250k MAU. Do NOT re-plan this without a Premium subscription appearing first. Script #1's GUI path remains the correct, proven mechanism.
8. **Email widenings** (each a deliberate slice): multiple recipients/CC, attachments beyond the cage, inbox reading (a much larger privacy surface).
9. **Web/search widenings** — the vision fallback applied in-page for canvas/JS UIs with no accessible names; browser screenshots into the HUD; multi-tab; a fallback search backend if ddgs throttling annoys.
10. **Wake-word refinements** — a HUD wake toggle; custom "hey jarvis" sensitivity; self-trigger suppression during TTS beyond the `_busy` drop.
11. **DND without the Settings pop** — revisit the CloudStore serialized blob for a silent path.
12. **Desktop-native automation hardening** — beyond the browser: more robust arbitrary native-app automation (the plan for slice 25 explicitly scoped this out in favor of browser web-apps; `input.py`'s click/type/press already work on any window, but haven't had the same measurement/hardening pass as the browser primitives).

Also deferred: ElevenLabs/local-Whisper providers are now ported (slice 23); OpenAI/Claude/Ollama brain providers still sit in `legacy/` until item 1 above.

---

## 8. First moves in the new session

1. Read `JARVIS_Spec_v1.md`, this file, and `CLAUDE.md` (the discipline is already in force).
2. `git log --oneline -30` for the slice history; `python -m pytest tests/ -q` to confirm **606** (deterministic core always green). Keep the desktop idle during the run (live-UIA input tests) — and if real-browser mode is on in `data/settings.json`, expect JARVIS's dedicated Chrome to open/close during the run too. **Live-MODEL tests need a healthy daily Gemini bucket AND pace under the per-minute cap** — on a heavily-used day they rate-limit and rotate failures (see `REGRESSION_CHECKPOINT.md` §1); re-run any live failure in isolation before treating it as a regression, don't run full live suites back-to-back, and capture a clean 0-failed pass on a fresh daily bucket. A paid-tier key removes this entirely.
3. Skim `REGRESSION_CHECKPOINT.md` for the 4 acceptance scripts' live status (all passing) and the most recent gate run's honest failure breakdown.
4. Ask the user which slice is next (or they'll tell you), then plan it in plan mode. If the new slice depends on an unverified mechanism, probe it first (see §6).
