"""
Google Cloud STT adapter — Chirp 3, Speech-to-Text V2 streaming.

Implements the STTProvider protocol so the voice-gateway pipeline can swap
providers via env (STT_PROVIDER=google).

Wire format (Speech-to-Text V2, gRPC):
  → requests:  StreamingRecognizeRequest(streaming_config=...) first, then
               StreamingRecognizeRequest(audio=<pcm frame>) per 20 ms frame.
               The request stream is CLOSED to force a final result (the
               server flushes endpointing when the client stops sending).
  ← responses: StreamingRecognizeResponse with results[]; each result has
               alternatives[].transcript and is_final.

Audio format: PCM 16 kHz mono 16-bit (matches the existing pipeline; the
mobile client sends 20 ms frames).

Auth (first match wins):
  1. GOOGLE_CLOUD_SERVICE_ACCOUNT_FILE — path to a service-account JSON.
     REQUIRED for production: the Speech API does not accept API keys
     (live-verified 2026-08-26: 401 CREDENTIALS_MISSING on both
     Recognize and StreamingRecognize with an x-goog-api-key).
  2. GOOGLE_CLOUD_API_KEY — passed via client_options={"api_key": ...}
     (per spec; the transport accepts it, but the API currently rejects
     it — kept so the spec path works if support lands).
  3. Application Default Credentials (fallback).

Threading: google-cloud-speech's gRPC client is synchronous. A dedicated
worker thread runs the request generator + response iterator. Audio frames
enter via a thread-safe queue; results come back via a second queue that an
async reader task drains on the event loop.

Lifecycle (same as DeepgramSTT):
  client = await GoogleSTT.open(language, on_partial, on_final)
  await client.start_utterance()
  await client.send_audio(frame)
  await client.end_utterance()   # closes request stream → final → done
  await client.close()
"""
from __future__ import annotations

import asyncio
import os
import queue
import threading
from typing import Any, Optional

from .interfaces import OnFinal, OnPartial, STTProvider

GOOGLE_STT_MODEL = os.getenv("GOOGLE_STT_MODEL", "chirp_3")
GOOGLE_CLOUD_API_KEY = os.getenv("GOOGLE_CLOUD_API_KEY", "")
GOOGLE_CLOUD_SERVICE_ACCOUNT_FILE = os.getenv("GOOGLE_CLOUD_SERVICE_ACCOUNT_FILE", "")
GOOGLE_CLOUD_PROJECT = os.getenv("GOOGLE_CLOUD_PROJECT", "")
SAMPLE_RATE_HZ = 16000
AUDIO_CHANNEL_COUNT = 1
# Sentinel pushed on the audio queue to close the request stream.
_END = object()


def _build_client(api_key: Optional[str] = None) -> tuple[Any, str]:
    """Build the SpeechClient per the auth chain. Returns (client, mode).

    Auth chain (first match wins):
      1. GOOGLE_CLOUD_SERVICE_ACCOUNT_FILE as a file path (if the path exists).
      2. GOOGLE_CLOUD_SERVICE_ACCOUNT_FILE as inline JSON (if it parses as JSON).
         This is how Fly secrets deliver it — the env var holds the JSON string,
         not a path to a file on disk.
      3. GOOGLE_CLOUD_API_KEY via client_options (currently rejected by the API,
         kept for forward-compat).
      4. Application Default Credentials (fallback).
    """
    from google.cloud import speech_v2

    sa_value = GOOGLE_CLOUD_SERVICE_ACCOUNT_FILE
    if sa_value:
        from google.oauth2 import service_account

        # Try as a file path first (local dev).
        if os.path.exists(sa_value):
            creds = service_account.Credentials.from_service_account_file(sa_value)
            return speech_v2.SpeechClient(credentials=creds), "service-account-file"
        # Try as inline JSON (Fly secrets deliver the JSON string directly).
        try:
            import json as _json
            info = _json.loads(sa_value)
            if isinstance(info, dict) and "private_key" in info:
                creds = service_account.Credentials.from_service_account_info(info)
                return speech_v2.SpeechClient(credentials=creds), "service-account-inline"
        except (ValueError, TypeError):
            pass  # Not JSON; fall through to other auth methods.
    key = api_key or GOOGLE_CLOUD_API_KEY
    if key:
        return speech_v2.SpeechClient(client_options={"api_key": key}), "api-key"
    return speech_v2.SpeechClient(), "adc"


def _streaming_config(language: str):
    """V2 config: model + language + explicit PCM 16 kHz mono decoding."""
    from google.cloud import speech_v2

    return speech_v2.StreamingRecognitionConfig(
        config=speech_v2.RecognitionConfig(
            model=GOOGLE_STT_MODEL,
            language_codes=[language],
            explicit_decoding_config=speech_v2.ExplicitDecodingConfig(
                encoding=speech_v2.ExplicitDecodingConfig.AudioEncoding.LINEAR16,
                sample_rate_hertz=SAMPLE_RATE_HZ,
                audio_channel_count=AUDIO_CHANNEL_COUNT,
            ),
        ),
        streaming_features=speech_v2.StreamingRecognitionFeatures(
            interim_results=True
        ),
    )


