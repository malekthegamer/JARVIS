# JARVIS — feature ideas

Candidate features, kept out of `SESSION_HANDOFF.md` §7 on purpose: that list is
*engineering* backlog (gaps, safety items, deferred work). This one is
**product** — things that would change what JARVIS feels like to use.

Nothing here is committed. Each entry says what it would reuse, because in this
codebase the cost is almost entirely "what already exists".

---

## 1. Routines — "remember this as 'work mode'"

> *"work mode"* → open VS Code, mute Spotify, DND on, close non-work tabs.

A routine is a **named, saved chain**. The model composes the steps once, the
user names it, and it replays on request.

- **Reuses:** the `PRIMITIVES` registry, `ChainTracker` (`core/chain.py`), and
  the DPAPI-encrypted memory store for persistence.
- **Design question worth settling first:** does a saved routine re-confirm its
  CONFIRM steps on every run, or is approving it once at save time enough?
  Leaning **re-confirm** — approving a routine's *shape* is not approving
  today's arguments, and the whole gate doctrine is "ground truth, per action".
- **Why it's good:** small build, large payoff. It's what makes JARVIS feel like
  *yours* rather than a generic assistant.

## 2. Proactive / scheduled JARVIS

> *"Every weekday at 8, tell me my calendar and anything urgent."*

- **Newly viable as of v1.0.6.** Until that fix, autostart failed on every
  reboot, so nothing scheduled could be relied on. "Runs in the background and
  speaks up" only became real once JARVIS survived a restart.
- **Safety shape (the interesting part):** scheduled tasks should run
  **AUTO-tier only**. Anything reaching CONFIRM **parks and waits** for the
  user rather than executing unattended — an unattended agent must never be
  able to approve itself.
- **Reuses:** the tray process (already persistent), `ChainTracker`, the audit
  log. Needs a durable schedule store and a timer thread.
- Composes directly with **#1** — scheduled *routines* is the real feature.

## 3. Screen-aware Q&A — ✅ SHIPPED (slice 47)

> *"What am I looking at?"* / *"Summarise this."*

- **Reuses:** `screen.py` (`capture_screen`), `vision.py`, and a brain that is
  already multimodal. Mostly wiring machinery that exists but has never been
  pointed at the user's own question.
- **Cheapest big win on this list**, and the best ten-second demo.
- **DONE:** `screen_query` — whole screen by default (capturing the FOCUSED
  window would have answered "you're looking at the JARVIS interface" when
  you type into the HUD), AUTO tier, untrusted-wrapped, `vision.enabled`
  gated. Stage 0 measured 12px text readable at max_edge 1024.

## 4. Barge-in (interrupt mid-sentence)

Today TTS plays to completion and the wake listener drops input while `_busy`.
Being able to cut JARVIS off — *"stop"* — is the single change that most makes
an assistant feel alive rather than like a script playing back.

- **Cost:** a real cancel path through `voice/playback.py` **and** the chain
  loop, which is not currently interruptible mid-step. Moderate, not trivial.

## 5. Local model fallback (Ollama)

Less flashy, most strategic: it attacks the project's #1 recurring pain.

- A small local model handles simple intents with **no quota, no network, no
  key**; Gemini handles the hard reasoning.
- This is §7 item 1's brain fallback chain **plus** offline capability.
- **CORRECTION (slice 45):** this entry used to claim it would "stop the test
  gate producing false red". That problem is SOLVED, by a different mechanism
  — test-call pacing (`tests/_pacer.py`), 6 failures → 0. Do not re-justify
  this idea on gate noise. The real remaining case is offline / no-key /
  no-quota operation, which is still genuinely unbuilt.

## 6. Chrome extension bridge — ✅ SHIPPED (slices 41-43, v1.1.0)

The purist answer to slice 39. An extension in the user's **literal everyday
profile**, talking to JARVIS over native messaging — driving the browser they
already have open, across all profiles, with no CDP at all.

- **PROMOTED 2026-07-25: this is now the ONLY route, not the purist option.**
  Slice 40's probes killed every alternative on this machine — Chrome 150
  silently ignores `--remote-debugging-port` on the default profile, and a
  relocated/copied user-data-dir loses every login (App-Bound Encryption does
  not survive the move: 0 auth cookies vs 71 in the real store). See
  `SESSION_HANDOFF.md` §5 for the measurements.
- **Cost, honestly:** ~2–3 slices. The tier/CONFIRM layer has to be re-derived
  for content scripts — that layer is currently proven, and this re-opens it.
  It shows a developer-mode banner unless published to the Web Store (or
  force-installed by local policy), and it cannot touch `chrome://` or Web
  Store pages.
- **What it buys:** the literal everyday browser, already open, all profiles,
  no migration, no flags — and it survives the next round of Chrome hardening,
  which the CDP approach demonstrably does not.

---

## Deliberately deferred

- **Inbox reading / triage.** Genuinely useful, but a far larger privacy surface
  than anything JARVIS touches today and it needs a wider Gmail OAuth scope.
  Deserves its own deliberate slice, not a bundle.
- **Anything that exposes JARVIS beyond localhost.** The transport has no user
  auth by design (`SESSION_HANDOFF.md` §5); remote access would require real
  authentication first, not as an afterthought.
