"""
BargeInDetector — Phase 2.3.

Lightweight RMS-based VAD for detecting user speech *during* TTS
playback. Used by the voice-gateway to fire a `cancel` signal within
200 ms of the user starting to talk over the assistant.

Why RMS instead of a real VAD (Silero, WebRTC, etc.)?
  * Zero dependencies and zero startup time.
  * The barge-in trigger doesn't need to distinguish speech from
    noise perfectly — Deepgram is still doing real transcription in
    parallel and will reject noise as `interim_results=true` flicker.
  * If the user is loud enough to talk over the bot, RMS will catch it.

Future upgrade path: swap the `_is_voiced()` core with a tiny ONNX
Silero model. The public surface (`feed`, `reset`) stays the same.

Wire assumptions:
  * Inbound frames are PCM 16-bit signed little-endian, 16 kHz mono,
    20 ms windows (= 640 bytes / 320 samples). Opus frames are
    decoded upstream before they reach the detector.
  * If a frame has the wrong width or shape we treat it as silence
    (best-effort) rather than crashing the session.
"""
from __future__ import annotations

import math
import os
import struct
from typing import Optional


DEFAULT_RMS_THRESHOLD = int(os.getenv("BARGE_RMS_THRESHOLD", "650"))
DEFAULT_WINDOW_MS = int(os.getenv("BARGE_WINDOW_MS", "200"))
DEFAULT_FRAME_MS = int(os.getenv("BARGE_FRAME_MS", "20"))
DEFAULT_COOLDOWN_MS = int(os.getenv("BARGE_COOLDOWN_MS", "800"))


class BargeInDetector:
    """
    Per-session detector. Call `feed(frame, playing=True)` for every
    inbound audio frame; returns True the first time the running
    "voiced frames within the window" count crosses the trigger.

    Lifecycle:
        bi = BargeInDetector()
        bi.notify_tts_start()           # on first TTS audio frame
        for frame in mic_frames:
            if bi.feed(frame, playing=tts_is_playing):
                # cancel the in-flight reply
        bi.notify_tts_end()              # on TTS flush() or end of utterance
    """

    def __init__(
        self,
        *,
        window_ms: int = DEFAULT_WINDOW_MS,
        frame_ms: int = DEFAULT_FRAME_MS,
        rms_threshold: int = DEFAULT_RMS_THRESHOLD,
        cooldown_ms: int = DEFAULT_COOLDOWN_MS,
        # Voiced-frame ratio inside the window that triggers cancel.
        trigger_ratio: float = 0.6,
    ):
        self.window_ms = window_ms
        self.frame_ms = max(1, frame_ms)
        self.rms_threshold = rms_threshold
        self.cooldown_ms = cooldown_ms
        self.trigger_ratio = trigger_ratio

        self._window_size = max(1, window_ms // self.frame_ms)
        self._voiced_count = 0
        # Circular buffer of 1/0 voiced flags.
        self._ring: list[int] = []
        self._cooldown_remaining_ms = 0
        self._tts_playing = False

    # ----- public API -----
    def notify_tts_start(self) -> None:
        self._tts_playing = True
        self._reset_window()

    def notify_tts_end(self) -> None:
        self._tts_playing = False
        self._reset_window()
        # Short cooldown so the bot's own audio echo doesn't immediately
        # re-trigger barge-in on devices without acoustic echo cancellation.
        self._cooldown_remaining_ms = self.cooldown_ms

    def reset(self) -> None:
        self._tts_playing = False
        self._cooldown_remaining_ms = 0
        self._reset_window()

    def feed(self, frame: bytes, *, playing: Optional[bool] = None) -> bool:
        """
        Push one inbound audio frame. Returns True if barge-in is
        triggered by *this* frame (not by previous frames).

        `playing` overrides the internal `_tts_playing` flag if provided.
        Callers may prefer to keep their own playback bookkeeping.
        """
        is_playing = self._tts_playing if playing is None else playing

        # Cooldown burns down regardless — barge-in is suppressed in this period.
        if self._cooldown_remaining_ms > 0:
            self._cooldown_remaining_ms -= self.frame_ms
            return False

        if not is_playing:
            # When the assistant is silent the STT pipeline handles speech.
            self._reset_window()
            return False

        voiced = 1 if self._is_voiced(frame) else 0
        self._ring.append(voiced)
        self._voiced_count += voiced
        if len(self._ring) > self._window_size:
            self._voiced_count -= self._ring.pop(0)

        trigger = (
            len(self._ring) >= self._window_size
            and (self._voiced_count / self._window_size) >= self.trigger_ratio
        )
        if trigger:
            # Reset so we don't keep firing on every subsequent frame.
            self._cooldown_remaining_ms = self.cooldown_ms
            self._reset_window()
        return trigger

    # ----- internals -----
    def _reset_window(self) -> None:
        self._ring.clear()
        self._voiced_count = 0

    def _is_voiced(self, frame: bytes) -> bool:
        rms = _rms_int16(frame)
        return rms >= self.rms_threshold


def _rms_int16(frame: bytes) -> float:
    """Compute RMS of a PCM 16-bit signed little-endian frame.

    Defensive: returns 0.0 for empty / mis-aligned frames rather than
    raising, since corrupted frames shouldn't kill the voice session.
    """
    if not frame:
        return 0.0
    # 2 bytes per sample. Truncate to a multiple of 2.
    n = len(frame) // 2
    if n == 0:
        return 0.0
    samples = struct.unpack_from("<%dh" % n, frame, 0)
    # Avoid pulling numpy in. Loop is fine for 320-sample frames.
    sq = 0
    for s in samples:
        sq += s * s
    return math.sqrt(sq / n)
