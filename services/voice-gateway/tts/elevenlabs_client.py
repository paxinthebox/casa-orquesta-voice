"""
ElevenLabs Flash streaming TTS adapter — Phase 2.2.

Wraps ElevenLabs' v1 stream-input WebSocket. Flash v2.5 is the low-latency
multilingual model (~75 ms first frame in their benchmarks; we target
< 300 ms first frame including network).

Wire format:
  endpoint: wss://api.elevenlabs.io/v1/text-to-speech/{voice_id}/stream-input
            ?model_id=eleven_flash_v2_5&output_format=pcm_16000
  headers:  xi-api-key: <KEY>
  → outbound JSON:  {"text": "phrase ", "try_trigger_generation": true,
                     "voice_settings": {...}}
                    {"text": ""}                  -- flush / end stream
  ← inbound JSON:   {"audio": "<base64 pcm>", "isFinal": false}
                    {"audio": null, "isFinal": true}

The adapter feeds the chunker; when a chunk is ready it ships it over the
WS. Inbound audio frames are decoded and pushed to `on_audio()`.
"""
from __future__ import annotations

import asyncio
import base64
import json
import os
import time
from typing import Any, Awaitable, Callable, Optional, Protocol

from .interfaces import OnAudio, TTSProvider
from .phrase_chunker import PhraseChunker

ELEVENLABS_API_KEY = os.getenv("ELEVENLABS_API_KEY", "")
ELEVENLABS_MODEL = os.getenv("ELEVENLABS_MODEL", "eleven_flash_v2_5")
ELEVENLABS_OUTPUT_FORMAT = os.getenv("ELEVENLABS_OUTPUT_FORMAT", "mp3_44100_128")
DEFAULT_VOICE_ID = os.getenv("ELEVENLABS_VOICE_ID", "21m00Tcm4TlvDq8ikWAM")


def _humanize_error(raw: str) -> str:
    """Map ElevenLabs API errors to actionable es-MX messages."""
    lower = raw.lower()
    if "paid_plan_required" in lower or "library voices" in lower:
        return (
            "Esta voz de ElevenLabs (Voice Library) requiere plan de pago "
            "para usarse por API. Actualiza tu plan en elevenlabs.io o usa "
            "una voz clonada propia en ELEVENLABS_VOICE_ID."
        )
    if "quota" in lower or "character limit" in lower:
        return "Se agotó la cuota de caracteres de ElevenLabs para hoy."
    return raw


def _extract_error(payload: dict) -> Optional[str]:
    err = payload.get("error") or payload.get("message")
    detail = payload.get("detail")
    if isinstance(detail, dict):
        err = detail.get("message") or err
    elif detail:
        err = str(detail)
    if err:
        return _humanize_error(str(err))
    return None


def resolve_voice_id(voice: Optional[str]) -> str:
    """Map env-style names to an ElevenLabs voice ID.

    Priority:
      1. ``ELEVENLABS_VOICE_ID`` env (dashboard selection — primary knob)
      2. Raw 20-char ID in ``TTS_VOICE`` (``elevenlabs:abc…`` or bare ID)
      3. Module default (legacy Rachel fallback)
    """
    env_id = (os.getenv("ELEVENLABS_VOICE_ID") or "").strip()
    if env_id:
        return env_id
    if voice:
        raw = voice.split(":", 1)[-1].strip()
        if len(raw) == 20 and raw.isalnum():
            return raw
    if voice:
        return DEFAULT_VOICE_ID
    return DEFAULT_VOICE_ID


class _WSConn(Protocol):
    async def send(self, data: Any) -> None: ...
    async def recv(self) -> Any: ...
    async def close(self, code: int = 1000) -> None: ...
    @property
    def closed(self) -> bool: ...


WSConnector = Callable[[str, dict], Awaitable[_WSConn]]


async def _default_connector(url: str, headers: dict) -> _WSConn:
    import websockets  # type: ignore
    return await websockets.connect(url, extra_headers=headers)  # type: ignore


