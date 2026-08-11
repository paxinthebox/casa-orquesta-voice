"""
P2.3 latency benchmark — synthetic end-to-end measurement.

Measures the loop:
    STT-final → orchestrator first text_delta → first TTS audio byte → mobile

Each iteration is a fresh VoiceSession bound to fake STT/orch/TTS clients
that introduce *configurable* per-stage latencies so the benchmark can be
run hermetically (no httpx, no Deepgram, no ElevenLabs) and the budget
gates remain meaningful.

Defaults model the P50 budget from ARCHITECTURE.md:
    + 200 ms  orchestrator first-token (Haiku-routed)
    + 300 ms  TTS first audio frame (ElevenLabs Flash)

Gate (CI default):
    P50  ≤ 1500 ms
    P95  ≤ 2500 ms
    max  ≤ 5000 ms

Override via env:
    VOICE_LATENCY_ITERATIONS   default 50
    VOICE_LATENCY_ORCH_MS      default 200
    VOICE_LATENCY_TTS_MS       default 300
    VOICE_LATENCY_JITTER_MS    default 80
    VOICE_LATENCY_GATE         "strict" (default) | "report" (never fail)

Exit codes:
    0  budget met
    1  budget breached and gate is strict
"""
from __future__ import annotations

import asyncio
import math
import os
import random
import statistics
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
VG = os.path.normpath(os.path.join(HERE, "..", ".."))
sys.path.insert(0, VG)

from pipeline import VoiceSession                                    # noqa: E402

ITERATIONS = int(os.getenv("VOICE_LATENCY_ITERATIONS", "50"))
ORCH_MS = int(os.getenv("VOICE_LATENCY_ORCH_MS", "200"))
TTS_MS = int(os.getenv("VOICE_LATENCY_TTS_MS", "300"))
JITTER_MS = int(os.getenv("VOICE_LATENCY_JITTER_MS", "80"))
GATE = os.getenv("VOICE_LATENCY_GATE", "strict").lower()

P50_BUDGET_MS = 1500
P95_BUDGET_MS = 2500
MAX_BUDGET_MS = 5000


# ----------------------------------------------------------------------
# Fakes (mirror tests/test_pipeline.py shapes but with timing knobs)
# ----------------------------------------------------------------------
class _FakeSTT:
    def __init__(self, on_partial, on_final):
        self.on_partial = on_partial
        self.on_final = on_final

    async def send_audio(self, b): ...
    async def start_utterance(self): ...
    async def end_utterance(self): ...
    async def close(self): ...


class _TimedTTS:
    """Simulates TTS first-frame latency on the *first* feed() per reply."""

    def __init__(self, on_audio, *, first_frame_ms: int, jitter_ms: int):
        self.on_audio = on_audio
        self._fff = first_frame_ms
        self._jitter = jitter_ms
        self._first = True

    async def feed(self, text: str) -> None:
        if self._first:
            self._first = False
            jitter = random.uniform(0, self._jitter / 1000.0)
            await asyncio.sleep(self._fff / 1000.0 + jitter)
        await self.on_audio(b"AUDIO:" + text.encode("utf-8"))

    async def flush(self) -> None:
        self._first = True

    async def close(self) -> None: ...


class _TimedOrch:
    """Simulates orchestrator first-token latency on stream()."""

    def __init__(self, *, first_token_ms: int, jitter_ms: int,
                 reply_text: str = "Hola, encontré tres opciones."):
        self._ftt = first_token_ms
        self._jitter = jitter_ms
        self._reply = reply_text

    def stream(self, message: str, state=None):
        ftt = self._ftt
        jitter = self._jitter

        async def _gen():
            jit = random.uniform(0, jitter / 1000.0)
            await asyncio.sleep(ftt / 1000.0 + jit)
            yield {"kind": "text_delta", "agent": "realestate_agent",
                   "detail": {"text": self._reply}, "run_id": "R"}
            yield {"kind": "run_end", "agent": "realestate_agent",
                   "detail": {"run_id": "R"}, "run_id": "R"}
        return _gen()

    async def close(self): ...


