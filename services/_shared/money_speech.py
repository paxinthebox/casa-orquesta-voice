"""
Normalize spoken Spanish (es-MX) money into standard $MXN strings.

Used by voice-gateway STT callbacks and orchestrator query_parse so budgets
like "cuarenta mil al mes" become "$40,000/mes" and "ocho millones" becomes
"$8,000,000".
"""
from __future__ import annotations

import re

_ONES: dict[str, int] = {
    "un": 1, "uno": 1, "una": 1, "dos": 2, "tres": 3, "cuatro": 4,
    "cinco": 5, "seis": 6, "siete": 7, "ocho": 8, "nueve": 9, "diez": 10,
    "once": 11, "doce": 12, "trece": 13, "catorce": 14, "quince": 15,
    "dieciséis": 16, "dieciseis": 16, "diecisiete": 17, "dieciocho": 18,
    "diecinueve": 19,
}
_TENS: dict[str, int] = {
    "veinte": 20, "veintiuno": 21, "veintidós": 22, "veintidos": 22,
    "veintitrés": 23, "veintitres": 23, "veinticuatro": 24, "veinticinco": 25,
    "veintiséis": 26, "veintiseis": 26, "veintisiete": 27, "veintiocho": 28,
    "veintinueve": 29, "treinta": 30, "cuarenta": 40, "cincuenta": 50,
    "sesenta": 60, "setenta": 70, "ochenta": 80, "noventa": 90,
}
_HUNDREDS: dict[str, int] = {
    "cien": 100, "ciento": 100, "doscientos": 200, "doscientas": 200,
    "trescientos": 300, "trescientas": 300, "cuatrocientos": 400,
    "cuatrocientas": 400, "quinientos": 500, "quinientas": 500,
    "seiscientos": 600, "seiscientas": 600, "setecientos": 700,
    "setecientas": 700, "ochocientos": 800, "ochocientas": 800,
    "novecientos": 900, "novecientas": 900,
}


def format_mxn(amount: int, *, monthly: bool = False) -> str:
    """Standard display: $40,000 or $40,000/mes."""
    if amount <= 0:
        return ""
    text = f"${amount:,}"
    return f"{text}/mes" if monthly else text


def _parse_number_words(phrase: str) -> int | None:
    phrase = phrase.strip().lower()
    if not phrase:
        return None
    if phrase.isdigit():
        return int(phrase)
    phrase = phrase.replace("-", " ")
    if " y " in phrase:
        left, right = phrase.split(" y ", 1)
        lv = _parse_number_words(left)
        rv = _parse_number_words(right)
        if lv is not None and rv is not None:
            return lv + rv
        return None
    tokens = phrase.split()
    if len(tokens) == 1:
        tok = tokens[0]
        if tok in _ONES:
            return _ONES[tok]
        if tok in _TENS:
            return _TENS[tok]
        if tok in _HUNDREDS:
            return _HUNDREDS[tok]
        m = re.match(r"(\d+(?:[.,]\d+)?)", tok)
        if m:
            return int(float(m.group(1).replace(",", ".")))
    # "dos cientos" style compounds — best-effort sum of tokens
    total = 0
    for tok in tokens:
        if tok in _HUNDREDS:
            total += _HUNDREDS[tok]
        elif tok in _TENS:
            total += _TENS[tok]
        elif tok in _ONES:
            total += _ONES[tok]
        else:
            return None
    return total if total > 0 else None


def _parse_numeric_token(raw: str) -> int | None:
    raw = raw.strip().replace(",", "")
    if not raw:
        return None
    try:
        val = float(raw)
    except ValueError:
        return _parse_number_words(raw)
    return int(val)


def _is_monthly_context(full_text: str, start: int, end: int) -> bool:
    window = full_text[max(0, start - 20): min(len(full_text), end + 40)].lower()
    return any(h in window for h in ("al mes", "por mes", "mensual", "/mes", "mensuales"))


