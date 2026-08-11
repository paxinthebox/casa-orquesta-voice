import * as cheerio from 'cheerio';

const MLM_RE = /MLM-(\d{6,})/i;
export const ITEMS_PER_PAGE = 48;

export function itemIdFromUrl(url = '') {
    const match = String(url).match(MLM_RE);
    return match ? match[1] : '';
}

export function canonicalUrl(url = '') {
    const cleaned = String(url).trim().split('#')[0].split('?')[0];
    return cleaned;
}

export function listingMode(startUrl = '') {
    const lowered = startUrl.toLowerCase();
    return lowered.includes('/renta/') ? 'rent' : 'sale';
}

export function propertyTypeFromUrl(url = '') {
    try {
        const host = new URL(url).hostname.toLowerCase();
        if (host.startsWith('departamento.')) return 'departamento';
        if (host.startsWith('casa.')) return 'casa';
    } catch {
        // ignore
    }
    return 'inmueble';
}

export function normalizeState(raw = '') {
    const folded = raw.trim().toLowerCase();
    if (['cdmx', 'ciudad de mexico', 'ciudad de méxico', 'distrito federal', 'df'].includes(folded)) {
        return 'CDMX';
    }
    if (folded === 'morelos') return 'Morelos';
    return raw.trim();
}

export function splitLocation(location = '') {
    const parts = String(location)
        .split(',')
        .map((part) => part.trim())
        .filter(Boolean);
    if (parts.length >= 3) {
        return {
            neighborhood: parts[0],
            city: parts[1],
            state: normalizeState(parts[2]),
        };
    }
    if (parts.length === 2) {
        return {
            neighborhood: parts[0],
            city: parts[1],
            state: '',
        };
    }
    if (parts.length === 1) {
        return { neighborhood: parts[0], city: '', state: '' };
    }
    return { neighborhood: '', city: '', state: '' };
}

export function parsePrice(text = '') {
    const raw = String(text).replace(/\s+/g, ' ');
    const match = raw.match(/(?:MXN|\$)\s*([\d.,]+)/i) || raw.match(/([\d]{1,3}(?:[.,]\d{3})+)/);
    if (!match) return 0;
    const digits = match[1].replace(/[.,](?=\d{3}\b)/g, '').replace(',', '');
    const value = Number.parseInt(digits, 10);
    return Number.isFinite(value) ? value : 0;
}

export function parseIntAttr(text = '', pattern) {
    const match = String(text).match(pattern);
    if (!match) return null;
    const value = Number.parseInt(match[1], 10);
    return Number.isFinite(value) ? value : null;
}

export function pageUrl(startUrl, page) {
    const url = new URL(startUrl.endsWith('/') ? startUrl : `${startUrl}/`);
    const stripped = url.pathname.replace(/\/_Desde_\d+\/?$/, '').replace(/\/$/, '');
    if (page <= 1) {
        url.pathname = `${stripped}/`;
        return url.toString();
    }
    const offset = (page - 1) * ITEMS_PER_PAGE + 1;
    url.pathname = `${stripped}/_Desde_${offset}/`;
    return url.toString();
}

function cardRoot($, el) {
    const selectors = [
        'li.ui-search-layout__item',
        'div.ui-search-layout__item',
        'article',
        'div.poly-card',
        'div[class*="poly-card"]',
    ];
    for (const selector of selectors) {
        const node = $(el).closest(selector);
        if (node.length) return node.first();
    }
    return $(el).parent();
}

function thumbnailFromCard($, card) {
    const img = card.find('img[src*="mlstatic"]').first();
    const src = img.attr('src') || img.attr('data-src') || '';
    return src.trim();
}

export function parseSerpHtml(html, startUrl = '') {
    const anchorRows = parsePolycardAnchors(html, startUrl);
    if (anchorRows.length > 0) {
        return anchorRows;
    }
    return parseMlmLinksRegex(html, startUrl);
}

function parsePolycardAnchors(html, startUrl = '') {
    const $ = cheerio.load(html);
    const seen = new Set();
    const rows = [];

    $('a[href*="mercadolibre.com.mx/MLM-"]').each((_, el) => {
        const href = canonicalUrl($(el).attr('href') || '');
        if (!href || !href.includes('_JM')) return;
        if (href.includes('/click.') || href.includes('/registration')) return;

        const itemId = itemIdFromUrl(href);
        if (!itemId || seen.has(itemId)) return;

        const title = $(el).text().replace(/\s+/g, ' ').trim();
        if (!title || title.length < 8) return;

        seen.add(itemId);
        const card = cardRoot($, el);
        const cardText = card.text().replace(/\s+/g, ' ');

        const locationMatch = cardText.match(
            /([A-Za-zÁÉÍÓÚáéíóúñÑ][A-Za-zÁÉÍÓÚáéíóúñÑ0-9 .'-]*,\s*[A-Za-zÁÉÍÓÚáéíóúñÑ .'-]+,\s*(?:Distrito Federal|CDMX|Morelos|Ciudad de México))\b/,
        );
        const location = locationMatch ? locationMatch[1].trim() : '';
        const geo = splitLocation(location);

        const price = parsePrice(cardText);
        const bedrooms = parseIntAttr(cardText, /(\d+)\s*rec(?:ámaras?|\.?)?/i);
        const bathrooms = parseIntAttr(cardText, /(\d+)\s*ba[nñ]os?/i);
        const areaM2 = parseIntAttr(cardText, /(\d+)\s*m²/i);

        rows.push({
            item_id: itemId,
            url: href,
            title,
            description: '',
            location,
            neighborhood: geo.neighborhood,
            city: geo.city,
            state: geo.state,
            price,
            currency: 'MXN',
            bedrooms,
            bathrooms,
            area_m2: areaM2,
            property_type: propertyTypeFromUrl(href),
            listing_mode: listingMode(startUrl),
            lat: null,
            lng: null,
            thumbnail: thumbnailFromCard($, card) || null,
        });
    });

    return rows;
}

const MLM_LINK_RE = /https?:\/\/[a-z0-9.-]*mercadolibre\.com\.mx\/MLM-(\d{6,})[^"'\\s<]*/gi;

function parseMlmLinksRegex(html, startUrl = '') {
    const seen = new Set();
    const rows = [];
    const matches = String(html).matchAll(MLM_LINK_RE);
    for (const match of matches) {
        const href = canonicalUrl(match[0]);
        if (!href.includes('_JM') && !href.includes('-_JM')) continue;
        const itemId = match[1];
        if (!itemId || seen.has(itemId)) continue;
        seen.add(itemId);
        rows.push({
            item_id: itemId,
            url: href,
            title: '',
            description: '',
            location: '',
            neighborhood: '',
            city: '',
            state: '',
            price: 0,
            currency: 'MXN',
            bedrooms: null,
            bathrooms: null,
            area_m2: null,
            property_type: propertyTypeFromUrl(href),
            listing_mode: listingMode(startUrl),
            lat: null,
            lng: null,
            thumbnail: null,
        });
    }
    return rows;
}
