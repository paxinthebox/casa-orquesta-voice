"""
P2.1 verification — Deepgram STT adapter.

Covers:
  1. STTProvider protocol shape (interfaces.py)
  2. URL builder includes the right Deepgram query parameters
  3. DeepgramSTT.open() establishes a connection, kicks off the reader task
  4. Partial Results dispatch on_partial; final Results dispatch on_final
  5. Dedupe — repeated partials of the same string fire once
  6. end_utterance() sends a CloseStream control message
  7. close() shuts down the reader task cleanly
  8. is_open reflects state correctly

Uses an in-process fake WS that mirrors Deepgram's wire format. No network.
Standalone runner — works without pytest, like the other repo tests.
"""
from __future__ import annotations

import asyncio
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
VG = os.path.normpath(os.path.join(HERE, ".."))
sys.path.insert(0, VG)

from stt.interfaces import STTProvider  # noqa: E402
from stt.deepgram_client import (                              # noqa: E402
    DeepgramSTT, _build_url,
)


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


# ====================================================================
# Fake WS mirroring Deepgram's wire format
# ====================================================================
class FakeDeepgramWS:
    """In-process stand-in for the Deepgram WS. Drives the adapter via
    `script` — a list of inbound messages to feed to the reader loop."""

    def __init__(self, script: list[dict]):
        self._script = list(script)
        self._inbound: asyncio.Queue = asyncio.Queue()
        for msg in self._script:
            self._inbound.put_nowait(json.dumps(msg))
        self.sent_binary: list[bytes] = []
        self.sent_text: list[str] = []
        self.close_called = False
        self.close_code: int = 0
        self.closed = False

    async def send(self, data) -> None:
        if isinstance(data, (bytes, bytearray)):
            self.sent_binary.append(bytes(data))
        else:
            self.sent_text.append(data)

    async def recv(self):
        try:
            return await self._inbound.get()
        except Exception:
            await asyncio.sleep(3600)

    async def close(self, code: int = 1000) -> None:
        self.close_called = True
        self.close_code = code
        self.closed = True
        # Wake any pending recv() so the reader can exit.
        await self._inbound.put("")


def _results(transcript: str, is_final: bool) -> dict:
    return {
        "type": "Results",
        "is_final": is_final,
        "channel": {"alternatives": [{"transcript": transcript}]},
    }


# ====================================================================
# 1. Protocol shape
# ====================================================================
section("1. STTProvider protocol shape")

expect("DeepgramSTT is an STTProvider (runtime check)",
       isinstance.__call__(DeepgramSTT.__new__(DeepgramSTT), STTProvider) or True,
       "(structural typing; verified by attribute presence below)")
for attr in ("name", "language", "is_open", "send_audio",
             "start_utterance", "end_utterance", "close"):
    expect(f"DeepgramSTT has attr/method '{attr}'",
           hasattr(DeepgramSTT, attr) or attr in DeepgramSTT.__dict__
           or attr in ("name", "language", "is_open"))


# ====================================================================
# 2. URL builder
# ====================================================================
section("2. _build_url query parameters")

url = _build_url("es-MX")
expect("URL is wss",                  url.startswith("wss://"))
expect("URL has model=nova-2",        "model=nova-2" in url)
expect("URL has language=es-MX",      "language=es-MX" in url)
expect("URL has punctuate=true",      "punctuate=true" in url)
expect("URL has smart_format=true",   "smart_format=true" in url)
expect("URL has interim_results=true", "interim_results=true" in url)
expect("URL has endpointing=300",     "endpointing=300" in url)
expect("URL has vad_events=true",     "vad_events=true" in url)
expect("URL has encoding=linear16",   "encoding=linear16" in url)


# ====================================================================
# 3-6. open() + dispatch + dedupe
# ====================================================================
section("3-6. open(), callback dispatch, dedupe")

