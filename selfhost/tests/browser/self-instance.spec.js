import {test, expect} from '@playwright/test';
import fs from 'node:fs';
import path from 'node:path';
import {setSwitch} from './switch.js';

const input = JSON.parse(fs.readFileSync(process.env.ASSOZETA_BROWSER_INPUT, 'utf8'));
const phase = process.env.ASSOZETA_BROWSER_PHASE;
const viewports = phase === 'start' ? [{name: 'desktop', width: 1440, height: 1000}] : [
    {name: 'desktop', width: 1440, height: 1000}, {name: 'mobile', width: 390, height: 844},
];

async function session(browser, viewport, token = input.token) {
    const context = await browser.newContext({viewport});
    await context.addInitScript(({token, refreshToken}) => {
        if (!localStorage.getItem('sessionToken')) {
            for (const [key, value] of Object.entries({sessionToken: token, refreshToken, role: 'association', currentPage: 'profile',
                subPage: 'self-instance', expires: Date.now() + 3600000, userData: {}})) {
                localStorage.setItem(key, JSON.stringify(value));
            }
        }
    }, {token, refreshToken: input.refreshTokens[token]});
    const page = await context.newPage();
    page.on('pageerror', error => console.error(error.stack));
    await page.goto(`${input.origin}/#/profile?page=self-instance`);
    return {context, page};
}

