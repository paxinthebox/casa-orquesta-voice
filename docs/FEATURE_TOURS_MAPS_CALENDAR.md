# Feature — Maps, calendar sync & property tour routing

**Status:** Environment prepared (Phase 6 prep landed) — UI/tools not shipped yet  
**Target:** Sprint 6+ after Week 8 closed-beta stabilizes  
**Owner:** TBD  
**MVP reference:** `../casa-orquesta-mvp/frontend/buyer.html` (calendar sync UI demo only — not wired)

## Goal

Let a buyer (or agent) say:

> *"Quiero visitar estas 4 propiedades el sábado, ordénalas de la más cercana a la más lejana y sincroniza mi calendario."*

…and get:

1. A **map** with pins + driving route
2. A **tour itinerary** (stop 1 → 2 → 3 → 4, with travel times)
3. **Calendar events** on buyer + agent calendars (with reminders)
4. **WhatsApp confirmations** to owner/agent (existing comms path)

## Current baseline

| Piece | Today |
|-------|--------|
| Listing geo | `lat` / `lng` in listing JSON (portal ingest when available; gaps possible) |
| Geocoding | Nominatim in `services/orchestrator/registries/geocode.py` (audit); not backfilled on all listings |
| Scheduling | Single listing → 3 proposed slots → confirm (`VisitScheduleScreen`, `scheduling` service) |
| Compare | `compare_listings` tool (price/size matrix, no distance) |
| Maps / calendar | **Not implemented**; Android permissions explicitly **block location & calendar** in `apps/mobile/app.json` |
| MVP parity | Calendar sync was **simulated UI** in `buyer.html`, not real OAuth |

## Architecture (target)

```
mobile (MapScreen, TourBuilder, CalendarConnect)
    │
    ▼
realestate_agent ──► plan_property_tour (new tool)
    │
    ├── routing service (new) ──► Mapbox / Google Directions / OSRM fallback
    ├── scheduling service (extended) ──► tours + multi-stop propose/confirm
    └── calendar-adapter (new) ──► Google Calendar API + Apple EventKit / ICS
```

**Principle:** routing and calendar logic live in **backend services**; one **orchestrator tool** serves voice; mobile UI mirrors the same APIs (same pattern as client profile + one-tap audit).

## Phase 0 — Geo foundation (~1 week)

**Prerequisite** before maps or routing.

1. **Backfill coordinates** on ingest + nightly job  
   - Listings missing `lat`/`lng` → geocode address via Nominatim (rate-limited, cached)  
   - Track `geo_source`: `portal` | `geocoded` | `missing`  
   - Flag listings outside CDMX + Morelos (`scripts/ingestion/market_geo.py`)

2. **Optional Postgres upgrade**  
   - Add `lat`, `lng`, `geom geography(POINT)` + GiST index on listings  
   - Enables “listings within N km” without scanning JSONB

3. **Haversine fallback**  
   - When Directions API is unavailable, sort by straight-line distance

**Acceptance:** ≥90% of ingested catalog has valid coordinates; search cards expose lat/lng consistently.

**Files (expected):**

- `scripts/ingestion/geocode_backfill.py`
- `services/listings/migrations/V00N__listing_geo.sql` (optional)
- `services/orchestrator/registries/geocode.py` — shared cache

## Phase 1 — Maps UI (~1.5 weeks)

### Mobile

- Add `react-native-maps` (or `expo-maps` when stable on target Expo SDK)
- New screens:
  - `MapScreen` — pins for search results / saved listings
  - `TourMapPreview` — numbered stops + polyline
- Entry points: Home feed “Ver en mapa”, Detail “Ver ubicación”, post-search map toggle

### Permissions

- Update `apps/mobile/app.json`: allow **foreground location** for “start from my location”
- es-MX usage string: *“Usamos tu ubicación para ordenar visitas y calcular rutas.”*
- **LFPDPPP:** disclose location in Aviso; do not persist raw GPS beyond session unless user opts in

### Backend

- `GET /listings/nearby?lat=&lng=&radius_km=` (listings or matching service)

