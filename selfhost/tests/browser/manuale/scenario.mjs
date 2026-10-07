import {initializeActorSession} from './actor-session.mjs';
// Common real-backend fixture setup; scenario code supplies actions and assertions.
import {chromium, expect as browserExpect} from '@playwright/test';
import manualConfig from '../playwright.manual.config.mjs';
import fs from 'node:fs';
import path from 'node:path';
import crypto from 'node:crypto';
import {redactReport} from './redaction.mjs';
import {captureFrame} from './frame.mjs';
import {reconcileBrowserDenials} from './expected-denials.mjs';

export const expect = browserExpect.configure({timeout: 15000});
export async function scenario({id, prefix, sources, actions}) {
    const run = process.env.ASSOZETA_MANUAL_RUN;
    if (!run) throw new Error('ASSOZETA_MANUAL_RUN is required');
    const state = JSON.parse(fs.readFileSync(path.join(run, 'run.json')));
    const input = JSON.parse(fs.readFileSync(path.join(run, 'browser-input.json')));
    const sha = file => crypto.createHash('sha256').update(fs.readFileSync(file)).digest('hex');
    const report = {id, status: 'running', application_revision: state.application_input.revision,
        capture_id: state.capture_id, tooling_hashes: state.tooling_hashes,
        source_hashes: Object.fromEntries(sources.map(relative => [relative, sha(path.join(state.application, relative))])),
        fixture_version: input.fixture_version, reference_date: input.reference_date,
        fixture_profile: input.fixture_profile || 'baseline',
        viewport: manualConfig.use.viewport, locale: manualConfig.use.locale, timezone: manualConfig.use.timezoneId,
        theme: manualConfig.use.colorScheme, device_scale_factor: manualConfig.use.deviceScaleFactor,
        backend: 'real', capture_format: 'full-hd-v1', screenshots: [], checks: [], expected_denials: []};
    let browser;
    const actorPages = [];
    const errors = [];
    const failedResponses = [];
    async function actor(name) {
        const identity = name ? input.identities[name] : input;
        const context = await browser.newContext(manualConfig.use);
        const headers = {Authorization: `Bearer ${identity.token}`};
        const send = (relative, options) => context.request.fetch(input.origin + '/api/' + relative,
            {...options, headers: {...headers, ...options.headers}});
        const api = async (relative, options = {}) => {
            try {
                return await send(relative, options);
            } catch (error) {
                // The preview server closes idle keep-alive sockets after 5 s; a reused
                // socket closed at that instant resets. Retry only idempotent reads once.
                if (!['GET', 'HEAD'].includes((options.method || 'GET').toUpperCase())
                    || !/ECONNRESET|socket hang up|EPIPE/.test(String(error?.message))) throw error;
                report.transport_retries = (report.transport_retries || 0) + 1;
                return send(relative, options);
            }
        };
        const health = await api('healthz');
        expect((await health.json()).manual_capture).toEqual({run_id: state.run_id,
            application_revision: state.application_input.revision, reference_time: input.reference_date + 'T12:00:00+00:00'});
        const profile = await api('profile/info');
        expect(profile.ok()).toBeTruthy();
        const profileData = await profile.json();
        expect(profileData.user_data.user_id).toBe(identity.user_id);
        const billing = await api('billing/active-plan');
        expect(billing.ok()).toBeTruthy();
        await context.addInitScript(initializeActorSession, {identity, input});
        const page = await context.newPage();
        actorPages.push({page, name: name || 'owner'});
        page.setDefaultTimeout(45000);
        page.on('pageerror', error => errors.push(error.message));
        page.on('response', response => {
            if (response.status() >= 400 && response.url().startsWith(input.origin)) {
                const failure = {path: new URL(response.url()).pathname, status: response.status(),
                    method: response.request().method(), identity: name || 'owner'};
                // Existing export-progress polling is mounted for collaborators;
                // the export endpoint requires the association owner. Preserve that
                // exact expected denial while failing all other browser errors.
                if (name === 'reader' && failure.path === '/api/association/export/active' && failure.status === 403)
                    report.expected_denials.push({...failure, identity: 'reader'});
                else failedResponses.push(failure);
            }
        });
        await page.goto(input.origin + '/#/');
        await expect(page.getByText('Organizzazione', {exact: true})).toBeVisible({timeout: 60000});
        const notice = page.getByRole('button', {name: /Ok, grazie/});
        if (await notice.isVisible()) await notice.click();
        async function open(parent, href) {
            if (page.url() === input.origin + href) return;
            const link = page.locator(`a[href="${href}"]`).first();
            if (!await link.isVisible()) await page.getByText(parent, {exact: true}).first().click();
            await expect(link).toBeVisible();
            await link.click();
        }
        return {page, api, open, context};
    }
    async function capture(page, number, checkpoint, locator, {mask = [], redactionReason = '', blurEditableFocus = false} = {}) {
        report.screenshots.push(await captureFrame({page, run, relative: `${prefix}/${number}.png`,
            checkpoint, locator, mask, redactionReason, blurEditableFocus}));
    }
    try {
        browser = await chromium.launch();
        const owner = await actor();
        await actions({...owner, actor, input, capture, report});
        expect(errors).toEqual([]);
        const denials = reconcileBrowserDenials(failedResponses, report.expected_denials);
        expect(denials.unexpected).toEqual([]);
        expect(denials.unobserved).toEqual([]);
        report.status = 'passed';
    } catch (error) {
        report.status = 'failed'; report.error = error.message; report.error_stack = error.stack; report.browser_errors = errors;
        report.failed_responses = failedResponses; process.exitCode = 1;
        report.diagnostics = [];
        for (const {page, name} of actorPages) {
            if (page.isClosed()) continue;
            const relative = `diagnostics/${id}/${name}.png`;
            const file = path.join(run, relative);
            fs.mkdirSync(path.dirname(file), {recursive: true});
            try {
                await page.screenshot({path: file, fullPage: false, animations: 'disabled',
                    mask: [page.locator('input, textarea, [data-manual-private]')]});
                report.diagnostics.push({path: relative, sha256: sha(file), identity: name, private_fields_masked: true});
            } catch {
                report.diagnostics.push({identity: name, status: 'screenshot-unavailable'});
            }
        }
    } finally {
        if (browser) await browser.close();
        const secrets = [input, ...Object.values(input.identities || {})]
            .flatMap(identity => [identity.token, identity.refresh_token, identity.login_password]);
        fs.writeFileSync(path.join(run, id + '.json'), JSON.stringify(redactReport(report, secrets), null, 2) + '\n');
    }
    console.log(`${id}: ${report.status}`);
}
