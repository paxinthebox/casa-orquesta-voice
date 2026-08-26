"""
Google Gemini TTS adapter — Phase 2.2 (provider swap per Dario).

Buffers text during a turn (feed/flush), then synthesizes the whole buffer
on finish() via the Interactions API with streaming:
    await client.aio.interactions.create(
        model="gemini-3.1-flash-tts-preview",
        input=<text>,
        response_format={"type": "audio"},
        generation_config={"speech_config": [{"voice": <voice>}]},
        stream=True,
    )

Streaming shape (live-verified 2026-08-26 from the running Fly machine,
google-genai 2.20.0): events arrive as a pydantic union keyed on
`event_type`:
    interaction.created → interaction.status_update → step.start
    → step.delta (xN) → step.stop → interaction.completed
Audio arrives on `step.delta` events where `delta.type == "audio"`;
`delta.data` is base64 PCM 16-bit mono, `delta.sample_rate` is 24000.

The pipeline expects PCM 16 kHz mono (tts/interfaces.py), so each delta is
resampled 24 kHz → 16 kHz with numpy (phase-continuous across chunks).

Auth: GOOGLE_CLOUD_API_KEY — the Gemini API accepts the same key the town
already uses (live-verified 2026-08-26; note this does NOT extend to the
Speech-to-Text gRPC API, which requires OAuth2/service-account).

Spanish is auto-detected from the text; no per-request language config.

Barge-in: flush() bumps a generation epoch; audio deltas from a stale
generation are discarded.
"""
from __future__ import annotations

import base64
import math
import os
from typing import Optional

from .interfaces import OnAudio, TTSProvider

GOOGLE_CLOUD_API_KEY = os.getenv("GOOGLE_CLOUD_API_KEY", "")
GEMINI_TTS_MODEL = os.getenv("GEMINI_TTS_MODEL", "gemini-3.1-flash-tts-preview")
GEMINI_TTS_VOICE = os.getenv("GEMINI_TTS_VOICE", "Kore")
GEMINI_TTS_SAMPLE_RATE = 24000   # verified live: delta.sample_rate == 24000
PIPELINE_SAMPLE_RATE = 16000     # tts/interfaces.py contract: PCM 16 kHz mono


def _resolve_voice(voice: Optional[str]) -> str:
    """Priority: provider-prefixed TTS_VOICE (``google:Kore``) → bare voice
    string → GEMINI_TTS_VOICE env → "Kore"."""
    if voice:
        raw = voice.split(":", 1)[-1].strip()
        if raw:
            return raw
    env_voice = os.getenv("GEMINI_TTS_VOICE", "").strip()
    if env_voice:
        return env_voice
    return "Kore"


def _error_text(ev) -> str:
    """Best-effort extraction of a message from an `error` event."""
    err = getattr(ev, "error", None)
    if err is None:
        return "Google returned an error event"
    msg = getattr(err, "message", None) or getattr(err, "detail", None)
    if not msg:
        try:
            msg = str(err)
        except Exception:
            msg = repr(err)
    return str(msg)


