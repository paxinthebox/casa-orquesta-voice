# Casa·Orquesta · Voice — Architecture

Target architecture for the voice-conversational, mobile-native, production-trajectory version of Casa·Orquesta.

## Top-level view

```
┌────────────────────────────┐
│  iPhone / Android device   │
│  React Native + Expo       │
│  - Mic capture (PTT)       │
│  - Speaker playback        │
│  - WebSocket client        │
│  - Visual cards + audit    │
└──────────────┬─────────────┘
               │  WSS (audio frames + control)
               ▼
┌────────────────────────────────────────────────────────────────────┐
│  Edge / Gateway  (Fly.io, Mexico region preferred)                 │
│  Nginx + WAF + Cloudflare                                          │
└──────────────┬─────────────────────────────────────────────────────┘
               │
               ▼
┌────────────────────────────┐     ┌─────────────────────────────┐
│   voice-gateway            │◄───►│  Deepgram STT (Nova-2 es-MX)│
│   (FastAPI + WS)           │     └─────────────────────────────┘
│   Pipecat-style pipeline   │     ┌─────────────────────────────┐
│   • barge-in detection     │◄───►│  ElevenLabs Flash es-MX     │
│   • VAD                    │     └─────────────────────────────┘
│   • partial→final reroute  │
└──────────────┬─────────────┘
               │  HTTP SSE (text only)
               ▼
┌────────────────────────────────────────────────────────────────────┐
│   orchestrator  ← THE HARNESS  (Claude Agent SDK)                  │
│                                                                    │
│   realestate_agent (Sonnet 4.6)                                    │
│     ├─ locator_agent (Haiku 4.5)                                   │
│     │   ├─ search_listings  get_listing  compare_listings          │
│     │   └─ find_buyers  find_collaborator_agents  find_brokers     │
│     └─ audit_agent (Haiku 4.5)                                     │
│         ├─ review_promesa                                          │
│         └─ rpp_lookup  catastro_lookup  inegi_zone_stats  sat_rfc  │
│                                                                    │
│   Hooks: audit_log · pii_redaction · spend_caps · trace_emit       │
└──────────────┬─────────────────────────────────────────────────────┘
               │  HTTP (internal)
               ▼
┌────────────────────────────────────────────────────────────────────┐
│   Domain services   (FastAPI, ported from MVP)                     │
│   listings · matching · scheduling · documents · payments · comms  │
│   identity (NEW)                                                   │
└──────────────┬─────────────────────────────────────────────────────┘
               │
               ▼
┌────────────────────────────────────────────────────────────────────┐
│   Data layer                                                       │
│   Postgres + pgvector (Fly)   Redis (Upstash)                      │
│   S3/Tigris WORM (audit)      Langfuse (AI traces)                 │
└────────────────────────────────────────────────────────────────────┘
```

## The harness

The orchestrator service is a thin FastAPI layer wrapping the **Claude Agent SDK**. The SDK is the "harness" — it owns the agent loop, tool invocation, streaming, and hook execution. Our code declares agents, tools, and hooks; the SDK runs them.

```
services/orchestrator/
├── main.py                 FastAPI: /chat, /agent/run, /agent/run/stream, /agents
├── harness.py              Single source of truth: loads + registers all agents
├── agents/
│   ├── realestate.py       Orchestrator agent (sonnet); delegates to sub-agents
│   ├── locator.py          Property + people finder (haiku); 6 tools
│   └── audit.py            Compliance + registries (haiku); 5 tools
├── tools/
│   ├── listings_tools.py
│   ├── people_tools.py     find_buyers, find_collaborator_agents, find_brokers
│   ├── audit_tools.py
│   └── registry.py         Optional MCP-server export for partner integrations
├── hooks/
│   ├── audit_log.py        Pre-tool + post-tool → tamper-evident S3 log
│   ├── pii_redaction.py    Presidio + Spanish NER; runs before LLM calls
│   ├── spend_caps.py       Per-tenant per-day cap; degrades to SLM-only mode
│   └── trace.py            Emits agents.html-compatible event stream
├── datasets.py             Mocked public registries (RPP/Catastro/INEGI/SAT)
└── tests/test_agents.py    161+ assertions, ported from MVP
```