def normalize_money_in_text(text: str) -> str:
    """Replace spoken money phrases with $MXN strings (digits preserved)."""
    if not text or not text.strip():
        return text

    word = (
        r"(?:un|uno|una|dos|tres|cuatro|cinco|seis|siete|ocho|nueve|diez|once|doce|"
        r"trece|catorce|quince|diecis[eé]is|diecisiete|dieciocho|diecinueve|"
        r"veinte|veinti\w+|treinta|cuarenta|cincuenta|sesenta|setenta|ochenta|noventa|"
        r"cien|ciento|doscient\w+|trescient\w+|cuatrocient\w+|quinient\w+|"
        r"seiscient\w+|setecient\w+|ochocient\w+|novecient\w+)"
    )
    compound = rf"(?:{word}(?:\s+y\s+{word})?)"
    number = rf"(?P<num>\d+(?:[.,]\d+)?|{compound})"

    def _replace_millions(match: re.Match[str]) -> str:
        raw_num = match.group("num")
        amount = _parse_numeric_token(raw_num)
        if amount is None:
            return match.group(0)
        total = int(amount * 1_000_000)
        monthly = _is_monthly_context(text, match.start(), match.end())
        return format_mxn(total, monthly=monthly)

    def _replace_mdp(match: re.Match[str]) -> str:
        raw_num = match.group("num")
        amount = _parse_numeric_token(raw_num)
        if amount is None:
            return match.group(0)
        total = int(amount * 1_000_000)
        return format_mxn(total, monthly=False)

    def _replace_thousands(match: re.Match[str]) -> str:
        raw_num = match.group("num")
        amount = _parse_numeric_token(raw_num)
        if amount is None:
            return match.group(0)
        total = int(amount * 1_000)
        monthly = _is_monthly_context(text, match.start(), match.end())
        return format_mxn(total, monthly=monthly)

    out = text
    out = re.sub(rf"\b{number}\s*millones?\b", _replace_millions, out, flags=re.IGNORECASE)
    out = re.sub(
        rf"\b{number}\s*(?:mdp|m\.?d\.?p\.?)\b",
        _replace_mdp,
        out,
        flags=re.IGNORECASE,
    )
    out = re.sub(rf"\b{number}\s*mil\b", _replace_thousands, out, flags=re.IGNORECASE)
    return out


_WORD_NUM = (
    r"(?:un|uno|una|dos|tres|cuatro|cinco|seis|siete|ocho|nueve|diez|once|doce|"
    r"trece|catorce|quince|diecis[eé]is|diecisiete|dieciocho|diecinueve|"
    r"veinte|veinti\w+|treinta|cuarenta|cincuenta|sesenta|setenta|ochenta|noventa|"
    r"cien|ciento|doscient\w+|trescient\w+|cuatrocient\w+|quinient\w+|"
    r"seiscient\w+|setecient\w+|ochocient\w+|novecient\w+)"
)
_WORD_COMPOUND = rf"(?:{_WORD_NUM}(?:\s+y\s+{_WORD_NUM})?)"


def _extract_compound_millones_mil(raw: str) -> int | None:
    """
    Spoken sale budgets like ``4 millones novecientos mil`` → 4_900_000.
    """
    pattern = (
        rf"(?P<millions>\d+(?:\.\d+)?|{_WORD_NUM})\s*millones?\s+"
        rf"(?P<thousands>\d+(?:\.\d+)?|{_WORD_COMPOUND})\s*mil\b"
    )
    m = re.search(pattern, raw, flags=re.IGNORECASE)
    if not m:
        return None
    millions = _parse_numeric_token(m.group("millions"))
    thousands = _parse_numeric_token(m.group("thousands"))
    if millions is None or thousands is None:
        return None
    return int(millions * 1_000_000 + thousands * 1_000)


def _sum_budget_phrase_amounts(normalized: str) -> int | None:
    """
    After normalization, ``hasta $4,000,000 $900,000`` is one compound budget.
    """
    m = re.search(
        r"(?:hasta|presupuesto|valor de|m[aá]ximo de|tope de)\s+((?:\$[\d,]+(?:/mes)?\s*)+)",
        normalized,
        flags=re.IGNORECASE,
    )
    if not m:
        return None
    parts = [
        int(x.replace(",", ""))
        for x in re.findall(r"\$\s*([\d,]+)", m.group(1))
    ]
    if not parts:
        return None
    if len(parts) == 1:
        return parts[0]
    if len(parts) >= 2 and all(p < 1_000_000 for p in parts[1:]):
        return sum(parts)
    return max(parts)