**Acceptance:** User sees search results on a map; tapping a pin opens Detail.

**Files (expected):**

- `apps/mobile/src/screens/MapScreen.tsx`
- `apps/mobile/src/screens/TourMapPreview.tsx`
- `apps/mobile/src/api/routingClient.ts` (stub until Phase 2)

## Phase 2 — Tour routing: nearest → furthest (~2 weeks)

### Domain model (extend scheduling)

Today: `visits` = one `listing_id`. Add:

```text
tours
  id, tenant_id, buyer_id, agent_id
  status: draft | proposed | confirmed | completed | cancelled
  origin: { type: user_location | listing | address, lat, lng, label }
  listing_ids: ordered JSON array
  legs: [{ from, to, distance_m, duration_s, polyline }]
  proposed_slots: [...]          # whole-tour windows
  selected_slot
  dwell_minutes_per_stop: 45    # default
```

Child `visits` rows may link to `tour_id` for per-owner notifications.

### Routing service (`services/routing/` — new)

| Endpoint | Purpose |
|----------|---------|
| `POST /route/optimize` | `{ origin, listing_ids[], dwell_min }` → ordered stops + legs |
| `POST /route/directions` | Ordered waypoints → polyline + ETAs |

**Algorithm (pilot):**

1. Resolve origin (user GPS, client profile home, or first listing)
2. **Nearest-neighbor TSP heuristic**
3. Refine with **drive times** from Directions API
4. Insert dwell time (default 45 min/stop) + buffer (10 min between stops)

For 3–6 stops in CDMX/Morelos, nearest-neighbor + one Directions pass is sufficient.

### Directions provider

| Option | Pros | Cons |
|--------|------|------|
| **OSRM** | Free for dev | Public instance not SLA |
| **Mapbox Directions** | Solid MX coverage | New vendor |
| **Google Maps Directions** | Best MX traffic | Cost + billing setup |

**Recommendation:** Mapbox or Google for pilot; OSRM as dev fallback. Cache route matrices 24h.

### Orchestrator tool

```text
plan_property_tour
  inputs: listing_ids[], origin?, date_preference?, dwell_minutes?
  calls: routing.optimize → scheduling.propose_tour
  returns: ordered stops, total km/time, slot proposals, polyline ref
```

Router keywords: *“ruta”*, *“visitar varias”*, *“ordenar por distancia”*, *“recorrido”*, *“tour”*.

### Mobile UX

- `TourBuilderScreen` — multi-select from `last_candidates` / compare results
- `TourPreviewScreen` — ordered list + map + total time
- Voice: *“Arma un recorrido con las 3 que vimos”*

**Acceptance:** 4 listings with coords → single ordered tour; map shows numbered route; drive time within ±15% of spot-check.

**Files (expected):**

- `services/routing/main.py`
- `services/scheduling/migrations/0002_tours.sql`
- `services/orchestrator/tools/tour_tools.py`
- `apps/mobile/src/screens/TourBuilderScreen.tsx`
- `apps/mobile/src/screens/TourPreviewScreen.tsx`
- `services/orchestrator/tests/test_tour_routing.py`

## Phase 3 — Calendar sync (~2 weeks)

### Tier A — Ship first (low friction)

- Generate **`.ics`** / `webcal://` for whole tour
- One event per stop + optional travel blocks
- Mobile: share sheet → “Añadir al calendario”

### Tier B — Full sync

- **Google Calendar API** — OAuth in Settings; read `freeBusy`, write events with attendees
- **Apple EventKit** — on-device read/write after permission
- Encrypted refresh tokens in identity service (`calendar_connections` table)

### Scheduling extensions

```text
POST /schedule/tour/propose    → tour + slots (respect busy if calendar connected)
POST /schedule/tour/confirm     → confirm + calendar events + WhatsApp
GET  /calendar/status
POST /calendar/connect/google
DELETE /calendar/disconnect     → DSAR purge tokens
```

On confirm, preserve MVP multi-party pattern: buyer + agent + each owner notified for their stop window.

**Acceptance:** 3-stop tour confirm → 3 calendar events on device; conflict detection when Google connected.

