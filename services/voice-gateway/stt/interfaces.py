"""
STT provider protocol — Phase 2.1.

Any concrete STT adapter (Deepgram, AssemblyAI, AWS Transcribe, Whisper,
self-hosted Wav2Vec) implements this interface so the voice-gateway pipeline
can swap providers via env without touching the orchestrator.
"""
from __future__ import annotations

from typing import Awaitable, Callable, Protocol, runtime_checkable

# Callback signatures the gateway provides to the STT adapter.
OnPartial = Callable[[str], Awaitable[None]]
OnFinal = Callable[[str], Awaitable[None]]


@runtime_checkable
class STTProvider(Protocol):
    """
    Provider-agnostic STT interface.

    Lifecycle:
        client = await Provider.open(language, on_partial, on_final)
        # ...mic frames arrive...
        await client.start_utterance()      # PTT pressed
        await client.send_audio(frame_bytes)
        await client.send_audio(frame_bytes)
        ...
        await client.end_utterance()        # PTT released (or VAD detects)
        await client.close()                # session ends
    """

    name: str               # adapter identifier, e.g. "deepgram-nova-2"
    language: str           # BCP-47, e.g. "es-MX"
    is_open: bool

    async def send_audio(self, frame: bytes) -> None:
        """Push one audio frame (Opus or PCM 16 kHz mono, 20 ms windows)."""
        ...

    async def start_utterance(self) -> None:
        """Signal the start of a single utterance (PTT-press)."""
        ...

    async def end_utterance(self) -> None:
        """Force end-of-speech regardless of VAD (PTT-release)."""
        ...

    async def close(self) -> None:
        """Tear down the WS connection."""
        ...
