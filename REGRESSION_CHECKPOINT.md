# JARVIS — Regression Checkpoint

> The reference point for the next regression checkpoint. Compare future runs
> against this baseline: the test-suite result should stay green, and any
> script whose verdict *improves* (Blocked → Runnable) means its primitives
> have landed and it can be promoted to a real acceptance test.

**Checkpoint date:** 2026-07-20 (after Slice 29 — scroll + double/right click)
**Tip commit at capture:** see `git log -1` (Slice 29)
**Scope:** full suite (deterministic + live/model + live-email + live-DND +
live-web + live-search) + the four-script status table below, each verdict backed
by a documented live run. Slices 13 (wake+tray), 14 (web automation), 15 (web
search) and 18 (audit log + dry-run) add capability outside the spec §1.6
four-script set, so that table is unchanged; each was live-verified separately
(wake: `harness_wake.py`; web: `test_web_live.py` incl. refusing a
prompt-injected page; search: `test_search_live.py` incl. a
search→navigate→read chain; audit/dry-run: `test_dryrun.py` incl. a live
dry-run chain proving no Notepad appeared).

> Previous checkpoints: (slice 28) 644; (slice 27) 632; (slice 26) 619; `6ec7dc7` (slice 25) 606; `4a95cc9` (slice 24) 597;
> `867986f` (slice 23) 588; `90db8d4` (slice 22) 570; (slice 21, no new tests)
> 550; (slice 20, harness only, not collected) 550; `5e3f0dc` (slice 19) 547;
> `a67c4e5` (slice 18) 530; `7c469e8` (slice 17) 504; `9c7638f` (slice 16) 489;
> (slice 15) 423; `818a921` (slice 14) 412; `65aa362` (slice 13)
> 391; `a920313` (slice 12) 374; `3dfefa7` (slice 11) 364; `a4aa50b` (slice 5)
> 193. All 0 failed / 0 skipped.

---

## 1. Regression signal — test suite (652 tests: 644 + 8 scroll/click-kind)

**Slice-29 full-suite run (2026-07-20):** **648 passed / 4 failed / 0 skipped**
(277s, idle desktop). The 4 broke into TWO REAL + two environmental:
- **2 REAL, FIXED** — `test_agent_loop`'s vision-point-click stubs had a stale
  `jinput.click` lambda signature (missing the new `kind=` kwarg the lost-turn
  code correctly passes); a TypeError, not a logic bug. Fixed the 3 stubs →
  `test_agent_loop` 16/16, and a 208-test combined run (test_input +
  test_agent_loop + test_primitives + test_server) green — proving the fix and
  that slice-29 files don't leak state.
- **2 environmental** — `test_confirm_primitives::test_close_window_closes_notepad`
  (the recurring unsaved-Notepad session-restore orphan; my scroll tests
  open/kill Notepad heavily) passed in isolation after clearing strays;
  `test_search_live::test_live_search_then_read_chain` (free-tier Gemini RPM)
  passed in isolation after a pause.
- **Honest note:** a follow-up run that EXCLUDED the 6 live-model files (to
  confirm the deterministic core post-fix) surfaced ONE ordering-artifact —
  `test_server::test_state_endpoint` saw a leaked THINKING state from an
  earlier file's `execute()`-outside-`think()` (the pre-existing broadcaster
  leak documented since slice 18). It passes alone (16/16) and did NOT fail in
  the authoritative full run; it is NOT a slice-29 regression (the 208-test
  combined run with test_server was green). A conftest broadcaster-reset guard
  is a sensible future hygiene item, out of scope here.
- New baseline **652**; a single clean `652/0/0` capture still wants a fresh
  daily bucket (the standing free-tier condition).

### Slice 29 — spec §1.2 input completion: scroll + double/right click
- Closes a **silently-missing** spec gap (§1.2 named scroll/double_click/
  right_click; none existed, none documented as deferred). `scroll` = new AUTO
  tool; `click` gained `kind=single|double|right` threaded through the fast
  AND vision paths. **`classify_click` untouched — kind never weakens the
  gate** (committal target CONFIRMs for every kind; test-pinned).
- **STAGE-0 mechanism pivot:** synthetic mouse-wheel (`pyautogui.scroll` /
  `mouse_event WHEEL` / `WM_MOUSEWHEEL`) is UNRELIABLE on Win11 WinUI (5 probes:
  moved once then never; foreground intact). Pivoted to **keyboard PageUp/
  PageDown** into the focused control (moved every press, 4/4). Vertical only;
  left/right fail closed. The lost-turn wheel impl had been mocked-green but
  live-dead — the pivot makes the live test real.
- **Verify by window-REGION diff** (not full-screen — a scroll changes only the
  target window, ≈12% of its region vs ≈0.5% of the desktop): `scroll()`
  returns the bbox, `_run_scroll` diffs that crop (threshold 0.02), honest
  "view didn't change" below it.
- **Live-proven:** `_run_scroll` on real Notepad (region-diff VERIFIED;
  at-the-top honest no-change); real-brain chain scrolled and the executor
  independently confirmed a **13.1% view change**.
- **Deliberate deferrals (documented, no longer silent):** drag, move_mouse,
  horizontal scroll, clipboard, wifi — each with a one-line reason.