class _PcmResampler:
    """int16 mono resampler, phase-continuous across chunk boundaries.

    Output index j maps to source position j/ratio. Each push() emits
    exactly the indices [self._j, floor(total_samples*ratio)) — a globally
    continuous index, so however the input is chunked the concatenated
    output equals the single-shot resample of the whole stream. Linear
    interpolation is fine for speech at 24k→16k. Odd-length byte inputs are
    tolerated (a trailing lone byte is carried to the next push).
    """

    def __init__(self, src_rate: int, dst_rate: int):
        import numpy as np  # already in requirements

        self._np = np
        self._ratio = dst_rate / float(src_rate)
        self._tail = np.zeros(0, dtype=np.float32)
        self._start = 0  # global source sample position of tail[0]
        self._j = 0      # output samples emitted so far (global index)
        self._odd = b""  # leftover odd byte awaiting its pair

    def push(self, pcm: bytes) -> bytes:
        np = self._np
        pcm = self._odd + pcm
        self._odd = pcm[-1:] if len(pcm) % 2 else b""
        pcm = pcm[: len(pcm) // 2 * 2]
        if not pcm:
            return b""
        if self._ratio == 1.0:
            self._tail = np.zeros(0, dtype=np.float32)
            return pcm
        x = np.frombuffer(pcm, dtype=np.int16).astype(np.float32)
        src = np.concatenate((self._tail, x)) if self._tail.size else x
        total = self._start + src.size
        j_end = math.floor(total * self._ratio)
        n_out = j_end - self._j
        if n_out <= 0:
            self._tail, self._start = src, self._start
            return b""
        j0, j1 = self._j, self._j + n_out
        positions = (np.arange(j0, j1, dtype=np.float64)) / self._ratio
        xs = np.arange(src.size, dtype=np.float64) + self._start
        out = np.interp(positions, xs, src)
        # The next chunk's first output position is j1/ratio; keep one sample
        # before it so boundary interpolation stays exact.
        keep_abs = math.floor(j1 / self._ratio) - 1
        keep = min(src.size, max(0, keep_abs - self._start))
        self._tail = src[keep:]
        self._start += keep
        self._j = j1
        out16 = np.clip(out, -32768.0, 32767.0).astype(np.int16)
        return out16.tobytes()


class GoogleTTS:
    """Gemini 3.1 Flash TTS — buffer-then-synthesize, streaming audio out."""

    name = "google-gemini-tts"
    language = "es-MX"  # auto-detected; annotation only

    def __init__(
        self,
        voice: str,
        on_audio: OnAudio,
        *,
        api_key: str,
        model: str,
        client: object,
    ):
        self.voice = voice
        self._on_audio = on_audio
        self._api_key = api_key
        self._model = model
        self._client = client
        self._buffer = ""
        self._closed = False
        self._epoch = 0
        self._bytes_generated = 0
        self._last_error: Optional[str] = None

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
        client: Optional[object] = None,
    ) -> "GoogleTTS":
        if on_audio is None:
            async def on_audio(_: bytes) -> None: ...
        key = api_key or GOOGLE_CLOUD_API_KEY
        if not key and client is None:
            raise RuntimeError(
                "GOOGLE_CLOUD_API_KEY not set; refusing to open a real connection. "
                "Tests should pass a `client`."
            )
        if client is None:
            from google import genai

            client = genai.Client(api_key=key)
        v = _resolve_voice(voice)
        print(f"[tts.google] voice={v} model={GEMINI_TTS_MODEL}")
        return cls(v, on_audio, api_key=key, model=GEMINI_TTS_MODEL, client=client)

    # ---- provider surface ----
    async def feed(self, text: str) -> None:
        if text:
            self._buffer += text

    async def flush(self) -> None:
        """Barge-in: drop pending text; stale in-flight audio is discarded
        via the generation epoch."""
        self._buffer = ""
        self._epoch += 1

    async def finish(self) -> None:
        """Synthesize the buffered text; stream audio through the resampler."""
        if self._closed:
            return
        text = self._buffer.strip()
        self._buffer = ""
        if not text:
            return
        await self._synthesize(text)

    async def close(self) -> None:
        self._closed = True
        self._buffer = ""
        self._epoch += 1

    # ---- internals ----
    async def _synthesize(self, text: str) -> None:
        print(
            f"[tts.google] streaming chars={len(text)} voice={self.voice} "
            f"model={self._model}"
        )
        before = self._bytes_generated
        epoch = self._epoch
        resampler = _PcmResampler(GEMINI_TTS_SAMPLE_RATE, PIPELINE_SAMPLE_RATE)
        stream = None
        try:
            stream = await self._client.aio.interactions.create(
                model=self._model,
                input=text,
                response_format={"type": "audio"},
                generation_config={"speech_config": [{"voice": self.voice}]},
                stream=True,
            )
            async for ev in stream:
                if self._closed or self._epoch != epoch:
                    break  # barge-in: stop consuming
                et = getattr(ev, "event_type", None)
                if et == "step.delta":
                    delta = getattr(ev, "delta", None)
                    if delta is None or getattr(delta, "type", None) != "audio":
                        continue
                    data = getattr(delta, "data", "") or ""
                    if not data:
                        continue
                    try:
                        pcm = base64.b64decode(data)
                    except Exception:
                        continue
                    sr = getattr(delta, "sample_rate", None) or GEMINI_TTS_SAMPLE_RATE
                    if sr != GEMINI_TTS_SAMPLE_RATE:
                        resampler = _PcmResampler(sr, PIPELINE_SAMPLE_RATE)
                    out = resampler.push(pcm)
                    if out:
                        self._bytes_generated += len(out)
                        await self._on_audio(out)
                elif et == "error":
                    self._last_error = _error_text(ev)
                    print(f"[tts.google] generation error: {self._last_error!r}")
                    break
        except Exception as e:
            self._last_error = str(e)
            print(f"[tts.google] error: {self._last_error!r}")
        finally:
            if stream is not None:
                try:
                    stream.close()
                except Exception:
                    pass
        added = self._bytes_generated - before
        if added:
            print(f"[tts.google] delivered {added} bytes @16kHz")
        elif not self._last_error:
            self._last_error = "Google returned no audio"
            print(f"[tts.google] {self._last_error}")


# Protocol conformance for type checkers / factory return.
assert isinstance(GoogleTTS, type)