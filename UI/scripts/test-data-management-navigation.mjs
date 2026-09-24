// UI-only fixture: no request can reach a real API or import data.
import assert from 'node:assert/strict';
import path from 'node:path';
import {fileURLToPath, pathToFileURL} from 'node:url';
import {createServer} from 'vite';
import {svelte} from '@sveltejs/vite-plugin-svelte';
const ui = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
const {chromium, expect} = await import(pathToFileURL(path.join(ui, '../selfhost/tests/browser/node_modules/@playwright/test/index.mjs')));
const server = await createServer({root:ui, configFile:false, logLevel:'error',
    server:{host:'127.0.0.1',port:5201,strictPort:true},
    resolve:{alias:{utils:`${ui}/src/utils`,store:`${ui}/src/store`}},
    plugins:[{name:'navigation-fixture', enforce:'pre',
        resolveId(id) {
            const stub = ['ApiMiddleware.js','instanceStore.js','stores.js','NotificationService.js','exportProgressStore.js'].find(name => id.endsWith('/'+name));
            if (stub) return '\0'+stub;
            if (id === 'utils/Permissions' || id.endsWith('/Permissions') || id.endsWith('/Permissions.js')) return '\0permissions';
            if (id === '/fixture.js') return '\0entry';
        },
        load(id) {
            if (id === '\0ApiMiddleware.js') return `export async function apiFetch(url,options) {const r=await fetch(url,options);return {error:!r.ok,status:r.status,response:await r.json()};}`;
            if (id === '\0instanceStore.js') return `export const getApiHost=()=>'/api'; export const clearInstanceCache=()=>{};`;
            if (id === '\0stores.js') return `import {writable} from 'svelte/store';const make=value=>Object.assign(writable(value),{useLocalStorage(){}});export const sessionToken=make('test'),subPage=make('data-management'),userData=make({});`;
            if (id === '\0NotificationService.js') return `export default {syncActiveExport:async()=>{},subscribeRestoreProgress:()=>()=>{}};`;
            if (id === '\0exportProgressStore.js') return `import {writable} from 'svelte/store';export const exportProgress=Object.assign(writable({active:false}),{applySnapshot(){}});`;
            if (id === '\0permissions') return `export const canPerformAction=()=>true;`;
            if (id === '\0entry') return `import DataManagement from '/src/routes/profile/sections/DataManagement.svelte';window.__bakney={env:{API:{ASSOCIATION:{EXPORT:{LIST:'/fixture/exports',START:'/fixture/start'}},DOCUMENT:{RETRIEVE:'/fixture/file'}}}};new DataManagement({target:document.getElementById('app'),props:{instanceOwner:!location.search.includes('non-owner')}});`;
        },
        configureServer(vite) {vite.middlewares.use(async (req,res,next)=>{
            if (req.url.split('?')[0] !== '/') return next();
            res.setHeader('Content-Type','text/html');res.end(await vite.transformIndexHtml('/', '<html><body><div id="app"></div><script type="module" src="/fixture.js"></script></body></html>'));
        });},
    },svelte({configFile:false})]});
let browser;
try {
    await server.listen();
    browser=await chromium.launch({headless:true});
    const page=await browser.newPage();
    await page.clock.install();
    const errors=[];
    page.on('pageerror', e=>{errors.push(e.message);console.error(e.message);});
    const pending=[];
    let requests=0;
    await page.route('**/fixture/exports', route=>{requests++;pending.push(route);});
    await page.route('**/api/**', route=>route.fulfill({json:{available:true,max_upload_bytes:5*1024**3,active:null,history:[],backups:[],backups_next:null}}));
    const nextRequest=async()=>{await expect.poll(()=>pending.length).toBeGreaterThan(0);return pending.shift();};
    await page.goto('http://127.0.0.1:5201/#/profile?page=data-management&tab=export');
    await expect(page.getByText('Caricamento degli export…',{exact:true})).toBeVisible();
    await expect(page.getByText('Nessun export disponibile',{exact:true})).toHaveCount(0);
    await expect(page.getByRole('button',{name:'Avvia Export',exact:true})).toBeDisabled();
    await (await nextRequest()).fulfill({status:503,json:{error:'maintenance'}});
    await expect(page.getByRole('alert')).toContainText('manutenzione');
    await page.getByRole('button',{name:'Riprova caricamento export'}).click();
    await (await nextRequest()).abort('failed');
    await expect(page.getByRole('alert')).toContainText('Connessione non disponibile');
    await page.getByRole('button',{name:'Riprova caricamento export'}).click();
    const timedOut = await nextRequest();
    await page.clock.fastForward(31000);
    await expect(page.getByRole('alert')).toContainText('impiegando troppo tempo');
    await timedOut.abort().catch(()=>{});
    await page.getByRole('button',{name:'Riprova caricamento export'}).click();
    await (await nextRequest()).fulfill({json:{exports:[]}});
    await expect(page.getByText('Nessun export disponibile',{exact:true})).toBeVisible();
    await expect(page.getByRole('button',{name:'Avvia Export',exact:true})).toBeEnabled();
    await page.getByRole('button',{name:'Ripristina backup',exact:true}).click();
    await expect(page).toHaveURL(/page=data-management&tab=restore$/);
    const count=requests;
    await page.reload();
    await expect(page.getByRole('button',{name:'Ripristina backup',exact:true})).toHaveAttribute('aria-pressed','true');
    await expect(page.getByRole('heading',{name:'Ripristina da backup Bakney',exact:true})).toBeVisible();
    assert.equal(requests,count,'restore landing must not load the hidden export list');
    await page.getByRole('button',{name:'Esporta dati',exact:true}).click();
    await (await nextRequest()).fulfill({json:{exports:[]}});
    await page.goBack();
    await expect(page.getByRole('button',{name:'Ripristina backup',exact:true})).toHaveAttribute('aria-pressed','true');
    await page.goForward();
    await expect(page.getByRole('button',{name:'Esporta dati',exact:true})).toHaveAttribute('aria-pressed','true');
    await page.goto('http://127.0.0.1:5201/?non-owner#/profile?page=data-management&tab=restore');
    await (await nextRequest()).fulfill({json:{exports:[]}});
    await expect(page).toHaveURL(/page=data-management&tab=export$/);
    await expect(page.getByRole('button',{name:'Ripristina backup',exact:true})).toHaveCount(0);
    assert.deepEqual(errors,[]);
    console.log('PASS: loading, inline maintenance/network errors, retry, restore refresh, back/forward and non-owner fallback.');
} finally {await browser?.close();await server.close();}
