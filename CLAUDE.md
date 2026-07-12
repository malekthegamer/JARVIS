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
  failure with better manners. (Baseline N = 391 as of slice 13; it only grows.)
- The full suite needs a real desktop, launches/kills Notepad + a throwaway
  Chrome, sends live-test email to `TEST_SELF_EMAIL`, briefly toggles real Do
  Not Disturb (restored), and takes ~5–6 min. Live/model tests are gated on
  `GEMINI_API_KEY` (email-live also on `TEST_SELF_EMAIL` + the Gmail token);
  with those present they run (0 skipped). (Wake-word/tray tests are
  deterministic — fakes, no real mic; the live wake demo is `tests/harness_wake.py`.)
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
