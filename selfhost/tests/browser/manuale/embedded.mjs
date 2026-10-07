import {initializeActorSession} from './actor-session.mjs';
import {chromium, expect as baseExpect} from '@playwright/test';
import manualConfig from '../playwright.manual.config.mjs';
import fs from 'node:fs';
import path from 'node:path';
import {readerPlan} from './reader-plan.mjs';

const run = process.env.ASSOZETA_MANUAL_RUN;
const expect = baseExpect.configure({timeout: 45000});
if (!run) throw new Error('ASSOZETA_MANUAL_RUN is required');
const state = JSON.parse(fs.readFileSync(path.join(run, 'run.json')));
const input = JSON.parse(fs.readFileSync(path.join(run, 'browser-input.json')));
const report = {status: 'running', backend: 'real', application_revision: state.application_input.revision, checks: []};
const corpus = JSON.parse(fs.readFileSync(path.join(run, 'index.json')));
const plan = readerPlan(corpus.chunks);
const publicSections = plan.sections;
const browser = await chromium.launch();
const context = await browser.newContext(manualConfig.use);
const appPage = await context.newPage();
let page = appPage;
page.setDefaultTimeout(45000);
const errors = [];
function markdownParts(content) {
    return (content || []).flatMap(part => part.kind === 'markdown' ? [part.markdown]
        : [...markdownParts(part.content), ...(part.cards || []).flatMap(card => markdownParts(card.content))]);
}
const proseWords = text => text.replace(/\[([^\]]*)\]\([^)]*\)/g, '$1')
    .replace(/^\s*(?:[-+*]|\d+[.)])\s+/gm, '')
    .replace(/&quot;/g, '"').replace(/&#39;|&apos;/g, "'").replace(/&amp;/g, '&')
    .replace(/[\p{P}\p{S}\s]+/gu, ' ').trim();
page.on('pageerror', error => errors.push(error.message));
try {
    const health = await context.request.get(input.origin + '/api/healthz');
    expect(health.ok(), 'Fixture health endpoint HTTP ' + health.status()).toBeTruthy();
    expect((await health.json()).manual_capture).toEqual({run_id: state.run_id,
        application_revision: state.application_input.revision, reference_time: input.reference_date + 'T12:00:00+00:00'});
    const headers = {Authorization: `Bearer ${input.token}`};
    const profile = await context.request.get(input.origin + '/api/profile/info', {headers});
    expect(profile.ok(), 'Authenticated profile HTTP ' + profile.status() + ': ' + (profile.ok() ? '' : await profile.text())).toBeTruthy();
    const profileData = await profile.json();
    expect(profileData.user_data.user_id).toBe(input.user_id);
    const billing = await context.request.get(input.origin + '/api/billing/active-plan', {headers});
    expect(billing.ok(), 'Billing endpoint HTTP ' + billing.status() + ': ' + (billing.ok() ? '' : await billing.text())).toBeTruthy();
    const billingData = (await billing.json()).data;
    await context.addInitScript(initializeActorSession, {identity: input, input, captureMode: true});
    await page.goto(input.origin + '/#/');
    await expect(page.getByText('Organizzazione', {exact: true})).toBeVisible({timeout: 60000});
    const releaseNotice = page.getByRole('button', {name: /Ok, grazie/});
    if (await releaseNotice.isVisible()) await releaseNotice.click();
    const appState = await appPage.evaluate(() => ({currentPage: localStorage.getItem('currentPage'),
        subPage: localStorage.getItem('subPage'), sidebarCollapsed: localStorage.getItem('sidebarCollapsed'), sidebarClass: document.querySelector('#bkn_aside')?.className}));
    const link = page.locator('#manuale_assozeta a');
    await expect(link).toHaveAttribute('href', '/#/manuale');
    await expect(link).toHaveAttribute('target', '_blank');
    await expect(link).toHaveAttribute('rel', /noopener/);
    [page] = await Promise.all([appPage.waitForEvent('popup'), link.click()]);
    page.setDefaultTimeout(45000);
    page.on('pageerror', error => errors.push(error.message));
    await expect(appPage).toHaveURL(input.origin + '/#/');
    await expect(page).toHaveURL(input.origin + '/#/manuale');
    await expect(page.getByRole('heading', {name: 'Come possiamo aiutarti?', exact: true})).toBeVisible();
    await expect(page.locator('#bkn_header, #bkn_header_mobile, #bkn_aside')).toHaveCount(0);
    expect(await appPage.evaluate(() => ({currentPage: localStorage.getItem('currentPage'),
        subPage: localStorage.getItem('subPage'), sidebarCollapsed: localStorage.getItem('sidebarCollapsed'), sidebarClass: document.querySelector('#bkn_aside')?.className}))).toEqual(appState);
    const navigation = page.getByRole('navigation', {name: 'Sezioni del manuale'});
    const publicPages = [...new Set(publicSections.map(section => section.page))];
    await expect(navigation.getByRole('link')).toHaveCount(publicPages.length);
    let imageReferences = 0;
    async function select(section) {
        await navigation.locator(`a[href="/#/manuale?page=${encodeURIComponent(section.page)}"]`).click();
        const article = page.getByRole('article', {name: section.page_title, exact: true});
        await expect(article).toBeVisible();
        await article.getByRole('link', {name: 'Apri sezione: ' + section.title, exact: true}).click();
        return article.locator('.chapter-section').filter({has: page.getByRole('heading', {name: section.title, exact: true, level: 2})});
    }
    for (const section of publicSections) {
        const article = await select(section);
        await expect(article.getByRole('heading', {name: section.title, exact: true, level: 2})).toBeVisible();
        const blocks = section.reader || [{title: '', text: section.text, screenshots: section.screenshots.map(image => image.path)}];
        const renderedBlocks = article.locator('.manual-step');
        await expect(renderedBlocks).toHaveCount(blocks.length);
        for (const [index, block] of blocks.entries()) {
            if (block.title) await expect(article).toContainText(block.title);
            const renderedBlock = renderedBlocks.nth(index);
            const expectedProse = block.content ? markdownParts(block.content) : [block.markdown || block.text].filter(Boolean);
            const renderedProse = await renderedBlock.locator('.manual-markdown').allInnerTexts();
            expect(renderedProse.map(proseWords), section.id + ': every prose fragment in order')
                .toEqual(expectedProse.map(proseWords));
            await expect(renderedBlock.locator('img')).toHaveCount(block.screenshots.length, {timeout: 45000});
            if (block.markdown?.includes('**')) expect(await renderedBlock.locator('strong').count()).toBeGreaterThan(0);
        }
        const count = blocks.reduce((total, block) => total + block.screenshots.length, 0);
        await expect(article.locator('img')).toHaveCount(count, {timeout: 45000});
        await expect.poll(() => article.locator('img').evaluateAll(images => images.every(image => image.complete && image.naturalWidth > 0)), {timeout: 45000}).toBeTruthy();
        expect(await article.locator('img').evaluateAll(images => images.every(image => {
            const box = image.getBoundingClientRect();
            return box.width <= 640 && box.height <= 420;
        })), 'Images should stay comfortably sized in the reader').toBe(true);
        if (count) {
            await article.getByRole('button', {name: /^Ingrandisci:/}).first().click();
            const viewer = article.getByRole('dialog');
            await expect(viewer).toBeVisible();
            await expect(viewer.locator('img')).toBeVisible();
            await page.keyboard.press('Escape');
            await expect(viewer).not.toBeVisible();
        }
        imageReferences += count;
    }
    await select(plan.section);
    await page.screenshot({path: path.join(run, 'embedded-manual.png'), fullPage: false, animations: 'disabled'});
    const deepLink = page.url();
    await page.reload();
    await expect(page.getByRole('article', {name: plan.section.page_title, exact: true})).toBeVisible();
    await expect(page).toHaveURL(deepLink);
    const search = page.getByRole('combobox', {name: 'Cerca nel manuale'});
    await search.fill(plan.query);
    await page.getByRole('button', {name: 'Cerca', exact: true}).click();
    const searchHit = page.locator('.search-result').filter({has: page.getByRole('heading', {name: plan.section.title, exact: true})});
    await expect(searchHit).toBeVisible();
    await expect(navigation.getByRole('link')).toHaveCount(publicPages.length);
    await searchHit.click();
    await expect(page.getByRole('article', {name: plan.section.page_title, exact: true})).toBeVisible();
    await expect(page.locator('.chapter-section')).toHaveCount(publicSections.filter(section => section.page === plan.section.page).length);
    await search.fill(plan.unsupportedQuery);
    await page.getByRole('button', {name: 'Cerca', exact: true}).click();
    await expect(page.getByRole('status')).toHaveText('Non ho trovato istruzioni verificate per questa domanda.');
    await page.getByRole('button', {name: 'Tutte le guide'}).first().click();
    await expect(navigation.getByRole('link')).toHaveCount(publicPages.length);
    await page.setViewportSize({width: 390, height: 844});
    const toggle = page.getByRole('button', {name: 'Sfoglia il manuale'});
    await expect(toggle).toBeVisible();
    await toggle.click();
    await expect(navigation).toBeVisible();
    await select(plan.section);
    await expect(navigation).not.toBeVisible();
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth),
        'Mobile reader should not overflow horizontally').toBe(true);
    await page.setViewportSize(manualConfig.use.viewport);
    if (publicSections.some(section => section.id === 'docs/corsi#creare-un-corso')) {
        await select(publicSections.find(section => section.id === 'docs/corsi#creare-un-corso'));
        await expect(page.locator('.chapter-section').filter({has: page.getByRole('heading', {name: 'Creare un corso', exact: true})})).toContainText('Un collaboratore con accesso in sola lettura');
    }
    if (publicSections.some(section => section.id === 'faq/come-archiviare-dati#cosa-viene-archiviato')) {
        await select(publicSections.find(section => section.id === 'faq/come-archiviare-dati#cosa-viene-archiviato'));
        await expect(page.locator('.chapter-section').filter({has: page.getByRole('heading', {name: 'Cosa viene archiviato', exact: true})}))
            .toContainText('rimane archiviato finché non lo ripristini separatamente');
    }
    const headerHelp = appPage.locator('#bkn_header').getByRole('link', {name: "Manuale d'uso", exact: true});
    await expect(headerHelp).toHaveAttribute('target', '_blank');
    await expect(headerHelp).toHaveAttribute('rel', /noopener/);
    const [headerReader] = await Promise.all([appPage.waitForEvent('popup'), headerHelp.click()]);
    headerReader.on('pageerror', error => errors.push(error.message));
    await expect(headerReader).toHaveURL(input.origin + '/#/manuale');
    await expect(headerReader.getByRole('heading', {name: 'Come possiamo aiutarti?', exact: true})).toBeVisible();
    await expect(headerReader.locator('#bkn_header, #bkn_header_mobile, #bkn_aside')).toHaveCount(0);
    await expect(appPage).toHaveURL(input.origin + '/#/');
    await headerReader.close();
    expect(context.pages()).toHaveLength(2);
    // Exercise the visible chat and real socket with the fixture's unconfigured AI.
    await appPage.getByRole('button', {name: 'Assistenza manuale', exact: true}).click();
    const chat = appPage.locator('.agent-panel');
    await expect(chat).toContainText('Posso cercare istruzioni verificate');
    const message = chat.locator('textarea').first();
    await expect(message).toBeEnabled();
    await message.fill(plan.query);
    await message.press('Enter');
    await expect(chat.locator('.manual-answer')).toBeVisible({timeout: 45000});
    await expect(chat).toContainText(plan.section.title);
    await expect(message).toBeEnabled();
    await message.fill(plan.unsupportedQuery);
    await message.press('Enter');
    await expect(chat).toContainText('Non ho trovato istruzioni verificate');
    report.manual_only_chat = {transport: 'real-websocket', provider: 'unconfigured',
        supported_section: plan.section.id, unsupported_question_abstained: true};

    expect(errors).toEqual([]);
    report.sections = publicSections.length;
    report.image_references = imageReferences;
    report.search_case = {query: plan.query, section: plan.section.id};
    report.checks = ['sidebar opens the internal standalone reader in a new tab', 'all compatible public sections load',
        'every indexed reader block and referenced screenshot renders', 'section deep link survives reload',
        'search uses verified retrieval and abstains for unsupported instructions', 'header opens a standalone reader tab and preserves the application',
        'images have bounded reading size and an accessible enlargement dialog', 'mobile navigation has no horizontal overflow',
        'no browser errors; only the requested manual tabs open; application chrome stays hidden'];
    report.status = 'passed';
} catch (error) {
    report.status = 'failed'; report.error = error.message; process.exitCode = 1;
} finally {
    await browser.close();
    fs.writeFileSync(path.join(run, 'embedded-manual.json'), JSON.stringify(report, null, 2) + '\n');
}
console.log('Embedded manual verification: ' + report.status);
