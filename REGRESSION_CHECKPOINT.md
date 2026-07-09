# JARVIS — Regression Checkpoint

> The reference point for the next regression checkpoint. Compare future runs
> against this baseline: the test-suite result should stay green, and any
> script whose verdict *improves* (Blocked → Runnable) means its primitives
> have landed and it can be promoted to a real acceptance test.

**Checkpoint date:** 2026-07-09
**Tip commit at capture:** `a4aa50b` on `main` (after Slice 5 — vision fallback)
**Scope:** deterministic suite + documented four-script feasibility baseline. **No live input fired** (no real mouse/keyboard driven on the desktop).

---

## 1. Regression signal — test suite

```
python -m pytest tests/ -q
193 passed, 3 warnings in 175.07s (0:02:55)   # exit 0
```

| Metric | Value |
|---|---|
| Passed | **193** |
| Failed | **0** |
| Skipped | **0** |
| Duration | **175.07s** (2:55) |
| Exit code | **0** |

**0 skipped is significant:** the `GEMINI_API_KEY`-gated live/model tests ran and
passed too — so this covers real Gemini tool-calling + the vision fallback, not
just the deterministic core. (3 warnings are benign third-party deprecations:
`python_multipart`, `aifc`, `audioop`.)

---

## 2. Four-script feasibility baseline (spec §1.6)

Measured against the **7 primitives that exist today**: `launch_app`,
`read_ui_tree`, `delete_file`, `close_window`, `click`, `type_text`, `press_keys`.

| # | Script | Verdict | Reason |
|---|--------|---------|--------|
| 1 | Open Spotify → play Discover Weekly | ⚠ **Partially runnable** | `launch_app` → `read_ui_tree` → `click` all exist. Spotify's custom-rendered UI likely defeats the accessibility tree → forces the vision fallback (flaky). App install status **unverified** — `launch_app`'s registry/App-Paths resolver is the authority and it was not driven this checkpoint. |

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
| 2 | Close every browser tab except YouTube | ⛔ **Blocked** | No tab enumeration/close verb; `close_window` closes whole windows only, not individual tabs. |
| 3 | Find yesterday's invoice PDF → email Sam | ⛔ **Blocked** | No file-search verb (only `delete_file`, caged to `data/agent_files/`); no email compose/attach/send verb. |
| 4 | Turn brightness down + DND for a film | ⛔ **Blocked** | No `system_control` primitive exists at all. |

**Hostile check:** grep of `tests/` for these scenarios (`spotify`, `discover
weekly`, `brightness`, `dnd`, `invoice`, `youtube`, `browser tab`) returned **no
matches** — so no scripted regression was silently "passing" these. The blocks
are unbuilt scope (Handoff §7 item 4), **not** breakage.

---

## 3. Known gaps carried forward

These primitives must land before scripts #2–#4 can become real end-to-end
acceptance tests:

- **Tab enumeration + per-tab close** — unblocks script #2 (`close_window` closes
  whole windows only).
- **File-search** (by date + type, outside the `data/agent_files/` cage) —
  needed for script #3.
- **Email** compose / attach / send — needed for script #3 (hits the CONFIRM gate
  on send).
- **`system_control`** (brightness, DND, and the wider volume/media/clipboard
  set) — unblocks script #4.

Script #1 needs no new primitives to *attempt*, but its reliability depends on
Spotify's accessibility exposure and the vision fallback; treat it as flaky until
hardened.

---

## 4. How to reproduce this checkpoint

```powershell
cd e:\J.A.R.V.I.S
python -m pytest tests/ -q          # expect: 193 passed, 0 failed, 0 skipped (needs GEMINI_API_KEY + a desktop)
```

The four-script table is a **static feasibility assessment** against the
primitive registry (`jarvis/primitives/__init__.py`) — re-derive it by listing
`PRIMITIVES` and mapping each script's required verbs. Promote a script from
Blocked/Partial to a live acceptance test only once its primitives exist.