**Slice-28 full-suite run (2026-07-19):** **641 passed / 3 failed / 0 skipped**
(271s, idle desktop). The 3 failures are the SAME standing live-MODEL RPM trio
as slice 27 (`test_email_live`, `test_search_live`, `test_web_live` cross-host)
— none touch the audit code. Re-verified after a healthy 5/5 burst-probe:
web_live + search_live passed together, email_live passed alone after a pause.
No deterministic test failed; **all 12 new audit tests (9 API + 3 audit.py)
passed in the full run**, and the **vision check PASSED** (12 DOM asserts +
screenshot Read: tier/status badges, dry marker, enc-null "no data", a
revealed payload in the amber mono box). A single clean `644 / 0 / 0` capture
still wants a fresh daily bucket (the unchanged free-tier condition).

### Slice 28 — audit-log HUD viewer (read-only records browser)
- Turns the slice-18 durable audit log (which had only a decrypt CLI) into a
  browsable `/audit` page. **Envelope-first / reveal-on-demand:**
  `GET /api/audit?tail=N` returns only the plaintext envelope timeline — the
  DPAPI payload is NEVER decrypted for a browse (pinned: a seeded
  `SECRET_MARKER` arg is absent from the list JSON, present only via
  `GET /api/audit/{index}/payload`, the sole decrypt path, on explicit reveal).
- `audit.py` gained `read_envelopes()` (no-decrypt, indexes each line) +
  `read_payload(index)` (decrypt one; out-of-range → None; enc-null/undecrypt-
  able → `payload_error`). `read()` + CLI unchanged. `JARVIS_AUDIT_FILE` env
  override lets the visual harness seed a temp log without touching data/audit.
- Front-end `static/audit.{html,js,css}` reuses the settings shell + hud tokens;
  filterable table, per-row reveal into an amber mono box, honest "no data"
  (enc-null) + "no records" (empty) states; all record text via `textContent`
  (injection-safe). Read-only, localhost-only, no new mutation path.


**Slice-27 full-suite run (2026-07-19):** **629 passed / 3 failed / 0 skipped**
(265s, idle desktop). All 3 failures are the standing live-MODEL RPM rotation
(`test_email_live::test_live_script3_invoice_chain`,
`test_search_live::test_live_search_then_read_chain`,
`test_web_live::test_live_cross_host_click_prompts_before_navigating`) — the
suite's clustered live calls exhaust the free-tier per-minute cap by the time
it reaches them (the web_live one failed fast at 3.6s = an immediate 429, zero
tool calls). **Each was re-verified in isolation after a healthy 5/5
burst-probe: all three pass** (email_live 7.2s, web_live 6.6s, search earlier).
No deterministic test and **no slice-27 test** failed. The change is `web.py`
cross-host click gating only; `test_web.py` is **49/49** (12 new + the
navigate/click regression pins). A single clean `632 / 0 / 0` capture still
wants a fresh daily bucket (the standing free-tier condition, unchanged since
slice 20) — the code is proven regardless.

### Slice 27 — re-gate cross-host navigation from a browser click
- Closes the slice-25 residual "a click that itself triggers cross-host
  navigation isn't re-gated." `classify_navigate` already CONFIRMs a
  cross-origin `browse_navigate`; `classify_web_click` reasoned only on the
  element name, so a benign-named link to another host navigated un-gated.
- **Anchors (knowable):** `find_clickable` now surfaces the anchor's absolute
  `href`; a shared `_cross_host(url)` (factored out of `classify_navigate`,
  which reuses it — identical behaviour) makes `classify_web_click` CONFIRM
  when the href leaves the host, naming the destination host + verbatim URL
  in the mono box, in **both** isolated and real mode.
- **JS navigation (unknowable pre-click):** no href to inspect →
  `session.click()` flags a cross-host move in its result message ("moved to
  a different site …"). Request-interception rejected (deadlock-risky). The
  residual is narrowed to a *named-benign-JS-navigating* control, detected
  not silent.
- Scope: safety gap only (user-confirmed); rich-editor coverage + HUD
  indicator remain future slices.
- **Real-Chrome live acceptance (2026-07-19, idle desktop):** the real brain
  driving JARVIS's DEDICATED Chrome via CDP (`profile_mode=real`,
  `allow_actions=true`) was told to open a local `/linkto` page and click a
  link to a different host — the cross-origin CONFIRM fired **while still on
  the origin host (127.0.0.1)**, naming the destination, before navigating.
  Confirms the gate is engine-agnostic (real Chrome == isolated Chromium for
  href extraction). Settings restored after.


**Slice-26 run (2026-07-18):** 616 passed / 3 failed / 0 skipped (239s).
All three failures are live-MODEL tests (`test_dryrun::test_live_dry_run_notepad`,
`test_email_live::test_live_script3_invoice_chain`,
`test_search_live::test_live_search_then_read_chain`) — a DIFFERENT set than
the same morning's orientation run (email + memory×3): the documented RPM
rotation signature. Each was re-run in isolation: email + search passed
immediately; dry-run failed once more *inside the throttle window* (2.6s,
assertion "the model called no tools at all" — the instant-429 profile,
model errors before any tool call) then passed twice cleanly. **All 13 new
undo tests passed in the full run, including the live volume-undo chain.**
Deterministic core 100% green. Definitive clean 0-failed pass still wants a
fresh daily bucket + idle desktop (standing recommendation, unchanged).

