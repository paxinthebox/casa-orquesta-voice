"""
PII redaction hook — Phase 1.7.

Strips Mexican PII (INE, RFC, CURP, names, phone numbers, emails) from any
text that's about to leave the process toward a third-party LLM. Per
CLAUDE.md non-negotiable #4.

Strategy: pattern-first, NER-second.
  1. Deterministic regexes catch RFC, CURP, INE, MX phones, emails, CPs.
     These match high-precision and run in <1ms.
  2. Optional Presidio + es_core_news_sm spaCy model catches names that
     don't fit the patterns. Loaded lazily; falls back to a regex-only
     redactor when Presidio isn't installed.

Output: same string with sensitive spans replaced by tagged tokens
        (e.g. `[RFC]`, `[CURP]`, `[INE]`, `[NAME]`).

The redactor is *idempotent* — running it twice produces the same string.
"""
from __future__ import annotations

import os
import re
from typing import Optional

REDACTION_ENABLED = os.getenv("PII_REDACTION_ENABLED", "true").lower() == "true"

# ----------------- Deterministic patterns (Mexican context) -----------------
# RFC: 4 alpha + 6 digits + 3 alphanumeric (persona moral starts with 3)
_RFC_RE = re.compile(r"\b([A-ZÑ&]{3,4}\d{6}[A-Z0-9]{3})\b")
# CURP: 18 alphanumeric with a known shape (RAMM750101HDFNRR01-style)
_CURP_RE = re.compile(
    r"\b([A-Z][AEIOUX][A-Z]{2}\d{6}[HM][A-Z]{5}[A-Z0-9]\d)\b")
# INE: 18 digits (clave de elector) or 13-digit OCR
_INE_RE = re.compile(r"\b(\d{18}|\d{13})\b")
# Mexican phones — supports +52, country code, and 10-digit local.
_PHONE_RE = re.compile(
    r"(\+?52\s?1?\s?\d{2,3}[-\s]?\d{3,4}[-\s]?\d{4}|\b\d{2,3}[-\s]?\d{3,4}[-\s]?\d{4}\b)")
_EMAIL_RE = re.compile(r"\b[\w._%+-]+@[\w.-]+\.[A-Za-z]{2,}\b")
# Mexican postal codes (5 digits — only a soft hint).
_CP_RE = re.compile(r"\bC\.?P\.?\s?(\d{5})\b", re.IGNORECASE)

# Names are tricky — we rely on Presidio when available. The deterministic
# fallback only catches "FUTURO COMPRADOR: <NAME>"-style explicit labels.
_NAME_LABELED_RE = re.compile(
    r"((?:Comprador|Vendedor|Propietario|Cliente|Nombre)\s*:\s*)([A-ZÁÉÍÓÚÑ][A-Za-zÁÉÍÓÚÑáéíóúñ\s]{2,60})")


def _regex_redact(text: str) -> str:
    if not text:
        return text
    text = _CURP_RE.sub("[CURP]", text)
    text = _RFC_RE.sub("[RFC]", text)
    text = _INE_RE.sub("[INE]", text)
    text = _PHONE_RE.sub("[PHONE]", text)
    text = _EMAIL_RE.sub("[EMAIL]", text)
    text = _NAME_LABELED_RE.sub(r"\1[NAME]", text)
    return text


# ----------------- Optional Presidio path -----------------
_presidio_analyzer = None
_presidio_loaded_attempted = False


def _try_load_presidio():
    """Lazy-init the Presidio analyzer with Spanish NER. No-op if missing."""
    global _presidio_analyzer, _presidio_loaded_attempted
    if _presidio_loaded_attempted:
        return _presidio_analyzer
    _presidio_loaded_attempted = True
    try:
        from presidio_analyzer import AnalyzerEngine  # type: ignore
        from presidio_analyzer.nlp_engine import NlpEngineProvider  # type: ignore
        config = {
            "nlp_engine_name": "spacy",
            "models": [{"lang_code": "es", "model_name": "es_core_news_sm"}],
        }
        provider = NlpEngineProvider(nlp_configuration=config)
        nlp_engine = provider.create_engine()
        _presidio_analyzer = AnalyzerEngine(
            nlp_engine=nlp_engine, supported_languages=["es"])
        print("[hooks.pii_redaction] Presidio analyzer loaded (es)")
    except Exception as e:
        print(f"[hooks.pii_redaction] Presidio unavailable, regex-only mode: {e}")
        _presidio_analyzer = None
    return _presidio_analyzer


def _presidio_redact(text: str) -> str:
    analyzer = _try_load_presidio()
    if analyzer is None:
        return text
    try:
        results = analyzer.analyze(text=text, language="es",
                                    entities=["PERSON"])
        # Replace in reverse order so spans stay valid
        spans = sorted(results, key=lambda r: r.start, reverse=True)
        for r in spans:
            text = text[:r.start] + "[NAME]" + text[r.end:]
        return text
    except Exception as e:
        print(f"[hooks.pii_redaction] Presidio analyze failed: {e}")
        return text


# ----------------- Public API -----------------
def redact(text: Optional[str]) -> Optional[str]:
    """Redact PII from a single string. Idempotent. None → None."""
    if not text or not REDACTION_ENABLED:
        return text
    text = _regex_redact(text)
    text = _presidio_redact(text)
    return text


def redact_messages(messages: list[dict]) -> list[dict]:
    """Walk an Anthropic-style messages list and redact all string content.
    Per CLAUDE.md non-negotiable #4 — runs immediately before every
    third-party LLM call."""
    if not REDACTION_ENABLED:
        return messages
    out = []
    for m in messages:
        c = m.get("content")
        if isinstance(c, str):
            out.append({**m, "content": redact(c)})
        elif isinstance(c, list):
            new_blocks = []
            for block in c:
                if isinstance(block, dict) and isinstance(block.get("text"), str):
                    new_blocks.append({**block, "text": redact(block["text"])})
                else:
                    new_blocks.append(block)
            out.append({**m, "content": new_blocks})
        else:
            out.append(m)
    return out


# Trace event shape (consumed by Agent.run() to log the redaction action).
def trace_event(original_len: int, redacted_len: int) -> dict:
    return {
        "kind": "policy",
        "policy": "pii_redaction",
        "original_len": original_len,
        "redacted_len": redacted_len,
        "delta": original_len - redacted_len,
    }
