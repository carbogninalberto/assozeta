// Real scoped setup, password + TOTP login and disable. Secrets stay in memory.
import crypto from 'node:crypto';
import {scenario, expect} from './scenario.mjs';
import {focusRegion, textFocus} from './focus.mjs';
import manualConfig from '../playwright.manual.config.mjs';
import {redactReport} from './redaction.mjs';
import {communicationAuthAuthoredWorkflows} from '../../../../docs/manuale/communication-auth-authored-workflows.mjs';

const id = 'account-two-factor';
const spec = communicationAuthAuthoredWorkflows[id];

// RFC 6238, SHA-1 / 30 seconds / six digits, matching the actual pyotp handler.
// The dedicated capture backend freezes time at reference_date 12:00 UTC.
function totp(secret, milliseconds) {
    const alphabet = 'ABCDEFGHIJKLMNOPQRSTUVWXYZ234567';
    let bits = 0, value = 0;
    const bytes = [];
    for (const char of secret.toUpperCase().replace(/=+$/, '')) {
        const digit = alphabet.indexOf(char);
        if (digit < 0) throw new Error('Invalid private TOTP configuration');
        value = (value << 5) | digit;
        bits += 5;
        if (bits >= 8) { bits -= 8; bytes.push((value >>> bits) & 255); }
    }
    const counter = Buffer.alloc(8);
    counter.writeBigUInt64BE(BigInt(Math.floor(milliseconds / 30000)));
    const digest = crypto.createHmac('sha1', Buffer.from(bytes)).update(counter).digest();
    const offset = digest[digest.length - 1] & 15;
    return String((digest.readUInt32BE(offset) & 0x7fffffff) % 1000000).padStart(6, '0');
}

// Install before setup, including on every later navigation. Failure diagnostics
// in scenario.mjs also mask these entire containers, not only form inputs.
function markPrivateContainers() {
    function mark() {
        for (const image of document.querySelectorAll('#bkn_form_password_update img[src^="data:image/"]')) {
            const container = image.parentElement;
            if (container && !container.hasAttribute('data-manual-private'))
                container.setAttribute('data-manual-private', 'true');
        }
        for (const heading of document.querySelectorAll('#bkn_form_password_update h5')) {
            if (heading.textContent.trim() === 'Chiave Segreta:') {
                const container = heading.parentElement;
                if (container && !container.hasAttribute('data-manual-private'))
                    container.setAttribute('data-manual-private', 'true');
            }
        }
        for (const field of document.querySelectorAll('input[name="username_login"], input[name="password_login"], input[name="otp-code"], .swal2-input')) {
            if (!field.hasAttribute('data-manual-private')) field.setAttribute('data-manual-private', 'true');
        }
    }
    if (!window.__assozetaManualPrivateMaskInstalled) {
        new MutationObserver(mark).observe(document, {childList: true, subtree: true});
        window.__assozetaManualPrivateMaskInstalled = true;
    }
    mark();
}

