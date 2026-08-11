import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';
import test from 'node:test';

import {
    canonicalizeQueryUrl,
    pageUrl,
    parseSerpHtml,
    resolveStartUrls,
} from './parse.js';

const __dirname = dirname(fileURLToPath(import.meta.url));
const fixturePath = join(
    __dirname,
    '../../../../data/fixtures/vivanuncios_prado_serp.html',
);

const QUERY =
    'https://www.vivanuncios.com.mx/s-casas-en-venta/prado-churubusco/v1c1293l13521p1';

test('parseSerpHtml extracts PRELOADED_STATE postings', () => {
    const html = readFileSync(fixturePath, 'utf8');
    const rows = parseSerpHtml(html, QUERY);
    assert.equal(rows.length, 2);
    assert.equal(rows[0].posting_id, '141933259');
    assert.equal(rows[0].title.includes('Prado'), true);
    assert.equal(rows[0].query_url, canonicalizeQueryUrl(QUERY));
    assert.equal(rows[0].listing_mode, 'sale');
    assert.equal(rows[1].posting_id, '148173680');
});

test('pageUrl paginates …pN suffix', () => {
    assert.equal(
        pageUrl(QUERY, 2),
        'https://www.vivanuncios.com.mx/s-casas-en-venta/prado-churubusco/v1c1293l13521p2',
    );
    assert.equal(pageUrl(QUERY, 1), QUERY);
});

test('pageUrl appends pN for bare slugs', () => {
    const bare = 'https://www.vivanuncios.com.mx/s-casas-en-venta/prado-churubusco/';
    assert.equal(
        pageUrl(bare, 2),
        'https://www.vivanuncios.com.mx/s-casas-en-venta/prado-churubusco/p2',
    );
});

test('resolveStartUrls merges startUrls, urls, startUrl', () => {
    const urls = resolveStartUrls({
        startUrls: [QUERY],
        urls: ['https://www.vivanuncios.com.mx/s-departamentos-en-venta/roma-norte/v1c1294l13669p1'],
        startUrl: QUERY,
    });
    assert.equal(urls.length, 2);
});

test('canonicalizeQueryUrl normalizes page to p1', () => {
    assert.equal(
        canonicalizeQueryUrl(
            'https://www.vivanuncios.com.mx/s-casas-en-venta/prado-churubusco/v1c1293l13521p3',
        ),
        canonicalizeQueryUrl(QUERY),
    );
});
