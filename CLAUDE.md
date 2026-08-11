# CLAUDE.md — Project Constitution for Claude Code

This file is loaded into every Claude Code session in this repo. It is the source of truth for what the project is, what's expected of you, and what NOT to change without asking.

## What this project is

Casa·Orquesta · Voice — a voice-conversational mobile app (iOS + Android) backed by the Claude Agent SDK harness. It serves real estate buyers, owners, and realtors in CDMX and Morelos, Mexico, with full LFPDPPP / NOM-151 / CFDI compliance.

The product is in **stage build** mode, targeting a real-tester pilot at the end of Week 8. See `docs/PLAN.md` for the 8-week phase plan and `docs/ARCHITECTURE.md` for the target architecture.

## Source of truth — the MVP repo

The working text-chat version lives at **`../casa-orquesta-mvp/`** (sibling directory). It is **the behavior contract**. The voice repo migrates that logic; it must not invent new behavior.

Look there first when you need to know how something works:

| If you need to know… | Read |
|---|---|
| How the three agents route | `../casa-orquesta-mvp/services/orchestrator/agents.py` |
| How the 11 tools are shaped | same file, plus `datasets.py` for mock data |
| The behavior contract (161 assertions) | `../casa-orquesta-mvp/tests/test_agents.py` |
| The 16-listing CDMX + Morelos catalog | `../casa-orquesta-mvp/data/seeds/listings.json` |
| The Promesa de Compraventa template | `../casa-orquesta-mvp/services/documents/main.py` |
| The CFDI 4.0 XML structure | `../casa-orquesta-mvp/services/payments/main.py` |

When you're about to write something new, ask: *does the MVP already do this?* If yes, port it; don't reinvent.

## The non-negotiables

These rules override anything else in any prompt:

1. **The assertions in `services/orchestrator/tests/test_agents.py` are the behavior contract.** The current count (161+ as of the MVP port; whatever it is on `main` plus any new assertions you add for new behavior) must stay green on every PR, and the count must never regress. PR descriptions reference the contract as "the current count + any new behavior, never regressing" — don't pin to a single number. If a change requires updating an existing assertion, you must (a) understand why the original was wrong, (b) write a comment in the test explaining the shift, (c) flag it in your summary for human review. Never silently change a test to make code pass.
2. **Never commit secrets.** `.env` is gitignored. If a value belongs in `.env.example`, leave it empty there.
3. **Spanish-MX is the primary language for user-facing strings.** System prompts, error messages shown to users, voice synthesis — all es-MX. English is acceptable in code, comments, internal logs.
4. **PII redaction runs before every third-party LLM call.** No INE numbers, RFCs, CURPs, full names, or phone numbers leave the process unredacted. The `hooks/pii_redaction.py` hook is the enforcement point.
5. **Per-tenant per-day spend caps must be enforced.** Default $5/day. The `hooks/spend_caps.py` hook is the enforcement point. Voice can degrade gracefully to SLM-only mode; it cannot silently exceed the cap.
6. **Audit log entries are append-only and tamper-evident.** Hash-chained, written to S3/Tigris WORM. Never mutate an existing entry.
7. **Don't touch `../casa-orquesta-mvp/`.** That repo is the pitch demo. Read it for reference, never edit it.

## Repo layout

```
apps/mobile/                React Native + Expo voice client
  App.tsx                   Entry — Auth0 + Voice + Consent providers
  src/voice/                Audio capture/playback + WebSocket
  src/screens/              Onboarding, Home, ListingDetail, …
  src/compliance/           ConsentModal, DSARScreen
services/
  orchestrator/             Claude Agent SDK harness — the brain
    harness.py              Entry point — DO read first
    agents/                 realestate, locator, audit
    tools/                  listings_tools, people_tools, audit_tools
    hooks/                  pii_redaction, spend_caps, audit_log, trace
    datasets.py             Mock public registries (port from MVP)
    tests/test_agents.py    The behavior contract
  voice-gateway/            STT → orchestrator → TTS pipeline
    main.py                 WebSocket entry
    stt/, tts/, pipeline.py, barge_in.py
  identity/                 Auth0 + tenant + user model (NEW)
  listings, matching, scheduling, documents, payments, comms, gateway
infra/                      Fly.io + Terraform + GitHub Actions
docs/
  PLAN.md                   8-week build plan, 4 phases, 8 decision gates
  ARCHITECTURE.md           Target architecture, voice pipeline
  HANDOFF.md                Your kickoff brief — read on Day 1
  TASK_PROMPTS.md           Copy-pasteable task prompts per work unit
  Stage_Voice_Plan.xlsx     Cost-integrated workbook
tests/                      Cross-service integration tests
scripts/                    bootstrap.sh, verify.sh, seed.py
```

