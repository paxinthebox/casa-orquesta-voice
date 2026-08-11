"""
Prompt guard hook — blocks obvious injection / jailbreak attempts before LLM calls.

Runs on depth-0 (user-facing) turns in Agent.run(). Complements the system-prompt
guardrails in agents/guardrails.py; this layer is deterministic and auditable.
"""
from __future__ import annotations

import os
import re

GUARD_ENABLED = os.getenv("PROMPT_GUARD_ENABLED", "true").lower() == "true"
MAX_USER_CHARS = int(os.getenv("PROMPT_GUARD_MAX_CHARS", "4000"))

REFUSAL_MESSAGE = (
    "Solo puedo ayudarte con propiedades, compradores, agentes y auditoría "
    "inmobiliaria en CDMX y Morelos. ¿Qué zona o presupuesto te interesa?"
)

_INJECTION_PATTERNS: tuple[re.Pattern[str], ...] = tuple(
    re.compile(p, re.IGNORECASE)
    for p in (
        r"ignore\s+(all\s+)?(previous|prior|above)\s+instructions",
        r"ignora\s+(todas?\s+)?(las\s+)?instrucciones",
        r"olvida\s+(tus\s+)?instrucciones",
        r"disregard\s+(your\s+)?(rules|instructions|guidelines)",
        r"system\s*prompt",
        r"prompt\s+del\s+sistema",
        r"reveal\s+(your\s+)?(instructions|system|prompt|tools?)",
        r"mu[eé]stra(me\s+)?(el\s+)?(prompt|instrucciones|system)",
        r"jailbreak|DAN\s+mode|modo\s+sin\s+restricciones",
        r"act\s+as\s+if\s+you\s+(have\s+)?no\s+(rules|restrictions)",
        r"developer\s+mode|modo\s+desarrollador",
        r"\bAPI[\s_-]?KEY\b|\bANTHROPIC_API\b|\bOPENAI_API\b",
        r"secret(os?)?\s+de\s+(la\s+)?(app|api|servidor)",
        r"ejecuta(r|)\s+(c[oó]digo|python|shell|sql|bash)",
        r"<\/?script\b|javascript\s*:",
        r"you\s+are\s+now\s+(in\s+)?(unrestricted|admin)\s+mode",
    )
)


def evaluate(text: str | None) -> dict:
    """Return {allow: bool, reason?, response?} for an inbound user message."""
    if not GUARD_ENABLED or not text or not text.strip():
        return {"allow": True}

    if len(text) > MAX_USER_CHARS:
        return {
            "allow": False,
            "reason": "message_too_long",
            "response": REFUSAL_MESSAGE,
        }

    for pattern in _INJECTION_PATTERNS:
        if pattern.search(text):
            return {
                "allow": False,
                "reason": "injection_pattern",
                "pattern": pattern.pattern,
                "response": REFUSAL_MESSAGE,
            }

    return {"allow": True}


def trace_event(decision: dict) -> dict:
    payload = {k: v for k, v in decision.items() if k != "response"}
    return {"kind": "policy", "policy": "prompt_guard", **payload}
