# Agent evals

Regression and quality checks for the orchestrator harness (routing, guardrails, seller/buyer proactivity).

## Quick start

```bash
# Simulated mode — no API key, CI-safe (~1s)
make eval

# Filter cases
make eval FILTER=route-

# JSON report (for CI artifacts)
make eval-json

# Real Claude — requires ANTHROPIC_API_KEY in .env
make eval-live

# Promptfoo UI + comparison (optional — needs Node 18+)
npm install          # installs promptfoo devDependency
make eval-promptfoo  # runs eval + opens nothing
npx promptfoo view   # inspect results in browser
```

## Case format

Cases live in `evals/cases/orchestrator.json`. Each case has:

| Field | Purpose |
|-------|---------|
| `id` | Stable identifier (`route-search-loft`) |
| `description` | Human-readable intent |
| `vars.message` | User utterance (es-MX) |
| `vars.client_role` | `buyer` or `seller` |
| `assert` | Deterministic checks (see below) |

### Assertion types

```json
{
  "agents_invoked": { "includes": ["realestate_agent"], "excludes": ["audit_agent"] },
  "subagents": { "includes": ["locator_agent"] },
  "tools_used": { "includes": ["find_buyers"], "includes_any": ["search_listings"] },
  "prompt_guard": { "allow": false },
  "reply": { "contains_any": ["publicar", "ofertas"], "max_chars": 600 },
  "trace": { "balanced_agent_lifecycle": true },
  "state": { "filters.state": "Morelos" }
}
```

Add new cases when you ship behavior — they complement (not replace) the 161+ assertion contract in `test_agents.py`.

## Runners

| Runner | When to use |
|--------|-------------|
| **`scripts/evals/run_evals.py`** | Default CI gate, fast sim checks |
| **[Promptfoo](https://promptfoo.dev)** | Compare sim vs live Claude, share reports, LLM-as-judge extensions |

Both runners read the same JSON cases. Promptfoo config: `evals/promptfoo/promptfooconfig.yaml`.

Simulated evals stub matching/listings HTTP (`EVAL_STUB_SERVICES=1`, default). Set `EVAL_STUB_SERVICES=0` to hit the real docker stack during integration evals.

## Live evals & cost

`make eval-live` calls Anthropic for every case (~17 turns). Use sparingly before releases or prompt changes. Spend caps hook still applies per tenant.

## Extending with LLM-as-judge (optional)

Promptfoo supports rubric assertions. Example add-on in a case:

```yaml
assert:
  - type: llm-rubric
    value: Reply is proactive es-MX, suggests a concrete next step, stays in real-estate domain
```

Requires `OPENAI_API_KEY` or another judge provider in promptfoo config — not enabled by default in this repo.

## CI

`verify.sh` runs simulated evals after the behavior contract. Failures block merge.
