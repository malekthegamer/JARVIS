# J.A.R.V.I.S

A voice-driven agent that actually operates a Windows 11 PC — launches and
drives apps, reads the screen, browses the web, manages files, and remembers
things — behind a reactive HUD, with a confirmation gate in front of anything
destructive.

It is **not a chatbot that answers.** It runs a perceive → plan → act → verify →
correct loop: an LLM composes 40 tested primitives, every action is tiered
AUTO / CONFIRM / BLOCKED, and each one lands in an encrypted audit log.

---

## ⚠ Read this before you run it

This software **can and will act on your computer.** With default settings it
can run shell commands, delete and overwrite files anywhere on the machine
(to the Recycle Bin), send email as you, and drive a Chrome that is logged into
your accounts.

The protection is not a sandbox — it is the **CONFIRM gate**: every irreversible
action pauses and shows you the *literal* thing it is about to do (the exact
shell command, the exact resolved file path, the full email) and waits for you
to approve. Ground truth, never the model's paraphrase of it.

That gate is genuinely good, but understand what you are trusting:

- An LLM chooses the actions. Models make mistakes. **Read the confirmation.**
- The catastrophic-path denylist (`C:\Windows`, drive roots, …) is a **backstop,
  not the boundary** — it is deliberately, provably bypassable by obfuscation.
  The gate is the boundary.
- JARVIS reads untrusted web pages. Page content is wrapped as data, never
  instructions, but prompt injection is a real and unsolved class of attack.
- Anything you switch off is genuinely off (see **Turning capabilities off**).

Run it on your own machine, on an account you control. If that trade isn't for
you, that is a completely reasonable conclusion.

---

## Requirements

- **Windows 11** (hard requirement — DPAPI encryption, UI Automation, Recycle
  Bin, and the win32 window layer are all Windows-specific)
- **Python 3.12**
- A free **Gemini API key** → https://aistudio.google.com/apikey

## Setup — the easy way

1. Download this repo (green **Code → Download ZIP**, then unzip — or
   `git clone https://github.com/malekthegamer/JARVIS.git`)
2. **Double-click `install.bat`**
3. Double-click the **J.A.R.V.I.S** shortcut it puts on your Desktop

The installer builds a local `.venv` (your system Python is left alone),
installs everything, downloads Chromium and a small speech model (~500 MB
total), and creates the shortcut. It is safe to re-run — it skips whatever is
already done, and stops loudly rather than leaving a half-install.

