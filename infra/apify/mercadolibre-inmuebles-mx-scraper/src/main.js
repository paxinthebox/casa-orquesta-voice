import { Actor, log } from 'apify';
import { PlaywrightCrawler } from 'crawlee';

import { pageUrl, parseSerpHtml } from './parse.js';

function resolveStartUrls(input = {}) {
    const out = [];
    const seen = new Set();
    const push = (raw) => {
        const url = String(raw || '').trim();
        if (!url.includes('mercadolibre.com.mx')) return;
        if (seen.has(url)) return;
        seen.add(url);
        out.push(url);
    };
    if (Array.isArray(input.startUrls)) {
        for (const item of input.startUrls) {
            if (typeof item === 'string') push(item);
            else if (item?.url) push(item.url);
        }
    }
    push(input.startUrl);
    return out;
}

await Actor.init();

const input = await Actor.getInput() || {};
const startUrls = resolveStartUrls(input);
const maxItemsPerUrl = Math.min(
    Math.max(Number(input.maxItemsPerUrl || input.maxItems) || 80, 1),
    500,
);
const maxPages = Math.min(Math.max(Number(input.maxPages) || 10, 1), 30);
const maxItemsTotal = Math.min(
    Math.max(
        Number(input.maxItems) || maxItemsPerUrl * Math.max(1, startUrls.length),
        1,
    ),
    5000,
);
const ignoreFailures = input.ignore_url_failures !== false;

const proxyConfiguration = input.proxyConfiguration
    ? await Actor.createProxyConfiguration(input.proxyConfiguration)
    : await Actor.createProxyConfiguration({
        useApifyProxy: true,
        apifyProxyGroups: ['RESIDENTIAL'],
        apifyProxyCountry: 'MX',
    });

if (!startUrls.length) {
    throw new Error('Provide startUrls or startUrl (inmuebles.mercadolibre.com.mx search page)');
}

const seen = new Set();
const collected = [];
const perUrlCounts = new Map();

const crawler = new PlaywrightCrawler({
    proxyConfiguration,
    maxConcurrency: 1,
    requestHandlerTimeoutSecs: 180,
    launchContext: {
        launchOptions: {
            args: ['--disable-blink-features=AutomationControlled'],
        },
    },
    async requestHandler({ page, request, pushData }) {
        const queryUrl = String(request.userData.queryUrl || request.url);
        const pageNum = Number(request.userData.pageNum) || 1;
        await page.setExtraHTTPHeaders({
            'Accept-Language': 'es-MX,es;q=0.9',
        });
        await page.goto(request.url, { waitUntil: 'domcontentloaded', timeout: 120_000 });
        await page.waitForSelector('a[href*="MLM-"]', { timeout: 45_000 }).catch(() => {});

        const html = await page.content();
        const lowered = html.toLowerCase();
        if (lowered.includes('para continuar, ingresa')) {
            const msg = `Mercado Libre login wall on page ${pageNum} — check MX residential proxy`;
            if (ignoreFailures) {
                log.warning(`${queryUrl}: ${msg}`);
                return;
            }
            throw new Error(msg);
        }

        const rows = parseSerpHtml(html, queryUrl).map((row) => ({
            ...row,
            query_url: queryUrl.split('?')[0].replace(/\/+$/, ''),
        }));
        const mlmInHtml = (html.match(/MLM-\d{6,}/g) || []).length;
        log.info(
            `URL ${queryUrl} page ${pageNum}: ${rows.length} listings (mlm tokens: ${mlmInHtml})`,
        );

        let added = 0;
        for (const row of rows) {
            if (collected.length >= maxItemsTotal) break;
            if ((perUrlCounts.get(queryUrl) || 0) >= maxItemsPerUrl) break;
            if (seen.has(row.item_id)) continue;
            seen.add(row.item_id);
            collected.push(row);
            perUrlCounts.set(queryUrl, (perUrlCounts.get(queryUrl) || 0) + 1);
            await pushData(row);
            added += 1;
        }

        if (collected.length >= maxItemsTotal) return;
        if ((perUrlCounts.get(queryUrl) || 0) >= maxItemsPerUrl) return;
        if (rows.length === 0) return;
        if (added === 0 && pageNum > 1) return;

        const nextPage = pageNum + 1;
        if (nextPage <= maxPages) {
            await crawler.addRequests([
                {
                    url: pageUrl(queryUrl, nextPage),
                    userData: { pageNum: nextPage, queryUrl },
                },
            ]);
        }
    },
});

await crawler.run(
    startUrls.map((url) => ({
        url: pageUrl(url, 1),
        userData: { pageNum: 1, queryUrl: url },
    })),
);

log.info(`Done — ${collected.length} listings from ${startUrls.length} start URL(s)`);
await Actor.exit();
