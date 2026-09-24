// Disposable UI/API fixtures: this test cannot restore or contact a real instance.
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
const output = path.join(root, 'quality-reports/data-restore-ui');
await fs.mkdir(output, {recursive: true});
const server = await createServer({
    root: ui, configFile: false, logLevel: 'error', server: {host: '127.0.0.1', port: 5199, strictPort: true},
    resolve: {alias: {utils: `${ui}/src/utils`, store: `${ui}/src/store`}},
    plugins: [{name: 'data-restore-fixture', enforce: 'pre',
        resolveId(id) {
            const stubs = ['ApiMiddleware.js', 'instanceStore.js', 'stores.js', 'NotificationService.js', 'exportProgressStore.js'];
            const name = stubs.find(name => id.endsWith('/' + name));
            if (name) return '\0fixture-' + name;
            if (id === 'utils/Permissions' || id.endsWith('/Permissions') || id.endsWith('/Permissions.js')) return '\0fixture-permissions';
            if (id === '/restore-entry.js') return '\0fixture-entry';
        },
        load(id) {
            if (id === '\0fixture-ApiMiddleware.js') return `export async function apiFetch(url, options) { const r = await fetch(url, options); return {error: !r.ok, response: await r.json()}; }`;
            if (id === '\0fixture-instanceStore.js') return `export const getApiHost = () => '/api'; export const clearInstanceCache = () => {};`;
            if (id === '\0fixture-stores.js') return `import {writable} from 'svelte/store'; const make = value => Object.assign(writable(value), {useLocalStorage(){}}); export const subPage = make('data-management'), userData = make({}), sessionToken = make('fixture');`;
            if (id === '\0fixture-exportProgressStore.js') return `import {writable} from 'svelte/store'; export const exportProgress = Object.assign(writable({active:false}), {applySnapshot(){}});`;
            if (id === '\0fixture-NotificationService.js') return `export default {syncActiveExport: async () => {}, subscribeRestoreProgress(callback) { window.restoreProgressFixture = callback; return () => { window.restoreProgressFixture = null; }; }};`;
            if (id === '\0fixture-permissions') return `export const canPerformAction = () => true;`;
            if (id === '\0fixture-entry') return `import DataManagement from '/src/routes/profile/sections/DataManagement.svelte'; window.__bakney = {env:{API:{ASSOCIATION:{EXPORT:{LIST:'/fixture/exports', START:'/fixture/export'}}, DOCUMENT:{RETRIEVE:'/fixture/document'}}}}; new DataManagement({target: document.getElementById('app'), props:{instanceOwner: !location.search.includes('non-owner')}});`;
        },
        configureServer(vite) {
            vite.middlewares.use(async (req, res, next) => {
                if (req.url?.split('?')[0] !== '/') return next();
                const html = await vite.transformIndexHtml('/', '<!doctype html><html lang="it"><head><meta name="viewport" content="width=device-width,initial-scale=1"><link rel="stylesheet" href="/static/css/bootstrap.min.css"><link rel="stylesheet" href="/static/css/app-bundle.css"><link rel="stylesheet" href="/global.css"><link rel="stylesheet" href="/dark-mode.css"><link rel="stylesheet" href="/brand.css"></head><body><main id="app" style="max-width:1100px;margin:24px auto;padding:12px"></main><script type="module" src="/restore-entry.js"></script></body></html>');
                res.setHeader('Content-Type', 'text/html'); res.end(html);
            });
        },
    }, svelte({configFile: false})],
});
let browser;
try {
    await server.listen();
    browser = await chromium.launch({headless: true});
    for (const viewport of [{width: 1440, height: 1000}, {width: 390, height: 844}]) {
        const context = await browser.newContext({viewport});
        const page = await context.newPage();
        await page.clock.install();
        const errors = [];
        page.on('requestfailed', request => console.error('Request failed:', request.url(), request.failure()));
        page.on('pageerror', error => { errors.push(error.message); console.error(error.stack); });
        page.on('console', message => { if (message.type() === 'error') console.error(message.text()); });
        let op = null;
        let failUpload = false;
        let failStatus = false;
        let starts = 0;
        let downloads = 0;
        let relationReports = 0;
        let downloadedPath;
        let profileFetches = 0;
        let failProfile = true;
        const historicBackups = Array.from({length:12}, (_, i) => ({id:`old-backup-${i}`, state:'completed', stage:'completed',
            created_at:`2026-08-${String(20-i).padStart(2,'0')}T12:00:00Z`, has_recovery_backup:true, preview:{}}));
        await page.route('**/api/profile/info', route => {
            profileFetches++;
            return failProfile ? route.fulfill({status:503,json:{error:'Profilo non disponibile. Riprova.'}})
                : route.fulfill({json:{user_data:{user_id:'owner-fixture',sport_association:{denomination:'Restored ASD',tax_code:'RESTORED'}}}});
        });
        await page.route('**/fixture/**', route => route.fulfill({json: {exports: []}}));
        await page.route('**/api/instance/admin/data-restore**', async route => {
            const request = route.request();
            const endpoint = new URL(request.url()).pathname;
            if (request.method() === 'GET' && endpoint.endsWith('/missing-relations')) {
                relationReports++;
                return route.fulfill({contentType: 'application/json', json: {missing_relations: [{model:'MedicalCertificate', field:'user_id', record_id:'certificate-original-id', target_id:'missing-user-id'}]}});
            }
            if (request.method() === 'GET' && endpoint.endsWith('/backup')) {
                downloads++;
                downloadedPath = endpoint;
                return route.fulfill({contentType: 'application/zip', body: 'fixture backup bytes'});
            }
            if (request.method() === 'GET' && endpoint.endsWith('/backups')) {
                const cursor = new URL(request.url()).searchParams.get('before');
                const start = historicBackups.findIndex(item => item.id === cursor) + 1;
                assert.ok(start > 0);
                return route.fulfill({json:{backups:historicBackups.slice(start),backups_next:null}});
            }
            if (request.method() === 'GET') {
                if (failStatus) return route.fulfill({status:503, json:{error:'Connessione temporaneamente non disponibile'}});
                return route.fulfill({json: {available: true, max_upload_bytes: 5*1024**3, history: op ? [op] : [],
                    backups: [...(op?.has_recovery_backup ? [op] : []), ...historicBackups].slice(0,10), backups_next:op?.has_recovery_backup ? 'old-backup-8' : 'old-backup-9',
                    active: op && ['queued','running'].includes(op.state) ? op : null}});
            }
            if (endpoint.endsWith('/data-restore')) {
                if (failUpload) return route.fulfill({status:400, json:{error:'Export incompleto: file mancante.'}});
                op = {id:'fixture-restore', state:'review', stage:'validated', created_at:'2026-09-23T12:00:00Z',
                    preview:{association:'Associazione Bakney', export_date:'2026-09-23T12:00:00Z', records:1234, files:80, missing_media:2, missing_relations:58, legacy_invoices:3}, has_recovery_backup:false};
                return route.fulfill({status:201, json:op});
            }
            if (endpoint.endsWith('/start')) {
                assert.equal(request.postDataJSON().confirmation, 'RIPRISTINA');
                assert.equal(request.postDataJSON().allow_missing_media, true);
                starts++;
                op = {...op, state:'queued', stage:'queued'};
            } else if (endpoint.endsWith('/dismiss')) op = {...op,preview:{...op.preview,dismissed:true}};
            else if (endpoint.endsWith('/cancel')) op = {...op,state:'cancelled',stage:'cancelled'};
            else if (endpoint.endsWith('/resume')) op = {...op,state:'running',stage:'backup'};
            return route.fulfill({status:202, json:op});
        });
        await page.goto('http://127.0.0.1:5199/');
        await page.evaluate(() => import('/restore-entry.js'));
        await expect(page.getByRole('heading', {name:'Gestione Dati',exact:true})).toBeVisible({timeout:30000});
        await expect(page.getByRole('button',{name:'Avvia Export',exact:true})).toBeVisible();
        await page.getByRole('button',{name:'Ripristina backup',exact:true}).click();
        await expect(page.getByText('Formato ZIP · Dimensione massima 5 GB', {exact:true})).toBeVisible();
        const picker = page.getByRole('button',{name:'Seleziona backup ZIP',exact:true});
        await expect(picker).toBeEnabled();
        const [chooser] = await Promise.all([
            page.waitForEvent('filechooser'),
            picker.press('Enter'),
        ]);
        await chooser.setFiles({name:'wrong.txt',mimeType:'text/plain',buffer:Buffer.from('fixture')});
        await expect(page.getByRole('alert')).toContainText('Seleziona un solo file ZIP');
        // Exercise the size boundary without allocating or uploading a 5 GB file.
        await page.locator('input[type=file]').evaluate(input => {
            const transfer = new DataTransfer();
            const oversized = new File(['fixture'], 'oversized.zip', {type:'application/zip'});
            Object.defineProperty(oversized, 'size', {value:5*1024**3 + 1});
            transfer.items.add(oversized);
            input.files = transfer.files;
            input.dispatchEvent(new Event('change', {bubbles:true}));
        });
        await expect(page.getByRole('alert')).toContainText('Il file supera il limite di 5 GB');
        await picker.evaluate(element => {
            const transfer = new DataTransfer();
            transfer.items.add(new File(['fixture'], 'dropped-backup.ZIP', {type:'application/zip'}));
            element.dispatchEvent(new DragEvent('drop', {bubbles:true,cancelable:true,dataTransfer:transfer}));
        });
        await expect(page.getByText('dropped-backup.ZIP',{exact:true})).toBeVisible();
        await page.getByRole('button',{name:'Rimuovi backup selezionato',exact:true}).click();
        await expect(picker).toBeVisible();
        await page.screenshot({path:path.join(output,`${viewport.width}-upload.png`),fullPage:true});
        await page.locator('input[type=file]').setInputFiles({name:'bakney.zip',mimeType:'application/zip',buffer:Buffer.from('fixture')});
        await expect(page.getByText('bakney.zip',{exact:true})).toBeVisible();
        failUpload = true;
        await page.getByRole('button',{name:'Carica e verifica backup',exact:true}).click();
        await expect(page.getByRole('alert')).toContainText('Export incompleto');
        failUpload = false;
        await page.getByRole('button',{name:'Carica e verifica backup',exact:true}).click();
        await expect(page.getByRole('heading',{name:'Backup verificato',exact:true})).toBeVisible();
        await expect(page.getByText('Le 3 fatture del servizio Bakney', {exact:false})).toBeVisible();
        await expect(page.getByText('Il backup contiene 58 collegamenti opzionali', {exact:false})).toBeVisible();
        await page.getByRole('button',{name:'Scarica rapporto relazioni mancanti',exact:true}).click();
        await expect.poll(()=>relationReports).toBe(1);
        await page.getByRole('button',{name:'Visualizza rapporto',exact:true}).click();
        await expect(page.getByText('Certificati medici → Utente', {exact:false})).toBeVisible();
        await page.locator('summary').filter({hasText:'Certificati medici'}).click();
        await expect(page.getByText('certificate-original-id',{exact:true})).toBeVisible();
        await expect(page.getByText('missing-user-id',{exact:true})).toBeVisible();
        const warningIcon = page.locator('.alert-light-warning .alert-icon').first();
        await expect(warningIcon).toHaveCSS('color', 'rgb(154, 103, 0)');
        const confirm = page.getByRole('button',{name:'Sostituisci i dati',exact:true});
        await expect(confirm).toBeDisabled();
        await page.getByLabel('Scrivi RIPRISTINA per confermare la sostituzione').fill('RIPRISTINA');
        await expect(confirm).toBeDisabled();
        await setSwitch(page, 'restore-missing-media', 'Accetto che 2 allegati o firme non inclusi non siano recuperati', true);
        await expect(confirm).toBeEnabled();
        await page.screenshot({path:path.join(output,`${viewport.width}-review.png`),fullPage:true});
        assert.equal(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth), true, JSON.stringify(await page.evaluate(() => [...document.querySelectorAll('*')].filter(e => e.getBoundingClientRect().right > innerWidth + 1).slice(0, 12).map(e => [e.tagName,e.className,e.getBoundingClientRect().width]))));
        await confirm.click();
        await expect(page.getByRole('heading',{name:'In attesa del worker',exact:true})).toBeVisible();
        assert.equal(starts,1);
        await page.evaluate(() => window.restoreProgressFixture({operation_id:'fixture-restore', attempt:1, sequence:2,
            phase:'files', completed:1048576, total:2097152, unit:'bytes', percent:50, eta_seconds:120, updated_at:new Date().toISOString()}));
        await expect(page.getByRole('progressbar')).toHaveAttribute('aria-valuenow','50');
        await expect(page.getByText('Circa 2 min',{exact:true})).toBeVisible();
        await page.evaluate(() => window.restoreProgressFixture({operation_id:'fixture-restore', attempt:1, sequence:1,
            phase:'files', percent:10, updated_at:new Date().toISOString()}));
        await expect(page.getByRole('progressbar')).toHaveAttribute('aria-valuenow','50');
        await page.screenshot({path:path.join(output,`${viewport.width}-progress.png`),fullPage:true});
        await page.reload();
        await page.getByRole('button',{name:'Ripristina backup',exact:true}).click();
        await expect(page.getByRole('heading',{name:'In attesa del worker',exact:true})).toBeVisible();
        failStatus = true;
        await page.clock.fastForward(4500);
        await expect(page.getByRole('alert')).toContainText('Connessione temporaneamente');
        failStatus = false;
        await page.getByRole('button',{name:'Aggiorna stato',exact:true}).click();
        await page.locator('summary').filter({hasText:'Il ripristino sembra fermo?'}).click();
        await page.getByRole('button',{name:'Riprendi',exact:true}).click();
        await expect(page.getByRole('heading',{name:'Creazione della copia di sicurezza',exact:true})).toBeVisible();
        await expect(page.getByRole('button',{name:'Riprendi',exact:true})).toBeEnabled();
        op = {...op,state:'failed',stage:'failed',error:'I dati precedenti sono conservati.',has_recovery_backup:true};
        await page.clock.fastForward(4500);
        await expect(page.getByRole('heading',{name:'Ripristino non riuscito',exact:true})).toBeVisible({timeout:10000});
        await expect(page.getByRole('alert')).toContainText('I dati precedenti');
        await page.getByRole('button',{name:'Chiudi esito ripristino',exact:true}).click();
        await expect(page.getByRole('heading',{name:'Ripristino non riuscito',exact:true})).toHaveCount(0);
        await page.reload();
        await page.getByRole('button',{name:'Ripristina backup',exact:true}).click();
        await expect(page.getByRole('heading',{name:'Ripristino non riuscito',exact:true})).toHaveCount(0);
        op = {...op,state:'completed',stage:'completed',error:'',preview:{...op.preview,dismissed:false}};
        await page.clock.fastForward(4500);
        await expect(page.getByRole('button',{name:'Ricarica l’applicazione',exact:true})).toBeVisible({timeout:30000});
        await page.locator('summary').filter({hasText:'Backup di sicurezza'}).click();
        await page.getByRole('button',{name:'Carica backup precedenti',exact:true}).click();
        await expect(page.getByRole('button',{name:'Scarica backup di sicurezza',exact:true})).toHaveCount(13);
        await expect(page.getByRole('button',{name:'Carica backup precedenti',exact:true})).toHaveCount(0);
        await page.getByRole('button',{name:'Scarica backup di sicurezza',exact:true}).last().click();
        await expect.poll(()=>downloads).toBe(1);
        assert.equal(downloadedPath, '/api/instance/admin/data-restore/old-backup-11/backup');
        await page.screenshot({path:path.join(output,`${viewport.width}-completed.png`),fullPage:true});
        await page.evaluate(() => {
            localStorage.setItem('userData', JSON.stringify({user_id:'owner-fixture',sport_association:{denomination:'Old ASD'}}));
            localStorage.setItem('selectedGroup', 'old-group');
            localStorage.setItem('sessionToken', 'retained-token');
            localStorage.setItem('refreshToken', 'retained-refresh');
        });
        await page.getByRole('button',{name:'Ricarica l’applicazione',exact:true}).click();
        await expect(page.getByRole('alert')).toContainText('Profilo non disponibile');
        assert.equal(profileFetches, 1);
        assert.equal(await page.evaluate(() => JSON.parse(localStorage.userData).sport_association.denomination), 'Old ASD');
        failProfile = false;
        await Promise.all([
            page.waitForNavigation({waitUntil:'domcontentloaded'}),
            page.getByRole('button',{name:'Ricarica l’applicazione',exact:true}).click(),
        ]);
        assert.equal(profileFetches, 2);
        assert.deepEqual(await page.evaluate(() => ({
            data:JSON.parse(localStorage.userData), group:localStorage.getItem('selectedGroup'),
            token:localStorage.getItem('sessionToken'), refresh:localStorage.getItem('refreshToken'),
        })), {data:{user_id:'owner-fixture',sport_association:{denomination:'Restored ASD',tax_code:'RESTORED'}},
            group:null,token:'retained-token',refresh:'retained-refresh'});
        await page.goto('http://127.0.0.1:5199/?non-owner');
        await expect(page.getByRole('button',{name:'Ripristina backup',exact:true})).toHaveCount(0);
        await expect(page.getByRole('button',{name:'Avvia Export',exact:true})).toBeVisible();
        assert.deepEqual(errors, []);
        await context.close();
    }
    await fs.writeFile(path.join(output,'report.json'),JSON.stringify({passed:true,checks:['desktop','mobile','export preserved','validation error','typed confirmation','missing media consent','reload','connection failure','resume','failed restore','completion','backup pagination and download','profile refresh before reload','profile refresh failure retry','authentication retained','owner visibility']},null,2));
    console.log('PASS: desktop/mobile data restore review, confirmation, status reload, error recovery, completion and download.');
} finally {
    await browser?.close();
    await server.close();
}