### Why hooks matter for this build

In the MVP, the trace events are emitted manually with `ctx.emit(...)`. In the SDK harness, hooks emit them automatically — same JSON shape, half the code, and the same hook code path generates the LFPIORPI-grade audit log that gets WORM-stored. **One mechanism replaces two**, which is the main reason migrating now is worth it.

## The voice pipeline

End-to-end audio path with the latency budget allocation:

```
User speaks
   │   ◄────────────────  T = 0 ms
   ▼
[mobile mic capture, 20 ms frames, Opus 16 kHz]
   │
   │   ◄────────────────  T ≈ 20 ms
   ▼
[WebSocket → voice-gateway → Deepgram WS]
   │
   ▼
Deepgram streams partial transcripts
   │   ◄────────────────  T ≈ 100-200 ms (first partial)
   ▼
[voice-gateway detects end-of-speech via Deepgram VAD]
   │   ◄────────────────  T ≈ 500-800 ms (after user pause)
   ▼
[final transcript → orchestrator /agent/run/stream]
   │
   ▼
Claude streams tokens (Haiku first; Sonnet only for ambiguity)
   │   ◄────────────────  T ≈ 800-1100 ms (first reply token)
   ▼
[voice-gateway chunks tokens into TTS-friendly phrases]
   │
   ▼
[ElevenLabs Flash es-MX streams audio frames]
   │   ◄────────────────  T ≈ 1000-1400 ms (first audio frame plays)
   ▼
[mobile speaker plays]
   │   ◄────────────────  T ≈ 1100-1500 ms (audible response begins)
```

