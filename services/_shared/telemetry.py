"""
Shared telemetry — Phase 4.7.

Three things every service needs and would otherwise reimplement
inconsistently:

  1. Structured JSON logging with a stable schema (request_id,
     tenant_id, user_id, service, action, latency_ms, …).
  2. Per-tenant cost ledger — every Anthropic/Deepgram/ElevenLabs
     call records `(tenant_id, model, in_tokens, out_tokens, usd)`
     into a process-local accumulator that the cost-dashboard cron
     drains.
  3. Langfuse trace wrapper that becomes a no-op when LANGFUSE_*
     env vars aren't set (which is the standalone-test path).

The module is import-safe in any environment — every external dep is
lazy. If Langfuse + structlog aren't installed, logging falls back to
plain `json.dumps(...)` on stdout. That keeps the test runners
hermetic and gives the production deploy a clean upgrade path.

Public API:

    log = get_logger(service="orchestrator")
    log.info("agent.run", tenant_id=..., user_id=..., agents=["realestate"])

    with trace_span(name="agent.run", tenant_id=..., user_id=...) as span:
        ...
        span.record_tokens(model="claude-sonnet-4-6",
                           in_tokens=420, out_tokens=110)

    record_vendor_cost(
        tenant_id="tnt_x", vendor="anthropic",
        model="claude-sonnet-4-6",
        in_tokens=420, out_tokens=110, usd=0.0021,
    )

    summary = ledger_snapshot()   # → list of cost rows; cron writes to DB

The ledger is intentionally in-memory only; durability is the cron's
job (it reads + clears every minute and writes to Postgres). That
keeps the hot path lock-free.
"""
from __future__ import annotations

import contextlib
import json
import os
import sys
import threading
import time
import uuid
from dataclasses import asdict, dataclass, field
from typing import Any, Iterator, Optional


SERVICE_NAME_FALLBACK = os.getenv("CO_SERVICE_NAME", "unknown")
LOG_LEVEL = os.getenv("CO_LOG_LEVEL", "INFO").upper()


# ===========================================================================
# Structured logger
# ===========================================================================
_LEVELS = {"DEBUG": 10, "INFO": 20, "WARN": 30, "WARNING": 30,
           "ERROR": 40, "FATAL": 50}


class _StructLogger:
    """JSON-line logger writing to stdout. Stays lock-free per call by
    writing one `print()` per event; cPython's print holds the GIL for
    the duration of the write so partial lines don't interleave."""

    def __init__(self, *, service: str, level: str = LOG_LEVEL):
        self.service = service
        self.level = _LEVELS.get(level, 20)

    def _emit(self, lvl: str, event: str, kwargs: dict) -> None:
        if _LEVELS.get(lvl, 0) < self.level:
            return
        record = {
            "ts": time.time(),
            "level": lvl,
            "service": self.service,
            "event": event,
        }
        # Don't let callers shadow the schema columns.
        for k, v in kwargs.items():
            if k not in record:
                record[k] = v
        sys.stdout.write(json.dumps(record, default=str,
                                    ensure_ascii=False) + "\n")
        sys.stdout.flush()

    def debug(self, event: str, **kw: Any) -> None: self._emit("DEBUG", event, kw)
    def info(self, event: str, **kw: Any) -> None:  self._emit("INFO",  event, kw)
    def warn(self, event: str, **kw: Any) -> None:  self._emit("WARN",  event, kw)
    def error(self, event: str, **kw: Any) -> None: self._emit("ERROR", event, kw)


_logger_cache: dict[str, _StructLogger] = {}


def get_logger(*, service: Optional[str] = None,
               level: str = LOG_LEVEL) -> _StructLogger:
    name = service or SERVICE_NAME_FALLBACK
    if name not in _logger_cache:
        _logger_cache[name] = _StructLogger(service=name, level=level)
    return _logger_cache[name]


# ===========================================================================
# Request-id propagation
# ===========================================================================
def new_request_id() -> str:
    """Generate a request id. ULID-ish — 8 hex chars of time + 16 random."""
    return f"req_{int(time.time() * 1000):011x}_{uuid.uuid4().hex[:12]}"


