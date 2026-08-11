# Casa·Orquesta · Voice — Build Plan

**Goal.** Ship a voice-conversational mobile app (iOS + Android) backed by the Claude Agent SDK harness, running in a stage environment that 5–10 real testers can hit. Carry forward every proven piece of the MVP — the three agents, the 11 tools, the 16-listing CDMX + Morelos catalog, the LFPDPPP/NOM-151/CFDI scaffolding — and add voice as the primary interface.

**Timebox.** 8 calendar weeks (40 working days) split into 4 phases of 2 weeks each.

**Engineering model.** Claude does ~80% of implementation; you review, integration-test, and pilot-test.

## Why this plan exists as its own thing

The MVP repo proves the **logic**: agents route correctly, tools call cleanly, traces are auditable. This plan transports that logic into a **voice-first, mobile-native, production-trajectory** product. Three big shifts happen at once:

1. **Voice replaces chat as the primary interaction.** Conversations are spoken in es-MX, not typed.
2. **The Claude Agent SDK becomes the harness.** Custom `agents.py` is retired in favor of the SDK's agent/tool/hook primitives — same behavior contract (161 tests), different machinery.
3. **The product runs on mobile devices**, not in browser tabs — capturing audio, playing audio, handling background/foreground states, push notifications, offline tolerance.

Doing all three together is intentional. The SDK gives us streaming, which voice needs. Mobile gives us microphone access, which voice needs. The MVP's HTTP-based service contracts already match what mobile expects.

## Success criteria

By end of week 8 a tester should be able to:

1. Install the app from TestFlight (iOS) or an internal Android distribution.
2. Sign in with phone OTP. Accept LFPDPPP Aviso de Privacidad once.
3. Tap a mic button, say *"Busco un departamento en Roma Norte de 2 recámaras hasta 8 millones"*, and within ~1.5 s see the agent answer back **in spoken Spanish** while listing cards render on screen.
4. Say *"Agenda una visita al primero"* and have the system reconcile three time slots with simulated WhatsApp confirmations to the owner and agent.
5. Say *"¿Tiene gravámenes?"* and hear the audit agent's response.
6. Receive a follow-up notification on WhatsApp (real Cloud API).
7. See their full session and the agent's decision trace in the in-app audit log.

Performance targets:

- **End-to-end voice latency** (user stops talking → first audio plays): **≤ 1.5 s** P50, ≤ 2.5 s P95.
- **Wake-word-free push-to-talk** with barge-in support.
- **App cold start to first useful screen**: ≤ 2 s.
- **Battery cost** of a 5-minute voice session: ≤ 3% on a recent iPhone, ≤ 5% on mid-range Android.

## Phase plan

| Phase | Weeks | Theme | Critical-path output |
|---|---|---|---|
| **1** | 1–2 | Backend on the SDK harness | Backend services running on Fly with Postgres + Redis; orchestrator ported to Claude Agent SDK; 161+ tests green; streaming `/agent/run/stream` endpoint live |
| **2** | 3–4 | Voice gateway service | WebSocket bidirectional audio: Deepgram es-MX STT in, ElevenLabs Flash es-MX TTS out; latency < 1.5 s P50 against the orchestrator |
| **3** | 5–6 | Mobile app skeleton (RN + Expo) | iOS + Android builds; mic + speaker pipeline; PTT UX; renders listings, schedule slots, audit cards |
| **4** | 7–8 | Identity + compliance + tester onboarding | Phone OTP; LFPDPPP consent; DSAR; WhatsApp Cloud; audit trail; 3 internal testers; TestFlight build |

## Phase 1 — Backend on the SDK harness (Weeks 1–2)

**Output:** the seven backend services from `casa-orquesta-mvp` running on Fly.io with Postgres + Redis, plus the orchestrator rewritten on the Claude Agent SDK, plus a new `/agent/run/stream` SSE endpoint that streams agent reasoning and tool calls as they happen.

**What gets ported (mostly verbatim):**

