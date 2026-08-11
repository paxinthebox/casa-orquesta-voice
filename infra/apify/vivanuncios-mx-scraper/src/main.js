import { Actor, log } from 'apify';
import { PlaywrightCrawler } from 'crawlee';

import {
    canonicalizeQueryUrl,
    pageUrl,
    parseSerpHtml,
    resolveStartUrls,
} from './parse.js';

await Actor.init();

const input = await Actor.getInput() || {};
const startUrls = resolveStartUrls(input);
const maxItemsPerUrl = Math.min(
    Math.max(
        Number(input.maxItemsPerUrl || input.max_items_per_url || input.maxItems) || 80,
        1,
    ),
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
    throw new Error('Provide startUrls, urls, or startUrl (vivanuncios.com.mx list page)');
}

const seen = new Set();
const collected = [];
const perUrlCounts = new Map();

const crawler = new PlaywrightCrawler({
    proxyConfiguration,
    maxConcurrency: 1,
    requestHandlerTimeoutSecs: 180,
    navigationTimeoutSecs: 120,
    launchContext: {
        launchOptions: {
            args: ['--disable-blink-features=AutomationControlled'],
        },
    },
    async requestHandler({ page, request, pushData }) {
        const queryUrl = String(request.userData.queryUrl || request.url);
        const pageNum = Number(request.userData.pageNum) || 1;
        const urlKey = canonicalizeQueryUrl(queryUrl) || queryUrl;

        await page.setExtraHTTPHeaders({
            'Accept-Language': 'es-MX,es;q=0.9',
        });
        await page.goto(request.url, { waitUntil: 'domcontentloaded', timeout: 120_000 });
        // Wait for Navent state or listing cards after Cloudflare JS challenge.
        await page.waitForFunction(
            () => Boolean(
                window.__PRELOADED_STATE__
                || window.PRELOADED_STATE
                || document.querySelector('a[href*="/d-"]'),
            ),
            { timeout: 60_000 },
        ).catch(() => {});

        const html = await page.content();
        let rows;
        try {
            rows = parseSerpHtml(html, queryUrl);
        } catch (err) {
            if (ignoreFailures) {
                log.warning(`SERP failed for ${queryUrl} page ${pageNum}: ${err.message}`);
                return;
            }
            throw err;
        }

        log.info(`URL ${urlKey} page ${pageNum}: ${rows.length} listings`);

        let added = 0;
        for (const row of rows) {
            if (collected.length >= maxItemsTotal) break;
            if ((perUrlCounts.get(urlKey) || 0) >= maxItemsPerUrl) break;
            if (seen.has(row.posting_id)) continue;
            seen.add(row.posting_id);
            row.query_url = canonicalizeQueryUrl(queryUrl) || queryUrl;
            collected.push(row);
            perUrlCounts.set(urlKey, (perUrlCounts.get(urlKey) || 0) + 1);
            await pushData(row);
            added += 1;
        }

        if (collected.length >= maxItemsTotal) return;
        if ((perUrlCounts.get(urlKey) || 0) >= maxItemsPerUrl) return;
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
    failedRequestHandler({ request }, error) {
        const queryUrl = request.userData?.queryUrl || request.url;
        if (ignoreFailures) {
            log.warning(`Request failed for ${queryUrl}: ${error.message}`);
            return;
        }
        throw error;
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
