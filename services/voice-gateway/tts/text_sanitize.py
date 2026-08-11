"""Expand abbreviations so TTS reads es-MX replies naturally."""
from __future__ import annotations

import re

# $2.2 MDP / $2.2M → spoken millions
_MDP_RE = re.compile(
    r"\$\s*([\d]+(?:[.,]\d+)?)\s*(?:MDP|M\b|millones?)?",
    re.IGNORECASE,
)
_M2_RE = re.compile(r"(\d+(?:[.,]\d+)?)\s*m²", re.IGNORECASE)
_M2_WORD_RE = re.compile(r"\bm2\b", re.IGNORECASE)


def sanitize_for_tts(text: str) -> str:
    """Make orchestrator reply text speakable (MDP, m², symbols)."""
    if not text:
        return text

    def _mdp(m: re.Match) -> str:
        raw = m.group(1).replace(",", ".")
        try:
            val = float(raw)
        except ValueError:
            return m.group(0)
        if val == int(val):
            return f"{int(val)} millones de pesos"
        whole = int(val)
        frac = round((val - whole) * 10)
        if frac:
            return f"{whole} punto {frac} millones de pesos"
        return f"{whole} millones de pesos"

    out = _MDP_RE.sub(_mdp, text)
    out = _M2_RE.sub(r"\1 metros cuadrados", out)
    out = _M2_WORD_RE.sub("metros cuadrados", out)
    out = out.replace("CDMX", "Ciudad de México")
    out = out.replace("INFONAVIT", "Infonavit")
    out = out.replace("FOVISSSTE", "Fovissste")
    return out
