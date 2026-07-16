# The Harness — exact methods that produced this build

A reusable playbook. `CLAUDE.md` states the rules; this file shows the concrete
techniques behind them, with real examples from the slices. Follow the method,
not just the checklist. The loop:

```
PROBE → PLAN → GATE → BUILD (tests-first) → SELF-TEST → VERIFY → REPORT → COMMIT
```

---

## 1. Probe before planning
Verify every load-bearing assumption with a cheap read-only check **before**
designing on top of it. Never assume a capability exists.
- Ran `win32crypt` encrypt/roundtrip before choosing DPAPI for memory.
- Queried Chrome's UIA tree to confirm tabs are `TabItem`s before designing `close_tabs`.
- Probed `pycaw`/`nvidia-smi`/brightness availability before the system_control tiers.
- **Read the actual load-bearing code before designing** (read `confirmations.py`
  + the executor before adding run_shell's BLOCKED tier — the splice point was
  found, not guessed).

## 2. Plan (get approval before any code)
A plan is approvable only with ALL of:
- **Objective = literal Definition of Done** — a checkable sentence.
- **Stages**, each with an entry + exit criterion.
- **File map** — every file, one-line purpose. Touching an off-map file mid-build
  is a *named amendment*.
- **Risk register** — task-specific failure modes, one mitigation each.
- **Test plan** — tests **named before code exists**.

Extra discipline that paid off:
- **Ask on genuine ambiguity, don't silently pick.** Used a question for real
  forks (strip-vs-panel, cmd.exe-vs-PowerShell, encryption approach); proceeded
  on defaults for the rest.
