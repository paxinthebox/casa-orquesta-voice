/**
 * Vivanuncios (Navent) SERP parsers.
 *
 * Primary path: window.__PRELOADED_STATE__ / PRELOADED_STATE JSON embedded in HTML.
 * Fallback: cheerio link scrape for /d-…/{postingId} cards.
 */

import * as cheerio from 'cheerio';

const POSTING_ID_RE = /\/(\d{6,})(?:\.html)?(?:\?|$|\/)/;
const DETAIL_HREF_RE = /\/d-[^"'?\s]+\/(\d{6,})/i;

export function postingIdFromUrl(url = '') {
    const match = String(url).match(POSTING_ID_RE);
    return match ? match[1] : '';
}

export function canonicalizeQueryUrl(url = '') {
    let cleaned = String(url || '').trim().split('?')[0].split('#')[0].replace(/\/+$/, '');
    cleaned = cleaned.replace(/p\d+$/i, 'p1');
    return cleaned.toLowerCase();
}

/**
 * Paginate Vivanuncios list URLs.
 * Location-id form: …/v1c1293l13521p1 → …p2
 * Bare slug: append /p{N} when no p-suffix present.
 */
export function pageUrl(startUrl, page) {
    const raw = String(startUrl || '').trim();
    if (!raw) return raw;
    const pageNum = Math.max(1, Number(page) || 1);

    if (/p\d+/i.test(raw)) {
        return raw.replace(/p\d+(\/?$)/i, `p${pageNum}$1`);
    }

    const base = raw.replace(/\/+$/, '');
    if (pageNum <= 1) return `${base}/`;
    return `${base}/p${pageNum}`;
}

export function listingMode(startUrl = '') {
    const lowered = String(startUrl).toLowerCase();
    if (lowered.includes('-en-renta') || lowered.includes('/renta')) return 'rent';
    return 'sale';
}

function absoluteUrl(url = '') {
    const cleaned = String(url || '').trim();
    if (!cleaned) return '';
    if (cleaned.startsWith('http')) return cleaned.split('?')[0];
    if (cleaned.startsWith('/')) return `https://www.vivanuncios.com.mx${cleaned.split('?')[0]}`;
    return cleaned;
}

function looksLikePosting(obj) {
    if (!obj || typeof obj !== 'object' || Array.isArray(obj)) return false;
    const id = String(obj.posting_id || obj.postingId || obj.id || '').trim();
    if (/^\d{6,}$/.test(id)) return true;
    const url = String(obj.url || obj.link || obj.detailUrl || '');
    return Boolean(postingIdFromUrl(url) || url.match(DETAIL_HREF_RE));
}

function collectPostings(node, out = [], seen = new Set()) {
    if (!node || out.length > 5000) return out;
    if (Array.isArray(node)) {
        for (const item of node) collectPostings(item, out, seen);
        return out;
    }
    if (typeof node !== 'object') return out;

    if (looksLikePosting(node)) {
        const id = String(
            node.posting_id
            || node.postingId
            || postingIdFromUrl(String(node.url || node.link || ''))
            || '',
        );
        if (id && !seen.has(id)) {
            seen.add(id);
            out.push(node);
        }
        return out;
    }

    for (const key of Object.keys(node)) {
        // Skip huge unrelated blobs.
        if (['filters', 'seo', 'breadcrumbs', 'footer', 'header'].includes(key)) continue;
        collectPostings(node[key], out, seen);
    }
    return out;
}

/**
 * Extract JSON assigned to window.__PRELOADED_STATE__ (or close variants).
 */
export function extractPreloadedState(html = '') {
    const text = String(html || '');
    const patterns = [
        /window\.__PRELOADED_STATE__\s*=\s*/,
        /window\.PRELOADED_STATE\s*=\s*/,
        /__PRELOADED_STATE__\s*=\s*/,
    ];
    for (const re of patterns) {
        const match = re.exec(text);
        if (!match) continue;
        const start = match.index + match[0].length;
        const json = sliceBalancedJson(text, start);
        if (!json) continue;
        try {
            return JSON.parse(json);
        } catch {
            // try next pattern
        }
    }
    return null;
}

function sliceBalancedJson(text, start) {
    let i = start;
    while (i < text.length && /\s/.test(text[i])) i += 1;
    if (text[i] !== '{' && text[i] !== '[') return null;
    const open = text[i];
    const close = open === '{' ? '}' : ']';
    let depth = 0;
    let inString = false;
    let escape = false;
    for (let j = i; j < text.length; j += 1) {
        const ch = text[j];
        if (inString) {
            if (escape) {
                escape = false;
            } else if (ch === '\\') {
                escape = true;
            } else if (ch === '"') {
                inString = false;
            }
            continue;
        }
        if (ch === '"') {
            inString = true;
            continue;
        }
        if (ch === open) depth += 1;
        else if (ch === close) {
            depth -= 1;
            if (depth === 0) return text.slice(i, j + 1);
        }
    }
    return null;
}

export function postingToRow(raw, queryUrl = '') {
    if (!raw || typeof raw !== 'object') return null;
    const url = absoluteUrl(String(raw.url || raw.link || raw.detailUrl || ''));
    const postingId = String(
        raw.posting_id || raw.postingId || postingIdFromUrl(url) || '',
    ).trim();
    if (!postingId) return null;

    const title = String(raw.title || raw.headline || raw.name || '').trim();
    let price = null;
    const priceOps = raw.price_operation_types || raw.priceOperationTypes;
    if (Array.isArray(priceOps)) {
        for (const block of priceOps) {
            const prices = block?.prices;
            if (Array.isArray(prices) && prices[0]?.amount != null) {
                price = Number(prices[0].amount);
                break;
            }
        }
    }
    if (price == null && raw.price != null) price = Number(raw.price);

    const loc = raw.posting_location || raw.postingLocation || {};
    const inner = loc.location && typeof loc.location === 'object' ? loc.location : {};
    const neighborhood = String(inner.name || raw.neighborhood || '').trim();
    let city = '';
    let state = '';
    if (inner.parent && typeof inner.parent === 'object') {
        city = String(inner.parent.name || '').trim();
        if (inner.parent.parent && typeof inner.parent.parent === 'object') {
            state = String(
                inner.parent.parent.name || inner.parent.parent.acronym || '',
            ).trim();
        }
    }

    const prop = raw.real_estate_type || raw.realEstateType || {};
    const propertyType = String(
        (typeof prop === 'object' ? prop.name : prop) || raw.property_type || '',
    ).trim();

    return {
        posting_id: postingId,
        url: url || `/d-aviso/${postingId}`,
        title,
        price_operation_types: priceOps || undefined,
        price: Number.isFinite(price) ? price : undefined,
        posting_location: loc.location ? loc : raw.posting_location,
        real_estate_type: prop.name ? prop : raw.real_estate_type,
        property_type: propertyType || undefined,
        description_normalized: raw.description_normalized || raw.description || undefined,
        visible_pictures: raw.visible_pictures || raw.visiblePictures,
        publisher_name: raw.publisher_name || raw.publisher || undefined,
        listing_mode: listingMode(queryUrl),
        query_url: canonicalizeQueryUrl(queryUrl) || queryUrl,
        neighborhood: neighborhood || undefined,
        city: city || undefined,
        state: state || undefined,
    };
}

export function parsePreloadedPostings(state, queryUrl = '') {
    if (!state) return [];
    const rawList = collectPostings(state);
    const rows = [];
    const seen = new Set();
    for (const raw of rawList) {
        const row = postingToRow(raw, queryUrl);
        if (!row || seen.has(row.posting_id)) continue;
        seen.add(row.posting_id);
        rows.push(row);
    }
    return rows;
}

export function parseHtmlFallback(html, queryUrl = '') {
    const $ = cheerio.load(String(html || ''));
    const rows = [];
    const seen = new Set();

    $('a[href*="/d-"]').each((_, el) => {
        const href = $(el).attr('href') || '';
        const idMatch = href.match(DETAIL_HREF_RE);
        if (!idMatch) return;
        const postingId = idMatch[1];
        if (seen.has(postingId)) return;
        seen.add(postingId);
        const title = $(el).attr('title') || $(el).text().replace(/\s+/g, ' ').trim();
        rows.push({
            posting_id: postingId,
            url: absoluteUrl(href),
            title: title.slice(0, 200),
            listing_mode: listingMode(queryUrl),
            query_url: canonicalizeQueryUrl(queryUrl) || queryUrl,
        });
    });

    return rows;
}

export function parseSerpHtml(html, queryUrl = '') {
    const text = String(html || '');
    if (
        text.includes('Just a moment')
        || text.includes('cf-browser-verification')
        || text.includes('Attention Required')
    ) {
        throw new Error('Cloudflare challenge page — check MX residential proxy');
    }

    const state = extractPreloadedState(text);
    const fromState = parsePreloadedPostings(state, queryUrl);
    if (fromState.length) return fromState;
    return parseHtmlFallback(text, queryUrl);
}

export function resolveStartUrls(input = {}) {
    const fromList = [];
    for (const key of ['startUrls', 'urls']) {
        const value = input[key];
        if (!Array.isArray(value)) continue;
        for (const item of value) {
            if (typeof item === 'string' && item.trim()) fromList.push(item.trim());
            else if (item && typeof item === 'object' && item.url) fromList.push(String(item.url).trim());
        }
    }
    const single = String(input.startUrl || '').trim();
    if (single) fromList.push(single);

    const seen = new Set();
    const out = [];
    for (const url of fromList) {
        if (!url.includes('vivanuncios.com')) continue;
        const key = canonicalizeQueryUrl(url) || url;
        if (seen.has(key)) continue;
        seen.add(key);
        out.push(url);
    }
    return out;
}
