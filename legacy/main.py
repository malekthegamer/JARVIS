"""JARVIS terminal entry point.

    python main.py --text     text-only REPL (imports NO audio code — the safety net)
    python main.py            voice mode: listen -> think -> speak
    python main.py --wake     voice mode gated on the wake word ("jarvis")

The web dashboard is a separate process:  python server.py
"""
from __future__ import annotations

import argparse
import sys

if sys.stdout and hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")


def text_mode() -> None:
    """Text REPL. Deliberately imports nothing audio-related."""
    from brain import jarvis_brain
    from core import skill_registry

    skill_registry.load_all()
    print("JARVIS text mode — type 'exit' to quit.")
    while True:
        try:
            user = input("\nYou: ").strip()
        except (EOFError, KeyboardInterrupt):
            print("\nGoodbye, sir.")
            return
        if not user:
            continue
        if user.lower() in ("exit", "quit", "bye"):
            print("JARVIS: Goodbye, sir.")
            return
        try:
            print(f"JARVIS: {jarvis_brain.think(user)}")
        except Exception as exc:  # never-crash contract
            print(f"JARVIS: Something went wrong: {exc}")


def voice_mode(wake: bool = False) -> None:
    from brain import jarvis_brain
    from core import skill_registry
    from core.settings_store import settings
    from voice.voice_manager import voice_manager

    skill_registry.load_all()
    wake_word = str(settings.get("wake_word", "jarvis")).lower()
    if wake:
        print(f"JARVIS wake-word mode — say '{wake_word}' followed by your request. Ctrl+C to quit.")
    else:
        print("JARVIS voice mode — speak when ready. Ctrl+C to quit.")

    while True:
        try:
            print("\n🎤 Listening…", flush=True)
            heard = voice_manager.listen()
            if not heard:
                continue
            print(f"You said: {heard}")

            if wake:
                lower = heard.lower()
                if wake_word not in lower:
                    continue  # not addressed to us — stay silent (reactive-only)
                heard = heard[lower.find(wake_word) + len(wake_word):].strip(" ,.!?") or "yes?"

            print("🧠 Thinking…", flush=True)
            reply = jarvis_brain.think(heard)
            print(f"JARVIS: {reply}")
            print("🔊 Speaking…", flush=True)
            voice_manager.speak(reply)
        except KeyboardInterrupt:
            print("\nGoodbye, sir.")
            return
        except Exception as exc:  # never-crash contract: log and keep listening
            print(f"  [loop] recovered from: {exc}")


def main() -> None:
    parser = argparse.ArgumentParser(description="JARVIS personal AI assistant")
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--text", action="store_true", help="text-only REPL (no audio code loaded)")
    mode.add_argument("--wake", action="store_true", help="voice mode gated on the wake word")
    args = parser.parse_args()

    if args.text:
        text_mode()
    else:
        voice_mode(wake=args.wake)


if __name__ == "__main__":
    sys.exit(main())