On first launch JARVIS asks for a **free Gemini API key** in the HUD itself —
paste it in and you're running. Get one at
[aistudio.google.com/apikey](https://aistudio.google.com/apikey).

## Setup — by hand

If you'd rather not run a script:

```powershell
pip install -r requirements.txt
playwright install chromium              # browser automation
python -m jarvis.core.embedder --setup   # local memory model (~90 MB)
python -c "import openwakeword.utils as u; u.download_models(['hey_jarvis'])"  # "hey jarvis" wake word (optional)
copy .env.example .env                   # then paste your Gemini key into .env
```

## Run it

| Command | What it does |
|---|---|
| `python run.py` | Serves the HUD at http://127.0.0.1:8000 and opens it |
| `python run.py --no-open` | Same, without opening a browser |
| `python -m jarvis.tray` | Server **plus** a system-tray icon (Open HUD / wake word / Quit) |

In the HUD: type a message, or click the orb / press **Space** and speak.
The gear icon opens **Settings**; the 🗎 icon opens the **audit log**.

## What it can actually do

40 registered primitives, each individually tested:

- **Apps & windows** — `launch_app` (Start Menu, desktop shortcuts, Steam and
  Epic libraries), `close_window`, `read_ui_tree`
- **Input** — `click` (single/double/right), `type_text`, `press_keys`, `scroll`
- **Files (sandboxed)** — `write_file`, `read_file`, `delete_file`,
  `search_files` inside `data/agent_files/` only
- **Files (whole PC)** — `list_directory`, `read_path`, `write_path`,
  `move_path`, `rename_path`, `copy_path`, `delete_path`, `create_shortcut`
- **Web** — `browse_navigate`, `read_page`, `browse_click`, `browse_fill`,
  `browse_key`, `web_search`
- **System** — volume, mute, brightness, media keys, Do Not Disturb, clipboard
- **Comms** — `send_email` (Gmail API, send-only scope)
- **Shell** — `run_shell`
- **Memory** — remembers facts you explicitly ask it to keep, encrypted at rest

Plus: multi-step chains with a visible plan, a vision fallback for icon-only
controls, dry-run mode (`dry run: …`), and `undo_last_action`.

## Turning capabilities off

Settings → **Capabilities & safety**. Each switch both hides the verb from the
model *and* refuses it if called directly:

| Switch | Disables |
|---|---|
| `shell.enabled` | `run_shell` |
| `fs.enabled` | all 8 whole-PC file verbs |
| `web.enabled` | all 6 browser verbs |
| `search.enabled` | `web_search` |
| `email.enabled` | `send_email` |

Two extra opt-ins, **off by default**: `web.profile_mode="real"` (drive your
real logged-in Chrome) and, beneath it, `web.allow_actions` (let it click and
type on your sites).

## Optional extras

- **Email** — put a Google OAuth client at `data/email/credentials.json`, then
  run `python -m jarvis.primitives.email` once to authorise.
- **Better voice** — add an `ELEVENLABS_API_KEY` to `.env`.
- **Local speech-to-text** — `faster-whisper` is installed; pick it in Settings.
- **Wake word** — "hey jarvis", local and keyless; enable it in Settings.

Everything optional degrades to a clear message. Nothing crashes if it's absent.

## Security notes

- The server binds to **127.0.0.1 only** and is never exposed to the network.
- The **WebSocket and every state-changing request** are guarded by an
  `Origin` check, so a random website you visit cannot connect to the HUD and
  drive the agent (run commands, change settings, act). Ordinary **page loads
  (GET) are allowed** — a malicious site still cannot *read* any response,
  because the browser's same-origin policy blocks that (the server sends no
  `Access-Control-Allow-Origin`). Requests with **no** `Origin` are allowed on
  purpose — browsers always send it, so the browser attack surface is closed,
  while local tooling (tests, harnesses, curl) keeps working; a local
  non-browser process already has code execution, so gating it would buy
  nothing.
- Long-term memory, the audit log, and the Gmail token are encrypted at rest
  with **Windows DPAPI** — bound to your Windows user account.
- `.env`, `data/` and the browser profile are git-ignored and never committed.

## Testing

```powershell
python -m pytest tests/ -q
```

754 tests. Heads-up before running the full suite: it drives a **real desktop**
(launching and closing Notepad and Chrome — keep the desktop idle for ~8 min),
briefly toggles Do Not Disturb, and the live tests need a `GEMINI_API_KEY`.
The live email test sends mail to whatever `TEST_SELF_EMAIL` is set to.

Some tests currently write real local state (the real memory store, the real
`data/agent_files`) and run `taskkill /IM notepad.exe` — so close unsaved
Notepad windows first. This is a known rough edge, tracked in
`SESSION_HANDOFF.md` §5.

The free Gemini tier rate-limits clustered live calls, so a handful of
live-brain tests may fail in a full run and pass when re-run individually.
That's throttling, not a regression.

## How this was built

`JARVIS_Spec_v1.md` is what was built and why. `CLAUDE.md` and `HARNESS.md` are
the engineering discipline it was built under (plan → build → tests-first →
verify, with measurement before optimisation). `SESSION_HANDOFF.md` and
`REGRESSION_CHECKPOINT.md` are the running build log — including the honest
list of known gaps and limitations.

## License

MIT — see [LICENSE](LICENSE).