def _build_url(voice_id: str) -> str:
    qs = f"model_id={ELEVENLABS_MODEL}&output_format={ELEVENLABS_OUTPUT_FORMAT}"
    return f"wss://api.elevenlabs.io/v1/text-to-speech/{voice_id}/stream-input?{qs}"


class ElevenLabsTTS(TTSProvider):
    name = "elevenlabs-flash"
    language = "es-MX"  # voice-determined; this is the default annotation

    def __init__(
        self,
        voice: str,
        on_audio: OnAudio,
        conn: _WSConn,
    ):
        self.voice = voice
        self._on_audio = on_audio
        self._conn = conn
        self._chunker = PhraseChunker()
        self._closed = False
        self._reader_task: asyncio.Task | None = None
        # Generation epoch — bumped on flush() so late-arriving audio frames
        # from before the barge-in can be discarded.
        self._epoch = 0
        self._warned_empty = False
        self._bytes_generated = 0
        self._utterance_text = ""
        self._last_error: Optional[str] = None
        self._api_key = ""
        self._sent_first = False

    @property
    def last_error(self) -> Optional[str]:
        return self._last_error

    @property
    def bytes_generated(self) -> int:
        return self._bytes_generated

    @classmethod
    async def open(
        cls,
        voice: Optional[str] = None,
        on_audio: Optional[OnAudio] = None,
        *,
        api_key: Optional[str] = None,
        connector: WSConnector = _default_connector,
    ) -> "ElevenLabsTTS":
        if on_audio is None:
            async def on_audio(_: bytes) -> None: ...
        key = api_key or ELEVENLABS_API_KEY
        if not key and connector is _default_connector:
            raise RuntimeError(
                "ELEVENLABS_API_KEY not set; refusing to open a real connection."
            )
        vid = resolve_voice_id(voice)
        print(f"[tts.elevenlabs] opening voice_id={vid} model={ELEVENLABS_MODEL}")
        url = _build_url(vid)
        headers = {"xi-api-key": key}
        conn = await connector(url, headers)
        client = cls(vid, on_audio, conn)
        client._api_key = key
        client._reader_task = asyncio.create_task(client._reader_loop())
        return client

    @property
    def is_open(self) -> bool:
        return not self._closed and not getattr(self._conn, "closed", False)

    async def feed(self, text: str) -> None:
        if text:
            self._utterance_text += text
        chunks = self._chunker.feed(text)
        for c in chunks:
            first = not self._sent_first
            await self._send_text(c, first=first)
            self._sent_first = True

    async def flush(self) -> None:
        """Barge-in: drop pending text + bump epoch so in-flight audio is ignored."""
        self._chunker.reset()
        self._sent_first = False
        self._epoch += 1
        if self.is_open:
            # Empty-text frame tells ElevenLabs to flush this generation.
            try:
                await self._conn.send(json.dumps({"text": ""}))
            except Exception:
                pass

    async def finish(self) -> None:
        """End of utterance: flush phrase chunker + signal generation complete."""
        if self._closed:
            return
        remainder = self._chunker.flush()
        if remainder:
            first = not self._sent_first
            await self._send_text(remainder, first=first)
            self._sent_first = True
        if self.is_open:
            try:
                await self._conn.send(json.dumps({"text": ""}))
            except Exception:
                pass
        # Wait for all phrase-sized generations to finish streaming audio.
        await self._wait_audio_idle(idle_seconds=0.65, timeout=15.0)
        if self._bytes_generated == 0 and self._utterance_text.strip():
            await self._rest_stream_fallback(self._utterance_text.strip())
        self._utterance_text = ""

    async def _wait_audio_idle(
        self,
        *,
        idle_seconds: float = 0.65,
        timeout: float = 15.0,
    ) -> None:
        """Block until audio bytes stop arriving (between phrases or at end)."""
        deadline = time.monotonic() + timeout
        last_count = self._bytes_generated
        quiet_since = time.monotonic()
        while time.monotonic() < deadline:
            await asyncio.sleep(0.05)
            cur = self._bytes_generated
            now = time.monotonic()
            if cur != last_count:
                last_count = cur
                quiet_since = now
                continue
            if cur > 0 and (now - quiet_since) >= idle_seconds:
                return
            if cur == 0 and (now - quiet_since) >= 0.4:
                return

    async def close(self, *, code: int = 1000) -> None:
        if self._closed:
            return
        self._closed = True
        try:
            # Send the closing empty text + a CloseConnection signal.
            await self._conn.send(json.dumps({"text": ""}))
        except Exception:
            pass
        try:
            await self._conn.close(code=code)
        except Exception:
            pass
        if self._reader_task and not self._reader_task.done():
            self._reader_task.cancel()
            try:
                await self._reader_task
            except (asyncio.CancelledError, Exception):
                pass

    # ---- internals ----
    async def _send_text(self, text: str, *, first: bool = False) -> None:
        if self._closed:
            return
        payload: dict = {"text": text}
        if first:
            # Per ElevenLabs docs, voice_settings + first-frame nudging.
            payload["voice_settings"] = {
                "stability": 0.5,
                "similarity_boost": 0.75,
            }
            payload["try_trigger_generation"] = True
        await self._conn.send(json.dumps(payload, ensure_ascii=False))

    async def _reader_loop(self) -> None:
        epoch_at_recv = self._epoch
        try:
            while not self._closed:
                msg = await self._conn.recv()
                if not msg:
                    continue
                try:
                    payload = json.loads(msg) if isinstance(msg, str) else None
                except (ValueError, TypeError):
                    continue
                if not payload:
                    continue
                audio_b64 = payload.get("audio")
                if audio_b64:
                    # Skip frames from a stale generation (post-flush).
                    if self._epoch != epoch_at_recv:
                        epoch_at_recv = self._epoch
                        continue
                    try:
                        pcm = base64.b64decode(audio_b64)
                    except Exception:
                        pcm = b""
                    if pcm:
                        await self._emit_audio(pcm)
                if payload.get("isFinal"):
                    if not audio_b64:
                        err = _extract_error(payload)
                        if err:
                            self._last_error = err
                            print(f"[tts.elevenlabs] generation error: {err!r}")
                        elif not getattr(self, "_warned_empty", False):
                            self._warned_empty = True
                            print(
                                "[tts.elevenlabs] WS stream ended with no audio "
                                "(will try REST fallback if text was fed)"
                            )
                    epoch_at_recv = self._epoch
        except asyncio.CancelledError:
            raise
        except Exception as e:
            # Normal WS close after generation — not a fatal error.
            if type(e).__name__ in (
                "ConnectionClosedOK", "ConnectionClosed", "ConnectionClosedError",
            ):
                pass
            else:
                print(f"[tts.elevenlabs] reader loop error: {e!r}")
                self._closed = True

    async def _emit_audio(self, pcm: bytes) -> None:
        self._bytes_generated += len(pcm)
        await self._on_audio(pcm)

    async def _rest_stream_fallback(self, text: str) -> None:
        """HTTP stream when the WS stream-input session yields no PCM."""
        key = self._api_key or ELEVENLABS_API_KEY
        if not key:
            return
        url = (
            f"https://api.elevenlabs.io/v1/text-to-speech/{self.voice}/stream"
            f"?output_format={ELEVENLABS_OUTPUT_FORMAT}"
        )
        body = {"text": text, "model_id": ELEVENLABS_MODEL}
        print(f"[tts.elevenlabs] REST fallback voice_id={self.voice} chars={len(text)}")
        before = self._bytes_generated
        try:
            import httpx  # type: ignore

            async with httpx.AsyncClient(timeout=30.0) as client:
                async with client.stream(
                    "POST",
                    url,
                    headers={"xi-api-key": key, "Content-Type": "application/json"},
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
                            f"[tts.elevenlabs] REST fallback failed "
                            f"({resp.status_code}): {self._last_error!r}"
                        )
                        return
                    async for chunk in resp.aiter_bytes(chunk_size=4096):
                        if chunk:
                            await self._emit_audio(chunk)
        except Exception as e:
            self._last_error = _humanize_error(str(e))
            print(f"[tts.elevenlabs] REST fallback error: {e!r}")
        added = self._bytes_generated - before
        if added:
            print(f"[tts.elevenlabs] REST fallback delivered {added} bytes")
