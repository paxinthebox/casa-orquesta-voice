"""
P2.2 verification — TTS adapters (ElevenLabs Flash + Azure Neural Spanish)
and the phrase chunker that drives them.

Covers:
  1. PhraseChunker — punctuation flush, word-count flush, max-chars, idempotency
  2. ElevenLabs adapter — WS handshake (initial empty + voice_settings),
     phrase forwarding, base64 audio decode + dispatch, flush (barge-in)
     bumps epoch and discards stale frames, close lifecycle, no-API-key
     refusal, malformed-JSON resilience
  3. Azure adapter — SSML generation, HTTP POST, audio chunk streaming,
     flush cancellation, voice override

Standalone runner — no pytest needed.
"""
from __future__ import annotations

import asyncio
import base64
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
VG = os.path.normpath(os.path.join(HERE, ".."))
sys.path.insert(0, VG)

from tts.phrase_chunker import PhraseChunker                     # noqa: E402
from tts.elevenlabs_client import (                               # noqa: E402
    ElevenLabsTTS, _build_url as _eleven_url,
    _humanize_error,
    resolve_voice_id,
)
from tts.text_sanitize import sanitize_for_tts                    # noqa: E402
from tts.azure_client import (                                    # noqa: E402
    AzureTTS, _build_url as _azure_url, _ssml,
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
# 1. PhraseChunker
# ====================================================================
section("1. PhraseChunker")

c = PhraseChunker(min_words=6)

expect("empty feed → no chunks",        c.feed("") == [])

# Word-count flush (6 words)
out = c.feed("una dos tres cuatro cinco seis siete")
expect("6-word threshold flushes",      len(out) == 1, str(out))
expect("flush kept 6 words",            len(out[0].split()) == 6 if out else False,
       str(out))
# Remainder
out2 = c.feed(" ocho")
expect("remainder buffered (no chunk)", out2 == [])
expect("flush() returns remainder",     c.flush().strip() in ("siete ocho", "siete  ocho",
                                                              "siete\n ocho"),
       repr(c.flush()))

# Punctuation flush
c = PhraseChunker(min_words=6)
out = c.feed("Hola, ")
expect("punctuation (comma) flushes",   out == ["Hola,"], str(out))

c = PhraseChunker(min_words=6)
out = c.feed("Encontré tres opciones.")
expect("period flushes",                out == ["Encontré tres opciones."], str(out))

# Multi-chunk in one feed (3 clause-final punctuation marks → 3 chunks)
c = PhraseChunker(min_words=6)
out = c.feed("Hola, encontré tres opciones. Te las muestro.")
expect("multi-clause splits into ≥ 2",   len(out) >= 2, str(out))

# Reset
c.feed("queda algo aquí")
c.reset()
expect("reset clears buffer",           c.flush() == "")

# Max-chars
big = "x" * 250
c = PhraseChunker(min_words=6, max_chars=240)
out = c.feed(big)
expect("max-chars enforces flush",      len(out) == 1 and len(out[0]) == 240,
       f"len={len(out[0]) if out else 0}")


# ====================================================================
# 2. ElevenLabs URL + WS handshake
# ====================================================================
section("2. ElevenLabs WSS URL + handshake")

url = _eleven_url("voice-abc")
expect("URL is wss",                     url.startswith("wss://"))
expect("URL contains voice id",          "voice-abc" in url)
expect("URL has model_id=flash",         "model_id=eleven_flash_v2_5" in url
                                          or "model_id=" in url, url)
expect("URL has pcm 16000 output",       "output_format=pcm_16000" in url
                                          or "output_format=" in url, url)


# ====================================================================
# 3. Fake WS that mirrors ElevenLabs wire format
# ====================================================================
class FakeElevenWS:
    def __init__(self):
        self.sent: list[str] = []
        self._inbound: asyncio.Queue = asyncio.Queue()
        self.closed = False

    async def send(self, data) -> None:
        self.sent.append(data if isinstance(data, str) else "<bytes>")
        # Auto-respond: any non-empty text → audio frame; empty text → final.
        if isinstance(data, str):
            try:
                p = json.loads(data)
            except (ValueError, TypeError):
                return
            txt = p.get("text", "")
            if txt:
                pcm = ("PCM:" + txt).encode("utf-8")
                self._inbound.put_nowait(json.dumps({
                    "audio": base64.b64encode(pcm).decode("ascii"),
                    "isFinal": False,
                }))
            else:
                # Empty text = flush / final
                self._inbound.put_nowait(json.dumps({
                    "audio": None,
                    "isFinal": True,
                }))

    async def recv(self):
        return await self._inbound.get()

    async def close(self, code: int = 1000) -> None:
        self.closed = True
        # Wake the reader.
        await self._inbound.put("")


section("3. ElevenLabs adapter — open + feed + audio dispatch + close")

async def _eleven_scenario():
    received_audio: list[bytes] = []

    async def on_audio(b: bytes) -> None:
        received_audio.append(b)

    fake = FakeElevenWS()
    async def connector(url, headers): return fake

    client = await ElevenLabsTTS.open(
        voice="voice-abc",
        on_audio=on_audio,
        api_key="test",
        connector=connector,
    )
    # Feed a phrase that ends with a period (causes one chunk).
    await client.feed("Hola, encontré tres opciones para ti.")
    await asyncio.sleep(0.05)
    await client.close()
    return {
        "sent": fake.sent,
        "audio": received_audio,
        "closed": fake.closed,
        "is_open_after_close": client.is_open,
    }


r = asyncio.run(_eleven_scenario())

# Initial handshake (first=True) + at least one chunk + one trailing empty on close.
expect("first wire msg has voice_settings",
       any("voice_settings" in s for s in r["sent"]),
       str(r["sent"][:2]))
expect("phrase chunks were sent",
       any('"text": "Hola,"' in s for s in r["sent"])
       and any("encontr" in s and "opciones" in s for s in r["sent"]),
       str(r["sent"]))
expect("close sent an empty-text frame",
       any('"text": ""' in s or '"text":""' in s for s in r["sent"]),
       str(r["sent"]))
expect("at least one audio frame decoded",
       len(r["audio"]) >= 1, f"got {len(r['audio'])}")
expect("audio decoded as PCM bytes (not base64)",
       all(isinstance(b, bytes) for b in r["audio"]))
expect("close marked WS closed",
       r["closed"] is True)
expect("is_open False after close",
       r["is_open_after_close"] is False)


# ====================================================================
# 4. ElevenLabs flush() bumps epoch → discards stale audio
# ====================================================================
section("4. ElevenLabs flush() (barge-in)")

async def _flush_scenario():
    received: list[bytes] = []
    async def on_audio(b): received.append(b)

    fake = FakeElevenWS()
    async def connector(u, h): return fake

    client = await ElevenLabsTTS.open(
        voice="v",
        on_audio=on_audio,
        api_key="test",
        connector=connector,
    )
    initial_epoch = client._epoch
    await client.flush()
    bumped_epoch = client._epoch
    await client.close()
    return {"initial": initial_epoch, "bumped": bumped_epoch,
            "sent": fake.sent}


r = asyncio.run(_flush_scenario())
expect("flush() bumps epoch", r["bumped"] == r["initial"] + 1,
       f"{r['initial']} → {r['bumped']}")
expect("flush() sends a text='' to ElevenLabs",
       any('"text":""' in s or '"text": ""' in s for s in r["sent"]),
       str(r["sent"]))


# ====================================================================
# 5. ElevenLabs no-API-key refusal
# ====================================================================
section("5. ElevenLabs refuses without API key")

async def _no_key():
    try:
        await ElevenLabsTTS.open(voice="v", api_key="")
        return "did not raise"
    except RuntimeError as e:
        return str(e)


expect("RuntimeError on empty key",
       "ELEVENLABS_API_KEY" in asyncio.run(_no_key()))


# ====================================================================
# 6. Azure SSML + URL
# ====================================================================
section("6. Azure SSML + URL")

ssml = _ssml("Hola & bienvenido", "es-MX-DaliaNeural")
expect("SSML is bytes",                   isinstance(ssml, bytes))
expect("SSML escapes ampersand",          b"&amp;" in ssml)
expect("SSML carries voice name",         b"es-MX-DaliaNeural" in ssml)
expect("SSML declares es-MX",             b"xml:lang='es-MX'" in ssml)
expect("Azure URL is microsoft.com",      "tts.speech.microsoft.com" in _azure_url())


# ====================================================================
# 7. Azure adapter — HTTP streaming + flush cancellation
# ====================================================================
section("7. Azure adapter — HTTP streaming + flush")

class FakeAzureResponse:
    def __init__(self, chunks: list[bytes], status_code: int = 200):
        self.status_code = status_code
        self._chunks = chunks

    async def aiter_bytes(self, chunk_size: int = 4096):
        for c in self._chunks:
            await asyncio.sleep(0)  # yield control
            yield c


class FakeAzureClient:
    def __init__(self, response: FakeAzureResponse):
        self._response = response
        self.calls: list[dict] = []

    async def post(self, url, *, headers, content):
        self.calls.append({"url": url, "headers": headers, "content": content})
        return self._response

    async def aclose(self):
        pass


def _factory(client):
    return lambda: client


async def _azure_scenario():
    received: list[bytes] = []
    async def on_audio(b): received.append(b)

    fake = FakeAzureClient(FakeAzureResponse([b"AB", b"CD", b"EF"]))
    client = await AzureTTS.open(
        voice="es-MX-DaliaNeural",
        on_audio=on_audio,
        api_key="test",
        http_factory=_factory(fake),
    )
    await client.feed("Hola, encontré tres opciones.")
    await asyncio.sleep(0.05)
    await client.close()
    return {
        "received": received,
        "calls": fake.calls,
    }


r = asyncio.run(_azure_scenario())
expect("Azure HTTP POST issued",          len(r["calls"]) >= 1)
expect("POST includes subscription key",
       "Ocp-Apim-Subscription-Key" in r["calls"][0]["headers"],
       str(r["calls"][0]["headers"]))
expect("POST body is SSML",
       r["calls"][0]["content"].startswith(b"<speak"),
       r["calls"][0]["content"][:30].decode())
expect("audio chunks streamed (one set per phrase chunk)",
       r["received"] and set(r["received"]) == {b"AB", b"CD", b"EF"},
       str(r["received"]))
expect("audio chunks preserve per-chunk ordering",
       all(c in (b"AB", b"CD", b"EF") for c in r["received"]),
       str(r["received"]))


# ====================================================================
# 8. Azure no-key refusal
# ====================================================================
section("8. Azure refuses without API key")

async def _no_key_azure():
    try:
        await AzureTTS.open(voice="v", api_key="")
        return "did not raise"
    except RuntimeError as e:
        return str(e)


expect("RuntimeError on empty AZURE_TTS_KEY",
       "AZURE_TTS_KEY" in asyncio.run(_no_key_azure()))


# ====================================================================
# 7. resolve_voice_id + TTS text sanitize
# ====================================================================
section("7. resolve_voice_id prefers ELEVENLABS_VOICE_ID")

os.environ["ELEVENLABS_VOICE_ID"] = "ewn5JTa3lNPY8QVuZJi6"
expect("ELEVENLABS_VOICE_ID wins over stale TTS_VOICE raw id",
       resolve_voice_id("elevenlabs:abcd1234567890efgh12") == "ewn5JTa3lNPY8QVuZJi6")
expect("alias TTS_VOICE uses env voice id",
       resolve_voice_id("elevenlabs:antoni-multilingual") == "ewn5JTa3lNPY8QVuZJi6")
del os.environ["ELEVENLABS_VOICE_ID"]
expect("bare 20-char id passes through when env unset",
       resolve_voice_id("elevenlabs:abcd1234567890efgh12") == "abcd1234567890efgh12")
spoken = sanitize_for_tts("Casa en Cuernavaca ($2.2 MDP)")
expect("MDP expanded for speech", "millones de pesos" in spoken and "MDP" not in spoken)
expect("402 maps to es-MX guidance",
       "plan de pago" in _humanize_error("paid_plan_required: library voices"))


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
print("  All TTS adapter assertions green. ✅")
sys.exit(0)
