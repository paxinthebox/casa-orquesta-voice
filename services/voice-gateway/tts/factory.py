"""
TTS provider factory — Phase 2.2.

Selects the active TTS adapter based on `TTS_PROVIDER` env:
    TTS_PROVIDER=elevenlabs (default) → ElevenLabsTTS
    TTS_PROVIDER=azure                → AzureTTS
    TTS_PROVIDER=google               → GoogleTTS (Gemini 3.1 Flash TTS)

Both implement TTSProvider, so callers don't change.
"""
from __future__ import annotations

import os
from typing import Optional

from .interfaces import OnAudio, TTSProvider
from .elevenlabs_client import ElevenLabsTTS
from .azure_client import AzureTTS

TTS_PROVIDER = os.getenv("TTS_PROVIDER", "elevenlabs").lower()
# WS stream-input is flaky in stage; REST stream is the default transport.
ELEVENLABS_TRANSPORT = os.getenv("ELEVENLABS_TRANSPORT", "rest").lower()


async def open_tts(
    voice: Optional[str] = None,
    on_audio: Optional[OnAudio] = None,
) -> TTSProvider:
    """Open the active TTS provider per `TTS_PROVIDER`."""
    if TTS_PROVIDER == "google":
        from .google_tts import GoogleTTS

        return await GoogleTTS.open(voice=voice, on_audio=on_audio)
    if TTS_PROVIDER == "azure":
        return await AzureTTS.open(voice=voice, on_audio=on_audio)
    if ELEVENLABS_TRANSPORT == "rest":
        from .elevenlabs_rest_client import ElevenLabsRestTTS

        return await ElevenLabsRestTTS.open(voice=voice, on_audio=on_audio)
    return await ElevenLabsTTS.open(voice=voice, on_audio=on_audio)
