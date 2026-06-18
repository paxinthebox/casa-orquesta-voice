.PHONY: dev down logs build check reset mobile-ios mobile-android lint ingest-public ingest-market push-market market-sync ingest-market-weekly market-sync-daily pilot-smoke eval eval-json eval-live eval-promptfoo geocode-backfill phase6-check

PYTHON ?= $(shell command -v python3.13 2>/dev/null || command -v python3.12 2>/dev/null || command -v python3.11 2>/dev/null || command -v python3)

dev:
	docker compose up -d --build
	@echo ""
	@echo "Casa·Orquesta Voice running:"
	@echo "  Backend gateway:    http://localhost:$${GATEWAY_PORT:-8080}/"
	@echo "  Voice WS endpoint:  ws://localhost:$${VOICE_GATEWAY_PORT:-8010}/voice/{tenantId}/{userId}"
	@echo "  Orchestrator:       http://localhost:$${ORCHESTRATOR_PORT:-8001}/health"
	@echo "  Langfuse UI:        http://localhost:$${LANGFUSE_PORT:-3000}/"
	@echo ""
	@echo "  After first boot or DB reset, load live inventory:"
	@echo "    make push-market   (or make repair-market)"

down:
	docker compose down

logs:
	docker compose logs -f --tail=100

build:
	docker compose build

check:
	$(PYTHON) services/orchestrator/tests/test_agents.py

reset:
	docker compose down -v
	docker compose up -d --build

mobile-ios:
	cd apps/mobile && npm run ios

mobile-android:
	cd apps/mobile && npm run android

lint:
	cd apps/mobile && npm run lint && npm run typecheck
	$(PYTHON) -m ruff check services/

ingest-public:
	$(PYTHON) scripts/ingest_public.py

ingest-market:
	$(PYTHON) scripts/ingest_market.py

push-market:
	$(PYTHON) scripts/ingest_market.py --push-only

market-sync:
	chmod +x scripts/pilot/sync_market.sh
	./scripts/pilot/sync_market.sh $(if $(PUSH_ONLY),--push-only,)

ingest-market-weekly:
	chmod +x scripts/cron/ingest-market-weekly.sh
	./scripts/cron/ingest-market-weekly.sh

market-sync-daily:
	chmod +x scripts/cron/market-sync-daily.sh
	./scripts/cron/market-sync-daily.sh

pilot-smoke:
	chmod +x scripts/pilot/voice_smoke.sh
	./scripts/pilot/voice_smoke.sh

repair-market:
	$(PYTHON) scripts/ingest_market.py --repair-cache --push-only

# Agent evals — simulated by default (see evals/README.md)
eval:
	$(PYTHON) scripts/evals/run_evals.py

eval-json:
	$(PYTHON) scripts/evals/run_evals.py --json

eval-live:
	$(PYTHON) scripts/evals/run_evals.py --live

eval-promptfoo:
	npx promptfoo eval -c evals/promptfoo/promptfooconfig.yaml

eval-promptfoo-live:
	npx promptfoo eval -c evals/promptfoo/promptfooconfig.yaml --providers orchestrator-live

# Phase 6 — maps, tours, routing (see docs/FEATURE_TOURS_MAPS_CALENDAR.md)
geocode-backfill:
	$(PYTHON) scripts/ingestion/geocode_backfill.py

phase6-check:
	$(PYTHON) services/_shared/tests/test_geo.py
	$(PYTHON) services/routing/tests/test_routing.py