for (const viewport of viewports) {
    test(`${phase}: ${viewport.name}`, async ({browser}) => {
        const {context, page} = await session(browser, viewport);
        try {
            if (phase === 'operations') {
                await expect(page.getByRole('heading', {name: 'Panoramica', exact: true})).toBeVisible();
                await page.getByRole('button', {name: 'Email', exact: true}).click();
                await page.getByLabel('Server SMTP', {exact: true}).fill('127.0.0.1');
                await page.getByLabel('Porta', {exact: true}).fill('9');
                await page.getByLabel('Nome utente', {exact: true}).fill('');
                await page.getByLabel('Email mittente', {exact: true}).fill('fixture@example.test');
                await page.getByLabel('Nome mittente', {exact: true}).fill('Disposable fixture');
                await page.getByText('Password e opzioni avanzate', {exact: true}).click();
                await setSwitch(page, 'email-clear-password', 'Rimuovi la password salvata', true);
                await page.getByRole('button', {name: 'Salva email', exact: true}).click();
                await expect(page.getByRole('button', {name: 'Salva email', exact: true})).toBeDisabled();
                await page.reload();
                await page.getByRole('button', {name: 'Email', exact: true}).click();
                await expect(page.getByLabel('Server SMTP', {exact: true})).toHaveValue('127.0.0.1');
                await expect(page.getByLabel('Porta', {exact: true})).toHaveValue('9');
                if (viewport.name === 'desktop') {
                    await page.getByRole('button', {name: 'Verifica connessione SMTP', exact: true}).click();
                    await expect(page.locator('.diagnostic-result:visible')).toContainText('Non riuscito');
                    await page.getByRole('button', {name: 'Diagnostica', exact: true}).click();
                    await page.getByRole('button', {name: 'Esegui diagnostica', exact: true}).click();
                    await expect(page.getByRole('button', {name: 'Esegui diagnostica', exact: true})).toBeEnabled({timeout: 45000});
                    const checks = page.locator('.diagnostic-result:visible');
                    await expect(checks).toHaveCount(10);
                    await expect(checks.filter({has: page.getByText('Configurazione email', {exact: true})})).toContainText('Configurazione');
                    const response = await context.request.get(`${input.requestOrigin || input.origin}/api/instance/admin/diagnostics`, {headers: {Authorization: `Bearer ${input.token}`, Host: input.host}});
                    expect(response.ok()).toBeTruthy();
                    const result = await response.json();
                    fs.writeFileSync(path.join(process.env.ASSOZETA_BROWSER_OUTPUT, 'diagnostics.json'), JSON.stringify(result, null, 2));
                    await expect(checks.filter({has: page.getByText('Database', {exact: true})})).toContainText('Verificato');
                    expect(result.checks.every(check => check.checked_at)).toBeTruthy();
                    expect(result.checks.find(check => check.id === 'public_url').status).toBe('warning'); // Fixture uses HTTP.
                    for (const id of ['api', 'database', 'storage', 'worker', 'scheduler', 'renderer', 'updater']) {
                        expect(result.checks.find(check => check.id === id).status, `${id} health evidence`).toBe('passed');
                    }
                }
                await page.reload();
                await page.getByRole('button', {name: 'Diagnostica', exact: true}).click();
                await expect(page.locator('.diagnostic-result:visible').filter({has: page.getByText('Database', {exact: true})})).toContainText('Verificato');
                await page.getByRole('button', {name: 'Email', exact: true}).click();
                await page.getByText('Ripristina le impostazioni di ambiente', {exact: true}).click();
                await setSwitch(page, 'email-confirmReset', 'Confermo il ripristino della configurazione di ambiente', true);
                await page.getByRole('button', {name: 'Ripristina email di ambiente', exact: true}).click();
                await expect(page.getByText('Variabili di ambiente del server', {exact: true})).toBeVisible();
                await page.getByRole('button', {name: 'Integrazioni', exact: true}).click();
                for (const provider of ['Stripe', 'Google', 'Apple']) {
                    const card = page.locator('.integration-card').filter({has: page.getByRole('heading', {name: provider, exact: true})});
                    await setSwitch(card, `integration-${provider.toLowerCase()}-enabled`, provider === 'Apple' ? 'Abilita verifica token Apple' : `Abilita ${provider}`, false);
                    if (provider === 'Stripe') {
                        await card.getByLabel('Chiave pubblica Stripe', {exact: true}).fill('pk_test_disposable');
                        await card.getByLabel('Chiave segreta Stripe', {exact: true}).fill('sk_test_disposable');
                        await card.getByLabel('Firma webhook Stripe', {exact: true}).fill('whsec_disposable');
                    } else await card.getByLabel(`Client ID ${provider}`, {exact: true}).fill(provider === 'Google' ? 'disposable.apps.googleusercontent.com' : 'com.example.disposable');
                    await card.getByRole('button', {name: `Salva ${provider}`, exact: true}).click();
                    await expect(card.getByRole('button', {name: `Salva ${provider}`, exact: true})).toBeDisabled();
                }
                await page.reload();
                await page.getByRole('button', {name: 'Integrazioni', exact: true}).click();
                await expect(page.getByLabel('Chiave pubblica Stripe', {exact: true})).toHaveValue('pk_test_disposable');
                await expect(page.getByLabel('Chiave segreta Stripe', {exact: true})).toHaveValue('');
                await expect(page.getByLabel('Client ID Google', {exact: true})).toHaveValue('disposable.apps.googleusercontent.com');
                await expect(page.getByLabel('Client ID Apple', {exact: true})).toHaveValue('com.example.disposable');
                for (const provider of ['Stripe', 'Google', 'Apple']) {
                    const card = page.locator('.integration-card').filter({has: page.getByRole('heading', {name: provider, exact: true})});
                    await card.getByText(`Ripristina ${provider} da .env`, {exact: true}).click();
                    await setSwitch(card, `${provider.toLowerCase()}-confirm-reset`, `Confermo il ripristino di ${provider} da .env`, true);
                    await card.getByRole('button', {name: `Ripristina ${provider}`, exact: true}).click();
                    await expect(card.getByText('Variabili di ambiente del server (.env)', {exact: true})).toBeVisible();
                }
                expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1)).toBeTruthy();
            } else if (phase === 'branding') {
                await expect(page.getByRole('heading', {name: 'Self Instance', exact: true})).toBeVisible();
                await page.getByRole('button', {name: 'Identità e logo', exact: true}).click();
                const name = page.getByLabel('Nome', {exact: true});
                await name.fill(`Browser check ${viewport.name}`);
                await page.getByRole('button', {name: 'Salva impostazioni', exact: true}).click();
                await expect(page.getByRole('button', {name: 'Salva impostazioni', exact: true})).toBeDisabled();
                await page.reload();
                await page.getByRole('button', {name: 'Identità e logo', exact: true}).click();
                await expect(name).toHaveValue(`Browser check ${viewport.name}`);
                await name.fill('Preserved Club');
                await page.getByRole('button', {name: 'Salva impostazioni', exact: true}).click();
                await expect(page.getByRole('button', {name: 'Salva impostazioni', exact: true})).toBeDisabled();
                await page.getByLabel('Scegli un nuovo logo').setInputFiles(input.logo);
                const uploaded = page.waitForResponse(r => r.url().includes('/instance/admin/logo') && r.request().method() === 'POST');
                await page.getByRole('button', {name: 'Salva logo', exact: true}).click();
                expect((await uploaded).ok()).toBeTruthy();
                await expect(page.getByRole('button', {name: 'Salva logo', exact: true})).toBeDisabled();
                const logo = page.getByAltText('Anteprima del logo dell’applicazione');
                await expect.poll(() => logo.evaluate(img => img.complete && img.naturalWidth > 0)).toBeTruthy();
                expect(await logo.evaluate(img => getComputedStyle(img).objectFit)).toBe('contain');
                expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1)).toBeTruthy();
            } else if (phase === 'start') {
                await page.getByRole('button', {name: 'Aggiornamenti e backup', exact: true}).click();
                await page.getByRole('button', {name: 'Esamina aggiornamento a v2.0.2', exact: true}).click();
                await expect(page.getByRole('heading', {name: 'Aggiorna a v2.0.2', exact: true})).toBeVisible();
                expect(await page.locator('body').innerText()).toContain('Complete fixture release 2');
                const response = page.waitForResponse(r => r.url().endsWith('/instance/admin/updates') && r.request().method() === 'POST');
                await page.getByRole('button', {name: 'Conferma aggiornamento a v2.0.2', exact: true}).click();
                const accepted = await response;
                const submitted = accepted.request().postDataJSON();
                expect(submitted.request_id).toMatch(/^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/i);
                expect(submitted.tag).toBe('v2.0.2');
                expect(submitted.release_id).toBe(2);
                const body = await accepted.text();
                expect(accepted.ok(), `Update returned HTTP ${accepted.status()}: ${body.slice(0, 1000)}`).toBeTruthy();
                const result = JSON.parse(body);
                const operation = result.operation;
                expect(operation.request_id).toBe(submitted.request_id);
                fs.writeFileSync(input.operation, JSON.stringify(operation), {mode: 0o600});
            } else if (phase === 'during' || phase === 'recovery') {
                await expect(page.getByRole('region', {name: 'Stato aggiornamenti'})).toContainText(
                    phase === 'during' ? 'Backup dei dati' : 'Ripristino necessario');
                await page.reload();
                await expect(page.getByRole('region', {name: 'Stato aggiornamenti'})).toContainText(
                    phase === 'during' ? 'Backup dei dati' : 'Ripristino necessario');
                expect(page.url()).not.toContain('/login');
            } else if (phase === 'readiness') {
                await expect(page.getByRole('heading', {name: 'Self Instance', exact: true})).toBeVisible();
                await expect(page.locator('.version-grid')).toContainText(input.target);
                await page.reload();
                await expect(page.getByRole('heading', {name: 'Self Instance', exact: true})).toBeVisible();
            } else if (phase === 'completed') {
                await expect(page.getByRole('heading', {name: 'Self Instance', exact: true})).toBeVisible();
                await page.getByRole('button', {name: 'Aggiornamenti e backup', exact: true}).click();
                await expect(page.getByRole('region', {name: 'Stato aggiornamenti'})).toContainText('Aggiornamento completato');
                await expect(page.locator('.version-grid')).toContainText('2.0.2');
                await page.reload();
                await page.getByRole('button', {name: 'Aggiornamenti e backup', exact: true}).click();
                await expect(page.getByRole('region', {name: 'Stato aggiornamenti'})).toContainText('Aggiornamento completato');
            } else { throw new Error(`Unknown browser phase: ${phase}`); }
        } finally {
            await page.screenshot({path: path.join(process.env.ASSOZETA_BROWSER_OUTPUT, `${phase}-${viewport.name}.png`), fullPage: true}).catch(() => {});
            await context.close();
        }
    });
}

