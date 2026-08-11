"""
Azure Neural Spanish TTS adapter — Phase 2.2.

Lower-cost alternative to ElevenLabs Flash. Same TTSProvider interface, so
a single env flip (TTS_PROVIDER=azure) swaps the wire format.

Wire format:
  endpoint:  POST https://{REGION}.tts.speech.microsoft.com/cognitiveservices/v1
  headers:   Ocp-Apim-Subscription-Key, X-Microsoft-OutputFormat,
             Content-Type: application/ssml+xml
  body:      SSML with the chosen voice
  response:  raw audio (PCM 16 kHz mono) — streamed

Azure doesn't expose a WS streaming session, so we issue one request per
phrase chunk. This raises first-frame latency slightly compared to
ElevenLabs Flash but keeps cost ~5-10× lower for the same volume.
"""
from __future__ import annotations

import asyncio
import os
from typing import Any, Callable, Optional, Protocol

from .interfaces import OnAudio, TTSProvider
from .phrase_chunker import PhraseChunker

AZURE_TTS_KEY = os.getenv("AZURE_TTS_KEY", "")
AZURE_TTS_REGION = os.getenv("AZURE_TTS_REGION", "westus")
AZURE_TTS_VOICE = os.getenv("AZURE_TTS_VOICE", "es-MX-DaliaNeural")
AZURE_OUTPUT_FORMAT = os.getenv(
    "AZURE_OUTPUT_FORMAT", "raw-16khz-16bit-mono-pcm")


# Injectable HTTP client for tests.
class _HTTPClient(Protocol):
    async def post(self, url: str, *, headers: dict, content: bytes) -> "_HTTPResponse": ...


class _HTTPResponse(Protocol):
    status_code: int
    async def aiter_bytes(self, chunk_size: int = 4096) -> Any: ...


HTTPFactory = Callable[[], _HTTPClient]


def _default_http_client() -> _HTTPClient:
    import httpx  # type: ignore

    class _Wrapper:
        def __init__(self):
            self._client = httpx.AsyncClient(timeout=15.0)

        async def post(self, url, *, headers, content):
            return await self._client.post(url, headers=headers, content=content)

        async def aclose(self):
            await self._client.aclose()

    return _Wrapper()


def _build_url() -> str:
    return f"https://{AZURE_TTS_REGION}.tts.speech.microsoft.com/cognitiveservices/v1"


def _ssml(text: str, voice: str) -> bytes:
    safe = (text.replace("&", "&amp;")
                 .replace("<", "&lt;")
                 .replace(">", "&gt;"))
    body = (
        f"<speak version='1.0' xml:lang='es-MX'>"
        f"<voice name='{voice}'>{safe}</voice></speak>"
    )
    return body.encode("utf-8")


class AzureTTS(TTSProvider):
    name = "azure-neural-spanish"
    language = "es-MX"

    def __init__(self, voice: str, on_audio: OnAudio,
                 http: _HTTPClient):
        self.voice = voice
        self._on_audio = on_audio
        self._http = http
        self._chunker = PhraseChunker()
        self._closed = False
        self._epoch = 0
        self._inflight: list[asyncio.Task] = []

    @classmethod
    async def open(
        cls,
        voice: Optional[str] = None,
        on_audio: Optional[OnAudio] = None,
        *,
        api_key: Optional[str] = None,
        http_factory: HTTPFactory = _default_http_client,
    ) -> "AzureTTS":
        if on_audio is None:
            async def on_audio(_: bytes) -> None: ...
        key = api_key or AZURE_TTS_KEY
        if not key and http_factory is _default_http_client:
            raise RuntimeError(
                "AZURE_TTS_KEY not set; refusing to open a real connection."
            )
        v = voice or AZURE_TTS_VOICE
        client = cls(v, on_audio, http_factory())
        client._api_key = key  # type: ignore[attr-defined]
        return client

    @property
    def is_open(self) -> bool:
        return not self._closed

    async def feed(self, text: str) -> None:
        for chunk in self._chunker.feed(text):
            self._inflight.append(asyncio.create_task(self._synth(chunk, self._epoch)))

    async def flush(self) -> None:
        self._chunker.reset()
        self._epoch += 1
        # Cancel pending synthesis tasks from the current generation.
        for t in self._inflight:
            if not t.done():
                t.cancel()
        self._inflight = []

    async def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        # Drain remaining inflight tasks briefly.
        for t in self._inflight:
            if not t.done():
                t.cancel()
                try:
                    await t
                except (asyncio.CancelledError, Exception):
                    pass
        self._inflight = []
        if hasattr(self._http, "aclose"):
            try:
                await self._http.aclose()  # type: ignore[attr-defined]
            except Exception:
                pass

    # ---- internals ----
    async def _synth(self, text: str, started_epoch: int) -> None:
        try:
            headers = {
                "Ocp-Apim-Subscription-Key": getattr(self, "_api_key", ""),
                "X-Microsoft-OutputFormat": AZURE_OUTPUT_FORMAT,
                "Content-Type": "application/ssml+xml",
                "User-Agent": "casa-orquesta-voice/0.1",
            }
            resp = await self._http.post(
                _build_url(),
                headers=headers,
                content=_ssml(text, self.voice),
            )
            if getattr(resp, "status_code", 0) != 200:
                print(f"[tts.azure] non-200: {resp.status_code}")
                return
            async for chunk in resp.aiter_bytes(chunk_size=4096):
                if self._epoch != started_epoch:
                    return  # barge-in / flush happened
                if chunk:
                    await self._on_audio(chunk)
        except asyncio.CancelledError:
            raise
        except Exception as e:
            print(f"[tts.azure] synth error: {e!r}")