**Files (expected):**

- `services/calendar/` or `services/scheduling/calendar_adapter.py`
- `services/identity/migrations/000N_calendar_connections.sql`
- `apps/mobile/src/screens/CalendarConnectScreen.tsx`

## Phase 4 — Voice + agent polish (~1 week)

- New feed card: **`TourCard`** (map thumbnail + stop count)
- Session state: `focus_tour_id`
- Re-plan: *“Empieza por Cuernavaca”* → re-optimize
- Spanish summary: *“Recorrido de 4 propiedades: 38 km, ~1h 20min de manejo…”*

**Behavior contract tests** (add to `test_agents.py`):

- Router: *“Arma recorrido con L-001 L-002 L-003”* → `plan_property_tour`
- Mock coords → assert nearest-neighbor order
- Single-listing `VisitScheduleScreen` flow unchanged

## Phase 5 — Pilot hardening (~1 week)

- Offline: cached route; queue confirm when online
- Listings without coords excluded + voice explains why
- Directions API spend caps (per tenant/day, like LLM caps)
- Audit log: tour planned, route provider, calendar writes
- Device QA on LTE in CDMX

## Timeline summary

| Phase | Duration | Depends on |
|-------|----------|------------|
| 0 Geo foundation | 1 wk | Ingest complete |
| 1 Maps UI | 1.5 wk | Phase 0 |
| 2 Tour routing | 2 wk | Phase 0–1 |
| 3 Calendar sync | 2 wk | Phase 2 (Tier A can parallel after Phase 2) |
| 4 Voice polish | 1 wk | Phase 2–3 |
| 5 Hardening | 1 wk | All |

**Total:** ~8–9 weeks — or **~5 weeks** for MVP slice (maps + haversine order + ICS only).

## MVP slice (smallest useful version)

1. Map of search results (Phase 0 + 1)
2. Manual multi-select → nearest-neighbor + haversine (Phase 2 lite, no paid Directions)
3. ICS export for tour (Phase 3 Tier A only)

## Environment (new)

```env
# Routing
ROUTING_PROVIDER=mapbox          # mapbox | google | osrm
MAPBOX_ACCESS_TOKEN=
GOOGLE_MAPS_API_KEY=

# Calendar (Tier B)
GOOGLE_CALENDAR_CLIENT_ID=
GOOGLE_CALENDAR_CLIENT_SECRET=
CALENDAR_TOKEN_ENCRYPTION_KEY=

# Tour defaults
TOUR_DWELL_MINUTES=45
TOUR_TRAVEL_BUFFER_MINUTES=10
TOUR_MAX_STOPS=6
```

## Compliance (Mexico pilot)

- **LFPDPPP:** location + calendar are sensitive — update Aviso, bump consent version, include tours + calendar tokens in DSAR export
- Do **not** store raw GPS trails — origin at plan time + hashed route cache only
- Owner access windows are operational — audit log only, not marketing analytics

## Deferred (post-pilot)

- Live traffic re-routing mid-tour
- Multi-buyer / one-agent fleet optimization
- Waze / Uber deep links
- Optimal TSP for 10+ stops
- Realtor tour-assignment dashboard

## Task checklist (for TASK_PROMPTS.md)

- [ ] P6.0 — Geo backfill + listing coord coverage metrics
- [ ] P6.1 — MapScreen + permissions + nearby API
- [ ] P6.2 — Routing service + `plan_property_tour` tool
- [ ] P6.3 — TourBuilder / TourPreview mobile + TourCard
- [ ] P6.4 — ICS export (Tier A calendar)
- [ ] P6.5 — Google Calendar OAuth (Tier B)
- [ ] P6.6 — Behavior contract + device QA for tour flow

## Related docs

- `docs/PLAN.md` — Week 8 pilot scope (visit scheduling exists; tours do not)
- `docs/ARCHITECTURE.md` — service map
- `scripts/ingestion/market_geo.py` — CDMX + Morelos geo validation
- `apps/mobile/src/screens/VisitScheduleScreen.tsx` — single-property flow to preserve
