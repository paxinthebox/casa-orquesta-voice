"""
VoiceSession — Phase 2.3 integration point.

Glues together:
  * STT (Deepgram or equivalent)            — STTProvider protocol
  * Orchestrator SSE stream                  — OrchestratorStreamClient
  * TTS (ElevenLabs Flash or Azure)          — TTSProvider protocol
  * Barge-in detector                        — BargeInDetector

Surface area is intentionally small so it stays testable without httpx,
websockets, or fastapi installed:

    session = VoiceSession.create(
        session_id="S-ABC123",
        tenant_id="tnt_demo",
        user_id="u_paco",
        stt_factory=lambda on_partial, on_final: FakeSTT(on_partial, on_final),
        tts_factory=lambda on_audio: FakeTTS(on_audio),
        orch=fake_orch_client,
        send_text=async_callback,
        send_bytes=async_callback,
    )
    await session.open()
    # ... mic frames + control ...
    await session.handle_audio(frame_bytes)
    await session.handle_control({"type": "ptt_end"})
    await session.close()

Reconnect / resume:

    # On reconnect the gateway looks up the session by id and re-attaches:
    existing = REGISTRY.get("S-ABC123")
    if existing:
        await existing.attach_socket(new_send_text, new_send_bytes)
    else:
        existing = VoiceSession.create(... session_id="S-ABC123" ...)
        await existing.open()
        REGISTRY.put(existing)
"""
from __future__ import annotations

import asyncio
import os
import sys
import time
import uuid
from typing import Any, Awaitable, Callable, Optional

from tts.text_sanitize import sanitize_for_tts

_SHARED = os.path.normpath(os.path.join(os.path.dirname(__file__), "..", "..", "_shared"))
if _SHARED not in sys.path:
    sys.path.insert(0, _SHARED)
from money_speech import normalize_money_in_text  # noqa: E402
from location_filters import extract_location_filters  # noqa: E402

VOICE_WELCOME_ENABLED = os.getenv("VOICE_WELCOME", "1") == "1"
DEFAULT_WELCOME_BUYER = (
    "¡Hola! Soy tu asesor de Casa·Orquesta. "
    "Cuéntame qué tipo de casa buscas en CDMX o Morelos. "
    "Mantén presionado el micrófono cuando quieras hablar."
)
DEFAULT_WELCOME_SELLER = (
    "¡Hola! Soy tu asesor de Casa·Orquesta. "
    "¿Quieres publicar tu propiedad, revisar ofertas o preparar la documentación? "
    "Mantén presionado el micrófono cuando quieras hablar."
)


def welcome_message(state: dict | None = None) -> str:
    override = (os.getenv("VOICE_WELCOME_MESSAGE") or "").strip()
    if override:
        return override
    role = (state or {}).get("client_role", "buyer")
    if role == "seller":
        return DEFAULT_WELCOME_SELLER
    return DEFAULT_WELCOME_BUYER


