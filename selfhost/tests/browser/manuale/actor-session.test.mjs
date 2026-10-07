import test from 'node:test';
import assert from 'node:assert/strict';
import http from 'node:http';
import {chromium} from '@playwright/test';
import {initializeActorSession} from './actor-session.mjs';

test('actor setup preserves hydrated/refreshed identity on reload and respects logout', async () => {
    const server = http.createServer((_request, response) => response.end('<!doctype html><title>Session setup regression</title>'));
    await new Promise(resolve => server.listen(0, '127.0.0.1', resolve));
    const browser = await chromium.launch();
    try {
        const context = await browser.newContext();
        const input = {reference_date: '2026-09-30'};
        await context.addInitScript(initializeActorSession, {identity: {token: 'initial-fixture-token', refresh_token: 'initial-refresh'}, input});
        const page = await context.newPage();
        const pageErrors = [];
        page.on('pageerror', error => pageErrors.push(error.message));
        await page.goto('data:text/html,<!doctype html><title>Opaque preview</title>');
        await page.goto(`http://127.0.0.1:${server.address().port}/`);
        assert.equal(await page.evaluate(() => JSON.parse(localStorage.getItem('role'))), null);
        const hydrated = {sessionToken: 'refreshed-session', refreshToken: 'refreshed-refresh', role: 'association',
            userData: {user_id: 'loaded-owner', first_name: 'Elena'}, billingData: {active_plan: {billing_type: 1}},
            permissions: ['association.members.read'], currentPage: 'members'};
        await page.evaluate(values => {for (const [key, value] of Object.entries(values)) localStorage.setItem(key, JSON.stringify(value));}, hydrated);
        await page.reload();
        const retained = await page.evaluate(keys => Object.fromEntries(keys.map(key => [key, JSON.parse(localStorage.getItem(key))])), Object.keys(hydrated));
        assert.deepEqual(retained, hydrated);
        assert.equal(await page.evaluate(() => Date.now()), Date.parse('2026-09-30T12:00:00Z'));
        await page.evaluate(() => localStorage.clear());
        await page.reload();
        assert.equal(await page.evaluate(() => localStorage.getItem('sessionToken')), null);
        assert.equal(await page.evaluate(() => localStorage.getItem('role')), null);
        assert.deepEqual(pageErrors, []);
    } finally {
        await browser.close();
        await new Promise(resolve => server.close(resolve));
    }
});
