# JARVIS — Regression Checkpoint

> The reference point for the next regression checkpoint. Compare future runs
> against this baseline: the test-suite result should stay green, and any
> script whose verdict *improves* (Blocked → Runnable) means its primitives
> have landed and it can be promoted to a real acceptance test.

**Checkpoint date:** 2026-07-20 (after Slice 33 — real-FS write/move/rename/copy)
**Tip commit at capture:** see `git log -1` (Slice 33)
**Scope:** full suite (deterministic + live/model + live-email + live-DND +
live-web + live-search) + the four-script status table below, each verdict backed
by a documented live run. Slices 13 (wake+tray), 14 (web automation), 15 (web
search) and 18 (audit log + dry-run) add capability outside the spec §1.6
four-script set, so that table is unchanged; each was live-verified separately
(wake: `harness_wake.py`; web: `test_web_live.py` incl. refusing a
prompt-injected page; search: `test_search_live.py` incl. a
search→navigate→read chain; audit/dry-run: `test_dryrun.py` incl. a live
dry-run chain proving no Notepad appeared).

> Previous checkpoints: (slice 33) 716; (slice 32) 705; (slice 31) 684; (slice 30) 671; (slice 29) 652; (slice 28) 644; (slice 27) 632; (slice 26) 619; `6ec7dc7` (slice 25) 606; `4a95cc9` (slice 24) 597;
> `867986f` (slice 23) 588; `90db8d4` (slice 22) 570; (slice 21, no new tests)
> 550; (slice 20, harness only, not collected) 550; `5e3f0dc` (slice 19) 547;
> `a67c4e5` (slice 18) 530; `7c469e8` (slice 17) 504; `9c7638f` (slice 16) 489;
> (slice 15) 423; `818a921` (slice 14) 412; `65aa362` (slice 13)
> 391; `a920313` (slice 12) 374; `3dfefa7` (slice 11) 364; `a4aa50b` (slice 5)
> 193. All 0 failed / 0 skipped.

---

## 1. Regression signal — test suite (976 tests as of slice 48)

### Slice 48 — Routines (named, saved chains)
- **save/run/list/delete_routine**, DPAPI-encrypted at rest, `routines.enabled`
  kill switch both directions.
- **The safety invariant to re-check if this is ever refactored:** `run_routine`
  replays every step through `primitives.execute()`, so each step re-hits its
  own gate. Live-proven — a `run_shell` step inside a routine still prompts.
- **Stage 0 measured the model half:** compose 4/4, bare-name invoke 4/4,
  near-miss names 4/4, and **0/4 with the routine names removed from the prompt**
  — that block is load-bearing, not decoration.
- **Regression to watch:** step-stop detection uses `startswith`, NOT
  `split(":")[0]`. The gate returns `"CANCELLED (declined): …"` and the
  parenthetical broke the original comparison, letting a DECLINED step fall
  through to the next one. Pinned by
  `test_declining_a_step_aborts_the_rest_of_the_routine`.
- Bounded: 40 steps, 100 routines, no nested routines (rejected at save AND
  re-validated at run, because the file can be hand-edited).

### Slice 47 — JARVIS can read the screen
- **`screen_query`** answers questions about what is visible. AUTO tier (prose
  only), answer wrapped as UNTRUSTED, `vision.enabled` gated both ways.