# ----------------------------------------------------------------------
# One iteration
# ----------------------------------------------------------------------
async def _one_iteration() -> float:
    """Returns measured latency (ms) from STT-final to first audio byte."""
    first_audio_at: list[float] = []
    final_at: list[float] = []

    async def send_text(_): ...

    async def send_bytes(_b):
        if not first_audio_at:
            first_audio_at.append(time.perf_counter())

    stt_holder = {}

    async def stt_factory(on_p, on_f):
        s = _FakeSTT(on_p, on_f); stt_holder["s"] = s; return s

    async def tts_factory(on_a):
        return _TimedTTS(on_a, first_frame_ms=TTS_MS, jitter_ms=JITTER_MS)

    orch = _TimedOrch(first_token_ms=ORCH_MS, jitter_ms=JITTER_MS)
    session = VoiceSession.create(
        session_id="S-PERF", tenant_id="t", user_id="u",
        stt_factory=stt_factory, tts_factory=tts_factory, orch=orch,
        send_text=send_text, send_bytes=send_bytes,
    )
    await session.open()
    await session._ensure_stt()
    stt = stt_holder["s"]
    # Time zero: STT final.
    t0 = time.perf_counter()
    final_at.append(t0)
    await stt.on_final("busco depa en roma norte")
    if session._reply_task:
        await session._reply_task
    await session.close()
    if not first_audio_at:
        return float("inf")
    return (first_audio_at[0] - final_at[0]) * 1000.0


async def _main_async() -> int:
    print("=" * 70)
    print("  Casa·Orquesta voice — synthetic latency benchmark (P2.3)")
    print("=" * 70)
    print(f"  iterations={ITERATIONS}  orch_first_token={ORCH_MS}ms  "
          f"tts_first_frame={TTS_MS}ms  jitter≤{JITTER_MS}ms")
    print()
    samples: list[float] = []
    for i in range(ITERATIONS):
        ms = await _one_iteration()
        samples.append(ms)
        if (i + 1) % max(1, ITERATIONS // 10) == 0:
            print(f"    [{i + 1}/{ITERATIONS}] last={ms:.1f}ms  "
                  f"running_p50={statistics.median(samples):.1f}ms")

    samples_sorted = sorted(samples)
    p50 = statistics.median(samples_sorted)
    # P95: linear interpolation between two nearest ranks.
    p95_rank = 0.95 * (len(samples_sorted) - 1)
    lo = int(math.floor(p95_rank))
    hi = int(math.ceil(p95_rank))
    if lo == hi:
        p95 = samples_sorted[lo]
    else:
        frac = p95_rank - lo
        p95 = samples_sorted[lo] * (1 - frac) + samples_sorted[hi] * frac
    p99 = samples_sorted[int(0.99 * (len(samples_sorted) - 1))]
    max_ = samples_sorted[-1]

    print()
    print("-" * 70)
    print(f"  P50  : {p50:>7.1f} ms   (budget {P50_BUDGET_MS} ms)")
    print(f"  P95  : {p95:>7.1f} ms   (budget {P95_BUDGET_MS} ms)")
    print(f"  P99  : {p99:>7.1f} ms")
    print(f"  max  : {max_:>7.1f} ms   (budget {MAX_BUDGET_MS} ms)")
    print("-" * 70)

    breaches = []
    if p50 > P50_BUDGET_MS:
        breaches.append(f"P50 {p50:.1f}ms > {P50_BUDGET_MS}ms")
    if p95 > P95_BUDGET_MS:
        breaches.append(f"P95 {p95:.1f}ms > {P95_BUDGET_MS}ms")
    if max_ > MAX_BUDGET_MS:
        breaches.append(f"max {max_:.1f}ms > {MAX_BUDGET_MS}ms")

    if breaches:
        print("  ❌ budget breached:")
        for b in breaches:
            print(f"     - {b}")
        if GATE == "strict":
            return 1
        else:
            print("  (gate=report → exit 0 anyway)")
            return 0
    print("  ✅ budget met")
    return 0


if __name__ == "__main__":
    rc = asyncio.run(_main_async())
    sys.exit(rc)
