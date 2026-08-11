"""
P2.3 verification — pipeline + barge-in + reconnect.

Covers:
  1. OrchestratorStreamClient SSE parsing (typed events, run_end stops,
     error surfaced, malformed lines tolerated, HTTP 4xx → error)
  2. BargeInDetector RMS thresholding, window logic, cooldown, latency
  3. VoiceSession happy path — STT final → orch stream → TTS feed + caption
  4. VoiceSession barge-in — flush TTS + emit cancel + stats bump
  5. VoiceSession reconnect — registry preserves state across socket swap
  6. VoiceSession control plane — ptt_start/ptt_end/cancel/focus

Standalone runner — no pytest needed.
"""
from __future__ import annotations

import asyncio
import json
import math
import os
import struct
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
VG = os.path.normpath(os.path.join(HERE, ".."))
sys.path.insert(0, VG)

os.environ["VOICE_WELCOME"] = "0"

from orchestrator_client import OrchestratorStreamClient            # noqa: E402
from barge_in import BargeInDetector                                # noqa: E402
from pipeline import VoiceSession, SessionRegistry                  # noqa: E402


PASSED: list[str] = []
FAILED: list[tuple[str, str]] = []


def expect(label: str, cond: bool, detail: str = ""):
    if cond:
        PASSED.append(label)
        print(f"  ✅ {label}")
    else:
        FAILED.append((label, detail))
        print(f"  ❌ {label}  ← {detail}")


def section(t: str):
    print()
    print("=" * 70)
    print(f"  {t}")
    print("=" * 70)


# =============================================================================
# Helpers — fake HTTP for orchestrator_client
# =============================================================================
class _FakeSSEResponse:
    def __init__(self, lines: list[str], status_code: int = 200):
        self.status_code = status_code
        self._lines = lines
        self.closed = False

    async def aiter_lines(self):
        for line in self._lines:
            await asyncio.sleep(0)
            yield line

    async def aclose(self):
        self.closed = True


class _FakeStreamCtx:
    def __init__(self, response: _FakeSSEResponse, recorder: dict):
        self._resp = response
        self._recorder = recorder

    async def __aenter__(self):
        return self._resp

    async def __aexit__(self, *a):
        await self._resp.aclose()


class _FakeHTTPClient:
    def __init__(self, response: _FakeSSEResponse):
        self._response = response
        self.calls: list[dict] = []
        self.closed = False

    def stream(self, method, url, *, json, headers=None, timeout=None):
        self.calls.append({"method": method, "url": url, "body": json,
                           "headers": headers or {}})
        return _FakeStreamCtx(self._response, {})

    async def aclose(self):
        self.closed = True


def _sse(events: list[dict]) -> list[str]:
    out: list[str] = []
    for e in events:
        out.append(f"data: {json.dumps(e)}")
        out.append("")
    return out


# =============================================================================
# 1. OrchestratorStreamClient
# =============================================================================
section("1. OrchestratorStreamClient — SSE parsing")

async def _orch_happy():
    events = [
        {"kind": "agent_start", "agent": "realestate_agent", "ts_ms": 1,
         "detail": {}, "run_id": "R-1"},
        {"kind": "text_delta", "agent": "realestate_agent", "ts_ms": 2,
         "detail": {"text": "Hola"}, "run_id": "R-1"},
        {"kind": "run_end", "agent": "realestate_agent", "ts_ms": 3,
         "detail": {"run_id": "R-1"}, "run_id": "R-1"},
        # This should NOT be yielded — run_end terminates the stream.
        {"kind": "text_delta", "agent": "realestate_agent", "ts_ms": 4,
         "detail": {"text": "lost"}, "run_id": "R-1"},
    ]
    lines = _sse(events)
    # Add a comment line + a blank line to verify resilience.
    lines = [": keepalive", ""] + lines
    fake_resp = _FakeSSEResponse(lines)
    fake_http = _FakeHTTPClient(fake_resp)
    client = OrchestratorStreamClient(
        "http://orch:8000", tenant_id="tnt", user_id="u",
        http_factory=lambda: fake_http,
    )
    got = []
    async for ev in client.stream("buscar depas", state={"foo": "bar"}):
        got.append(ev)
    await client.close()
    return got, fake_http


got, fake_http = asyncio.run(_orch_happy())
expect("emits 3 events, stops at run_end",      len(got) == 3, str([g['kind'] for g in got]))
expect("first event is agent_start",            got[0]["kind"] == "agent_start")
expect("text_delta text decoded",               got[1]["detail"]["text"] == "Hola")
expect("last event is run_end",                 got[-1]["kind"] == "run_end")
expect("HTTP POST issued",                      len(fake_http.calls) == 1)
expect("URL targets /agent/run/stream",
       fake_http.calls[0]["url"].endswith("/agent/run/stream"),
       fake_http.calls[0]["url"])
