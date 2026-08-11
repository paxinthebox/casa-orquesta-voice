"""
Deepgram STT adapter — Phase 2.1.

Streaming wrapper around Deepgram's Live Transcription WebSocket. Implements
the STTProvider protocol so the voice-gateway pipeline can swap providers
via env.

Wire format (Deepgram's, abbreviated):
  → outbound binary frames: Opus/PCM 16 kHz mono, 20 ms windows
  → outbound text:          {"type":"KeepAlive"} | {"type":"CloseStream"}
  ← inbound JSON:           {"type":"Results", "is_final":bool,
                             "channel":{"alternatives":[{"transcript":str}]}}
                            {"type":"SpeechStarted"}
                            {"type":"UtteranceEnd"}

Endpoints used:
  wss://api.deepgram.com/v1/listen?model=nova-2&language=es-MX&punctuate=true
        &smart_format=true&interim_results=true&endpointing=300&vad_events=true
        &numerals=true

The class is testable: open() takes an injectable `connector` so the test
suite can substitute a fake WS without hitting the real network.

Lifecycle:
  client = await DeepgramSTT.open(language, on_partial, on_final)
  await client.start_utterance()
  await client.send_audio(frame)
  await client.end_utterance()
  await client.close()
"""
from __future__ import annotations

import asyncio
import json
import os
from typing import Any, Awaitable, Callable, Optional, Protocol

from .interfaces import OnFinal, OnPartial, STTProvider

DEEPGRAM_API_KEY = os.getenv("DEEPGRAM_API_KEY", "")
DEEPGRAM_MODEL = os.getenv("DEEPGRAM_MODEL", "nova-2")
DEEPGRAM_ENDPOINTING_MS = int(os.getenv("DEEPGRAM_ENDPOINTING_MS", "300"))


# ----------------- WebSocket abstraction -----------------
class _WSConn(Protocol):
    """Minimal duck-typed WS we depend on. Both `websockets` and the test
    fake implement this surface."""

    async def send(self, data: Any) -> None: ...
    async def recv(self) -> Any: ...
    async def close(self, code: int = 1000) -> None: ...
    @property
    def closed(self) -> bool: ...


WSConnector = Callable[[str, dict], Awaitable[_WSConn]]


async def _default_connector(url: str, headers: dict) -> _WSConn:
    """Default to the `websockets` library. Imported lazily so tests can
    pass a `connector` without pulling in the dep."""
    import websockets  # type: ignore

    return await websockets.connect(url, extra_headers=headers)  # type: ignore


def _build_url(language: str) -> str:
    qs = (
        f"model={DEEPGRAM_MODEL}"
        f"&language={language}"
        "&punctuate=true"
        "&smart_format=true"
        "&numerals=true"
        "&interim_results=true"
        f"&endpointing={DEEPGRAM_ENDPOINTING_MS}"
        "&vad_events=true"
        "&encoding=linear16"
        "&channels=1"
        "&sample_rate=16000"
    )
    return f"wss://api.deepgram.com/v1/listen?{qs}"


# ----------------- The adapter -----------------
class DeepgramSTT(STTProvider):
    """Streaming Deepgram client implementing STTProvider."""

    name = "deepgram-nova-2"

    def __init__(
        self,
        language: str,
        on_partial: OnPartial,
        on_final: OnFinal,
        conn: _WSConn,
        reader_task: asyncio.Task,
    ):
        self.language = language
        self._on_partial = on_partial
        self._on_final = on_final
        self._conn = conn
        self._reader_task = reader_task
        self._closed = False
        # Last partial we emitted, so we can suppress duplicates.
        self._last_partial = ""

    # ---- public API ----
    @classmethod
    async def open(
        cls,
        language: str = "es-MX",
        on_partial: Optional[OnPartial] = None,
        on_final: Optional[OnFinal] = None,
        *,
        api_key: Optional[str] = None,
        connector: WSConnector = _default_connector,
    ) -> "DeepgramSTT":
        """Open the WS, kick off the reader task, return a ready client."""
        if on_partial is None:
            async def on_partial(_: str) -> None: ...
        if on_final is None:
            async def on_final(_: str) -> None: ...

        key = api_key or DEEPGRAM_API_KEY
        if not key and connector is _default_connector:
            raise RuntimeError(
                "DEEPGRAM_API_KEY not set; refusing to open a real connection. "
                "Tests should pass a `connector`."
            )

        url = _build_url(language)
        headers = {"Authorization": f"Token {key}"}
        conn = await connector(url, headers)

        # Create the client first so the reader can call its callbacks.
        client = cls.__new__(cls)
        client.language = language
        client._on_partial = on_partial
        client._on_final = on_final
        client._conn = conn
        client._closed = False
        client._last_partial = ""
        client._reader_task = asyncio.create_task(client._reader_loop())
        return client

    @property
    def is_open(self) -> bool:
        return not self._closed and not getattr(self._conn, "closed", False)

    async def send_audio(self, frame: bytes) -> None:
        if self._closed:
            return
        await self._conn.send(frame)

    async def start_utterance(self) -> None:
        # Deepgram has no explicit utterance start — the WS is always-on and
        # endpointing is automatic. We reset the dedupe cache here.
        self._last_partial = ""

    async def end_utterance(self) -> None:
        """Force end-of-stream so Deepgram emits a final transcript."""
        if self._closed:
            return
        await self._conn.send(json.dumps({"type": "CloseStream"}))

    async def close(self, *, code: int = 1000) -> None:
        if self._closed:
            return
        self._closed = True
        try:
            await self._conn.close(code=code)
        except Exception:
            pass
        if not self._reader_task.done():
            self._reader_task.cancel()
            try:
                await self._reader_task
            except (asyncio.CancelledError, Exception):
                pass

    # ---- reader loop ----
    async def _reader_loop(self) -> None:
        """Read inbound messages from Deepgram; dispatch to callbacks."""
        try:
            while not self._closed:
                msg = await self._conn.recv()
                if isinstance(msg, bytes):
                    # Deepgram doesn't send binary on this path; ignore.
                    continue
                if not msg:
                    continue
                try:
                    payload = json.loads(msg)
                except (ValueError, TypeError):
                    continue
                await self._dispatch(payload)
        except asyncio.CancelledError:
            raise
        except Exception as e:
            print(f"[stt.deepgram] reader loop error: {e!r}")
            self._closed = True

    async def _dispatch(self, payload: dict) -> None:
        kind = payload.get("type")
        if kind == "Results":
            transcript = self._extract_transcript(payload)
            if not transcript:
                return
            is_final = bool(payload.get("is_final"))
            if is_final:
                await self._on_final(transcript)
                self._last_partial = ""
            else:
                if transcript != self._last_partial:
                    self._last_partial = transcript
                    await self._on_partial(transcript)
        elif kind == "UtteranceEnd":
            # VAD-detected end-of-speech; the next Results will be is_final.
            pass
        elif kind == "Metadata":
            pass
        # SpeechStarted, Error, etc. are no-ops for now.

    @staticmethod
    def _extract_transcript(payload: dict) -> str:
        channel = payload.get("channel") or {}
        alts = channel.get("alternatives") or []
        if not alts:
            return ""
        return (alts[0].get("transcript") or "").strip()
