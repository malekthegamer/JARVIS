# JARVIS

A Windows personal AI assistant — swappable AI brain, voice, and skills, with a
local web dashboard. Everything runs on your machine; no paid API is ever required.

## Quick start

```bash
pip install -r requirements.txt
copy .env.example .env          # then paste your Gemini key into .env
```

Run it any of these ways:

| Command | What it does |
|---|---|
| `python main.py --text` | Text REPL — the always-works fallback (loads no audio code) |
| `python main.py` | Voice mode: listen → think → speak |
| `python main.py --wake` | Voice mode gated on the wake word ("jarvis") |
| `python server.py` | Web dashboard at http://localhost:8000 |
| `python tray.py` | Desktop window + system-tray icon |
| `python tools/list_mics.py` | List mics and flag your real one |

## Swapping providers

Open the dashboard → **Settings**. Change the brain (Gemini / OpenAI / Claude /
Ollama / OpenRouter), voice output (edge-tts / ElevenLabs / pyttsx3), or voice
input (Google / local Whisper / OpenAI Whisper), enter any keys, and hit
**Save & Apply**. Changes take effect live — no restart. Keys are stored in
`.env` and shown masked (last 4 chars) afterward.

**Defaults out of the box (all free):** Gemini brain · Google Web Speech input ·
edge-tts output. Enter an ElevenLabs key and it upgrades the voice automatically.

## Notes for this machine

- **Microphone:** auto-detected (prefers `Microphone (Realtek(R) Audio)`), skipping
  Voicemeeter/WO Mic/Voicemod virtuals. Pin one in Settings if needed.
- **Local Whisper on the RTX 5060 (Blackwell):** runs `float16` on CUDA (int8
  crashes sm_120) and falls back to CPU automatically if the CUDA libs don't load.
- **Optional extras:** Tesseract binary (screen OCR), `playwright install chromium`
  (browser automation), Gmail `credentials.json` (email), ffmpeg (Whisper file
  decoding). Each degrades to a clear message if absent — nothing crashes.

## Safety

- Every destructive action (delete, close app, power, send email, side-effecting
  commands, form submits) asks for confirmation first — in the dashboard or terminal.
- JARVIS never speaks unprompted. Background alerts (low battery, due reminders,
  price targets) appear as silent dashboard notifications only.
- The server binds to localhost only.