## How to run / verify

```bash
make dev              # docker compose up + apps start
make check            # run the 161-assertion test suite
./scripts/verify.sh   # the canonical pre-commit gate: lint + tests + typecheck
make mobile-ios       # apps/mobile in Expo iOS simulator
```

Acceptance is not "tests pass" alone — it's `./scripts/verify.sh` green.

## Decision rules

When you have to choose:

- **Behavior choice that the MVP already made** → match the MVP.
- **Behavior choice the MVP didn't address** → pick the simplest option that doesn't lock out future flexibility; flag in your summary.
- **Architecture choice between SDK convention and our convention** → follow the SDK convention unless it breaks the 161 assertions. The SDK is the harness; don't fight it.
- **Cost vs. quality tradeoff** → cost-first for stage. Quality decisions get made post-pilot.
- **Spanish phrasing** → conversational es-MX, no jargon, mid-formality (`tú` not `usted`).

## When to ask vs. decide

- **Ask** before: changing any test assertion; touching `../casa-orquesta-mvp/`; introducing a new third-party dependency; making a security-relevant change (auth, audit, secrets); writing more than 200 lines without a milestone.
- **Decide** without asking for: naming, file layout within a service, refactoring within a single file, choosing between two equivalent libraries, updating documentation to match new code.

## Tool/agent registry — the shape that must hold

The orchestrator service exposes three endpoints that other services and the mobile app depend on:

- `GET /api/orchestrator/agents` — returns a **hierarchical list of 3 top-level agents, each with its own `tools` array**. The shape is:
  ```json
  [
    {"name": "realestate_agent", "model": "...", "description": "...",
     "tools": [{"name": "locator_agent", ...}, {"name": "audit_agent", ...}]},
    {"name": "locator_agent",   "model": "...", "tools": [ /* 6 tools */ ]},
    {"name": "audit_agent",     "model": "...", "tools": [ /* 5 tools */ ]}
  ]
  ```
  A caller can count tools either way by walking the structure: `2` at the orchestrator level (sub-agents-as-tools), or `6 + 5 = 11` at the leaf tools, or `2 + 6 + 5 = 13` flattened. The canonical "tool count" used in tests and PR descriptions is the **leaf count: 11**.
- `POST /api/orchestrator/chat` — non-streaming chat entry; returns `{reply, agents_invoked, trace, run_id, ai_mode}`.
- `POST /api/orchestrator/agent/run/stream` — SSE stream of agent events to the voice gateway.

If you change one of these shapes, you must update the mobile app, the voice gateway, and the test suite together. **They are joint contracts**.

## Style

- Python: 3.11+, `ruff` formatter, type hints required on public functions, async by default for I/O.
- TypeScript: strict mode, no `any`, prefer `unknown` + type guards.
- JSON shapes: `snake_case` keys throughout (matches MVP).
- File naming: `snake_case.py` (Python), `PascalCase.tsx` (React components), `camelCase.ts` (other TS).
- Commit messages: imperative mood, scope prefix, e.g. `orchestrator: port realestate_agent to SDK harness`.

## What's intentionally unfinished

These files are skeletons until the phase that fills them in:

- `services/orchestrator/agents/*.py` → Phase 1
- `services/orchestrator/tools/*.py` → Phase 1
- `services/orchestrator/hooks/*.py` → Phase 1
- `services/voice-gateway/stt/`, `tts/`, `pipeline.py`, `barge_in.py` → Phase 2
- `apps/mobile/src/screens/*.tsx`, `src/voice/AudioRecorder.ts`, `AudioPlayer.ts` → Phase 3
- `services/identity/*` → Phase 4
- `infra/fly/*`, `infra/terraform/*` → progressive across phases

Don't be alarmed when you find a file with `raise NotImplementedError`. The file's header docstring will tell you which phase owns it.
