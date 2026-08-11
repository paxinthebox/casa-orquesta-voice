# Lamudi MX scraper (Apify)

Live list-page scraper for **lamudi.com.mx**. Parses structured **JSON-LD ItemList** from SERP HTML (30 listings/page, `?page=N` pagination).

## Deploy

```bash
cd infra/apify/lamudi-mx-scraper
npm install
apify login          # once
apify push           # publishes to YOUR_USERNAME/lamudi-mx-scraper
```

Set in `.env`:

```bash
INGEST_LAMUDI_LIVE=1
APIFY_LAMUDI_ACTOR=YOUR_USERNAME/lamudi-mx-scraper
```

## Input

| Field | Description |
|-------|-------------|
| `startUrls` | Multi-URL batch (preferred for full-grid ingest) |
| `startUrl` | Single search URL (legacy) |
| `maxItemsPerUrl` | Cap per search URL (default 80) |
| `maxPages` | Pagination safety cap (default 10) |
| `proxyConfiguration` | **Required** — Apify residential **MX** proxy (CloudFront blocks datacenter IPs) |

## Output

Rows match `scripts/ingestion/portals/lamudi.py` live flatten:

`listing_id`, `url`, `title`, `description`, `neighborhood`, `city`, `state`, `price`, `currency`, `bedrooms`, `bathrooms`, `area_m2`, `propertyType`, `lat`, `lng`, `listing_mode`, `query_url`

## Local test

```bash
npm test
python3 ../../../scripts/pilot_lamudi_live.py --local prado
```

## Pilot URLs

- Prados: `https://www.lamudi.com.mx/distrito-federal/coyoacan/prado-churubusco/for-sale/`
- Cuernavaca: `https://www.lamudi.com.mx/morelos/cuernavaca/for-sale/`
