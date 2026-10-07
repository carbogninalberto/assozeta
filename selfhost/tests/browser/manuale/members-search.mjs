import {initializeActorSession} from './actor-session.mjs';
// Real application + real backend. No page.route or simulated successful API.
import {chromium, expect} from '@playwright/test';
import manualConfig from '../playwright.manual.config.mjs';
import fs from 'node:fs';
import path from 'node:path';
import crypto from 'node:crypto';
import {captureFrame} from './frame.mjs';
import {focusRegion, textFocus} from './focus.mjs';

const run = process.env.ASSOZETA_MANUAL_RUN;
if (!run) throw new Error('ASSOZETA_MANUAL_RUN is required');
const state = JSON.parse(fs.readFileSync(path.join(run, 'run.json')));
const input = JSON.parse(fs.readFileSync(path.join(run, 'browser-input.json')));
const code = state.application;
const sha = file => crypto.createHash('sha256').update(fs.readFileSync(file)).digest('hex');
const sourcePaths = [
    'UI/src/routes/association/Members/MembersList.svelte',
    'UI/src/components/tables/BKNDatatable.svelte',
    'UI/src/components/Sidebar.svelte',
    'UI/src/routes.js',
    'UI/src/utils/Permissions.js',
    'BE/application/views/subscriptions_views.py',
    'BE/application/utils/subscriptions_utils.py',
    'BE/application/serializers/subscriptions_serializers.py',
    'BE/application/models/user_models.py',
    'UI/src/store/stores.js',
    'BE/application/models/subscriptions_models.py',
    'BE/application/permissions_registry.py',
];
const report = {id: 'members-search', status: 'running', application_revision: state.application_input.revision,
    capture_id: state.capture_id, tooling_hashes: state.tooling_hashes || {},
    source_hashes: Object.fromEntries(sourcePaths.map(relative => [relative, sha(path.join(code, relative))])),
    fixture_version: input.fixture_version, reference_date: input.reference_date,
    viewport: manualConfig.use.viewport, locale: manualConfig.use.locale, timezone: manualConfig.use.timezoneId, theme: manualConfig.use.colorScheme,
    device_scale_factor: manualConfig.use.deviceScaleFactor, fixture_profile: input.fixture_profile || 'baseline',
    backend: 'real', capture_format: 'full-hd-v1', screenshots: [], checks: []};
const reportPath = path.join(run, 'members-search.json');
const browser = await chromium.launch({headless: true});
const context = await browser.newContext(manualConfig.use);
const page = await context.newPage();
page.setDefaultTimeout(45000);
const errors = [];
const failedResponses = [];
page.on('pageerror', error => errors.push(error.message));
page.on('response', response => {
    if (response.status() >= 400 && response.url().startsWith(input.origin)) {
        failedResponses.push({path: new URL(response.url()).pathname, status: response.status()});
    }
});

async function capture(number, checkpoint) {
    const relative = `images/faq/ricerca-filtri/${number}.png`;
    await expect(page.locator('[data-sonner-toast]:visible')).toHaveCount(0, {timeout: 10000});
    const rows = page.locator('[data-row]');
    const result = await rows.count()
        ? rows.last().locator('[data-field="associate"]')
        : page.getByText('Nessun dato disponibile', {exact: true});
    await expect(result).toBeVisible();
    const locator = focusRegion(page.locator('#bkn_datatable_search_query'),
        page.locator('.datatable-head [data-field="associate"]'),
        await rows.count() ? result : textFocus(result));
    report.screenshots.push(await captureFrame({page, run, relative, checkpoint, locator}));
}

