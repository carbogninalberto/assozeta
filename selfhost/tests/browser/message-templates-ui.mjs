// Run against a disposable shared installation with synthetic recipient data.
import assert from 'node:assert/strict';
import fs from 'node:fs/promises';
import path from 'node:path';
import {chromium, expect as playwrightExpect} from '@playwright/test';

const {MESSAGE_TEST_ORIGIN: origin, MESSAGE_TEST_USER: username, MESSAGE_TEST_PASSWORD: password,
    MESSAGE_TEST_OUTPUT: output, MESSAGE_TEST_RECIPIENT_ID: recipientId,
    MESSAGE_TEST_COLLABORATOR: collaborator} = process.env;
assert.ok(origin && username && password && recipientId, 'Set the disposable instance and owner fixture environment.');
if (output) await fs.mkdir(output, {recursive: true});

const settingsPath = '/communications/settings/message-templates';
const recipientPath = `/communications/outgoing/recipients/${recipientId}`;
const browser = await chromium.launch({headless: true});
const expect = playwrightExpect.configure({timeout: 15000});
const roles = {django: 'v1', go: 'v2'};
const sessions = {};

async function login(backend) {
    const context = await browser.newContext({viewport: {width: 1280, height: 950}});
    // Keep all requests local and prevent the WhatsApp link from contacting a real recipient.
    await context.route('**/*', route => new URL(route.request().url()).origin === origin ? route.continue() : route.abort());
    const page = await context.newPage();
    await page.goto(`${origin}/?api=${roles[backend]}#/login`);
    await page.locator('input[name="username_login"]').fill(username);
    await page.locator('input[name="password_login"]').fill(password);
    const response = page.waitForResponse(result => new URL(result.url()).pathname.endsWith('/oauth2/login'));
    await page.locator('#bkn_login_signin_submit').click();
    assert.equal((await response).status(), 200);
    await expect.poll(() => page.evaluate(() => Boolean(localStorage.getItem('sessionToken')))).toBe(true);
    await page.waitForURL(url => !url.hash.startsWith('#/login'));
    const token = await page.evaluate(() => JSON.parse(localStorage.getItem('sessionToken')));
    const prefix = backend === 'go' ? '/api/v2' : '/api';
    const request = (method, endpoint, data, headers) => context.request.fetch(`${origin}${prefix}${endpoint}`, {
        method, data, headers: {Authorization: `Bearer ${token}`, ...headers},
    });
    return {page, request, prefix};
}

async function settings(page) {
    await page.goto(`${origin}/#/communication/configuration?page=templates`);
    await expect(page.getByRole('heading', {name: 'Modelli di messaggio'})).toBeVisible();
    await expect(page.getByRole('row', {name: /Messaggio di saluto.*WhatsApp/})).toBeVisible();
}

async function createTemplate(page, name, channel, body) {
    await page.getByRole('button', {name: 'Nuovo modello'}).click();
    await page.getByLabel('Nome modello').fill(name);
    await page.getByLabel('Canale', {exact: true}).selectOption(channel);
    await page.getByLabel('Lingua del modello').fill('en-gb');
    if (channel === 'email') await page.getByLabel('Oggetto').fill('Invito per {{customer_name}}');
    await page.getByLabel('Testo del modello').fill(body);
    await page.getByText('Predefinito per questo canale e lingua').click();
}

async function openComposer(page) {
    await page.goto(`${origin}/#/personas/list`);
    await page.getByText('Rossi Giulia', {exact: true}).click();
    await page.getByRole('button', {name: 'Invia messaggio'}).click();
    await expect(page.getByRole('dialog', {name: 'Nuovo messaggio da modello'})).toBeVisible();
}

async function startPreview(page, channel) {
    await page.getByRole('button', {name: 'Invia messaggio'}).click();
    const dialog = page.getByRole('dialog', {name: 'Nuovo messaggio da modello'});
    await dialog.getByLabel('Canale', {exact: true}).selectOption(channel);
    const sentPreview = page.waitForResponse(res => res.request().method() === 'POST' && res.url().endsWith(recipientPath));
    await dialog.getByRole('button', {name: 'Mostra anteprima'}).click();
    const response = await sentPreview;
    assert.equal(response.status(), 201);
    return {preview: (await response.json()).data, dialog: page.getByRole('dialog', {name: 'Anteprima del messaggio'})};
}

