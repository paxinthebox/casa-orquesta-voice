# Mercado Libre Inmuebles MX scraper (Apify)

Live list-page scraper for **inmuebles.mercadolibre.com.mx**. Parses polycard SERP HTML (~48 listings/page, `_Desde_N` pagination).

**Status:** Step 0–1 complete (parser + fixture tests). Deploy to Apify before live ingest.

## Deploy

```bash
make deploy-mercadolibre-actor
```

Set in `.env`:

```bash
INGEST_MERCADOLIBRE_LIVE=1
APIFY_MERCADOLIBRE_ACTOR=YOUR_USERNAME/mercadolibre-inmuebles-mx-scraper
INGEST_PORTALS=...,mercadolibre
INGEST_MERCADOLIBRE=1
```

## Input

| Field | Description |
|-------|-------------|
| `startUrls` | Multi-URL batch (preferred for full-grid ingest) |
| `startUrl` | Single search URL (legacy) |
| `maxItemsPerUrl` | Cap per search URL (default 80) |
| `maxPages` | Pagination safety cap (default 10) |
| `proxyConfiguration` | **Required** — Apify residential **MX** proxy |

## Output

Flat rows for `scripts/ingestion/portals/mercadolibre.py` live mode:

`item_id`, `url`, `title`, `location`, `neighborhood`, `city`, `state`, `price`, `currency`, `bedrooms`, `bathrooms`, `area_m2`, `property_type`, `listing_mode`, `thumbnail`

## Local test

```bash
make test-mercadolibre-actor
make pilot-mercadolibre-live          # offline fixture
make pilot-mercadolibre-live-apify    # after deploy + .env
```

## Pilot URLs

- Coyoacán: `https://inmuebles.mercadolibre.com.mx/departamentos/venta/distrito-federal/coyoacan/`
- Prados: `https://inmuebles.mercadolibre.com.mx/casas/venta/distrito-federal/coyoacan/prado-churubusco/`
- Cuernavaca: `https://inmuebles.mercadolibre.com.mx/casas/venta/morelos/cuernavaca/`

See also the original scope notes in git history for architecture rationale.