### Slice 26 — undo (spec §1.4's last unbuilt clause)
- A bounded (5), process-scoped, in-memory LIFO undo stack
  (`jarvis/core/undo.py`); `undo_last_action` is a brain-level meta-tool
  (like remember/forget — no new OS surface, own audit splice, dry-run
  narrates via peek without popping).
- **Undoable (capture points):** volume / mute / brightness (pre-state read
  via the paired `get_*` before acting), DND (`set_dnd` now surfaces the
  pre-toggle state it already read — no second Settings open), `remember`
  (delete-by-id of the exact record just created — no query ambiguity), and
  `delete_file` (now QUARANTINES to `data/agent_trash/<token>/` instead of
  unlinking; restore refuses to overwrite a newer occupant; retention capped
  at 20, oldest purged — a bounded undo window, disclosed).
- **Deliberately NOT undoable (test-pinned negative):** media keys (edge-
  triggered, no state), close_tabs (titles only, no URLs — reopening would
  be a guess), send_email / run_shell (categorically irreversible). A fake
  undo is worse than none.
- Pop-on-attempt semantics: a failed undo is reported honestly and NOT kept
  (a permanently-failing entry would jam everything beneath it).
- **Live proof (mechanical):** (1) `test_undo_live.py` — real brain set
  volume 25% then "undo that" → pycaw readback restored the exact pre-level;
  (2) manual chain — real brain deleted a workspace file (CONFIRM approved),
  "undo that — bring the file back" → file restored byte-identical.

**Slice-25 run (2026-07-18):** 602 passed / 4 failed. Three environmental
(isolation-green): live-model RPM (email + search_live) + the recurring
Notepad session-restore orphan. One REAL find, fixed: `test_search.py`'s
downstream-click-gate test asserted CONFIRM but the machine's persisted
`profile_mode=real` (from the live sign-in) made web-click classify BLOCKED —
so test_search now pins isolated mode (like test_web/web_live/search_live did
in slice 25 S1). Deterministic core + all web tests green. A definitive clean
0-failed pass still wants a fresh daily bucket + idle desktop (unchanged).


**Slice-24 run (2026-07-18):** 592 passed / 5 failed, all environmental,
isolation-green: 4 live-model RPM rotation (dry-run/email/memory/search) + the
recurring cross-session Win11-Notepad session-restore orphan
(`test_close_window_closes_notepad` — see the slice-23 note; passed alone).
**Zero web/slice-24 failures** — the real-browser code is clean; deterministic
core + all 28 web tests green. Definitive clean pass still wants a fresh daily
bucket (unchanged recommendation).


**HONEST STATUS — no single clean 0-failed pass was captured this checkpoint.**
The deterministic core (~500 tests) passed on every attempt; the failures were
all **live-MODEL** tests hitting the free-tier Gemini **per-minute** rate limit,
and they **rotated** across four full runs (the definitive signature of
throttling, not a regression — a real break fails the same test every time):

| Run | passed | failed | the failed set (all live-model) |
|---|---|---|---|
| 1 (02:45 UTC) | 545 | 5 | email, memory×3, vision — all "Gemini is rate-limiting us" |
| 2 (07:06 UTC) | 547 | 3 | email, dry-run + one `test_confirmations` **load** flake |
| 3 (07:1x)     | 548 | 2 | email, search |
| 4 (07:2x)     | 545 | 5 | chain, email, memory×3 |

- **Every failure verified green in isolation** (`test_confirmations` 13/13;
  `test_dryrun::test_live_dry_run_notepad` + `test_email_live` 2/2). Three
  spaced probes confirmed the **daily** quota was healthy — the cause is the
  suite's live calls clustering past the per-minute cap, cascading once one
  429 returns instantly. **No deterministic test, and no test that exercises
  the slice-21 change** (`test_input`, `test_primitives`, `test_confirm_primitives`,
  `test_tabs` — all live-UIA, not live-model) **failed on any run.**
- **Why not just retry to green:** today's free-tier bucket was depleted by
  slice-20/21 profiling, so RPM bit every clustered run. A definitive single
  `550 / 0 / 0` should be re-captured on a fresh daily bucket (a day with no
  prior heavy API use) — but the slice-21 change is proven correct without it
  (its own live-UIA tests passed every run). Same doctrine as the slice-18
  four-attempt note; the difference here is the clean single pass wasn't
  reached, and this checkpoint says so plainly rather than cherry-picking.

**Slice-22 update (2026-07-17): same pattern, two more runs, one REAL fix.**
Run 1: 567/3 — the `test_confirmations` single-flight race (its SECOND gate
flake) + dry-run/email RPM. The race was a genuine test bug — it polled for
the pending request but not for the subscriber event before dereferencing
`events[0]` — **fixed mechanically** (wait for both), so that one is gone,
not dismissed. Run 2: 566/4 — email + memory×3, all "Gemini is
rate-limiting us", all isolation-green (5/5). The standing conclusion holds:
free-tier RPM cannot reliably carry ~570 tests' clustered live calls in one
pass; the deterministic core has never failed once across all slice-21/22
gate runs. **The definitive clean pass wants a fresh daily bucket or a paid
key** — the recurring recommendation stands.