def _strip_budget_analysis_section(message: str) -> str:
    """Drop formulary 'Análisis de presupuesto: …' so nested $ amounts don't win."""
    return re.sub(
        r"an[aá]lisis\s+de\s+presupuesto\s*:.*?(?=(?:seguimiento\s+crediticio|notas\s+adicionales|$))",
        " ",
        message,
        flags=re.IGNORECASE | re.DOTALL,
    )


def extract_price_max_mxn(message: str, *, listing_mode: str | None) -> int | None:
    """
    Parse max budget from user text.

    Rent → monthly cap only (ignores bare "millones" unless "al mes").
    Sale → total price (millones/mdp/mil).
    """
    cleaned = _strip_budget_analysis_section(message)
    normalized = normalize_money_in_text(cleaned)
    msg = normalized.lower()
    raw = cleaned.lower()
    is_rent = listing_mode == "rent"

    if not is_rent:
        compound = _extract_compound_millones_mil(raw)
        if compound:
            return compound
        m_dec = re.search(
            r"(\d+(?:\.\d+)?)\s*(?:millon|millones|mdp|m\.?d\.?p\.?)\b",
            raw,
        )
        if m_dec:
            return int(float(m_dec.group(1)) * 1_000_000)
        summed = _sum_budget_phrase_amounts(normalized)
        if summed:
            return summed

    # Normalized $ amounts from speech normalization
    for m in re.finditer(r"\$\s*([\d,]+)(?:/mes)?", normalized):
        try:
            val = int(m.group(1).replace(",", ""))
            if val <= 0:
                continue
            if is_rent and val >= 1_000_000 and "/mes" not in m.group(0):
                continue
            return val
        except ValueError:
            continue

    if is_rent:
        m = re.search(
            r"(\d{1,3}(?:,\d{3})+|\d+)\s*pesos?\s*(?:al\s+mes|mens\w*|por\s+mes)",
            raw,
        )
        if m:
            return int(m.group(1).replace(",", ""))
        m = re.search(
            r"(\d+(?:\.\d+)?)\s*millones?\s*(?:al\s+mes|mensual|por\s+mes)",
            raw,
        )
        if m:
            return int(float(m.group(1)) * 1_000_000)
        m = re.search(r"(\d+(?:\.\d+)?)\s*mil\b", msg)
        if m:
            return int(float(m.group(1)) * 1_000)
        m = re.search(r"\$\s*([\d,]+)/mes\b", normalized)
        if m:
            try:
                return int(m.group(1).replace(",", ""))
            except ValueError:
                pass
        return None

    m = re.search(r"(\d+(?:\.\d+)?)\s*(?:millon|millones|mdp|m\.?d\.?p\.?)\b", msg)
    if m:
        return int(float(m.group(1)) * 1_000_000)
    m = re.search(r"(\d+(?:\.\d+)?)\s*mil\b", msg)
    if m:
        return int(float(m.group(1)) * 1_000)
    m = re.search(r"\$?\s*([\d,]{4,})", normalized)
    if m:
        try:
            return int(m.group(1).replace(",", ""))
        except ValueError:
            pass
    return None


def infer_listing_mode(message: str) -> str | None:
    msg = message.lower()
    short_term = any(
        w in msg
        for w in (
            "airbnb", "vacacional", "por noche", "por día", "por dia",
            "temporal", "corta estancia", "short term", "short-term",
        )
    )
    if any(
        w in msg
        for w in (
            "renta", "renta anual", "alquiler", "arrendar", "arrendamiento",
            "for rent", "to rent", "mensual", "al mes", "por mes",
        )
    ) and not short_term:
        return "rent"
    if any(
        w in msg
        for w in ("venta", "comprar", "compra", "en venta", "for sale", "to buy")
    ):
        return "sale"
    return None