- `services/listings/` → schema lifted to Postgres + pgvector; same RESO-aligned fields; same JSON API.
- `services/matching/` → same hybrid filter + rerank logic; embeddings now persisted via pgvector instead of computed at query time.
- `services/scheduling/` → same multi-party calendar; state moved to Redis.
- `services/documents/` → identical Promesa PDF generation; PDFs stored in S3/Tigris.
- `services/payments/` → CFDI XML structure unchanged; Facturama sandbox wired (gated by `CFDI_PAC_SIMULATED` flag).
- `services/comms/` → simulator preserved; new real-WhatsApp adapter behind a flag.
- `services/gateway/` → Nginx config with the resolver trick already present in the MVP.

**What gets rewritten (this is the real Phase 1 work):**

- `services/orchestrator/` becomes a Claude Agent SDK harness:
  ```
  services/orchestrator/
  ├── main.py                 # FastAPI; /chat, /agent/run, /agent/run/stream, /agents
  ├── harness.py              # Single entry point: loads SDK, registers agents
  ├── agents/
  │   ├── realestate.py       # Orchestrator agent (Sonnet)
  │   ├── locator.py          # 6 tools (Haiku)
  │   └── audit.py            # 5 tools (Haiku)
  ├── tools/
  │   ├── listings_tools.py   # search_listings, get_listing, compare_listings
  │   ├── people_tools.py     # find_buyers, find_collaborator_agents, find_brokers
  │   ├── audit_tools.py      # review_promesa, rpp/catastro/inegi/sat
  │   └── registry.py         # MCP-style descriptors
  ├── hooks/
  │   ├── audit_log.py        # Pre/post tool → tamper-evident log
  │   ├── pii_redaction.py    # Presidio + Spanish NER before LLM
  │   ├── spend_caps.py       # Per-tenant per-day caps
  │   └── trace.py            # Emit /agents.html-compatible trace events
  ├── datasets.py             # PORTED verbatim from MVP
  └── tests/test_agents.py    # PORTED + adapted to SDK
  ```
- The behavior contract is the 161 assertions from `casa-orquesta-mvp/tests/test_agents.py`. If those stay green, the migration is correct. New tests get added for streaming and hooks.
- The simulated fallback (no API key) is preserved by wrapping the SDK's client with a stub that returns deterministic outputs — same trace shape as real Claude.

**Phase 1 acceptance gate.** 161 assertions green; SSE stream emits agent events to a `curl` consumer; spend caps enforced; PII redaction tested with a Mexican RFC + INE number in the input.

## Phase 2 — Voice gateway service (Weeks 3–4)

**Output:** a new `services/voice-gateway/` that accepts a WebSocket from the mobile app, streams audio to Deepgram, feeds the transcript to the orchestrator (via the new SSE endpoint), streams the text response to ElevenLabs Flash, and streams the synthesized audio back to the mobile app — all under a 1.5 s end-to-end budget on a healthy LTE connection in Mexico City.

**Architecture:**

```
mobile app ──WebSocket──► voice-gateway ──WebSocket──► Deepgram STT (es-MX, Nova-2)
                              │
                              ├──(transcript)──► orchestrator /agent/run/stream
                              │                       │
                              │                       └──(streamed reply text)──► voice-gateway
                              │
                              └──(text chunks)──► ElevenLabs Flash es-MX (streaming)
                                                       │
                                                       └──(audio frames)──► mobile app
```

**Key engineering challenges:**

1. **Latency budget allocation:**
   - First audio frame to STT partial result: 100–200 ms
   - End-of-speech detection (Deepgram VAD): 150–300 ms
   - Orchestrator first token: 200–500 ms (Haiku) / 400–800 ms (Sonnet)
   - TTS first audio frame: 100–250 ms
   - Network RTT (Mexico City LTE → US-East): 50–150 ms each way
   - **Total P50 target: ≤ 1.5 s**; achievable if Haiku handles 70%+ of turns.
2. **Barge-in handling**: detect when the user starts speaking again while the agent is talking; stop TTS playback, flush the in-flight response.
3. **Tool-call latency**: a search_listings call adds ~150–300 ms. The agent should not speak until the tool result is back; otherwise it babbles a placeholder.
4. **Network resilience**: drops on LTE in Mexico are common; the WebSocket reconnects within 500 ms and resumes the conversation.

**File layout:**

