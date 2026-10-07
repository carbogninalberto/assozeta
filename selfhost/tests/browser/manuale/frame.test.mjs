// Browser geometry regression only: unit images are never manual evidence.
import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import {chromium} from '@playwright/test';
import {captureFrame, pngDimensions} from './frame.mjs';
import {initializeActorSession} from './actor-session.mjs';

test('focused capture measures the complete dialog after its opening animation', async () => {
    const run = fs.mkdtempSync(path.join(os.tmpdir(), 'manual-frame-unit-'));
    const browser = await chromium.launch();
    try {
        const page = await browser.newPage({viewport: {width: 1920, height: 1080}, deviceScaleFactor: 1});
        await page.setContent(`<style>
            body{margin:0}#dialog{position:absolute;left:400px;top:200px;width:400px;height:300px;background:#fff;border:0;padding:0}
            #dialog.entering{animation:enter 300ms linear forwards}
            @keyframes enter{from{transform:scale(.5)}to{transform:scale(1)}}
            footer{position:absolute;bottom:0;height:30px;width:400px;background:#253145;color:#fff}
        </style><div id="dialog"><h2>Pubblica il bilancio</h2><footer>Puoi annullare la pubblicazione</footer></div>`);
        await page.locator('#dialog').evaluate(el => el.classList.add('entering'));
        const capture = await captureFrame({page, run, relative:'images/unit-dialog.png',checkpoint:'unit-final-dialog',locator:page.locator('#dialog')});
        assert.deepEqual(capture.clip,{x:400,y:200,width:400,height:300});
        assert.deepEqual(pngDimensions(fs.readFileSync(path.join(run,'captures',capture.path))),{width:400,height:300});
        assert.deepEqual({width:capture.master.width,height:capture.master.height},{width:1920,height:1080});
        assert.equal(await page.locator('footer').isVisible(),true);
        assert.equal(capture.stability.scope,'same-state-consecutive-captures');
        assert.deepEqual(fs.readFileSync(path.join(run,capture.stability.path)),fs.readFileSync(path.join(run,capture.master.path)));
    } finally { await browser.close(); fs.rmSync(run,{recursive:true,force:true}); }
});

test('capture waits for dependent real browser requests before taking the frame', async () => {
    const run=fs.mkdtempSync(path.join(os.tmpdir(),'manual-frame-loading-unit-'));
    const browser=await chromium.launch();
    try {
        const page=await browser.newPage({viewport:{width:1920,height:1080},deviceScaleFactor:1});
        await page.addInitScript(initializeActorSession, {identity: {token:'unit-only',refresh_token:'unit-only'},
            input:{reference_date:'2026-09-30'}});
        await page.route('https://manual.test/**',async route=>{
            if(route.request().url().endsWith('/totals')) {
                await new Promise(resolve=>setTimeout(resolve,400));
                return route.fulfill({contentType:'application/json',body:'{"total":25}'});
            }
            return route.fulfill({contentType:'text/html',body:'<main>Caricamento...</main><script>fetch("/totals").then(r=>r.json()).then(d=>document.querySelector("main").textContent="Totale: "+d.total)</script>'});
        });
        await page.goto('https://manual.test/');
        await captureFrame({page,run,relative:'images/unit-loaded.png',checkpoint:'loaded'});
        assert.equal(await page.locator('main').textContent(),'Totale: 25');
        // Subsequent checkpoints use actual request completion, including
        // dependent reads after a same-document navigation.
        await page.evaluate(() => {
            history.pushState({}, '', '#second');
            document.querySelector('main').textContent = 'Caricamento...';
            fetch('/totals').then(r => r.json()).then(d => document.querySelector('main').textContent = 'Totale: ' + d.total);
        });
        await captureFrame({page,run,relative:'images/unit-reloaded.png',checkpoint:'reloaded'});
        assert.equal(await page.locator('main').textContent(),'Totale: 25');
        await page.evaluate(() => {
            const controller = new AbortController();
            fetch('/totals', {signal: controller.signal}).catch(() => {});
            controller.abort();
            const xhr = new XMLHttpRequest();
            xhr.open('GET', '/totals'); xhr.send(); xhr.abort();
        });
        await captureFrame({page,run,relative:'images/unit-aborted.png',checkpoint:'aborted'});
        assert.equal(await page.evaluate(() => window.__manualeRequestActivity.pending), 0);
    } finally {await browser.close();fs.rmSync(run,{recursive:true,force:true});}
});

test('editable focus is preserved unless a completed-form capture explicitly releases it', async () => {
    const run=fs.mkdtempSync(path.join(os.tmpdir(),'manual-frame-focus-unit-'));
    const browser=await chromium.launch();
    try {
        const page=await browser.newPage({viewport:{width:1920,height:1080},deviceScaleFactor:1});
        await page.setContent('<label>Descrizione<textarea>Descrizione compilata</textarea></label>');
        await page.locator('textarea').focus();
        await captureFrame({page,run,relative:'images/unit-focused.png',checkpoint:'focused'});
        assert.equal(await page.locator('textarea').evaluate(el=>el===document.activeElement),true);
        await captureFrame({page,run,relative:'images/unit-completed.png',checkpoint:'completed',blurEditableFocus:true});
        assert.equal(await page.locator('textarea').evaluate(el=>el===document.activeElement),false);
        assert.equal(await page.locator('textarea').inputValue(),'Descrizione compilata');
    } finally {await browser.close();fs.rmSync(run,{recursive:true,force:true});}
});
