"""Shared audio playback for TTS providers that produce bytes (edge-tts, ElevenLabs).

Uses pygame's mixer (pure pip install, no ffmpeg needed for mp3 playback).
Lazy-imported so text-only runs never load audio libraries.
"""
from __future__ import annotations

import io
import threading

_lock = threading.Lock()
_stop_flag = threading.Event()


def play_bytes(data: bytes, block: bool = True) -> None:
    """Play mp3/wav/ogg bytes. Serialized so overlapping speech can't stack."""
    import pygame  # lazy

    with _lock:
        _stop_flag.clear()
        if not pygame.mixer.get_init():
            pygame.mixer.init()
        pygame.mixer.music.load(io.BytesIO(data))
        pygame.mixer.music.play()
        if block:
            clock = pygame.time.Clock()
            while pygame.mixer.music.get_busy():
                if _stop_flag.is_set():
                    pygame.mixer.music.stop()
                    break
                clock.tick(20)


def stop() -> None:
    """Interrupt any current playback (used by dashboard 'stop speaking')."""
    _stop_flag.set()
