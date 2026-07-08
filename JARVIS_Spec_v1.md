# JARVIS — Build Specification v1

**Platform:** Windows · **Built with:** Fable 5 (use "Fable Mode" for every build request)
**Scope of this doc:** the two foundational features — (1) advanced PC control, (2) the reactive HUD UI.
**How to use this doc:** feed it to Fable as the source of truth. Build slice by slice. Make Fable produce a *plan* before code on each slice, review the plan, then let it build and self-verify against the "Definition of Done" for that slice.

---

## PART 0 — Core principle

JARVIS is not a chatbot that answers. It is an **agent that acts**. Everything below assumes a
**perceive → plan → act → verify → correct** loop as the heart of the system. Build that loop first
as a walking skeleton, then widen what it can perceive and do.

---

## PART 1 — PC CONTROL

### 1.1 The mental model
"Do anything a user can do manually" = **a library of primitives** (the verbs) + **an agent loop**
(the brain that composes them). Do NOT try to build "do anything" directly. Build the verbs, then let
the LLM compose them.

### 1.2 The primitives library (the verbs)
Build these as clean, individually-testable functions. This is the foundation.

**Perception**
- `capture_screen()` → screenshot (fast; use `mss`)
- `read_ui_tree()` → structured list of on-screen elements via Windows UI Automation (`uiautomation` / `pywinauto`)
- `find_element(description)` → locate a control by natural-language description (accessibility-first, vision fallback)
- `get_active_window()` / `list_windows()`

**Action (input synthesis)**
- `click(target)` · `double_click` · `right_click`
- `type_text(str)` · `press_keys(combo)` (e.g. Ctrl+S)
- `move_mouse` · `scroll` · `drag`
- Use `pydirectinput` (works in games/fullscreen) with `pyautogui` as fallback.

**Apps & OS**
- `launch_app(name)` · `focus_window` · `close_window`
- `run_shell(cmd)` — PowerShell/cmd execution (⚠ high-privilege, see Safety)
- `file_ops` — read / write / move / rename / search files
- `system_control` — volume, brightness, wifi, clipboard, media keys

**Web (phase 2)**
- Browser automation subset (Playwright) OR reuse the vision loop inside a browser window.

**Observe / verify**
- `screenshot_diff()` — did the screen change after my action?
- `verify(expectation)` — LLM checks "does the current screen match what I expected?"

> ⚠ **The brain must be multimodal.** The vision-fallback and the verify step both require a model
> that can look at a screenshot. Pick your "brain" accordingly.

### 1.3 The agent loop (the brain)
```
receive intent  ("play my Discover Weekly on Spotify")
   ↓
PLAN     → LLM decomposes into ordered steps using available primitives
   ↓
for each step:
   choose primitive + args
   check permission tier  → (auto | confirm | blocked)   ← see 1.4
   execute
   OBSERVE  (screenshot / read_ui_tree)
   VERIFY   (did it do what I intended?)
   if failed → re-plan / retry (max N attempts)
   ↓
REPORT back to user + update the Action Log in the UI
```
**Perception strategy = hybrid.** Try the accessibility tree first (fast, reliable, structured).
Fall back to screenshot+vision only when the tree doesn't expose the element. This is how the good
computer-use agents stay both fast and robust.

### 1.4 Safety / permission tiers (non-negotiable)
Every primitive is tagged with a tier:

| Tier | Examples | Behavior |
|---|---|---|
| **AUTO** | open app, read screen, focus window, volume, search files | runs immediately |
| **CONFIRM** | delete file, send message/email, purchase, `run_shell` arbitrary, overwrite file | pause → show what it's about to do → wait for yes |
| **BLOCKED** | (your custom list — e.g. anything touching banking, system format) | refuse + explain |

Also build: a **dry-run mode** (plan + narrate without executing), an **action log** (every action
recorded, so you can see what it did), and **undo** where technically possible. This is what makes it
trustworthy enough to actually let run.

