# Casa·Orquesta · Voice

Voice-first mobile app + production backend for Casa·Orquesta, the Real Estate Orchestration platform for Mexico.

This is a **separate repository** from `casa-orquesta-mvp`. The MVP repo stays as the text-chat demo for broker pitches; this one is the production-trajectory codebase: voice-conversational mobile UX, real users, real auth, real compliance, real cloud.

## What changes vs. the MVP repo

| Dimension | MVP repo (`casa-orquesta-mvp`) | Voice repo (`casa-orquesta-voice`) |
|---|---|---|
| Primary interface | Web chat (`buyer.html`) | Mobile voice (React Native + Expo) |
| Agent layer | Custom `agents.py` | **Claude Agent SDK** (the harness) |
| AI I/O | Text in / text out | Voice in (Deepgram STT) → text → Claude → text → Voice out (ElevenLabs TTS) |
| Persistence | SQLite + in-memory dicts | Postgres + Redis on Fly.io |
| Auth | Mock | Auth0 phone OTP |
| Audit | In-memory log | Tamper-evident S3/Tigris WORM |
| Comms | WhatsApp simulator | WhatsApp Cloud API + voice channel |
| Compliance | Designed but not active | LFPDPPP consent + DSAR + LFPIORPI audit live |
| Frontend | Multi-page HTML demo | React Native app (iOS + Android) |
| Tests | 161 assertions | 161+ ported + voice pipeline + mobile E2E |

## Why a separate repo

- The MVP repo is a **stable artifact** for ongoing brokerage demos — touching it risks the pitch.
- The voice/mobile build introduces React Native and the Claude Agent SDK, which have different CI/CD needs and shouldn't slow the MVP repo's lightweight workflow.
- Both repos can coexist in the same monorepo later if cohesion becomes more valuable than isolation — for now, isolation wins.

## Repo layout

```
casa-orquesta-voice/
├── apps/
│   └── mobile/                React Native + Expo voice client (iOS + Android)
├── services/
│   ├── voice-gateway/         WebSocket STT/TTS pipeline (Deepgram + ElevenLabs)
│   ├── orchestrator/          Claude Agent SDK harness (realestate + locator + audit)
│   ├── identity/              Auth0 / phone OTP integration
│   ├── listings/              Listings CRUD (Postgres)
│   ├── matching/              Hybrid matching
│   ├── scheduling/            Multi-party visit calendar
│   ├── documents/             Promesa PDF + Mifiel
│   ├── payments/              Payments + CFDI (Facturama sandbox)
│   ├── comms/                 WhatsApp + SMS + voice
│   └── gateway/               Nginx
├── infra/                     Terraform + Fly.io configs
├── docs/                      PLAN.md, ARCHITECTURE.md, VOICE_PIPELINE.md, …
├── tests/                     Backend + voice + mobile E2E
└── scripts/                   Helper scripts (db seed, key rotation, etc.)
```

## Quick start

```bash
# 1. Bootstrap — idempotent, safe to re-run
./scripts/bootstrap.sh
# - Copies .env.example to .env (if absent)
# - Installs Python deps per backend service
# - Installs mobile npm deps
# - Configures pre-commit hooks

# 2. Edit .env with your keys (at minimum ANTHROPIC_API_KEY; rest can stay blank for sim mode)
$EDITOR .env

# 3. Verify the green gate
./scripts/verify.sh
# - ruff lint + mypy typecheck (backend)
# - 161+ assertion behavior contract (when test_agents.py is ported)
# - ESLint + tsc (mobile)
# - Exits 0 only if every step passes

# 4. Start the full stack
make dev                   # docker compose up
make check                 # run the 161+ assertion test suite
make eval                  # run orchestrator eval cases (simulated, see evals/)
make pilot-smoke           # routing + guardrail evals + health spot-check
make market-sync           # push ingest cache + restart catalog (after ingest)
make mobile-ios            # opens iOS simulator

# Useful checks
make logs                  # tail all services
make down                  # stop everything
make reset                 # nuke volumes and rebuild

# Cursor agent helpers (optional): .agents/skills/apify-* + .cursor/mcp.json
# After pull: Cursor Settings → MCP → enable "apify" → OAuth once.
# Scoped to our portal Actors only (no Store-wide scrape).

# Whole-market inventory (Inmuebles24 + Vivanuncios portal + optional EasyBroker)
# 1. Add APIFY_API_TOKEN to .env (https://console.apify.com)
# 2. Portal actors (override in .env if needed):
#    APIFY_INMUEBLES24_ACTOR=azzouzana/inmuebles24-scraper-pro-by-search-url
#    APIFY_VIVANUNCIOS_ACTOR=stealth_mode/vivanuncios-property-search-scraper
#    # Or custom live: make deploy-vivanuncios-actor → INGEST_VIVANUNCIOS_LIVE=1
#    APIFY_VIVANUNCIOS_BATCH_SIZE=15   # multi-URL Apify runs (also Lamudi/ML live)
#    INGEST_PORTALS=inmuebles24,vivanuncios
# 3. Optionally EASYBROKER_API_KEY for your CRM/MLS inventory
# 4. With backend running:
make ingest-market          # portals + EasyBroker → listings + people index
make validate-portals       # colonia pilot (Prados Churubusco) + per-portal validation
docker compose restart orchestrator listings
# Edit search regions/types in data/market_queries.json (CDMX + all Morelos municipalities)
# Colonia-level scrapes (Prados Churubusco, Roma Norte, Santa Fe Lifestyle, …):
#   data/market_queries_colonias.json
# Optional per-query override: "searchUrl": "https://www.inmuebles24.com/..."
```