expect("body carries message + tenant",
       fake_http.calls[0]["body"]["message"] == "buscar depas"
       and fake_http.calls[0]["body"]["tenant_id"] == "tnt",
       str(fake_http.calls[0]["body"]))
expect("X-Tenant-Id header set",
       fake_http.calls[0]["headers"].get("X-Tenant-Id") == "tnt")
expect("client.close closes http",              fake_http.closed)


# 1b — error path
async def _orch_4xx():
    fake_resp = _FakeSSEResponse([], status_code=502)
    fake_http = _FakeHTTPClient(fake_resp)
    client = OrchestratorStreamClient("http://orch:8000",
                                      tenant_id="tnt", user_id="u",
                                      http_factory=lambda: fake_http)
    out = [ev async for ev in client.stream("x")]
    await client.close()
    return out


out = asyncio.run(_orch_4xx())
expect("non-200 yields a single error event",   len(out) == 1
       and out[0]["kind"] == "error", str(out))
expect("error detail mentions HTTP code",       "502" in out[0]["detail"]["message"])


# 1c — malformed JSON inside SSE is skipped
async def _orch_malformed():
    lines = ["data: {not json}", "", "data: {\"kind\":\"run_end\",\"detail\":{}}"]
    fake_resp = _FakeSSEResponse(lines)
    fake_http = _FakeHTTPClient(fake_resp)
    client = OrchestratorStreamClient("http://orch:8000",
                                      tenant_id="tnt", user_id="u",
                                      http_factory=lambda: fake_http)
    return [ev async for ev in client.stream("x")]


out = asyncio.run(_orch_malformed())
expect("malformed lines skipped, valid lines kept",
       len(out) == 1 and out[0]["kind"] == "run_end", str(out))


# =============================================================================
# 2. BargeInDetector
# =============================================================================
section("2. BargeInDetector — RMS thresholding + window logic")


def _frame(amp: int, n: int = 320) -> bytes:
    """Make a PCM-16 LE frame of length n at constant amplitude."""
    return struct.pack("<%dh" % n, *([amp] * n))


def _sine_frame(amp: int, n: int = 320, freq: int = 200,
                sr: int = 16000) -> bytes:
    samples = [int(amp * math.sin(2 * math.pi * freq * i / sr))
               for i in range(n)]
    return struct.pack("<%dh" % n, *samples)


SILENCE = _frame(0)
LOUD = _sine_frame(8000)        # well above default threshold (650)

bi = BargeInDetector(window_ms=200, frame_ms=20, rms_threshold=650,
                     cooldown_ms=200)
expect("no-playback feeds → no trigger",        not any(bi.feed(LOUD, playing=False)
                                                       for _ in range(50)))

bi = BargeInDetector(window_ms=200, frame_ms=20, rms_threshold=650,
                     cooldown_ms=200)
# Build voiced ratio inside the 10-frame (200ms) window.
triggered = False
fired_at = -1
for i in range(20):
    if bi.feed(LOUD, playing=True):
        triggered = True
        fired_at = i
        break
expect("loud frames during playback trigger",   triggered, f"i={fired_at}")
# Trigger should be within window_ms / frame_ms = 10 frames (=200ms).
expect("trigger fires within 200 ms window",    0 <= fired_at <= 10,
       f"fired_at={fired_at}")

# Silence during playback should NOT trigger.
bi = BargeInDetector(window_ms=200, frame_ms=20, rms_threshold=650,
                     cooldown_ms=200)
triggered = any(bi.feed(SILENCE, playing=True) for _ in range(50))
expect("silence during playback does not trigger", not triggered)

# Cooldown suppresses immediate re-fire.
bi = BargeInDetector(window_ms=200, frame_ms=20, rms_threshold=650,
                     cooldown_ms=200)
fires = 0
for _ in range(50):
    if bi.feed(LOUD, playing=True):
        fires += 1
expect("cooldown prevents stampede (fires ≤ 3)", fires <= 3, f"fires={fires}")

# RMS calc sanity.
from barge_in import _rms_int16  # noqa: E402
expect("RMS of silence == 0",                    _rms_int16(SILENCE) == 0.0)
expect("RMS of loud sine > threshold",           _rms_int16(LOUD) > 650)
expect("RMS tolerates empty frame",              _rms_int16(b"") == 0.0)
expect("RMS tolerates odd-length frame",         _rms_int16(b"\x00\x01\x02") >= 0.0)


