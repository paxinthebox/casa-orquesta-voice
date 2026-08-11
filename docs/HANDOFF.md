# Claude Code Handoff — Casa·Orquesta · Voice

Welcome. This is your kickoff brief. **Read it once, then read `../CLAUDE.md`, then read `docs/PLAN.md`, then begin Phase 1 Task 1.**

## Mission, in one sentence

Move the proven text-chat agent system from `../casa-orquesta-mvp/` into a voice-first mobile product, running on the Claude Agent SDK harness, ready for 5–10 real testers to hit through TestFlight by end of Week 8.

## Your operating contract

| Aspect | Value |
|---|---|
| Languages | Python 3.11+ (backend), TypeScript (mobile), bash (scripts) |
| Behavior contract | `services/orchestrator/tests/test_agents.py` — 161+ assertions, must stay green |
| Source of truth for "how" | `../casa-orquesta-mvp/` — port from there, don't reinvent |
| AI harness | Claude Agent SDK (`claude-agent-sdk` Python package) |
| Stage cloud | Fly.io (us-east primary, MX region when available) |
| Voice STT | Deepgram Nova-2 es-MX |
| Voice TTS | ElevenLabs Flash (planned downgrade to Azure Neural Spanish if cost-pressed) |
| Auth | Auth0 phone OTP |
| Persistence | Postgres + pgvector + Redis |
| Compliance | LFPDPPP, LFPIORPI, NOM-151 (audit), NOM-247 (advertising) |

## Day 1 reading order (≤30 min)

1. `../CLAUDE.md` — the constitution. **Non-negotiables are in there.**
2. `docs/PLAN.md` — the 8-week phase plan.
3. `docs/ARCHITECTURE.md` — what we're building.
4. `docs/TASK_PROMPTS.md` — your work queue.
5. `services/orchestrator/harness.py` — the entry point you're filling in.
6. `../casa-orquesta-mvp/services/orchestrator/agents.py` — the code you're porting from.

## Day 1 first commit

Set up the working environment and verify the green path:

```bash
# 1. Bootstrap
cp .env.example .env
# Edit .env: at minimum set ANTHROPIC_API_KEY (or leave empty for simulated mode)

# 2. Install deps
cd services/orchestrator && pip install -r requirements.txt && cd ../..

# 3. Verify the MVP test contract loads cleanly
cd ../casa-orquesta-mvp && python3 tests/test_agents.py
# Expect: 161+ passed, 0 failed.

# 4. Port the test file to this repo with the SDK in mind:
#    services/orchestrator/tests/test_agents.py
#    (See TASK_PROMPTS.md — Task P1.7)
```

Your first PR should be small: the bootstrap script + an adapted but still-passing test suite that targets the new file paths. **Do not** start the SDK migration until the test contract is mechanically running in this repo.

## How you should work

- **Read CLAUDE.md before every session.** Even if you've read it before.
- **Take tasks from `docs/TASK_PROMPTS.md` in the suggested order.** They are dependency-ordered.
- **One task → one PR → green verify script → merge → next task.**
- **Update `docs/TASK_PROMPTS.md` itself** as you complete tasks. Mark them `[x]`, note any deviations.
- **When you finish a phase, write a 5-line retro at the bottom of PLAN.md.** What changed, what surprised you, what to do differently in the next phase.

## When you get stuck

In order of escalation:

1. **Look in `../casa-orquesta-mvp/`** for how that codebase did it. If present, copy the pattern.
2. **Check the SDK docs** — `https://docs.claude.com/en/api/agent-sdk`. If the SDK has a primitive that does what you want, use it.
3. **Re-read the relevant section of `docs/PLAN.md` and `docs/ARCHITECTURE.md`.** Often the constraint is documented.
4. **Open a `STUCK.md` at the repo root** with: the task, what you tried, what failed, what you'd like to do. Stop work. Wait for human review.
5. **Never silently change a test assertion to make code pass.** Better to leave the task half-done with a clear STUCK.md than to corrupt the behavior contract.

## What "done" means per task

A task is done when:

- All code is in place at the file paths specified in the task prompt.
- `./scripts/verify.sh` exits 0 (lint + tests + typecheck all green).
- New behavior is covered by a new assertion in the test file.
- The PR description summarizes: what changed, what was ported from MVP, what was new, what the next task depends on.
- The relevant section of `docs/TASK_PROMPTS.md` is marked `[x]`.

## What "done" means per phase

A phase is done when all its tasks are `[x]`, the **decision gate** in `docs/PLAN.md` for that phase is met, and the founder has reviewed.

## Things you must escalate to the founder

- Anything that changes a `MUST` non-negotiable in CLAUDE.md.
- Anything that changes a public API shape (orchestrator endpoints, voice gateway WS protocol, mobile→backend contracts).
- Anything that adds a new third-party paid dependency.
- Anything LFPDPPP-related where you're not sure.
- Anything that costs more than $20/month in runtime.
- Choosing between two valid architectural options when the choice has long-term consequences.

## Working hours convention

There aren't any. Make as many incremental commits as you want. The founder reviews in batches once or twice a day.

## What the founder is doing while you work

- Reviewing your PRs within ~24 hours.
- Running integration tests in real Docker / on a real iPhone.
- Talking to brokers and INAI counsel.
- Updating the cost model when assumptions change.
- Setting up Fly.io, Auth0, Deepgram, ElevenLabs accounts.
- Decision-gating each phase.

You can assume the founder will handle: cloud account creation, payment methods, App Store/Play Store enrollment, broker outreach, legal review.

You should not assume the founder will handle: any code, tests, docs, or runnable scripts.