if (phase === 'branding') {
    test('non-owners cannot restore the instance tab or use administration APIs', async ({browser}) => {
        for (const token of input.deniedTokens) {
            const {context, page} = await session(browser, {width: 1440, height: 1000}, token);
            try {
                const access = await context.request.get(`${input.requestOrigin || input.origin}/api/instance/access`, {headers: {Authorization: `Bearer ${token}`, Host: input.host}});
                expect((await access.json()).is_owner).toBe(false);
                const admin = await context.request.get(`${input.requestOrigin || input.origin}/api/instance/admin`, {headers: {Authorization: `Bearer ${token}`, Host: input.host}});
                expect(admin.status()).toBe(403);
                for (const path of ['email', 'diagnostics', 'integrations/stripe', 'integrations/google', 'integrations/apple']) {
                    const denied = await context.request.get(`${input.requestOrigin || input.origin}/api/instance/admin/${path}`, {headers: {Authorization: `Bearer ${token}`, Host: input.host}});
                    expect(denied.status()).toBe(403);
                }
                await expect(page.getByRole('alert').filter({hasText: 'Questa sezione è riservata al proprietario'})).toBeVisible();
                await expect(page.getByRole('heading', {name: 'Self Instance', exact: true})).toHaveCount(0);
                await expect(page.getByRole('button', {name: /Conferma aggiornamento/})).toHaveCount(0);
            } finally { await context.close(); }
        }
    });
}

