// Isolated UI regression checks. API fixtures never contact a deployed installation.
import assert from 'node:assert/strict';
import fs from 'node:fs/promises';
import os from 'node:os';
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
// Scan every profile component up front: a dependency discovered mid-test makes
// Vite reload the page. A private cache and port keep concurrent runs apart.
const port = Number(process.env.ASSOZETA_OPERATIONS_PORT || 5198);
const cacheDir = await fs.mkdtemp(path.join(os.tmpdir(), 'assozeta-operations-vite-'));
const server = await createServer({
    root: ui, configFile: false, logLevel: process.env.ASSOZETA_VITE_LOG || 'error', server: {host: '127.0.0.1', port, strictPort: true, hmr: false, watch: null},
    cacheDir, optimizeDeps: {entries: ['src/routes/profile/**/*.svelte'],
        // Reached through imports the scan cannot follow (app aliases are not configured here).
        include: ['lucide', 'moment', 'sweetalert2', 'fetch-intercept', 'svelte-spa-router', 'svelte-spa-router/wrap', 'svelte-portal', 'lucide-svelte', '@capacitor/core']},
    resolve: {alias: {utils: `${ui}/src/utils`, store: `${ui}/src/store`}},
    plugins: [{name: 'operations-fixture', enforce: 'pre',
        resolveId(id) {
            if (id.endsWith('/ApiMiddleware.js')) return '\0fixture-api';
            if (id.endsWith('/instanceStore.js')) return '\0fixture-store';
            if (id === '/operations-entry.js') return '\0fixture-entry';
        },
        load(id) {
            if (id === '\0fixture-api') return `export const originalFetch = window.fetch.bind(window); export async function apiFetch(url, options) { const r = await fetch(url, options); return {error: !r.ok, response: await r.json()}; }`;
            if (id === '\0fixture-store') return `import {writable} from 'svelte/store'; export const oemConfig = writable({logo: '/oem/assozeta/brand/logo.svg'}); export const getApiHost = () => '/api'; export const saveRuntimeConfig = () => {}; export const clearInstanceCache = () => {}; export const uploadInstanceLogo = async () => {}; export const applyApiVersion = async () => 'v1';`;
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
        const context = await browser.newContext({viewport, acceptDownloads: true});
        const page = await context.newPage();
        const errors = [];
        page.on('pageerror', error => {errors.push(error.message); console.error(error.stack);});
        let email = {source: 'environment', revision: 0, host: '', port: 465, security: 'ssl', username: '', from_email: '', sender_name: '', password_configured: false, restart_required: false};
        let diagnostics = {overall: 'not_checked', checked_at: null, checks: [
            {id: 'database', label: 'Database', core: true, status: 'not_checked', message: 'Esegui la diagnostica.', level: 'connectivity', section: 'diagnostics'},
            {id: 'email', label: 'Configurazione email', core: false, status: 'not_checked', message: 'Esegui la diagnostica.', level: 'configuration', section: 'email'},
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
        let runnerActive = null;
        let runnerHistory = Array.from({length: 20}, (_, index) => ({id: `fixture-operation-${index}`, source_version: '1.0.2', target_version: '1.0.3', stage: 'completed', status: 'succeeded', actor_id: 'fixture-owner', created_at: '2026-09-18T12:00:00Z'}));
        const latestRelease = {id: 4, tag: 'v1.0.4', name: 'Miglioramenti dell’istanza', published_at: '2026-09-23T12:00:00Z', artifacts_ready: true, url: 'https://example.test/release',
            notes: '# Novità della versione\n\n' + 'Migliorata la gestione degli aggiornamenti e la consultazione delle versioni precedenti.\n\n'.repeat(30) + 'Fine delle note di rilascio.'};
        const GIB = 1024 ** 3;
        const backupArchive = 'assozeta-20261106T200459Z.tar.gz';
        const hoursAgo = hours => new Date(Date.now() - hours * 3600000).toISOString();
        let backupArchives = [
            {name: backupArchive, size: 3 * GIB, created_at: hoursAgo(24 * 9), version: '1.0.3', origin: 'manual', keep: false, last_upgrade: false, pending_upgrade: false, location: 'local'},
            {name: 'assozeta-20261101T020000Z.tar.gz', size: 3 * GIB, created_at: hoursAgo(24 * 14), version: '1.0.2', origin: 'pre-upgrade', keep: false, last_upgrade: true, pending_upgrade: false, location: 'local'},
            {name: 'assozeta-20261001T020000Z.tar.gz', size: 2 * GIB, created_at: hoursAgo(24 * 40), version: null, origin: 'unknown', keep: false, last_upgrade: false, pending_upgrade: false, location: 'local'},
        ];
        let schedule = {enabled: false, frequency: 'daily', time: '03:00', weekday: 0, next_run: null, last_run: null};
        let retention = {automatic: 3};
        const backupOverview = () => ({available: true, retention, schedule, archives: backupArchives,
            actions: {create: true, keep: true, delete: true, restore: true, upload: true},
            disk: {total: 40 * GIB, used: 33 * GIB, free: 7 * GIB, backups: 8 * GIB, database: GIB, objects: 3 * GIB},
            next_backup: {estimated: 4 * GIB, required: 9 * GIB, fits: false}});
        const backupPasswords = [];
        const backupCalls = [];
        await page.route('**/api/instance/**', async route => {
            const request = route.request();
            const endpoint = decodeURIComponent(new URL(request.url()).pathname.replace('/api/instance', ''));
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
            else if (endpoint === '/admin/updates') value = {available: true, can_update: true, can_restart: true, active: runnerActive, history: runnerHistory};
            else if (endpoint === '/admin/backups' && method === 'GET') value = backupOverview();
            else if (endpoint === '/admin/backups' && method === 'POST') {
                backupCalls.push('create');
                runnerActive = {id: 'fixture-backup', kind: 'backup', stage: 'backup', status: 'running', target_version: '1.0.3', created_at: new Date().toISOString()};
                value = {operation: runnerActive}; status = 202;
            }
            else if (endpoint === '/admin/backups/schedule' && method === 'PUT') {
                const input = request.postDataJSON();
                backupCalls.push(`schedule:${input.enabled}:${input.frequency}:${input.time}:${input.keep}`);
                schedule = {...schedule, enabled: input.enabled, frequency: input.frequency, time: input.time, weekday: input.weekday, next_run: input.enabled ? hoursAgo(-12) : null};
                retention = {automatic: input.keep};
                value = {schedule, retention};
            }
            else if (endpoint.startsWith('/admin/backups/assozeta-') && method === 'PATCH') {
                const name = endpoint.split('/').at(-1);
                backupCalls.push(`keep:${name}:${request.postDataJSON().keep}`);
                backupArchives = backupArchives.map(item => item.name === name ? {...item, keep: request.postDataJSON().keep} : item);
                value = backupArchives.find(item => item.name === name);
            }
            else if (endpoint.startsWith('/admin/backups/assozeta-') && method === 'DELETE') {
                const name = endpoint.split('/').at(-1);
                assert.equal(request.postDataJSON().password, 'fixture-secret');
                backupCalls.push(`delete:${name}`);
                backupArchives = backupArchives.filter(item => item.name !== name);
                value = {deleted: name};
            }
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
                } else if (method === 'DELETE') {
                    email = {...email, source: 'environment', revision: email.revision + 1};
                    value = email;
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
            await route.fulfill({status, contentType: 'application/json', body: status === 204 ? '' : JSON.stringify(value)});
        });
        // Trasferisci dati closes the Backup tab: Bakney ZIP export list and import drawer.
        await page.route('**/api/association/export/list', route => route.fulfill({json: {exports: [{document_id: 'fixture-export', filename: 'export_fixture.zip', file_size_bytes: 48 * 1024 ** 2, download_token: 'fixture', created_at: hoursAgo(30)}]}}));
        await page.route('**/api/instance/admin/data-restore', route => route.fulfill({json: {available: true, max_upload_bytes: 5 * GIB, active: null, history: [], backups: [], backups_next: null}}));
        await page.goto(`http://127.0.0.1:${port}/#/profile?page=self-instance&tab=backups`);
        const transfer = page.getByRole('region', {name: 'Trasferisci dati'});
        await expect(transfer.getByRole('list', {name: 'Export'}).getByRole('listitem')).toHaveCount(1);
        await transfer.getByRole('button', {name: 'Importa da Bakney'}).click();
        await expect(page.getByRole('dialog', {name: 'Importa da Bakney'}).getByRole('button', {name: 'Seleziona backup ZIP'})).toBeVisible();
        await page.waitForTimeout(400);
        await page.screenshot({path: path.join(output, `transfer-import-${viewport.width}.png`)});
        await page.keyboard.press('Escape');
        await transfer.scrollIntoViewIfNeeded();
        await page.screenshot({path: path.join(output, `transfer-${viewport.width}.png`)});
        assert(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1), 'transfer: horizontal overflow');
        await page.goto('about:blank');
        const tabs = page.getByRole('tablist', {name: 'Sezioni istanza'});
        const settingsNav = page.getByRole('tablist', {name: 'Impostazioni'});
        const navigate = label => tabs.getByRole('tab', {name: label}).click();
        const settings = async label => { await navigate('Impostazioni'); await settingsNav.getByRole('tab', {name: label}).click(); };
        const noOverflow = async label => assert(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1), `${label}: horizontal overflow`);
        // Dialogs live in the top layer, which full-page captures do not composite correctly.
        const shot = async name => { const dialog = await page.locator('dialog[open]').count(); if (dialog) await page.waitForTimeout(400); await page.screenshot({path: path.join(output, `${name}-${viewport.width}.png`), fullPage: !dialog}); };

        // Panoramica: three status tiles and one next step.
        await page.goto(`http://127.0.0.1:${port}/`);
        await expect(page.getByRole('heading', {name: 'Istanza', exact: true})).toBeVisible();
        await expect(tabs.getByRole('tab', {name: 'Panoramica'})).toHaveAttribute('aria-selected', 'true');
        await expect(page.getByText('Non ancora verificati')).toBeVisible();
        await expect(page.getByText('9 giorni fa', {exact: true})).toBeVisible();
        await expect(page.getByText('L’ultimo backup è di 9 giorni fa.')).toBeVisible();
        await expect(page.getByRole('button', {name: /^Versione/})).toContainText('Disponibile v1.0.4');
        await expect(page.getByRole('region', {name: 'Stato operazioni'})).toHaveCount(1);
        await noOverflow('overview');
        await shot('overview');

        // Keyboard: arrows move between tabs.
        await tabs.getByRole('tab', {name: 'Panoramica'}).focus();
        await page.keyboard.press('ArrowRight');
        await expect(tabs.getByRole('tab', {name: 'Backup'})).toBeFocused();
        await expect(tabs.getByRole('tab', {name: 'Backup'})).toHaveAttribute('aria-selected', 'true');

        // Email settings: save, conflict, reload, test without sending, reset through a dialog.
        await settings('Email');
        await expect(page.getByRole('heading', {name: 'Email di sistema', exact: true})).toBeVisible();
        await expect(page.getByRole('button', {name: 'Salva email', exact: true})).toBeDisabled();
        await page.getByLabel('Server SMTP', {exact: true}).fill('smtp.example.test');
        await page.getByLabel('Email mittente', {exact: true}).fill('sender@example.test');
        await page.getByLabel('Nome mittente', {exact: true}).fill('Club');
        await expect(page.getByRole('button', {name: 'Verifica connessione'})).toBeDisabled();
        await expect(page.getByText('Salva prima le modifiche.').first()).toBeVisible();
        await navigate('Panoramica');
        await expect(tabs.getByRole('tab', {name: /Impostazioni/})).toContainText('Impostazioni');
        await expect(tabs.locator('.dirty-dot')).toHaveCount(1);
        await settings('Email');
        await expect(page.getByLabel('Server SMTP', {exact: true})).toHaveValue('smtp.example.test');
        failSave = true;
        await page.getByRole('button', {name: 'Salva email', exact: true}).click();
        await expect(page.getByRole('alert').filter({hasText: 'Configurazione modificata'})).toBeVisible();
        await expect(page.getByRole('button', {name: 'Salva email', exact: true})).toBeEnabled();
        failSave = false;
        await page.getByRole('button', {name: 'Salva email', exact: true}).click();
        await expect(page.getByRole('button', {name: 'Salva email', exact: true})).toBeDisabled();
        await page.reload();
        await settings('Email');
        await expect(page.getByLabel('Server SMTP', {exact: true})).toHaveValue('smtp.example.test');
        await page.getByRole('button', {name: 'Verifica connessione'}).click();
        await expect(page.getByText('Connessione SMTP riuscita. Nessun messaggio inviato.')).toBeVisible();
        assert.equal(sent, 0);
        await page.getByLabel('Destinatario di prova').fill('chosen@example.test');
        await page.getByRole('button', {name: 'Invia email di prova', exact: true}).click();
        await expect(page.getByText('Il server SMTP ha accettato il messaggio. Verifica la casella.')).toBeVisible();
        assert.equal(sent, 1);
        await noOverflow('email');
        await shot('email');
        await page.getByRole('button', {name: 'Altre azioni email'}).click();
        await page.getByRole('menuitem', {name: /Ripristina valori del server/}).click();
        const resetDialog = page.getByRole('dialog', {name: 'Ripristinare i valori del server?'});
        await expect(resetDialog.getByRole('button', {name: 'Annulla'})).toBeFocused();
        await page.keyboard.press('Escape');
        await expect(resetDialog).toHaveCount(0);
        await expect(page.getByRole('button', {name: 'Altre azioni email'})).toBeFocused();
        await page.getByRole('button', {name: 'Altre azioni email'}).click();
        await page.getByRole('menuitem', {name: /Ripristina valori del server/}).click();
        await resetDialog.getByRole('button', {name: 'Ripristina'}).click();
        await expect(page.getByText('Valori del server', {exact: true})).toBeVisible();

        // Services: failed run reports an error, then the checks and the overview reflect the result.
        await navigate('Panoramica');
        await page.getByRole('button', {name: /Servizi/}).click();
        const checks = page.getByRole('dialog', {name: 'Stato dei servizi'});
        await expect(checks.locator('.diagnostic-result .verified')).toHaveCount(0);
        failRun = true;
        await checks.getByRole('button', {name: 'Verifica ora'}).click();
        await expect(checks.getByRole('alert')).toContainText('Servizio temporaneamente non disponibile');
        failRun = false;
        await checks.getByRole('button', {name: 'Verifica ora'}).click();
        await expect(checks.getByText('Database non disponibile. Controlla il servizio.')).toBeVisible();
        await expect(checks.locator('.diagnostic-result').filter({hasText: 'Configurazione email'})).toContainText('Non configurato');
        assert.equal(sent, 1, 'Routine diagnostics must never send mail');
        await shot('services');
        await checks.getByRole('button', {name: 'Chiudi'}).click();
        await expect(page.getByText('Servizi con problemi')).toBeVisible();
        await expect(page.getByRole('heading', {name: 'Da sistemare'})).toBeVisible();

        // Integrations: environment values, masked secrets, conflict, saves, reset.
        await settings('Integrazioni');
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
        await navigate('Panoramica'); await settings('Integrazioni');
        await expect(settingsNav.getByRole('tab', {name: /Integrazioni/}).locator('.dirty-dot')).toHaveCount(1);
        await expect(page.getByLabel('Chiave segreta Stripe', {exact: true})).toHaveValue('sk_test_changed');
        failIntegrationSave = true;
        await page.getByRole('button', {name: 'Salva Stripe', exact: true}).click();
        await expect(page.getByRole('alert').filter({hasText: 'Integrazione modificata'})).toBeVisible();
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
        await page.reload(); await settings('Integrazioni');
        await expect(page.getByLabel('Chiave pubblica Stripe', {exact: true})).toHaveValue('pk_test_changed');
        await expect(page.getByLabel('Client ID Google', {exact: true})).toHaveValue('saved.apps.googleusercontent.com');
        await expect(page.getByLabel('Client ID Apple', {exact: true})).toHaveValue('com.example.saved');
        await expect(page.getByText(/Il pulsante Apple nella pagina di accesso web non è disponibile/)).toBeVisible();
        await noOverflow('integrations');
        await shot('integrations');
        await page.getByRole('button', {name: 'Altre azioni Stripe'}).click();
        await page.getByRole('menuitem', {name: /Ripristina valori del server/}).click();
        await page.getByRole('dialog').getByRole('button', {name: 'Ripristina Stripe', exact: true}).click();
        await expect(page.getByLabel('Chiave pubblica Stripe', {exact: true})).toHaveValue('pk_test_environment');

        // Backup: disk, list, download with password, keep, delete, schedule, create.
        await navigate('Backup');
        await expect(page.getByRole('heading', {name: 'Backup', exact: true})).toBeVisible();
        await expect(page.getByRole('img', {name: /Disco: 33 GB usati su 40 GB, 7 GB liberi/})).toBeVisible();
        await expect(page.getByRole('alert').filter({hasText: 'Spazio insufficiente per il prossimo backup'})).toBeVisible();
        await expect(page.getByRole('button', {name: 'Crea backup', exact: true})).toBeDisabled();
        const backupRows = page.locator('table.backups tbody tr');
        await expect(backupRows).toHaveCount(3);
        await expect(backupRows.nth(0)).toContainText('Manuale');
        await expect(backupRows.nth(1)).toContainText('Prima dell’aggiornamento');
        await expect(backupRows.nth(1)).toContainText('Ultimo aggiornamento riuscito');
        await expect(backupRows.nth(2)).toContainText('—');
        await noOverflow('backups');
        await shot('backups');
        await backupRows.nth(0).getByRole('button', {name: /^Azioni backup del/}).click();
        await page.getByRole('menuitem', {name: 'Scarica', exact: true}).click();
        const download = page.getByRole('dialog', {name: 'Scarica backup'});
        await expect(download.getByLabel('Conferma la tua password')).toBeFocused();
        await expect(download.getByRole('note')).toContainText('non è cifrato');
        await shot('backup-download');
        await download.getByLabel('Conferma la tua password').fill('wrong');
        await download.getByRole('button', {name: 'Scarica backup', exact: true}).click();
        await expect(download.getByText('Password non corretta.', {exact: true})).toBeVisible();
        await expect(download.getByLabel('Conferma la tua password')).toHaveValue('');
        await download.getByLabel('Conferma la tua password').fill('fixture-secret');
        const backupDownload = page.waitForEvent('download');
        await page.keyboard.press('Enter');
        assert.equal((await backupDownload).suggestedFilename(), backupArchive);
        await expect(download).toHaveCount(0);
        assert.deepEqual(backupPasswords, ['wrong', 'fixture-secret']);
        backupPasswords.length = 0;

        await backupRows.nth(0).getByRole('button', {name: /^Azioni backup del/}).click();
        await page.getByRole('menuitem', {name: 'Conserva', exact: true}).click();
        await expect(backupRows.nth(0).getByText('Conservato')).toHaveCount(1);
        await backupRows.nth(2).getByRole('button', {name: /^Azioni backup del/}).click();
        await page.getByRole('menuitem', {name: 'Elimina', exact: true}).click();
        const remove = page.getByRole('dialog', {name: 'Eliminare il backup?'});
        await expect(remove.getByRole('button', {name: 'Annulla'})).toBeFocused();
        await expect(remove.getByRole('button', {name: 'Elimina'})).toBeDisabled();
        await remove.getByLabel('Conferma la tua password').fill('fixture-secret');
        await remove.getByRole('button', {name: 'Elimina'}).click();
        await expect(backupRows).toHaveCount(2);

        const scheduleCard = page.getByRole('form', {name: 'Backup automatici'});
        await expect(scheduleCard.getByText('Disattivati')).toBeVisible();
        await setSwitch(scheduleCard, 'backup-schedule-enabled', 'Attiva backup automatici', true);
        await scheduleCard.getByLabel('Frequenza', {exact: true}).selectOption('weekly');
        await scheduleCard.getByLabel('Giorno', {exact: true}).selectOption({label: 'Domenica'});
        await scheduleCard.getByLabel('Ora', {exact: true}).fill('02:30');
        await scheduleCard.getByLabel('Da conservare').fill('5');
        await scheduleCard.getByRole('button', {name: 'Salva pianificazione'}).click();
        await expect(scheduleCard.getByText(/^Prossimo:/)).toBeVisible();
        await expect(scheduleCard.getByRole('button', {name: 'Salva pianificazione'})).toBeDisabled();
        await shot('backups-schedule');

        // Copia remota: S3 drawer, key wizard, location icons and recovering a remote-only copy.
        // Self-contained: its own routes and state, removed at the end.
        {
            const remoteKey = 'AGE-SECRET-KEY-1' + 'QPZRY9X8GF2TVDW0S3JN54KHCE6MUA7L'.repeat(2).slice(0, 58);
            const savedHistory = runnerHistory;
            const remoteCalls = [];
            let remoteConfig = {configured: false, enabled: false, endpoint: '', region: '', bucket: '', prefix: '', access_key: '', path_style: false,
                retention: 3, secret_configured: false, key: null, pending_key: null, previous_keys: [], last_upload: null, last_failure: null, uploading: null};
            const remoteOnly = {name: 'assozeta-20261020T020000Z.tar.gz', size: 2 * GIB, created_at: hoursAgo(24 * 20), version: '1.0.2', origin: 'scheduled',
                keep: false, last_upgrade: false, pending_upgrade: false, location: 'remote', key_id: 'abcd1234'};
            const remoteSummary = () => ({configured: remoteConfig.configured, enabled: remoteConfig.enabled, key_id: remoteConfig.key?.id || null,
                last_upload: remoteConfig.enabled ? {at: hoursAgo(2), name: backupArchive} : null, last_failure: null, uploading: null,
                status: remoteConfig.configured ? 'ok' : 'off', error: null});
            const remoteArchives = () => remoteConfig.enabled
                ? [...backupArchives.map((item, index) => ({...item, location: index === 0 ? 'both' : 'local', key_id: index === 0 ? 'abcd1234' : undefined})), remoteOnly]
                : backupArchives;
            const remotePattern = /\/api\/instance\/admin\/backups(\/remote(\/.*)?|\?.*)?$/;
            const remoteRoute = async route => {
                const request = route.request();
                const method = request.method();
                const endpoint = new URL(request.url()).pathname.replace('/api/instance', '');
                let value;
                let status = 200;
                if (endpoint === '/admin/backups') {
                    if (method !== 'GET') return route.fallback();
                    value = {...backupOverview(), remote: remoteSummary(), archives: remoteArchives()};
                } else if (endpoint === '/admin/backups/remote' && method === 'GET') value = remoteConfig;
                else if (endpoint === '/admin/backups/remote' && method === 'PUT') {
                    const {secret_key: secret, password, ...input} = request.postDataJSON();
                    assert.equal(password, 'fixture-secret');
                    remoteCalls.push(`put:${Object.keys(input).sort().join(',')}${secret ? ':secret' : ''}`);
                    remoteConfig = {...remoteConfig, ...input, secret_configured: remoteConfig.secret_configured || !!secret};
                    remoteConfig.configured = !!(remoteConfig.bucket && remoteConfig.access_key && remoteConfig.secret_configured);
                    value = remoteConfig;
                } else if (endpoint === '/admin/backups/remote/test') {
                    assert.equal(request.postDataJSON().password, 'fixture-secret');
                    remoteCalls.push(`test:${request.postDataJSON().bucket}`);
                    value = {ok: true, message: 'Connessione riuscita: scrittura, lettura ed eliminazione funzionano.'};
                } else if (endpoint === '/admin/backups/remote/key') {
                    assert.equal(request.postDataJSON().password, 'fixture-secret');
                    remoteCalls.push('key');
                    remoteConfig = {...remoteConfig, pending_key: {id: 'abcd1234'}};
                    value = {key_id: 'abcd1234', recovery_key: remoteKey};
                    status = 201;
                } else if (endpoint === '/admin/backups/remote/key/confirm') {
                    const input = request.postDataJSON();
                    assert.equal(input.password, 'fixture-secret');
                    if (input.tail.toUpperCase() !== remoteKey.slice(-6)) {status = 400; value = {error: 'I caratteri non corrispondono alla chiave.'};}
                    else {
                        remoteCalls.push('confirm');
                        remoteConfig = {...remoteConfig, key: {id: 'abcd1234', created_at: new Date().toISOString()}, pending_key: null, enabled: true};
                        value = remoteConfig;
                    }
                } else if (endpoint === '/admin/backups/remote/fetch') {
                    const input = request.postDataJSON();
                    remoteCalls.push(`fetch:${input.name}`);
                    assert.equal(input.recovery_key, remoteKey);
                    runnerActive = {id: 'fixture-fetch', kind: 'fetch', stage: 'downloading', status: 'running', target_version: '1.0.3', created_at: new Date().toISOString()};
                    value = {operation: runnerActive};
                    status = 202;
                } else return route.fallback();
                await route.fulfill({status, contentType: 'application/json', body: JSON.stringify(value)});
            };
            await page.route(remotePattern, remoteRoute);
            await page.getByRole('button', {name: 'Altre azioni backup'}).click();
            await page.getByRole('menuitem', {name: 'Aggiorna elenco'}).click();
            const remoteCard = page.getByRole('region', {name: 'Copia remota'});
            await expect(remoteCard.getByText('Non configurata', {exact: true})).toBeVisible();
            await expect(page.locator('table.backups th.where')).toHaveCount(0);
            await remoteCard.getByRole('button', {name: 'Configura'}).click();
            const remoteDrawer = page.getByRole('dialog', {name: 'Copia remota'});
            await remoteDrawer.getByLabel('Endpoint').fill('https://s3.example.test');
            await remoteDrawer.getByLabel('Bucket').fill('club-backups');
            await remoteDrawer.getByLabel('Access key').fill('AKIAFIXTURE');
            await remoteDrawer.getByLabel('Secret key').fill('fixture-s3-secret');
            await expect(remoteDrawer.getByRole('button', {name: 'Prova connessione'})).toBeDisabled();
            await expect(remoteDrawer.getByRole('button', {name: 'Salva', exact: true})).toBeDisabled();
            await remoteDrawer.getByLabel('Conferma la tua password').fill('fixture-secret');
            await remoteDrawer.getByRole('button', {name: 'Prova connessione'}).click();
            await expect(remoteDrawer.getByText(/^Connessione riuscita/)).toBeVisible();
            await noOverflow('remote-drawer');
            await shot('remote-drawer');
            await remoteDrawer.getByRole('button', {name: 'Salva', exact: true}).click();
            const wizard = page.getByRole('dialog', {name: 'Chiave di ripristino'});
            await expect(wizard.getByRole('note')).toContainText('Senza questa chiave');
            await expect(wizard.getByLabel('Chiave abcd1234')).toHaveValue(remoteKey);
            const keyDownload = page.waitForEvent('download');
            await wizard.getByRole('button', {name: 'Scarica file'}).click();
            assert.equal((await keyDownload).suggestedFilename(), 'assozeta-chiave-abcd1234.txt');
            await shot('remote-key');
            await wizard.getByRole('button', {name: 'Avanti'}).click();
            await wizard.getByLabel('Ultimi 6 caratteri della chiave').fill('AAAAAA');
            await wizard.getByRole('button', {name: 'Conferma'}).click();
            await expect(wizard.getByText('I caratteri non corrispondono alla chiave.')).toBeVisible();
            await wizard.getByLabel('Ultimi 6 caratteri della chiave').fill(remoteKey.slice(-6).toLowerCase());
            await wizard.getByRole('button', {name: 'Conferma'}).click();
            await expect(wizard.getByText('Chiave abcd1234 attiva.')).toBeVisible();
            await wizard.getByRole('button', {name: 'Fine'}).click();
            await expect(wizard).toHaveCount(0);
            await expect(remoteCard.getByRole('switch', {name: 'Attiva copia remota'})).toBeChecked();
            await expect(remoteCard.getByText(/^Ultima copia/)).toBeVisible();
            const remoteRows = page.locator('table.backups tbody tr');
            await expect(remoteRows).toHaveCount(3);
            await expect(remoteRows.nth(0).getByText('Locale e remoto', {exact: true})).toBeAttached();
            await expect(remoteRows.nth(1).getByText('Solo locale', {exact: true})).toBeAttached();
            await expect(remoteRows.nth(2).getByText('Solo remoto', {exact: true})).toBeAttached();
            await noOverflow('backups-remote');
            await shot('backups-remote');
            await remoteRows.nth(2).getByRole('button', {name: /^Azioni backup del/}).click();
            await expect(page.getByRole('menuitem', {name: 'Scarica', exact: true})).toHaveCount(0);
            await page.getByRole('menuitem', {name: 'Recupera sul server'}).click();
            const fetchDialog = page.getByRole('dialog', {name: 'Recupera dalla copia remota'});
            await expect(fetchDialog.getByLabel('Chiave di ripristino')).toBeFocused();
            await expect(fetchDialog.getByRole('button', {name: 'Recupera', exact: true})).toBeDisabled();
            await fetchDialog.getByLabel('Chiave di ripristino').fill(remoteKey);
            await shot('remote-fetch');
            await fetchDialog.getByRole('button', {name: 'Recupera', exact: true}).click();
            await expect(page.getByRole('region', {name: 'Stato operazioni'})).toContainText(/Recupero dalla copia remota\s+Download e decifratura/);
            await expect(page.getByText('Recupero dalla copia remota…')).toBeVisible();
            runnerActive = null;
            runnerHistory = [{id: 'fixture-fetch', kind: 'fetch', stage: 'completed', status: 'succeeded', target_version: '1.0.3', created_at: new Date().toISOString(), updated_at: new Date().toISOString()}, ...savedHistory];
            // The idle status poll runs every 15 s.
            await expect(page.getByRole('region', {name: 'Stato operazioni'})).toContainText('Backup recuperato sul server', {timeout: 20000});
            assert.deepEqual(remoteCalls, ['test:club-backups', 'put:access_key,bucket,endpoint,path_style,prefix,region,retention:secret', 'key', 'confirm', `fetch:${remoteOnly.name}`]);
            await page.unroute(remotePattern, remoteRoute);
            runnerHistory = savedHistory;
        }

        backupArchives = backupArchives.map(item => ({...item, created_at: item === backupArchives[0] ? hoursAgo(1) : item.created_at}));
        await page.reload();
        await navigate('Backup');
        assert.deepEqual(backupCalls.slice(0, 3), [`keep:${backupArchive}:true`, 'delete:assozeta-20261001T020000Z.tar.gz', 'schedule:true:weekly:02:30:5']);

        // Updates: one version line, release notes drawer, compact history, review dialog.
        await navigate('Aggiornamenti');
        await expect(page.getByRole('heading', {name: 'Versione installata'})).toBeVisible();
        await expect(page.locator('#instance-panel-updates').getByText('Disponibile v1.0.4')).toBeVisible();
        const history = page.getByRole('list', {name: 'Cronologia'});
        await expect(history.getByRole('listitem')).toHaveCount(5);
        await page.getByRole('button', {name: 'Mostra tutte (20)'}).click();
        await expect(history.getByRole('listitem')).toHaveCount(20);
        await page.getByRole('button', {name: 'Note di rilascio'}).click();
        const notes = page.getByRole('dialog', {name: 'Note di rilascio'});
        await expect(notes.getByText('Fine delle note di rilascio.', {exact: true})).toBeAttached();
        await shot('release-notes');
        await page.keyboard.press('Escape');
        await expect(notes).toHaveCount(0);
        await page.getByRole('button', {name: 'Aggiorna a v1.0.4', exact: true}).click();
        const review = page.getByRole('dialog', {name: 'Aggiorna a v1.0.4'});
        assert(await review.locator('.review-notes').evaluate(element => element.scrollHeight > element.clientHeight));
        await expect(review.getByRole('button', {name: 'Avvia aggiornamento', exact: true})).toBeEnabled();
        await shot('update-review');
        await review.getByRole('button', {name: 'Annulla', exact: true}).click();
        await expect(review).toHaveCount(0);
        await noOverflow('updates');
        await shot('updates');

        await settings('Identità');
        await expect(page.getByRole('heading', {name: 'Identità', exact: true})).toBeVisible();
        await noOverflow('identity');
        await shot('identity');
        await settings('Bakney');
        await expect(page.getByRole('button', {name: 'Genera codice'})).toBeVisible();
        await noOverflow('bakney');

        // Header menu: restart shows a short confirmation.
        await page.getByRole('button', {name: 'Azioni istanza'}).click();
        await page.getByRole('menuitem', {name: /Riavvia applicazione/}).click();
        await expect(page.getByRole('dialog', {name: 'Riavviare l’applicazione?'})).toBeVisible();
        await page.keyboard.press('Escape');

        // Creating a backup shows progress in the banner and the list.
        await navigate('Backup');
        await page.route(/\/api\/instance\/admin\/backups(\?.*)?$/, async route => {
            if (route.request().method() !== 'GET') return route.fallback();
            await route.fulfill({contentType: 'application/json', body: JSON.stringify({...backupOverview(), next_backup: {estimated: GIB, required: 3 * GIB, fits: true}})});
        });
        await page.getByRole('button', {name: 'Altre azioni backup'}).click();
        await page.getByRole('menuitem', {name: 'Aggiorna elenco'}).click();
        await expect(page.getByRole('button', {name: 'Crea backup', exact: true})).toBeEnabled();
        await page.getByRole('button', {name: 'Crea backup', exact: true}).click();
        await page.getByRole('dialog', {name: 'Creare un backup ora?'}).getByRole('button', {name: 'Crea backup'}).click();
        await expect(page.getByRole('region', {name: 'Stato operazioni'})).toContainText('Backup in corso');
        await expect(page.getByText('Backup in corso…')).toBeVisible();
        await expect(page.getByRole('button', {name: 'Crea backup', exact: true})).toBeDisabled();
        await shot('backup-running');
        assert(backupCalls.includes('create'));
        runnerActive = null;
        runnerHistory = [{id: 'fixture-backup', kind: 'backup', stage: 'completed', status: 'succeeded', target_version: '1.0.3', created_at: new Date().toISOString(), updated_at: new Date().toISOString()}, ...runnerHistory];
        await expect(page.getByRole('region', {name: 'Stato operazioni'})).toContainText('Backup completato', {timeout: 10000});

        // Restore and upload: typed confirmation, banner progress, upload progress and the new row.
        {
            const restoreCalls = [];
            const uploaded = 'assozeta-20261109T100000Z.tar.gz';
            await page.route(/\/api\/instance\/admin\/backups\/(upload|assozeta-[^/]+\/restore)$/, async route => {
                const request = route.request();
                if (request.url().endsWith('/upload')) {
                    const body = request.postDataBuffer();
                    restoreCalls.push(`upload:${body.length}:${request.headers()['content-type']}`);
                    const archive = {name: uploaded, size: body.length, created_at: hoursAgo(48), version: '1.0.2', origin: 'uploaded', keep: false, last_upgrade: false, pending_upgrade: false, location: 'local'};
                    backupArchives = [archive, ...backupArchives];
                    await route.fulfill({status: 201, contentType: 'application/json', body: JSON.stringify({archive})});
                    return;
                }
                const input = request.postDataJSON();
                restoreCalls.push(`restore:${decodeURIComponent(request.url().split('/').at(-2))}:${input.password}:${input.confirm}`);
                runnerActive = {id: 'fixture-restore', kind: 'restore', stage: 'backup', status: 'running', target_version: '1.0.3', created_at: new Date().toISOString()};
                await route.fulfill({status: 202, contentType: 'application/json', body: JSON.stringify({operation: runnerActive})});
            });
            await page.getByRole('button', {name: 'Altre azioni backup'}).click();
            await page.getByRole('menuitem', {name: 'Carica backup'}).click();
            const uploadDialog = page.getByRole('dialog', {name: 'Carica backup'});
            const picker = uploadDialog.getByLabel('File di backup (.tar.gz)');
            await picker.setInputFiles({name: 'export.zip', mimeType: 'application/zip', buffer: Buffer.alloc(16)});
            await expect(uploadDialog.getByRole('alert')).toHaveText('Scegli un file di backup .tar.gz.');
            await expect(uploadDialog.getByRole('button', {name: 'Carica', exact: true})).toBeDisabled();
            await picker.setInputFiles({name: 'assozeta-20261107T020000Z.tar.gz', mimeType: 'application/gzip', buffer: Buffer.alloc(256 * 1024, 1)});
            await expect(uploadDialog.getByText('Dimensione 256 KB')).toBeVisible();
            await noOverflow('backup-upload');
            await shot('backup-upload');
            await uploadDialog.getByRole('button', {name: 'Carica', exact: true}).click();
            await expect(uploadDialog).toHaveCount(0);
            await expect(backupRows.nth(0)).toContainText('Caricato');
            assert.deepEqual(restoreCalls, [`upload:${256 * 1024}:application/gzip`]);

            await backupRows.nth(0).getByRole('button', {name: /^Azioni backup del/}).click();
            await page.getByRole('menuitem', {name: 'Ripristina', exact: true}).click();
            const restore = page.getByRole('dialog', {name: 'Ripristinare questo backup?'});
            await expect(restore.getByRole('button', {name: 'Annulla'})).toBeFocused();
            await expect(restore).toContainText('Prima viene creato un backup di sicurezza.');
            await expect(restore).toContainText('v1.0.2');
            const confirmRestore = restore.getByRole('button', {name: 'Ripristina', exact: true});
            await expect(confirmRestore).toBeDisabled();
            await restore.getByLabel('Conferma la tua password').fill('fixture-secret');
            await restore.getByLabel('Scrivi RIPRISTINA').fill('ripristina');
            await expect(confirmRestore).toBeDisabled();
            await restore.getByLabel('Scrivi RIPRISTINA').fill('RIPRISTINA');
            await expect(confirmRestore).toBeEnabled();
            await expect(confirmRestore).toHaveClass(/btn-danger/);
            await shot('backup-restore');
            await confirmRestore.click();
            await expect(restore).toHaveCount(0);
            await expect(page.getByRole('region', {name: 'Stato operazioni'})).toContainText('Ripristino in corso');
            await expect(page.getByText('Ripristino in corso…')).toBeVisible();
            await noOverflow('backup-restore-running');
            await shot('backup-restore-running');
            assert.equal(restoreCalls[1], `restore:${uploaded}:fixture-secret:RIPRISTINA`);
            runnerActive = null;
            runnerHistory = [{id: 'fixture-restore', kind: 'restore', stage: 'completed', status: 'succeeded', target_version: '1.0.3', created_at: new Date().toISOString(), updated_at: new Date().toISOString()}, ...runnerHistory];
            await expect(page.getByRole('region', {name: 'Stato operazioni'})).toContainText('Ripristino completato', {timeout: 20000});
            runnerHistory = runnerHistory.filter(item => item.id !== 'fixture-restore');
        }

        // A recovery requirement is shown on every tab.
        runnerHistory = [{...runnerHistory[1], stage: 'recovery_required', recovery: 'Ripristina il backup verificato.'}];
        await page.reload();
        await expect(page.getByRole('region', {name: 'Stato operazioni'}).getByText('Ripristina il backup verificato.', {exact: true})).toBeVisible();
        await navigate('Aggiornamenti');
        await expect(page.getByText('Ripristina il backup verificato.', {exact: true}).first()).toBeVisible();
        for (const label of ['Panoramica', 'Backup', 'Aggiornamenti', 'Impostazioni']) {
            await navigate(label);
            await noOverflow(label);
        }
        assert.deepEqual(errors, []);
        reports.push({viewport, passed: true, scope: 'Real Svelte components, fixture API; tab keyboard navigation, status tiles and next step, saves/reloads, failed saves, SMTP actions, dialogs and focus, failed diagnostics, integration environment values, masked secrets, explicit resets, backup download/keep/delete/schedule/create/upload/restore, update review, history, recovery visibility, overflow.'});
        await context.close();
    }
    await fs.writeFile(path.join(output, 'report.json'), JSON.stringify(reports, null, 2));
    console.log(JSON.stringify(reports, null, 2));
} finally {
    await browser?.close();
    await server.close();
    await fs.rm(cacheDir, {recursive: true, force: true});
}