# ===========================================================================
# Trace spans (Langfuse-compatible interface, no-op when unconfigured)
# ===========================================================================
@dataclass
class _Span:
    """Tiny span recorder. Records tokens + latency; emits a JSON log on
    close and (optionally) forwards to Langfuse when available."""
    name: str
    tenant_id: Optional[str] = None
    user_id: Optional[str] = None
    request_id: Optional[str] = None
    service: str = SERVICE_NAME_FALLBACK
    started_at: float = field(default_factory=time.monotonic)
    tokens_in: int = 0
    tokens_out: int = 0
    model: Optional[str] = None
    metadata: dict = field(default_factory=dict)

    def record_tokens(self, *, model: str, in_tokens: int,
                      out_tokens: int) -> None:
        self.model = model
        self.tokens_in += in_tokens
        self.tokens_out += out_tokens

    def set(self, **kv: Any) -> None:
        self.metadata.update(kv)

    def close(self) -> None:
        latency_ms = int((time.monotonic() - self.started_at) * 1000)
        log = get_logger(service=self.service)
        log.info(
            "trace.span",
            name=self.name,
            tenant_id=self.tenant_id,
            user_id=self.user_id,
            request_id=self.request_id,
            latency_ms=latency_ms,
            tokens_in=self.tokens_in,
            tokens_out=self.tokens_out,
            model=self.model,
            **self.metadata,
        )
        _langfuse_emit(self, latency_ms)


@contextlib.contextmanager
def trace_span(
    name: str,
    *,
    tenant_id: Optional[str] = None,
    user_id: Optional[str] = None,
    request_id: Optional[str] = None,
    service: Optional[str] = None,
    **metadata: Any,
) -> Iterator[_Span]:
    span = _Span(
        name=name,
        tenant_id=tenant_id,
        user_id=user_id,
        request_id=request_id or new_request_id(),
        service=service or SERVICE_NAME_FALLBACK,
        metadata=dict(metadata),
    )
    try:
        yield span
    finally:
        span.close()


def _langfuse_emit(span: _Span, latency_ms: int) -> None:
    """Forward to Langfuse if installed + configured. Best-effort — a
    failed Langfuse call must NOT take down the request."""
    if not os.getenv("LANGFUSE_PUBLIC_KEY"):
        return
    try:

        client = _langfuse_singleton()
        if client is None:
            return
        client.trace(
            name=span.name,
            user_id=span.user_id,
            metadata={
                "tenant_id": span.tenant_id,
                "service": span.service,
                "model": span.model,
                "tokens_in": span.tokens_in,
                "tokens_out": span.tokens_out,
                "latency_ms": latency_ms,
                **span.metadata,
            },
        )
    except Exception as e:                                     # pragma: no cover
        get_logger(service=span.service).warn(
            "langfuse.emit_failed", error=str(e), name=span.name,
        )


_langfuse_client: Any = None
_langfuse_lock = threading.Lock()


def _langfuse_singleton() -> Any:
    global _langfuse_client
    if _langfuse_client is not None:
        return _langfuse_client
    with _langfuse_lock:
        if _langfuse_client is not None:
            return _langfuse_client
        try:
            from langfuse import Langfuse                      # type: ignore
            _langfuse_client = Langfuse(
                public_key=os.getenv("LANGFUSE_PUBLIC_KEY", ""),
                secret_key=os.getenv("LANGFUSE_SECRET_KEY", ""),
                host=os.getenv("LANGFUSE_HOST",
                               "https://cloud.langfuse.com"),
            )
        except Exception:
            _langfuse_client = None
    return _langfuse_client


# ===========================================================================
# Per-tenant cost ledger
# ===========================================================================
@dataclass
class CostEntry:
    ts: float
    tenant_id: str
    vendor: str          # "anthropic" | "deepgram" | "elevenlabs" | "azure" | …
    model: Optional[str]
    in_tokens: int
    out_tokens: int
    usd: float
    request_id: Optional[str] = None
    service: str = SERVICE_NAME_FALLBACK

    def as_dict(self) -> dict:
        return asdict(self)


_ledger: list[CostEntry] = []
_ledger_lock = threading.Lock()


