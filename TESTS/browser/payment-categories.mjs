// Unchanged UI and real password authentication. Same-origin business responses
// are never intercepted; the adapter explicitly records Django coexistence.
import assert from 'node:assert/strict';
import fs from 'node:fs/promises';
import path from 'node:path';
import {pathToFileURL} from 'node:url';

const input = JSON.parse(await fs.readFile(process.env.TESTS_FIXTURE, 'utf8'));
const {chromium, expect} = await import(pathToFileURL(input.playwright_module).href);
const output = process.env.TESTS_OUTPUT;
await fs.mkdir(output, {recursive: true});
const browser = await chromium.launch();
const responses = [], pending = [], failures = [], checks = [], blockedExternal = [];
const selected = input.version === 'v1' ? 'django-v1' : 'go-v2';
const matches = (pathname, patterns) => patterns.some(pattern =>
    new RegExp('^/api/' + pattern.split('*').map(part => part.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')).join('[^/]+') + '$').test(pathname));
let phase = 'initialization', success = false, categoryID, initialVAT, paymentID;
const deadline = setTimeout(() => {failures.push({kind: 'workflow-deadline', phase}); void browser.close();}, 210000);
const record = async response => {
    const url = new URL(response.url());
    if (url.origin !== input.origin) return;
    const item = {method: response.request().method(), path: url.pathname, status: response.status(),
        backend: await response.headerValue('x-fixture-backend')};
    responses.push(item);
    if (matches(item.path, input.selected_routes)) {
        assert.equal(item.backend, selected, 'Selected category/support route used wrong backend');
        assert([200, 201, 401, 403, 404].includes(item.status), 'Unexpected selected response');
    } else if (matches(item.path, input.coexistence_routes)) {
        assert.equal(item.backend, 'django-coexistence');
        assert([200, 201, 403].includes(item.status), 'Unexpected explicit Django coexistence response');
    } else {
        assert(!item.path.startsWith('/api/'), 'Unaccounted API request');
        assert.equal(item.backend, 'ui-static');
        assert(item.status >= 200 && item.status < 400, 'Failed static frontend resource');
    }
};
async function fresh(username, userID) {
    const context = await browser.newContext({viewport: {width: 1440, height: 1000}});
    await context.route('**/*', route => {
        const url = new URL(route.request().url());
        if (url.origin === input.origin) return route.continue();
        blockedExternal.push({origin: url.origin, path: url.pathname});
        return route.abort('blockedbyclient');
    });
    const page = await context.newPage(); page.setDefaultTimeout(25000);
    page.on('response', response => pending.push(record(response).catch(() => failures.push({kind: 'response', phase,
        path: new URL(response.url()).pathname, status: response.status()}))));
    page.on('pageerror', error => failures.push({kind: 'pageerror', phase, error_class: error.name,
        asset_frames: [...String(error.stack || '').matchAll(/\/build-assets\/([A-Za-z0-9_.-]+\.js):(\d+):(\d+)/g)]
            .map(match => ({asset: match[1], line: Number(match[2]), column: Number(match[3])}))}));
    page.on('requestfailed', request => {
        const url = new URL(request.url());
        if (url.origin === input.origin && request.failure()?.errorText !== 'net::ERR_ABORTED')
            failures.push({kind: 'requestfailed', path: url.pathname});
    });
    await page.goto(input.origin + '/#/login');
    await expect(page.locator('input[name="username_login"]')).toBeVisible();
    assert(await page.evaluate(() => localStorage.getItem('sessionToken') === null), 'Authentication state was seeded');
    await page.locator('input[name="username_login"]').fill(username);
    await page.locator('input[name="password_login"]').fill(input.password);
    const login = page.waitForResponse(r => new URL(r.url()).pathname === '/api/oauth2/login' && r.request().method() === 'POST');
    await page.locator('#bkn_login_signin_submit').click();
    const response = await login;
    assert.equal(response.status(), 200); assert.equal(response.headers()['x-fixture-backend'], selected);
    const payload = await response.json(); assert.equal(payload.user_data.user_id, userID);
    await expect(page.locator('#bkn_aside')).toBeVisible();
    const notice = page.getByRole('button', {name: /Ok, grazie/});
    if (await notice.isVisible()) await notice.click();
    // Login populates the UI profile cache. Independently read the actual profile
    // to bind this session to its actor and association through the selected API.
    const profile = await api(page, 'profile/info');
    assert.equal(profile.status, 200); assert.equal(profile.backend, selected);
    assert.equal(profile.body.user_data.user_id, userID);
    assert.equal(profile.body.user_data.sport_association.sport_association_id, input.association_id);
    return {context, page};
}
async function api(page, route, method = 'GET', body) {
    return page.evaluate(async ({route, method, body}) => {
        const response = await fetch('/api/' + route, {method, headers: {'Content-Type': 'application/json',
            Authorization: 'Bearer ' + JSON.parse(localStorage.getItem('sessionToken'))},
            ...(body === undefined ? {} : {body: JSON.stringify(body)})});
        return {status: response.status, backend: response.headers.get('x-fixture-backend'), body: await response.json()};
    }, {route, method, body});
}
async function categories(page, name) {
    await page.goto(input.origin + '/#/payment/category/list');
    await expect(page.getByText('Causali dei pagamenti', {exact: true})).toBeVisible();
    await page.getByRole('textbox', {name: 'Cerca nella tabella', exact: true}).fill(name);
    const row = page.locator('[data-row]').filter({hasText: name.toUpperCase()});
    await expect(row).toBeVisible(); return row;
}
async function select(modal, label, option) {
    await modal.getByRole('textbox', {name: label, exact: true}).click();
    await modal.locator('.list-item').getByText(option, {exact: true}).click();
}
async function readCategory(page) {
    const result = await api(page, 'payment/category/list');
    assert.equal(result.status, 200); assert.equal(result.backend, selected);
    assert(!result.body.data.some(row => row.payment_category_id === input.foreign_category_id), 'Foreign category leaked into scoped list');
    return result.body.data.find(row => row.payment_category_id === categoryID);
}
async function screenshot(page, name) {
    await expect(page.locator('[data-sonner-toast]:visible')).toHaveCount(0, {timeout: 15000});
    await page.screenshot({path: path.join(output, name + '.png'), fullPage: true});
}
try {
    phase = 'writer-password-login';
    const writer = await fresh(input.username, input.user_id), page = writer.page;
    phase = 'create-category';
    await page.goto(input.origin + '/#/payment/category/list');
    await expect(page.getByText('Causali dei pagamenti', {exact: true})).toBeVisible();
    await page.getByText('Crea causale', {exact: true}).click();
    const create = page.locator('#addAccountModal'); await expect(create).toBeVisible();
    await create.getByPlaceholder('Nome', {exact: true}).fill(input.created_name);
    await create.locator('select[name="expense"]').selectOption('false');
    await select(create, 'Tipologia', 'Istituzionale');
    await select(create, 'Tipo IVA', 'IVA Esente');
    await select(create, 'Tipo detrazione', 'No');
    const added = page.waitForResponse(r => new URL(r.url()).pathname === '/api/payment/category/add' && r.request().method() === 'POST');
    await create.getByRole('button', {name: 'Salva', exact: true}).click();
    const addResponse = await added; assert.equal(addResponse.status(), 200);
    const created = (await addResponse.json()).data;
    categoryID = created.payment_category_id; initialVAT = created.vat_management.vat_management_id;
    assert.equal(created.name, input.created_name); assert.equal(created.sport_association, input.association_id);
    assert.equal(created.expense, false); assert.equal(created.type, 1);
    assert.equal(created.tax_deductible, false); assert.equal(created.archived, false); assert.equal(created.deleted, false);
    assert.equal(created.vat_management.vat, 1); assert.equal(created.vat_management.deduction, 0);
    let row = await categories(page, input.created_name);
    await page.reload();
    await expect(page.locator('[data-row]').filter({hasText: input.created_name.toUpperCase()})).toBeVisible();
    checks.push('unchanged-ui-create-and-reload');

    phase = 'edit-category';
    row = await categories(page, input.created_name);
    await row.locator('button:is([title="Modifica"],[data-original-title="Modifica"])').click();
    const edit = page.locator('#editAccountModal-' + categoryID); await expect(edit).toBeVisible();
    await edit.getByPlaceholder('Nome', {exact: true}).fill(input.changed_name);
    await select(edit, 'Tipologia', 'Commerciale');
    await select(edit, 'Tipo IVA', 'IVA 22%');
    await select(edit, 'Tipo detrazione', '50%');
    const edited = page.waitForResponse(r => new URL(r.url()).pathname === `/api/payment/category/${categoryID}/update` && r.request().method() === 'PATCH');
    await edit.getByRole('button', {name: 'Salva', exact: true}).click(); assert.equal((await edited).status(), 200);
    row = await categories(page, input.changed_name);
    const saved = await readCategory(page); assert.equal(saved.name, input.changed_name); assert.equal(saved.type, 2);
    assert.equal(saved.vat_management.vat, 16); assert.equal(saved.vat_management.deduction, 2);
    await screenshot(page, 'category-edited'); checks.push('unchanged-ui-edit-vat-and-reload');

    phase = 'archive-api-visible-readback';
    // The unchanged category screen has no archive controls. This API operation
    // is kept distinct from UI create/edit/delete; archived categories stay visible.
    for (const archived of [true, false]) {
        const result = await api(page, `payment/category/${categoryID}/update`, 'PATCH', {archived});
        assert.equal(result.status, 200); assert.equal(result.backend, selected); assert.equal(result.body.data.archived, archived);
        row = await categories(page, input.changed_name); await page.reload();
        await expect(page.locator('[data-row]').filter({hasText: input.changed_name.toUpperCase()})).toBeVisible();
        assert.equal((await readCategory(page)).archived, archived);
    }
    checks.push('authenticated-api-archive-unarchive-with-unchanged-ui-visibility');

    phase = 'reader-password-login-and-denials';
    const reader = await fresh(input.reader_username, input.reader_user_id);
    const readerRow = await categories(reader.page, input.changed_name);
    await expect(reader.page.getByText('Crea causale', {exact: true})).toHaveCount(0);
    await expect(readerRow.locator('button:is([title="Modifica"],[data-original-title="Modifica"])')).toBeDisabled();
    await expect(readerRow.locator('button:is([title="Elimina"],[data-original-title="Elimina"])')).toBeDisabled();
    for (const [route, method, body] of [['payment/category/add', 'POST', {name: 'Denied', vat_management: {}}],
        [`payment/category/${categoryID}/update`, 'PATCH', {name: 'Denied'}], [`payment/category/${categoryID}/delete`, 'DELETE', undefined]]) {
        const result = await api(reader.page, route, method, body); assert.equal(result.status, 403); assert.equal(result.backend, selected);
    }
    assert.deepEqual(await readCategory(reader.page), saved);
    assert.deepEqual(await readCategory(page), saved);
    for (const deniedID of [input.shared_category_id, input.foreign_category_id]) {
        for (const method of ['PATCH', 'DELETE']) {
            const result = await api(page, `payment/category/${deniedID}/${method === 'PATCH' ? 'update' : 'delete'}`, method,
                method === 'PATCH' ? {name: 'Denied category'} : undefined);
            assert.equal(result.status, 404); assert.equal(result.backend, selected);
        }
    }
    await screenshot(reader.page, 'category-reader-denied'); checks.push('real-reader-ui-and-server-denials');

    phase = 'payment-ui-category-selection';
    await page.goto(input.origin + '/#/payment/list');
    await expect(page.getByRole('button', {name: 'Pagamento', exact: true})).toBeVisible();
    await page.getByRole('button', {name: 'Pagamento', exact: true}).click();
    const modal = page.locator('#payment-modal'); await expect(modal).toBeVisible();
    await expect(modal.locator('input[name="description"]')).toBeVisible();
    await modal.locator('input[name="description"]').fill(input.payment_description);
    await modal.locator('input[name="amount"]').fill('12,50');
    await select(modal, 'Causale', input.changed_name);
    assert.equal(JSON.parse(await modal.locator('input[name="payment_category"]').inputValue()).value, categoryID);
    await screenshot(page, 'payment-category-selected');
    const paymentAdded = page.waitForResponse(r => new URL(r.url()).pathname === '/api/payment/add' && r.request().method() === 'POST');
    await modal.getByRole('button', {name: 'Crea', exact: true}).click();
    const paymentResponse = await paymentAdded; assert.equal(paymentResponse.status(), 201);
    assert.equal(paymentResponse.headers()['x-fixture-backend'], 'django-coexistence');
    const payment = await paymentResponse.json(); paymentID = payment.payment_id;
    assert.equal(payment.payment_category, categoryID); assert.equal(payment.description, input.payment_description);
    const paymentDrawer = page.getByRole('dialog', {name: 'Dettagli Pagamento', exact: true});
    await expect(paymentDrawer).toBeVisible();
    await paymentDrawer.locator('.drawer-header button.close').click();
    await expect(paymentDrawer).toBeHidden();
    checks.push('real-payment-ui-selects-selected-backend-category-and-django-persists-reference');

    phase = 'delete-cancel-confirm-reload';
    row = await categories(page, input.changed_name);
    const remove = () => row.locator('button:is([title="Elimina"],[data-original-title="Elimina"])').click();
    await remove();
    const confirmation = page.locator('.swal2-popup'); await expect(confirmation).toContainText('Vuoi eliminare la causale di pagamento?');
    await confirmation.getByRole('button', {name: 'Annulla', exact: true}).click(); assert.equal((await readCategory(page)).deleted, false);
    await remove();
    const deleted = page.waitForResponse(r => new URL(r.url()).pathname === `/api/payment/category/${categoryID}/delete` && r.request().method() === 'DELETE');
    await confirmation.getByRole('button', {name: 'Elimina', exact: true}).click(); assert.equal((await deleted).status(), 200);
    row = await categories(page, input.changed_name); await expect(row).toContainText('Eliminata');
    assert.equal((await readCategory(page)).deleted, true);
    await screenshot(page, 'category-deleted'); checks.push('unchanged-ui-delete-cancel-soft-delete-reload');
    await Promise.all(pending); assert.deepEqual(failures, []);
    assert.equal(responses.filter(r => r.path === '/api/payment/category/add' && r.status === 200).length, 1);
    assert.equal(responses.filter(r => r.path === `/api/payment/category/${categoryID}/update` && r.status === 200).length, 3);
    assert.equal(responses.filter(r => r.path === `/api/payment/category/${categoryID}/delete` && r.status === 200).length, 1);
    success = true;
} catch (error) {
    failures.push({kind: 'workflow-assertion', phase, error_class: error.name, assertion_code: error.code || null,
        category: String(error.message).includes('Timeout') ? 'timeout' : 'assertion'});
} finally {
    clearTimeout(deadline); await browser.close(); await Promise.all(pending);
    success = success && failures.length === 0;
    await fs.writeFile(path.join(output, 'browser.json'), JSON.stringify({success, version: input.version, phase, checks,
        responses, failures, blocked_external: blockedExternal, category_id: categoryID, initial_vat_id: initialVAT, payment_id: paymentID,
        authentication_state_seeded: false, api_responses_mocked: false,
        archive_ui_limit: 'No category archive/unarchive UI exists. Actual authenticated PATCH and subsequent UI/readback tested.',
        payment_coexistence: 'Category reads/mutations use the selected versioned backend; payment/dependency operations remain real Django.'}, null, 2)+'\n');
    if (!success) throw new Error('Category browser failed at sanitized phase: '+phase);
}