### 1.5 Suggested Windows stack
`mss` (screenshots) · `uiautomation`/`pywinauto` (accessibility tree) · `pydirectinput`+`pyautogui`
(input) · `psutil` (processes) · `subprocess`/PowerShell (OS) · multimodal LLM (plan + vision + verify).

### 1.6 Interaction scripts (build & test against these)
1. **"Open Spotify and play my Discover Weekly."**
   launch Spotify → wait for load → read_ui_tree → find "Discover Weekly" → click → verify audio/UI playing.
2. **"Close every browser tab except YouTube."**
   focus browser → read tabs → for each non-YouTube tab: close → verify count.
3. **"Find the invoice PDF I saved yesterday and email it to Sam."** ← hits the CONFIRM gate
   search files by date+type → attach → compose → **pause for confirmation** → send.
4. **"Turn my brightness down and put on do-not-disturb, I'm watching a film."**
   system_control brightness ↓ → toggle DND → verify.

### 1.7 Definition of Done (PC control, slice 1)
- The four scripts above run end-to-end.
- CONFIRM-tier actions actually pause and wait.
- Every action appears in the Action Log.
- When a step fails, the loop retries or reports cleanly — it never silently does the wrong thing.

---

## PART 2 — THE HUD UI

### 2.1 Step zero (YOU do this before Fable touches it)
Gather 3–6 **reference images** of the exact look you want (real Iron Man HUD frames, apps/dashboards
whose aesthetic you love). Paste them into Fable. *One screenshot conveys more than a paragraph of
adjectives.* The design system below is a strong **starting default** — override any of it with your refs.

### 2.2 Proposed design language (starting default)
- **Base:** near-black (#0A0E14 / #05070A). Deep, not flat grey.
- **Accent:** cyan/arc-reactor blue (#3BE8FF) + a warning amber (#FFB020) for CONFIRM/alerts.
- **Surfaces:** glassmorphic panels — subtle blur, thin 1px accent border, faint inner glow.
- **Type:** one geometric/technical sans for UI + a monospace for logs/telemetry.
- **Motion:** everything animates in (~200ms ease). Nothing pops in hard. Subtle idle drift so it feels alive.
- **Signature element:** a central reactive **core/orb** that visually responds to state.

### 2.3 Components
- **Core orb** — center; the "presence" of JARVIS.
- **Transcript feed** — what you said / what it replied.
- **Action Log panel** — live, scrolling: *what JARVIS is doing right now*, step by step. (This panel is
  the single most "JARVIS" thing on screen — you watch it think and act.)
- **System telemetry** — CPU/RAM/GPU, time, active window (mostly aesthetic + functional).
- **CONFIRM modal** — when a CONFIRM-tier action fires, a distinct amber panel: "About to: [action]. Proceed?"

### 2.4 Reactive states (each visually distinct)
`IDLE` (calm drift) → `LISTENING` (waveform reacts to mic) → `THINKING` (core pulses / spins) →
`EXECUTING` (action log active, core in "work" color) → `SPEAKING` (waveform on output). The state
machine driving these is what makes it feel alive rather than static.

### 2.5 Build order (critical)
1. Build the UI as a **static, lookable shell first** — every state hard-coded so you can stare at it and
   react ("more like this"). Aesthetic is impossible to convey in prose, trivial to convey by pointing.
2. Only once you love how it *looks*, wire the state machine to real events.

### 2.6 Definition of Done (UI, slice 1)
- All five states render and are visually distinct.
- The Action Log updates live from real agent steps.
- The CONFIRM modal actually gates a CONFIRM-tier action.
- You look at it and think "yeah, that's it" — the real test.

---

## PART 3 — Build order across the whole thing
1. **Walking skeleton:** voice in → brain → voice out → one UI state reacts. Thin but end-to-end.
2. **PC control primitives** (perception + action verbs), tested individually.
3. **Agent loop** wiring them together, with permission tiers.
4. **UI shell** (static, all states), then wire it to the loop.
5. Widen: more primitives, web actions, more scripts — one complete slice at a time.

**Every slice:** Fable plans → you review the plan → Fable builds → Fable self-tests against the
Definition of Done → then move on.