- **Record user-added approval conditions as binding** (e.g. "forget must never
  guess" → became a store contract + a named test).
- **Thin vertical slices**: one narrow feature end-to-end (verb + gate + HUD +
  tests + live proof) before the next.

## 3. Build — tests-first within each stage
1. Write/extend the **named** tests.
2. Run them; **watch them fail for the right reason** (ImportError,
   NotImplementedError, or the specific assertion — not a typo).
3. Implement to green.
- **Red-check for trust-critical additive changes:** `git stash` the
  implementation, prove the test fails without it, `git stash pop`. (Used on the
  WS chain-snapshot test so a regression fails fast instead of hanging.)
- Read a file's current state before editing it. Name deviations explicitly.

---

## 4. The test suite (structure + methods)

**Command / exit criterion**
```
python -m pytest tests/ -q          # full gate: N passed, 0 failed, 0 skipped
python -m pytest tests/test_x.py -q # inner loop: only the touched files
```
- **Capture the real exit code** — piping to `tail` reports *tail's* exit, not
  pytest's:
  ```
  python -m pytest tests/ -q > run.log 2>&1; echo "PYTEST_EXIT=$?"; tail -3 run.log
  ```
- A **skip is a failure with better manners.** 0 failed AND 0 skipped.
- **Deterministic core + key-gated live tests.** Live/model tests skip without
  `GEMINI_API_KEY` and run with it (0 skipped when present). Keeps the core fast
  and offline while still exercising the real model.

**Isolation patterns (so tests never touch real state or each other)**
- `tmp_path` + `monkeypatch` for filesystem/env; save-and-**restore in `finally`**
  for global state (volume level/mute; `confirm.timeout_s`).
- **Subprocess spy that RAISES if called** — the safety net for destructive code:
  denylist tests patch `subprocess.Popen`/`run` to raise, so no catastrophic
  command can execute even on a logic bug.
- **Isolated real resources**: throwaway Chrome `--user-data-dir` with
  uniquely-marked tab titles so the user's browser can never match; killed by
  PID-tree in teardown.
- **Autouse leak guards**: assert no stale global state after each test (no leaked
  chain tracker, broadcaster back at IDLE) — catches cross-test pollution at the
  offending test, not two files later.
- **Cross-process tests**: subprocess A writes, a *fresh* subprocess B reads —
  proves real restart persistence, not just a new object.

**Test kinds that pull their weight**
- **Hostile tests, named in the plan**: "step 2 of 3 fails → replan or clean-fail,
  never forever"; "approve-everything still can't run a denylisted command";
  "timeout kills the process tree AND the child is gone."
- **Honesty tests that encode limits**: a base64-obfuscated `rm -rf /` must reach
  CONFIRM (proving the denylist is a backstop, not a boundary) — a test whose job
  is to stop you over-claiming.

---

## 5. Self-test — evidence over claims
- Run the **whole** suite before claiming done; paste the real command + result.
- **Never** "this should now work." Show the output.
- A failing live/model test: **re-run it in isolation, read the actual
  assertion, and find the cause before calling it flaky.** (A "flaky" test turned
  out to be a real cross-slice interaction — the model reaching for a newly-added
  CONFIRM-tier tool; fixed the test's expectation, not the product.)

## 6. Verify — visual + live end-to-end
Testing proves the code does what tests say; verification proves it does what was
asked.
- **Visual goals**: start the app (`python run.py --no-open`), drive it
  deterministically via test hooks (`window.__hudEvent`, `window.__hudSetState`)
  with Playwright, screenshot to a PNG, then **`Read` the PNG and check every
  stated visual claim** — plus DOM assertions for structure. Both, not one.
  Pattern: `tests/harness_hud_visual.py`. Don't trust the mental render.
- **Live E2E**: drive the real pipeline (WS chat in, auto-answer CONFIRM,
  capture events) and **verify mechanically** — Spotify's Pause control for
  "is it playing", a volume readback, a tab count — not the model's own claim.
  Restore state afterward (volume, kill spawned apps, temp cleanup).

## 6b. MEASURE — when "better" is a claim, make it a number (slices 16–17)
For anything probabilistic (vision, a model's judgment, a heuristic), "hardened"
is meaningless until it's a metric. **Build the metric BEFORE the fix — it may
tell you not to build the fix.**

- **Golden set with known ground truth.** Construct a surface *you* control, so
  the right answer is not a matter of opinion: `tests/harness_visionpad.py` draws
  controls on a canvas (⇒ no UIA elements, so the vision path is FORCED) and
  reports their exact rects. Score against those rects, not against vibes.
- **Escalate difficulty until it breaks.** An easy benchmark says "everything is
  perfect" and teaches nothing. VisionPad has `easy` → `--blank` → `--hard`
  (dense 40px toolbar, look-alike save/save-as, faint buttons) → `--tight`
  (touching icons). The *easy* set showed 1.0 across the board; the *hard* set
  immediately found a Print icon classified AUTO (would print with no confirm).
- **ALWAYS report the COST metric next to the win.** A verifier that refuses
  everything has a perfect catch-rate and is useless. So slice 17 reports
  **false-refusal rate** beside catch-rate, and the bottom-line **wrong-click
  rate**. The first cut looked great on catch-rate and was quietly refusing 19.6%
  of *legitimate* clicks — only the cost metric exposed it.
- **Baseline first, then the change, same cases, N reps** (live models are
  stochastic — report rates, not single runs). Paste both tables verbatim.
- **NEVER retune the benchmark after seeing the result.** Slice 16's copy/paste
  glyph was genuinely ambiguous and dragged the score down; it was left alone and
  the number reported. Tuning the test to flatter the code is how benchmarks get
  gamed.
- **Let the measurement overrule the plan.** Slice 16's approved centerpiece (a
  crop-verify 2nd model call) was **not built**: measured confabulation was 0.0
  even on a blank canvas, so it would have cost 2× latency to fix nothing. Say so
  and ship the thing the numbers *did* justify.
- Metrics harnesses are `harness_`-prefixed (not pytest-collected) because they
  drive the real model: `harness_vision_eval.py`, `harness_click_verify_eval.py`.

## 7. Report — honestly
End with: Objective / Status / Stages / Tests (with command) / Hostile pass /
**Plan deviations (named)** / **Known gaps** / Next steps.
- Distinguish "I verified" vs "I believe" vs "I'm guessing."
- State residual risk plainly (denylist is bypassable; brightness unsupported on
  this monitor; lexical retrieval misses paraphrase). Never imply airtight.
- Never report DONE with failing/skipped tests or unmet DoD clauses.

## 8. Commit — per stage, after the gate
Only after: plan approved → implemented tests-first → suite 0 failed/0 skipped →
vision check (if visual) → **then** commit. Message body = *why* + any deviation.

---

## Reasoning habits (always on)
- **Second-hypothesis rule**: write two candidate explanations before committing
  to a diagnosis. (Ruled out memory-store pollution by probing it was empty,
  *then* found the real cause.)
- **Root-cause over dismiss**: investigate surprises; don't wave them away.
- **An untested inference must NEVER be written down as a fact.** Slice 16
  recorded "a second look does not fix this — it's a perception disagreement, not
  a hallucination" in `vision.py`, the checkpoint, the handoff *and* memory. It
  was never tested. Slice 17 measured it and it was **false** (a *non-leading*
  crop re-read names the control correctly 3/3) — a whole slice was nearly
  mis-scoped by a confident guess that had hardened into documentation. If you
  didn't run it, mark it "believed", not "measured".
- **Ask the leading-question trap**: how you *phrase* a model's question changes
  its answer. "Find the paste icon" (leading, whole window) biases it into
  labelling the wrong glyph 'paste'; "what IS this?" (non-leading, isolated crop)
  gets the truth. When a model seems confidently wrong, re-ask it neutrally
  before concluding it *can't* know.
- **Live testing finds what unit tests miss**: the biggest bugs surfaced only by
  running against real apps + the real model (a library's actual API shape, a
  "set" call that silently no-ops, an app that retitles its own window). When a
  live run reveals a bug, fix it **test-first**.
- **API-first, checked explicitly, not assumed**: the fastest, most reliable way
  to make JARVIS act on a *specific external service* is almost never GUI
  automation of that service's app — it's a direct API call, when one exists.
  Probe for it like any other load-bearing assumption before planning (slice 11:
  Gmail API over Outlook UI). When no API exists, GUI automation via the existing
  primitives (`input.py`, `vision.py`) is the correct and *only* path — not a
  compromise, just what the task requires — built to the same safety standard as
  everything else (tiering, CONFIRM, verify-before-claiming-success).

## Operational gotchas
- Long suites / servers: run in the **background**, get notified, don't poll.
- Server port stuck (a stopped background server can orphan the process holding
  it): **kill by port**, then restart.
- Use a scratch dir for throwaway scripts and screenshots.
