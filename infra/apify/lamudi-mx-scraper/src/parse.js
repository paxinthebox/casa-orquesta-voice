const RESIDENTIAL_TYPES = new Set([
    'apartment',
    'house',
    'singlefamilyresidence',
    'condominium',
    'realestatelisting',
    'product',
    'landform',
    'accommodation',
]);

export function listingIdFromUrl(url = '') {
    const match = String(url).match(/\/detalle\/([^/?#]+)/);
    return match ? match[1] : '';
}

function normalizeState(raw = '') {
    const folded = raw.trim().toLowerCase();
    if (['cdmx', 'ciudad de mexico', 'ciudad de méxico', 'distrito federal'].includes(folded)) {
        return 'CDMX';
    }
    if (folded === 'morelos') return 'Morelos';
    return raw.trim();
}

function propertyType(schemaType = '', title = '') {
    const raw = schemaType.toLowerCase();
    const name = title.toLowerCase();
    if (raw === 'apartment' || name.includes('departamento') || name.includes('depto')) {
        return 'departamento';
    }
    if (raw === 'house' || raw === 'singlefamilyresidence' || name.includes('casa')) {
        return 'casa';
    }
    if (name.includes('terreno') || name.includes('lote') || raw === 'landform') {
        return 'terreno';
    }
    if (raw === 'realestatelisting') {
        if (name.includes('departamento')) return 'departamento';
        if (name.includes('casa')) return 'casa';
        if (name.includes('terreno') || name.includes('lote')) return 'terreno';
    }
    return 'inmueble';
}

function neighborhoodFromAddress(address = {}) {
    const street = String(address.streetAddress || '').trim();
    if (!street) return '';
    const parts = street.split(',').map((p) => p.trim()).filter(Boolean);
    if (parts.length >= 2) return parts[1];
    return parts[0] || '';
}

function listingMode(startUrl = '') {
    const lowered = startUrl.toLowerCase();
    return lowered.includes('/for-rent/') || lowered.includes('en-renta') ? 'rent' : 'sale';
}

export function pageUrl(startUrl, page) {
    const url = new URL(startUrl);
    if (page <= 1) {
        url.searchParams.delete('page');
        return url.toString();
    }
    url.searchParams.set('page', String(page));
    return url.toString();
}

export function schemaItemToRow(item, startUrl = '') {
    const schemaType = String(item['@type'] || '');
    if (!RESIDENTIAL_TYPES.has(schemaType.toLowerCase())) return null;

    const url = String(item.url || item['@id'] || '').split('?')[0];
    const listingId = listingIdFromUrl(url);
    if (!listingId) return null;

    const title = String(item.name || '').trim();
    const description = String(item.description || '').trim();
    const address = item.address && typeof item.address === 'object' ? item.address : {};
    const city = String(address.addressLocality || '').trim();
    const state = normalizeState(String(address.addressRegion || ''));
    const neighborhood = neighborhoodFromAddress(address);

    const offers = item.offers && typeof item.offers === 'object' ? item.offers : {};
    const priceRaw = offers.price ?? item.price ?? 0;
    const price = Number.parseInt(String(priceRaw).replace(/,/g, ''), 10) || 0;

    const floor = item.floorSize && typeof item.floorSize === 'object' ? item.floorSize : {};
    const m2 = floor.value != null ? Number.parseFloat(String(floor.value)) : null;

    const geo = item.geo && typeof item.geo === 'object' ? item.geo : {};

    return {
        listing_id: listingId,
        url,
        title,
        description,
        neighborhood,
        city,
        state,
        price,
        currency: String(offers.priceCurrency || 'MXN'),
        bedrooms: item.numberOfBedrooms ?? null,
        bathrooms: item.numberOfBathroomsTotal ?? null,
        area_m2: Number.isFinite(m2) ? m2 : null,
        propertyType: propertyType(schemaType, title),
        lat: geo.latitude ?? null,
        lng: geo.longitude ?? null,
        listing_mode: listingMode(startUrl),
    };
}

export function parseJsonLdListings(payload, startUrl = '') {
    const graphs = [];
    if (Array.isArray(payload)) {
        for (const block of payload) {
            if (block && Array.isArray(block['@graph'])) graphs.push(block['@graph']);
        }
    } else if (payload && Array.isArray(payload['@graph'])) {
        graphs.push(payload['@graph']);
    }

    const rows = [];
    for (const graph of graphs) {
        for (const node of graph) {
            if (node['@type'] !== 'SearchResultsPage') continue;
            for (const entity of node.mainEntity || []) {
                if (entity['@type'] !== 'ItemList' || entity['@id'] !== '#listings') continue;
                for (const element of entity.itemListElement || []) {
                    const item = element?.item;
                    if (!item || typeof item !== 'object') continue;
                    const row = schemaItemToRow(item, startUrl);
                    if (row) rows.push(row);
                }
            }
        }
    }
    return rows;
}

export function parseSerpHtml(html, startUrl = '') {
    const match = String(html).match(/<script type="application\/ld\+json">([\s\S]*?)<\/script>/);
    if (!match) return [];
    const payload = JSON.parse(match[1]);
    return parseJsonLdListings(payload, startUrl);
}