- **Stage 0 measured** on a synthetic 1920x1080 desktop, exact-string scored:
  max_edge 1024 / 1536 / 1920 all read a 30px heading, 15px body, **12px small
  print** and a dialog message — 4/4 each, ~1.3-1.6s. **1024 is the default**
  and is why `vision.qa_max_edge_px` exists separately from `vision.max_edge_px`
  (which is load-bearing for slices 16/17's published click accuracy).
- **Live-proven on the real desktop** (`tests/harness_screen_qa.py`) — quoted a
  small in-app error message verbatim.
- Gate tests are synthetic-image + REAL model call, so they are deterministic
  and `test_vision.py` stays out of `_DESKTOP_DRIVING_MODULES`.
- **Privacy:** sends the WHOLE screen to Gemini by default. Documented in README,
  gated by `vision.enabled`, `window_hint` narrows it.

### Slice 46 — the entry point is finally tested by RUNNING it
- **`tests/test_entrypoint_smoke.py` (14 tests)** launches
  `.venv\Scripts\pythonw.exe tray_start.pyw` for real and asserts the HUD serves.
  Before this, 34 entry-point tests existed and **none executed the launch** —
  which is why five post-release bugs reached users.
- **Measured:** cold start **11.6s** to first HTTP 200; process tree kill leaves
  **zero** orphans; no dialog on a clean boot.
- **Deliberate-break proof (a DoD clause, not a nicety):** breaking
  `tray_start.pyw`'s import made the boot test go red in **6.11s** naming the
  exit code, then green after restore. Re-run that proof if you ever doubt the
  file can fail.
- **Requires port 8000.** Quit JARVIS first. `test_entrypoint_smoke.py` must run
  BEFORE `test_extension_browser.py` (which leaks a uvicorn daemon thread for the
  rest of the process); alphabetical collection guarantees this today.

### Slice 45 — the first 0-failure gate (quota pacing)
- **Non-desktop gate: `677 passed, 0 failed, 0 skipped`.** First clean gate in the
  project's history. Nothing was loosened, skipped or deleted to get it.

  | | before | after |
  |---|---|---|
  | failures | 6 | **0** |
  | 429s in the run | 9 fallback calls | **0** |
  | wall-clock | 1:56 | 5:10 |
  | slept on purpose | 0s | 176.6s |

- **The cost is the headline, not a footnote:** the gate is ~2.7x slower because
  it deliberately sleeps 176.6s to stay under the ~15 RPM cap. Printed at the end
  of every run (`quota pacer: N calls … slept Xs`). **Do not "fix" the slow gate
  by raising `JARVIS_TEST_RPM_BUDGET`** — 16.5 calls/min is precisely what caused
  6-9 false failures per run for seven slices.
- **Read the "pacing was re-armed Nx" note if it appears.** It means a test tore
  the wrapper off the SDK and later tests ran unprotected. The first paced gate
  read 6 → 1 failures and was PARTLY AN ILLUSION for exactly this reason
  (`tests/test_quota_pacer.py` sorts before the four remaining live files). The
  tell was a `gemini-2.5-flash` fallback in the log that the counter said never
  happened — one inconsistent line between two numbers.
- **Still true after this slice:** pacing fixes per-minute limits only. The DAILY
  cap is untouched, so two full live suites back-to-back still fail in clusters.

### Slice 44 — brain fallback chain: the metric REFUSED the claim
- **What was built:** transient brain failures (`rate_limit`/`quota_exceeded`/
  `connection`) walk a bounded model chain; non-transient ones never do; the
  answering model is attributed.
- **The baseline vs. the result, same non-desktop gate:**

  | | live failures | chain engaged | rescued |
  |---|---|---|---|
  | before (slice 43 gate) | **6** | — | — |
  | after (slice 44 gate) | **7** | 9 | **3** |

- **So the DoD's measured clause is NOT met** and is recorded as such. The chain
  works (3 real rescues, plus a live 429 proof in `harness_brain_chain.py`) but
  cannot clear the cluster: 6 of 9 engagements exhausted BOTH models, because the
  suite bursts past the two buckets' combined ~30 RPM.
- **Recovery time, measured:** a 429 clears in **~22s** (still 429 at 18s). Too
  long for a user-facing wait, so the backoff retry was **not** built — the probe
  cancelled that work rather than the other way round.
- **Reusable conclusion for the next session:** clustered live failures are a
  **test-pacing** problem. Do not attack them from `brain.py` again.
- **Trap that cost a whole gate run:** the first post-change run read *15 failed*
  with **zero** rate-limit errors — my own chain tests had leaked
  `brain.models.gemini="m-primary"` into the settings store, 404ing every later
  live test. The diagnostic that caught it was the fallback counter reading 0
  when it should have been >0. **Always check that a metric moved for the reason
  you think.**

### v1.0.6 — "JARVIS could not start" on EVERY boot: a guessed constant
- **Symptom:** the user got the `JARVIS could not start` dialog after every
  single restart. `tray_error.log` said *"the JARVIS server did not come up
  within 15s (is something already using port 8000, or did startup crash?)"* —
  **the same message that lied in v1.0.4.**
- **Root cause (MEASURED, not guessed):** `main()` gave the server a fixed
  **15s** to answer. That constant was a guess at cold-start cost, and it is
  simply too small. On one idle machine, same command, minutes apart:

  | launch | time to answer `/api/state` | vs 15s |
  |---|---|---|
  | first (cold imports) | **17.6s** | ❌ |
  | second (warm) | **3.3s** | ✅ |

  Every boot is cold **by definition**, and competes with every other startup
  app — so autostart failed deterministically at boot and worked on every later
  double-click. `main()` then RAISED, killing the process, so JARVIS never came
  up at all: the dialog was not a warning, it was the whole outcome.
- **Correlation that pinned it:** last boot 17:43:08, `tray_error.log` written
  17:44:13 (65s after boot), nothing listening on 8000, and **no python process
  alive** — the launcher had died and never recovered.
- **Two hypotheses were measured and DISCARDED before the real one:**
  `start_wake()` blocking the lifespan (measured **1.5s** — not it) and urllib
  routing localhost through a proxy (no proxy configured — not it).
- **Fix — stop timing, start observing.** `_wait_for_server(thread=…)` now
  waits while the server thread is **alive** and returns the moment it **dies**.
  A slow start is not a failure; a dead thread is, and needs no waiting. The
  120s timeout is only a backstop against a wedged thread.
- **The v1.0.4 lesson, finally implemented.** `_run_server()` had no
  `try/except`, and `_ensure_std_streams()` points stderr at `os.devnull` — so
  a server-thread traceback went **nowhere**, which is precisely why the old
  message had to ASK "did startup crash?". It now captures the traceback to
  `data/server_error.log` and reports it. `_startup_failure_reason()` states
  facts: the real traceback, or the **named process** holding the port
  (via psutil), or an honest "still starting, nothing looks broken".
- **Second bug found in the same investigation:** `autostart._command()` built
  the Run key from `sys.executable`, so enabling autostart from a global-Python
  run pinned startup to **global Python** while the Desktop shortcut used
  `.venv` — the same app in two environments. Now prefers `.venv`, which
  install.bat guarantees is 3.12 with the right packages. (Left alone, a future
  global upgrade to 3.13 would have silently killed all voice — see v1.0.5.)
- **Verified through the REAL entry point,** not unit tests: `__pycache__`
  cleared to force a cold-ish start, launched via the literal Run-key command
  (`.venv\Scripts\pythonw.exe tray_start.pyw`). Server answered after
  **16.9s — past the old 15s deadline** — and produced **no tray_error.log, no
  server_error.log, tray alive**. The exact conditions that used to fail now
  start clean.
- Gate: non-desktop deterministic **567 passed / 7 failed**, all 7 live-brain
  (free-tier quota); `test_tray` 15/15, `test_installer` 13/13.

### Slice 38 — the CONFIRM modal shows WHAT, not just WHERE
- **Closed the one open safety hole (§7 item 0) and the blind-approval half of
  two existing gates.** `browse_key("Enter")` submitted forms on the owner's
  real logged-in accounts with **no gate at all**; `press_keys("enter")` and
  `type_text`-into-a-terminal did gate, but showed only the keystroke and the
  window — the command being submitted was invisible.
- **Reach was live, not hypothetical:** `data/settings.json` had
  `profile_mode: "real"` and `allow_actions: true` at the time of the fix.
- **Design was decided by a Stage-0 probe, and it inverted the plan's premise.**
  The plan assumed a confirm-time UIA read of the focused desktop field was
  *unavailable*. It is available — but on a real Notepad edit control it
  returned text that **did not match what had just been typed**. Showing that
  would have put the WRONG command in front of the user to approve. The desktop
  side therefore shows **what JARVIS itself typed** (per-window record, 120s
  TTL, bounded to 8, in-memory). **Do not swap this for a live read without
  re-running that probe** (`scratchpad/probe_b.py` pattern).
- **Web side reads for real:** `session.focused_field()` runs `page.evaluate`
  on the owner thread via `_do()` — the same position `classify_web_click`
  already calls `find_clickable` from. Proven end-to-end deterministically
  against the local fixture (`test_focused_field_reads_the_real_typed_value`),
  so it needs no live gate and no real Chrome.
- **Fail-closed pins:** a read that raises or finds nothing still CONFIRMs
  (`test_classify_web_key_read_failure_still_confirms`); `document.body` as
  activeElement reads as "no field", not a payload of `' '`; password fields
  are redacted (`test_classify_web_key_password_field_is_redacted`); payloads
  cap at 500 chars stating the true length.
- **Additive by construction:** both descriptions are pinned byte-identical
  (`test_classify_type_description_unchanged`,
  `test_classify_press_description_unchanged`), and isolated-mode browser
  behaviour is unchanged — `test_browse_key_enter_submits_search` passes
  untouched.
- **One sanitizer, two callers:** `_sanitize_typed()` is shared by `type_text`
  and `classify_type`, so the box can never drift from what actually gets
  typed (`test_classify_type_command_matches_what_will_actually_be_typed`).
- **Vision check passed** (`tests/harness_commit_modal.py`, 11 DOM asserts):
  all three modals screenshotted and **inspected image-by-image** — terminal
  type showing `del /s /q C:\Users\malek\Documents\*`; the submit modal
  rendering `JARVIS typed this 0s ago:` above the command as two real lines;
  and the real-mode web submit reading `Press Enter to submit on
  bank.example.com` over `transfer $5000 to account 9912`.

### v1.0.4 — THE Desktop shortcut bug, root-caused at last
- **Symptom:** the shortcut showed "JARVIS could not start"; `tray_error.log`
  said *"the JARVIS server did not come up within 15s (is something already
  using port 8000…)"*. **That message was misleading — port 8000 was FREE.**