# =============================================================================
# 3. VoiceSession — happy path
# =============================================================================
section("3. VoiceSession — STT final → orch SSE → TTS feed + caption")


class FakeSTT:
    def __init__(self, on_partial, on_final):
        self.on_partial = on_partial
        self.on_final = on_final
        self.audio_sent: list[bytes] = []
        self.started = 0
        self.ended = 0
        self.closed = False

    async def send_audio(self, b: bytes) -> None:
        self.audio_sent.append(b)

    async def start_utterance(self) -> None:
        self.started += 1

    async def end_utterance(self) -> None:
        self.ended += 1

    async def close(self) -> None:
        self.closed = True

    @property
    def is_open(self) -> bool:
        return not self.closed


class FakeTTS:
    def __init__(self, on_audio):
        self.on_audio = on_audio
        self.fed: list[str] = []
        self.flushed = 0
        self.closed = False

    async def feed(self, text: str) -> None:
        self.fed.append(text)
        # Emit one fake audio frame per feed call.
        await self.on_audio(b"AUDIO:" + text.encode("utf-8"))

    async def flush(self) -> None:
        self.flushed += 1

    async def close(self) -> None:
        self.closed = True

    @property
    def is_open(self) -> bool:
        return not self.closed


class FakeOrch:
    def __init__(self, events: list[dict], *, delay_per_event: float = 0.0):
        self.events = events
        self.delay = delay_per_event
        self.streams_started = 0
        self.last_message: str = ""
        self.last_state: dict = {}
        self.closed = False

    def stream(self, message: str, state: dict = None):
        self.streams_started += 1
        self.last_message = message
        self.last_state = dict(state or {})
        events = list(self.events)

        async def _gen():
            for e in events:
                if self.delay:
                    await asyncio.sleep(self.delay)
                yield e
        return _gen()

    async def close(self):
        self.closed = True


async def _happy_path():
    text_events: list[dict] = []
    byte_events: list[bytes] = []

    async def send_text(d):
        text_events.append(d)

    async def send_bytes(b):
        byte_events.append(b)

    fake_stt_holder = {}
    fake_tts_holder = {}

    async def stt_factory(on_p, on_f):
        s = FakeSTT(on_p, on_f)
        fake_stt_holder["stt"] = s
        return s

    async def tts_factory(on_a):
        t = FakeTTS(on_a)
        fake_tts_holder["tts"] = t
        return t

    orch = FakeOrch([
        {"kind": "agent_start", "agent": "realestate_agent", "detail": {},
         "run_id": "R-1"},
        {"kind": "text_delta", "agent": "realestate_agent",
         "detail": {"text": "Hola, "}, "run_id": "R-1"},
        {"kind": "text_delta", "agent": "realestate_agent",
         "detail": {"text": "encontré opciones."}, "run_id": "R-1"},
        {"kind": "run_end", "agent": "realestate_agent",
         "detail": {"run_id": "R-1", "agents_invoked": ["realestate_agent"]},
         "run_id": "R-1"},
    ])

    session = VoiceSession.create(
        session_id="S-TEST01",
        tenant_id="tnt",
        user_id="u",
        stt_factory=stt_factory,
        tts_factory=tts_factory,
        orch=orch,
        send_text=send_text,
        send_bytes=send_bytes,
    )
    await session.open()
    # Lazy STT — open via first PCM frame (matches iOS batch-send path).
    await session.handle_audio(b"\x00" * 640)
    # Simulate a STT final.
    await fake_stt_holder["stt"].on_final("busco departamento en Roma")
    # Give the reply task a few cycles.
    for _ in range(50):
        await asyncio.sleep(0)
    # Wait for reply task to complete naturally.
    if session._reply_task:
        await session._reply_task
    await session.close()
    return {
        "text_events": text_events,
        "byte_events": byte_events,
        "stt": fake_stt_holder["stt"],
        "tts": fake_tts_holder["tts"],
        "orch": orch,
        "session": session,
    }


r = asyncio.run(_happy_path())
expect("orchestrator stream invoked once",      r["orch"].streams_started == 1)
expect("orchestrator received user message",
       r["orch"].last_message == "busco departamento en Roma")
expect("state forwarded to orchestrator",
       r["orch"].last_state.get("tenant_id") == "tnt")
kinds = [e.get("type") for e in r["text_events"]]
expect("client received hello first",            kinds[0] == "hello",
       str(kinds[:3]))
expect("client received transcript_final",       "transcript_final" in kinds)
expect("client received agent_event (≥1)",
       sum(1 for k in kinds if k == "agent_event") >= 1)
