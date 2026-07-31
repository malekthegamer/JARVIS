# CLAUDE.md — how to build in this repo

This file governs **how** to work here. It is the standing default for every
session — follow it without being asked (no need to type `/fable-mode`).
For **what** to build, read `JARVIS_Spec_v1.md`. Do not duplicate spec content here.

Every change moves through four stages **in order**: plan → build → self-test →
vision-check. No stage may be skipped. A stage is not "done" until its exit
criteria below are met.

---

## 1. Planning gate

Write a plan and get explicit user approval **before writing any implementation
code.** A plan is approvable only if it contains ALL of:

- [ ] **Objective** stated as a literal **Definition of Done** — a checkable
      sentence, not "improve X". ("Script #1 completes with chain_end=done and
      playback verified.")
- [ ] **Stages**, each with an entry criterion and an exit criterion.
- [ ] **File map** — every file to create/modify, one line of purpose each.
      Touching a file not on the map mid-build = a named plan amendment.
- [ ] **Risk register** — task-specific failure modes, one mitigation line each.
- [ ] **Test plan** — the tests that prove each stage, **named before code exists**.

No implementation begins until the user approves the plan. Approval conditions
the user adds are binding — record them in the plan.

### API-first for external services

Before scoping GUI automation (clicking, typing, screen-reading) against a
**specific named external service or app** — a platform, an app with a company
behind it — **probe for an official, documented API first.** An API call is
faster, more reliable, and lower-risk than driving a UI whenever one exists
(precedent: slice 11 chose the Gmail API over Outlook UI automation for exactly
this reason). This is a **standing check, not a one-time decision**:

- **State the finding in the plan even when the answer is "no API — GUI
  automation is the only path."** Don't skip the check silently.
- **GUI automation (`input.py`/`vision.py`) is the permanent, correct path for
  anything without an API** — most desktop apps, games, anything with no company
  building integrations. That's the majority of real-world software, not a
  failure case; the tiering + CONFIRM architecture is what makes it safe there.
- **If an API exists but has real limits, say so honestly** — restricted access
  tiers, app-review requirements, personal-vs-business account gates (e.g.
  Instagram's Graph API needing a business/creator account) — rather than
  assuming "API-first" always resolves cleanly.

---

## 2. Build

- Build **stage by stage in plan order**, one stage fully before the next.
- **Tests first within each stage:** write/extend the named tests, run them,
  watch them fail for the right reason, then implement to green.
- Name any deviation from the plan explicitly and amend the file map.
- Read a file in its current state before editing it.

---

## 3. Self-test loop

Before claiming ANY stage or task done, run and pass:

```
python -m pytest tests/ -q
```

- **Exit criterion: `N passed, 0 failed, 0 skipped` (exit code 0).** A skip is a
  failure with better manners. (Baseline N = 606 as of slice 25; **1034 collected
  as of slice 50**; it only grows.)
- The full suite needs a real desktop, launches/kills Notepad + a throwaway
  Chrome, drives a headless Playwright Chromium against local fixtures, sends
  live-test email to `TEST_SELF_EMAIL`, briefly toggles real Do Not Disturb
  (restored), and takes ~6 min. Live/model tests are gated on `GEMINI_API_KEY`
  (email-live also on `TEST_SELF_EMAIL` + the Gmail token); with those present
  they run (0 skipped). **The gated live-search tests (`test_search_live.py`) hit
  the real network (ddgs + a real site).** (Wake-word/tray + deterministic web/
  search tests use fakes / local fixtures / mocked ddgs — no real mic or internet;
  the live wake demo is `tests/harness_wake.py`.)
- **Live-UIA flake note:** a few live tests (`test_input`, `test_tabs`,
  live-model chains) intermittently fail under load in a full run — real mouse/
  UIA/browser timing. Re-run the named test in isolation; if it passes there,
  it's environmental, not a regression. Confirm before ever calling it flaky.
- Inner loop while iterating: run only the touched files, e.g.
  `python -m pytest tests/test_memory.py tests/test_brain.py -q`.
- Paste the real command + real result. Never assert green from memory.
- A failing live/model test: re-run it in isolation and read the actual
  assertion. Confirm regression vs. nondeterminism before dismissing it — do not
  call something flaky without evidence.
- **Per-minute rate limits are handled now (slice 45) — the DAILY cap is not.**
  `tests/_pacer.py` wraps the one SDK method every Gemini call goes through
  (`google.genai.models.Models.generate_content`, covering brain AND both vision
  paths) and paces each model to 12 calls/min against a measured ~15 RPM cap.
  This is why a gate now takes ~5 min instead of ~2: it sleeps ~175s on purpose.
  The cost is printed at the end of every run (`quota pacer: N calls … slept Xs`)
  — read it; it is the honest price of trustworthy red.
  - **Do not "fix" a slow gate by raising the budget.** 16.5 calls/min was the
    measured cause of 6-9 false failures per run for seven slices.
  - `JARVIS_TEST_NO_PACING=1` disables it (for deliberate-429 work like
    `tests/harness_brain_chain.py`); `JARVIS_TEST_RPM_BUDGET=N` retunes it.
  - If the summary says **"pacing was re-armed Nx"**, a test tore the wrapper off
    the SDK and later tests ran unprotected — find that test. (This happened once,
    in the pacer's own file, and made a gate look 5 failures better than it was.)
- **Never run two full live suites back-to-back**: the second exhausts the
  free-tier Gemini DAILY quota (429 RESOURCE_EXHAUSTED; resets midnight
  Pacific / 07:00 UTC) and live tests fail in clusters. **Pacing does NOT fix
  this** — it spaces calls within a minute, it cannot create daily headroom. A
  single probe call succeeding is a token trickle, NOT headroom — burst-probe
  (5 rapid calls) before believing quota is back. The deterministic core is
  unaffected and can be re-run freely.
- **QUIT the running JARVIS before a full suite.** Two files start a REAL server
  and must own port 8000: `test_entrypoint_smoke.py` (slice 46 — launches the
  actual `pythonw tray_start.pyw` user path) and `test_extension_browser.py`.
  With JARVIS running you get ~18 errors, each naming the holder and the fix.
  Note the implicit ordering: `test_extension_browser.py` leaks its uvicorn
  daemon thread for the rest of the process, so `test_entrypoint_smoke.py` only
  works because alphabetical collection runs it first.
- **Announce full-suite runs and get an idle desktop (~8 min)** — the live-UIA
  tests steal focus (the user may be gaming; busy desktops also flake those
  tests). Mechanical backstop: conftest refuses to start a desktop-driving run
  while a fullscreen app is up (SHQueryUserNotificationState; test-pinned in
  `tests/test_desktop_guard.py`).

---

## 4. Vision verifier

Required whenever the stage's plan states a **visual goal** (HUD, modal, panel,
layout). Do not trust your mental render of the code.

Procedure (pattern: `tests/harness_hud_visual.py`):

1. Start the server: `python run.py --no-open` (serves `http://127.0.0.1:8000`).
2. Drive the HUD deterministically with Playwright (Python) via the test hooks
   `window.__hudEvent(event)` and `window.__hudSetState(state, detail)`.
3. **Screenshot** to a PNG in the scratchpad, then **`Read` the PNG and inspect
   it** — confirm every visual claim in the plan literally (e.g. "amber modal
   shows the verbatim command in a monospace box").
4. **Also assert the DOM** for structure/state (`page.eval_on_selector_all(...)`).

"Compared" = both (3) the image checked against each stated visual claim, and
(4) DOM assertions for structure. Exit criterion: both pass.

Port 8000 stuck between runs? Kill by port, then restart:
```
Get-NetTCPConnection -LocalPort 8000 -State Listen | ForEach-Object { Stop-Process -Id $_.OwningProcess -Force }
```

---

## 5. Order (the "pipeline")

There is **no harness CLI** — do not invent or reference one. The order is
enforced procedurally. A stage is done only after every box is checked, in order:

- [ ] Plan approved (§1)
- [ ] Implemented, tests-first (§2)
- [ ] `python -m pytest tests/ -q` → 0 failed, 0 skipped (§3)
- [ ] Vision check passed, if the stage is visual (§4)
- [ ] **Then** commit the stage

A CLI to enforce this is **not worth building**: the gates already exist (plan
approval + `pytest` exit code) and a wrapper would duplicate them without adding
a real check. If that ever changes, propose the tool explicitly — never assume
one exists.

---

## Commit + environment

- **Commit per stage.** The message body states *why* + any plan deviation. End
  every commit with:
  `Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>`
- Commit only after §5 is fully checked. Never commit with failing/skipped tests.
- Windows 11, PowerShell, Python 3.12 (global installs, no venv). The machine may
  have a fullscreen app up (focus-steal); prefer `window_hint` targeting.
- Report honestly: distinguish "verified" from "believe" from "guessing". Never
  report DONE with failing tests or unmet Definition-of-Done clauses.