```
services/voice-gateway/
├── main.py                    # FastAPI with WebSocket endpoint
├── pipeline.py                # Audio frames → STT → orchestrator → TTS → audio
├── stt/
│   ├── deepgram_client.py     # WS adapter, Nova-2 es-MX
│   └── interfaces.py          # STTProvider protocol (for swapping)
├── tts/
│   ├── elevenlabs_client.py   # Streaming TTS adapter (Flash v2.5)
│   └── interfaces.py
├── orchestrator_client.py     # SSE client for /agent/run/stream
├── barge_in.py                # VAD + interrupt logic
└── tests/test_pipeline.py     # Latency + barge-in + reconnect tests
```

**Phase 2 acceptance gate.** Spoken query "Busco un departamento en Roma Norte" → spoken Spanish reply with listings rendered, P50 latency ≤ 1.5 s measured locally; barge-in works; WebSocket recovers from a forced disconnect within 1 s.

## Phase 3 — Mobile app skeleton (Weeks 5–6)

**Output:** a React Native + Expo app that builds for iOS (TestFlight) and Android (internal track), captures and plays audio, talks to the voice gateway, renders the visual cards the agents produce, and works offline-tolerant for non-voice screens.

**Stack:**

- **React Native + Expo (SDK 52+)** — fast iteration, easy distribution, good Spanish locale support.
- **`expo-av`** + **`expo-audio`** for capture and playback, or **`react-native-webrtc`** if we need lower-level control.
- **State**: Zustand or Jotai (lightweight; no Redux overhead for this app size).
- **Navigation**: `expo-router` (file-based).
- **Auth**: Auth0 SDK with phone OTP.
- **Network**: native `WebSocket` + `fetch`; SSE via `react-native-sse` for the secondary text channel.
- **i18n**: es-MX primary, en-US toggle for QA.

**Screens (minimal viable set):**

```
apps/mobile/src/screens/
├── OnboardingScreen.tsx       # Phone OTP, Aviso de Privacidad acceptance
├── HomeScreen.tsx             # Main voice interaction surface
├── ListingDetailScreen.tsx    # Tapping a card from voice search
├── VisitScheduleScreen.tsx    # Slot selection
├── AuditViewScreen.tsx        # See RPP/Catastro/INEGI/SAT findings
├── DocumentsScreen.tsx        # View/sign promesas
├── SessionTraceScreen.tsx     # Agent decision trace (LFPDPPP visibility)
└── SettingsScreen.tsx         # Account, consent log, DSAR
```

**Voice UX:**

- Big circular mic button on Home, push-to-talk by default.
- Switch to hands-free (always-listening) is an explicit toggle, off by default — saves battery and avoids accidental capture.
- Visual feedback: pulsing mic when listening, animated waveform when agent speaks, agent-chip strip showing which agents are running (realestate / locator / audit) — same chips as `casa-orquesta-mvp/frontend/buyer.html`.
- Cards (listings, slots, audit, promesa) appear below the mic in a scrollable feed; tapping a card narrows the next voice turn's context (the focus_listing_id / focus_document_id pattern from the MVP).

**Phase 3 acceptance gate.** Internal QA build runs on a real iPhone 13+ and a Pixel 6+, completes the "search → visit → audit" flow over real LTE in Mexico City, and stays under battery and latency targets.

## Phase 4 — Identity, compliance, tester onboarding (Weeks 7–8)

**Output:** the app is shippable to real testers. Phone auth works, LFPDPPP consent is captured, DSAR endpoint replies within seconds, WhatsApp Cloud sends real notifications, the audit trail is tamper-evident.

**Workstreams:**

- **Auth0 phone OTP** for buyers, magic link for realtors/owners. Tenant + user model in Postgres.
- **LFPDPPP Aviso de Privacidad** modal at first launch with voice-accept option ("Acepto" via voice). Consent stored with timestamp and version hash.
- **DSAR endpoint** (`/api/identity/dsar/export`) that fan-out-queries every service for the user's data and returns a ZIP.
- **WhatsApp Cloud API**: template registration in Meta Business; opt-in flow; 24-hour-window logic; SMS fallback via Twilio.
- **Audit trail**: every tool call, every consent action, every payment, every CFDI emission lands in an S3/Tigris bucket with hash-chained entries and WORM lock.
- **Tester onboarding pack**: 8-page PDF in es-MX, NDA, invite codes generation, support contact.
- **TestFlight + Google Play internal track** distribution.
- **Sentry + Langfuse self-hosted** observability live before the first tester logs in.