expect("client received reply_text (≥2 chunks)",
       sum(1 for k in kinds if k == "reply_text") >= 2)
expect("client received run_end",                "run_end" in kinds)
expect("TTS fed both text deltas",
       r["tts"].fed == ["Hola, ", "encontré opciones."],
       str(r["tts"].fed))
expect("TTS audio bytes reached the socket",
       len(r["byte_events"]) >= 2, f"got {len(r['byte_events'])}")
expect("first audio byte tagged 'Hola'",
       r["byte_events"][0].startswith(b"AUDIO:Hola"),
       r["byte_events"][0].decode())
expect("session stats recorded one reply",       r["session"].stats["replies_started"] == 1)
expect("session stats recorded first-audio time",
       r["session"].stats["last_first_audio_at"] > 0)
expect("close marked STT closed",                r["stt"].closed)
expect("close marked TTS closed",                r["tts"].closed)
expect("close marked orch closed",               r["orch"].closed)


# =============================================================================
# 4. VoiceSession — barge-in cancels reply
# =============================================================================
section("4. VoiceSession — barge-in flushes TTS + emits cancel")


class TriggerBarge:
    """Always says 'speech detected' so the test deterministically fires."""

    def __init__(self):
        self.fed = 0
        self.started = 0
        self.ended = 0

    def feed(self, frame: bytes, *, playing: bool) -> bool:
        self.fed += 1
        return playing  # trigger any time TTS is playing

    def notify_tts_start(self):
        self.started += 1

    def notify_tts_end(self):
        self.ended += 1


async def _barge_path():
    text_events: list[dict] = []
    byte_events: list[bytes] = []

    async def send_text(d): text_events.append(d)
    async def send_bytes(b): byte_events.append(b)

    fake_stt_holder, fake_tts_holder = {}, {}

    async def stt_factory(on_p, on_f):
        s = FakeSTT(on_p, on_f); fake_stt_holder["stt"] = s; return s

    async def tts_factory(on_a):
        t = FakeTTS(on_a); fake_tts_holder["tts"] = t; return t

    # Orchestrator that drips events slowly so we have time to barge in.
    orch = FakeOrch([
        {"kind": "text_delta", "agent": "realestate_agent",
         "detail": {"text": "Hola, "}, "run_id": "R-1"},
        {"kind": "text_delta", "agent": "realestate_agent",
         "detail": {"text": "encontré tres opciones."}, "run_id": "R-1"},
        {"kind": "text_delta", "agent": "realestate_agent",
         "detail": {"text": "La primera es..."}, "run_id": "R-1"},
        {"kind": "run_end", "agent": "realestate_agent",
         "detail": {"run_id": "R-1"}, "run_id": "R-1"},
    ], delay_per_event=0.02)

    barge = TriggerBarge()
    session = VoiceSession.create(
        session_id="S-BARGE01",
        tenant_id="tnt", user_id="u",
        stt_factory=stt_factory, tts_factory=tts_factory, orch=orch,
        send_text=send_text, send_bytes=send_bytes,
        barge=barge,
    )
    await session.open()
    await session.handle_audio(b"\x00" * 640)
    await fake_stt_holder["stt"].on_final("dime opciones")
    # Let the first text_delta land + audio fire so _tts_playing becomes True.
    for _ in range(5):
        await asyncio.sleep(0.005)
    # Now user speaks → barge-in trigger.
    await session.handle_audio(LOUD)
    # Drain remaining tasks.
    for _ in range(50):
        await asyncio.sleep(0)
    await session.close()
    return {
        "text_events": text_events,
        "byte_events": byte_events,
        "tts": fake_tts_holder["tts"],
        "session": session,
    }


r = asyncio.run(_barge_path())
kinds = [e.get("type") for e in r["text_events"]]
expect("barge-in emitted at least one cancel",
       sum(1 for k in kinds if k == "cancel") >= 1, str(kinds))
expect("TTS flushed at least once due to barge-in",
       r["tts"].flushed >= 1, f"flushed={r['tts'].flushed}")
expect("session stats counted the barge-in",
       r["session"].stats["barge_ins"] >= 1,
       str(r["session"].stats))
expect("session stats counted a cancelled reply",
       r["session"].stats["replies_cancelled"] >= 1,
       str(r["session"].stats))


# =============================================================================
# 5. VoiceSession — reconnect via registry
# =============================================================================
section("5. SessionRegistry — reconnect swaps hooks, preserves state")


