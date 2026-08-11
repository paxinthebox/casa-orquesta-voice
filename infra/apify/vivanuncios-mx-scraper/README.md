# Vivanuncios MX scraper (Apify)

Live list-page scraper for **vivanuncios.com.mx**. Parses Navent
`window.__PRELOADED_STATE__` from SERP HTML (falls back to `/d-…/{id}` links).
Supports **multi-URL batches** (`startUrls` / `urls`) with per-row `query_url`
tags for full-grid ingest.

Cloudflare blocks datacenter IPs — **MX residential proxy required**.

## Deploy

```bash
make deploy-vivanuncios-actor
# or:
cd infra/apify/vivanuncios-mx-scraper && npm install && apify push
```

Set in `.env`:

```bash
INGEST_VIVANUNCIOS_LIVE=1
APIFY_VIVANUNCIOS_ACTOR=YOUR_USERNAME/vivanuncios-mx-scraper
APIFY_VIVANUNCIOS_BATCH_SIZE=15
```

## Input

| Field | Description |
|-------|-------------|
| `startUrls` / `urls` | One or more verified search URLs (prefer `…/v1c…l…p1`) |
| `startUrl` | Single-URL legacy |
| `maxItemsPerUrl` | Cap per search URL (default 80) |
| `maxPages` | Pagination safety cap on `…pN` (default 10) |
| `proxyConfiguration` | **Required** — Apify residential **MX** |

## Output

Rows match `scripts/ingestion/portals/vivanuncios.py` flatten:

`posting_id`, `url`, `title`, `price_operation_types`, `posting_location`,
`real_estate_type`, `visible_pictures`, `query_url`, `listing_mode`

## Local test

```bash
make test-vivanuncios-actor
```
