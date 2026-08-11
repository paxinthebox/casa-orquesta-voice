"""
Prompt prefix caching — Phase 4.7.

Anthropic's `cache_control: {type: "ephemeral"}` saves ~90 % of the
token cost on the cached prefix when the same request signature
repeats within ~5 minutes. We use it for two stable prefixes:

  1. The supervisor agent's system prompt + tool definitions
     (long, never changes during a turn — perfect for caching).
  2. The audit_agent's compliance-rules library + its dataset
     stub definitions (also stable, often re-read).

The user's actual turn (`message`) goes AFTER the cached prefix and
is never cached.

Public API:

    from prompt_cache import build_messages, mark_cacheable

    messages = build_messages(
        system_prefix=SYSTEM_PROMPT,
        tools_prefix=TOOLS_BLOCK,
        history=conv_history,
        user_message=req.message,
        cache=True,   # turn the optimization on
    )
    # → list[dict] in the shape the Anthropic Messages API expects.
    # Anything before `user_message` is marked with cache_control.

We also track cache-hit telemetry so the orchestrator's cost
dashboard reflects the actual savings. The Anthropic API returns
`cache_read_input_tokens` and `cache_creation_input_tokens` on every
response; `record_cache_usage(...)` writes them into the telemetry
ledger.

The module is dependency-free — no Anthropic SDK import — so the
test runners can exercise the message-shape logic hermetically.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

try:
    from telemetry import (                                    # type: ignore
        record_vendor_cost, get_logger,
    )
    _HAS_TELEMETRY = True
except Exception:                                              # pragma: no cover
    try:
        from _shared.telemetry import (                        # type: ignore
            record_vendor_cost, get_logger,
        )
        _HAS_TELEMETRY = True
    except Exception:
        _HAS_TELEMETRY = False


# ---------------------------------------------------------------------------
# Anthropic message-shape builders
# ---------------------------------------------------------------------------
def mark_cacheable(block: dict) -> dict:
    """Return a copy of `block` with `cache_control: {type: 'ephemeral'}`
    added. Operates on any content block (text, document, etc.).
    """
    out = dict(block)
    out["cache_control"] = {"type": "ephemeral"}
    return out


def system_blocks(*, system_prefix: str, tools_prefix: Optional[str] = None,
                  cache: bool = True) -> list[dict]:
    """Return the `system` array as Anthropic expects it. When
    `cache=True`, mark the long prefix(es) as ephemeral-cached."""
    out: list[dict] = []
    sys_block = {"type": "text", "text": system_prefix}
    if cache:
        sys_block = mark_cacheable(sys_block)
    out.append(sys_block)
    if tools_prefix:
        tools_block = {"type": "text", "text": tools_prefix}
        if cache:
            tools_block = mark_cacheable(tools_block)
        out.append(tools_block)
    return out


def build_messages(
    *,
    history: Optional[list[dict]] = None,
    user_message: str,
) -> list[dict]:
    """Return the `messages` array. Conversation history (the previous
    user + assistant turns) goes first; the user's current turn is the
    last entry. We deliberately do NOT cache the dynamic user content."""
    msgs: list[dict] = []
    for turn in (history or []):
        role = turn.get("role")
        content = turn.get("content")
        if role in ("user", "assistant") and isinstance(content, str):
            msgs.append({"role": role, "content": content})
    msgs.append({"role": "user", "content": user_message})
    return msgs


def build_request(
    *,
    system_prefix: str,
    tools_prefix: Optional[str] = None,
    history: Optional[list[dict]] = None,
    user_message: str,
    cache: bool = True,
) -> dict:
    """One-shot builder. Returns `{"system": [...], "messages": [...]}`
    ready to drop into `client.messages.create(...)`."""
    return {
        "system": system_blocks(
            system_prefix=system_prefix, tools_prefix=tools_prefix,
            cache=cache,
        ),
        "messages": build_messages(
            history=history, user_message=user_message,
        ),
    }


# ---------------------------------------------------------------------------
# Telemetry recorder
# ---------------------------------------------------------------------------
@dataclass
class CacheUsageRecord:
    tenant_id: str
    model: str
    cache_read_input_tokens: int = 0
    cache_creation_input_tokens: int = 0
    regular_input_tokens: int = 0
    output_tokens: int = 0
    request_id: Optional[str] = None


def record_cache_usage(
    *,
    tenant_id: str,
    model: str,
    usage: dict,
    request_id: Optional[str] = None,
    service: Optional[str] = None,
) -> CacheUsageRecord:
    """Read the `usage` field from an Anthropic Messages response and
    record the breakdown into the telemetry ledger.

    Anthropic's usage shape:
      {
        "input_tokens": 12,
        "cache_creation_input_tokens": 2200,
        "cache_read_input_tokens": 0,
        "output_tokens": 156
      }

    We charge:
      regular tokens   @ 1.0x
      cache create     @ 1.25x (per Anthropic docs)
      cache read       @ 0.10x
      output tokens    @ normal rate
    """
    cr_in = int(usage.get("cache_read_input_tokens") or 0)
    cc_in = int(usage.get("cache_creation_input_tokens") or 0)
    reg_in = int(usage.get("input_tokens") or 0)
    out = int(usage.get("output_tokens") or 0)

    rec = CacheUsageRecord(
        tenant_id=tenant_id, model=model,
        cache_read_input_tokens=cr_in,
        cache_creation_input_tokens=cc_in,
        regular_input_tokens=reg_in,
        output_tokens=out,
        request_id=request_id,
    )

    if _HAS_TELEMETRY:
        # Rough multipliers per Anthropic docs.
        billed_in = reg_in + int(cc_in * 1.25) + int(cr_in * 0.10)
        usd = estimate_usd_anthropic(model, billed_in, out)
        record_vendor_cost(
            tenant_id=tenant_id,
            vendor="anthropic",
            model=model,
            in_tokens=billed_in,
            out_tokens=out,
            usd=usd,
            request_id=request_id,
            service=service,
        )
        get_logger(service=service or "orchestrator").info(
            "anthropic.usage",
            tenant_id=tenant_id, model=model,
            regular_in=reg_in,
            cache_read_in=cr_in,
            cache_creation_in=cc_in,
            output=out,
            billed_in=billed_in,
            usd=usd,
            cache_hit_ratio=round(cr_in / max(1, reg_in + cc_in + cr_in), 4),
            request_id=request_id,
        )
    return rec


def estimate_usd_anthropic(model: str, in_tokens: int,
                           out_tokens: int) -> float:
    """Fall back to the telemetry rate card; pulled here so this module
    stays self-contained when telemetry isn't importable."""
    if _HAS_TELEMETRY:
        from telemetry import estimate_usd                     # type: ignore
        return estimate_usd("anthropic", model=model,
                            in_tokens=in_tokens, out_tokens=out_tokens)
    # Conservative fallback: Sonnet-ish rate (~$3 + $15 per million).
    return round(in_tokens / 1000.0 * 0.003 + out_tokens / 1000.0 * 0.015, 6)


# ---------------------------------------------------------------------------
# Quick analyzer — useful for the dashboard
# ---------------------------------------------------------------------------
def summarize_cache_hit_ratio(records: list[CacheUsageRecord]) -> dict:
    """For a list of records, return total in/out + cache-read ratio
    + estimated savings vs. no-cache baseline. Used by the cron that
    reads the ledger and emits the daily roll-up."""
    cr = sum(r.cache_read_input_tokens for r in records)
    cc = sum(r.cache_creation_input_tokens for r in records)
    reg = sum(r.regular_input_tokens for r in records)
    out = sum(r.output_tokens for r in records)
    total_in = cr + cc + reg
    hit_ratio = round(cr / max(1, total_in), 4)
    # Naive savings: if cache had been a miss, we'd have paid for cr
    # tokens at full rate. We paid 0.10x.
    savings_tokens = int(cr * 0.9)
    return {
        "regular_input_tokens": reg,
        "cache_creation_input_tokens": cc,
        "cache_read_input_tokens": cr,
        "output_tokens": out,
        "total_input_tokens": total_in,
        "cache_hit_ratio": hit_ratio,
        "savings_tokens_vs_nocache": savings_tokens,
    }
