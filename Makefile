.PHONY: dev down logs build check reset mobile-ios mobile-android lint ingest-public ingest-easybroker ingest-market ingest-vivanuncios ingest-vivanuncios-retry ingest-propiedades ingest-mercadolibre ingest-morelos-rent ingest-rent push-market market-sync ingest-market-weekly market-sync-daily validate-portals pilot-smoke pilot-lamudi-live pilot-lamudi-live-apify test-lamudi-actor deploy-lamudi-actor pilot-mercadolibre-live pilot-mercadolibre-live-apify test-mercadolibre-actor deploy-mercadolibre-actor test-vivanuncios-actor deploy-vivanuncios-actor eval eval-json eval-live eval-promptfoo geocode-backfill phase6-check

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

ingest-easybroker:
	$(PYTHON) scripts/ingest_market.py --no-portal --merge-cache

ingest-market:
	$(PYTHON) scripts/ingest_market.py

ingest-vivanuncios-retry:
	INGEST_PORTALS=vivanuncios $(PYTHON) scripts/ingest_market.py --no-easybroker --merge-cache \
		--queries data/market_queries_vivanuncios_retry.json

# Full market query grid (CDMX + Morelos + colonias), VA only — merges into cache.
ingest-vivanuncios:
	INGEST_PORTALS=vivanuncios INGEST_VIVANUNCIOS=1 INGEST_VIVANUNCIOS_LIVE=1 \
	APIFY_VIVANUNCIOS_ACTOR=$${APIFY_VIVANUNCIOS_ACTOR:-flax_lump/vivanuncios-mx-scraper} \
		$(PYTHON) scripts/ingest_market.py --no-easybroker --merge-cache

ingest-propiedades:
	INGEST_PORTALS=propiedades $(PYTHON) scripts/ingest_market.py --no-easybroker --merge-cache

# Playwright actor ~1–3 min/query; full grid ~170 queries. Merges into existing cache.
ingest-mercadolibre:
	INGEST_PORTALS=mercadolibre INGEST_MERCADOLIBRE=1 INGEST_MERCADOLIBRE_LIVE=1 \
	APIFY_MERCADOLIBRE_ACTOR=flax_lump/mercadolibre-inmuebles-mx-scraper \
	APIFY_MERCADOLIBRE_TIMEOUT_SEC=360 \
		$(PYTHON) scripts/ingest_market.py --no-easybroker --merge-cache

validate-portals:
	$(PYTHON) scripts/validate_portal_ingest.py --merge-cache --no-push

ingest-morelos-rent:
	$(PYTHON) scripts/ingest_market.py --no-easybroker --merge-cache \
		--queries data/market_queries_morelos_rent.json

ingest-rent:
	$(PYTHON) scripts/ingest_market.py --no-easybroker --merge-cache \
		--queries data/market_queries_rent.json
	@echo "Rent ingest: CDMX boroughs + Morelos state-wide + per-municipality (see data/market_queries_rent.json)"

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

pilot-lamudi-live:
	$(PYTHON) scripts/pilot_lamudi_live.py --local both

pilot-lamudi-live-apify:
	$(PYTHON) scripts/pilot_lamudi_live.py both

test-lamudi-actor:
	cd infra/apify/lamudi-mx-scraper && npm install && npm test

# Publishes infra/apify/lamudi-mx-scraper → YOUR_USERNAME/lamudi-mx-scraper on Apify.
# Requires: APIFY_API_TOKEN in .env (or apify login). Then set APIFY_LAMUDI_ACTOR in .env.
deploy-lamudi-actor:
	cd infra/apify/lamudi-mx-scraper && npm install
	@set -a; [ -f .env ] && . ./.env; set +a; \
	TOKEN="$${APIFY_TOKEN:-$$APIFY_API_TOKEN}"; \
	if [ -z "$$TOKEN" ]; then \
		echo "Set APIFY_API_TOKEN in .env or run: apify login"; exit 1; \
	fi; \
	cd infra/apify/lamudi-mx-scraper && \
		npx --yes apify-cli@0.21.8 login --token "$$TOKEN" && \
		CI=true APIFY_TOKEN="$$TOKEN" npx --yes apify-cli@0.21.8 push --force

pilot-mercadolibre-live:
	$(PYTHON) scripts/pilot_mercadolibre_live.py --fixture coyoacan

pilot-mercadolibre-live-apify:
	$(PYTHON) scripts/pilot_mercadolibre_live.py coyoacan

test-mercadolibre-actor:
	cd infra/apify/mercadolibre-inmuebles-mx-scraper && npm install && npm test

# Publishes infra/apify/mercadolibre-inmuebles-mx-scraper → YOUR_USERNAME/mercadolibre-inmuebles-mx-scraper
deploy-mercadolibre-actor:
	cd infra/apify/mercadolibre-inmuebles-mx-scraper && npm install
	@set -a; [ -f .env ] && . ./.env; set +a; \
	TOKEN="$${APIFY_TOKEN:-$$APIFY_API_TOKEN}"; \
	if [ -z "$$TOKEN" ]; then \
		echo "Set APIFY_API_TOKEN in .env or run: apify login"; exit 1; \
	fi; \
	cd infra/apify/mercadolibre-inmuebles-mx-scraper && \
		npx --yes apify-cli@0.21.8 login --token "$$TOKEN" && \
		CI=true APIFY_TOKEN="$$TOKEN" npx --yes apify-cli@0.21.8 push --force

test-vivanuncios-actor:
	cd infra/apify/vivanuncios-mx-scraper && npm install && npm test

# Publishes infra/apify/vivanuncios-mx-scraper → YOUR_USERNAME/vivanuncios-mx-scraper
# Then: INGEST_VIVANUNCIOS_LIVE=1 APIFY_VIVANUNCIOS_ACTOR=YOUR_USERNAME/vivanuncios-mx-scraper
deploy-vivanuncios-actor:
	cd infra/apify/vivanuncios-mx-scraper && npm install
	@set -a; [ -f .env ] && . ./.env; set +a; \
	TOKEN="$${APIFY_TOKEN:-$$APIFY_API_TOKEN}"; \
	if [ -z "$$TOKEN" ]; then \
		echo "Set APIFY_API_TOKEN in .env or run: apify login"; exit 1; \
	fi; \
	cd infra/apify/vivanuncios-mx-scraper && \
		npx --yes apify-cli@0.21.8 login --token "$$TOKEN" && \
		CI=true APIFY_TOKEN="$$TOKEN" npx --yes apify-cli@0.21.8 push --force

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