async def _reconnect_path():
    reg = SessionRegistry()
    captures_v1: list[dict] = []
    captures_v2: list[dict] = []

    async def send_text_v1(d): captures_v1.append(d)
    async def send_bytes_v1(_): ...
    async def send_text_v2(d): captures_v2.append(d)
    async def send_bytes_v2(_): ...

    fake_stt = {}
    fake_tts = {}

    async def stt_factory(on_p, on_f):
        s = FakeSTT(on_p, on_f); fake_stt["s"] = s; return s

    async def tts_factory(on_a):
        t = FakeTTS(on_a); fake_tts["t"] = t; return t

    orch = FakeOrch([
        {"kind": "text_delta", "agent": "realestate_agent",
         "detail": {"text": "Listo."}, "run_id": "R-2"},
        {"kind": "run_end", "agent": "realestate_agent",
         "detail": {"run_id": "R-2"}, "run_id": "R-2"},
    ])

    session = VoiceSession.create(
        session_id="S-RESUME1",
        tenant_id="tnt", user_id="u",
        stt_factory=stt_factory, tts_factory=tts_factory, orch=orch,
        send_text=send_text_v1, send_bytes=send_bytes_v1,
    )
    reg.put(session)
    await session.open()
    # Tuck a focus into state, then "drop" the WS.
    await session.handle_control({"type": "focus", "listing_id": "L-001"})
    await session.detach_socket()
    # Reconnect: registry should still hold the same session_id.
    same = reg.get("S-RESUME1")
    assert same is session, "registry did not return same session"
    await same.attach_socket(send_text_v2, send_bytes_v2)
    # New turn arrives on the resumed socket.
    await same.handle_audio(b"\x00" * 640)
    await fake_stt["s"].on_final("siguiente paso")
    if session._reply_task:
        await session._reply_task
    await session.close()
    return {
        "v1": captures_v1, "v2": captures_v2,
        "state": session.state,
        "stats": session.stats,
        "reg_size_before": len(reg),
    }


r = asyncio.run(_reconnect_path())
v1_kinds = [e.get("type") for e in r["v1"]]
v2_kinds = [e.get("type") for e in r["v2"]]
expect("v1 received hello on open",              "hello" in v1_kinds)
expect("v1 did not receive resumed",             "resumed" not in v1_kinds)
expect("v2 received resumed on attach",          "resumed" in v2_kinds, str(v2_kinds))
expect("v2 received the new turn's text",
       "reply_text" in v2_kinds, str(v2_kinds))
expect("focus state preserved across reconnect",
       r["state"].get("focus_listing_id") == "L-001",
       str(r["state"]))
expect("reconnect stat incremented",             r["stats"]["reconnects"] == 1)


# =============================================================================
# 6. VoiceSession — control plane
# =============================================================================
section("6. VoiceSession — control plane (ptt_start/ptt_end/cancel/focus)")


async def _control_plane():
    fake_stt_holder = {}
    fake_tts_holder = {}

    async def stt_factory(on_p, on_f):
        s = FakeSTT(on_p, on_f); fake_stt_holder["stt"] = s; return s

    async def tts_factory(on_a):
        t = FakeTTS(on_a); fake_tts_holder["tts"] = t; return t

    orch = FakeOrch([
        {"kind": "run_end", "agent": "realestate_agent",
         "detail": {"run_id": "R-Y"}, "run_id": "R-Y"},
    ])
    session = VoiceSession.create(
        session_id="S-CTL", tenant_id="t", user_id="u",
        stt_factory=stt_factory, tts_factory=tts_factory, orch=orch,
    )
    await session.open()
    await session.handle_control({"type": "ptt_start"})
    await session.handle_audio(b"\x00" * 640)
    await session.handle_control({"type": "ptt_end"})
    await session.handle_control({"type": "focus",
                                  "document_id": "D-9"})
    await session.handle_control({"type": "cancel"})
    await session.close()
    return fake_stt_holder["stt"], session


stt, sess = asyncio.run(_control_plane())
expect("first audio frame opened STT",           stt.started == 1)
expect("ptt_end routed to STT",                  stt.ended == 1)
expect("focus stashed document_id",              sess.state.get("focus_document_id") == "D-9")
expect("cancel handled without active reply",    True)


# =============================================================================
# Summary
# =============================================================================
print()
print("=" * 70)
print("  SUMMARY")
print("=" * 70)
print(f"  Passed: {len(PASSED)}")
print(f"  Failed: {len(FAILED)}")
if FAILED:
    for label, detail in FAILED:
        print(f"  ❌ {label}: {detail}")
    sys.exit(1)
print("  All P2.3 pipeline assertions green. ✅")
sys.exit(0)