- **Root cause (proven, not guessed):** `pythonw.exe` — what the shortcut runs —
  gives a process with **`sys.stdout` and `sys.stderr` set to `None`**. uvicorn's
  log formatter calls `sys.stdout.isatty()` (uvicorn/logging.py:42) while
  configuring logging → `AttributeError` → `ValueError: Unable to configure
  formatter 'default'` **inside the daemon server thread**, which swallowed it.
  The server died before binding; the tray only saw "didn't come up."
- **How it was caught:** the v1.0.2 crash log narrowed it to the server thread;
  then a probe script run under real `pythonw.exe` captured the thread's
  traceback to a file (the only way to see it — no console exists).
  `python.exe` works fine, which is exactly why every earlier check missed it.
- **Fix:** `_ensure_std_streams()` points `sys.stdout`/`stderr` at `os.devnull`
  when they are `None`, called at the top of `_run_server()` (before uvicorn
  configures logging) and in `run_guarded()` (before any `print()`). Real
  streams are never replaced — pinned by a test.
- **Verified the way that matters:** launched via the literal
  `pythonw.exe tray_start.pyw` — port 8000 BOUND, no crash log, `/api/state`
  returned `{"state":"idle"}`, and the HUD served with the orb. Before the fix
  the identical command produced a crash log every time.
- Gate: 528 non-desktop deterministic passed / 0 failed (+2 pins).



### v1.0.3 — wake word couldn't be enabled on a fresh install
- **User-reported:** `python tray_start.pyw` printed `[wake] could not start:
  NO_SUCHFILE ... hey_jarvis_v0.1.onnx failed. File doesn't exist`, and the tray
  "Wake-word listening" toggle wouldn't stay on.