try {
    for (const backend of Object.keys(roles)) sessions[backend] = await login(backend);
    const django = sessions.django;
    const go = sessions.go;

    await settings(django.page);
    await createTemplate(django.page, 'Invito allenamento', 'email', '{{bad}}');
    await django.page.getByRole('button', {name: 'Mostra esempio'}).click();
    await expect(django.page.getByRole('alert')).toContainText('Variabile sconosciuta');
    await django.page.getByLabel('Testo del modello').fill('Ciao {{customer_name}}, ti aspettiamo il {{date}} alle {{time}}. {{link}}');
    await django.page.getByRole('button', {name: 'Mostra esempio'}).click();
    await expect(django.page.getByText(/Ciao Giulia Rossi, ti aspettiamo il 2026-12-15/)).toBeVisible();
    if (output) await django.page.screenshot({path: path.join(output, 'message-template-settings.png'), animations: 'disabled'});
    await django.page.getByRole('button', {name: 'Salva modello'}).click();
    await expect(django.page.getByRole('row', {name: /Invito allenamento/})).toBeVisible();

    await settings(go.page);
    await go.page.getByRole('button', {name: 'Modifica Invito allenamento Email'}).click();
    await go.page.getByLabel('Nome modello').fill('Invito prova');
    await go.page.getByRole('button', {name: 'Salva modello'}).click();
    await expect(go.page.getByRole('row', {name: /Invito prova/})).toBeVisible();
    await createTemplate(go.page, 'Promemoria WhatsApp', 'whatsapp', 'Promemoria per {{customer_name}}.');
    await go.page.getByRole('button', {name: 'Mostra esempio'}).click();
    await expect(go.page.getByText('Promemoria per Giulia Rossi.')).toBeVisible();
    await go.page.getByRole('button', {name: 'Salva modello'}).click();
    await expect(go.page.getByRole('row', {name: /Promemoria WhatsApp/})).toBeVisible();

    for (const [backend, session] of Object.entries(sessions)) {
        const {page, request, prefix} = session;
        const wrongGroup = {'X-Group-Id': '60067283-205b-420f-8217-bae63d98be37'};
        assert.equal((await request('GET', recipientPath, undefined, wrongGroup)).status(), 404);
        await openComposer(page);
        const composer = page.getByRole('dialog', {name: 'Nuovo messaggio da modello'});
        await composer.getByLabel('Lingua del messaggio').selectOption('en-gb');
        await composer.getByLabel('Data', {exact: true}).fill('2026-12-03');
        await composer.getByLabel('Ora', {exact: true}).fill('18:15');
        await composer.getByLabel('Link', {exact: true}).fill('https://example.org/prova');
        const previewResponse = page.waitForResponse(res => res.request().method() === 'POST' && res.url().endsWith(recipientPath));
        await composer.getByRole('button', {name: 'Mostra anteprima'}).click();
        const response = await previewResponse;
        assert.equal(new URL(response.url()).pathname, prefix + recipientPath);
        assert.equal(response.status(), 201);
        const {id} = (await response.json()).data;
        const dialog = page.getByRole('dialog', {name: 'Anteprima del messaggio'});
        await expect(dialog.getByText('Giulia Rossi · giulia@example.org')).toBeVisible();
        await expect(dialog.getByLabel('Oggetto')).toHaveValue('Invito per Giulia Rossi');
        await expect(dialog.getByLabel('Testo del messaggio')).toHaveValue('Ciao Giulia Rossi, ti aspettiamo il 2026-12-03 alle 18:15. https://example.org/prova');
        const edit = {action: 'send', subject: 'Invito aggiornato', body: `  Conferma ${backend} & grazie!\nA presto.  `};
        await dialog.getByLabel('Oggetto').fill(edit.subject);
        await dialog.getByLabel('Testo del messaggio').fill(edit.body);
        if (output && backend === 'django') {
            await expect(page.getByText('Modello salvato.', {exact: true})).not.toBeVisible();
            await page.screenshot({path: path.join(output, 'editable-message-preview.png'), animations: 'disabled'});
        }
        await dialog.getByRole('button', {name: 'Invia ora'}).click();
        await expect(dialog.getByRole('status')).toHaveText('Messaggio affidato al servizio di invio.');
        const cross = sessions[backend === 'django' ? 'go' : 'django'];
        const replay = await cross.request('POST', `/communications/outgoing/previews/${id}`, edit);
        assert.equal(replay.status(), 200);
        assert.equal((await replay.json()).data.body, edit.body);
        assert.equal((await request('POST', `/communications/outgoing/previews/${id}`, {...edit, body: 'different'})).status(), 409);
        await dialog.getByRole('button', {name: 'Chiudi', exact: true}).first().click();

        const whatsapp = await startPreview(page, 'whatsapp');
        await whatsapp.dialog.getByLabel('Testo del messaggio').fill('Ciao & grazie! ☀');
        await whatsapp.dialog.getByRole('button', {name: 'Prepara WhatsApp'}).click();
        await expect(whatsapp.dialog.getByRole('status')).toContainText('Invio automatico saltato');
        await expect(whatsapp.dialog.getByRole('link', {name: 'Apri in WhatsApp'})).toHaveAttribute(
            'href', 'https://wa.me/393331234567?text=Ciao%20%26%20grazie%21%20%E2%98%80'
        );
        if (output && backend === 'go') await page.screenshot({path: path.join(output, 'whatsapp-fallback.png'), animations: 'disabled'});
        await whatsapp.dialog.getByRole('button', {name: 'Chiudi', exact: true}).first().click();

        const discarded = await startPreview(page, 'email');
        await discarded.dialog.getByRole('button', {name: 'Scarta anteprima'}).click();
        await expect(discarded.dialog.getByRole('status')).toContainText('L’invio automatico è stato annullato');
        assert.equal((await request('POST', `/communications/outgoing/previews/${discarded.preview.id}`, edit)).status(), 409);
        await discarded.dialog.getByRole('button', {name: 'Fine', exact: true}).click();
        await page.getByRole('dialog', {name: 'Giulia Rossi'}).locator('.drawer-header button').click();
        console.log(`${backend}: settings, editable email, WhatsApp fallback and discard passed`);
    }

    const created = await go.request('POST', recipientPath, {channel: 'sms', locale: 'it'});
    assert.equal(created.status(), 201);
    const sms = (await created.json()).data;
    const skipped = await django.request('POST', `/communications/outgoing/previews/${sms.id}`, {
        action: 'send', subject: '', body: sms.body,
    });
    assert.equal(skipped.status(), 200);
    assert.equal((await skipped.json()).data.status, 'skipped');

    const email = (await (await django.request('POST', recipientPath, {channel: 'email', locale: 'it'})).json()).data;
    const action = {action: 'send', subject: 'Invio concorrente', body: 'Il recapito riceve una sola copia.'};
    const attemptPath = `/communications/outgoing/previews/${email.id}`;
    const concurrent = await Promise.all([go.request('POST', attemptPath, action), django.request('POST', attemptPath, action)]);
    assert.deepEqual(concurrent.map(response => response.status()), [200, 200]);
    assert.equal((await (await go.request('POST', attemptPath, action)).json()).data.status, 'submitted');

    if (collaborator) {
        for (const {page, prefix} of Object.values(sessions)) {
            const response = await page.request.post(`${origin}${prefix}/oauth2/login`, {
                data: {username: collaborator, password},
            });
            assert.equal(response.status(), 200);
            const {access_token: token} = await response.json();
            const headers = {Authorization: `Bearer ${token}`};
            assert.equal((await page.request.get(`${origin}${prefix}${settingsPath}`, {headers})).status(), 200);
            assert.equal((await page.request.post(`${origin}${prefix}${settingsPath}`, {headers, data: {}})).status(), 403);
            assert.equal((await page.request.get(`${origin}${prefix}${recipientPath}`, {headers})).status(), 403);
            assert.equal((await page.request.post(`${origin}${prefix}${attemptPath}`, {headers, data: action})).status(), 403);
        }
    }
    await settings(go.page);
    const whatsappTemplate = go.page.getByRole('row', {name: /Promemoria WhatsApp/});
    await whatsappTemplate.getByRole('button', {name: /Modifica/}).click();
    await go.page.getByText('Attivo', {exact: true}).click();
    await go.page.getByRole('button', {name: 'Salva modello'}).click();
    await expect(whatsappTemplate).toContainText('Disattivato');
    const djangoOptions = await (await django.request('GET', recipientPath)).json();
    assert.ok(djangoOptions.data.templates.every(template => template.name !== 'Promemoria WhatsApp'));
    await whatsappTemplate.getByRole('button', {name: /Modifica/}).click();
    await go.page.getByRole('button', {name: 'Elimina modello'}).click();
    await go.page.getByRole('button', {name: 'Elimina', exact: true}).click();
    await expect(whatsappTemplate).toHaveCount(0);
    const djangoTemplates = await (await django.request('GET', settingsPath)).json();
    assert.ok(djangoTemplates.data.every(template => template.name !== 'Promemoria WhatsApp'));
    console.log('Cross-backend preview writes and action replays passed');
} finally {
    await browser.close();
}