def filters_from_client_profile(profile: dict | None) -> dict:
    """Map mobile client_profile → session filters (mirrors orchestrator baseline)."""
    if not isinstance(profile, dict) or not profile:
        return {}
    out: dict = {}
    mode = profile.get("listing_mode")
    if mode in ("sale", "rent"):
        out["listing_mode"] = mode
    budget = profile.get("budget_mxn")
    if isinstance(budget, (int, float)) and budget > 0:
        out["price_max_mxn"] = int(budget)
    state = profile.get("state")
    if state in ("CDMX", "Morelos"):
        out["state"] = state
    elif state in ("both", "CDMX y Morelos", "cdmx y morelos"):
        out["states"] = ["CDMX", "Morelos"]
    area = str(profile.get("area") or "").strip()
    if area:
        loc = extract_location_filters(area)
        out.update(loc)
        if "city" not in out:
            out.setdefault("neighborhood", area)
    prop_types = profile.get("property_types")
    if isinstance(prop_types, list) and prop_types:
        cleaned = [str(t).strip() for t in prop_types if str(t).strip()]
        if cleaned:
            out["property_types"] = cleaned
            if len(cleaned) == 1:
                out["type"] = cleaned[0]
            else:
                out["types"] = cleaned
    else:
        prop_type = profile.get("property_type")
        if isinstance(prop_type, str) and prop_type.strip():
            out["type"] = prop_type.strip()
    beds = profile.get("beds_min")
    if isinstance(beds, (int, float)) and beds > 0:
        out["beds_min"] = int(beds)
    baths = profile.get("baths_min")
    if isinstance(baths, (int, float)) and baths > 0:
        out["baths_min"] = int(baths)
    # loan_type stays on client_profile for credit CRM; not a search mortgage filter.
    features = profile.get("features")
    if isinstance(features, list) and features:
        # Align UI tokens with matching closeness keywords.
        mapped: list[str] = []
        for raw in features:
            feat = str(raw).strip()
            if not feat:
                continue
            if feat.lower() in {"parking", "estacionamiento"}:
                mapped.append("estacionamiento")
            else:
                mapped.append(feat)
        if mapped:
            out["features"] = list(dict.fromkeys(mapped))
    return out

# --------------------------------------------------------------------------
# Hooks the WebSocket layer provides for outbound frames.
# --------------------------------------------------------------------------
SendText = Callable[[dict], Awaitable[None]]
SendBytes = Callable[[bytes], Awaitable[None]]

# Factories — passed at construction so the test suite can inject fakes
# without importing the real Deepgram / ElevenLabs / httpx clients.
STTFactory = Callable[
    [Callable[[str], Awaitable[None]], Callable[[str], Awaitable[None]]],
    Awaitable[Any],
]
TTSFactory = Callable[
    [Callable[[bytes], Awaitable[None]]],
    Awaitable[Any],
]


async def _noop_text(_: dict) -> None: ...
async def _noop_bytes(_: bytes) -> None: ...