**Phase 4 acceptance gate.** Three internal testers (one founder, one designer, one external advisor) complete the full flow over 48 hours; zero P0 bugs; LFPDPPP compliance review passes a one-hour audit; ready to invite the first external broker.

## Decision gates (don't pre-commit, decide on the day)

| Gate | When | Decision | If "no" |
|---|---|---|---|
| 1 | End of Week 1 | SDK harness passing the ported 161 tests? | If not, slip Week 2 by 3 days, don't start streaming work until tests are green. |
| 2 | End of Week 2 | Streaming endpoint working, hooks emitting audit events? | If hooks are broken, defer voice barge-in to Week 4 (kept simpler). |
| 3 | End of Week 3 | Deepgram + ElevenLabs latency budget met locally? | If P50 > 2 s, downgrade to Cartesia TTS or accept the latency for first tester wave. |
| 4 | End of Week 4 | Voice gateway stable under barge-in + reconnect? | If barge-in is glitchy, ship without it; testers will tolerate; fix in Week 7 slack. |
| 5 | End of Week 5 | iOS build runs on real device with audio working? | If audio is flaky, deprioritize Android until iOS is solid (smaller test cohort first). |
| 6 | End of Week 6 | E2E voice flow on real device hits 1.5 s P50? | If 1.8–2.0 s, ship; if > 2.5 s, regional Anthropic endpoint or Haiku-only routing. |
| 7 | End of Week 7 | LFPDPPP review clean, Auth0 free tier OK? | If Auth0 caps approach, swap to Cognito (1-day pivot). |
| 8 | Mid Week 8 | Three internal testers comfortable with the flow? | If UX feels off, delay external testers by a week — better than poisoning the first impression. |

## Risk register

| Risk | Likelihood | Impact | Mitigation |
|---|---|---|---|
| Deepgram es-MX struggles with regional accents (Morelos vs. CDMX vs. Norte) | Med | High | Test with 5+ regional recordings before locking; AssemblyAI as backup |
| ElevenLabs Flash es-MX voice feels robotic | Low | Med | A/B against Cartesia Sonic Spanish; pick on tester preference |
| Latency exceeds 2 s on real LTE | Med | High | Edge deployment in Mexico region; tiered routing keeps Haiku as default |
| Apple App Store review rejects voice agent on first submission | Med | Med | Submit early (Week 7); have a desktop web fallback if rejection drags |
| LFPDPPP audit raises a finding | Low | High | DPO consult in Week 7; pre-audit with mock data |
| SDK migration discovers an edge case the 161 tests don't cover | Med | Med | Add regression tests for each new finding; never break the contract |
| Mobile team velocity slower than expected | High | Med | Phase 3 is the right place to add a contractor day if needed |
| Anthropic API quota / rate limits | Low | Med | Apply for production tier early; SLM fallback path stays alive |

## What's NOT in this plan (deferred)

- Real KYC (Truora INE OCR) — needed before any real money moves; deferred to Phase 5.
- Real CFDI emission to SAT — kept in sandbox until first real transaction.
- Real Mifiel e-signature — sandbox only for the pilot.
- INFONAVIT / FOVISSSTE workflows — Phase 5.
- US (RESO / Fair Housing / RESPA) — Phase 6+, after MX validation.
- On-prem SLM hosting — Phase 6+, after we know AI cost shape.
- Voice biometrics / speaker recognition — not needed for the pilot.

## Deliverables at end of Week 8

1. Two GitHub repos: `casa-orquesta-mvp` (preserved) and `casa-orquesta-voice` (this one, stage-ready).
2. iOS TestFlight build + Android internal track.
3. Public stage URL for backend: `https://stage-voice.casaorquesta.io`.
4. Three internal testers signed off; first external broker scheduled.
5. Updated `ARCHITECTURE.md`, `VOICE_PIPELINE.md`, `MOBILE_APP.md`.
6. Cost dashboard: per-tester voice spend, per-day.
7. 161+ assertions + voice gateway + mobile E2E tests, all green in CI.

See `Stage_Voice_Plan.xlsx` in this folder for the cost-integrated workbook.