if (phase === 'operations') {
    test('Google login refreshes its client ID when cached public configuration changes', async ({browser}) => {
        const context = await browser.newContext({viewport: {width: 1440, height: 1000}});
        try {
            const response = await context.request.get(`${input.requestOrigin || input.origin}/api/instance/config`, {headers: {Host: input.host}});
            expect(response.ok()).toBeTruthy();
            const baseline = await response.json();
            let clientId = 'first.apps.googleusercontent.com';
            let delayRefresh = false;
            const configured = () => ({...baseline, oauth: {...baseline.oauth, googleClientId: clientId, googleEnabled: true}});
            await context.route('**/api/instance/config', async route => {
                if (delayRefresh) await new Promise(resolve => setTimeout(resolve, 750));
                await route.fulfill({contentType: 'application/json', body: JSON.stringify(configured())});
            });
            // Exercise our real login component without contacting or signing in to Google.
            await context.route('https://accounts.google.com/**', route => route.fulfill({contentType: 'application/javascript', body: `
                window.__googleClients = [];
                window.google = {accounts: {id: {
                    initialize: value => window.__googleClients.push(value.client_id),
                    renderButton: element => {element.textContent = 'Google SDK fixture';}, prompt: () => {},
                }}};
            `}));
            const page = await context.newPage();
            await page.goto(`${input.origin}/#/login?page=login`);
            await expect.poll(() => page.evaluate(() => window.__googleClients || [])).toContain(clientId);
            const stale = configured();
            await page.evaluate(config => {
                localStorage.setItem('assozeta_instance_config', JSON.stringify(config));
                localStorage.setItem('assozeta_config_timestamp', String(Date.now()));
            }, stale);
            clientId = 'second.apps.googleusercontent.com';
            delayRefresh = true;
            await page.reload();
            await expect.poll(() => page.evaluate(() => window.__googleClients || [])).toContain(clientId);
            await expect(page.locator('#login-with-google')).toContainText('Google SDK fixture');
        } finally { await context.close(); }
    });
}