**Tier routing for latency**: the harness routes ~70% of voice turns to Haiku, ~25% to mid-priority Haiku reasoning with tools, ~5% to Sonnet for negotiation/ambiguity. The SLM router (in MVP's `extract_filters`) ports as an SDK pre-hook that classifies intent and biases tool selection.

**Tool calls during a turn**: if the agent invokes `search_listings` mid-turn, the gateway holds the TTS stream (instead of speaking a filler) and resumes once the tool result comes back. This avoids "uhm, dame un segundo" hallucinations.

**Barge-in**: VAD on the gateway watches the user's mic while TTS is playing back. If the user starts speaking, the gateway flushes the in-flight TTS buffer, sends a `cancel` to the orchestrator, and starts a new transcription cycle. The mobile app stops playback immediately on a `cancel` event.

## Mobile app

```
apps/mobile/
├── App.tsx                    Root with providers
├── app.json                   Expo config (es-MX, mic permission, push)
├── eas.json                   Build profiles (development, preview, production)
├── package.json
├── src/
│   ├── screens/               Onboarding, Home (voice), ListingDetail, …
│   ├── components/            MicButton, AgentChips, ListingCard, AuditCard, …
│   ├── voice/                 Audio capture/playback abstraction
│   │   ├── AudioRecorder.ts   Wraps expo-av or react-native-webrtc
│   │   ├── AudioPlayer.ts
│   │   └── VoiceClient.ts     WebSocket to voice-gateway, handles reconnect
│   ├── api/                   REST clients for listings/scheduling/documents/…
│   ├── state/                 Zustand stores (session, agent trace, focus IDs)
│   ├── assets/                Fonts, splash, icons
│   ├── locale/                es-MX strings (primary), en-US (QA)
│   └── compliance/
│       ├── ConsentModal.tsx   LFPDPPP Aviso de Privacidad
│       └── DSARScreen.tsx
└── tests/
    ├── voice.e2e.ts           Detox end-to-end voice flow
    └── unit/
```

**State that lives client-side:**

- Conversation ID, current focus listing/document, session UUID.
- Auth0 ID token (Secure storage / Keychain).
- Cached listings, schedule slots, audit summaries (so the screen survives a network blip).
- Agent trace events (mirrored from the SSE channel for the in-app SessionTrace view).

**State that lives server-side:**

- Full conversation history (Redis, 24 h TTL).
- Long-term user profile facts (Postgres, opt-in).
- Audit trail (S3/Tigris WORM).

## What stays vs. what changes vs. MVP

| Component | MVP | Voice repo | Change |
|---|---|---|---|
| `agents.py` | Custom Agent/Tool/RunContext | Claude Agent SDK declarations | **Rewrite** |
| 161 assertions test suite | Tests custom code paths | Tests SDK harness, same behavior | **Adapt, preserve contract** |
| `datasets.py` (RPP/Catastro/INEGI/SAT mocks) | Same | Same | Port verbatim |
| Listings service | SQLite | Postgres + pgvector | Schema lift |
| Matching service | TF-IDF + numpy | Same logic; embeddings persisted | Light port |
| Scheduling service | In-mem dicts | Redis | State move |
| Documents service | Local PDF | S3/Tigris | Storage move |
| Payments / CFDI | Mock | Facturama sandbox behind flag | Adapter swap |
| Comms simulator | Console + log | + Real WhatsApp Cloud API + voice | Channel add |
| Buyer UI (HTML) | Single page chat | iOS + Android voice app | **Replace** |
| Realtor UI / Owner UI | HTML | Postponed to Phase 5 (web) | Defer |
| Agents.html visualizer | HTML | Becomes in-app SessionTrace screen | Port |
| Comms console (HTML) | HTML | Becomes admin web (web stays separate) | Defer |

## Data residency

Mexico data residency is a real LFPDPPP consideration. Two-tier approach:

1. **Stage** (this plan, weeks 1–8): Fly.io in us-east region. Acceptable for pilot testers because they explicitly consent. Aviso de Privacidad discloses US storage.
2. **Production** (Phase 5+): migrate transactional Postgres + audit S3 to AWS Mexico (mx-central-1) or Fly's planned MX region. Anthropic API calls always go to AWS US — disclose this explicitly.

## Cost shape

See `Stage_Voice_Plan.xlsx`. Headline ranges:

- **Build (8 weeks, Claude doing engineering)**: API spend ≈ $80–250; founder review time ≈ $4,800–6,400; **total ≈ $5,000–6,700**.
- **Runtime per active tester per month**: ≈ $4–8 for Claude tokens, $5–12 for STT (Deepgram), $8–25 for TTS (ElevenLabs Flash). Total **≈ $20–45/tester/mo** at moderate use.
- **Stage cloud baseline**: ≈ $200–350/mo (Fly compute + Postgres + Redis + Langfuse + Sentry).
- **All-in 8 testers × 3 months testing**: ≈ $6,700 build + $700–1,000/mo runtime ≈ **$8,800–9,700**.

## Compliance hooks already designed

- LFPDPPP consent flow (mobile modal + voice accept) wired in Phase 4.
- DSAR endpoint `/api/identity/dsar/export` returns a ZIP within 20 business days (target: minutes).
- PII redaction pre-hook strips INE, RFC, CURP, names, phone numbers from prompts before any third-party LLM call.
- LFPIORPI audit trail in S3/Tigris with WORM lock and hash chain.
- NOM-247 ad disclosure check in audit_agent before any listing is shared publicly.

## What's intentionally not designed yet (Phase 5+)

- Real Truora INE OCR — needs real users + monetization first.
- Real Facturama PAC emission — needs first real commission.
- Real Mifiel signature — needs first real close.
- Multi-tenant isolation per brokerage — needs brokerage onboarding flow first.
- Mobile background voice notifications — needs APNs setup work.
- US expansion (RESO, Fair Housing, RESPA) — Phase 6+.

## Files to look at first when onboarding to this repo

| Looking for | File |
|---|---|
| Build plan | `docs/PLAN.md` |
| Cost model | `docs/Stage_Voice_Plan.xlsx` |
| Voice latency budget | `docs/VOICE_PIPELINE.md` (Phase 2 deliverable) |
| Mobile architecture | `apps/mobile/README.md` |
| Harness entry point | `services/orchestrator/harness.py` |
| Agent definitions | `services/orchestrator/agents/*.py` |
| Tool implementations | `services/orchestrator/tools/*.py` |
| Voice pipeline | `services/voice-gateway/pipeline.py` |
| Test contract | `services/orchestrator/tests/test_agents.py` |
