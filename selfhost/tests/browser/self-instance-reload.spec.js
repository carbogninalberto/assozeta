import {test, expect} from '@playwright/test';

// Restart lives in the header "Azioni istanza" menu; a disabled item keeps its reason.
const restartItem = async page => {
    const menu = page.getByRole('button', {name: 'Azioni istanza'});
    if (await menu.getAttribute('aria-expanded') !== 'true') await menu.click();
    return page.getByRole('menuitem', {name: 'Riavvia applicazione'});
};
const expectRestart = async (page, enabled) => {
    await expect.poll(async () => { const item = await restartItem(page); const value = await item.getAttribute('aria-disabled') !== 'true'; await page.keyboard.press('Escape'); return value; }).toBe(enabled);
};
const confirmButton = page => page.getByRole('dialog', {name: 'Riavviare l’applicazione?'}).getByRole('button', {name: 'Riavvia', exact: true});
const settings = async (page, label) => { await page.getByRole('tablist', {name: 'Sezioni istanza'}).getByRole('tab', {name: 'Impostazioni'}).click(); await page.getByRole('tablist', {name: 'Impostazioni'}).getByRole('tab', {name: label}).click(); };

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
    await expectRestart(page, true);
    await expect.poll(async () => (await (await request.get('/api/fixture/state')).json()).connections).toBeGreaterThan(0);
    const before = await (await request.get('/api/fixture/state')).json();
    const originalScript = await page.locator('script[type="module"]').getAttribute('src');
    await page.evaluate(() => {
        window.oldDocumentSentinel = true;
        localStorage.setItem('assozeta_instance_config', '{"stale":true}');
        localStorage.setItem('assozeta_config_timestamp', '1');
    });
    await request.post('/api/fixture/state', {data: {version: 2}});
    await (await restartItem(page)).click();
    await expect(page.getByRole('dialog')).toContainText('circa un minuto');
    await expect(page.getByRole('dialog')).toHaveCSS('opacity', '1');
    await page.screenshot({path: testInfo.outputPath('self-instance-restart-confirmation-desktop.png'), animations: 'disabled'});
    await Promise.all([page.waitForEvent('load'), confirmButton(page).click()]);
    await expect(page.getByLabel('Versione frontend')).toHaveText('Frontend 2');
    await expect(page.locator('.instance-name')).toHaveText('Associazione Aurora');
    expect(await page.locator('script[type="module"]').getAttribute('src')).not.toBe(originalScript);
    expect(page.url()).toBe('http://127.0.0.1:5194/?keep=1#/profile/self-instance');
    expect(await page.evaluate(() => window.oldDocumentSentinel)).toBeUndefined();
    expect(await page.evaluate(() => ({session: localStorage.getItem('sessionToken'), user: localStorage.getItem('userData'), other: localStorage.getItem('unrelated-app'), cacheAtStartup: window.cachedConfigAtStartup})))
        .toEqual({session: '"fixture-session"', user: '{"dark_mode":true}', other: 'preserve', cacheAtStartup: null});
    expect(await page.evaluate(() => JSON.parse(localStorage.getItem('assozeta_instance_config')).oem.name)).toBe('Associazione Aurora');
    expect((await context.cookies()).find(cookie => cookie.name === 'fixture-cookie').value).toBe('keep-session');
    expect((await (await request.get('/api/fixture/state')).json()).writes).toBe(1);
    await expect.poll(async () => (await (await request.get('/api/fixture/state')).json()).connections).toBeGreaterThan(before.connections);
    expect((await (await request.get('/api/fixture/state')).json()).reads).toBeGreaterThan(before.reads);
    await page.screenshot({path: testInfo.outputPath('self-instance-reload-desktop.png'), fullPage: true});
    expect(errors).toEqual([]);
});

