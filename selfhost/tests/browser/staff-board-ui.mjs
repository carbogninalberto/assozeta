// Isolated component regression checks: fixture requests never reach a deployed installation.
// Run: node selfhost/tests/browser/staff-board-ui.mjs
import assert from 'node:assert/strict';
import fs from 'node:fs/promises';
import path from 'node:path';
import {fileURLToPath, pathToFileURL} from 'node:url';
import {chromium, expect} from '@playwright/test';
const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '../../..');
const ui = path.join(root, 'UI');
const {createServer} = await import(pathToFileURL(path.join(ui, 'node_modules/vite/dist/node/index.js')));
const {svelte} = await import(pathToFileURL(path.join(ui, 'node_modules/@sveltejs/vite-plugin-svelte/src/index.js')));
const output = process.env.ASSOZETA_BROWSER_OUTPUT || path.join(root, 'quality-reports/staff-board-ui');
await fs.mkdir(output, {recursive: true});
const server = await createServer({
    root: ui, configFile: false, logLevel: 'error', server: {host: '127.0.0.1', port: 5199, strictPort: true},
    resolve: {alias: {utils: `${ui}/src/utils`, components: `${ui}/src/components`, store: `${ui}/src/store`}},
    plugins: [{name: 'staff-board-fixture', enforce: 'pre',
        resolveId(id) {
            if (/\/ApiMiddleware(?:\.js)?$/.test(id)) return '\0fixture-api';
            if (/\/Permissions(?:\.js)?$/.test(id)) return '\0fixture-permissions';
            if (id === '/staff-board-entry.js') return '\0fixture-entry';
        },
        load(id) {
            if (id === '\0fixture-api') return `export async function apiFetch(url, options) { const r = await fetch(url, options); return {error: !r.ok, response: await r.json()}; } export const replaceUID = (url, id) => url.replace('<uid>', id);`;
            if (id === '\0fixture-permissions') return `export const canPerformAction = action => { const role = new URLSearchParams(location.search).get('role'); return role !== 'none' && (role !== 'read' || action.endsWith('.read')); };`;
            if (id === '\0fixture-entry') return `
                import Board from '/src/routes/association/communication/staffboard/StaffBoard.svelte';
                import Widget from '/src/components/widgets/StaffBoard.svelte';
                import ReferenceWidget from '/src/components/widgets/BaseNumberWidget.svelte';
                import {Toaster} from 'svelte-sonner';
                import {writable} from 'svelte/store';
                import notificationService from '/src/utils/NotificationService.js';
                import Swal from 'sweetalert2';
                window.swal = Swal;
                window.__bakney = {env: {DOMAIN: location.origin, WS: {NOTIFICATIONS: '/ws/notifications/'}, API: {ASSOCIATION: {EXPORT: {ACTIVE: '/fixture/export'}}, COMMUNICATIONS: {STAFF_BOARD: {LIST: '/fixture/list', ADD: '/fixture/add', UPDATE: '/fixture/<uid>/update', DELETE: '/fixture/<uid>/delete'}}}}};
                new Board({target: document.getElementById('board')});
                new Widget({target: document.getElementById('widget')});
                new ReferenceWidget({target: document.getElementById('reference-widget'), props: {title: 'Associati', value: 128, size: 12, valueSuffix: ''}});
                new Toaster({target: document.body});
                notificationService.init(writable([]), writable(0), 'fixture');`;
        },
        configureServer(vite) {
            vite.middlewares.use(async (req, res, next) => {
                if (req.url.split('?')[0] !== '/') return next();
                // Use the exact neighboring Messages header markup for typography/spacing comparison.
                const messages = await fs.readFile(path.join(ui, 'src/routes/association/communication/messages/Messages.svelte'), 'utf8');
                const header = messages.slice(messages.indexOf('<div class="card-title">'), messages.indexOf('<div class="card-toolbar">'));
                const html = await vite.transformIndexHtml('/', `<!doctype html><html lang="it"><head><meta name="viewport" content="width=device-width,initial-scale=1"><link rel="stylesheet" href="/static/css/bootstrap.min.css"><link rel="stylesheet" href="/static/css/app-bundle.css"><link rel="stylesheet" href="/global.css"><link rel="stylesheet" href="/dark-mode.css"></head><body><main style="width:100%;max-width:1100px;min-width:0;margin:24px auto;padding:12px"><section id="reference" class="container"><div class="card card-custom"><div class="card-header flex-wrap border-0 p-0">${header}</div></div></section><section id="board"></section><div class="row"><section id="widget" class="col-12 col-md-4"></section><section id="reference-widget" class="col-12 col-md-4"></section></div></main><script type="module" src="/staff-board-entry.js"></script></body></html>`);
                res.setHeader('Content-Type', 'text/html'); res.end(html);
            });
        },
    }, svelte({configFile: false})],
});
let browser;
const reports = [];
const imageBytes = await fs.readFile(path.join(ui, 'public/static/placeholder_logo.png'));
const avatarBytes = await fs.readFile(path.join(ui, 'public/static/assets/media/users/blank.png'));
const initialMessages = () => Array.from({length: 7}, (_, i) => ({
    staff_board_message_id: String(i + 1), author: i === 0 ? 'me' : 'other', is_owner: i === 0,
    author_name: i === 0 ? 'Collaboratore '.repeat(8).trim() : `Autore ${i + 1}`,
    author_avatar: i === 0 ? 'https://example.test/avatar.png' : null,
    content: i === 0 ? `Avviso per lo staff\n${'TestoMoltoLungo'.repeat(50)}\nA & B <script>alert(1)</script>` : `Aggiornamento ${i + 1}: ricordati di confermare la presenza alla riunione.`,
    document: null, pinned: i === 0, created_at: '2026-09-21T10:30:00Z',
}));
function plainText(doc) {
    if (doc?.type === 'text') return doc.text;
    return (doc?.content || []).map(plainText).join(doc.type === 'doc' ? '\n' : '');
}
try {
    await server.listen();
    browser = await chromium.launch({headless: true});
    for (const width of [1440, 390]) {
        for (const theme of ['light', 'dark']) {
            const context = await browser.newContext({viewport: {width, height: 1000}, hasTouch: true});
            const errors = [], sockets = [];
            let messages = initialMessages(), failRead = false, failWrite = false, malformed = false;
            const writes = [];
            let reads = 0, releaseWrite;
            await context.route('https://example.test/avatar.png', route => route.fulfill({contentType: 'image/png', body: avatarBytes}));
            await context.routeWebSocket('**/ws/notifications/**', socket => {
                sockets.push(socket);
                socket.onMessage(message => {
                    const data = JSON.parse(message);
                    if (data.type === 'ping') socket.send(JSON.stringify({type: 'pong', timestamp: data.timestamp}));
                    if (data.type === 'fetch') socket.send(JSON.stringify({type: 'notifications', data: [], unread: 0}));
                });
            });
            const broadcast = () => sockets.forEach(socket => socket.send(JSON.stringify({type: 'staff_board_changed'})));
            await context.route('**/fixture/**', async route => {
                const request = route.request(), method = request.method(), url = new URL(request.url());
                if (url.pathname === '/fixture/export') return route.fulfill({json: {active: false}});
                if (method === 'GET') {
                    reads++;
                    return route.fulfill({status: failRead ? 503 : 200, json: failRead ? {error: 'Unavailable'} : malformed ? {data: {invalid: true}} : {data: messages}});
                }
                const body = request.postDataJSON();
                if (body?.document) assert.ok(body.content, 'Rich posts must also send the legacy content field');
                writes.push({method, path: url.pathname, body});
                if (releaseWrite) await releaseWrite;
                if (failWrite) return route.fulfill({status: 500, json: {error: 'Unavailable'}});
                const id = url.pathname.split('/')[2];
                if (method === 'POST') messages = [{staff_board_message_id: `new-${writes.length}`, author: 'me', is_owner: true, author_name: 'Nuovo autore', pinned: false, created_at: '2026-09-21T12:00:00Z', ...body, content: plainText(body.document)}, ...messages];
                if (method === 'PATCH') messages = messages.map(m => m.staff_board_message_id === id ? {...m, ...body, ...(body.document ? {content: plainText(body.document)} : {})} : m);
                if (method === 'DELETE') messages = messages.filter(m => m.staff_board_message_id !== id);
                await route.fulfill({json: {data: messages}});
                broadcast();
            });
            const page = await context.newPage();
            page.on('pageerror', e => errors.push(e.message));
            async function load(role = 'all') {
                await page.goto(`http://127.0.0.1:5199/?role=${role}`);
                await page.locator('#board h3').waitFor();
                await page.evaluate(theme => document.documentElement.dataset.theme = theme, theme);
            }
            const board = page.locator('#board'), widget = page.locator('#widget');
            await load();
            await expect(board.locator('article')).toHaveCount(7);
            await expect(widget.locator('article')).toHaveCount(5);
            await expect(board.getByRole('button', {name: 'Modifica', exact: true})).toHaveCount(1);
            await expect(board.getByRole('button', {name: 'Elimina', exact: true})).toHaveCount(1);
            await expect(board.locator('.author-avatar img')).toHaveCount(1);
            await expect(board.locator('.author-avatar').nth(1)).toHaveText('A2');
            assert.equal(await board.locator('script').count(), 0);
            await page.screenshot({path: path.join(output, `${width}-${theme}-populated.png`), fullPage: true, animations: 'disabled'});
            const metrics = await page.evaluate(() => ({
                overflow: document.documentElement.scrollWidth > innerWidth,
                widgetHeight: document.querySelector('.staff-board-widget').getBoundingClientRect().height,
                heading: getComputedStyle(document.querySelector('#board h3')).fontSize,
                referenceHeading: getComputedStyle(document.querySelector('#reference h3')).fontSize,
            }));
            assert.equal(metrics.overflow, false);
            assert.equal(metrics.widgetHeight, 230);
            assert.equal(metrics.heading, metrics.referenceHeading);
            // New messages live in a modal, with the actual shared Tiptap editor and its image picker.
            await expect(board.locator('[contenteditable]')).toHaveCount(0);
            await board.getByRole('button', {name: 'Nuovo messaggio', exact: true}).click();
            let modal = page.getByRole('dialog', {name: 'Nuovo messaggio', exact: true});
            await expect(modal).toBeVisible();
            await modal.evaluate(el => Promise.all(el.getAnimations({subtree: true}).map(animation => animation.finished.catch(() => {}))));
            await modal.getByRole('button', {name: 'Pubblica', exact: true}).click();
            await expect(modal.getByRole('alert')).toContainText('Scrivi un messaggio o aggiungi');
            await expect(page.locator('[data-sonner-toast]').filter({hasText: 'Scrivi un messaggio o aggiungi'})).toBeVisible();
            const editor = modal.getByRole('textbox', {name: 'Messaggio per lo staff'});
            await modal.getByRole('button', {name: 'Grassetto', exact: true}).click();
            await page.evaluate(() => new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve))));
            await editor.pressSequentially('Aggiornamento con foto', {delay: 20});
            await expect(editor).toContainText('Aggiornamento con foto');
            await modal.getByRole('button', {name: 'Grassetto', exact: true}).click();
            await editor.press('ControlOrMeta+End');
            await editor.press('Enter');
            await expect(editor).toContainText('Aggiornamento con foto');
            const chooserPromise = page.waitForEvent('filechooser');
            await modal.getByRole('button', {name: 'Aggiungi immagine', exact: true}).click();
            await (await chooserPromise).setFiles({name: 'staff.png', mimeType: 'image/png', buffer: imageBytes});
            await expect(modal.locator('.ProseMirror img[src]')).toHaveCount(1);
            await expect(editor).toContainText('Aggiornamento con foto');
            await page.screenshot({path: path.join(output, `${width}-${theme}-composer.png`), fullPage: true, animations: 'disabled'});
            assert.equal(await modal.evaluate(el => el.scrollWidth > el.clientWidth), false);
            // A remote update refreshes board and widget without replacing the open draft.
            messages = [...messages, {staff_board_message_id: 'remote', author_name: 'Collega', is_owner: false, content: 'Novità in tempo reale', pinned: false, created_at: '2026-09-21T12:00:00Z'}];
            broadcast();
            await expect(board.locator('article')).toHaveCount(8);
            await expect(editor).toContainText('Aggiornamento con foto');
            failWrite = true;
            await modal.getByRole('button', {name: 'Pubblica', exact: true}).click();
            await expect(modal.getByRole('alert')).toBeVisible();
            await expect(editor).toContainText('Aggiornamento con foto');
            failWrite = false;
            let finishWrite;
            releaseWrite = new Promise(resolve => { finishWrite = resolve; });
            const beforePublish = writes.length;
            await modal.getByRole('button', {name: 'Pubblica', exact: true}).click();
            await expect(modal.getByRole('button', {name: 'Salvataggio in corso...'})).toBeDisabled();
            await expect.poll(() => writes.length).toBe(beforePublish + 1);
            await page.keyboard.press('Enter');
            assert.equal(writes.length, beforePublish + 1);
            finishWrite(); releaseWrite = null;
            await expect(modal).toHaveCount(0);
            await expect(board.locator('article')).toHaveCount(9);
            await expect(widget.locator('article')).toHaveCount(5);
            await expect(board.locator('.message-content strong').first()).toHaveText('Aggiornamento con foto');
            await expect(board.locator('.message-content img')).toHaveCount(1);
            await expect(widget.locator('article').first()).toContainText('Aggiornamento con foto');
            await page.screenshot({path: path.join(output, `${width}-${theme}-rich-post.png`), fullPage: true, animations: 'disabled'});
            // Edit the document in the same modal and preserve its attachment.
            await board.getByRole('button', {name: 'Modifica', exact: true}).first().click();
            modal = page.getByRole('dialog', {name: 'Modifica messaggio', exact: true});
            const edit = modal.getByRole('textbox', {name: 'Messaggio per lo staff'});
            await expect(edit.locator('img[src]')).toHaveCount(1);
            await modal.evaluate(el => Promise.all(el.getAnimations({subtree: true}).map(animation => animation.finished.catch(() => {}))));
            await edit.click();
            await edit.press('ControlOrMeta+End');
            await edit.press('Enter');
            await edit.pressSequentially('Nota aggiunta', {delay: 20});
            await expect(edit).toContainText('Nota aggiunta');
            await modal.getByRole('button', {name: 'Salva', exact: true}).click();
            await expect(modal).toHaveCount(0);
            await expect(board.locator('article').first()).toContainText('Nota aggiunta');
            await expect(widget.locator('article').first()).toContainText('Nota aggiunta');
            // Pinning and deletion both broadcast invalidations; other authors have no edit/delete controls.
            const beforePin = writes.length;
            await board.getByRole('button', {name: 'Togli dai messaggi in evidenza', exact: true}).tap();
            await expect(board.getByRole('button', {name: 'Togli dai messaggi in evidenza', exact: true})).toHaveCount(0);
            assert.equal(writes.length, beforePin + 1);
            await board.getByRole('button', {name: 'Elimina', exact: true}).first().click();
            await page.getByRole('dialog').getByRole('button', {name: 'Elimina', exact: true}).click();
            await expect(board.locator('article')).toHaveCount(8);
            await expect(widget.locator('article').first()).not.toContainText('Nota aggiunta');
            // Remote deletion and reconnect invalidation refresh both surfaces.
            messages = messages.filter(m => m.staff_board_message_id !== '1');
            broadcast();
            await expect(board.locator('article')).toHaveCount(7);
            await expect(board.getByRole('button', {name: 'Modifica', exact: true})).toHaveCount(0);
            await expect(widget.locator('.author-avatar img')).toHaveCount(0);
            messages = [];
            broadcast();
            await expect(board.getByText('Uno spazio per il tuo staff')).toBeVisible();
            await expect(widget.getByText('Il tuo team, sempre aggiornato')).toBeVisible();
            await page.screenshot({path: path.join(output, `${width}-${theme}-empty.png`), fullPage: true, animations: 'disabled'});
            await board.getByRole('button', {name: 'Scrivi il primo messaggio'}).click();
            modal = page.getByRole('dialog', {name: 'Nuovo messaggio', exact: true});
            await modal.evaluate(el => Promise.all(el.getAnimations({subtree: true}).map(animation => animation.finished.catch(() => {}))));
            const invalidChooser = page.waitForEvent('filechooser');
            await modal.getByRole('button', {name: 'Aggiungi immagine', exact: true}).click();
            await (await invalidChooser).setFiles({name: 'invalid.svg', mimeType: 'image/svg+xml', buffer: Buffer.from('<svg></svg>')});
            await expect(modal.getByRole('alert')).toContainText('Scegli un’immagine PNG');
            await expect(page.locator('[data-sonner-toast]').filter({hasText: 'Scegli un’immagine PNG'})).toBeVisible();
            const imageOnlyChooser = page.waitForEvent('filechooser');
            await modal.getByRole('button', {name: 'Aggiungi immagine', exact: true}).click();
            await (await imageOnlyChooser).setFiles({name: 'image-only.png', mimeType: 'image/png', buffer: imageBytes});
            await expect(modal.locator('.ProseMirror img[src]')).toHaveCount(1);
            await modal.getByRole('button', {name: 'Pubblica', exact: true}).click();
            await expect(modal).toHaveCount(0);
            assert.equal(writes.at(-1).body.content, 'Immagine allegata');
            await expect(board.locator('.message-content img')).toHaveCount(1);
            await expect(widget.getByText('Immagine allegata')).toBeVisible();
            // Reconnection fetches an update missed while the socket was unavailable.
            messages = [...messages, {staff_board_message_id: 'offline', author_name: 'Collega', is_owner: false, content: 'Arrivato durante la disconnessione', created_at: '2026-09-21T12:00:00Z'}];
            await sockets.at(-1).close({code: 1012, reason: 'fixture restart'});
            await expect(board.getByText('Arrivato durante la disconnessione')).toBeVisible();
            await expect(widget.getByText('Arrivato durante la disconnessione')).toBeVisible();
            failRead = true;
            broadcast();
            await expect(board.getByRole('alert')).toBeVisible();
            await expect(widget.getByRole('alert')).toBeVisible();
            failRead = false;
            messages = initialMessages();
            await board.getByRole('button', {name: 'Riprova'}).click();
            await widget.getByRole('button', {name: 'Riprova'}).click();
            await expect(board.locator('article')).toHaveCount(7);
            await expect(widget.locator('article')).toHaveCount(5);
            malformed = true;
            broadcast();
            await expect(board.getByText('Uno spazio per il tuo staff')).toBeVisible();
            malformed = false;
            await load('read');
            await expect(board.locator('article')).toHaveCount(7);
            await expect(board.locator('button')).toHaveCount(0);
            const readsBeforeDenied = reads;
            await load('none');
            await expect(board.getByText('Permessi insufficienti')).toBeVisible();
            await expect(widget.getByText('Permessi insufficienti')).toBeVisible();
            assert.equal(reads, readsBeforeDenied);
            assert.deepEqual(errors, []);
            reports.push({width, theme, metrics, writes: writes.length, errors});
            await context.close();
        }
    }
    await fs.writeFile(path.join(output, 'results.json'), JSON.stringify(reports, null, 2));
    console.log(JSON.stringify(reports, null, 2));
} finally {
    await browser?.close();
    await server.close();
}