**Slice-23 update (2026-07-17): settings page salvaged; same RPM pattern.**
Two runs: run 1 585/3 (a `test_close_window_closes_notepad` failure + email/
search RPM); run 2 586/2 (dry-run + email RPM). The close_window failure was
**diagnosed and cleared, not dismissed**: a cross-session Win11-Notepad
session-restore orphan (`*ACHAIN PROOF … - Notepad`, unsaved) survived the
fixture's `taskkill /F` because modern Notepad resurrects unsaved tabs on
relaunch; `close_window` correctly refused to force it past its save dialog.
Cleared → it passed run 2. All remaining failures are the standing live-model
per-minute rate-limit rotation (email/search/dry-run, isolation-green every
time). The deterministic core — including ALL new slice-23 settings/provider
tests — passed both runs. A definitive clean 0-failed pass still wants a
fresh daily bucket or a paid key (unchanged recommendation).

### Slice 25 — act in the real browser: JARVIS clicks/types on logged-in sites (committal-gated)
- Unlocks the committal browser actions slice 24 withheld — behind a SECOND
  default-off opt-in `web.allow_actions`. Real mode stays navigate+read until
  it's on; then `browse_click`/`browse_fill`/`browse_key` are advertised and
  tiered: **committal actions (post/buy/send/delete/submit) CONFIRM** (reused
  `input._click_tier`, and the confirm names the SITE), benign clicks & typing
  AUTO. Off → all three withheld from the schema AND refused via classify.
- **New `browse_key`** (Enter/Tab/Escape/arrows — presses the FOCUSED element)
  so a search submits; **contenteditable fill** (Claude/ProseMirror boxes,
  readback via inner_text); **click awaits navigation**; **editability-aware
  fill** (skips look-alike buttons — YouTube's search *button* shares the
  input's "Search" label); **stale-Chrome reaper** before real launch (only
  the dedicated-profile pid, never the user's Chrome — test-pinned).
- **Live acceptance (real brain + real signed-in Chrome):** "go to YouTube,
  search MrBeast, open a video" → an actual `/watch` page opened end-to-end;
  Claude's ProseMirror box proven fillable directly. Committal actions ask
  first; the cross-origin nav confirm fired (auto-approved in the harness).
- Settings sub-toggle "Let JARVIS click & type on my sites" (vision-checked).
  Isolated + slice-24 navigate/read untouched.

### Slice 24 — real-browser mode: JARVIS drives a real logged-in Chrome (navigate + read)
- Slice 14's isolation pillar is now **optional**: `web.profile_mode="real"`
  makes JARVIS drive a **dedicated real Chrome** (its own profile dir, via CDP)
  logged into the user's accounts after a one-time sign-in per site. "go to
  <any site>" opens it in that logged-in Chrome. Isolated mode stays the
  **default**.
- **S0 pivot gate (probed, not assumed):** driving the literal Default profile
  is impossible on modern Chrome — persistent-context hangs (self-relaunch),
  and `--remote-debugging-port` is blocked on the default dir by Chrome 136+
  (this machine: 150). VERIFIED working path: launch Chrome with the debug port
  on a **separate `--user-data-dir`**, then `connect_over_cdp`.
- **Safety:** real mode is **navigate + read only** — `browse_click`/`browse_fill`
  are withheld from the schema AND refuse via classify (BLOCKED); cross-origin
  CONFIRM + the untrusted-content boundary still apply; teardown kills only
  JARVIS's own Chrome pid (never the user's). Settings-page toggle with an
  honest warning; **vision check passed**.
- **Live acceptance (real primitives):** JARVIS's dedicated Chrome launched,
  navigated youtube/example/wikipedia, `read_page` returned content,
  `close_browser` terminated only our pid. Found + fixed a redirect race
  (reddit-style client-side redirect destroyed the JS context → naive
  `page.title()` raised; navigate now settles + reads title defensively —
  helps isolated mode too; deterministic `/redirect` test added). Logged-in
  experience needs a one-time manual Google sign-in (harness `--wait`).

### Slice 23 — settings page salvaged from legacy (+ ElevenLabs/Whisper ports)
- `/settings` served by the rebuilt app: **every legacy feature restored** —
  brain provider/model/masked-key, TTS (auto/ElevenLabs/edge/pyttsx3) + voice
  pickers + Speak-a-test-line, STT (google/local-Whisper + model/GPU) + mic
  picker, wake toggle+sensitivity, Windows autostart — **plus** a new
  "Capabilities & safety" module (shell/email/web/search/memory/audit/vision
  kill switches, confirm timeout, smooth cursor). Hot-applied; keys land in
  `.env` masked in every response (pinned test); saves audited (section names
  only). ElevenLabs + local-Whisper providers ported from legacy and
  re-registered (unit-seam tested; no key here so no live synth).
- **Vision check passed** (`harness_settings_visual.py` + screenshot Read):
  all 5 modules, gemini configured ✓, unported brains disabled, key masked,
  voices/mics populated — image inspected claim-by-claim AND DOM-asserted.

### Slice 22 — app discovery + smooth cursor (features, live-verified)
- **`launch_app` now finds what actually exists on the machine**: desktop
  `.lnk`/`.url` shortcuts (files AND folders — a real desktop shortcut
  targeted a config folder), Steam libraries (registry root →
  libraryfolders.vdf → appmanifests, deduped), Epic manifests. Launches use
  the launchers' documented URI protocols (API-first): `steam://rungameid/`,
  `com.epicgames.launcher://apps/`. Ambiguity across genuinely different
  apps returns candidates and launches NOTHING; the same app on several
  sources resolves by priority. Game URIs get a 20 s window poll +
  honest-dispatch message (never a false OK).
