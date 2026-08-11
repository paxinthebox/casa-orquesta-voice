import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';
import test from 'node:test';

import { pageUrl, parseJsonLdListings } from './parse.js';

const __dirname = dirname(fileURLToPath(import.meta.url));
const fixturePath = join(
    __dirname,
    '../../../../data/fixtures/lamudi_prado_jsonld.json',
);

test('parseJsonLdListings extracts Prado Churubusco rows', () => {
    const payload = JSON.parse(readFileSync(fixturePath, 'utf8'));
    const rows = parseJsonLdListings(
        payload,
        'https://www.lamudi.com.mx/distrito-federal/coyoacan/prado-churubusco/for-sale/',
    );
    assert.equal(rows.length, 3);
    assert.match(rows[0].listing_id, /^41032-73-/);
    assert.equal(rows[0].city, 'Coyoacán');
    assert.equal(rows[0].state, 'CDMX');
    assert.equal(rows[0].neighborhood, 'Prado Churubusco');
    assert.equal(rows[0].price, 3290000);
});

test('pageUrl paginates with ?page=N', () => {
    const start = 'https://www.lamudi.com.mx/distrito-federal/coyoacan/prado-churubusco/for-sale/';
    assert.equal(
        pageUrl(start, 2),
        'https://www.lamudi.com.mx/distrito-federal/coyoacan/prado-churubusco/for-sale/?page=2',
    );
});