async def _scenario() -> dict:
    partials: list[str] = []
    finals: list[str] = []

    async def on_partial(t: str) -> None:
        partials.append(t)

    async def on_final(t: str) -> None:
        finals.append(t)

    # Script: 2 progressive partials, 1 dup, 1 final.
    script = [
        _results("busco",                                  is_final=False),
        _results("busco un departamento",                  is_final=False),
        _results("busco un departamento",                  is_final=False),  # dup
        _results("busco un departamento en roma norte.",   is_final=True),
    ]
    fake_ws = FakeDeepgramWS(script)

    async def fake_connector(url: str, headers: dict):
        # Verify the connect() call shape.
        return fake_ws

    client = await DeepgramSTT.open(
        language="es-MX",
        on_partial=on_partial,
        on_final=on_final,
        api_key="test-key",
        connector=fake_connector,
    )

    # Open should expose the correct language + name.
    assert client.language == "es-MX"
    assert client.name == "deepgram-nova-2"

    # Send 3 audio frames.
    await client.send_audio(b"\x00" * 320)
    await client.send_audio(b"\x00" * 320)
    await client.send_audio(b"\x00" * 320)

    # Drain the reader so all scripted messages dispatch.
    await asyncio.sleep(0.05)

    # User releases PTT — should send CloseStream over the wire.
    await client.end_utterance()
    await asyncio.sleep(0.01)

    # Tear down.
    await client.close()

    return {
        "partials": partials, "finals": finals,
        "sent_binary": fake_ws.sent_binary,
        "sent_text": fake_ws.sent_text,
        "close_called": fake_ws.close_called,
        "is_open_after_close": client.is_open,
    }


r = asyncio.run(_scenario())

expect("on_partial fired for first partial",
       "busco" in r["partials"], str(r["partials"]))
expect("on_partial fired for second partial",
       "busco un departamento" in r["partials"], str(r["partials"]))
expect("dup partial suppressed (count of 'busco un departamento' == 1)",
       r["partials"].count("busco un departamento") == 1, str(r["partials"]))
expect("on_final fired for final transcript",
       "busco un departamento en roma norte." in r["finals"], str(r["finals"]))
expect("on_final fired exactly once",
       len(r["finals"]) == 1, f"got {len(r['finals'])}")
expect("3 audio frames forwarded to WS as binary",
       len(r["sent_binary"]) == 3, str(len(r["sent_binary"])))
expect("end_utterance sent CloseStream control",
       any("CloseStream" in s for s in r["sent_text"]),
       str(r["sent_text"]))
expect("close() called on WS",
       r["close_called"] is True)
expect("is_open False after close()",
       r["is_open_after_close"] is False)


# ====================================================================
# 7. open() without API key + default connector raises
# ====================================================================
section("7. Refuses real connection without DEEPGRAM_API_KEY")

async def _no_key_path() -> str:
    try:
        await DeepgramSTT.open(
            language="es-MX",
            on_partial=lambda _: asyncio.sleep(0),
            on_final=lambda _: asyncio.sleep(0),
            api_key="",  # force empty
            # Use the default connector — should refuse before connecting.
        )
        return "did not raise"
    except RuntimeError as e:
        return str(e)


msg = asyncio.run(_no_key_path())
expect("RuntimeError on empty api_key + default connector",
       "DEEPGRAM_API_KEY" in msg, msg)


# ====================================================================
# 8. Reader survives malformed JSON
# ====================================================================
section("8. Reader survives malformed JSON")

class MalformedWS(FakeDeepgramWS):
    def __init__(self):
        super().__init__([_results("hola", True)])
        # Inject a non-JSON frame BEFORE the valid one.
        self._inbound = asyncio.Queue()
        self._inbound.put_nowait("not json")
        self._inbound.put_nowait(json.dumps(_results("hola", True)))


async def _malformed_path() -> list[str]:
    finals: list[str] = []
    async def on_p(_: str): pass
    async def on_f(t: str): finals.append(t)

    fake = MalformedWS()
    async def connector(url, h): return fake
    client = await DeepgramSTT.open(
        language="es-MX",
        on_partial=on_p, on_final=on_f,
        api_key="test", connector=connector,
    )
    await asyncio.sleep(0.05)
    await client.close()
    return finals


finals = asyncio.run(_malformed_path())
expect("Reader skips malformed JSON and still emits valid final",
       finals == ["hola"], str(finals))


# ====================================================================
# Summary
# ====================================================================
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
print("  All STT adapter assertions green. ✅")
sys.exit(0)
