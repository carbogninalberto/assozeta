// Unchanged frontend, real password login and real manual HTTP endpoints.
// External fonts/providers are blocked; no application response is substituted.
import assert from 'node:assert/strict';
import fs from 'node:fs/promises';
import path from 'node:path';
import {pathToFileURL} from 'node:url';

const input = JSON.parse(await fs.readFile(process.env.TESTS_FIXTURE, 'utf8'));
const {chromium, expect: baseExpect} = await import(pathToFileURL(input.playwright_module).href);
const expect = baseExpect.configure({timeout: 15000});
const output = process.env.TESTS_OUTPUT;
const expected = input.manual;
const selected = input.version === 'v1' ? 'django-v1' : 'go-v2';
const browser = await chromium.launch();
const context = await browser.newContext({viewport: {width: 1440, height: 1000}, reducedMotion: 'reduce'});
const responses = [], failures = [], pending = [], checks = [];
let phase = 'login', success = false;
const deadline = setTimeout(() => { failures.push({kind: 'deadline', phase}); void browser.close(); }, 150000);
const core = new Set(['instance/status', 'instance/config', 'instance/access', 'oauth2/login',
    'oauth2/refresh-token', 'oauth2/check/username', 'profile/info']);
const coexistence = new Map([['instance/manifest.json', 200], ['instance/logo.png', 200], ['association/export/active', 403]]);

async function record(response) {
    const url = new URL(response.url());
    if (url.origin !== input.origin || !['http:', 'https:'].includes(url.protocol)) return;
    const item = {path: url.pathname, method: response.request().method(), status: response.status(),
        backend: await response.headerValue('x-fixture-backend')};
    responses.push(item);
    if (url.pathname.startsWith('/api/manuale/')) {
        assert.equal(item.backend, selected);
        assert.equal(item.status, 200);
        assert.equal(item.method, 'GET');
        assert.match((await response.request().allHeaders()).authorization || '', /^Bearer .+/);
        if (url.pathname.includes('/assets/')) {
            item.content_type = await response.headerValue('content-type');
            assert.equal(item.content_type, 'image/png', 'Downloaded image content type');
            assert.match(await response.headerValue('cache-control'), /private/);
        } else {
            const payload = await response.json();
            assert(!JSON.stringify(payload).includes('private-source-canary'));
            assert(!JSON.stringify(payload).includes('Private maintainer canary'));
            const ids = payload.results.map(section => section.id);
            if (url.searchParams.get('query') === expected.absent_query) {
                assert.equal(payload.status, 'no_evidence');
                assert.deepEqual(ids, []);
            } else {
                assert.equal(payload.status, 'verified');
                assert.deepEqual([...ids].sort(), [...expected.public_ids].sort());
            }
        }
    } else if (url.pathname.startsWith('/api/')) {
        const route = url.pathname.slice('/api/'.length);
        if (core.has(route)) {
            assert.equal(item.backend, selected);
            assert.equal(item.status, 200);
        } else {
            assert(coexistence.has(route), 'Unexpected application API request: '+route);
            assert.equal(item.backend, 'django-coexistence');
            assert.equal(item.status, coexistence.get(route));
        }
    } else {
        assert.equal(item.backend, 'ui-static');
        assert(item.status >= 200 && item.status < 400);
    }
}

function observe(page) {
    page.setDefaultTimeout(30000);
    page.on('response', response => pending.push(record(response).catch(error => failures.push({
        kind: 'response', phase, path: new URL(response.url()).pathname, assertion: error.message.split('\n')[0]}))));
    page.on('pageerror', error => failures.push({kind: 'pageerror', phase, error_class: error.name}));
    page.on('requestfailed', request => {
        const url = new URL(request.url());
        if (url.origin === input.origin && request.failure()?.errorText !== 'net::ERR_ABORTED')
            failures.push({kind: 'requestfailed', phase, path: url.pathname});
    });
}

