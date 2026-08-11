"""
Voice pipeline package — Phase 2.3.

Public exports:
    VoiceSession       — per-session orchestrator: STT → orch SSE → TTS
    SessionRegistry    — in-memory registry for WS reconnect/resume
    REGISTRY           — process-wide SessionRegistry singleton (main.py uses it)
    SendText, SendBytes — async hooks the gateway provides for outbound frames
"""
from .session import (  # noqa: F401
    REGISTRY,
    SendBytes,
    SendText,
    SessionRegistry,
    VoiceSession,
)
