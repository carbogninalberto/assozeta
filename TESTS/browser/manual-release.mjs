// A real installed release, real login and real manual-only WebSocket chat.
import assert from 'node:assert/strict';
import fs from 'node:fs/promises';
import path from 'node:path';
import {chromium, expect} from '../../selfhost/tests/browser/node_modules/@playwright/test/index.mjs';

const origin = process.env.TESTS_ORIGIN;
const fixture = JSON.parse(await fs.readFile(process.env.TESTS_FIXTURE, 'utf8'));
const output = process.env.TESTS_OUTPUT;
await fs.mkdir(output, {recursive: true});
const browser = await chromium.launch();
const context = await browser.newContext({viewport: {width: 1440, height: 1000}});
const errors = [];
let page;
try {
    // Prove that no external manual/AI/CDN request is needed for the reader.
    await context.route('**/*', route => new URL(route.request().url()).origin === origin
        ? route.continue() : route.abort());
    // Older installations may retain the former branding opt-out. A bundled
    // manual must remain discoverable without editing that saved setting.
    await context.route('**/api/instance/config', async route => {
        const response = await route.fetch();
        const config = await response.json();
        config.oem.displaySettings.sidebar.showManual = false;
        await route.fulfill({response, json: config});
    });
    page = await context.newPage();
    page.on('pageerror', error => errors.push(error.message));
    await page.goto(origin + '/#/login');
    await page.getByPlaceholder('Inserisci email / username').fill(fixture.accounts.missing.username);
    await page.getByPlaceholder('Inserisci la password...').fill(fixture.accounts.missing.password);
    await page.getByRole('button', {name: 'Accedi', exact: true}).click();
    await expect(page.locator('#bkn_aside')).toBeVisible({timeout: 60000});
    const notice = page.getByRole('button', {name: /Ok, grazie/});
    if (await notice.isVisible()) await notice.click();
    const help = page.locator('#bkn_header').getByRole('link', {name: "Manuale d'uso", exact: true});
    await expect(page.locator('#manuale_assozeta').getByRole('link', {name: "Manuale d'uso", exact: true})).toBeVisible();
    const [reader] = await Promise.all([page.waitForEvent('popup'), help.click()]);
    reader.on('pageerror', error => errors.push(error.message));
    await expect(reader.getByRole('heading', {name: 'Come possiamo aiutarti?', exact: true})).toBeVisible();
    const [sidebarReader] = await Promise.all([page.waitForEvent('popup'),
        page.locator('#manuale_assozeta').getByRole('link', {name: "Manuale d'uso", exact: true}).click()]);
    await expect(sidebarReader.getByRole('heading', {name: 'Come possiamo aiutarti?', exact: true})).toBeVisible();
    await sidebarReader.close();
    await reader.getByRole('combobox', {name: 'Cerca nel manuale'}).fill(fixture.manual.query);
    await reader.getByRole('button', {name: 'Cerca', exact: true}).click();
    const hit = reader.locator('.search-result').filter({has: reader.getByRole('heading', {name: fixture.manual.section_title, exact: true})});
    await expect(hit).toBeVisible();
    await hit.click();
    await expect(reader.getByRole('heading', {name: fixture.manual.section_title, exact: true, level: 2})).toBeVisible();
    await reader.goto(origin + '/#/manuale?section=' + encodeURIComponent(fixture.manual.section_id));
    await expect(reader.getByRole('heading', {name: fixture.manual.section_title, exact: true, level: 2})).toBeVisible();
    await expect.poll(() => reader.locator('article img').evaluateAll(images =>
        images.length > 0 && images.every(image => image.complete && image.naturalWidth > 0)), {timeout: 45000}).toBe(true);
    await reader.screenshot({path: path.join(output, 'reader.png'), fullPage: true});
    await reader.reload();
    await expect(reader.getByRole('heading', {name: fixture.manual.section_title, exact: true, level: 2})).toBeVisible();
    await page.getByRole('button', {name: 'Assistenza manuale', exact: true}).click();
    const chat = page.locator('.agent-panel');
    await expect(chat).toContainText('Posso cercare istruzioni verificate');
    const message = chat.locator('textarea').first();
    await expect(message).toBeEnabled();
    await message.fill(fixture.manual.query);
    await message.press('Enter');
    await expect(chat.locator('.manual-answer')).toBeVisible({timeout: 45000});
    await expect(chat).toContainText(fixture.manual.section_title);
    await expect(message).toBeEnabled();
    await message.fill('Manuale teletrasporto satellitare');
    await message.press('Enter');
    await expect(chat).toContainText('Non ho trovato istruzioni verificate');
    assert.deepEqual(errors, []);
    await fs.writeFile(path.join(output, 'results.json'), JSON.stringify({status: 'passed',
        realLogin: true, externalNetworkBlocked: true, legacyManualSettingIgnored: true, reader: true, screenshots: true,
        headerOpensReader: true, sidebarOpensReader: true, search: true,
        deepLinkReload: true, manualOnlyWebSocketChat: true, unsupportedAbstained: true}));
} catch (error) {
    await page?.screenshot({path: path.join(output, 'failure.png'), fullPage: true}).catch(() => {});
    throw error;
} finally {
    await browser.close();
}
