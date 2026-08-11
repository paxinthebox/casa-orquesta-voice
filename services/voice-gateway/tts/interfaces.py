"""
TTS provider protocol — Phase 2.2.

Any concrete TTS adapter (ElevenLabs Flash, Azure Neural, Cartesia Sonic,
self-hosted Coqui XTTS) implements this interface so the pipeline can swap
providers via env without touching the orchestrator.
"""
from __future__ import annotations

from typing import Awaitable, Callable, Protocol, runtime_checkable

# Each call delivers one chunk of synthesized audio (PCM 16 kHz mono).
OnAudio = Callable[[bytes], Awaitable[None]]


@runtime_checkable
class TTSProvider(Protocol):
    """
    Provider-agnostic streaming TTS.

    Lifecycle:
        client = await Provider.open(voice, on_audio)
        await client.feed("Hola, ")     # buffered by the chunker
        await client.feed("encontré tres opciones.")  # punctuation → flush
        await client.flush()             # barge-in: discard pending audio
        await client.close()
    """

    name: str
    voice: str
    language: str
    is_open: bool

    async def feed(self, text: str) -> None:
        """Add text to the synthesis queue. The phrase chunker decides when
        to flush to the wire (punctuation or 6+ words)."""
        ...

    async def flush(self) -> None:
        """Discard any pending text + audio. Used for barge-in."""
        ...

    async def close(self) -> None:
        """Tear down the connection."""
        ...