test('unsaved edits and an in-flight save block reload', async ({page}) => {
    await page.goto('/#/profile/self-instance');
    await expectRestart(page, true);
    await settings(page, 'Identità');
    await page.getByLabel('Nome', {exact: true}).fill('Nuovo nome');
    await expectRestart(page, false);
    await (await restartItem(page)).focus();
    await expect(page.getByText('Salva o annulla prima le modifiche.', {exact: true})).toBeVisible();
    await page.keyboard.press('Escape');
    await page.getByLabel('Nome', {exact: true}).fill('Associazione Aurora');
    await expectRestart(page, true);
    await page.getByLabel('Nome', {exact: true}).fill('Nome salvato');
    await page.getByRole('button', {name: 'Salva identità', exact: true}).click();
    await expect(page.getByRole('button', {name: 'Salva identità', exact: true})).toBeDisabled();
    await expectRestart(page, true);
    await (await restartItem(page)).click();
    await expect(page.getByRole('dialog')).toContainText('circa un minuto');
    await Promise.all([page.waitForEvent('load'), confirmButton(page).click()]);
    // Refresh preserves the selected tab; verify the saved form before opening
    // the overview instead of expecting navigation to reset implicitly.
    await expect(page).toHaveURL(/#\/profile\?page=self-instance&tab=branding$/);
    await expect(page.getByLabel('Nome', {exact: true})).toHaveValue('Nome salvato');
    await expect(page.locator('.instance-name')).toHaveText('Nome salvato');
});

test('cancelling confirmation performs no mutation or navigation', async ({page, request}) => {
    await page.goto('/#/profile/self-instance');
    await (await restartItem(page)).click();
    const dialog = page.getByRole('dialog', {name: 'Riavviare l’applicazione?'});
    await expect(dialog).toContainText('non sarà disponibile');
    await expect(dialog).toContainText('modifiche non salvate');
    await expect(dialog).toContainText('Dati e sessione restano');
    await dialog.getByRole('button', {name: 'Annulla', exact: true}).click();
    await expect(dialog).not.toBeVisible();
    expect((await (await request.get('/api/fixture/state')).json()).writes).toBe(0);
    await expect(page.getByLabel('Versione frontend')).toHaveText('Frontend 1');
});

for (const stage of ['backup', 'recovery_required']) {
    test(`restart is blocked during ${stage}`, async ({page, request}) => {
        await request.post('/api/fixture/state', {data: {stage}});
        await page.goto('/#/profile/self-instance');
        await expect(page.getByRole('heading', {name: 'Istanza', exact: true})).toBeVisible();
        await expectRestart(page, false);
        expect((await (await request.get('/api/fixture/state')).json()).writes).toBe(0);
    });
}

test('API downtime prevents a new restart', async ({page, request}) => {
    await request.post('/api/fixture/state', {data: {stage: 'backup', unavailable: true}});
    await page.goto('/#/profile/self-instance');
    await expect(page.getByRole('region', {name: 'Stato operazioni'})).toContainText('Backup dei dati');
    await expect(page.getByRole('button', {name: 'Azioni istanza'})).toHaveCount(0);
    expect((await (await request.get('/api/fixture/state')).json()).writes).toBe(0);
});

for (const restartBehavior of ['success', 'lost-response']) {
    test(`confirmed restart recovers through downtime and ${restartBehavior} without duplicates`, async ({page, request}) => {
        await request.post('/api/fixture/state', {data: {restartBehavior}});
        await page.goto('/#/profile/self-instance');
        await expectRestart(page, true);
        await (await restartItem(page)).click();
        const loaded = page.waitForEvent('load');
        await confirmButton(page).click();
        await expect(page.getByRole('region', {name: 'Stato operazioni'})).toContainText('Riavvio in corso');
        await expectRestart(page, false);
        await loaded;
        await expectRestart(page, true);
        const state = await (await request.get('/api/fixture/state')).json();
        expect(state.writes).toBe(1);
        expect(state.status.history[0].kind).toBe('restart');
        expect(state.status.history[0].status).toBe('succeeded');
    });
}

test('failed restart keeps the page open with actionable feedback', async ({page, request}) => {
    await request.post('/api/fixture/state', {data: {restartBehavior: 'failed'}});
    await page.goto('/#/profile/self-instance');
    await page.evaluate(() => {window.documentSentinel = true;});
    await (await restartItem(page)).click();
    await confirmButton(page).click();
    await expect(page.getByRole('alert').first()).toContainText('Verifica i container e i log del server.');
    expect(await page.evaluate(() => window.documentSentinel)).toBe(true);
    expect((await (await request.get('/api/fixture/state')).json()).writes).toBe(1);
});

test('timeout offers a status-only retry without another restart', async ({page, request}) => {
    await request.post('/api/fixture/state', {data: {restartBehavior: 'stall'}});
    await page.goto('/#/profile/self-instance');
    await page.clock.install();
    await (await restartItem(page)).click();
    await confirmButton(page).click();
    await expect(page.getByRole('region', {name: 'Stato operazioni'})).toContainText('Riavvio in corso');
    await page.clock.fastForward(601000);
    await expect(page.getByRole('alert').filter({hasText: 'Riavvio non confermato'})).toBeVisible();
    await page.getByRole('button', {name: 'Verifica di nuovo', exact: true}).click();
    await expectRestart(page, false);
    expect((await (await request.get('/api/fixture/state')).json()).writes).toBe(1);
});

test('reload action fits a mobile viewport', async ({page}, testInfo) => {
    await page.setViewportSize({width: 390, height: 844});
    await page.goto('/#/profile/self-instance');
    await expectRestart(page, true);
    await expect(page.getByRole('button', {name: /^Servizi/})).toBeEnabled();
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1)).toBeTruthy();
    await (await restartItem(page)).click();
    await expect(page.getByRole('dialog')).toBeVisible();
    await expect(page.getByRole('dialog')).toHaveCSS('opacity', '1');
    await page.screenshot({path: testInfo.outputPath('self-instance-reload-mobile.png'), animations: 'disabled'});
});