try {
    const health = await context.request.get(input.origin + '/api/healthz');
    expect(health.ok(), 'Fixture health endpoint HTTP ' + health.status()).toBeTruthy();
    expect((await health.json()).manual_capture).toEqual({run_id: state.run_id,
        application_revision: state.application_input.revision, reference_time: input.reference_date + 'T12:00:00+00:00'});
    const headers = {Authorization: `Bearer ${input.token}`};
    const profile = await context.request.get(input.origin + '/api/profile/info', {headers});
    expect(profile.ok(), 'Authenticated profile HTTP ' + profile.status() + ': ' + (profile.ok() ? '' : await profile.text())).toBeTruthy();
    const profileData = await profile.json();
    expect(profileData.user_data.user_id).toBe(input.user_id);
    const billing = await context.request.get(input.origin + '/api/billing/active-plan', {headers});
    expect(billing.ok(), 'Billing endpoint HTTP ' + billing.status() + ': ' + (billing.ok() ? '' : await billing.text())).toBeTruthy();
    const billingData = (await billing.json()).data;
    expect(billingData).toBeTruthy();
    await context.addInitScript(initializeActorSession, {identity: input, input, captureMode: true});
    await page.goto(input.origin + '/#/');
    console.log('Authenticated application opened; waiting for navigation');
    // Use the application's navigation after its real billing response has
    // initialized permissions, exactly as a user entering the section would.
    const membersLink = page.locator('a[href="/#/members/list"]').first();
    await expect(page.getByText('Organizzazione', {exact: true})).toBeVisible({timeout: 60000});
    if (!await membersLink.isVisible()) await page.getByText('Organizzazione', {exact: true}).click();
    await expect(membersLink).toBeVisible({timeout: 60000});
    const releaseNotice = page.getByRole('button', {name: /Ok, grazie/});
    if (await releaseNotice.isVisible()) await releaseNotice.click();
    await membersLink.click();
    const member = page.locator('[data-row]').filter({hasText: /Giulia/i}).first();
    await expect(member).toBeVisible({timeout: 90000});

    const rows = page.locator('[data-row]');
    await expect(rows).toHaveCount(3);
    for (const name of ['Giulia Bianchi', 'Luca Verdi', 'Sara Conti']) await expect(rows).toContainText([name]);
    await capture(1, 'all-fixture-members');
    const search = page.locator('#bkn_datatable_search_query');
    const filtered = page.waitForResponse(response => {
        const url = new URL(response.url());
        return url.pathname.endsWith('/subscription/list') && url.searchParams.get('query[generalSearch]') === 'Giulia';
    });
    await search.fill('Giulia');
    const response = await filtered;
    expect(response.ok()).toBeTruthy();
    const data = await response.json();
    expect(data.meta.total).toBe(1);
    await expect(rows).toHaveCount(1);
    await expect(rows.first()).toContainText('Giulia Bianchi');
    await expect(rows.first()).not.toContainText('Luca Verdi');
    await capture(2, 'name-search-result');
    await search.fill('PersonaInesistente');
    await expect(rows).toHaveCount(0);
    await capture(3, 'no-matching-member');
    await search.locator('..').getByRole('button').click();
    await expect(search).toHaveValue('');
    await expect(rows).toHaveCount(3);
    await capture(4, 'search-cleared');
    report.member_search = {initial_rows: 3, filtered_rows: data.meta.total, unknown_name_rows: 0,
        reset_rows: await rows.count(), search_cleared: await search.inputValue() === ''};
    report.checks = ['three deterministic fixture members visible', 'real filtered GET returns exactly one member',
        'name search excludes other members', 'unknown name returns no rows', 'clear button restores the full list'];
    expect(errors).toEqual([]);
    report.status = 'passed';
} catch (error) {
    report.status = 'failed';
    report.error = String(error.message).replaceAll(input.token, '[redacted]').replaceAll(input.refresh_token, '[redacted]');
    await page.screenshot({path: path.join(run, 'members-search-failure.png'), fullPage: false,
        mask: [page.locator('input, textarea, [data-manual-private]')]}).catch(() => {});
    process.exitCode = 1;
} finally {
    report.page_errors = errors.map(message => message.replaceAll(input.token, '[redacted]').replaceAll(input.refresh_token, '[redacted]'));
    report.failed_responses = failedResponses;
    fs.writeFileSync(reportPath, JSON.stringify(report, null, 2) + '\n');
    await browser.close();
}
console.log(`Manual scenario ${report.id}: ${report.status}. Report: ${reportPath}`);
