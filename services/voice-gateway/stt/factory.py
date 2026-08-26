"""
STT provider factory — mirrors tts/factory.py.

Selects the active STT adapter based on `STT_PROVIDER` env:
    STT_PROVIDER=deepgram (default) → DeepgramSTT
    STT_PROVIDER=google             → GoogleSTT (Chirp 3, Speech v2)

Both implement STTProvider, so callers (pipeline/session.py via the
`stt_factory` closure in main.py) don't change.
"""
from __future__ import annotations

import os
from typing import Optional

from .interfaces import OnFinal, OnPartial, STTProvider

STT_PROVIDER = os.getenv("STT_PROVIDER", "deepgram").lower()


async def open_stt(
    language: str,
    on_partial: Optional[OnPartial] = None,
    on_final: Optional[OnFinal] = None,
) -> STTProvider:
    """Open the active STT provider per `STT_PROVIDER`."""
    if STT_PROVIDER == "google":
        from .google_stt import GoogleSTT

        return await GoogleSTT.open(
            language=language, on_partial=on_partial, on_final=on_final
        )
    from .deepgram_client import DeepgramSTT

    return await DeepgramSTT.open(
        language=language, on_partial=on_partial, on_final=on_final
    )