# --------------------------------------------------------------------------
# VoiceSession
# --------------------------------------------------------------------------
class VoiceSession:
    """Per-session orchestrator. One per WS connection (or per resumed id)."""

    def __init__(
        self,
        *,
        session_id: str,
        tenant_id: str,
        user_id: str,
        stt_factory: STTFactory,
        tts_factory: TTSFactory,
        orch: Any,                          # OrchestratorStreamClient-ish
        send_text: SendText = _noop_text,
        send_bytes: SendBytes = _noop_bytes,
        barge: Optional[Any] = None,        # BargeInDetector or fake
    ):
        self.session_id = session_id
        self.tenant_id = tenant_id
        self.user_id = user_id
        self._stt_factory = stt_factory
        self._tts_factory = tts_factory
        self._orch = orch
        self._send_text = send_text
        self._send_bytes = send_bytes
        self._barge = barge

        # Conversation state — survives a WS drop.
        self.state: dict[str, Any] = {
            "tenant_id": tenant_id,
            "client_role": "buyer",
            "filters": {},
            "candidates": [],
        }

        # Mutable connections.
        self._stt = None
        self._tts = None
        self._opened = False
        self._closing = False

        # Current outbound reply (so barge-in / cancel can stop it).
        self._reply_task: Optional[asyncio.Task] = None
        self._welcome_task: Optional[asyncio.Task] = None

        # TTS playback flag — set when we forward an audio frame, cleared
        # when we either flush or finish a reply.
        self._tts_playing = False
        self._ptt_active = False

        # Locks/queues
        self._lock = asyncio.Lock()

        # Stats for the perf harness.
        self.stats: dict[str, Any] = {
            "replies_started": 0,
            "replies_cancelled": 0,
            "barge_ins": 0,
            "reconnects": 0,
            "last_final_at": 0.0,
            "last_first_audio_at": 0.0,
            "audio_bytes_sent": 0,
        }

    # ----------------------------------------------------------------------
    # Factory
    # ----------------------------------------------------------------------
    @classmethod
    def create(
        cls,
        *,
        session_id: Optional[str] = None,
        tenant_id: str,
        user_id: str,
        stt_factory: STTFactory,
        tts_factory: TTSFactory,
        orch: Any,
        send_text: SendText = _noop_text,
        send_bytes: SendBytes = _noop_bytes,
        barge: Optional[Any] = None,
    ) -> "VoiceSession":
        sid = session_id or f"S-{uuid.uuid4().hex[:10].upper()}"
        return cls(
            session_id=sid,
            tenant_id=tenant_id,
            user_id=user_id,
            stt_factory=stt_factory,
            tts_factory=tts_factory,
            orch=orch,
            send_text=send_text,
            send_bytes=send_bytes,
            barge=barge,
        )

    def _apply_client_profile(self, profile: Any) -> None:
        """Persist formulary and reset search filters so sale/rent mode can't stick.

        ``None`` / empty clears both profile and filters (mobile clear sends null).
        """
        if profile:
            self.state["client_profile"] = profile
            self.state["filters"] = filters_from_client_profile(profile)
        else:
            self.state.pop("client_profile", None)
            self.state["filters"] = {}

    # ----------------------------------------------------------------------
    # Lifecycle
    # ----------------------------------------------------------------------
    async def open(self) -> None:
        if self._opened:
            return
        # STT/TTS open lazily: iOS batch-emits PCM only after PTT release,
        # so an eager Deepgram WS hits net0001 (no audio within ~10 s).
        self._opened = True
        await self._send_text({"type": "hello",
                               "session_id": self.session_id,
                               "tenant_id": self.tenant_id})
        # Welcome is deferred until the client sends set_context with
        # welcome_sent — avoids racing open() with duplicate set_context.

    async def close(self) -> None:
        if not self._opened or self._closing:
            return
        self._closing = True
        if self._reply_task and not self._reply_task.done():
            self._reply_task.cancel()
            try:
                await self._reply_task
            except (asyncio.CancelledError, Exception):
                pass
        if self._welcome_task and not self._welcome_task.done():
            self._welcome_task.cancel()
            try:
                await self._welcome_task
            except (asyncio.CancelledError, Exception):
                pass
        for c in (self._stt, self._tts):
            if c is not None:
                try:
                    await c.close()
                except Exception:
                    pass
        try:
            await self._orch.close()
        except Exception:
            pass
        self._opened = False

    # ----------------------------------------------------------------------
    # Socket binding (used by reconnect)
    # ----------------------------------------------------------------------
    async def attach_socket(self, send_text: SendText, send_bytes: SendBytes) -> None:
        """Swap the outbound hooks. Used when a new WS resumes this session_id.

        Conversation state survives; in-flight replies do *not* (they may have
        already drained to /dev/null). The client is expected to retry the
        last user turn if it missed the reply.
        """
        async with self._lock:
            self._send_text = send_text
            self._send_bytes = send_bytes
            self.stats["reconnects"] += 1
            await self._send_text({"type": "resumed",
                                   "session_id": self.session_id})

    async def detach_socket(self) -> None:
        """Called when the WS drops. Replace hooks with no-ops so any
        in-flight tasks don't blow up trying to write to a closed socket."""
        async with self._lock:
            self._send_text = _noop_text
            self._send_bytes = _noop_bytes

    # ----------------------------------------------------------------------
    # Inbound frames + control
    # ----------------------------------------------------------------------
    async def handle_audio(self, frame: bytes) -> None:
        if not self._opened or self._closing or not frame:
            return
        # Always forward to STT — Deepgram keeps a per-utterance stream and we
        # don't want to drop frames just because the bot is talking.
        try:
            await self._ensure_stt()
            await self._stt.send_audio(frame)
        except Exception as e:
            print(f"[voice.session] stt.send_audio error: {e!r}")
        # Barge-in: only fire when TTS is playing.
        if self._barge is not None and self._tts_playing:
            try:
                if self._barge.feed(frame, playing=True):
                    self.stats["barge_ins"] += 1
                    await self._cancel_reply(reason="barge_in")
            except Exception as e:
                print(f"[voice.session] barge.feed error: {e!r}")

    async def handle_control(self, ctrl: dict) -> None:
        if not self._opened:
            return
        t = ctrl.get("type")
        if t == "ptt_start":
            # STT opens on the first audio frame (see handle_audio). iOS sends
            # PCM in one burst after stop(), so opening here would idle out.
            self._ptt_active = True
            if self._welcome_task and not self._welcome_task.done():
                self._welcome_task.cancel()
            if self._tts is not None and self._tts_playing:
                try:
                    await self._tts.flush()
                except Exception:
                    pass
            if self._stt is not None and self._stt.is_open:
                await self._stt.start_utterance()
        elif t == "ptt_end":
            if not self._ptt_active:
                return
            self._ptt_active = False
            if self._stt is None or not self._stt.is_open:
                deadline = time.monotonic() + 1.0
                while time.monotonic() < deadline:
                    if self._stt is not None and self._stt.is_open:
                        break
                    await asyncio.sleep(0.05)
            if self._stt is None or not self._stt.is_open:
                await self._send_text({
                    "type": "error",
                    "message": "No se escuchó nada. Intenta de nuevo.",
                })
                return
            await self._stt.end_utterance()
        elif t == "cancel":
            await self._cancel_reply(reason="user_cancel")
        elif t == "focus":
            # Client can pin a listing/document/person into state.
            lid = ctrl.get("listing_id")
            did = ctrl.get("document_id")
            pid = ctrl.get("person_id")
            if lid:
                self.state["focus_listing_id"] = lid
            if did:
                self.state["focus_document_id"] = did
            if pid:
                self.state["focus_person_id"] = str(pid)
                kind = ctrl.get("person_kind")
                if kind in ("buyer", "collaborator", "broker"):
                    self.state["focus_person_kind"] = kind
                name = ctrl.get("person_name")
                if name:
                    self.state["focus_person_name"] = str(name)
        elif t == "set_context":
            conv_id = ctrl.get("conversation_id")
            if conv_id:
                self.state["conversation_id"] = str(conv_id)
            role = ctrl.get("client_role")
            prev_role = self.state.get("client_role")
            if role in ("buyer", "seller"):
                self.state["client_role"] = role
            if ctrl.get("welcome_sent"):
                self.state["welcome_sent"] = True
                if self._welcome_task and not self._welcome_task.done():
                    self._welcome_task.cancel()
            elif VOICE_WELCOME_ENABLED and not self.state.get("welcome_sent"):
                self._schedule_welcome()
            elif (
                role in ("buyer", "seller")
                and prev_role != role
                and not self.state.get("welcome_sent")
                and VOICE_WELCOME_ENABLED
            ):
                self._schedule_welcome()
            lid = ctrl.get("focus_listing_id")
            if lid is not None:
                if lid:
                    self.state["focus_listing_id"] = lid
                else:
                    self.state.pop("focus_listing_id", None)
            did = ctrl.get("focus_document_id")
            if did is not None:
                if did:
                    self.state["focus_document_id"] = did
                else:
                    self.state.pop("focus_document_id", None)
            pid = ctrl.get("focus_person_id")
            if pid is not None:
                if pid:
                    self.state["focus_person_id"] = str(pid)
                else:
                    self.state.pop("focus_person_id", None)
                    self.state.pop("focus_person_kind", None)
                    self.state.pop("focus_person_name", None)
            pkind = ctrl.get("focus_person_kind")
            if pkind is not None:
                if pkind in ("buyer", "collaborator", "broker"):
                    self.state["focus_person_kind"] = pkind
                elif not pkind:
                    self.state.pop("focus_person_kind", None)
            pname = ctrl.get("focus_person_name")
            if pname is not None:
                if pname:
                    self.state["focus_person_name"] = str(pname)
                else:
                    self.state.pop("focus_person_name", None)
            cp = ctrl.get("client_profile")
            if "client_profile" in ctrl:
                self._apply_client_profile(cp)
        elif t == "user_message":
            if "client_profile" in ctrl:
                self._apply_client_profile(ctrl.get("client_profile"))
            text = str(ctrl.get("text") or "").strip()
            if text:
                await self._send_text({"type": "transcript_final", "text": text})
                await self._cancel_reply(reason="new_turn")
                self._reply_task = asyncio.create_task(self._reply(text))
        # Unknown control types are ignored.
    # ----------------------------------------------------------------------
    # STT callbacks
    # ----------------------------------------------------------------------
    async def _on_partial(self, text: str) -> None:
        text = normalize_money_in_text(text)
        await self._send_text({"type": "transcript_partial", "text": text})

    async def _on_final(self, text: str) -> None:
        text = normalize_money_in_text(text)
        await self._send_text({"type": "transcript_final", "text": text})
        if not text.strip():
            await self._send_text({
                "type": "error",
                "message": "No se escuchó nada. Intenta de nuevo.",
            })
            return
        self.stats["last_final_at"] = time.monotonic()
        # Cancel any prior reply so the latest turn wins.
        await self._cancel_reply(reason="new_turn")
        # Fire off the reply task.
        self._reply_task = asyncio.create_task(self._reply(text))

    async def _ensure_stt(self) -> None:
        """Open Deepgram on first PCM frame so idle WS timeouts can't fire."""
        if self._stt is None or not self._stt.is_open:
            self._stt = await self._stt_factory(
                self._on_partial, self._on_final,
            )
            await self._stt.start_utterance()

    async def _ensure_tts(self) -> None:
        """Reopen TTS if a prior turn closed the provider WS."""
        if self._tts is None or not getattr(self._tts, "is_open", True):
            self._tts = await self._tts_factory(self._on_tts_audio)

    def _schedule_welcome(self) -> None:
        """Speak the proactive greeting once per session (deduped)."""
        if self._closing or not VOICE_WELCOME_ENABLED:
            return
        if self.state.get("welcome_sent"):
            return
        if self._welcome_task and not self._welcome_task.done():
            return
        self.state["welcome_sent"] = True
        self._welcome_task = asyncio.create_task(self._speak_welcome())

    async def _speak_welcome(self) -> None:
        """Proactive greeting — first assistant turn when the chat opens."""
        if self._closing:
            return
        text = welcome_message(self.state)
        if not text:
            return
        run_end = {
            "kind": "run_end",
            "agent": "realestate_agent",
            "ts_ms": int(time.time() * 1000),
            "detail": {"reply": text},
        }
        try:
            await self._ensure_tts()
            for i in range(0, len(text), 80):
                chunk = text[i:i + 80]
                await self._send_text({"type": "reply_text", "text": chunk})
            await self._tts.feed(sanitize_for_tts(text))
            finish = getattr(self._tts, "finish", None)
            if finish is not None:
                await finish()
            await asyncio.sleep(0.1)
            await self._send_text({"type": "run_end", "event": run_end})
        except asyncio.CancelledError:
            raise
        except Exception as e:
            print(f"[voice.session] welcome TTS error: {e!r}")
            try:
                await self._send_text({"type": "run_end", "event": run_end})
            except Exception:
                pass
        finally:
            self._tts_playing = False
            if self._tts is not None:
                try:
                    await self._tts.close()
                except Exception:
                    pass
                self._tts = None
            self._welcome_task = None

    # ----------------------------------------------------------------------
    # Reply path: text → orch SSE → TTS + caption events
    # ----------------------------------------------------------------------
    async def _reply(self, message: str) -> None:
        self.stats["replies_started"] += 1
        self.stats["audio_bytes_sent"] = 0
        first_audio_seen = False

        async def _maybe_record_first_audio() -> None:
            nonlocal first_audio_seen
            if first_audio_seen:
                return
            first_audio_seen = True
            self.stats["last_first_audio_at"] = time.monotonic()

        try:
            await self._ensure_tts()
            pending_run_end: dict[str, Any] | None = None
            fed_chars = 0
            async for ev in self._orch.stream(message, state=self.state):
                kind = ev.get("kind")
                detail = ev.get("detail") or {}
                if kind == "text_delta":
                    text = detail.get("text") or ""
                    if text:
                        # Caption to the client and feed to TTS.
                        await self._send_text({"type": "reply_text", "text": text})
                        await self._tts.feed(sanitize_for_tts(text))
                        fed_chars += len(text)
                elif kind in ("agent_start", "agent_tool",
                              "tool_result", "agent_end"):
                    await self._send_text({"type": "agent_event", "event": ev})
                elif kind == "run_end":
                    # Hold run_end until TTS audio has been synthesized.
                    detail_state = detail.get("state") or {}
                    if isinstance(detail_state, dict):
                        cp = detail_state.get("client_profile")
                        if isinstance(cp, dict) and cp:
                            self.state["client_profile"] = cp
                        if detail_state.get("filters"):
                            self.state["filters"] = {
                                **self.state.get("filters", {}),
                                **detail_state["filters"],
                            }
                        if detail_state.get("last_candidates"):
                            self.state["last_candidates"] = detail_state["last_candidates"]
                        for key in (
                            "last_match_quality",
                            "last_relaxations",
                            "focus_person_id",
                            "focus_person_kind",
                            "focus_person_name",
                            "last_collaborator_agents",
                            "last_brokers",
                            "last_buyers",
                        ):
                            if key in detail_state:
                                self.state[key] = detail_state[key]
                    self.state.setdefault("last_runs", []).append({
                        "run_id": detail.get("run_id"),
                        "agents": detail.get("agents_invoked"),
                    })
                    pending_run_end = ev
                elif kind == "error":
                    await self._send_text({"type": "error",
                                           "message": detail.get("message", "")})
            # Emit run_end before TTS drain so mobile can render cards while audio plays.
            if pending_run_end is not None:
                await self._send_text({"type": "run_end", "event": pending_run_end})
            # Drain phrase chunker + let ElevenLabs push trailing audio frames.
            if self._tts is not None:
                # Safety net: if text_deltas never arrived, speak run_end reply.
                if pending_run_end and fed_chars == 0:
                    reply = (pending_run_end.get("detail") or {}).get("reply") or ""
                    if reply.strip():
                        for i in range(0, len(reply), 80):
                            chunk = reply[i:i + 80]
                            await self._send_text({"type": "reply_text", "text": chunk})
                            await self._tts.feed(sanitize_for_tts(chunk))
                            fed_chars += len(chunk)
                finish = getattr(self._tts, "finish", None)
                if finish is not None:
                    try:
                        await finish()
                    except Exception as e:
                        print(f"[voice.session] tts.finish error: {e!r}")
                # finish() waits for idle audio; tiny cushion for WS send buffer.
                await asyncio.sleep(0.1)
            if pending_run_end is not None:
                if self.stats["audio_bytes_sent"] == 0 and fed_chars > 0:
                    last_err = (
                        getattr(self._tts, "last_error", None) if self._tts else None
                    )
                    msg = last_err or (
                        "No se pudo generar audio con ElevenLabs. "
                        "Verifica ELEVENLABS_VOICE_ID y tu plan."
                    )
                    voice_id = getattr(self._tts, "voice", None) if self._tts else None
                    print(
                        f"[voice.session] TTS produced 0 audio bytes "
                        f"(voice_id={voice_id}): {msg}"
                    )
                    await self._send_text({
                        "type": "tts_error",
                        "message": msg,
                        "voice_id": voice_id,
                    })
        except asyncio.CancelledError:
            # Cancellation is expected on barge-in / new-turn. Tell the
            # client to stop any pending playback.
            await self._send_text({"type": "cancel"})
            self.stats["replies_cancelled"] += 1
            raise
        finally:
            # End-of-utterance: stop barge-in tracking until next reply.
            self._tts_playing = False
            if self._barge is not None:
                try:
                    self._barge.notify_tts_end()
                except Exception:
                    pass
            # Fresh TTS connection each turn — avoids stale WS after failed gens.
            if self._tts is not None:
                try:
                    await self._tts.close()
                except Exception:
                    pass
                self._tts = None

        # `_maybe_record_first_audio` lives in this closure so the perf
        # harness can read `stats["last_first_audio_at"]` independent of
        # the implementation detail of where the TTS callback fires.
        await _maybe_record_first_audio()

    # ----------------------------------------------------------------------
    # TTS audio path: synthesized frames → outbound bytes
    # ----------------------------------------------------------------------
    async def _on_tts_audio(self, frame: bytes) -> None:
        if not frame:
            return
        if not self._tts_playing:
            self._tts_playing = True
            if self._barge is not None:
                try:
                    self._barge.notify_tts_start()
                except Exception:
                    pass
            self.stats["last_first_audio_at"] = time.monotonic()
        try:
            await self._send_bytes(frame)
            self.stats["audio_bytes_sent"] += len(frame)
        except Exception as e:
            print(f"[voice.session] send_bytes error: {e!r}")

    # ----------------------------------------------------------------------
    # Cancel helpers
    # ----------------------------------------------------------------------
    async def _cancel_reply(self, *, reason: str) -> None:
        # 1. Flush TTS before cancelling the reply task (its finally closes TTS).
        if self._tts is not None:
            try:
                await self._tts.flush()
            except Exception as e:
                print(f"[voice.session] tts.flush error: {e!r}")
        # 2. Cancel the SSE task (if any).
        if self._reply_task and not self._reply_task.done():
            self._reply_task.cancel()
            try:
                await self._reply_task
            except (asyncio.CancelledError, Exception):
                pass
        self._reply_task = None
        # 3. Tell the client to stop playback.
        try:
            await self._send_text({"type": "cancel", "reason": reason})
        except Exception:
            pass
        self._tts_playing = False
        if self._barge is not None:
            try:
                self._barge.notify_tts_end()
            except Exception:
                pass


