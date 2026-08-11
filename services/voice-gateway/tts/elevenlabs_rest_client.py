"""
ElevenLabs HTTP streaming TTS — reliable fallback when WS stream-input is flaky.

Buffers text during a turn, then POSTs to /v1/text-to-speech/{voice_id}/stream
once on finish(). Same OnAudio contract as ElevenLabsTTS (PCM or MP3 bytes).
"""
from __future__ import annotations

import json
from typing import Optional

from .elevenlabs_client import (
    ELEVENLABS_API_KEY,
    ELEVENLABS_MODEL,
    ELEVENLABS_OUTPUT_FORMAT,
    _humanize_error,
    resolve_voice_id,
)
from .interfaces import OnAudio
from .phrase_chunker import PhraseChunker


class ElevenLabsRestTTS:
    """REST-only ElevenLabs adapter — no WebSocket."""

    name = "elevenlabs-rest"
    language = "es-MX"

    def __init__(
        self,
        voice: str,
        on_audio: OnAudio,
        *,
        api_key: str,
    ):
        self.voice = voice
        self._on_audio = on_audio
        self._api_key = api_key
        self._chunker = PhraseChunker()
        self._buffer = ""
        self._closed = False
        self._last_error: Optional[str] = None
        self._bytes_generated = 0

    @property
    def last_error(self) -> Optional[str]:
        return self._last_error

    @property
    def bytes_generated(self) -> int:
        return self._bytes_generated

    @property
    def is_open(self) -> bool:
        return not self._closed

    @classmethod
    async def open(
        cls,
        voice: Optional[str] = None,
        on_audio: Optional[OnAudio] = None,
        *,
        api_key: Optional[str] = None,
    ) -> "ElevenLabsRestTTS":
        if on_audio is None:
            async def on_audio(_: bytes) -> None: ...

        key = api_key or ELEVENLABS_API_KEY
        if not key:
            raise RuntimeError(
                "ELEVENLABS_API_KEY not set; refusing to open a real connection."
            )
        vid = resolve_voice_id(voice)
        print(
            f"[tts.elevenlabs-rest] voice_id={vid} model={ELEVENLABS_MODEL} "
            f"format={ELEVENLABS_OUTPUT_FORMAT}"
        )
        return cls(vid, on_audio, api_key=key)

    async def feed(self, text: str) -> None:
        if text:
            self._buffer += text

    async def flush(self) -> None:
        self._chunker.reset()
        self._buffer = ""

    async def finish(self) -> None:
        """Synthesize buffered text via HTTP stream."""
        if self._closed:
            return
        text = self._buffer.strip()
        self._buffer = ""
        if not text:
            return
        await self._stream_text(text)

    async def close(self, *, code: int = 1000) -> None:
        self._closed = True
        self._buffer = ""

    async def _stream_text(self, text: str) -> None:
        url = (
            f"https://api.elevenlabs.io/v1/text-to-speech/{self.voice}/stream"
            f"?output_format={ELEVENLABS_OUTPUT_FORMAT}"
        )
        body = {
            "text": text,
            "model_id": ELEVENLABS_MODEL,
            "voice_settings": {"stability": 0.5, "similarity_boost": 0.75},
        }
        print(f"[tts.elevenlabs-rest] streaming chars={len(text)}")
        before = self._bytes_generated
        try:
            import httpx  # type: ignore

            async with httpx.AsyncClient(timeout=60.0) as client:
                async with client.stream(
                    "POST",
                    url,
                    headers={"xi-api-key": self._api_key, "Content-Type": "application/json"},
                    json=body,
                ) as resp:
                    if resp.status_code != 200:
                        raw = (await resp.aread()).decode("utf-8", errors="replace")
                        try:
                            detail = json.loads(raw).get("detail", raw)
                            if isinstance(detail, dict):
                                raw = detail.get("message") or str(detail)
                        except (ValueError, TypeError):
                            pass
                        self._last_error = _humanize_error(str(raw))
                        print(
                            f"[tts.elevenlabs-rest] failed ({resp.status_code}): "
                            f"{self._last_error!r}"
                        )
                        return
                    async for chunk in resp.aiter_bytes(chunk_size=4096):
                        if chunk:
                            self._bytes_generated += len(chunk)
                            await self._on_audio(chunk)
        except Exception as e:
            self._last_error = _humanize_error(str(e))
            print(f"[tts.elevenlabs-rest] error: {self._last_error!r}")
        added = self._bytes_generated - before
        if added:
            print(f"[tts.elevenlabs-rest] delivered {added} bytes")
        elif not self._last_error:
            self._last_error = "ElevenLabs returned no audio"
            print(f"[tts.elevenlabs-rest] {self._last_error}")


# Protocol conformance for type checkers / factory return.
assert isinstance(ElevenLabsRestTTS, type)
