"""
Phrase chunker — Phase 2.2.

Buffers incoming text tokens and emits "TTS-ready" chunks when either:
  - the buffer ends with sentence/clause-final punctuation (. ! ? , ; :)
  - the buffer reaches the word-count threshold (default 6 words)
  - explicit `flush()` is called (end of stream)

The goal is to keep the agent's reply flowing into the TTS provider in
phrase-sized units so the synthesized audio is natural-sounding while
still hitting the < 300 ms first-frame latency budget.

The chunker is provider-agnostic and stateful per session. It does NOT
talk to the TTS WS itself; it returns chunks for the adapter to send.
"""
from __future__ import annotations

import re

# Spanish punctuation that ends a clause (no ellipsis, no opening ¿/¡).
_CLAUSE_END = re.compile(r"[\.\!\?\,;:](\s|$)")
_OPENING = ("¿", "¡")


class PhraseChunker:
    def __init__(self, min_words: int = 6, max_chars: int = 240) -> None:
        self.min_words = min_words
        self.max_chars = max_chars
        self._buf = ""

    def feed(self, token: str) -> list[str]:
        """Append `token` to the buffer; return any chunks ready to send.
        Multiple chunks can be returned in one call when the token contains
        more than one punctuation mark."""
        if not token:
            return []
        self._buf += token
        out: list[str] = []
        while True:
            chunk = self._take_one()
            if chunk is None:
                return out
            out.append(chunk)

    def flush(self) -> str:
        """Return whatever's in the buffer (may be empty). Resets the buffer."""
        rest = self._buf.strip()
        self._buf = ""
        return rest

    def reset(self) -> None:
        """Drop any pending text (used by `flush()` for barge-in)."""
        self._buf = ""

    # ---- internals ----
    def _take_one(self) -> str | None:
        """Take the next chunk if one is ready; else None."""
        buf = self._buf
        if not buf.strip():
            return None

        # 1. Sentence/clause-final punctuation.
        m = _CLAUSE_END.search(buf)
        if m:
            end = m.end()
            chunk = buf[:end].strip()
            self._buf = buf[end:]
            if chunk:
                return chunk

        # 2. Reached word-count threshold.
        words = buf.split()
        if len(words) >= self.min_words:
            # Break after the (min_words)-th word, keep remainder.
            # Compute byte offset of the break point in the original buffer
            # to preserve trailing whitespace correctly.
            count = 0
            i = 0
            while i < len(buf) and count < self.min_words:
                # Skip whitespace
                while i < len(buf) and buf[i].isspace():
                    i += 1
                # Read word
                while i < len(buf) and not buf[i].isspace():
                    i += 1
                count += 1
            chunk = buf[:i].strip()
            self._buf = buf[i:]
            if chunk:
                return chunk

        # 3. Buffer is too long even without punctuation.
        if len(buf) >= self.max_chars:
            chunk = buf[: self.max_chars].strip()
            self._buf = buf[self.max_chars:]
            if chunk:
                return chunk

        return None