# --------------------------------------------------------------------------
# In-memory session registry for WS reconnect.
# --------------------------------------------------------------------------
class SessionRegistry:
    """Keyed by `session_id`. Sessions evict themselves on close()."""

    def __init__(self, *, ttl_seconds: int = 600):
        self._sessions: dict[str, VoiceSession] = {}
        self._ttl_seconds = ttl_seconds
        self._touched_at: dict[str, float] = {}

    def get(self, session_id: str) -> Optional[VoiceSession]:
        self._evict_stale()
        s = self._sessions.get(session_id)
        if s is not None:
            self._touched_at[session_id] = time.monotonic()
        return s

    def put(self, session: VoiceSession) -> None:
        self._sessions[session.session_id] = session
        self._touched_at[session.session_id] = time.monotonic()

    def evict(self, session_id: str) -> None:
        self._sessions.pop(session_id, None)
        self._touched_at.pop(session_id, None)

    def __len__(self) -> int:
        return len(self._sessions)

    def _evict_stale(self) -> None:
        now = time.monotonic()
        stale = [sid for sid, t in self._touched_at.items()
                 if (now - t) > self._ttl_seconds]
        for sid in stale:
            self.evict(sid)


# Module-level singleton — main.py uses this for /voice/.../resume.
REGISTRY = SessionRegistry()
