"""
Casa·Orquesta — Hooks package.

Four hooks per CLAUDE.md non-negotiables #4 (PII redaction), #5 (spend caps),
#6 (audit log), plus a trace emitter that bridges Agent.run() events to
Langfuse / mobile SessionTrace.

Each module exports:
  - A module-level configuration loaded from env at import time.
  - A `policy.*` callable the Agent.run() loop invokes at the right point.
  - Graceful degradation when its backend isn't configured (Redis missing,
    S3 unset, Presidio not installed). The hook becomes a no-op but logs.

CLAUDE.md non-negotiables this package enforces:
  #4 — PII redaction runs before every third-party LLM call.
  #5 — Per-tenant per-day spend caps. Default $5/day.
  #6 — Audit log entries are append-only and tamper-evident.
"""
from . import audit_log, pii_redaction, spend_caps, trace, prompt_guard  # noqa: F401

__all__ = ["audit_log", "pii_redaction", "spend_caps", "trace", "prompt_guard"]