def record_vendor_cost(
    *,
    tenant_id: str,
    vendor: str,
    model: Optional[str] = None,
    in_tokens: int = 0,
    out_tokens: int = 0,
    usd: float = 0.0,
    request_id: Optional[str] = None,
    service: Optional[str] = None,
) -> None:
    """Append a cost line. Called by the orchestrator's API-call wrapper
    after every vendor invocation."""
    entry = CostEntry(
        ts=time.time(),
        tenant_id=tenant_id, vendor=vendor, model=model,
        in_tokens=int(in_tokens), out_tokens=int(out_tokens),
        usd=float(usd),
        request_id=request_id,
        service=service or SERVICE_NAME_FALLBACK,
    )
    with _ledger_lock:
        _ledger.append(entry)
    get_logger(service=entry.service).info(
        "cost.recorded",
        tenant_id=tenant_id, vendor=vendor, model=model,
        in_tokens=in_tokens, out_tokens=out_tokens, usd=usd,
        request_id=request_id,
    )


def ledger_snapshot(*, drain: bool = False) -> list[dict]:
    """Return a copy of the current cost rows. If drain=True, clear the
    in-memory ledger atomically — that's what the per-minute cron uses."""
    with _ledger_lock:
        rows = [e.as_dict() for e in _ledger]
        if drain:
            _ledger.clear()
    return rows


def ledger_summary_by_tenant() -> dict:
    """Aggregate the current ledger by (tenant_id, vendor). Handy for
    ad-hoc dashboards + the runbook's `cost/tenant` rollup."""
    rows = ledger_snapshot()
    out: dict[tuple[str, str], dict] = {}
    for r in rows:
        key = (r["tenant_id"], r["vendor"])
        if key not in out:
            out[key] = {
                "tenant_id": r["tenant_id"], "vendor": r["vendor"],
                "in_tokens": 0, "out_tokens": 0, "usd": 0.0,
                "calls": 0,
            }
        out[key]["in_tokens"] += int(r["in_tokens"])
        out[key]["out_tokens"] += int(r["out_tokens"])
        out[key]["usd"] += float(r["usd"])
        out[key]["calls"] += 1
    return {
        "by_tenant_vendor": list(out.values()),
        "totals": {
            "calls": sum(v["calls"] for v in out.values()),
            "usd": round(sum(v["usd"] for v in out.values()), 4),
        },
    }


# ===========================================================================
# Convenience: vendor → USD estimator
# ===========================================================================
# Approximate rate cards (update when vendors change pricing). These are
# the rates the orchestrator's spend cap uses. Values are USD per 1k
# tokens for LLMs, USD per minute for audio.
RATE_CARDS = {
    # Anthropic — Claude rate card as of May 2026
    "anthropic": {
        "claude-sonnet-4-6":   {"in_per_1k": 0.003,    "out_per_1k": 0.015},
        "claude-haiku-4-5":    {"in_per_1k": 0.0008,   "out_per_1k": 0.004},
        "claude-opus-4-6":     {"in_per_1k": 0.015,    "out_per_1k": 0.075},
    },
    # Deepgram — Nova-2 streaming
    "deepgram": {
        "nova-2": {"per_minute": 0.0043},
    },
    # ElevenLabs Flash — pay-as-you-go
    "elevenlabs": {
        "flash_v2_5": {"per_1k_chars": 0.30},
    },
    # Azure Neural Spanish — significantly cheaper
    "azure": {
        "es-MX-DaliaNeural": {"per_1k_chars": 0.016},
    },
}


def estimate_usd(
    vendor: str,
    *,
    model: Optional[str] = None,
    in_tokens: int = 0,
    out_tokens: int = 0,
    audio_minutes: float = 0.0,
    chars: int = 0,
) -> float:
    """Quick rate-card estimator. Pricing is approximate — for billing
    we still defer to the vendor invoice."""
    card = RATE_CARDS.get(vendor, {})
    if not card:
        return 0.0
    row = card.get(model or list(card.keys())[0], {})
    if "in_per_1k" in row:
        return round(
            (in_tokens / 1000.0) * row["in_per_1k"]
            + (out_tokens / 1000.0) * row["out_per_1k"],
            6,
        )
    if "per_minute" in row:
        return round(audio_minutes * row["per_minute"], 6)
    if "per_1k_chars" in row:
        return round((chars / 1000.0) * row["per_1k_chars"], 6)
    return 0.0