- **Live acceptance (real brain, mechanical verification):** "open rocket
  league" → Epic `Sugar` URI → RocketLeague.exe up **13 s** after the
  request; "open ArtTuneDB" (desktop-only folder .lnk; old resolver: None)
  → real `ArtTuneDB - File Explorer` window.
- **Smooth cursor:** eased glide before clicks, 152.5 ms measured on a
  632 px real-screen move (cap 200 ms), landing pixel byte-identical,
  `input.smooth_cursor=false` restores teleport; zero targeting/tier/gate
  changes (pinned by the pre-existing point-click test, unmodified).

### The slice-21 win (measured, `tests/harness_latency_eval.py`)
PC-control window resolution moved from pywinauto UIA enumeration to win32
handle resolution. S1 = the Notepad chain "open notepad, type hello world,
press enter" (median of clean reps; gate/user-wait excluded):

| S1 category | Before (main, slice 20) | After (win32) |
|---|---|---|
| **Total wall** | **34.4 s** | **~5.3 s** (rep2); 7.6 s (rep1) |
| `win_resolve` (the bottleneck) | 19.0 s / **55%** | **0.01–0.16 s / ~0%** |
| readback (a nested `_target_window`) | 4.9 s / 14% | **0.04 s** |
| launch-poll UIA | 4.8 s / 14% | **~0 s** |
| model | 3.8 s / 11% | 3.0 s (now the largest piece) |
| typing + fixed settles | 1.4 s | 1.8 s (unchanged safety guards) |

(S2 vision-click after-numbers were RPM-corrupted this run; its `win_resolve`
share is eliminated by the same code path, and its ~13 s of vision *model*
calls are deliberately out of scope — slice-17-justified. Re-run the harness
on a fresh bucket for a clean S2.)

> **Slice-19 run note (honest):** the clean run above was attempt three.
> Attempt 1 failed 3 live-UIA tests (Notepad input/chain) because the
> desktop was IN USE during the background run — all passed in isolation;
> keep the machine idle for the ~8-minute suite. Attempt 2 failed only
> `test_chain_live::test_live_failing_step_hits_budget_not_infinite` on a
> transient provider fault mid-chain (chain_end `error` — a bounded,
> honest terminal state); confirmed nondeterministic by isolated re-run and
> the test's accepted set was hardened to include `error`, the same
> slice-12 doctrine already applied to `test_email_live` (named amendment).

### Memory retrieval (slice 19 — measured, golden set in `tests/harness_memory_eval.py`)
Frozen golden set: 25 memories, 22 zero-token-overlap paraphrase queries
(invariant mechanically enforced), 10 keyword, 15 unrelated negatives, 10
sibling-distractor probes. Local MiniLM embeddings (onnxruntime — no new
deps, no network, no key; `python -m jarvis.core.embedder --setup` once).
Re-run the harness rather than trusting this table.

