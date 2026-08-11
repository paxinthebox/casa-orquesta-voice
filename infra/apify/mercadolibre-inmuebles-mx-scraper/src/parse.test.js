import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';
import test from 'node:test';

import {
    itemIdFromUrl,
    pageUrl,
    parsePrice,
    parseSerpHtml,
    splitLocation,
} from './parse.js';

const __dirname = dirname(fileURLToPath(import.meta.url));
const fixturePath = join(
    __dirname,
    '../../../../data/fixtures/mercadolibre_coyoacan_serp.html',
);

test('itemIdFromUrl extracts MLM numeric id', () => {
    const url = 'https://departamento.mercadolibre.com.mx/MLM-2276252287-habitacion-_JM';
    assert.equal(itemIdFromUrl(url), '2276252287');
});

test('parseSerpHtml extracts Coyoacán fixture rows', () => {
    const html = readFileSync(fixturePath, 'utf8');
    const startUrl = 'https://inmuebles.mercadolibre.com.mx/departamentos/venta/distrito-federal/coyoacan/';
    const rows = parseSerpHtml(html, startUrl);
    assert.equal(rows.length, 3);
    assert.equal(rows[0].item_id, '2276252287');
    assert.equal(rows[0].price, 18500);
    assert.equal(rows[0].city, 'Coyoacán');
    assert.equal(rows[0].state, 'CDMX');
    assert.equal(rows[0].bedrooms, 2);
    assert.equal(rows[0].bathrooms, 1);
    assert.equal(rows[0].area_m2, 65);
    assert.equal(rows[2].property_type, 'casa');
});

test('pageUrl paginates with _Desde_N suffix', () => {
    const start = 'https://inmuebles.mercadolibre.com.mx/departamentos/venta/distrito-federal/coyoacan/';
    assert.equal(
        pageUrl(start, 2),
        'https://inmuebles.mercadolibre.com.mx/departamentos/venta/distrito-federal/coyoacan/_Desde_49/',
    );
});

test('parsePrice handles MXN comma amounts', () => {
    assert.equal(parsePrice('MXN3,450,000'), 3450000);
    assert.equal(parsePrice('MXN18,500'), 18500);
});

test('splitLocation maps Distrito Federal to CDMX', () => {
    const geo = splitLocation('Santa Cruz Atoyac, Coyoacán, Distrito Federal');
    assert.equal(geo.neighborhood, 'Santa Cruz Atoyac');
    assert.equal(geo.city, 'Coyoacán');
    assert.equal(geo.state, 'CDMX');
});
