// Isolated UI regression checks. API fixtures never contact a deployed installation.
import assert from 'node:assert/strict';
import fs from 'node:fs/promises';
import path from 'node:path';
import {fileURLToPath, pathToFileURL} from 'node:url';
import {chromium, expect} from '@playwright/test';
import {setSwitch} from './switch.js';
const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '../../..');
const ui = path.join(root, 'UI');
const {createServer} = await import(pathToFileURL(path.join(ui, 'node_modules/vite/dist/node/index.js')));
const {svelte} = await import(pathToFileURL(path.join(ui, 'node_modules/@sveltejs/vite-plugin-svelte/src/index.js')));
const output = process.env.ASSOZETA_BROWSER_OUTPUT || path.join(root, 'quality-reports/operations-ui');
await fs.mkdir(output, {recursive: true});
const server = await createServer({
    root: ui, configFile: false, logLevel: 'error', server: {host: '127.0.0.1', port: 5198, strictPort: true},
    resolve: {alias: {utils: `${ui}/src/utils`, store: `${ui}/src/store`}},
    plugins: [{name: 'operations-fixture', enforce: 'pre',
        resolveId(id) {
            if (id.endsWith('/ApiMiddleware.js')) return '\0fixture-api';
            if (id.endsWith('/instanceStore.js')) return '\0fixture-store';
            if (id === '/operations-entry.js') return '\0fixture-entry';
        },
        load(id) {
            if (id === '\0fixture-api') return `export const originalFetch = window.fetch.bind(window); export async function apiFetch(url, options) { const r = await fetch(url, options); return {error: !r.ok, response: await r.json()}; }`;
            if (id === '\0fixture-store') return `import {writable} from 'svelte/store'; export const oemConfig = writable({logo: '/oem/assozeta/brand/logo.svg'}); export const getApiHost = () => '/api'; export const saveRuntimeConfig = () => {}; export const clearInstanceCache = () => {}; export const uploadInstanceLogo = async () => {};`;
            if (id === '\0fixture-entry') return `import SelfInstance from '/src/routes/profile/sections/SelfInstance.svelte'; new SelfInstance({target: document.getElementById('app')});`;
        },
        configureServer(vite) {
            vite.middlewares.use(async (req, res, next) => {
                if (req.url !== '/') return next();
                const html = await vite.transformIndexHtml('/', '<!doctype html><html lang="it"><head><meta name="viewport" content="width=device-width,initial-scale=1"><link rel="stylesheet" href="/static/css/bootstrap.min.css"><link rel="stylesheet" href="/static/css/app-bundle.css"><link rel="stylesheet" href="/global.css"><link rel="stylesheet" href="/dark-mode.css"><link rel="stylesheet" href="/brand.css"></head><body><main id="app" style="max-width:1100px;margin:24px auto;padding:12px"></main><script type="module" src="/operations-entry.js"></script></body></html>');
                res.setHeader('Content-Type', 'text/html'); res.end(html);
            });
        },
    }, svelte({configFile: false})],
});
let browser;
const reports = [];
try {
    await server.listen();
    browser = await chromium.launch({headless: true});
    for (const viewport of [{width: 1440, height: 1000}, {width: 390, height: 844}]) {
        const context = await browser.newContext({viewport});
        const page = await context.newPage();
        const errors = [];
        page.on('pageerror', error => {errors.push(error.message); console.error(error.stack);});
        let email = {source: 'environment', revision: 0, host: '', port: 465, security: 'ssl', username: '', from_email: '', sender_name: '', password_configured: false, restart_required: false};
        let diagnostics = {overall: 'not_checked', checked_at: null, checks: [
            {id: 'database', label: 'Database', core: true, status: 'not_checked', message: 'Esegui la diagnostica.', level: 'connectivity'},
            {id: 'email', label: 'Configurazione email', core: false, status: 'not_checked', message: 'Esegui la diagnostica.', level: 'configuration'},
        ], integrations: [{id: 'stripe', label: 'Stripe', status: 'not_configured', message: 'Integrazione facoltativa non configurata.', level: 'configuration', core: false}]};
        const integrationEnvironment = {
            stripe: {source: 'environment', revision: 0, enabled: true, public_key: 'pk_test_environment', secret_key_configured: true, webhook_secret_configured: true},
            google: {source: 'environment', revision: 0, enabled: false, client_id: ''},
            apple: {source: 'environment', revision: 0, enabled: false, client_id: ''},
        };
        const integrationValues = structuredClone(integrationEnvironment);
        let failIntegrationSave = false;
        let failSave = false;
        let failRun = false;
        let sent = 0;
        let runnerHistory = Array.from({length: 20}, (_, index) => ({id: `fixture-operation-${index}`, source_version: '1.0.2', target_version: '1.0.3', stage: 'completed', actor_id: 'fixture-owner', created_at: '2026-09-18T12:00:00Z'}));
        const latestRelease = {id: 4, tag: 'v1.0.4', name: 'Miglioramenti dell’istanza', published_at: '2026-09-23T12:00:00Z', artifacts_ready: true, url: 'https://example.test/release',
            notes: '# Novità della versione\n\n' + 'Migliorata la gestione degli aggiornamenti e la consultazione delle versioni precedenti.\n\n'.repeat(30) + 'Fine delle note di rilascio.'};
        const GIB = 1024 ** 3;
        const backupArchive = 'assozeta-20261106T200459Z.tar.gz';
        const backupOverview = {available: true, retention: {automatic: 3},
            archives: [
                {name: backupArchive, size: 3 * GIB, created_at: '2026-11-06T20:04:59+00:00', version: '1.0.3', origin: 'manual', keep: false, last_upgrade: false, pending_upgrade: false, location: 'local'},
                {name: 'assozeta-20261101T020000Z.tar.gz', size: 3 * GIB, created_at: '2026-11-01T02:00:00+00:00', version: '1.0.2', origin: 'pre-upgrade', keep: false, last_upgrade: true, pending_upgrade: false, location: 'local'},
                {name: 'assozeta-20261001T020000Z.tar.gz', size: 2 * GIB, created_at: '2026-10-01T02:00:00+00:00', version: null, origin: 'unknown', keep: false, last_upgrade: false, pending_upgrade: false, location: 'local'},
            ],
            disk: {total: 40 * GIB, used: 33 * GIB, free: 7 * GIB, backups: 8 * GIB, database: GIB, objects: 3 * GIB},
            next_backup: {estimated: 4 * GIB, required: 9 * GIB, fits: false}};
        const backupPasswords = [];
        await page.route('**/api/instance/**', async route => {
            const request = route.request();
            const endpoint = new URL(request.url()).pathname.replace('/api/instance', '');
            const method = request.method();
            let value = {};
            let status = 200;
            if (endpoint === `/admin/backups/${backupArchive}/download`) {
                assert.equal(new URL(request.url()).searchParams.get('download_token'), 'fixture-token');
                await route.fulfill({status: 200, contentType: 'application/gzip', body: Buffer.from([0x1f, 0x8b, 0, 0]),
                    headers: {'Content-Disposition': `attachment; filename="${backupArchive}"`}});
                return;
            }
            if (endpoint === '/admin') value = {config: {oem: {name: 'Diagnostic Club', abbreviation: 'DC', primaryColor: '#234582', supportEmail: 'help@example.test'}}, running_version: '1.0.3', configured_version: '1.0.3', mode: 'production'};
            else if (endpoint === '/admin/releases') value = {history: [latestRelease], pending: [latestRelease], latest: latestRelease, relation: 'behind'};
            else if (endpoint === '/admin/updates') value = {available: true, can_update: true, active: null, history: runnerHistory};
            else if (endpoint === '/admin/backups' && method === 'GET') value = backupOverview;
            else if (endpoint === '/admin/backups/download-token' && method === 'POST') {
                const input = request.postDataJSON();
                backupPasswords.push(input.password);
                assert.equal(input.name, backupArchive);
                if (input.password === 'fixture-secret') value = {download_token: 'fixture-token', expires_in: 300};
                else {status = 400; value = {error: 'Password non corretta.'};}
            }
            else if (endpoint === '/admin/bakney-pairing' && method === 'GET') value = {state: 'disconnected', pairing_id: null, forwarding_enabled: false};
            else if (endpoint.startsWith('/admin/integrations/')) {
                const provider = endpoint.split('/').at(-1);
                if (method === 'PUT') {
                    if (failIntegrationSave) {status = 409; value = {error: 'Integrazione modificata in un’altra sessione.'};}
                    else {
                        const input = request.postDataJSON();
                        assert.equal(input.revision, integrationValues[provider].revision);
                        value = {...integrationValues[provider], ...input, source: 'instance', revision: input.revision + 1};
                        for (const key of ['secret_key', 'webhook_secret']) {
                            if (input[key]) value[key + '_configured'] = true;
                            if (input.clear_secrets?.includes(key)) value[key + '_configured'] = false;
                            delete value[key];
                        }
                        delete value.clear_secrets;
                        integrationValues[provider] = value;
                    }
                } else if (method === 'DELETE') {
                    value = {...integrationEnvironment[provider], revision: integrationValues[provider].revision + 1};
                    integrationValues[provider] = value;
                } else value = integrationValues[provider];
            }
            else if (endpoint === '/admin/email') {
                if (method === 'PUT') {
                    if (failSave) {status = 409; value = {error: 'Configurazione modificata in un’altra sessione.'};}
                    else {
                        const input = request.postDataJSON();
                        assert.equal(input.revision, email.revision);
                        email = {...email, ...input, revision: email.revision + 1, source: 'instance', password_configured: !!input.password || email.password_configured};
                        delete email.password;
                        value = email;
                    }
                } else value = email;
            } else if (endpoint === '/admin/email/test') {
                const input = request.postDataJSON();
                if (input.action === 'send') {assert.equal(input.recipient, 'chosen@example.test'); sent++;}
                value = {status: 'passed', level: input.action === 'send' ? 'functional' : 'connectivity', checked_at: new Date().toISOString(), message: input.action === 'send' ? 'Il server SMTP ha accettato il messaggio. Verifica la casella.' : 'Connessione SMTP riuscita. Nessun messaggio inviato.'};
                email.last_test = value;
            } else if (endpoint === '/admin/diagnostics') {
                if (method === 'POST') {
                    if (failRun) {status = 503; value = {error: 'Servizio temporaneamente non disponibile. Riprova.'};}
                    else diagnostics = {...diagnostics, overall: 'failed', checked_at: new Date().toISOString(), checks: diagnostics.checks.map(c => ({...c, checked_at: new Date().toISOString(), status: c.core ? 'failed' : 'not_configured', message: c.core ? 'Database non disponibile. Controlla il servizio.' : 'Configura il server SMTP.'}))};
                }
                if (status === 200) value = diagnostics;
            } else throw new Error(`Unexpected request ${method} ${endpoint}`);
            await route.fulfill({status, contentType: 'application/json', body: JSON.stringify(value)});
        });
        const navigate = label => page.getByRole('group', {name: 'Sezioni Self Instance'}).getByRole('button', {name: label, exact: true}).click();
        await page.goto('http://127.0.0.1:5198/');
        await expect(page.getByRole('heading', {name: 'Panoramica', exact: true})).toBeVisible();
        await expect(page.getByText('Salute non ancora verificata')).toBeVisible();
        await expect(page.getByRole('heading', {name: 'Cronologia aggiornamenti', exact: true})).toHaveCount(0);
        const emailNavigation = page.getByRole('group', {name: 'Sezioni Self Instance'}).getByRole('button', {name: 'Email', exact: true});
        await emailNavigation.focus();
        await page.keyboard.press('Enter');
        await expect(emailNavigation).toHaveAttribute('aria-pressed', 'true');
        await expect(page.getByRole('heading', {name: 'Email di sistema', exact: true})).toBeVisible();
        await expect(page.getByRole('button', {name: 'Salva email', exact: true})).toBeDisabled();
        await page.getByText('Password e opzioni avanzate', {exact: true}).click();
        await setSwitch(page, 'email-clear-password', 'Rimuovi la password salvata', true);
        await setSwitch(page, 'email-clear-password', 'Rimuovi la password salvata', false);
        await page.getByLabel('Server SMTP', {exact: true}).fill('smtp.example.test');
        await page.getByLabel('Email mittente', {exact: true}).fill('sender@example.test');
        await page.getByLabel('Nome mittente', {exact: true}).fill('Club');
        await expect(page.getByRole('button', {name: 'Verifica connessione SMTP'})).toBeDisabled();
        await navigate('Panoramica');
        await navigate('Email •');
        await expect(page.getByLabel('Server SMTP', {exact: true})).toHaveValue('smtp.example.test');
        failSave = true;
        await page.getByRole('button', {name: 'Salva email', exact: true}).click();
        await expect(page.getByRole('alert')).toContainText('Configurazione modificata');
        await expect(page.getByRole('button', {name: 'Salva email', exact: true})).toBeEnabled();
        failSave = false;
        await page.getByRole('button', {name: 'Salva email', exact: true}).click();
        await expect(page.getByRole('button', {name: 'Salva email', exact: true})).toBeDisabled();
        await page.reload();
        await navigate('Email');
        await expect(page.getByLabel('Server SMTP', {exact: true})).toHaveValue('smtp.example.test');
        await page.getByText('Ripristina le impostazioni di ambiente', {exact: true}).click();
        await setSwitch(page, 'email-confirmReset', 'Confermo il ripristino della configurazione di ambiente', true);
        await setSwitch(page, 'email-confirmReset', 'Confermo il ripristino della configurazione di ambiente', false);
        await page.getByRole('button', {name: 'Verifica connessione SMTP'}).click();
        await expect(page.getByText('Connessione SMTP riuscita. Nessun messaggio inviato.')).toBeVisible();
        assert.equal(sent, 0);
        await page.getByLabel('Destinatario di prova').fill('chosen@example.test');
        await page.getByRole('button', {name: 'Invia email di prova', exact: true}).click();
        await expect(page.getByText('Il server SMTP ha accettato il messaggio. Verifica la casella.')).toBeVisible();
        assert.equal(sent, 1);
        await page.screenshot({path: path.join(output, `email-${viewport.width}.png`), fullPage: true});
        await navigate('Diagnostica');
        await expect(page.locator('.diagnostic-result:visible .verified')).toHaveCount(0);
        failRun = true;
        await page.getByRole('button', {name: 'Esegui diagnostica', exact: true}).click();
        await expect(page.getByRole('alert')).toContainText('Servizio temporaneamente non disponibile');
        failRun = false;
        await page.getByRole('button', {name: 'Esegui diagnostica', exact: true}).click();
        await expect(page.getByText('Database non disponibile. Controlla il servizio.')).toBeVisible();
        await expect(page.locator('.diagnostic-result:visible').filter({hasText: 'Configurazione email'})).toContainText('Non configurato');
        assert.equal(sent, 1, 'Routine diagnostics must never send mail');
        await page.screenshot({path: path.join(output, `diagnostics-${viewport.width}.png`), fullPage: true});
        await navigate('Panoramica');
        await expect(page.getByText('Servizi da ripristinare')).toBeVisible();
        await navigate('Integrazioni');
        await expect(page.getByText('Integrazione facoltativa non configurata.')).toBeVisible();
        await expect(page.getByLabel('Chiave pubblica Stripe', {exact: true})).toHaveValue('pk_test_environment');
        await expect(page.getByLabel('Chiave segreta Stripe', {exact: true})).toHaveValue('');
        await expect(page.getByLabel('Firma webhook Stripe', {exact: true})).toHaveValue('');
        await expect(page.getByRole('button', {name: 'Salva Stripe', exact: true})).toBeDisabled();
        for (const provider of ['stripe', 'google', 'apple']) {
            const title = provider[0].toUpperCase() + provider.slice(1);
            const label = provider === 'apple' ? 'Abilita verifica token Apple' : `Abilita ${title}`;
            const checked = await page.getByRole('switch', {name: label, exact: true}).isChecked();
            await setSwitch(page, `integration-${provider}-enabled`, label, !checked);
            await setSwitch(page, `integration-${provider}-enabled`, label, checked);
        }
        await page.getByLabel('Chiave pubblica Stripe', {exact: true}).fill('pk_test_changed');
        await page.getByLabel('Chiave segreta Stripe', {exact: true}).fill('sk_test_changed');
        await navigate('Panoramica'); await navigate('Integrazioni •');
        await expect(page.getByLabel('Chiave segreta Stripe', {exact: true})).toHaveValue('sk_test_changed');
        failIntegrationSave = true;
        await page.getByRole('button', {name: 'Salva Stripe', exact: true}).click();
        await expect(page.getByRole('alert')).toContainText('Integrazione modificata');
        await expect(page.getByLabel('Chiave segreta Stripe', {exact: true})).toHaveValue('sk_test_changed');
        failIntegrationSave = false;
        await page.getByRole('button', {name: 'Salva Stripe', exact: true}).click();
        await expect(page.getByRole('button', {name: 'Salva Stripe', exact: true})).toBeDisabled();
        await expect(page.getByLabel('Chiave segreta Stripe', {exact: true})).toHaveValue('');
        for (const title of ['Google', 'Apple']) {
            await page.getByLabel(`Client ID ${title}`, {exact: true}).fill(title === 'Google' ? 'saved.apps.googleusercontent.com' : 'com.example.saved');
            await page.getByRole('button', {name: `Salva ${title}`, exact: true}).click();
            await expect(page.getByRole('button', {name: `Salva ${title}`, exact: true})).toBeDisabled();
        }
        await page.reload();await navigate('Integrazioni');
        await expect(page.getByLabel('Chiave pubblica Stripe', {exact: true})).toHaveValue('pk_test_changed');
        await expect(page.getByLabel('Client ID Google', {exact: true})).toHaveValue('saved.apps.googleusercontent.com');
        await expect(page.getByLabel('Client ID Apple', {exact: true})).toHaveValue('com.example.saved');
        await expect(page.getByText(/Il pulsante Apple nella pagina di accesso web non è disponibile/)).toBeVisible();
        await page.screenshot({path: path.join(output, `integrations-${viewport.width}.png`), fullPage: true});
        await page.getByText('Ripristina Stripe da .env', {exact: true}).click();
        await expect(page.getByRole('button', {name: 'Ripristina Stripe', exact: true})).toBeDisabled();
        await setSwitch(page, 'stripe-confirm-reset', 'Confermo il ripristino di Stripe da .env', true);
        await page.getByRole('button', {name: 'Ripristina Stripe', exact: true}).click();
        await expect(page.getByLabel('Chiave pubblica Stripe', {exact: true})).toHaveValue('pk_test_environment');
        await navigate('Backup e ripristino');
        await expect(page.getByRole('heading', {name: 'Backup e ripristino', exact: true})).toBeVisible();
        await expect(page.getByRole('img', {name: /Disco del server: 33 GB usati su 40 GB, 7 GB liberi/})).toBeVisible();
        await expect(page.getByRole('alert').filter({hasText: 'Il prossimo backup potrebbe non entrare nel disco'})).toBeVisible();
        await expect(page.getByText('La rotazione conserva gli ultimi 3 backup automatici.', {exact: false})).toBeVisible();
        const backupRows = page.locator('.backups-table tbody tr');
        await expect(backupRows).toHaveCount(3);
        await expect(backupRows.nth(0)).toContainText('Manuale');
        await expect(backupRows.nth(1)).toContainText('Prima di un aggiornamento');
        await expect(backupRows.nth(1)).toContainText('Ultimo aggiornamento riuscito');
        await expect(backupRows.nth(2)).toContainText('Non registrata');
        await backupRows.nth(0).getByRole('button', {name: /^Scarica il backup del/}).click();
        await expect(page.getByLabel('Conferma la tua password')).toBeFocused();
        await expect(page.getByRole('note')).toContainText('non è cifrato');
        await page.screenshot({path: path.join(output, `backups-${viewport.width}.png`), fullPage: true});
        await page.getByLabel('Conferma la tua password').fill('wrong');
        await page.getByRole('button', {name: 'Scarica backup', exact: true}).click();
        await expect(page.getByText('Password non corretta.', {exact: true})).toBeVisible();
        await expect(page.getByLabel('Conferma la tua password')).toHaveValue('');
        await page.getByLabel('Conferma la tua password').fill('fixture-secret');
        const backupDownload = page.waitForEvent('download');
        await page.getByRole('button', {name: 'Scarica backup', exact: true}).click();
        assert.equal((await backupDownload).suggestedFilename(), backupArchive);
        await expect(page.getByLabel('Conferma la tua password')).toHaveCount(0);
        assert.deepEqual(backupPasswords, ['wrong', 'fixture-secret']);
        backupPasswords.length = 0;
        await navigate('Aggiornamenti');
        await expect(page.getByRole('heading', {name: 'Cronologia aggiornamenti', exact: true})).toBeVisible();
        const updates = page.locator('.updates-sections');
        await expect(updates.getByRole('heading').first()).toHaveText('Versione e aggiornamenti');
        const panels = updates.locator('details.instance-accordion');
        await expect(panels).toHaveCount(3);
        for (const panel of await panels.all()) await expect(panel).toHaveJSProperty('open', false);
        await page.screenshot({path: path.join(output, `updates-collapsed-${viewport.width}.png`), fullPage: true});
        const operationHistory = panels.filter({has: page.getByRole('heading', {name: 'Cronologia aggiornamenti', exact: true})});
        await operationHistory.locator(':scope > summary').focus();
        await page.keyboard.press('Enter');
        await expect(operationHistory).toHaveJSProperty('open', true);
        const historyContent = operationHistory.getByRole('region');
        assert(await historyContent.evaluate(element => element.scrollHeight > element.clientHeight), 'Long update history must scroll');
        assert(await historyContent.evaluate(element => element.getBoundingClientRect().height <= innerHeight * .65 + 1), 'History height must respect the viewport');
        await historyContent.focus();
        await page.keyboard.press('End');
        await expect.poll(() => historyContent.evaluate(element => element.scrollTop)).toBeGreaterThan(0);
        await operationHistory.locator(':scope > summary').click();
        await expect(operationHistory).toHaveJSProperty('open', false);
        const notesPanel = panels.filter({has: page.getByRole('heading', {name: 'Note di rilascio', exact: true})});
        await notesPanel.locator(':scope > summary').click();
        await notesPanel.locator('.release-notes > summary').click();
        const notesContent = notesPanel.getByRole('region');
        assert(await notesContent.evaluate(element => element.scrollHeight > element.clientHeight), 'Long release notes must scroll');
        await notesContent.scrollIntoViewIfNeeded();
        await notesContent.evaluate(element => element.scrollTop = element.scrollHeight);
        await expect(notesPanel.getByText('Fine delle note di rilascio.', {exact: true})).toBeInViewport();
        await page.screenshot({path: path.join(output, `updates-expanded-${viewport.width}.png`), fullPage: true});
        await page.getByRole('button', {name: 'Esamina aggiornamento a v1.0.4', exact: true}).click();
        await expect(page.getByRole('heading', {name: 'Aggiorna a v1.0.4', exact: true})).toBeVisible();
        assert(await page.locator('.review-notes').evaluate(element => element.scrollHeight > element.clientHeight));
        await expect(page.getByRole('button', {name: 'Conferma aggiornamento a v1.0.4', exact: true})).toBeEnabled();
        await page.locator('.review-panel').getByRole('button', {name: 'Annulla', exact: true}).click();
        await navigate('Identità e logo');
        await expect(page.getByRole('heading', {name: 'Identità dell’istanza', exact: true})).toBeVisible();
        for (const label of ['Panoramica', 'Email', 'Diagnostica', 'Aggiornamenti', 'Backup e ripristino', 'Integrazioni', 'Identità e logo']) {
            await navigate(label);
            assert(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1), `${label}: horizontal overflow`);
        }
        runnerHistory = [{...runnerHistory[0], stage: 'recovery_required', recovery: 'Ripristina il backup verificato.'}];
        await page.reload();
        await expect(page.getByRole('heading', {name: 'Ultimo aggiornamento', exact: true})).toBeVisible();
        await expect(page.getByText('Ripristina il backup verificato.', {exact: true})).toBeVisible();
        await navigate('Aggiornamenti');
        await expect(page.getByText('Ripristina il backup verificato.', {exact: true}).filter({visible: true})).toBeVisible();
        assert.deepEqual(errors, []);
        reports.push({viewport, passed: true, scope: 'Real Svelte components, fixture API; keyboard navigation, saves/reloads, failed saves, SMTP actions, failed diagnostics, integration environment values, masked secrets, saves/reloads, failed saves, explicit resets, update history/recovery visibility, overflow.'});
        await context.close();
    }
    await fs.writeFile(path.join(output, 'report.json'), JSON.stringify(reports, null, 2));
    console.log(JSON.stringify(reports, null, 2));
} finally {
    await browser?.close();
    await server.close();
}