class GoogleSTT(STTProvider):
    """Streaming Chirp 3 client implementing STTProvider."""

    name = "google-chirp-3"

    def __init__(
        self,
        language: str,
        on_partial: OnPartial,
        on_final: OnFinal,
        client: Any,
        auth_mode: str,
        in_q: "queue.Queue",
        out_q: "queue.Queue",
        worker: threading.Thread,
        reader_task: asyncio.Task,
    ):
        self.language = language
        self._on_partial = on_partial
        self._on_final = on_final
        self._client = client
        self.auth_mode = auth_mode
        self._in_q = in_q
        self._out_q = out_q
        self._worker = worker
        self._reader_task = reader_task
        self._closed = False
        self._stream_done = False
        self._end_sent = False
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
        client: Any = None,
    ) -> "GoogleSTT":
        """Open the gRPC streaming session, kick off the worker + reader,
        return a ready client. Pass `client` in tests to inject a fake."""
        if on_partial is None:
            async def on_partial(_: str) -> None: ...
        if on_final is None:
            async def on_final(_: str) -> None: ...

        if client is None:
            client, auth_mode = _build_client(api_key)
        else:
            auth_mode = "injected"

        loop = asyncio.get_running_loop()
        in_q: "queue.Queue" = queue.Queue()
        out_q: "queue.Queue" = queue.Queue()

        cfg = _streaming_config(language)
        recognizer_path = (
            f"projects/{GOOGLE_CLOUD_PROJECT}/locations/global/recognizers/_"
            if GOOGLE_CLOUD_PROJECT else ""
        )

        def _worker_run() -> None:
            from google.cloud import speech_v2

            def requests():
                first = speech_v2.StreamingRecognizeRequest(
                    streaming_config=cfg
                )
                if recognizer_path:
                    first.recognizer = recognizer_path
                yield first
                while True:
                    frame = in_q.get()
                    if frame is _END:
                        break
                    yield speech_v2.StreamingRecognizeRequest(audio=frame)

            try:
                for resp in client.streaming_recognize(requests=requests()):
                    out_q.put(("result", resp))
                out_q.put(("done", None))
            except BaseException as e:  # noqa: BLE001 — surface to the reader
                out_q.put(("error", e))

        worker = threading.Thread(
            target=_worker_run, name="google-stt-worker", daemon=True
        )
        worker.start()

        instance = cls(
            language, on_partial, on_final, client, auth_mode,
            in_q, out_q, worker,
            None,  # reader task; set below once the instance exists
        )
        # The reader task needs the instance to exist first.
        instance._reader_task = asyncio.create_task(instance._reader_loop())
        return instance

    @property
    def is_open(self) -> bool:
        return (
            not self._closed
            and not self._stream_done
            and self._worker.is_alive()
        )

    async def send_audio(self, frame: bytes) -> None:
        if self._closed or self._stream_done or not frame:
            return
        self._in_q.put(frame)

    async def start_utterance(self) -> None:
        # V2 has no explicit utterance-start; endpointing is automatic.
        # Reset the dedupe cache, mirroring DeepgramSTT.
        self._last_partial = ""

    async def end_utterance(self) -> None:
        """Close the request stream so the server flushes a final result.
        After the stream ends, is_open flips False and the pipeline reopens
        a fresh client for the next utterance (same contract as Deepgram)."""
        if self._closed or self._end_sent:
            return
        self._end_sent = True
        self._in_q.put(_END)

    async def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        if not self._end_sent:
            self._end_sent = True
            self._in_q.put(_END)
        if self._reader_task is not None and not self._reader_task.done():
            self._reader_task.cancel()
            try:
                await self._reader_task
            except (asyncio.CancelledError, Exception):
                pass
        if self._worker.is_alive():
            self._worker.join(timeout=5.0)

    # ---- reader loop (event loop side) ----
    async def _reader_loop(self) -> None:
        """Drain worker results; dispatch to callbacks."""
        try:
            while not self._closed:
                kind, payload = await asyncio.to_thread(self._out_q.get)
                if kind == "result":
                    await self._dispatch(payload)
                elif kind == "error":
                    print(f"[stt.google] stream error: {payload!r}")
                    self._stream_done = True
                    break
                else:  # done — normal stream end
                    self._stream_done = True
                    break
        except asyncio.CancelledError:
            raise
        except Exception as e:
            print(f"[stt.google] reader loop error: {e!r}")
            self._closed = True

    async def _dispatch(self, resp: Any) -> None:
        if not getattr(resp, "results", None):
            return
        res = resp.results[0]
        alts = getattr(res, "alternatives", None)
        if not alts:
            return
        txt = (getattr(alts[0], "transcript", "") or "").strip()
        if not txt:
            return
        if getattr(res, "is_final", False):
            self._last_partial = ""
            await self._on_final(txt)
        elif txt != self._last_partial:
            self._last_partial = txt
            await self._on_partial(txt)