try {
    await context.route('**/*', route => {
        const url = new URL(route.request().url());
        return url.origin === input.origin ? route.continue() : route.abort('blockedbyclient');
    });
    const app = await context.newPage(); observe(app);
    await app.goto(input.origin+'/#/login');
    await expect(app.locator('input[name="username_login"]')).toBeVisible();
    assert.equal(await app.evaluate(() => localStorage.getItem('sessionToken')), null);
    await app.locator('input[name="username_login"]').fill(input.username);
    await app.locator('input[name="password_login"]').fill(input.password);
    const login = app.waitForResponse(response => new URL(response.url()).pathname === '/api/oauth2/login');
    await app.locator('#bkn_login_signin_submit').click();
    const authenticated = await login;
    assert.equal(authenticated.status(), 200);
    assert.equal((await authenticated.json()).user_data.user_id, input.user_id);
    await expect(app.locator('#bkn_aside')).toBeVisible();
    const notice = app.getByRole('button', {name: /Ok, grazie/});
    if (await notice.isVisible()) await notice.click();
    checks.push('real-password-login');

    phase = 'manual-navigation';
    const link = app.locator('#manuale_assozeta a');
    await expect(link).toHaveAttribute('target', '_blank');
    const [reader] = await Promise.all([app.waitForEvent('popup'), link.click()]);
    observe(reader);
    await expect(reader.getByRole('heading', {name: 'Come possiamo aiutarti?', exact: true})).toBeVisible();
    await expect(reader.locator('#bkn_header, #bkn_aside')).toHaveCount(0);
    const navigation = reader.getByRole('navigation', {name: 'Sezioni del manuale'});
    await expect(navigation.getByRole('link')).toHaveCount(1);
    await navigation.getByRole('link', {name: expected.page_title, exact: true}).click();
    const article = reader.getByRole('article', {name: expected.page_title, exact: true});
    await expect(article).toBeVisible();
    await expect(article.locator('.chapter-section')).toHaveCount(2);
    for (const prose of expected.prose) await expect(article).toContainText(prose);
    await expect(article.locator('.image-preview img')).toHaveCount(2);
    await expect.poll(() => article.locator('.image-preview img').evaluateAll(images =>
        images.map(image => [image.complete, image.naturalWidth, image.naturalHeight])))
        .toEqual([[true, expected.image_width, expected.image_height], [true, expected.image_width, expected.image_height]]);
    // Chromium may omit a streamed fetch body from Network.getResponseBody.
    // Read the exact Blob URLs consumed by the unchanged reader instead; this
    // does not issue another application HTTP request or replace image bytes.
    const imageDigests = await article.locator('.image-preview img').evaluateAll(async images =>
        Promise.all(images.map(async image => {
            if (!image.src.startsWith('blob:')) throw new Error('Reader did not use its authenticated image Blob');
            const bytes = await (await fetch(image.src)).arrayBuffer();
            return Array.from(new Uint8Array(await crypto.subtle.digest('SHA-256', bytes)))
                .map(byte => byte.toString(16).padStart(2, '0')).join('');
        })));
    assert.deepEqual(imageDigests, [expected.image_sha256, expected.image_sha256], 'Reader image Blob digests');
    await article.getByRole('button', {name: /^Ingrandisci:/}).first().click();
    await expect(article.getByRole('dialog')).toBeVisible();
    await reader.keyboard.press('Escape');
    await expect(article.getByRole('dialog')).not.toBeVisible();
    checks.push('public-sections-real-authenticated-images-and-zoom');

    phase = 'deep-link-reload';
    await article.getByRole('link', {name: 'Apri sezione: '+expected.titles[1], exact: true}).click();
    const deepLink = reader.url();
    assert.equal(new URL(deepLink.split('#')[1], input.origin).searchParams.get('section'), expected.public_ids[1]);
    await reader.reload();
    await expect(article).toBeVisible();
    await expect(reader).toHaveURL(deepLink);
    checks.push('deep-link-reload');

    phase = 'search';
    const search = reader.getByRole('combobox', {name: 'Cerca nel manuale'});
    await search.fill(expected.query);
    await reader.getByRole('button', {name: 'Cerca', exact: true}).click();
    await expect(reader.locator('.search-result')).toHaveCount(2);
    const hit = reader.locator('.search-result').filter({has: reader.getByRole('heading', {name: expected.titles[0], exact: true})});
    await hit.click();
    await expect(article).toBeVisible();
    await search.fill(expected.absent_query);
    await reader.getByRole('button', {name: 'Cerca', exact: true}).click();
    await expect(reader.getByRole('status')).toHaveText('Non ho trovato istruzioni verificate per questa domanda.');
    await reader.getByRole('button', {name: 'Tutte le guide'}).first().click();
    checks.push('ranked-search-and-no-evidence');

    phase = 'mobile';
    await reader.setViewportSize({width: 390, height: 844});
    await reader.getByRole('button', {name: 'Sfoglia il manuale'}).click();
    await expect(navigation).toBeVisible();
    await navigation.getByRole('link', {name: expected.page_title, exact: true}).click();
    await expect(article).toBeVisible();
    await expect(navigation).not.toBeVisible();
    assert(await reader.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth));
    await reader.screenshot({path: path.join(output, 'manual-mobile.png'), fullPage: true});
    await reader.setViewportSize({width: 1440, height: 1000});
    await reader.screenshot({path: path.join(output, 'manual-desktop.png'), fullPage: true});
    checks.push('mobile-navigation-and-layout');

    phase = 'denials';
    // The real login token is read only for direct denial checks, never retained.
    const token = await app.evaluate(() => JSON.parse(localStorage.getItem('sessionToken')));
    const privateImage = await context.request.get(input.origin+'/api/manuale/assets/'+expected.private_path,
        {headers: {Authorization: 'Bearer '+token}});
    assert.equal(privateImage.status(), 404);
    assert.equal(privateImage.headers()['x-fixture-backend'], selected);
    const anonymous = await context.request.get(input.origin+'/api/manuale/sections');
    assert([401, 403].includes(anonymous.status()));
    assert.equal(anonymous.headers()['x-fixture-backend'], selected);
    checks.push('anonymous-reader-and-private-image-denials');
    await Promise.all(pending);
    assert(responses.some(row => row.path === '/api/manuale/sections'));
    assert(responses.some(row => row.path.startsWith('/api/manuale/assets/')));
    assert.deepEqual(failures, []);
    success = true;
} catch (error) {
    failures.push({kind: 'workflow', phase, assertion: error.message.split('\n')[0]});
    throw error;
} finally {
    clearTimeout(deadline);
    await browser.close();
    await Promise.all(pending);
    success = success && failures.length === 0;
    await fs.writeFile(path.join(output, 'manual-browser.json'), JSON.stringify({success, phase, checks, responses, failures,
        scope: 'Synthetic installed manual through real selected HTTP backend and unchanged frontend; canonical API proxy only. WebSockets stay Django; no assistant or release-content proof.'}, null, 2)+'\n');
}