- **Root cause:** `openwakeword` ships WITHOUT its `.onnx` model files, and
  neither `install.bat` nor the manual setup downloaded them — it worked on the
  author's machine only because dev had fetched them months earlier. Both
  symptoms are the same missing files: `toggle_wake()` calls `start_wake()`,
  the model load raises, `wake_running()` stays False, so the checkmark never
  sticks (it reads as "can't be enabled").
- **Fix:** install.bat step 5 now also runs
  `openwakeword.utils.download_models(['hey_jarvis'])` (non-fatal — wake word is
  optional, so a failed download doesn't sink the install); README manual setup
  updated; pinned by `test_install_bat_downloads_wake_word_model`. **Proven:**
  the exact call was run to a temp dir — it fetched melspectrogram + embedding +
  VAD + hey_jarvis (7 files) and the model loaded from them.
- The tray now DOES launch (the earlier "shortcut does nothing" was the port
  clash with a running run.py, per the v1.0.2 diagnostics).
- Gate: static installer/README change + 1 new pin; targeted files green (46),
  deterministic core was 525/0 earlier this session.



### v1.0.2 — two user-reported bugs
- **Opening Settings/Audit wiped the conversation.** The ⚙/🗎 icons were plain
  same-tab `<a href>` links, so clicking one navigated the browser AWAY from the
  HUD; the transcript lives only in that page, so returning reloaded a blank
  HUD. Fixed: both links now carry `target="jarvisAux"` (a shared name = one
  reused side tab), so the HUD is never left. **Proven in a real browser** — a
  seeded transcript marker survived the gear click, HUD stayed on `/`, a second
  tab opened for settings. Pinned by a test.
- **The Desktop shortcut failed silently.** It runs `pythonw.exe`, which has no
  console, so any startup crash vanished ("it just doesn't work"). Two fixes:
  `main()` now RAISES instead of silently returning when the server doesn't come
  up, and a new `run_guarded()` (what `tray_start.pyw` now calls) writes the
  traceback to `data/tray_error.log` and shows a dialog. A silent launch failure
  is now always diagnosable. Pinned by two tests (logs-on-crash, passes-through-
  on-success). NOTE: this makes the failure *visible*; the user's specific cause
  is still to be read from their tray_error.log.
- Gate: 525 non-desktop deterministic passed / 0 failed (static HTML + tray
  launcher changes; no agent/desktop/model surface touched).



### v1.0.1 hotfix — the Origin guard blocked the HUD's own page load
- **Reported by the user minutes after v1.0.0: opening the HUD showed
  `{"error":"cross-origin request refused"}` instead of the page.** Reproduced,
  root-caused, fixed, verified — not guessed.
- **Root cause (a slice-36 regression):** the guard rejected EVERY cross-origin
  request, including a harmless `GET /`. A real browser stamps
  `Sec-Fetch-Site: cross-site` on an ordinary page load when you reach
  127.0.0.1:8000 via a redirect — e.g. typing "localhost:8000", which many
  browsers first treat as a search, then navigate from the results page. My
  tests missed it because `TestClient` and Playwright navigate cleanly
  (`Sec-Fetch-Site: none`), never via a redirect.
- **Fix:** the CSRF/Origin guard now applies to state-changing methods
  (POST/PUT/PATCH/DELETE) and the WebSocket only; safe methods (GET/HEAD/
  OPTIONS) pass. A cross-origin page still cannot READ any response — the
  same-origin policy blocks that because we send no Access-Control-Allow-Origin
  (test-pinned) — so serving a harmless GET is correct and standard CSRF design.
- **Did NOT re-open slice 36:** the WebSocket guard and the POST guard are
  unchanged; the exploit test and both CSRF tests still pass. Live re-probe:
  `GET / cross-site` → 200, `POST /api/settings` + `/api/listen` from evil.com →
  403, and the HUD loaded fully in real Firefox through the cross-site path
  (green "online" = WebSocket connected, telemetry live, zero console errors).
- Gate: 522 non-desktop deterministic passed / 0 failed (the change is
  HTTP-middleware only; desktop/live suites don't exercise it).



### Slice 37 — one-time installer + first-run key wizard
- **Setup went from four manual steps to: download → double-click
  `install.bat` → double-click a Desktop shortcut**, with the Gemini key
  collected in the HUD instead of a text editor.
- **A single `.exe` was costed and rejected on measurements, not vibes:** the
  scratch install came out at **1,004 MB** (packages 364 MB + Chromium 426 MB +
  model 91 MB), it would have to drop local Whisper, and an unsigned binary
  that synthesizes input + runs shell commands is an AV/SmartScreen worst case.
- **REAL BUG FOUND BY RUNNING IT, not reading it:** `install.bat` shipped with
  **LF-only line endings** (0 CRLF / 140 LF). `cmd.exe` mishandles that — a
  fresh clone fails with "'install.bat' is not recognized". Fixed, pinned by
  `test_install_bat_has_crlf_line_endings`, and `.gitattributes`
  (`*.bat text eol=crlf`) stops git ever handing a friend an LF copy. Also
  fixed: a successful install that declined the launch prompt exited with
  `choice`'s errorlevel 2, i.e. looked like a failure.
- **Distinguished from a false alarm:** a separate "not recognized" error was
  traced to Git Bash setting `NoDefaultCurrentDirectoryInExePath` (bare
  `install.bat` fails, `.\install.bat` works) — a harness artifact, NOT a
  product defect. Reported as such rather than "fixed" by guesswork.
- **Verified end to end on a real install** (scratch copy, path containing a
  space): all 6 steps ran, then the INSTALLED copy was functionally probed —
  `pywin32 COM OK`, **DPAPI encrypt/decrypt roundtrip True**, embedder
  available, **40 primitives registered**, full stack imports. Not "files
  exist" — it actually works. The Desktop shortcut resolved to the venv's
  `pythonw.exe` + `tray_start.pyw` + correct workdir.
- `pywin32_postinstall -install` is an explicit step: COM is NOT auto-registered
  in a venv, and `win32com` powers DPAPI/Recycle-Bin/shortcuts. `playwright
  install chromium` is pinned chromium-only (a bare install pulls firefox +
  webkit — 494 MB measured waste).
- **`GET /api/setup_state` returns booleans only** — a seeded key marker is
  test-pinned as absent, and it inherits the slice-36 Origin guard (pinned).
  Saving reuses the existing `POST /api/settings` path: detection added, no new
  way to write secrets.
- **Vision check passed** — and earned its keep: 5 DOM asserts were green while
  the screenshot showed the numbered steps rendered at `--text-dim`, nearly
  unreadable, on the single most important screen a new user sees. Raised to
  full contrast and re-shot. The DOM could not have caught that.



### Slice 36 — release readiness: closed an auth bypass, then published
- **The most serious defect found in this project.** The HUD transport was
  UNAUTHENTICATED and **WebSockets are exempt from the same-origin policy**, so
  the 127.0.0.1 bind stopped the network but not the browser. Any page the user
  visited could open `ws://127.0.0.1:8000/ws`, receive every broadcast
  `confirm_request` **including its id**, and reply `approved:true` —
  **approving its own prompt and defeating the CONFIRM gate**, the one control
  in front of `run_shell` / `delete_path` / `send_email`.
- **Red-checked, not asserted:** the exploit test returned `approved: True`
  against the unfixed server, and a live probe showed
  `Origin: https://evil.example.com` ACCEPTED. After the fix the same probe
  returns **403** while the real HUD still connects (verified in real Chromium:
  green "online", telemetry live, zero console errors).
- **Fix:** `_origin_ok()`/`_ALLOWED_ORIGINS` derived from `config.SERVER_HOST/
  PORT`; WS refuses **before `accept()`** (a rejected peer never enters
  `_clients`); the existing HTTP middleware got the same check. Rule: reject a
  PRESENT-and-foreign Origin, permit an ABSENT one (browsers always send it →
  browser surface closed; local tooling unaffected). Both pinned.
- **Honest scope:** the HTTP half was lesser — absent CORS headers a browser
  cannot read a cross-origin response, so audit payloads were never
  exfiltratable; the real HTTP risk was CSRF side-effects on POST.
- **Publish blockers also fixed:** `requirements.txt` was missing **pywin32
  (all encryption), pycaw (volume), comtypes and both Gmail libs** — now
  derived by AST import scan, 11 legacy deps pruned; the README documented
  **four commands that do not exist** — rewritten and pinned by a test; added
  MIT `LICENSE`; dropped `legacy/` (62 files) from the tree; removed the
  hardcoded `e:\J.A.R.V.I.S` path that broke the suite on other machines.
- Pre-publish secret audit clean: 124 commits, no keys, `.env`/`data/` never
  committed.



**Slice-35 gate — COMPLETED on a fresh bucket (2026-07-22).** **720 passed /
7 failed / 0 skipped** (270s, full suite). **All 7 are live-brain and every one
was re-verified GREEN individually** (each run strictly ALONE): clipboard 3.7s,
dry-run 5.2s, email 6.4s, files 5.2s, both fsaccess 10.1s/8.7s, web cross-host
9.9s. Two failed with the literal reply *"Gemini is rate-limiting us"* — the
standing per-minute signature.

**The dry-run test was checked FIRST and most carefully, not last**, because
slice 35 moved that exact code path (the kill-switch check now sits before the
dry-run branch). It passes alone → the change is clear.

**A real hypothesis was raised and ruled out, not hand-waved:** `test_web_live`
failed with `gated=[]` (no CONFIRM fired). Since slice 35 now *enforces*
`web.enabled`, a persisted `web.enabled=false` would have produced exactly that
symptom — so `data/settings.json` was checked directly (`enabled: true`), and
the brain was probed independently (replied `READY`, i.e. not rate-limited)
before re-running. It then passed, with the confirm firing correctly while
still on the origin host: *"Click 'go elsewhere' … it leaves this site for a
different one (localhost)"* @ `127.0.0.1` — pre-click gating intact.

**Standing conclusion, now better evidenced: a fresh DAILY bucket is not
sufficient.** The suite's ~29 clustered live-brain calls exhaust the free-tier
PER-MINUTE cap within a single run, so a clean single `727/0/0` remains
unreached — this is the strongest case yet for §7 item 1 (a paid/resilient
brain). Earlier that same day the deterministic core was separately verified
**698 passed / 0 failed** (499 non-desktop + 199 desktop-driving), including
`test_agent_loop`, the file that caught slice 29's real regression and which
exercises the restructured tier dispatch.

**A real self-inflicted regression was caught and fixed during this gate, not
dismissed:** `test_server::test_state_endpoint` failed on a leaked THINKING
state. It resembles the pre-existing broadcaster-leak ordering artifact
documented in slice 29 — but re-running the same selection with the new tests
deselected passed 487/0, proving the new tests caused it (they call `execute()`
outside `think()`). Fixed with the standing autouse leak guard
(`test_shell.py`/`test_audit.py` pattern). **Lesson: a new failure that
matches a documented artifact still has to be proven to be that artifact.**

### Slice 35 — safety integrity: the kill switches are now a boundary
- **The bug:** `fs.enabled` / `web.enabled` / `search.enabled` only ever
  WITHHELD a verb from `tools_schema()`. Unlike `shell.enabled`/`email.enabled`
  (re-checked in their classifiers), nothing stopped a **direct** `execute()`
  by name — so the switch for the most powerful surface in the app (delete/
  write anywhere on the PC) was advice to the model, not a boundary. Two code
  comments claimed "a direct call also refuses via classify" — **false**.
- **Fix — one choke point, not 15 guards.** New `_KILL_SWITCHES` map is the
  single source of truth: `tools_schema()` derives withholding from it AND
  `_disabled_by_switch()` refuses execution, checked **before the gate and
  before dry-run** (a disabled capability is refused, not rehearsed). This also
  covers the verbs with NO classifier at all (`list_directory`, `read_path`,
  `web_search`, `read_page`, `close_browser` are plain `tier:"auto"`), which a
  per-classifier fix would have missed entirely. An anti-drift test pins that
  withheld and enforced sets stay identical. `web.allow_actions` is untouched —
  it was already properly enforced via `_actions_blocked()`.
- **Tier dispatch now fails closed.** `_execute_inner` special-cased only
  `"blocked"`/`"confirm"` and **ran everything else** — a classifier returning
  `"CONFIRM"` (merely wrong-cased) executed UNGATED. Proven live by the test
  before the fix, not theorised. Now only the literal `"auto"` runs ungated.
- **Killed a shipped falsehood.** The workspace README claimed *"Nothing
  outside this folder is reachable by the agent's file tools"* — true until
  slices 32-33. Worse, `if not _README.exists()` meant it could **never** be
  corrected on an existing install (the on-disk copy was still pre-slice-30
  text). Now content-driven self-heal, and the text discloses the real-FS
  reach. Verified by reading the actual bytes on disk.
- **Manually verified (not just test-green):** each of the 5 switches off →
  direct call returns BLOCKED; for `delete_path` the victim file was still on
  disk with contents intact.



**Slice-34 full-suite run (2026-07-21):** **711 passed / 6 failed / 0 skipped**
(312s, idle desktop). All 6 are the standing environmental cluster, **each
re-verified GREEN individually**: 5 live-brain (`test_dryrun`, `test_email_live`,
`test_files`, and BOTH `test_fsaccess` live tests) + 1 live-UIA
(`test_input::test_live_scroll_notepad_screen_changes`). Burst-probe 5/5
healthy. **Diagnostic note worth keeping:** the two fsaccess tests failed
*again* when re-run as a group of three — only passing when run strictly ONE
at a time (12.1s / 10.0s). A "re-run in isolation" that still bundles several
live-brain tests is itself a cluster and reproduces the RPM failure; isolation
means one test, alone. Both fsaccess tests' FIRST brain call succeeded and only
the SECOND failed — the per-minute cap biting mid-test, not a logic fault.
No deterministic test failed; nothing in slice 34 touches these paths.

### Slice 34 — memory retrieval recall: MEASURED, no safe lever, nothing tuned
- **The slice-16 outcome repeated: measurement overruled the plan.** The goal
  was driving the residual ~18% paraphrase miss down. Every candidate lever was
  measured and **ruled out**; no threshold/model change shipped, because each
  would have cost more privacy than it bought recall.
- **Root cause (new, and the reason this gap is not a tuning gap):** the 4
  missing paraphrases score cosine **0.169-0.280**, while 3 genuinely
  UNRELATED negatives score **0.292-0.453**. The negatives *outrank* the
  misses, so **no threshold can separate them** — it is a small-embedding-model
  discrimination limit.
- **Levers measured dead:** lowering `semantic_threshold` (0.35→0.30 buys ZERO
  recall and DOUBLES false-surface; →0.22 buys +3 recall and TRIPLES it);
  widening `retrieve_k` (0 of the 4 misses were k-truncated — all failed the
  threshold outright); stemming the lexical guard (verified computationally:
  an aggressive stemmer creates overlap on NONE of the 4 pairs).
- **Stronger embedding model — probed head-to-head, also dead.** Best
  paraphrase recall at the false-surface≤0.067 bar: shipped **MiniLM-L6-v2
  0.818**, bge-small-en-v1.5 0.773 (mean) / 0.727 (cls+query-instruction) /
  0.682 (cls), gte-small never reaches the bar at all (cosines bunch near 0.9).
  Those rival numbers are *optimistic* (computed ignoring top-k truncation),
  so even their ceiling is below MiniLM's delivered figure.
- **Shipped:** `harness_memory_eval.py --verbose` (a permanent instrument:
  per-query cosine/margin/miss-reason, per-negative headroom, and a threshold
  sweep printing win beside cost) + a real latent-bug fix — `memory.py`'s
  inline fallback read `0.30` while `DEFAULT_SETTINGS` read `0.35`, now pinned
  equal by `test_semantic_threshold_fallback_matches_settings_default`.
- Metrics **unchanged** and re-verified this slice: paraphrase 0.818, keyword
  1.000, distractor top-1 1.000, false-surface 0.067.

**Slice-33 full-suite run (2026-07-20):** **709 passed / 7 failed / 0 skipped**
(262s, idle desktop). All 7 are live-brain tests — the RPM cluster now includes
BOTH fsaccess live tests (slice 32's + slice 33's). Burst-probe healthy 5/5;
both fsaccess-live re-verified GREEN together in isolation; the rest are
standing live-model tests unrelated to slice 33 (fsaccess.py only). All 30
deterministic fsaccess tests passed in the full run. New baseline **716**; a
clean `716/0/0` still wants a fresh daily bucket (7 live-brain tests now).

### Slice 33 — real-FS round 2: write / read / move / rename / copy anywhere
- Authoring verbs on the SAME proven slice-32 core (fsaccess.py): `write_path`,
  `read_path` (AUTO + untrusted-wrap), `move_path`, `rename_path`, `copy_path`.
  Lower-risk than slice 32 — no new safety model/mechanism.
- Every mutation reuses `classify_path_risk` on the RESOLVED path (traversal/
  symlink-safe, already pinned) → BLOCKED catastrophic / CONFIRM verbatim path.
  move/rename block a protected SOURCE too; copy gates the DEST only.
- **New principle: overwrite/clobber recycles the prior version first**
  (`_place()` → `_recycle` existing file, then move/copy/write) — recoverable,
  delete parity. Existing-folder dest refused (no silent merge).
- Reuses shutil + the slice-32 `_recycle`; `fs.enabled` withholds all 8 real-FS
  verbs; `fs.max_write_kb` caps writes. No JARVIS undo (Recycle Bin is recovery).
- Live-proven (real brain): wrote a note → read back (disk match) → renamed →
  copied (source preserved).

### Manual full-stack live acceptance (slices 29-33, 2026-07-20, user-run)
The user ran one hands-on session through the real HUD (not automated tests)
covering all five slices together: workspace-file create-is-AUTO vs
real-FS create-is-CONFIRM asymmetry (30 vs 33), real-PC write/read/rename/
copy/move (33), browse + shortcut + delete-to-Recycle-Bin (32), the System32
refusal (32), clipboard round-trip + audit redaction (31), and scroll + click
kinds (29). **Result: everything passed** except **`click kind='double'` was
visibly flaky** — real mouse/UIA timing, the standing live-UIA flake class
(§1's flaky-test note), not a logic/tiering defect. User decision: leave it,
low priority. This is the first *cross-slice* manual acceptance recorded here
(each slice above already had its own individual live proof) — it confirms
they compose correctly in one real session, not just individually.


**Slice-32 full-suite run (2026-07-20):** **699 passed / 6 failed / 0 skipped**
(255s, idle desktop). All 6 are live-brain tests — the RPM cluster grew because
this slice adds another live-model test (fsaccess-live) to the suite
(clipboard-live, dryrun-live, email-live, files-live, fsaccess-live, web-live).
Burst-probe healthy 5/5; fsaccess-live (mine) + web-live re-verified GREEN
together in isolation; the other four are standing live-model tests unrelated
to slice 32 (`fsaccess.py` only). **All 20 new deterministic fsaccess tests
passed in the full run.** New baseline **705**; a single clean `705/0/0` still
wants a fresh daily bucket (the per-minute cap now carries 6 live-brain tests).

### Slice 32 — real-filesystem access (browse / delete-to-Recycle-Bin / shortcut)
- JARVIS now operates on the whole PC (new `fsaccess.py`), not just the
  workspace. `list_directory` (AUTO), `delete_path` (→ Recycle Bin),
  `create_shortcut`. Zero new deps (pywin32).
- **Security = layered, NOT the denylist** (user chose broad access after the
  honest reframing): the **CONFIRM gate on the verbatim resolved path is the
  boundary**; a **BLOCKED denylist** is a backstop for catastrophic targets
  (Windows tree, Program Files/ProgramData/C:\Users roots, drive roots, profile
  root, JARVIS's own dirs); **deletes go to the Recycle Bin** (recoverable).
- `classify_path_risk` runs on the `.resolve()`d path (follows `..`+symlinks)
  BEFORE the check — traversal/symlink can't smuggle a target past it
  (test-pinned); case-insensitive; ancestors-of-protected blocked too.
- Kill-switch `fs.enabled` (default on) withholds all three verbs.
- Live-proven (real brain): Desktop shortcut to a temp folder + delete a temp
  file to the Recycle Bin + **"delete System32" refused, System32 intact**.


**Slice-31 full-suite run (2026-07-20):** **681 passed / 3 failed / 0 skipped**
(263s, idle desktop). All 3 are the standing live-model RPM trio
(`test_email_live`, `test_files` live, `test_web_live` cross-host) — none touch
clipboard code (system.py/__init__/brain). Each re-verified GREEN in isolation
after a healthy 5/5 burst-probe (email alone after a pause). **All 13 clipboard
tests — including the live-brain set-clipboard — passed in the full run.** New
baseline **684**; a single clean `684/0/0` still wants a fresh daily bucket.


### Slice 31 — clipboard (get_clipboard + set_clipboard)
- Spec §1.2 system_control's last unbuilt verb. Backend `pyperclip` (declared
  dep; Stage-0 probe confirmed it works). Both AUTO; `set_clipboard` undoable
  (restores prior text via the slice-26 stack, only when prior text existed).
- **Privacy (user-chosen): audit redaction.** A new general `redact_audit`
  registry flag makes `_audit_record` store the envelope (tool/tier/status) but
  a placeholder instead of verbatim args/result — a copied password never lands
  in the durable log. Pinned: a seeded secret marker is absent from the audit
  record; the seam is opt-in (normal verbs still log verbatim). The model still
  gets the real content for the task.
- `get_clipboard` output wrapped in `web._wrap_untrusted` (clipboard bytes are
  DATA, not instructions — hostile-page-copy defense).
- Live-proven: real-OS round-trip (restore-in-teardown) + gated real-brain
  "put '<marker>' on my clipboard" → pyperclip.paste() equalled the marker.


**Slice-30 full-suite run (2026-07-20):** **665 passed / 6 failed / 0 skipped**
(267s, idle desktop). All 6 are environmental — a heavier-than-usual live
cluster because this session had already burned quota with the new live file
test + burst-probes. Each re-verified GREEN in isolation after a healthy 5/5
burst-probe: `test_files::test_live_write_then_read_note` (mine — RPM
clustering), `test_confirm_primitives::test_close_window_closes_notepad` (the
recurring unsaved-Notepad orphan — cleared strays, passed), and four standing
live-model tests (`test_chain_live`, `test_dryrun`, `test_email_live`,
`test_web_live` — RPM). **None touch slice-30 code** (files.py only); all 18
new deterministic file tests passed in the full run. New baseline **671**; a
single clean `671/0/0` still wants a fresh daily bucket.

### Slice 30 — caged file authoring (write_file + read_file)
- Closes the audit's #2 gap + a shipped falsehood (workspace README promised
  "create/delete" but only delete+search existed; now create/read/delete are
  real and the README is corrected — pinned by a "no shipped falsehood" test).
- `write_file` — caged by the existing `_contained()`; **dynamic tier: AUTO to
  create, CONFIRM to overwrite** (modal names the file); **both undoable** via
  the slice-26 quarantine (overwrite stashes the prior bytes; undo restores
  them over the new file via the new `restore_file(token, over=True)`; undoing
  a create deletes it). Size-capped (`files.max_write_kb`).
- `read_file` — AUTO, size-capped, output wrapped in the untrusted-content
  boundary (a file may hold web content JARVIS saved).
- Reuse, not new machinery: `_quarantine()` factored out of `delete_file`
  (both call it, byte-identical); `restore_file` gained an additive `over=`
  flag (default False keeps the delete-undo "won't clobber" safety,
  test-pinned). No kill-switch (parity with delete/search).
- Live-proven: real brain wrote a marker to a file and read it back, verified
  from disk.


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

> **Slice-34 re-measurement (2026-07-21): these numbers still hold, and the
> residual is now explained.** The 4 remaining paraphrase misses are NOT a
> tuning gap — they score 0.169-0.280 while 3 unrelated negatives score
> 0.292-0.453, so no threshold separates them, and three rival embedding
> models all scored WORSE at the false-surface bar. Run
> `python tests/harness_memory_eval.py --verbose` for the per-query evidence
> before attempting this again.

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
- **Real-browser cross-host click gate (slice 27) residual:** a named-benign
  control that navigates cross-host via JavaScript (no inspectable href) is
  flagged AFTER the click, not pre-gated — request-interception was
  considered and rejected as deadlock-risky. Anchor-based cross-host clicks
  ARE pre-gated (the common case).
- **Audit viewer (slice 28):** read-only, envelope-first — the list view
  never decrypts payload; a reveal decrypts one record on demand. No search/
  filter beyond tier/status/tool-substring; no export.
- **Scroll + click kinds (slice 29):** scroll is KEYBOARD PAGING (PageUp/
  PageDown) — synthetic mouse-wheel was proven dead on real Win11 WinUI apps
  (Stage-0 probe). Vertical only; no horizontal-scroll analog exists via
  keyboard. **`click kind='double'` confirmed flaky in real manual use
  (2026-07-20)** — real mouse/UIA timing, not a tiering/logic defect (single-
  click and right-click were solid in the same session); user call: low
  priority, left as-is (see SESSION_HANDOFF.md §5/§7 for detail).
- **Caged file authoring (slice 30) / clipboard (slice 31) / real-FS access
  (slices 32-33) residuals:** covered individually above/below their own
  sections; in short — file-authoring undo/quarantine is bounded (retention
  20, same as delete); clipboard content is redacted from the audit log by
  design (the first per-tool redaction) but is NOT undoable if it held a
  non-text/empty prior value; real-FS deletes/overwrites recycle (bounded
  retention, not JARVIS-undo — the Recycle Bin IS the recovery); the
  catastrophic-path denylist is an explicitly-named BACKSTOP, not the
  boundary — the CONFIRM-on-verbatim-resolved-path is the real protection.
  A `make_folder` verb and PowerShell-as-a-second-shell remain undone.

---

## 4. How to reproduce this checkpoint

```powershell
cd e:\J.A.R.V.I.S
python -m pytest tests/ -q   # expect: 716 passed, 0 failed, 0 skipped (~4-8 min)
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