await scenario({id, prefix: spec.prefix.replace(/\/$/, ''), sources: spec.sources,
    actions: async ({page, api, context, open, input, capture, report}) => {
        expect(input.fixture_version).toBe(8);
        expect(input.fixture_profile || 'baseline').toBe('baseline');
        expect(Boolean(input.login_username && input.login_password)).toBe(true);
        const privateValues = [input.login_username, input.login_password];
        const freshContexts = [];
        const browserFailures = [];
        const proof = {};
        const checkpoints = new Set();
        let cleanupAllowed = false;
        let finished = false;
        let secret;
        const read = async route => {
            const response = await api(route);
            expect(response.status()).toBe(200);
            return response.json();
        };
        const enabled = async () => (await read('two-fa/info')).data.enabled;
        const isUpdate = response => new URL(response.url()).pathname === '/api/two-fa/update'
            && response.request().method() === 'POST';
        const isLogin = response => new URL(response.url()).pathname === '/api/oauth2/login'
            && response.request().method() === 'POST';
        const take = async (checkpoint, target = page) => {
            const index = spec.checkpoints.findIndex(item => item.id === checkpoint);
            expect(index >= 0 && !checkpoints.has(checkpoint)).toBe(true);
            await target.evaluate(markPrivateContainers);
            await expect(target.locator('[data-sonner-toast]:visible')).toHaveCount(0, {timeout: 10000});
            let focus;
            if (checkpoint === 'twofa-fresh-login-authenticated') {
                await expect(target.getByRole('heading', {name: 'Bacheca', exact: true})).toBeVisible();
                await expect(target.locator('.dashboard-widget')).toHaveCount(8);
                await expect(target.locator('.dashboard-widget').getByText('...', {exact: true})).toHaveCount(0);
                await expect(target.locator('.dashboard-widget [aria-busy="true"], .dashboard-widget [style*="visibility:hidden"]')).toHaveCount(0);
                focus = focusRegion(target.getByRole('heading', {name: 'Bacheca', exact: true}),
                    target.locator('.dashboard-widget').filter({has: target.locator('.card-label').filter({hasText: /^Iscrizioni$/})}));
            } else if (checkpoint === 'twofa-fresh-login-credentials-masked') {
                focus = target.locator('#bkn_login_signin_form');
            } else if (checkpoint === 'twofa-fresh-login-otp-masked') {
                focus = target.locator('.swal2-popup');
            } else {
                const heading = target.getByRole('heading', {name: 'Autenticazione a due fattori', exact: true});
                const form = target.locator('#bkn_form_password_update');
                focus = ['twofa-setup-secret-and-qr-masked', 'twofa-code-before-save-masked'].includes(checkpoint)
                    ? focusRegion(heading, form)
                    : focusRegion(heading, textFocus(form.locator('label').filter({hasText: /^Abilita Doppio Fattore$/})), form.locator('.switch'));
            }
            await capture(target, index + 1, checkpoint, focus, {
                mask: [target.locator('[data-manual-private], input[name="otp-code"], input[name="username_login"], input[name="password_login"], input[type="password"], .swal2-input')],
                redactionReason: 'QR, chiave segreta, OTP, identificativo e password nascosti integralmente; solo account di prova.',
            });
            checkpoints.add(checkpoint);
        };
        const openTwoFactor = async () => {
            await open('Impostazioni', '/#/profile');
            await page.getByText('Autenticazione 2 fattori', {exact: true}).click();
            await expect(page).toHaveURL(input.origin + '/#/profile?page=twofa');
            await expect(page.getByRole('heading', {name: 'Autenticazione a due fattori', exact: true})).toBeVisible();
        };
        const freshLogin = async (requireOtp, withCaptures = false) => {
            // No token, localStorage seeding or authenticated actor helper here.
            const fresh = await context.browser().newContext(manualConfig.use);
            freshContexts.push(fresh);
            await fresh.addInitScript(reference => {
                localStorage.setItem('seenUpdatesToast', 'true');
                const NativeDate = Date;
                const fixed = new NativeDate(reference + 'T12:00:00Z').getTime();
                window.Date = class extends NativeDate {
                    constructor(...args) { super(...(args.length ? args : [fixed])); }
                    static now() { return fixed; }
                };
            }, input.reference_date);
            await fresh.addInitScript(markPrivateContainers);
            const loginPage = await fresh.newPage();
            loginPage.setDefaultTimeout(45000);
            let loginDenials = 0;
            loginPage.on('pageerror', () => browserFailures.push('Fresh-login page error'));
            loginPage.on('response', response => {
                if (!response.url().startsWith(input.origin) || response.status() < 400) return;
                const route = new URL(response.url()).pathname;
                if (route === '/api/oauth2/login' && response.status() === 401 && requireOtp) loginDenials++;
                else browserFailures.push(route + ':' + response.status());
            });
            await loginPage.goto(input.origin + '/#/login');
            expect(await loginPage.evaluate(() => JSON.parse(localStorage.getItem('sessionToken') || 'null') === null)).toBe(true);
            await loginPage.locator('input[name="username_login"]').fill(input.login_username);
            await loginPage.locator('input[name="password_login"]').fill(input.login_password);
            if (withCaptures) await take('twofa-fresh-login-credentials-masked', loginPage);
            let [response] = await Promise.all([
                loginPage.waitForResponse(isLogin),
                loginPage.locator('#bkn_login_signin_submit').click(),
            ]);
            if (requireOtp) {
                expect(response.status()).toBe(401);
                expect((await response.json()).msg).toBe('OTP code required.');
                expect(await loginPage.evaluate(() => JSON.parse(localStorage.getItem('sessionToken') || 'null') === null)).toBe(true);
                report.expected_denials.push({path: '/api/oauth2/login', status: 401,
                    identity: 'fresh-owned-account', reason: 'Actual handler required OTP before granting an authenticated session.'});
                await expect(loginPage.getByText('Autenticazione a Due Fattori', {exact: true})).toBeVisible();
                const code = totp(secret, Date.parse(input.reference_date + 'T12:00:00Z'));
                privateValues.push(code);
                await loginPage.locator('.swal2-input').fill(code);
                if (withCaptures) await take('twofa-fresh-login-otp-masked', loginPage);
                [response] = await Promise.all([
                    loginPage.waitForResponse(isLogin),
                    loginPage.locator('.swal2-confirm').click(),
                ]);
                proof.fresh_login_required_otp = loginDenials === 1;
            }
            expect(response.status()).toBe(200);
            const authenticated = await response.json();
            privateValues.push(authenticated.access_token, authenticated.refresh_token);
            expect(authenticated.user_data?.user_id === input.user_id).toBe(true);
            await expect(loginPage.getByText('Organizzazione', {exact: true})).toBeVisible({timeout: 60000});
            expect(await loginPage.evaluate(ownedId => {
                try { return JSON.parse(localStorage.getItem('userData'))?.user_id === ownedId; }
                catch { return false; }
            }, input.user_id)).toBe(true);
            const notice = loginPage.getByRole('button', {name: /Ok, grazie/});
            if (await notice.isVisible()) await notice.click();
            expect(loginDenials).toBe(requireOtp ? 1 : 0);
            if (withCaptures) await take('twofa-fresh-login-authenticated', loginPage);
            if (requireOtp) {
                proof.fresh_login_authenticated = response.status() === 200;
                proof.authenticated_identity_matches = authenticated.user_data.user_id === input.user_id;
            } else proof.fresh_login_without_otp_after_disable = response.status() === 200 && loginDenials === 0;
            await fresh.close();
        };
        try {
            expect((await read('profile/info')).user_data.user_id === input.user_id).toBe(true);
            expect(await enabled()).toBe(false);
            cleanupAllowed = true;
            proof.initial_disabled = true;
            await context.addInitScript(markPrivateContainers);
            await page.evaluate(markPrivateContainers);
            await openTwoFactor();
            const toggle = page.locator('#bkn_form_password_update input[type="checkbox"]');
            await expect(toggle).not.toBeChecked();
            await take('twofa-original-disabled');
            const [setupResponse] = await Promise.all([
                page.waitForResponse(response => new URL(response.url()).pathname === '/api/two-fa/setup'),
                page.locator('#bkn_form_password_update .switch label').click(),
            ]);
            await expect(toggle).toBeChecked();
            expect(setupResponse.status()).toBe(200);
            const setup = (await setupResponse.json()).data;
            secret = setup.otp_secret;
            privateValues.push(secret, setup.qrcode_uri);
            expect(typeof secret === 'string' && /^[A-Z2-7]+$/.test(secret)).toBe(true);
            expect(typeof setup.qrcode_uri === 'string' && setup.qrcode_uri.startsWith('data:image/')).toBe(true);
            proof.setup_generated_via_ui = true;
            await expect(page.locator('#bkn_form_password_update img')).toBeVisible();
            await expect(page.getByText('Chiave Segreta:', {exact: true})).toBeVisible();
            await page.getByText('Chiave Segreta:', {exact: true}).scrollIntoViewIfNeeded();
            await take('twofa-setup-secret-and-qr-masked');
            expect(await page.locator('#bkn_form_password_update img').evaluate(image => image.parentElement.hasAttribute('data-manual-private'))).toBe(true);
            expect(await page.getByText('Chiave Segreta:', {exact: true}).evaluate(heading => heading.parentElement.hasAttribute('data-manual-private'))).toBe(true);
            proof.qr_and_secret_masked = true;
            const code = totp(secret, Date.parse(input.reference_date + 'T12:00:00Z'));
            privateValues.push(code);
            await page.locator('input[name="otp-code"]').fill(code);
            await take('twofa-code-before-save-masked');
            const [updateResponse] = await Promise.all([
                page.waitForResponse(isUpdate),
                page.locator('#bkn_form_password_update_submit').click(),
            ]);
            expect(updateResponse.status()).toBe(200);
            await expect(page.getByText('Autenticazione a due fattori aggiornata.', {exact: true})).toBeVisible();
            await expect.poll(enabled).toBe(true);
            await page.reload();
            await expect(toggle).toBeChecked();
            proof.enabled_persisted = await enabled();
            await take('twofa-enabled-after-reload');
            await freshLogin(true, true);
            await page.locator('#bkn_form_password_update .switch label').click();
            await expect(toggle).not.toBeChecked();
            const [disabledResponse] = await Promise.all([
                page.waitForResponse(isUpdate),
                page.locator('#bkn_form_password_update_submit').click(),
            ]);
            expect(disabledResponse.status()).toBe(200);
            await expect(page.getByText('Autenticazione a due fattori aggiornata.', {exact: true})).toBeVisible();
            await expect.poll(enabled).toBe(false);
            await page.reload();
            await expect(toggle).not.toBeChecked();
            proof.disabled_via_ui_persisted = await enabled() === false;
            await take('twofa-disabled-after-reload');
            await freshLogin(false);
            expect(checkpoints.size).toBe(spec.checkpoints.length);
            expect(browserFailures).toEqual([]);
            finished = true;
        } catch (error) {
            const sanitized = new Error(redactReport(error.message, privateValues));
            sanitized.stack = redactReport(error.stack || error.message, privateValues);
            throw sanitized;
        } finally {
            try {
                if (cleanupAllowed) {
                    // The token identifies the same owned fixture account; no
                    // collaborator, foreign account or reset route is touched.
                    expect((await read('profile/info')).user_data.user_id === input.user_id).toBe(true);
                    const response = await api('two-fa/update', {method: 'POST',
                        data: {enable: false, otp: '', secret: ''}});
                    expect(response.status()).toBe(200);
                    proof.scoped_cleanup_disabled = await enabled() === false;
                    expect(proof.scoped_cleanup_disabled).toBe(true);
                }
            } catch (error) {
                const sanitized = new Error(redactReport(error.message, privateValues));
            sanitized.stack = redactReport(error.stack || error.message, privateValues);
            throw sanitized;
            } finally {
                for (const fresh of freshContexts) await fresh.close().catch(() => {});
            }
        }
        if (finished) {
            expect(proof).toEqual(spec.outcome.expected);
            report[spec.outcome.field] = proof;
            report.checks.push('Owned account setup and QR generated through UI; six-digit TOTP validated by actual handler; fresh password login required OTP; UI disable persisted and next fresh login needed no OTP; scoped cleanup.');
            report.gaps = [
                {intent: 'authentication.two-factor.phone-app', status: 'needs_external_verification',
                    reason: 'No phone app UI, scan, manual secret import, backup or transfer was exercised. Local TOTP used the actual generated fixture secret and frozen backend time.'},
                {intent: 'authentication.two-factor.recovery', status: 'needs_external_verification',
                    reason: 'Recovery after losing access to the authenticator was not exercised.'},
                {intent: 'authentication.two-factor.other-login-methods', status: 'pending',
                    reason: 'Only password login of the owned account was exercised; social login and other identities remain unverified.'},
            ];
        }
    }});