| Metric | lexical (slice 10) | hybrid (shipped, thr 0.35) |
|---|---|---|
| Paraphrase recall@5 | 0/22 = **0.000** | 18/22 = **0.818** |
| Keyword recall@5 | 10/10 = 1.000 | 10/10 = **1.000** (guarded — can't regress) |
| Distractor top-1 | 10/10 = 1.000 | 10/10 = **1.000** |
| **False-surface rate** (privacy COST) | 1/15 = 0.067 | 1/15 = **0.067** (same single lexical Berlin-token hit; semantic added zero) |
| Median retrieve() | 0.1 ms | 2.8 ms |

Pinned prefs: `remember(pinned=true)` on the user's explicit "always…" →
a STANDING PREFERENCES block on every message (cap 10, newest first);
live-verified (pinned "address me as Captain" shaped an unrelated reply).
No model on disk → verbatim slice-10 lexical fallback (test-pinned).

> **Run note (honest):** capturing this checkpoint took FOUR full-suite
> attempts, all failures provider-side. Run 1: 529/530 — the one failure was
> `test_email_live::test_live_script3_invoice_chain` on a live Gemini
> rate-limit before any tool call (passed in isolation immediately after —
> the documented flake profile of exactly that test). Run 2, started straight
> after run 1: 5 live failures, all 429 RESOURCE_EXHAUSTED — two consecutive
> live suites exhaust the free-tier Gemini daily quota. Run 3, started after
> a SINGLE probe call succeeded: 12 live failures — one call passing is a
> token trickle, not headroom; burst-probe (5 rapid calls) before believing
> quota is back. Run 4 (recorded above): clean, on fresh post-reset quota
> (resets midnight Pacific / 07:00 UTC) as the only consumer. The
> deterministic core never failed once across all four attempts.

### Persistent audit log + dry-run (slice 18 — spec §1.4's second half)
Every `primitives.execute()` call — approved, declined, timed-out,
superseded, BLOCKED, unknown-tool, crashed — plus `remember`/`forget`
mutations now lands one durable JSONL line in `data/audit/` (plaintext
envelope: ts/chain/tool/tier/gate/status/dry_run; DPAPI-encrypted
args+result payload; rotation renames aside, never deletes; dump via
`python -m jarvis.core.audit`). Gate outcomes are recorded from the raw
`Decision.reason`, red-checked (removing the splice turns the
declined/blocked tests red). `dry run:`-prefixed requests set a tracker
flag that `execute()` enforces MECHANICALLY — zero primitives run, zero
modals, zero memory mutations; argument-complete classifiers still run, so
a denylisted command narrates BLOCKED even in a rehearsal. Live-verified:
real model, "dry run: open notepad and type hello" — no Notepad appeared,
every audit record `dry_run=true`.

### Vision-fallback accuracy (slice 16 — the first real metric)
Measured by `tests/harness_vision_eval.py` against the VisionPad golden set
(known rects). **Not** part of the suite (it drives the real model); re-run it
rather than trusting this table.

| Metric | Before | After | |
|---|---|---|---|
| Localization hit-rate (easy) | 1.00 | 1.00 | — |
| Localization hit-rate (hard) | 1.00 | 0.875 | one ambiguous copy/paste glyph (see below) |
| Tier correctness (easy / hard) | 0.958 / 0.833 | **1.00 / 1.00** | ✅ |
| **Unsafe-AUTO** (destructive→auto) | **3** | **0** | ✅ the bug that shipped the fix |
| Confabulation (populated / **blank**) | 0.00 / **0.00** | 0.00 / 0.00 | slice-5's flaw did NOT reproduce |
| Latency / model calls | 7050 ms / 1 | 7176 ms / 1 | unchanged — no 2nd call added |

- The **whole-window crop-verify pass was deliberately NOT built**: with
  localization at 1.0 and confabulation at 0.0 (even on a blank canvas), it would
  have cost **2× latency/calls to fix nothing**.
- **Adjacent-icon mis-localization** — vision can LABEL correctly while POINTING
  one icon over on a dense toolbar (asked for "paste", answered `'paste content'`,
  pointed at the neighbouring **copy** icon). That one case is the entire
  hard-hit-rate delta; the benchmark glyph was **not** retuned after seeing the
  result. **CLOSED in slice 17** — see below.

> ⚠ **CORRECTION (slice 17).** Slice 16 recorded here that "a second look does not
> fix it (perception disagreement, not hallucination)". That was an **untested
> inference and it is FALSE**. A *non-leading* re-read of a tight crop at the
> actual point names it correctly ("Copy", 3/3). The original locate asks a
> *leading* question over the whole window, which biases the label. That
> distinction is the mechanism slice 17 ships.

### Pre-click point verification (slice 17 — closes the above)
`tests/harness_click_verify_eval.py`, 48 samples over the `--hard` and `--tight`
(touching-icon) toolbars. A verifier that refuses everything would have a perfect
catch rate and be useless, so **both** rates are reported.

| Metric | Before (off) | After (on) | |
|---|---|---|---|
| **Wrong-click rate** (mis-localized **and clicked**) | **0.042** | **0.000** | ✅ the bottom line |
| Catch rate (mis-localized → refused) | — | **1.00** | ✅ |
| **False-refusal rate** (correct click wrongly refused) | 0.000 | **0.023** | the honest cost |
| Latency / model calls (vision path only) | 4344 ms / 1 | 8763 ms / 2 | ~2×; 0 extra when UIA names the control |

Default **ON**: a false refusal fails closed with an honest message and the model
can re-observe; a wrong click silently hits a control the user never approved
(possibly a destructive neighbour). Trading 2.3% honest refusals for 0% wrong
clicks is the right side of that asymmetry. The fast text path is untouched.

**0 skipped is significant:** the gated live tests ran and passed too — real
Gemini tool-calling, the vision fallback, live chains against real apps, the
two live Gmail sends, the live DND toggle against the real Settings UI, the live
web tests (`test_web_live.py`: navigate+read, and **refusing a prompt-injected
page**), AND the live search tests (`test_search_live.py`: real ddgs → answer, and
a search→navigate→read chain). Wake/tray tests are deterministic (fakes); the
wake path itself was live-verified by hand (`tests/harness_wake.py`). (3 warnings
are benign third-party deprecations: `python_multipart`, `aifc`, `audioop`.)

> **Live-UIA flake note:** two of the three full runs at this checkpoint each
> failed on ONE live test — `test_input::test_type_text_strips_newlines`, then
> `test_tabs::test_close_single_tab_verified` — real input/UIA/browser timing
> under load, both untouched by slice 13. Each passed in isolation; the run
> recorded above is the clean pass. Re-run a named live test solo before ever
> calling it a regression.

---

## 2. Four-script status (spec §1.6) — current verdicts

| # | Script | Verdict | Evidence |
|---|--------|---------|----------|
| 1 | Open Spotify → play Discover Weekly | ✅ **Passing** | Live cold run at 12 rounds (slice 6, round-12 update below); playback mechanically verified (Pause control + now-playing). Caveat: a similarly-named user playlist exists; UIA can't distinguish which exact-match the resolver picked. |
| 2 | Close every browser tab except YouTube | ✅ **Passing** | Live 4-tab isolated Chrome run (slice 8 update below); only the YouTube tab survived, batch CONFIRM named count/kept/samples. |
| 3 | Find yesterday's invoice PDF → email Sam | ✅ **Passing** | Live E2E `test_email_live.py::test_live_script3_invoice_chain` (slice 11 update below); Gmail accepted the message, modal showed verbatim recipient + exact attachment path. Runs in every full suite. |
| 4 | Turn brightness down + DND for a film | ✅ **Passing — with a documented hardware caveat** | DND ✅ (slice 12; readback-verified live via the real Settings toggle) and volume/media ✅. **Brightness is a hardware limit, not a code gap:** this monitor exposes no DDC/CI, so no software can change it — the agent reports that honestly (spec §1.7 "never silently does the wrong thing"), which is the correct behavior, not a failure. On a DDC/CI-capable display the same `set_brightness` path works. |

**History — how each verdict was reached** (kept verbatim; the table above is
the current state):

> **Slice-5 baseline (2026-07-09):** script #1 was ⚠ partially runnable
> (primitives existed; Spotify's UI exposure and install status unverified),
> scripts #2–#4 ⛔ blocked on missing verbs (tabs, file-search+email,
> system_control).

> **Slice-6 update (2026-07-10, live acceptance runs):** script #1 was driven
> live 3× through the real pipeline after the multi-step chain loop landed.
> **Chain machinery: proven** — visible plan, per-step HUD counter, a
> mid-chain CONFIRM ("Press enter (submit)") that paused and resumed the
> chain, failure → re-observe → visible replan (revision 2), honest bounded
> exhaustion reports. **Music played in run 1** (Spotify title showed the
> track), but the playlist identity was unverifiable from the UI and every
> full run **exhausted `MAX_TOOL_ROUNDS=8`** — the script genuinely needs
> ~10–12 rounds (plan+launch+observe+search-click+type+enter+observe+
> playlist-click+play+verify). Verdict: **blocked on the round budget**, not
> on primitives. Two real primitive bugs found & fixed by these runs:
> Spotify registers no App Paths key (added Start Menu .lnk resolution) and
> retitles its window to the playing track (added owning-process presence to
> the launch verify). Spotify's UI is fully UIA-visible — the vision-fallback
> concern above did not materialize.

> **Round-12 update (2026-07-10, later):** MAX_TOOL_ROUNDS raised 8 → 12 and
> the retitle bug fixed at its second site (`find_window_title` now also
> matches by owning process, so `window="Spotify"` keeps working after the
> title becomes the track name). With those plus a focus-first hint on
> type_text and verify-before-claiming prompt guidance, **script #1 PASSED
> cold, end-to-end**: plan(4 steps) → launch → click Search → type → Enter
> (CONFIRM gated, approved) → observe → click "Discover Weekly playlist
> icon" → click "Play button" → done in 11/12 rounds; playback mechanically
> verified (Pause control + now-playing) with the Discover Weekly page open.
> Script #1 verdict: ✅ **passing** (caveat: a similarly-named user playlist
> exists; UIA can't distinguish which exact-match the resolver picked).

> **Slice-8 update (2026-07-10): scripts #2 and #4 run live.**
> - **Script #2 (close every tab except YouTube): ✅ passing.** New
>   `list_tabs`/`close_tabs` primitives (UIA on the running browser's tab
>   strip; CONFIRM gates ONCE per batch with the resolved count/kept/sample
>   titles in the modal). Live: 4-tab isolated Chrome → modal named
>   "Close 3 tab(s)… keeping 1 (JTab YouTube Film)" → approved → only the
>   YouTube tab survived (VERIFY: 4 before, 1 remain).
> - **Script #4 (volume/brightness/DND for a film): ⚠ partial by hardware &
>   scope.** `set_volume 20` ✅ (readback-verified); `set_brightness` fails
>   HONESTLY on this monitor (no laptop panel, DDC/CI unresponsive —
>   sbc.set silently no-ops, so success now REQUIRES a readback; the live
>   run's false "OK" was caught and fixed). DND/Focus Assist deliberately
>   out of scope (no clean Windows API). Media keys shipped
>   (play_pause/next/prev/stop).
> - **Script #3 (find invoice → email Sam): ⚠ half-unblocked.**
>   `search_files` (AUTO, caged, name/ext/age filters) shipped; the email
>   verb remains the blocker.

> **Slice-11 update (2026-07-11): script #3 run live — ✅ passing.**
> `send_email` shipped (Gmail API, `gmail.send` scope only, OAuth token
> DPAPI-encrypted; CONFIRM on the VERBATIM To/Subject/exact-attachment-path/
> full-body block — no model summary; attachments caged to
> `data/agent_files/`; kill switch `email.enabled`). Live E2E
> (`tests/test_email_live.py::test_live_script3_invoice_chain`): the real
> model found a yesterday-dated invoice PDF via `search_files`, the modal's
> block named the recipient and the exact resolved attachment path, the
> auto-approver verified the To: line before approving (live tests send ONLY
> to `TEST_SELF_EMAIL`), and Gmail ACCEPTED the message (id returned;
> "accepted", never "delivered" — send-only scope can't verify delivery).
> Chain ended `done`. Suite at this checkpoint: **364 passed, 0 failed,
> 0 skipped.**

> **Slice-12 update (2026-07-11): script #4's DND clause run live — ✅.**
> `set_dnd`/`get_dnd` shipped. Stage 0 proved the planned WNF write is a no-op
> on the user-facing toggle (NTSTATUS 0 + changestamp advances, but the real
> switch never moves — the brightness/DDC trap); pivoted (user-approved) to
> driving the real `ms-settings:notifications` "Do not disturb" ToggleSwitch
> via UIA with a **readback confirm** (AUTO tier; opens Settings briefly).
> Live acceptance of script #4 through the real brain: model planned 2 steps →
> `set_brightness` FAILED honestly (this monitor) → `set_dnd` OK "readback
> confirmed" → chain `done`; independent `get_dnd` readback = enabled, and the
> spoken reply relayed the brightness limit truthfully. DND restored after.
> Suite at this checkpoint: **374 passed, 0 failed, 0 skipped.** Note: DND is
> wired into the suite as a live test (`test_live_dnd_toggle_and_restore`).

**Regression coverage note:** scripts #3 and #4(DND) are wired into the suite
as live acceptance/primitive tests (`test_email_live.py`,
`test_system.py::test_live_dnd_toggle_and_restore`). Scripts #1 and #2 were
verified by documented live runs, not by tests that re-run every suite —
their guardrail is the deterministic tests over their primitives
(tabs/apps/input/chain). A regression in #1/#2 end-to-end behavior would NOT
turn the suite red; re-drive them live when their primitives change.

---

## 3. Known gaps carried forward

- **All four spec scripts now pass** (script #4 with the documented hardware
  caveat: brightness is genuinely uncontrollable on this monitor — no DDC/CI —
  and the agent reports that honestly, which is correct spec §1.7 behavior, not
  a failure).
- **DND method is the public UI surface, with real costs (slice 12):** `set_dnd`
  opens a Settings window (~2–4 s) and briefly steals focus — the only silent
  path (WNF) was proven a no-op in Stage 0. It matches the toggle by
  automation_id/name; a Windows update that renames both would make it report
  "DND control isn't available…" (honest fail, pinned by a test) until the
  matcher is updated. Verified on build 26200 only.
- **Email limits (slice 11, documented + test-pinned):** "accepted by
  server" is the strongest verifiable claim; the verbatim modal is the only
  control over a prompt-injected composition; Google test-mode OAuth tokens
  expire after 7 days unless the app is published to production; send-only,
  one recipient, one caged attachment.
- **run_shell denylist is a backstop, not a boundary** (obfuscation-tested);
  vision can confabulate (gate + `from_point` are the defense).
- **Memory retrieval (slice 19) residuals:** ~18% of golden-set paraphrases
  still miss (4/22 — cosine below the 0.35 threshold); MiniLM is
  English-centric (non-English memories lean on the lexical guard); the
  semantic path needs the one-time model download, else silent-but-honest
  lexical fallback; better retrieval surfaces more, so the no-volunteer
  framing + false-surface metric are the guard (measured equal to lexical).
- **Audit log (slice 18) residuals:** process death mid-primitive leaves that
  action unrecorded (one line per action, no write-ahead record); an audit
  write failure is loud (appended note) but does NOT block the action —
  loud-but-alive over bricking the agent; `audit.enabled` can switch the
  log off (the owner's right, but an off log records nothing).
- **Dry-run (slice 18) limits:** only the leading `dry run:` prefix is
  mechanically guaranteed (mid-sentence asks rely on the model, which is
  inherently safe — not calling tools executes nothing); perception-dependent
  verbs (click/type/press/tabs/web) narrate a conditional tier rather than
  the real one, because prior dry steps never ran so the live screen can't
  match the plan.
- **Undo (slice 26) residuals:** the stack is in-memory and process-scoped —
  restart forgets what was undoable (same posture as the chain tracker);
  depth is 5, file-deletion retention is 20 (bounded windows, disclosed);
  a failed undo is popped, not retried; undoing DND re-opens the Settings
  window briefly (the original action's same visible cost); tabs, media
  keys, email and shell remain categorically irreversible — the negative
  test pins that boundary. Spotify Web API (the slice originally requested)
  was probed and is a policy dead-end without Premium (Feb 2026 dev-mode
  rules) — script #1 stays on its proven GUI path.

---

## 4. How to reproduce this checkpoint

```powershell
cd e:\J.A.R.V.I.S
python -m pytest tests/ -q   # expect: 652 passed, 0 failed, 0 skipped (~4-8 min)
                             # (on a throttled day the live-MODEL tests rotate
                             # failures — re-run each alone before suspecting
                             # a regression; deterministic core must be green)
                             # do NOT run twice back-to-back — a second full
                             # live run inside ~15 min hits Gemini 429 quota
                             # keep the DESKTOP IDLE (~8 min) — live-UIA input
                             # tests flake if the foreground window changes
                             # (live-search tests hit the real network: ddgs + a real site)
                             # needs: a real desktop, GEMINI_API_KEY,
                             # TEST_SELF_EMAIL + data/email OAuth token
                             # (sends 2 live emails to your own address),
                             # launches/kills Notepad + a throwaway Chrome,
                             # drives a headless Chromium (local fixtures only), and
                             # briefly toggles real Do Not Disturb (restored)
python tests/harness_wake.py # self-paced live wake demo (say "hey Jarvis" + a command)
```

The four-script table in §2 is backed by documented live runs; scripts #3 and
#4(DND) also re-run inside the suite. Re-verify #1/#2 by re-driving them live.
The wake word (slice 13) is verified by the harness above, not the suite.
