# Claude Agent SDK migration — status + plan

> Source-of-truth for *where we are* on the migration from the MVP's
> direct-API-call pattern to the Claude Agent SDK. Updated after each
> milestone. Counsel + founder review this before any pricing model
> change goes live.

## TL;DR

| Track                               | Status               | Notes                                                  |
| ----------------------------------- | -------------------- | ------------------------------------------------------ |
| Orchestrator agents-as-tools        | ✅ on SDK             | P1.3 — realestate / locator / audit agents.            |
| Streaming SSE → mobile              | ✅ on SDK trace       | P1.8 — events flow via the SDK's trace hook.           |
| Voice-gateway STT/TTS               | ✅ provider Protocol  | Not SDK-dependent — own adapters.                      |
| Prompt-prefix caching               | ✅ wired (P4.7)       | `services/_shared/prompt_cache.py`.                    |
| Per-tenant cost ledger              | ✅ wired (P4.7)       | `services/_shared/telemetry.py`.                       |
| Tool result schema enforcement      | ⏳ partial            | P1.6 tool handlers — some still loose dicts.           |
| Streaming partial tokens (text_delta)| ⏳ single-chunk       | Current code emits one text_delta at run_end.          |
| Sub-agent isolation (separate ctxs) | ⏳ partial            | Sub-agents share parent's context today.               |
| Agent-as-MCP-server                 | 🟡 explored, not adopted | Vendor lock-in vs. portability trade-off; deferred.   |

## What "on the SDK" means here

We use the SDK for:
- The supervisor `realestate_agent` and the two sub-agents (`locator_agent`, `audit_agent`).
- Tool definitions are SDK-native (`@tool` decorator equivalent).
- The trace hook (`ctx.emit`) is the source of every event the orchestrator's `/agent/run/stream` SSE endpoint forwards.

We do NOT use the SDK for:
- The audio I/O (`voice-gateway` STT/TTS) — that's our own Protocol layer per P2.x.
- The Anthropic Messages API call inside identity (we don't make one).
- The mobile-side LLM integration (none — the app is a thin client).

## Token-cost expectations

These are the assumed costs the runbook + the budget cap (`services/orchestrator/hooks/spend.py`) work off. Updated whenever Anthropic ships new rate cards.

| Model                 | Input  | Output | Notes                          |
| --------------------- | ------ | ------ | ------------------------------ |
| Claude Sonnet 4.6     | $3 / Mtok | $15 / Mtok | Default supervisor + audit |
| Claude Haiku 4.5      | $0.80 / Mtok | $4 / Mtok | Default router + locator |
| Claude Opus 4.6       | $15 / Mtok | $75 / Mtok | Reserved for hard cases |

Cache-create costs 1.25x of the regular input rate. Cache-read costs 0.10x. The prompt-cache helper (P4.7) wires this in the per-tenant ledger automatically.

Expected savings from cache adoption on a normal turn (long system + tools + short user query):
- 90 % of the input is in the cached prefix
- Cache-hit ratio after 10 turns: ~95 %
- Net savings: ~75 % of input cost; ~15 % of total cost for a typical turn

## Migration plan (remaining)

### Now (P4.7)
- ✅ Wire prompt-prefix caching for supervisor + audit prefixes.
- ✅ Per-tenant cost ledger.
- ✅ Runbook + backup script.

### Sprint 5
- Streaming partial tokens — replace the single `text_delta` at `run_end` with true per-token streaming. Latency win: ~200 ms on the first audible audio frame. Code site: `services/orchestrator/main.py` `agent_run_stream`.
- Sub-agent context isolation — separate `ctx` per sub-agent so `locator_agent` doesn't see the audit hash chain. Reduces input tokens per call.
- Tool-result schema enforcement — Pydantic models on every `tool_result` instead of loose dicts. P1.6 partially shipped; finish in P5.

### Sprint 6
- Consider Agent-as-MCP-server for the audit_agent specifically. The compliance rules library is the kind of stable, server-y thing that an MCP server is designed for. Trade-off: easier to test in isolation, but creates a network-call boundary inside the agent stack. Decision postponed until we have real load data from the bug bash.

### Sprint 7+
- Per-tenant fine-tuning of the supervisor on accepted vs. rejected reply pairs. Requires log retention + opt-in consent that's already in the Aviso de Privacidad.

## What we explicitly chose NOT to do

- **Migrate the MVP demo's HTML console to the SDK.** It's a working dev tool; rewriting it for the SDK would add no production value.
- **Move the voice-gateway STT/TTS to SDK tools.** The provider Protocol pattern is cleaner for a hot path that needs to swap vendors via env. SDK tools would add a JSON round-trip per audio frame.
- **Adopt the SDK's filesystem tools for documents/.** Our S3-backed flow already handles versioning + signed URLs the way the LFPDPPP audit log expects.

## How to verify on a running deploy

```bash
# Confirm prompt cache is wired
curl -s -H "Authorization: Bearer $USER_JWT" \
    https://api.casaorquesta.mx/_internal/telemetry/cost-summary?since=1h \
    | jq '.by_tenant_vendor[] | select(.vendor=="anthropic") | {tenant_id, calls, in_tokens, out_tokens, cache_hit_ratio}'

# A healthy turn should show cache_hit_ratio ≥ 0.8 after 10+ turns from the same tenant.
```

## Change log

| When         | Who      | Change                                                   |
| ------------ | -------- | -------------------------------------------------------- |
| 2026-04      | founder  | Original MVP on direct API calls.                        |
| 2026-05      | engineering | P1.3 — agents-as-tools on the SDK.                    |
| 2026-06      | engineering | P4.7 — prompt cache + cost ledger + runbook.          |
