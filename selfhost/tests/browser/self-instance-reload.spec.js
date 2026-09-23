import {test, expect} from '@playwright/test';

test.beforeEach(async ({page, request, context}) => {
    await request.post('/api/fixture/reset');
    await context.addCookies([{name: 'fixture-cookie', value: 'keep-session', url: 'http://127.0.0.1:5194'}]);
    await page.goto('/healthz');
    await page.evaluate(() => {
        localStorage.setItem('sessionToken', JSON.stringify('fixture-session'));
        localStorage.setItem('userData', JSON.stringify({dark_mode: true}));
        localStorage.setItem('unrelated-app', 'preserve');
    });
});

test('full reload retrieves deployed assets and preserves route, session and preferences', async ({page, request, context}, testInfo) => {
    const errors = [];
    page.on('pageerror', error => errors.push(error.message));
    const response = await page.goto('/?keep=1#/profile/self-instance');
    expect(response.headers()['cache-control']).toBe('no-cache');
    expect((await request.get('/static/css/app-bundle.css')).headers()['cache-control']).toBe('no-cache');
    expect((await request.get('/global.css')).headers()['cache-control']).toBe('no-cache');
    await expect(page.getByLabel('Versione frontend')).toHaveText('Frontend 1');
    const reload = page.getByRole('button', {name: 'Ricarica applicazione', exact: true});
    await expect(reload).toBeEnabled();
    await expect.poll(async () => (await (await request.get('/api/fixture/state')).json()).connections).toBeGreaterThan(0);
    const before = await (await request.get('/api/fixture/state')).json();
    const originalScript = await page.locator('script[type="module"]').getAttribute('src');
    await page.evaluate(() => {
        window.oldDocumentSentinel = true;
        localStorage.setItem('assozeta_instance_config', '{"stale":true}');
        localStorage.setItem('assozeta_config_timestamp', '1');
    });
    await request.post('/api/fixture/state', {data: {version: 2}});
    await Promise.all([page.waitForEvent('load'), reload.click()]);
    await expect(page.getByLabel('Versione frontend')).toHaveText('Frontend 2');
    await expect(page.locator('.overview-tile').getByText('Associazione Aurora', {exact: true})).toBeVisible();
    expect(await page.locator('script[type="module"]').getAttribute('src')).not.toBe(originalScript);
    expect(page.url()).toBe('http://127.0.0.1:5194/?keep=1#/profile/self-instance');
    expect(await page.evaluate(() => window.oldDocumentSentinel)).toBeUndefined();
    expect(await page.evaluate(() => ({session: localStorage.getItem('sessionToken'), user: localStorage.getItem('userData'), other: localStorage.getItem('unrelated-app'), cacheAtStartup: window.cachedConfigAtStartup})))
        .toEqual({session: '"fixture-session"', user: '{"dark_mode":true}', other: 'preserve', cacheAtStartup: null});
    expect(await page.evaluate(() => JSON.parse(localStorage.getItem('assozeta_instance_config')).oem.name)).toBe('Associazione Aurora');
    expect((await context.cookies()).find(cookie => cookie.name === 'fixture-cookie').value).toBe('keep-session');
    expect((await (await request.get('/api/fixture/state')).json()).writes).toBe(0);
    await expect.poll(async () => (await (await request.get('/api/fixture/state')).json()).connections).toBeGreaterThan(before.connections);
    expect((await (await request.get('/api/fixture/state')).json()).reads).toBeGreaterThan(before.reads);
    await page.screenshot({path: testInfo.outputPath('self-instance-reload-desktop.png'), fullPage: true});
    expect(errors).toEqual([]);
});

test('unsaved edits and an in-flight save block reload', async ({page}) => {
    await page.goto('/#/profile/self-instance');
    const reload = page.getByRole('button', {name: 'Ricarica applicazione', exact: true});
    await expect(reload).toBeEnabled();
    await page.getByRole('group', {name: 'Sezioni Self Instance'}).getByRole('button', {name: 'Identità e logo', exact: true}).click();
    await page.getByLabel('Nome', {exact: true}).fill('Nuovo nome');
    await expect(reload).toBeDisabled();
    await expect(page.getByText('Salva o annulla le modifiche prima di ricaricare.', {exact: false})).toBeVisible();
    await page.getByLabel('Nome', {exact: true}).fill('Associazione Aurora');
    await expect(reload).toBeEnabled();
    await page.getByLabel('Nome', {exact: true}).fill('Nome salvato');
    await page.getByRole('button', {name: 'Salva impostazioni', exact: true}).click();
    await expect(reload).toBeDisabled();
    await expect(reload).toBeEnabled();
    await Promise.all([page.waitForEvent('load'), reload.click()]);
    await expect(page.locator('.overview-tile').getByText('Nome salvato', {exact: true})).toBeVisible();
});

for (const stage of ['backup', 'completed', 'recovery_required']) {
    test(`reload restores durable ${stage} state without creating an update`, async ({page, request}) => {
        await request.post('/api/fixture/state', {data: {stage}});
        await page.goto('/#/profile/self-instance');
        const reload = page.getByRole('button', {name: 'Ricarica applicazione', exact: true});
        await expect(reload).toBeEnabled();
        await Promise.all([page.waitForEvent('load'), reload.click()]);
        await expect(reload).toBeEnabled();
        await page.getByRole('group', {name: 'Sezioni Self Instance'}).getByRole('button', {name: 'Aggiornamenti e backup', exact: true}).click();
        const status = page.getByRole('region', {name: 'Stato aggiornamenti'});
        await expect(status).toContainText(stage === 'backup' ? 'Backup dei dati' : stage === 'completed' ? 'Aggiornamento completato' : 'Ripristino necessario');
        if (stage !== 'backup') {
            await status.locator('.instance-accordion > summary').click();
            await status.getByText('Dettagli operazione').click();
            await expect(status).toContainText('fcc58c2a-436c-4c95-aa38-ea9b4ef6b135');
        }
        const state = await (await request.get('/api/fixture/state')).json();
        expect(state.writes).toBe(0);
        expect((stage === 'backup' ? state.status.active : state.status.history[0]).id).toBe('fcc58c2a-436c-4c95-aa38-ea9b4ef6b135');
    });
}

test('reload reconnects to independent status while the application API is unavailable', async ({page, request}) => {
    await request.post('/api/fixture/state', {data: {stage: 'backup', unavailable: true}});
    await page.goto('/#/profile/self-instance');
    const status = page.getByRole('region', {name: 'Stato aggiornamenti'});
    await expect(status).toContainText('Backup dei dati');
    const reload = page.getByRole('button', {name: 'Ricarica applicazione', exact: true});
    await expect(reload).toBeEnabled();
    await Promise.all([page.waitForEvent('load'), reload.click()]);
    await expect(status).toContainText('Backup dei dati');
    expect((await (await request.get('/api/fixture/state')).json()).writes).toBe(0);
});

test('reload action fits a mobile viewport', async ({page}, testInfo) => {
    await page.setViewportSize({width: 390, height: 844});
    await page.goto('/#/profile/self-instance');
    await expect(page.getByRole('button', {name: 'Ricarica applicazione', exact: true})).toBeEnabled();
    await expect(page.getByRole('button', {name: 'Apri diagnostica', exact: true})).toBeEnabled();
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1)).toBeTruthy();
    await page.screenshot({path: testInfo.outputPath('self-instance-reload-mobile.png'), fullPage: true});
});