### Whole market in the app

| Layer | Source | What you get |
|-------|--------|--------------|
| **Portal** | Inmuebles24 + Vivanuncios via Apify | Public listings across CDMX boroughs + colonias + Morelos (`data/market_queries.json`, `data/market_queries_colonias.json`). Per-query validation in `market_manifest.json`. |
| **People from listings** | Publisher/agent on each row | Broker + collaborator cards (deduped) |
| **Your CRM** | EasyBroker (optional) | Your inventory, team, MLS partners |
| **Deduped catalog** | Merged + `dedupe.py` | One card per property in the listings service |
| **Demo fallback** | `data/seeds/listings.json` | Stays until real ingest fills the DB |

Voice search uses the **local catalog** (`search_listings` → matching → listings DB), so ingested market data appears as cards after sync.

### Scheduled refresh (cron / GitHub Actions)

After the first manual `make ingest-market`, keep the catalog fresh on a schedule:

| Target | Command | When |
|--------|---------|------|
| **Local cron (full)** | `make ingest-market-weekly` | Weekly — Apify scrape + push + restart |
| **Local cron (push only)** | `make market-sync-daily` | Daily — push `data/cache/market_listings.json` (no Apify) |
| **GitHub Actions** | `.github/workflows/market-ingest.yml` | Sun 09:00 UTC full ingest; daily 12:00 UTC push-only |

Local cron examples (Mac/Linux; adjust paths):

```cron
0 3 * * 0  /path/to/casa-orquesta-voice/scripts/cron/ingest-market-weekly.sh >> /path/to/logs/ingest-market.log 2>&1
0 6 * * *  /path/to/casa-orquesta-voice/scripts/cron/market-sync-daily.sh >> /path/to/logs/market-sync.log 2>&1
```

GitHub: add repository secret `APIFY_API_TOKEN` (required for weekly portal scrape). Optional: `EASYBROKER_API_KEY`, variable `LISTINGS_URL` (remote listings API for push-only without docker on the runner). Manual runs: **Actions → Market ingest → Run workflow**.

Requires `make dev` (or a reachable `LISTINGS_URL`) for push steps. Full ingest takes 1–3+ hours and consumes Apify credits.

### EasyBroker (optional add-on)

| What | EasyBroker API | In the app |
|------|----------------|------------|
| **Your properties** | `GET /properties` (+ detail for photos) | Listing cards with images |
| **Your team** | `GET /users` | Collaborator cards |
| **MLS partner agencies** | `GET /collaborations` | Broker cards |
| **Network inventory** | `GET /mls_properties` | Requires **API MLS** plan |

CI runs `./scripts/verify.sh` on every push and PR via `.github/workflows/ci.yml`. A merge is gated on green verify + green behavior contract.

## Status

**Stage build** — core stack runs locally via `make dev`. Voice search, multi-client threads, guardrails, evals, and visit scheduling are wired for pilot testing. See `scripts/pilot/TESTFLIGHT.md` for the Week 8 tester checklist.

**Planned (post-pilot):** [Maps, calendar sync & property tour routing](docs/FEATURE_TOURS_MAPS_CALENDAR.md) — env prepared (`make phase6-check`); MapScreen ships when `EXPO_PUBLIC_PHASE6_MAPS=1`.

The MVP repo at `../casa-orquesta-mvp` remains the text-chat behavior reference.

## License

Proprietary — for pilot evaluation.
# casa-orquesta-voice
