# JARVIS Rebuild — Session Handoff

> Paste this into a new Claude Code session to continue the build with full context.
> Last updated: 2026-07-25, after **Slice 38 (close the CONFIRM payload gap — commit steps now show WHAT, not just WHERE)**. See `git log --oneline` for the full slice history. (The header had been stale at "Slice 33" through slices 34–37 and the v1.0.1–v1.0.4 run; corrected here.)

---

## 0. TL;DR for the next session

You are continuing a **from-scratch rebuild of JARVIS** — a voice-driven agent that controls a Windows 11 PC. The single source of truth for **what to build** is **`JARVIS_Spec_v1.md`** (read it first). **How to build** is codified in **`CLAUDE.md`** (auto-loaded — the plan→build→self-test→vision-check discipline runs by default, no need to type `/fable-mode`) and **`HARNESS.md`** (the concrete techniques with real examples).

**Capability set, built slice by slice (1–33), grouped by area:**
- **Core loop & HUD:** voice loop (push-to-talk + wake word), a reactive HUD (orb states, transcript, Action Log, telemetry, chain plan strip), a fail-closed **CONFIRM** gate + hard **BLOCKED** tier, real **multi-step agentic chains** (visible plan, replan, retry guards).
- **PC control:** launch/close/read-screen/click/type/press/**scroll** primitives (click supports `kind=single|double|right` — slice 29), a **vision fallback** for icon-only controls (measured accuracy + pre-click point verification so the control you approve is the control that gets clicked), **app discovery** (desktop shortcuts + Steam + Epic library URIs — not just registry App Paths), **smooth cursor motion**, and a **win32 latency fix** that cut a typical multi-step chain from 34.4 s to ~5.3 s.
- **Wider verbs:** browser tab list/close, **caged file authoring** (write/read/search/delete in `data/agent_files/` — write is AUTO-create/CONFIRM-overwrite and undoable; slice 30), **real-filesystem access** (slices 32-33: `list_directory`/`delete_path`/`create_shortcut` + `write_path`/`read_path`/`move_path`/`rename_path`/`copy_path` ANYWHERE on the PC — deletes AND overwrites go to the Recycle Bin, every mutation CONFIRM-gated on the verbatim path, catastrophic paths BLOCKED, `fs.enabled` kill-switch), **clipboard** (get/set — AUTO, content redacted from the audit log; slice 31), volume/media/brightness, **`run_shell`** (denylist + verbatim-confirm + tree-kill), **`send_email`** (Gmail API, verbatim-confirm, caged attachments), **`set_dnd`/`get_dnd`** (real Settings toggle + readback).
- **Memory:** DPAPI-encrypted long-term memory with **semantic (local embedding) retrieval + pinned always-on preferences**, explicit-intent writes only, forget-never-guesses.
- **Web:** an **isolated** sandbox browser (navigate/read/fill/click, untrusted-content boundary, cross-origin + committal-click gating) plus keyless **`web_search`** — AND, as of slices 24–25, an opt-in **real-browser mode**: JARVIS can drive a dedicated real Chrome logged into the user's own accounts, first navigate+read only, now (behind a second opt-in) able to **click/type/submit** on the user's real sites with committal actions CONFIRM-gated. As of slice 27, a **click that would leave the current host** is re-gated through the same cross-origin CONFIRM as a navigate (anchor destinations resolved pre-click; JS-driven jumps flagged post-click) — closing the slice-25 residual.
- **Trust & operability:** a **persistent audit log** (every action, including declined/BLOCKED, as DPAPI-encrypted JSONL) with a **read-only HUD viewer** (slice 28: `/audit` — an envelope-first records browser; verbatim args stay encrypted until you reveal a specific record) + a mechanical **dry-run mode** + **undo** (slice 26: `undo_last_action` walks back the newest reversible action — volume/mute/brightness/DND, a just-stored memory, a just-deleted workspace file, which now quarantines instead of unlinking; irreversible verbs are test-pinned as never-undoable); a **settings page** salvaged from the legacy app (`/settings`) covering brain/TTS/STT/wake/autostart/capability kill-switches + the new real-browser toggles, with ElevenLabs TTS and local-Whisper STT ported as working backends.
- **Ops discipline:** a fullscreen desktop guard (the full suite refuses to start over a game), a measured PC-control latency harness, and hard-won test-isolation lessons (see §5 and the "known gaps" entries below) baked into `CLAUDE.md`/`HARNESS.md`.

> ## 🚀 SHIPPED — this is now a PUBLIC product, not just a local build
>
> **https://github.com/malekthegamer/JARVIS** — public, MIT, latest release
> **v1.0.4**. Real users (the owner's friends) install from it. That changes the
> job: a bug now reaches other people's machines, and **the README/release notes
> are user-facing promises that must stay true.**
>
> Install path for a newcomer: download → double-click **`install.bat`** →
> double-click the Desktop shortcut → paste a Gemini key into the HUD's
> first-run panel. `legacy/` is NOT published (gitignored); the build docs are.
>
> **Post-release, FIVE bugs were found by the owner actually running it** — all
> one class: *verified in the dev environment, broken in the user's.* See the
> "v1.0.1–v1.0.4" section below. Read that before shipping anything else.

**Tests: 994 collected** (slice 49). The **non-desktop gate** (desktop-driving
tests deselected, so it runs without an idle machine) is now **712 passed / 0
failed / 0 skipped** (slice 47) — clean since slice 45, because
slice 45 paces live Gemini calls so quota stops forging failures (it costs ~175s
of deliberate sleeping; the price is printed at the end of every run). **The full
suite including desktop tests has NOT been re-run since pacing landed** — that
needs an idle desktop and now takes longer. Before slice 45 the best full run was
**748 passed / 6 failed / 0 skipped, all 6 live-brain/live-UIA and each re-verified
green individually**
 (a clean single 756/0/0 is still blocked by the free-tier PER-MINUTE cap — a fresh daily bucket is NOT sufficient; see §7 item 1) (deterministic core is 100% reliable; live-model tests need a healthy Gemini quota — see §4 and §8). All this is proven live, not just unit-tested — every slice ends in a real end-to-end acceptance run, several with mechanical (not model-claimed) verification.

- **Two durable measurement harnesses exist for vision** (numbers you can re-run, not vibes): `tests/harness_vision_eval.py` (localization / confabulation / unsafe-AUTO) and `tests/harness_click_verify_eval.py` (catch / **false-refusal** / wrong-click). Plus `tests/harness_memory_eval.py` (retrieval recall) and `tests/harness_latency_eval.py` (per-seam wall-clock).
- **Live app right now:** `python run.py` serves the HUD at `http://127.0.0.1:8000` (push-to-talk); `/settings` is the settings page (gear icon in the HUD header). **`python -m jarvis.tray`** runs server + tray icon (Open HUD / toggle wake word / Quit). Brain = Gemini `gemini-3.1-flash-lite`. Configured secrets: `GEMINI_API_KEY`, `TEST_SELF_EMAIL`, Gmail OAuth artifacts under `data/email/`. Wake word needs no key (openWakeWord is local).
- **All 4 spec acceptance scripts (§1.6) pass:** #1 Spotify→Discover Weekly ✅, #2 close tabs except YouTube ✅, #3 find invoice→email Sam ✅, #4 brightness+DND ✅ (brightness honestly unsupported on this monitor — hardware, not code). Status tracked in `REGRESSION_CHECKPOINT.md`.
- **Manually live-verified end to end (2026-07-20):** the user ran a single hands-on session across slices 29-33 (real-FS write/read/move/rename/copy/delete/browse/shortcut, clipboard, scroll, click kinds) through the actual HUD — all passed. Only rough edge found: **`click kind='double'` is flaky in real use** (live-UIA mouse timing, not a logic bug); user called it low-priority. See §2's acceptance note and §5/§7.
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
- **Live-proven end to end (real brain):** created a Desktop shortcut to a temp folder (`.lnk` + TargetPath verified), deleted a temp file → gone (Recycle Bin), and **"delete System32" → refused, System32 intact**. Scope: browse+delete+shortcut; the authoring verbs shipped in slice 33.

### Slice 33 — Real-filesystem round 2: write / read / move / rename / copy anywhere
- **Completes the real-FS surface** — the authoring verbs on the *same proven core* (all in `fsaccess.py`): `write_path` (create/overwrite a text file), `read_path` (read content anywhere, AUTO + untrusted-wrapped), `move_path`, `rename_path` (new name, not a path), `copy_path`. Lower-risk than slice 32 — no new safety model, no new mechanism.
- **Same layered safety, reused verbatim:** every mutation reuses `classify_path_risk` on the RESOLVED path (traversal/symlink-safe, already test-pinned) → BLOCKED catastrophic / CONFIRM with the verbatim path(s) in the mono box. move/rename block a protected **source too** (moving a system file out ≈ deleting it); copy gates only the **destination** (source stays).
- **New principle, applied consistently: overwrite/clobber recycles the prior version first** (`_place()` helper → `_recycle` the existing file, then move/copy/write) — so, like deletes, an overwrite is recoverable from the Recycle Bin, never silently lost. An existing-folder destination is refused (no silent merge).
- Reuses `shutil` (move/copy) + the slice-32 `_recycle`; `read_path` reuses `web._wrap_untrusted`. All five under the same `fs.enabled` kill-switch; `fs.max_write_kb` (256) caps writes. No JARVIS undo (the Recycle Bin is the recovery, delete parity).
- **Live-proven (real brain):** wrote a note → read it back (content matches on disk) → renamed it → copied it (source preserved), all verified from disk.

### Slice 49 — Barge-in: cut JARVIS off mid-sentence
- **The feature:** say "hey jarvis" while it is talking or acting, or click the
  amber **■ STOP** pill in the HUD, and it stops — speaking AND running further
  steps. `IDEAS.md` #4's own framing: the change that most makes an assistant
  feel alive rather than a script playing back.
- **LIVE-PROVEN:** a 14.4s utterance, interrupted 1.0s in, **stopped at 1.0s**.
  Audio still worked afterwards (the mixer is not wedged by an abrupt stop).
  `tests/harness_barge_in.py`.
- **Research found TWO barriers, not one:**
  1. `playback.stop()` already existed — and **nothing called it.** Its docstring
     claimed *"used by the HUD 'stop speaking'"*, describing a feature that did
     not exist. Fixed; it now names its one real caller.
  2. `_on_wake` **returns early whenever `_busy` is held**, so while JARVIS spoke
     the wake word could not fire at all. Voice barge-in was actively blocked,
     not merely unwired.
- **Stage 0 measured the headline risk before any voice code existed:** does
  JARVIS's own TTS trip its own wake word? **381 frames / 30.7s of real speech,
  0 trips, peak 0.196 against a 0.50 threshold** — a 2.5x margin, worst case
  being "JARVIS is ready. How can I help you today?". That measurement is what
  permitted the voice trigger to ship. Today's `_busy` drop was *accidentally*
  protecting it from hearing itself; barge-in removes that protection.
- **Reuses the proven abort path:** `interrupt.request()` sets
  `tracker.aborted="interrupted"` and `pre_call_guard` already refuses every
  later step. No new machinery — but `chain.py` now maps the reason properly, so
  an interrupted chain says **"the user interrupted you"**, not "declined a
  confirmation" or "too many failures". Reporting the wrong reason would have the
  model tell the user something untrue about their own request.
- **Never touches `_busy`** — a stop that waited on the lock held by the
  interaction it is cancelling would deadlock until that interaction finished on
  its own, i.e. never do its job. Test-pinned
  (`test_stop_does_not_acquire_the_busy_lock`).
- **Never stacks:** barge-in stops and RETURNS; it does not capture a follow-up,
  which would re-enter `_busy`. Test-pinned. CONFIRMING is excluded — that modal
  owns its own Approve/Cancel.
- **§4 vision-checked** (this slice had a visual goal): screenshots inspected —
  hidden at idle, amber pill reading "■ STOP" under the state label while
  SPEAKING/EXECUTING. It says STOP, never "undo".
- **Honest limit, stated everywhere:** a step already in flight cannot be
  un-fired. Stop prevents the NEXT step. A click that has fired has fired.

### Slice 48 — Routines: "work mode" runs a saved chain
- **The unit of autonomy.** `save_routine` / `run_routine` / `list_routines` /
  `delete_routine`. Saying a bare routine name runs it. Persisted
  **DPAPI-encrypted** (`data/routines.bin`) — steps carry app names, URLs and
  file paths, so the write path REFUSES plaintext, mirroring `MemoryStore`.
- **A routine is stored STEPS, never stored AUTHORITY.** `run_routine` replays
  each step through `primitives.execute()`, so every one re-hits the kill
  switch, the tier decision and the CONFIRM gate as if the model had just asked.
  **Re-confirmation is the FREE default** — skipping it would take deliberate
  extra work. That mechanically answers the open design question IDEAS.md #1
  posed. Live-proven: a `run_shell` step inside a routine still prompted.
- **Stage 0 measured the model's half BEFORE any code** — and the CONTROL row
  changed the plan:

  | probe | result |
  |---|---|
  | compose from a plain request | **4/4** — `steps=['launch_app','set_mute']`, real tools |
  | bare "work mode" | **4/4** → `run_routine('work mode')` |
  | "Work Mode" / "work-mode" | **4/4** — the model self-normalises to the stored name |
  | **CONTROL: no routine names in the prompt** | **0/4** — it called `list_routines` instead |

  So **Stage 3 (names in the prompt) is load-bearing, not a nicety** — it was
  planned as discoverability polish and the control row proved the feature does
  not work without it. Failure mode without it is at least benign
  (`list_routines`, not a wrong action). The store's normalized matching is a
  backstop for hand-edited files, since the model does the matching itself.
- **⚠ A REAL safety bug my own test caught.** `_run_run_routine` first detected
  a stopped step with `result.split(":")[0]`. The gate returns
  `"CANCELLED (declined): …"`, so the parenthetical made the comparison miss and
  **declining step 2 let step 3 run** — precisely the property this slice
  claims. Now a `startswith` prefix check, pinned by
  `test_declining_a_step_aborts_the_rest_of_the_routine`.
- **Visible, not opaque:** each step registers with `ChainTracker` so the HUD
  Action Log shows real progress. `brain.py:389` only wraps the OUTER tool call,
  so without this a whole routine collapsed into one row.
- **Honest failure:** stops at the first FAILED/CANCELLED/BLOCKED step and names
  which step of how many, plus what completed before it. A half-run routine
  reported as success would be the worst outcome here.
- **Bounded and non-recursive:** `run_routine` inside a routine is rejected at
  save AND re-validated at run (the file can be hand-edited); caps on steps (40),
  routines (100) and name length (80).
- **Known trade, stated in the README:** a routine with 3 CONFIRM steps prompts
  3× every run. Deliberate — approving a routine's *shape* once is not approving
  today's execution.
- Kill switch `routines.enabled`, both directions.
- **Stale-file note:** `data/reminders.json` exists, contains `[]`, and nothing
  in `jarvis/` references it — orphaned legacy debris. Flagged, not built on.

### Slice 47 — screen-aware Q&A ("what am I looking at?")
- **Closes `screen.py`'s own TODO.** Its docstring said *"capture_screen() is
  internal-only this slice — screenshots feed screenshot_diff, not the model
  (vision round-trip is a later slice)."* This is that slice: JARVIS could see
  that the screen *changed*, never what was *on* it.
- **`screen_query(question, window_hint=None)`** — AUTO tier (it returns prose;
  nothing is clicked, typed or run from its output, same precedent as
  `read_page`/`read_ui_tree`). Answer wrapped by `web._wrap_untrusted` before
  re-entering the agent loop, reusing the one boundary rather than a second copy.
- **The design flaw this avoided.** The obvious default — capture the *focused*
  window — is wrong: typing "what am I looking at?" into the HUD makes the
  BROWSER the focused window, so it would answer *"you're looking at the JARVIS
  interface."* Verified nothing excludes the HUD from `_foreground_window()`.
  **Owner chose whole-screen by default**; `window_hint` narrows. Bonus:
  `capture_screen()` does not `set_focus()`, so asking never rearranges the
  desktop (unlike the click path's `_grab_window`).
- **Stage 0 MEASURED before any code** (`scratchpad/probe_screen_qa.py`), 4
  planted facts checked by exact string on a synthetic 1920×1080 desktop:

  | max_edge | KB | sec | heading 30px | body 15px | small 12px | dialog |
  |---|---|---|---|---|---|---|
  | **1024** | 34 | 1.6 | ✅ | ✅ | ✅ | ✅ |
  | 1536 | 55 | 1.3 | ✅ | ✅ | ✅ | ✅ |
  | 1920 | 34 | 1.4 | ✅ | ✅ | ✅ | ✅ |

  **1024 reads 12px small print** (~6px after the 0.53 downscale), so it is the
  default — cheapest and measured sufficient. ~1.4s vs the click path's ~7.1s
  (no `response_schema`).
- **`vision.qa_max_edge_px` is SEPARATE from `vision.max_edge_px` on purpose** —
  the latter is load-bearing for slices 16/17's published click accuracy, so
  tuning Q&A must never drag the click path with it. Test-pinned
  (`test_qa_uses_its_own_max_edge_not_the_click_paths`).
- **Live-proven on the REAL screen** (`tests/harness_screen_qa.py`): read the
  foreground app, the browser, an installed extension, and quoted a small
  in-app error message verbatim — confirming the synthetic measurement holds on
  real antialiased content. Gate tests use a synthetic image + a REAL model call
  (the `_mock_grab_image` pattern), so they're deterministic and
  `test_vision.py` stays out of `_DESKTOP_DRIVING_MODULES`.
- **Confabulation guard test-pinned on this path**: asked for something not on
  screen ("the user's bank balance"), it must admit it rather than invent.
- **Kill switch both ways** — `vision.enabled` withholds it from `tools_schema()`
  AND `_disabled_by_switch()` refuses a direct call (the slice-35 lesson).
- **⚠ Privacy, stated not buried:** this sends the WHOLE screen — every visible
  window, notification and message — to Gemini. Genuinely bigger than the click
  path's single window. It rides the existing `vision.enabled` switch rather
  than a new flag nobody knows about; README says so plainly. Not solvable by
  cleverness — it is the honest cost of the feature.
- **Inherited gap, unchanged:** this is a THIRD Gemini call site and the vision
  path still has **no** brain fallback chain (slice 44 covers `brain.think()`
  only), so a 429 here is a hard failure. Slice 45's pacer *does* cover it
  automatically (it wraps the shared SDK method).

### Slice 46 — test the way a USER actually starts JARVIS
- **The gap it closed:** all five post-release bugs were one class — *green in my
  dev environment, broken on the user's machine* — and the entry point had **34
  tests that never ran it** (`test_installer.py` reads `install.bat` as text,
  `test_tray.py` is pure logic behind `monkeypatch`, `test_smoke.py` is
  import-level). Every one of them passes on a machine where the real launch is
  dead.
- **What it does now:** `tests/test_entrypoint_smoke.py` launches the *actual*
  user path — `.venv\Scripts\pythonw.exe tray_start.pyw` — as a subprocess and
  proves the HUD serves: `GET /` → 200 with a HUD body, `/api/state` → valid
  JSON, no startup-error artifact, no orphan process. **Under `pythonw`
  specifically**, because "pythonw has no stdout" is what made the v1.0.1 crash
  silent; the same code under `python.exe` cannot catch it.
- **Stage 0 measured before any test existed:** cold start **11.6s** to first
  HTTP 200, killing the process tree left **zero** orphan pythonw, and no dialog
  on a clean boot ⇒ the full-fidelity tray path is safe to automate (the plan's
  fallback to `run.py --no-open` was not needed).
- **PROVEN ABLE TO FAIL** (a smoke test that can't go red is decoration): with
  `tray_start.pyw`'s import deliberately broken, the boot test went red in
  **6.11s** with *"the launcher EXITED with code 1 … under pythonw a crash is
  silent — this is the v1.0.1 class"*, then green again once restored. The early
  exit-code check is what turns an opaque 90s timeout into a 6s diagnosis.
- **Venv fidelity, not dev fidelity:** asserts the venv is Python **3.12** (3.13
  removed `audioop`, PEP 594, which killed voice for a real user) and that
  `speech_recognition` / `pystray` / `win32com.client` / `openwakeword` /
  `uvicorn` import **in that interpreter**, plus that the openwakeword
  `hey_jarvis*.onnx` model file is really on disk (v1.0.3: the package ships
  without models).
- **Two of my own test-design bugs, caught by the tests themselves:**
  `test_the_launch_left_no_orphan_process` was **passing vacuously** — it read
  `booted.orphans` before the fixture's teardown populated it, so it asserted
  against an empty set — and the live module fixture held port 8000 against the
  mid-startup test. Both came from killing in teardown; the fixture now runs the
  **entire lifecycle in setup** and hands the tests a recording.
- **⚠ Ordering dependency, now explicit rather than accidental:**
  `test_extension_browser.py:77` starts uvicorn in a **daemon thread it never
  shuts down**, so it owns port 8000 for the rest of the pytest process.
  Alphabetical collection puts `test_entrypoint_smoke.py` first, which is the
  only reason the full suite works. Running them in the other order fails — and
  the port guard now **names the holder** (`psutil`) and says which of the two
  causes it is: the owner's JARVIS running, or a leaked test server. Fixing the
  leak properly is a separate slice (`uvicorn.run()` in a thread cannot be
  cleanly stopped).

### Slice 45 — quota stops forging test failures (the first 0-failure gate)
- **The problem, in one number:** for seven slices every gate carried 6-9 failures
  that were never bugs. Slice 44 attacked it from `brain.py` and the measurement
  said no (6 → 7). Slice 44's own conclusion — *this is test pacing, not brain
  resilience* — is what this slice acted on, and it worked.
- **Result, same suite, nothing loosened or skipped:**

  | | before | after |
  |---|---|---|
  | failures | **6** | **0** |
  | 429s in the run | 9 fallback calls | **0** |
  | wall-clock | 1:56 | 5:10 |
  | slept on purpose | 0s | **176.6s** |

  `677 passed, 0 failed, 0 skipped` — the first clean non-desktop gate in the
  project's history. **The cost is real and is the point:** the gate is ~2.7x
  slower because it sleeps to stay legal. That price is printed at the end of
  every run so it can never be hidden.
- **How:** `tests/_pacer.py` wraps the ONE method all three Gemini call sites go
  through (`google.genai.models.Models.generate_content` — brain
  `gemini_provider.py:84` plus vision `vision.py:232` and `:335`), and paces each
  model with a sliding 60s window at 12 calls/min. Per-model, because the fallback
  has a separate bucket. Deterministic tests make no API calls, so they sleep 0s.
- **Stage 0 measured the premise and could have cancelled the slice:** 32 primary
  calls in 116s = **16.5/min against a measured ~15/min cap.** Had it come in under
  12/min the failures would have been daily-bucket exhaustion, which pacing cannot
  fix, and the slice would have stopped. **Correction worth keeping:** that 41-call
  figure was an UNDERCOUNT — the leak below meant the last four live files weren't
  counted. The conclusion survives (the true rate was *higher*), but the number was
  measured with a broken instrument.
- **⚠ The bug that nearly shipped a fake win.** The first paced gate read 6 → 1
  failures. It was partly an illusion: `tests/test_quota_pacer.py` calls
  `uninstall()`, and it sorts *before* `search_live`/`undo_live`/`vision`/
  `web_live` — so those four ran **unpaced and uncounted** while the summary looked
  great. The tell was one inconsistent line: a `gemini-2.5-flash` fallback in the
  log that the counter said never happened. Chasing that discrepancy is the only
  reason this slice isn't a pacer that silently dies a third of the way through
  every run. Fixed with `rearm()` + a `pytest_runtest_teardown` backstop that
  re-arms and **names the culprit module**, scoped so it doesn't cry wolf on the
  pacer's own tests.
- **THIRD state-leak-between-tests bug in three slices** (43 browser settings, 44
  `brain.models.gemini`, 45 the SDK patch). That is a pattern in how these tests
  are written, not three coincidences: anything installed process-wide needs a
  restoring fixture written *at the same time as* the install.
- **Honest limits:** pacing handles per-minute limits only — the **daily** cap is
  untouched, so "never run two full live suites back-to-back" still stands. It is
  process-local (no xdist coordination; xdist isn't used). And it fixes the
  *signal*, not the product: a user on a free key still meets the real limit, which
  is what slice 44's chain is for.

### Slice 44 — brain resilience: a model-level fallback chain (DoD clause 4 NOT met)
- **Built:** a transient brain failure (`rate_limit` / `quota_exceeded` /
  `connection`) now retries the same request down a **bounded** chain
  (`brain.fallback_models`, default `gemini-3.1-flash-lite → gemini-2.5-flash`),
  each candidate at most once. A **non-transient** failure (`missing_key`,
  `bad_response`) does NOT walk — masking a config bug behind a slower answer is
  worse than failing. The answering model is attributed
  (`brain.last_model` / `last_model_was_fallback`, on telemetry).
- **Stage 0 measured the premise before any code, and it held:** the primary caps
  at **~15 RPM** (429 at burst 15-17), and `gemini-2.5-flash` **answered while the
  primary was 429** — sibling models have SEPARATE buckets, the only reason a
  model-level chain can help. Tool-calling parity confirmed against all 40 real
  declarations for both; `gemini-2.0-flash` could not be proven and is
  deliberately excluded. Model names were enumerated from the SDK, not guessed
  (`gemini-3.1-flash` does not exist).
- **Live-proven** against a real 429 (`tests/harness_brain_chain.py`):
  `gemini-2.0-flash unavailable (rate_limit) — falling back to gemini-2.5-flash`,
  `last_model='gemini-2.5-flash'`, `was_fallback=True`.
- **⚠ DoD clause 4 is NOT MET — recorded honestly.** The gate's live failures did
  **not** drop: **6 before → 7 after** (same suite, same noise band). Diagnosis,
  measured rather than assumed:
  - the chain **engaged 9 times** and **rescued 3**; the other **6 exhausted both
    models**, because the suite bursts past the combined ~30 RPM.
  - a 429 takes **~22s to clear** (measured: still 429 at 18s, recovered at 22s),
    so a backoff retry is **not** a legitimate product fix — a 22-second wait on a
    user-facing path is worse than an honest error. The probe said don't build it,
    so it wasn't built.
  - **Therefore: the "6–9 false failures per gate" problem is a TEST-PACING
    problem, not a brain problem.** The plan aimed clause 4 at the wrong
    subsystem; no model-level chain can fix a suite demanding more RPM than the
    account has. Next work belongs in the test harness, not `brain.py`.
- **A suspicion I had to drop:** the three `tools=[]` failures looked like the
  fallback model refusing to call tools (risk register #3, behavioural drift).
  It isn't — every one shows `falling back to` with **no** matching `answered by
  FALLBACK`, i.e. the chain was exhausted and the reply was the rate-limit
  message. Attribution (clause 3) is what made that distinguishable at all.
- **Gap this exposed:** the **vision** path (`the vision model was unavailable`)
  is a separate Gemini call site that gets **no** fallback — the chain only covers
  `brain.think()`. Not in this slice's scope; now a named gap (§5).
- **My own bug, worth remembering:** the first post-change gate showed *15*
  failures and zero rate-limit errors. Cause was my chain tests leaving
  `brain.models.gemini="m-primary"` in the store, so every later live test 404'd
  on a nonexistent model. Same class as the slice-43 leak. Now contained by an
  autouse `_restore_brain_settings` guard in `tests/test_brain.py`. **A metric
  that doesn't explain itself is not a metric** — the fallback count (0) is what
  exposed it.

### Slice 43 — JARVIS ACTS in the user's real Chrome (click / type / Enter)
- **The goal reached:** extension mode could look but not touch. It can now
  click, fill and press Enter in the owner's everyday browser, with every gate
  intact — committal CONFIRM naming the real site, cross-host gated pre-click
  via `href`, Enter carrying the field payload (slice 38), nameless elements
  failing closed, and `web.allow_actions` as a real second opt-in (default OFF).
- **Stage 0 measured the resolver BEFORE anything could click.**
  `lib.js:matchClickable` is a port of `_match_clickable`, and tiers are computed
  from the resolved element's NAME — so a mis-resolve can compute the WRONG TIER,
  not merely click the wrong thing. Scored against the proven Playwright
  resolver on the same pages: **7/7 element agreement, 7/7 TIER agreement**,
  including cross-host and the nameless fail-closed case.
  (`tests/harness_resolver_eval.py`, re-runnable.)
- **My shadow-DOM prediction was WRONG and the measurement said so:** I expected
  a naive `querySelectorAll` to find nothing on YouTube (Polymer). Measured **0
  open shadow hosts**, 79 clickables, identical to a piercing walk. Piercing was
  kept anyway (cheap, correct in principle) but the loudest risk did not exist.
- **`extension/lib.js` exists for two reasons:** Playwright cannot reach an MV3
  service worker, so logic that lives only in `background.js` is untestable —
  which is exactly how the pinned-tab bug shipped. And the resolver must be ONE
  copy, or the Python tier and the page's element silently diverge. It is a
  **classic script exposing a global**, not an ES module, because the same file
  must be importable by the worker AND injectable into pages; MV3's CSP forbids
  `new Function`, so it cannot be rebuilt from source there.
- **THREE real bugs the new tests caught:**
  1. **`classify_web_key` returned AUTO in extension mode** — Enter submitted
     forms on the user's logged-in accounts **with no confirmation**. Slice 38
     gated it behind `_real_mode_setting()`, and adding a third mode silently
     answered "no", reopening the exact hole slice 38 closed. Now
     `_on_user_accounts()` (real OR extension), so the next mode cannot repeat it.
  2. **The committal confirm did not name the site** — `_site_host()` read the
     Playwright singleton's `current_url`, so the modal said just `Click 'Delete
     account'`. Approving a destructive click without being told WHERE defeats
     informed consent. `_cross_host` had the same defect.
  3. **`activeTab()` returned nothing whenever Chrome lacked OS focus** — i.e.
     normally, since the user is typing in the HUD. Every command failed with
     "no web page to click in".
- **The flakiness, and the lesson.** The E2E suite was nondeterministic: the same
  file gave `1 failed / 1 failed / 9 failed`. Cause: tab identity was re-derived
  on EVERY command from two unstable sources (Chrome holding focus; whether
  `storage.session` survived a worker restart) — which also let classification
  and the action resolve DIFFERENT tabs, making a computed tier meaningless.
  Fixed by threading an explicit **`tab_id`** from `navigate` through every
  later command: `1 / 1 / 1`, then `20 / 20 / 20` after the split below.
  **I made three "fixes" while reacting to counts that were mostly noise** — a
  90-second stability check first would have prevented all of it.
- **Deterministic tests vs live proof, split deliberately.** Two checks depend on
  real navigation completing and were flaky as pytest; a flaky safety test is
  worse than none because it teaches you to ignore red. Their logic is now
  deterministic unit tests, and the live evidence is
  `tests/harness_extension_actions.py` — **7/7 PASS**, the same split as
  `harness_wake` / `harness_realbrowser_*`.
- **Automated what used to be manual:** `tests/test_extension_browser.py`
  launches Chrome with the extension itself (the unpacked id is derived from its
  PATH, so the repo copy gets the id the settings already allow). The owner's
  three reported tab bugs are now E2E tests instead of hand-checks. What it still
  cannot cover: their LOGINS (fresh profile).

### Slice 42 — browser control made correct and trustworthy (+ v1.0.7 fixes)
- **Three user-reported bugs, all in the extension, all now named tests:**
  1. *"it opened YouTube in my PINNED tab"* — `isUsable` checked http(s) and
     not-the-HUD and simply **never checked `pinned`**. Tab safety is now ONE
     predicate, `isProtected()` (pinned / HUD / non-http), so the next omission
     is a test failure rather than a hijacked tab.
  2. *"it opened Gmail OVER the YouTube tab it had just opened"* — a **design
     error, not an oversight**: `navigate` did `tabs.update(ACTIVE tab)`, and
     JARVIS's own new tab is active by then. **"Open" was implemented as
     "replace what's in front of me."** Now `open` = `tabs.create` in the
     current window; `tabs.update` is reachable only for JARVIS's **own tracked
     tab** and only when `reuse` is asked for.
  3. *"it typed the URL by hand and asked to confirm"* — **the model was
     reasoning correctly from wrong information.** `browse_navigate`'s
     description still said *"JARVIS's own ISOLATED browser … starts logged
     out"*, which is FALSE in extension mode. Told its browser is a logged-out
     sandbox and asked to open THEIR YouTube, driving the real window by hand is
     a reasonable inference. Descriptions are now **derived per mode from one
     source** (`_browser_blurb`) so they cannot rot away from behaviour — the
     same class as the shipped-README falsehood slice 35 reopened. **The v1.0.7
     heartbeat was a real fix but only half the cause; this was the other half.**
- **The tracked tab lives in `chrome.storage.session`** — Chrome kills the MV3
  worker constantly, so in-memory state is not state. A closed tab is EXPECTED
  (users close things): clear it and open a fresh one, never guess at another.
- **Stage 3 (speed) was DROPPED by its own measurement** — the honest outcome,
  and the point of measuring first:
  ```
  baseline: navigate median 1129 ms / p90 1647 ms / success 8/8
            read     median   10 ms / p90   27 ms / success 8/8
            example.com 69-333 ms   ·   github 854-4367 ms
  ```
  The spread **is page-load time**; JARVIS's own overhead is <100 ms and reads
  are already 10 ms. Returning sooner would mean replying *before the page is
  ready* — a prettier number bought with a false result. The owner's perceived
  slowness was the FAILURE path (dead extension → typing by hand → a confirm
  prompt), which bugs 2-3 fix.
- **Health is now visible** (vision-checked): the HUD badge rides the existing
  ~2s telemetry event and shows amber "your browser" vs dim "browser
  reconnecting…". The extension dying used to be invisible.
- **v1.0.7 (same session): the flashing console.** `nvidia-smi` runs on the
  telemetry loop every ~6s with no `CREATE_NO_WINDOW`; under `pythonw` there is
  no console to inherit, so Windows created a NEW one each time. Fixed in four
  places via one `config.NO_WINDOW`; GUI launches deliberately excluded.
  Invisible in dev because `python.exe` owns a console — the v1.0.4 class again.

### Slice 41 — JARVIS drives the user's REAL everyday Chrome (extension bridge)
- **What the owner actually wanted, finally delivered.** Slice 39's compromise
  (adopt a synced profile) was rejected; slice 40 then measured that **no CDP
  route exists** to the default profile. This ships the only remaining one: a
  Chrome extension. **Live-verified on the owner's own browser** — read their
  signed-in YouTube tab, then navigated that tab; screenshot confirms their
  bookmarks bar and profile avatar.
- **Scope: READ-ONLY** (navigate + read). Owner-chosen staging, mirroring
  slice 24 → 25: prove the channel before anything can click on a logged-in
  account. `browse_click/fill/key` are withheld **and** refused.
- **The swap is transport-only.** `ExtensionSession` implements the same method
  names as `BrowserSession`, so every classifier, `_cross_host()` check, CONFIRM
  gate and the slice-38 payload box sits above it unchanged.
- **Stage-0 probes drove the design and caught a bug that would have shipped:**
  a `setTimeout` reconnect works in every test (tests keep the socket alive) and
  then **never reconnects in real use** — when the socket closes, MV3 kills the
  idle service worker and the pending timer dies with it. Measured, not
  guessed. **`chrome.alarms` is the only timer that wakes a dead worker**, so
  reconnection is alarm-driven. **Cost, unavoidable:** MV3's minimum alarm
  period is 1 minute, so after a JARVIS restart the browser can take **up to
  ~60s** to reconnect.
- **Security shape.** The extension gets its **own** socket (`/ws/browser`) and
  is **never** added to `_clients` — that set carries `confirm_request` events
  *including their ids*, which is exactly the slice-36 auth bypass. Pinned by
  `test_extension_socket_is_never_added_to_the_hud_broadcast_set`. The allowed
  extension id is a setting, **empty by default = nobody may connect**.
- **A real drift bug found by the tests:** `tools_schema()` carried its **own
  copy** of the navigate+read-only rule instead of calling
  `web._actions_blocked()`, so extension mode refused `browse_click` at execute
  while still **advertising** it to the model. Now one predicate feeds both —
  the same duplication the slice-35 `_KILL_SWITCHES` rework removed.

### Slice 39 — JARVIS's Chrome becomes the user's DAILY browser
- **The complaint, and it was fair:** real-browser mode drove a dedicated,
  **empty** profile while the owner browsed in their everyday one — "it kinda
  kills its purpose". The problem was never the folder name; it was that the
  profile had none of their logins, bookmarks or sessions.
- **Driving the literal `Default` profile is NOT achievable — verified, and it
  is not ours to fix.** Chrome **150.0.7871.182** on this machine, with
  **App-Bound Encryption active** (`app_bound_encrypted_key` present in Local
  State). Three independent blocks: (1) remote debugging is a **launch flag**,
  so it can never be turned on for an already-running Chrome; (2) Chrome 136+
  **refuses `--remote-debugging-port` when the user-data-dir is the default
  one** — Google's deliberate anti-session-stealing hardening; (3) ABE is why
  copying a profile doesn't carry logins. **Do not re-plan this without new
  evidence that Chrome's policy changed.**
- **The answer instead:** sign JARVIS's profile into **Chrome Sync** and make it
  the browser the user actually uses. Same passwords/bookmarks/history/
  extensions — so it genuinely *is* their browser. Owner chose this over the
  extension bridge (kept in `IDEAS.md` §6 as the purist option).
- **Stage-0 probes settled the design** (and one of them made the slice
  necessary): attaching over CDP to a running Chrome took **0.32s** and
  `browser.close()` **detaches without closing it** (verified still alive) — so
  quitting JARVIS can safely leave the browser up. And a **second launch on the
  same `--user-data-dir` gets NO debug port**, exiting rc=0 and forwarding to
  the first instance — so "spawn anyway" is provably wrong; attaching is
  required, not an optimisation.
- **The dangerous thing this exposed:** `_reap_stale_profile_chrome()`
  **terminated** any Chrome holding that profile dir. Safe for a throwaway;
  catastrophic once it's the user's browser full of open tabs. Replaced by
  `_profile_chrome_pid()`, which only **reports** — and `_launch_real` now
  refuses with the pid and the exact recovery ("quit it, relaunch from the
  JARVIS browser shortcut") rather than killing anything. **`self._proc` is set
  only for a Chrome we started**, which is what makes teardown safe.
- **Tray gained "Open my browser"** (`web.launch_daily_browser()`, idempotent —
  it never spawns a second instance). Clicking the ordinary Chrome icon opens
  the Default profile, which JARVIS can never drive; this is the way to start
  the browser it can.
- **HUD badge (vision-checked):** an amber `YOUR BROWSER · ACTING` /
  `· READING` pill in the header, distinguishing the two stacked opt-ins.
  Closes §7 item 5's HUD-indicator residual, which mattered much more once this
  is the everyday browser. 4 DOM asserts + the screenshot inspected.

### Slice 38 — close the CONFIRM payload gap (the modal showed WHERE, not WHAT)
- **The gap, stated plainly:** JARVIS's whole safety promise is that a CONFIRM
  shows the *literal* thing it will do — and that held only where a verb
  carries its own payload (`run_shell`, `send_email`, `fsaccess`). It **broke
  on commit steps**, where an earlier AUTO step deposits the payload and the
  gated step just commits it. `type_text` into a terminal confirmed as `Type
  into 'Windows PowerShell'`; `press_keys("enter")` as `Press enter (submit)
  in 'X'`; and **`browse_key("Enter")` wasn't gated at all** (§7 item 0). You
  approved a keystroke without seeing the command it submits.
- **Severity was live, not theoretical:** `data/settings.json` had
  `profile_mode: "real"` AND `allow_actions: true`, so the ungated web submit
  was reachable on the owner's real logged-in accounts.
- **Zero executor plumbing needed** — `confirmations.request(..., command=)`
  already renders a monospace box (slice 9) and `_decide_tier` already returns
  the classify dict as `gate_info`, from which `_execute_inner` reads
  `command`. Verified in code before planning; the whole slice is three
  classifiers.
- **`browse_key` Enter now CONFIRMs in REAL mode only** (owner decision). The
  isolated sandbox starts logged out, so a stray submit there commits nothing
  of the user's, and gating it would add prompt fatigue — its own safety
  problem. This also left `test_browse_key_enter_submits_search` passing
  untouched. Navigation keys (Tab/Escape/arrows) stay AUTO: moving around is
  not committing.
- **Stage 0 probe changed the design, in the direction that matters.** The plan
  assumed a desktop UIA read of the focused field was *unavailable*. It is
  **available** — but on a real Notepad edit control it returned text that
  **did not match what had just been typed.** A modal filled from that would
  have shown the WRONG text to approve, which is strictly worse than showing
  none. So the desktop side shows **what JARVIS itself typed** (recorded per
  window, 120s TTL, bounded to 8 entries), which is definitionally accurate
  about JARVIS's own action. *Do not "improve" this by switching to a live
  read without re-running that probe.*
- **Fails closed throughout:** a web read that raises, or finds no focused
  field, still CONFIRMs and says the field could not be read — it never
  downgrades to AUTO. `document.body` as `activeElement` reads as "no field"
  (a blurred page reports body with a whitespace value). Password fields are
  detected and their contents replaced with a placeholder, never pasted into
  the HUD. Payloads cap at 500 chars with the true length stated.
- **One sanitizer, two callers:** `type_text` strips newlines before sending,
  so `classify_type` shows the *same* `_sanitize_typed()` string — otherwise
  the box would be a paraphrase of what actually lands.
- **Vision check passed** (`tests/harness_commit_modal.py`): 11 DOM asserts
  plus all three screenshots inspected claim-by-claim — the terminal type, the
  submit-with-recorded-payload (`JARVIS typed this 0s ago:` above the command,
  rendering as two real lines), and the real-mode web submit (`Press Enter to
  submit on bank.example.com` over `transfer $5000 to account 9912`).

### v1.0.1 – v1.0.4 — the post-release bug run (READ THIS BEFORE SHIPPING)
Publishing exposed **five** bugs in a row that the 754-test gate had passed.
Every one shares a root cause: **verified in the dev environment, broken in the
user's.** The tests weren't wrong; they ran where the bug couldn't occur.

| # | Bug | Why every check missed it |
|---|---|---|
| CRLF | `install.bat` had LF-only endings → `cmd.exe`: *"install.bat is not recognized"* | Caught pre-release only because I *ran* a real install. Now pinned + `.gitattributes` |
| v1.0.1 | Origin guard refused `GET /` → HUD showed `{"error":"cross-origin request refused"}` | `TestClient`/Playwright navigate cleanly (`Sec-Fetch-Site: none`); a real browser arriving **via a redirect** sends `cross-site` |
| v1.0.2 | Opening Settings/Audit **wiped the conversation** | The ⚙/🗎 were same-tab `<a href>`; no test opened them. Now `target="jarvisAux"` |
| v1.0.3 | Wake word couldn't be enabled | `openwakeword` ships **without** its `.onnx` models; dev machine had them from months earlier |
| v1.0.4 | **The Desktop shortcut wouldn't start at all** | `pythonw.exe` (no console) → `sys.stdout is None` → uvicorn's `sys.stdout.isatty()` raises **inside the daemon server thread**, swallowed. Everything dev-side uses `python.exe`, which has a real stdout |
| v1.0.5 | **All voice silently dead on a fresh install** | `install.bat` fell back to `py -3` = the NEWEST Python. 3.13 removed `audioop`/`aifc`; pip installs fine there, so setup printed "Done." and voice died at first use. Dev had 3.12 |
| v1.0.6 | **"JARVIS could not start" after EVERY reboot** | The launcher allowed the server a **guessed** 15s. Measured 17.6s cold vs 3.3s warm — every boot is cold, every double-click is warm. Dev only ever double-clicked |

**The transferable lessons:**
- **A timeout constant is a GUESS about someone else's machine.** v1.0.6's 15s
  was invisible in dev because dev is always warm. If a deadline guards work
  whose cost you don't control, watch *liveness* (is the worker alive?) instead
  of guessing a duration — a slow start is not a failure.
- **When a message asks the user a question ("did startup crash?"), that is a
  bug report about your own diagnostics.** It means the code could have known
  and didn't bother to find out. Both v1.0.4 and v1.0.6 were prolonged by the
  same sentence.
- **Green tests ≠ works for users.** The suite runs in the dev environment, on a
  machine with accumulated state. Ask "what does the *user's* environment have
  that mine doesn't — and vice versa?"
- **Verify through the real entry point.** `python run.py` working proved
  nothing about `pythonw.exe tray_start.pyw`. v1.0.4 was only ever reproducible
  by launching the literal shortcut command.
- **A daemon thread swallows tracebacks.** When something "just doesn't start,"
  capture the thread's exception **to a file** — with `pythonw` there is no
  console to print to. That single technique found v1.0.4 in minutes.
- **My own error messages can lie.** v1.0.4's log blamed port 8000; the port was
  free. Don't trust a diagnostic you wrote unless it was *verified*.
- **The crash-logging investment paid off**: `run_guarded()` →
  `data/tray_error.log` + a dialog (v1.0.2) is what made v1.0.3 and v1.0.4
  diagnosable at all. Keep it.
- **Diagnostic for the next launcher bug:**
  `.venv\Scripts\python.exe tray_start.pyw` (console version of the shortcut),
  then read `data/tray_error.log`.

### Slice 37 — one-time installer + first-run key wizard
- **Goal: make JARVIS runnable by a friend, not just by its author.** Setup was
  four manual steps (`pip install`, `playwright install chromium`, the embedder
  model, hand-editing `.env`). Now: download → **double-click `install.bat`** →
  double-click a Desktop shortcut, with the API key collected **in the HUD**.
- **A true single `.exe` was costed and rejected** (measured, not guessed):
  ~364 MB of packages + ~426 MB Chromium + 91 MB model ≈ **900 MB-1 GB**, would
  have to drop local Whisper, and an unsigned binary that synthesizes input and
  runs shell commands is an antivirus/SmartScreen worst case (code-signing cert
  ~$100-400/yr). Tier 1+2 gets ~90% of the benefit for ~15% of the effort.
- **`install.bat`:** finds Python (or installs 3.12 via winget — probe-confirmed
  present, v1.29.280) → `.venv` (system Python untouched; the shortcut targets
  `.venv\Scripts\pythonw.exe` **directly**, so nothing ever needs "activating")
  → pip install → **`pywin32_postinstall -install`** (COM is NOT auto-registered
  in a venv, and `win32com` powers DPAPI/Recycle-Bin/shortcuts — skipping it
  breaks JARVIS later in ways that look unrelated) → `playwright install
  chromium` **only** (a bare `playwright install` also pulls firefox+webkit —
  measured 494 MB of waste, now test-pinned) → embedder model → Desktop
  shortcut via PowerShell. Every step aborts loudly with the real error.
- **`fsaccess.py` was deliberately NOT touched:** `_create_lnk` sets no
  `Arguments` and forces `WorkingDirectory` to the target's parent, so it can't
  build this shortcut — and widening a CONFIRM-gated security primitive to
  serve an installer is the wrong trade. The installer makes its own shortcut.
- **First-run wizard:** new `GET /api/setup_state` returns **booleans only**
  (`brain_key`, `model_ready`) — a seeded key marker is test-pinned as absent
  from the response, the same posture as the audit viewer. The HUD shows a
  cyan setup panel when no key is set; saving reuses the **existing**
  `POST /api/settings` path (already writes `.env` + hot-reloads), so this adds
  *detection* only, never a new way to write secrets. Rides the slice-36 Origin
  guard automatically (pinned).
- **A REAL shipping bug was found by running the installer rather than reading
  it: `install.bat` had LF-only line endings** (0 CRLF / 140 LF), which
  `cmd.exe` mishandles — a fresh clone would have failed with "'install.bat' is
  not recognized". Fixed, plus **`.gitattributes` (`*.bat text eol=crlf`)** so
  git can never hand a friend an LF copy, plus two tests pinning both. No code
  review would have caught this.
- **Vision check passed** (`harness_setup_visual.py`): 5 DOM asserts + the
  screenshot inspected claim-by-claim. The image caught what the DOM could not
  — the numbered steps rendered at `--text-dim`, near-unreadable, on the single
  most important screen a new user sees. Raised to full contrast and re-shot.

### Slice 36 — release readiness: closed an auth bypass, then published
- **Found by a pre-publish audit, and it is the most serious defect found in
  this project so far.** The HUD transport was **unauthenticated**. The server
  binds to 127.0.0.1, which stops the network — but NOT the browser, because
  **WebSockets are exempt from the same-origin policy.**
- **The exploit (verified live BEFORE the fix, then kept as a test):** any page
  the user visited while JARVIS ran could open `ws://127.0.0.1:8000/ws`. A
  handshake carrying `Origin: https://evil.example.com` was ACCEPTED and
  immediately received state. Because `_pump` broadcasts to every socket in
  `_clients`, the attacker page also received every `confirm_request`
  **including its `id`**, and could reply `{"type":"confirm_response",
  "approved":true}` — **approving its own prompt.** That defeats the CONFIRM
  gate, the one control standing in front of `run_shell`, `delete_path` and
  `send_email`. The test proved `approved: True` against the unfixed server.
  Especially acute because JARVIS deliberately reads untrusted web pages.
- **Fix:** `_origin_ok()` + `_ALLOWED_ORIGINS` (derived from `config.SERVER_HOST/
  PORT`, never a second hardcoded port). The WS refuses **before `accept()`**,
  so a rejected peer never enters `_clients` and never sees an id; the existing
  HTTP middleware gained the same check (CSRF on `POST /api/settings`,
  `/api/listen`). **Rule: reject a PRESENT-and-foreign Origin, permit an ABSENT
  one** — browsers always send it, so the browser surface is fully closed,
  while local tooling (pytest/harnesses/curl) keeps working; a local
  non-browser process already has code execution. Both directions test-pinned.
  Defence in depth: `Sec-Fetch-Site: cross-site` rejected too.
- **Honest scope note:** the HTTP side was the *lesser* half. Absent CORS
  headers a browser cannot READ a cross-origin response, so `/api/audit`
  payloads were never exfiltratable; the real HTTP risk was CSRF side-effects.
  The WebSocket was the severe one.
- **Also fixed (all blocked publishing):** `requirements.txt` could not produce
  a working install — **`pywin32` (all DPAPI encryption), `pycaw` (volume),
  `comtypes`, and both Gmail libraries were missing**; the list is now derived
  by an AST import scan, with 11 legacy-only deps pruned. The **README was
  entirely false** — every command it documented (`main.py`, `server.py`,
  `tray.py`, `tools/list_mics.py`) does not exist; rewritten truthfully with a
  prominent safety section, and pinned by
  `test_project_readme_documents_only_commands_that_exist`. Added `LICENSE`
  (MIT), removed `legacy/` from the tree (62 files; still local, and it remains
  in git history — accepted, it holds no secrets), and replaced
  `test_memory_live.py`'s hardcoded `cwd=r"e:\J.A.R.V.I.S"` so the suite runs
  on someone else's machine.
- **Pre-publish secret audit: clean.** 124 commits scanned — no key patterns,
  `.env` never committed, `data/` never committed.

### Slice 35 — safety integrity: the kill switches are now a real boundary
- **Found by auditing `jarvis/` for gaps the docs DIDN'T name.** Three defects
  in the safety layer itself, all verified in code before planning.
- **(1) `fs.enabled` / `web.enabled` / `search.enabled` were advisory.** They
  only withheld the verb from `tools_schema()`; unlike `shell.enabled`/
  `email.enabled` (re-checked inside their classifiers), a **direct**
  `execute()` by name still ran at full power — so the switch for the most
  powerful surface in the app (delete/write anywhere on the PC) didn't stop a
  tool name carried in conversation history after the user flips it, or a
  prompt-injected page naming the verb. Two comments asserted the opposite.
  **Fixed with ONE source of truth** (`_KILL_SWITCHES`): `tools_schema()`
  derives withholding from it and `_disabled_by_switch()` enforces execution,
  before the gate AND before dry-run. Critically this also covers the verbs
  with **no classifier at all** (`list_directory`/`read_path`/`web_search`/
  `read_page`/`close_browser` are plain `tier:"auto"`) — a per-classifier fix
  would have silently missed them. Anti-drift test pins the two sets equal.
  `web.allow_actions` was already correctly enforced (`_actions_blocked()`).
- **(2) An unrecognized tier string executed UNGATED.** `_execute_inner`
  handled `"blocked"`/`"confirm"` and fell through to running everything else;
  `_decide_tier` fails closed on a *missing* key but not a malformed value. The
  test proved it live before the fix — tier `"CONFIRM"` (wrong case) ran the
  primitive. Now only literal `"auto"` runs ungated; unknown → CONFIRM, as the
  doctrine always claimed.
- **(3) A shipped falsehood, reopened.** The workspace README claimed *"Nothing
  outside this folder is reachable by the agent's file tools"* — true until
  slices 32-33. And `if not _README.exists()` meant it could never be corrected
  on an existing install (the on-disk copy was still pre-slice-30 text). Now
  content-driven self-heal + text that discloses the real-FS reach honestly.
- **Gate (completed on a fresh bucket): 720 passed / 7 failed / 0 skipped** —
  all 7 live-brain, **each re-verified green individually** (run strictly one
  at a time). The dry-run test was checked first because slice 35 moved that
  code path; it passes. A `web_live` failure that could plausibly have been
  caused by the new `web.enabled` enforcement was ruled out by inspecting
  `data/settings.json` (`enabled: true`) and probing the brain directly, then
  passed with the cross-host confirm firing correctly. Separately, the
  deterministic core is 698/0. A self-inflicted broadcaster leak was caught
  mid-gate and fixed (see REGRESSION_CHECKPOINT).

### Slice 34 — memory retrieval recall: measured, no safe lever, nothing tuned
- **The slice-16 pattern repeated: the measurement overruled the plan, and the
  honest outcome was to ship the instrument, not a change.** Target was the
  residual ~18% paraphrase miss rate (recall 0.818). Every lever was measured
  and **ruled out** because each cost more privacy (false-surface) than it
  bought recall. Shipped metrics are **unchanged** — deliberately.
- **Root cause found (this is the real deliverable):** the residual is a
  **small-embedding-model discrimination limit, not a tuning gap.** The 4
  missing paraphrases score cosine 0.169-0.280 while 3 genuinely UNRELATED
  negatives score 0.292-0.453 — the negatives *outrank* the misses, so no
  threshold can separate them.
- **Levers measured dead:** threshold (0.35→0.30 buys ZERO recall and DOUBLES
  false-surface; →0.22 buys +3 and TRIPLES it); `retrieve_k` (0 of 4 misses
  were k-truncated); stemming (computationally verified to create overlap on
  NONE of the 4 pairs); **and a stronger embedding model, probed head-to-head**
  — at the false-surface≤0.067 bar: MiniLM (shipped) **0.818**, bge-small-en-v1.5
  0.773/0.727/0.682 across poolings+query-instruction, gte-small never reaches
  the bar (its cosines bunch near 0.9). The rivals' numbers are *optimistic*
  (ignore top-k truncation), so even their ceiling loses.
- **Shipped:** `harness_memory_eval.py --verbose` — a permanent diagnostic
  (per-query cosine/margin/miss-reason, per-negative headroom, and a threshold
  sweep printing the win beside the cost), plus its docstring now records every
  dead lever so the experiment isn't repeated. Also fixed a real latent bug:
  `memory.py`'s inline `semantic_threshold` fallback read `0.30` while
  `DEFAULT_SETTINGS` read `0.35` — dead code today, but a `settings.json` wipe
  would have silently retrieved at an untuned threshold. Pinned equal by
  `test_semantic_threshold_fallback_matches_settings_default`.
- **Gate note (honest):** 711/6/0. All 6 are the standing environmental cluster
  and each was re-verified green **individually**. New lesson recorded: a
  "re-run in isolation" that still bundles 3 live-brain tests **is itself a
  cluster** and reproduces the RPM failure — isolation means ONE test, alone.

### Manual full-stack live acceptance (slices 29–33, 2026-07-20, user-run)
After slice 33 shipped, the user ran a single manual session exercising all five
slices end to end through the real HUD (not automated tests) — the workspace/
real-FS create asymmetry, real-PC write/read/rename/copy/move, browse +
shortcut + delete-to-Recycle-Bin, the System32 refusal, clipboard round-trip +
audit redaction, and scroll + click kinds. **Verdict: everything passed.** The
one flagged issue: **double-click (`click kind='double'`) was visibly flaky
live** — real mouse/UIA timing, consistent with the project's standing
live-UIA flake pattern (§5), not a logic bug (`classify_click`/tiering were
never in question; the deterministic `kind` tests all stayed green). **User
call: not worth fixing now** — left as a known, low-priority rough edge (see
§5 and §7) rather than spawning a slice. Right-click + the rest of scroll/
real-FS were solid. This is the first cross-slice manual acceptance pass
recorded in this doc — earlier slices were each proven individually; this
confirms they compose correctly together in one live session.

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
                              slice 33: write_path/read_path/move_path/rename_path/copy_path (shutil +
                              _place() helper; overwrite/clobber recycles prior version first — recoverable)
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

  tests/                      ← 756 tests. pytest. Live/model tests gated on GEMINI_API_KEY
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
    test_fsaccess.py ← slices 32-33: classify_path_risk denylist (blocked System32/roots/
                              ancestors, traversal+symlink resolved-then-blocked), blocked-never-
                              recycles, verbatim-path-in-modal, kill-switch, overwrite-recycles-prior,
                              write/read/move/rename/copy, gated live (shortcut/delete/refuse-System32
                              + write/read/rename/copy)
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

python -m pytest tests/ -q    # full suite. A clean 0-failed run is NOT reachable on the
                              # free Gemini tier (§7 item 1) — expect a handful of live-brain failures
                              # that pass when re-run ONE AT A TIME. (~4-8 min; launches/kills
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
- **Memory (re-measured slice 19; residual EXPLAINED slice 34):** semantic + lexical-guarded retrieval, paraphrase recall 0.818 on the frozen golden set, but ~18% of paraphrases still miss; MiniLM is English-centric; needs the one-time model download (`python -m jarvis.core.embedder --setup`) else honest lexical fallback; a pinned memory is in EVERY prompt by design. **Slice 34 measured the residual and found NO safe lever — do not re-attempt without reading `harness_memory_eval.py`'s docstring.** The 4 misses score 0.169-0.280 while 3 *unrelated* negatives score 0.292-0.453, so the negatives outrank the misses and no threshold separates them; lowering the threshold costs more privacy than it buys recall (0.35→0.30 = zero recall gain, double the false-surface); k-widening is irrelevant (0 misses were k-truncated); stemming recovers nothing (verified computationally on all 4 pairs); and three rival embedding models all scored WORSE at the false-surface≤0.067 bar (bge-small 0.773/0.727/0.682, gte-small never reaches it, vs MiniLM 0.818). It is a small-model discrimination limit, reopenable only with a materially better retrieval model or a rerank stage.
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
- **A flake that LOOKS like a safety regression (seen slice 47, evidence-backed).**
  `test_extension_browser.py::test_nameless_actionable_element_fails_closed` can
  fail with `assert 'auto' == 'confirm'` on `classify_web_click({"target":
  "submit"})`. That reads like a committal click escaping its gate — it is not.
  `web.py:753` returns `tier="auto"` when `find_clickable` raises
  `BrowserUnavailable`, because with no browser the click cannot execute at all;
  the CONFIRM is skipped only for an action that then fails. So a momentarily
  disconnected extension bridge mid-gate degrades the TIER, not the safety.
  **Evidence it is nondeterministic:** same code, back-to-back non-desktop gates
  — run A `1 failed`, run B `712 passed / 0 failed`; the test also passed alone
  (1/1) and as a whole file (20/20). Do not "fix" the classifier in response to
  this without reproducing it twice.
- **Brain fallback chain (slice 44) — what it does and does NOT fix.** A transient
  brain failure walks a bounded model chain, and that is **live-proven** to rescue
  real 429s. But it does **NOT** clear the gate's clustered live failures, and the
  numbers say why: 6 failures before → **7 after**, with the chain engaging 9
  times and rescuing only 3. The other 6 exhausted BOTH models, because the suite
  bursts past the combined ~30 RPM of the two buckets. **A 429 needs ~22s to
  clear** (measured), which is too long to wait on a user-facing path — so a
  backoff retry was deliberately NOT built. Conclusion: the clustered-failure
  problem is **test pacing**, not brain resilience; fixing it means spacing the
  suite's live calls, not adding more models. Also: the chain covers
  `brain.think()` only — **the vision path is a separate Gemini call site with no
  fallback** (`the vision model was unavailable` is still a hard live failure).
  Depth is 1 by design: only `gemini-2.5-flash` was PROVEN on tool-calling parity,
  and an unproven model in the chain would break agent chains worse than a clean
  failure.
- **Double-click (`click kind='double'`, slice 29) is confirmed flaky in real manual use (user-observed, 2026-07-20)** — the same live-UIA/mouse-timing class as (1) above, not a tiering/logic bug (the deterministic `kind` dispatch + `classify_click` tests are all green; `_click_xy` correctly maps `kind` to `pydirectinput`/`pyautogui` `doubleClick`). Single-click and right-click were solid in the same session. **User call: low priority, not worth a dedicated slice right now** — leave as a known rough edge. If it's ever revisited: likely candidates are the OS double-click-speed timing window (`pydirectinput`/`pyautogui`'s synthetic double-click may fire faster/slower than Windows' registered threshold) or needing a fresh point-verify between the two clicks rather than one shared resolve.
- **Undo (slice 26):** the stack is in-memory and process-scoped — a restart
  forgets what was undoable (same posture as the chain tracker, documented not
  hidden); depth 5, deletion-quarantine retention 20 (bounded windows,
  disclosed); pop-on-attempt (a failed undo is reported, not retried); undoing
  a DND change re-opens Settings briefly (the original action's same cost);
  tabs/media-keys/email/shell are categorically irreversible and test-pinned
  as never-undoable. Redo does not exist (undoing an undo is out of scope).
- **Extension mode (slice 41) — what it costs, honestly:**
  - The extension holds **`<all_urls>`** host permission. That is what lets it
    read the tab the user is on, and it is genuinely broad: every site, all
    profiles it is installed in. Slice 41 is read-only (committal verbs refused
    at two layers), but the *permission* is not read-only.
  - **Reconnect latency up to ~60s** after a JARVIS restart — MV3's minimum
    alarm period. Measured, not tunable. Surface it in the UI rather than
    letting it read as "broken".
  - **Prompt-injection reach grows**: page text now comes from a browser full of
    live logged-in sessions. `wrap_page_content()`'s untrusted-data boundary
    still applies (pinned by a test), but it is a mitigation, not a guarantee.
  - The extension is **per-profile**; the owner's four profiles each need it
    loaded separately. Only `Default` is set up.
  - Chrome forbids extensions on `chrome://`, the Web Store and PDF viewers —
    those return an honest refusal, never a blank success.
- **Driving the user's REAL everyday Chrome: MEASURED, and only one route
  survives (2026-07-25).** The owner rejected slice 39's compromise (sync a
  fresh profile and adopt it) — they want their actual Chrome, four signed-in
  profiles and all. Three probes, run on this machine, Chrome 150.0.7871.182:
  - **P1 — the 136+ block is REAL here.** Launched `chrome.exe
    --remote-debugging-port=9222` with **no** `--user-data-dir` on a fully
    closed Chrome. Chrome **started normally** (rc=None) and **silently ignored
    the flag** — no error, no warning, port never answered. Until now this was
    quoted from slice 24 and never reproduced; it is now first-hand.
  - **P2/P2b/P2c — a relocated profile LOSES every login.** A copy of
    `Local State` + `Default/Network/Cookies` + `Preferences` launched from a
    non-default dir: the debug port **did** answer (relocation *is*
    debuggable), but `myaccount.google.com` served the **logged-out** page
    (screenshot inspected), and after actually loading google.com the profile
    held **3 cookies, ZERO auth cookies** (SID/SSID/HSID/APISID/SAPISID) versus
    **1801 cookies / 71 on google.com** in the real store. App-Bound Encryption
    does not survive relocation.
  - **Therefore: relocating the user-data-dir is DEAD as a strategy**, and so
    is any copy-the-profile approach. **A Chrome extension + native messaging
    (`IDEAS.md` §6) is the only route to the literal everyday browser.**
  - Real profile provably untouched: `Local State` size+mtime identical before
    and after every probe; `--disable-sync` throughout.
  - **Do not re-plan relocation or profile-copying without new evidence that
    Chrome changed.** Probe scripts: `probe_p1_default_dir.py`,
    `probe_p2_abe_relocation.py`, `p2c.py` (scratchpad pattern).
- **Daily-browser debug port (slice 39):** once JARVIS's Chrome is the user's
  everyday browser, it runs with `--remote-debugging-port` open on 127.0.0.1.
  **Any local process can then drive that browser** — read its pages, act as the
  signed-in user. This is the same trust boundary as the transport-auth note
  below (a local non-browser process already has code execution), and it is the
  deliberate price of the feature, but it is a genuine widening: before, only a
  logged-out throwaway profile was exposed; now it is the user's real sessions.
  Disclosed in the README, not buried. Also note JARVIS **cannot** attach to a
  profile Chrome started without the flag — it refuses and names the pid rather
  than killing the user's tabs.
- **Chrome could harden further (slice 39):** the whole approach rests on
  `--remote-debugging-port` still being allowed on a NON-default user-data-dir.
  That is exactly the permission Chrome 136 revoked for the default dir. If a
  future Chrome extends the block, real-browser mode dies and the extension
  bridge (`IDEAS.md` §6) becomes the only route. Recorded as a known future
  risk, not as permanence.
- **Transport auth (slice 36):** the HUD has **no user authentication** — the
  boundary is `Origin` validation plus the 127.0.0.1 bind, nothing more. That
  closes the browser attack surface (browsers always send `Origin`), but any
  **local process** can still drive the agent over the WS: a request with no
  `Origin` header is permitted by design, since a local non-browser process
  already has code execution on the machine. If JARVIS is ever exposed beyond
  localhost, or run on a shared/multi-user box, this needs a real auth token —
  it is a deliberate single-trusted-user design, not an oversight.
- **Terminal detection is a 7-keyword title match (found slice 38, NOT closed).**
  `_is_terminal` (`input.py`) escalates `type_text`/`press_keys` to CONFIRM when
  the window title contains `cmd`, `command prompt`, `powershell`, `terminal`,
  `wt`, `conhost` or `console`. A **WSL tab titled `malek@DESKTOP: ~`, or any
  renamed Windows Terminal profile, matches none of them** — so typing there is
  AUTO. Same "curated, not exhaustive" class as the destructive-vocabulary list.
  Severity is moderate, not critical: bare Enter is CONFIRM *everywhere*, so the
  payload still can't be submitted ungated — and since slice 38 that confirm now
  shows the typed command, so the blind-approval half is closed even when the
  terminal itself isn't recognised.
- **Commit-step payloads: what slice 38 does and does NOT cover.** The payload
  box appears on SUBMIT combos only (`enter`/`ctrl+enter`/`ctrl+shift+enter`).
  `ctrl+s`, `alt+f4`, `ctrl+w` and `delete` still confirm with description only
  — they don't commit *typed text*, so attaching it would mislead rather than
  inform. Web-side, `space` on a focused button can also activate it and is
  **not** in `_COMMITTAL_WEB_KEYS` (the desktop rule leaves plain space AUTO
  too — kept consistent deliberately). The desktop record is in-memory and
  process-scoped (a restart forgets, same posture as the undo stack) with a
  120s TTL, because a stale payload would actively mislead.
- **Open findings from the slice-35 safety audit (verified in code, NOT yet
  fixed — each deferred deliberately, not overlooked):**
  - ~~**`browse_key("Enter")` is AUTO**~~ — **CLOSED by slice 38** (CONFIRM in
    real-browser mode, carrying the focused field's contents; isolated stays
    AUTO by owner decision).
  - **No `input.enabled` kill switch.** `click`/`type_text`/`press_keys`/
    `scroll` is the universal actuator (it drives any window, including an open
    terminal or the signed-in real browser) and is the only major surface with
    no switch — shell/email/web/search/fs/memory/audit/vision all have one. The
    `input` settings section already exists, so the seam is free.
  - **Four registered classifiers have ZERO deterministic tier test:**
    `classify_type`, `classify_web_key`, `classify_create_shortcut`,
    `classify_rename_path`. Tier classification is the safety-critical part;
    each is a small table-driven test.
  - **Live tests write REAL user state.** ~17 live `think()` call sites use the
    real `memory_store` (a model-called `remember` persists into the user's own
    memory, and real memories get injected into test prompts = nondeterminism);
    5 test files write the real `data/agent_files` instead of `tmp_path` (one
    really deletes); several run `taskkill /IM notepad.exe /F`, killing the
    USER's Notepad windows with unsaved work (`test_tabs.py` already shows the
    right pattern: kill by PID). `test_search.py`'s autouse settings fixture
    has no teardown at all.
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

All four spec §1.6 scripts pass; real-browser navigate+read AND committal actions both shipped (slices 24-25); the real-filesystem surface (browse/delete/shortcut/write/read/move/rename/copy anywhere) shipped and was **manually live-verified end to end** in slices 32-33 (see the acceptance note in §2). Spec §1.2's core verb list is now essentially complete. **The project is also
SHIPPED (public repo, real users, v1.0.4)** — so "does a friend's install work"
is now a first-class concern alongside new features. Pick one and plan it. In
rough priority:

0. ~~**`browse_key("Enter")` is UN-GATED**~~ — **CLOSED by slice 38.** Enter now
   CONFIRMs in real-browser mode and the modal shows the focused field's
   contents; isolated mode stays AUTO (owner decision: that browser is logged
   out, and over-gating causes prompt fatigue). The same slice closed the
   *blind-approval* half on the desktop side — `type_text` and submit combos
   now carry their payload into the modal's monospace box. Remaining related
   residuals are in §5 (`_is_terminal`'s 7-keyword match; `space` on a focused
   web button; non-submit combos carry no payload).
1. **A resilient/paid brain** — the free-tier `gemini-3.1-flash-lite` daily+RPM quota is the single biggest drag on the test gate (rate-limited runs at nearly every recent checkpoint, now carrying ~29 live-brain tests). **Slice 35 settled a long-standing assumption: a fresh DAILY bucket is NOT sufficient.** A full suite run on a verified-healthy bucket (burst-probe 5/5) still produced 7 live failures — the suite's clustered calls exhaust the PER-MINUTE cap inside one run, and all 7 passed when run one at a time. So "capture a clean pass on a fresh daily bucket", repeated in checkpoints since slice 20, is not actually achievable on the free tier; only billing or a fallback chain fixes it. Either enable billing on the key (zero code) or build a brain fallback chain (flash-lite → flash, the TTS-chain pattern) so a 429 doesn't stall the agent. Highest quality-of-life-per-effort item on the list.
2. **Multi-brain (OpenAI / Claude / Ollama)** — slice 23 salvaged the settings page and left these visibly-disabled ("not ported yet"). Each needs a tool-calling adapter mapping the full primitive schema + chain loop off Gemini specifics, per-provider live tests, and **re-verification of the tiering/CONFIRM safety behavior per brain** — realistically 2–3 slices, not one.
3. **Double-click reliability** (low priority — user call, 2026-07-20) — `click kind='double'` is confirmed flaky in real manual use, real mouse/UIA timing (§5). Single-click and right-click are solid. Not urgent; revisit only if it becomes a real friction point.
4. **Close the slice-35 audit's deferred findings** (all verified in code, all
   recorded in §5): gate `browse_key("Enter")` like the desktop Enter combo;
   add an `input.enabled` kill switch (the universal actuator is the only major
   surface without one); deterministic tier tests for the four untested
   classifiers (`classify_type`, `classify_web_key`, `classify_create_shortcut`,
   `classify_rename_path`); and stop live tests writing REAL user state (the
   real memory store, the real `data/agent_files`, `taskkill /IM notepad.exe`).
   The last group is a natural "test integrity" slice on its own.
5. **Real-browser mode, round 3** — cross-host click re-gating shipped in slice 27. Remaining residuals: broader rich-editor `fill()` coverage beyond Claude's ProseMirror box (Slate/Draft/Lexical/CodeMirror); a HUD indicator for when real-browser mode / allow_actions is live (user deprioritized 2026-07-19); and the narrowed slice-27 JS-navigation residual (a named-benign button that navigates cross-host via JS is flagged post-click, not pre-gated — request-interception was considered and rejected as deadlock-risky).
6. **Memory refinements, round 3** — a HUD memory-manager panel, per-memory sensitivity tags. **The "drive the ~18% paraphrase misses down" item is CLOSED as measured-and-not-safely-reachable (slice 34)** — every lever (threshold, retrieve_k, stemming, three rival embedding models) was measured and each cost more privacy than it bought recall; see §5 and `harness_memory_eval.py --verbose`. Reopen only with a materially better retrieval model or a rerank stage, and re-measure before believing it.
7. **Vision: drive the false-refusal rate toward zero** (slice 17 left it at ~2.3%) — tune `vision.verify_pad_px`, ask for a canonical action word, or a second opinion before refusing.
8. **Real-FS round 3 (small, optional)** — a `make_folder` verb (deferred from slice 33, same `fsaccess` core); PowerShell as a second `run_shell` backend (cmd.exe only).
9. **Spotify via Web API — PROBED AND DEAD-ENDED (2026-07-18):** the Feb 2026 policy change requires the app owner to hold Premium for ALL Development-Mode endpoints (search and own-playlist reads included, not just playback), and this account is free; Extended Quota Mode needs a registered business + 250k MAU. Do NOT re-plan this without a Premium subscription appearing first. Script #1's GUI path remains the correct, proven mechanism.
10. **Email widenings** (each a deliberate slice): multiple recipients/CC, attachments beyond the cage, inbox reading (a much larger privacy surface).
11. **Web/search widenings** — the vision fallback applied in-page for canvas/JS UIs with no accessible names; browser screenshots into the HUD; multi-tab; a fallback search backend if ddgs throttling annoys.
12. **Wake-word refinements** — a HUD wake toggle; custom "hey jarvis" sensitivity; self-trigger suppression during TTS beyond the `_busy` drop.
13. **DND without the Settings pop** — revisit the CloudStore serialized blob for a silent path.
14. **Desktop-native automation hardening** — beyond the browser: more robust arbitrary native-app automation (the plan for slice 25 explicitly scoped this out in favor of browser web-apps; `input.py`'s click/type/press already work on any window, but haven't had the same measurement/hardening pass as the browser primitives — see the double-click item above).

Deliberate, documented deferrals (not silent gaps — each has a one-line reason on record): `drag`, `move_mouse`, horizontal scroll, `wifi` (slice 29); a real-FS `fs.enabled` kill-switch exists but no per-verb granularity. ElevenLabs/local-Whisper providers are ported (slice 23); OpenAI/Claude/Ollama brain providers still sit in `legacy/` until item 2 above.

---

## 8. First moves in the new session

0. **Know that this is SHIPPED** (see the banner at the top): public repo,
   real users, latest release **v1.0.4**. A regression now reaches other
   people. Before any change to install/launch/HUD-serving paths, re-read the
   "v1.0.1–v1.0.4" section above — that failure class recurred five times.
   When work is release-worthy: commit → `git push` → `gh release create vX.Y.Z
   --latest --notes ...` (gh is authenticated as `malekthegamer`).
1. Read `JARVIS_Spec_v1.md`, this file, and `CLAUDE.md` (the discipline is already in force).
2. `git log --oneline -30` for the slice history; `python -m pytest tests/ -q` to confirm **756** (deterministic core always green). Keep the desktop idle during the run (live-UIA input tests) — and if real-browser mode is on in `data/settings.json`, expect JARVIS's dedicated Chrome to open/close during the run too. **Live-MODEL tests need a healthy daily Gemini bucket AND pace under the per-minute cap** — on a heavily-used day they rate-limit and rotate failures (see `REGRESSION_CHECKPOINT.md` §1); re-run any live failure in isolation before treating it as a regression, don't run full live suites back-to-back, and capture a clean 0-failed pass on a fresh daily bucket. A paid-tier key removes this entirely.
3. Skim `REGRESSION_CHECKPOINT.md` for the 4 acceptance scripts' live status (all passing) and the most recent gate run's honest failure breakdown.
4. Ask the user which slice is next (or they'll tell you), then plan it in plan mode. If the new slice depends on an unverified mechanism, probe it first (see §6).
