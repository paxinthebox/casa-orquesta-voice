"""
Spend caps hook — Phase 1.7.

Per CLAUDE.md non-negotiable #5. Tracks per-tenant per-day token cost.
When a tenant exceeds `SPEND_CAP_USD_PER_DAY` (default $5), the orchestrator
flips to SLM-only / simulated mode for the rest of the UTC day.

Backend: Redis when REDIS_URL is set; in-memory dict fallback. The fallback
is per-process so a multi-worker deployment NEEDS Redis; this is logged.

Default per-million-token prices (USD), tunable via env:
  Sonnet 4.6:   input $3.00 / output $15.00
  Haiku 4.5:    input $0.80 / output $4.00

This module is read by Agent.run() before each `messages.create()` call
(via `should_call_llm()`) and again after the response to record cost
(via `record_usage()`).
"""
from __future__ import annotations

import os
from datetime import datetime, timezone
from typing import Optional

DEFAULT_CAP = float(os.getenv("SPEND_CAP_USD_PER_DAY", "5.0"))
REDIS_URL = os.getenv("REDIS_URL", "")
REDIS_NS = os.getenv("REDIS_NAMESPACE", "casa:spend")

PRICE_TABLE = {
    # ($/M input, $/M output)
    "claude-sonnet-4-6":        (3.00, 15.00),
    "claude-sonnet-4-5":        (3.00, 15.00),
    "claude-haiku-4-5":         (0.80,  4.00),
    "claude-haiku-4-5-20251001": (0.80, 4.00),
    "claude-opus-4-6":          (15.00, 75.00),
}


def _price_per_million(model: str) -> tuple[float, float]:
    return PRICE_TABLE.get(model, (3.00, 15.00))


def _today_utc() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")


# ----------------- Backend -----------------
class _SpendStore:
    """Per-tenant per-day cost ledger. Redis primary; in-mem fallback."""
    def __init__(self):
        self._mem: dict[str, float] = {}
        self._redis = None
        self.mode = "memory"
        if REDIS_URL:
            try:
                import redis  # type: ignore
                self._redis = redis.Redis.from_url(REDIS_URL, decode_responses=True)
                self._redis.ping()
                self.mode = "redis"
            except Exception as e:
                print(f"[hooks.spend_caps] Redis unavailable; in-memory fallback: {e}")
                self._redis = None

    def _key(self, tenant_id: str) -> str:
        return f"{REDIS_NS}:{tenant_id}:{_today_utc()}"

    def get(self, tenant_id: str) -> float:
        if self._redis is not None:
            v = self._redis.get(self._key(tenant_id))
            return float(v) if v else 0.0
        return self._mem.get(self._key(tenant_id), 0.0)

    def add(self, tenant_id: str, dollars: float) -> float:
        k = self._key(tenant_id)
        if self._redis is not None:
            self._redis.incrbyfloat(k, dollars)
            # 24h TTL so old days don't accumulate.
            self._redis.expire(k, 60 * 60 * 26)
            return float(self._redis.get(k))
        self._mem[k] = self._mem.get(k, 0.0) + dollars
        return self._mem[k]


_store = _SpendStore()


# ----------------- Public API -----------------
def cap_for_tenant(tenant_id: str) -> float:
    """Per-tenant cap override hook. For now everyone gets the default."""
    return DEFAULT_CAP


def should_call_llm(tenant_id: str, model: str) -> dict:
    """
    Decide whether the next LLM call is allowed.

    Returns:
      {"allow": True, "spent_usd": .., "cap_usd": ..}
      {"allow": False, "reason": "cap_exceeded", "spent_usd": ..,
       "cap_usd": .., "degrade_to": "simulated"}
    """
    spent = _store.get(tenant_id)
    cap = cap_for_tenant(tenant_id)
    if spent >= cap:
        return {
            "allow": False,
            "reason": "cap_exceeded",
            "spent_usd": round(spent, 4),
            "cap_usd": cap,
            "degrade_to": "simulated",
        }
    return {"allow": True, "spent_usd": round(spent, 4), "cap_usd": cap}


def record_usage(tenant_id: str, model: str,
                 input_tokens: int, output_tokens: int) -> dict:
    """Charge the tenant for an LLM call. Returns the new total."""
    p_in, p_out = _price_per_million(model)
    cost = (input_tokens / 1_000_000) * p_in + (output_tokens / 1_000_000) * p_out
    total = _store.add(tenant_id, cost)
    return {
        "model": model,
        "input_tokens": input_tokens,
        "output_tokens": output_tokens,
        "cost_usd": round(cost, 6),
        "tenant_total_usd": round(total, 4),
    }


def trace_event(decision: dict) -> dict:
    """Emit-friendly trace payload for the Agent.run() loop."""
    return {"kind": "policy", "policy": "spend_caps", **decision}


def reset_for_tests(tenant_id: Optional[str] = None) -> None:
    """Test helper — clear the in-memory store. No-op for Redis."""
    if _store._redis is None:
        if tenant_id is None:
            _store._mem.clear()
        else:
            for k in list(_store._mem.keys()):
                if tenant_id in k:
                    del _store._mem[k]
