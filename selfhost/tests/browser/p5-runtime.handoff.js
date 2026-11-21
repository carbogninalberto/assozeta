import {test, expect, settings, retired} from './p5-runtime-stub.js';
import {operationId, snapshot} from '../../../UI/scripts/p5-runtime-fixtures.mjs';

const field = (page, name) => page.getByTestId('runtime-' + name).filter({visible: true});
const toggle = page => page.getByRole('switch', {name: 'Usa Go per l’istanza', exact: true});
const submissions = stub => stub.calls.filter(c => c.path.endsWith('/admin/runtime') && c.method === 'POST');

async function openSettings(stub, options) {
    const {page} = await stub.open(options);
    // Distinguish a missing runtime control from a broken fixture or a failed login.
    await expect(page.getByRole('tablist', {name: 'Sezioni istanza'})).toBeVisible();
    return page;
}
async function confirm(page, repeat = false) {
    const control = toggle(page);
    await expect(control, 'Owner settings must expose the instance runtime switch').toBeVisible();
    await control.focus();
    await page.keyboard.press('Space');
    const dialog = page.getByRole('dialog');
    await expect(dialog).toBeVisible();
    const button = dialog.getByRole('button', {name: 'Conferma cambio'});
    await expect(button).toBeDisabled();
    await dialog.getByLabel('Conferma la tua password').fill('fixture');
    await button.focus();
    await page.keyboard.press('Enter');
    if (repeat) await page.keyboard.press('Enter');
}
async function stage(page, label, index) {
    await expect(field(page, 'stage')).toHaveText(label);
    expect(await field(page, 'status').evaluate(node => !!node.closest('[role=status], [aria-live=polite], [aria-live=assertive]'))).toBe(true);
    await expect(field(page, 'progress')).toContainText(new RegExp(index + '\\s*(?:/|di)\\s*9'));
    await expect(field(page, 'status')).not.toContainText('Completato');
}
function accept(stub, source, generation, {replayed = false} = {}) {
    stub.onSubmit = route => {
        stub.runtime = snapshot('queued', {source, generation});
        return route.fulfill({status: 202, json: {operation: stub.runtime.operation, replayed,
            authority: {runtime: source, generation, operation_id: null}}});
    };
}
function assertBody(body, target, generation) {
    expect(body).toEqual({target, expected_generation: generation, password: 'fixture',
        idempotency_key: expect.stringMatching(/^[\da-f]{8}(?:-[\da-f]{4}){3}-[\da-f]{12}$/i)});
}

test('owner_access', async ({stub}) => {
    const guest = await stub.open({owner: false});
    await expect(guest.page.getByText(/riservata al proprietario/)).toBeVisible();
    await expect(toggle(guest.page)).toHaveCount(0);
    stub.runtime = {...snapshot(), switch_available: false, unavailable_reason: 'Cambio non disponibile su questa versione.'};
    const page = await openSettings(stub);
    await expect(toggle(page), 'Owner needs a named runtime switch even when unavailable').toBeVisible();
    await expect(toggle(page)).toBeDisabled();
    await expect(page.getByText(stub.runtime.unavailable_reason)).toBeVisible();
    await expect(field(page, 'effective')).toHaveText('Django');
    await expect(field(page, 'intended')).toHaveText('Django');
    await expect(page.getByText('Runtime effettivo', {exact: true})).toBeVisible();
    await expect(page.getByText('Runtime previsto', {exact: true})).toBeVisible();
    await expect(toggle(page)).not.toBeChecked();
    await expect(page.locator('#instance-go-tasks, #instance-api-v2')).toHaveCount(0);
    await expect(page.getByText('Le attività in background seguono il runtime effettivo.')).toBeVisible();
    expect(submissions(stub)).toHaveLength(0);
});

for (const [source, target] of [['django', 'go'], ['go', 'django']]) {
    test('switch_to_' + target, async ({stub}) => {
        const name = runtime => runtime === 'django' ? 'Django' : 'Go';
        stub.runtime = snapshot(null, {source});
        accept(stub, source, 7, {replayed: source === 'go'});
        const page = await openSettings(stub);
        await expect(toggle(page), 'Owner settings must expose the instance runtime switch').toBeVisible();
        await expect(toggle(page)).toBeChecked({checked: source === 'go'});
        await confirm(page, true);
        await expect(field(page, 'operation')).toHaveText(operationId);
        assertBody(submissions(stub)[0].body, target, 7);
        await expect(toggle(page)).toBeDisabled();
        const resumed = await openSettings(stub);
        await expect(field(resumed, 'operation')).toHaveText(operationId);
        await expect(toggle(resumed)).toBeDisabled();
        await resumed.context().close();
        await expect(field(page, 'intended')).toHaveText(name(target));
        await expect(field(page, 'effective')).toHaveText(name(source));
        stub.offline = true;
        for (const [key, label, index] of [
            ['stop_source', 'Arresto del runtime di origine', 5],
            ['target_preflight', 'Verifica del runtime di destinazione', 6],
            ['activate', 'Attivazione', 8],
        ]) {
            stub.runtime = snapshot(key, {source});
            await stage(page, label, index);
        }
        expect(stub.calls.some(c => c.method === 'POST' && c.path === '/instance-update-status'
            && c.authorization === 'Bearer fixture')).toBe(true);
        await expect(field(page, 'effective')).toHaveText(name(target));
        stub.offline = false;
        stub.runtime = snapshot('completed', {source});
        await expect(field(page, 'status')).toContainText('Completato');
        await expect(toggle(page)).toBeEnabled();
        await expect(toggle(page)).toBeChecked({checked: target === 'go'});
        expect(submissions(stub)).toHaveLength(1);
        expect(stub.calls.filter(c => c.method === 'PUT' && c.path.endsWith('/instance/admin'))).toHaveLength(0);
    });
}

test('lost_reply', async ({stub}) => {
    stub.onSubmit = route => {
        stub.runtime = snapshot('stop_source');
        stub.offline = true;
        return route.abort('connectionreset');
    };
    const page = await openSettings(stub);
    await confirm(page);
    await expect(field(page, 'operation')).toHaveText(operationId);
    await stage(page, 'Arresto del runtime di origine', 5);
    stub.offline = false;
    stub.runtime = snapshot('completed');
    await expect(field(page, 'status')).toContainText('Completato');
    expect(submissions(stub)).toHaveLength(1);
});

test('cold_maintenance', async ({stub}) => {
    stub.offline = true;
    stub.runtime = snapshot('stop_source');
    const first = await stub.open();
    expect(first.response.status()).toBe(503);
    await expect(first.page.locator('main')).toBeVisible();
    await expect(field(first.page, 'operation'), 'Cold maintenance document must resume the controller operation').toHaveText(operationId);
    await first.context.close();
    stub.runtime = snapshot('target_preflight');
    const {page} = await stub.open();
    await expect(field(page, 'operation')).toHaveText(operationId);
    await stage(page, 'Verifica del runtime di destinazione', 6);
    const guest = await stub.open({owner: false});
    await expect.poll(() => stub.calls.filter(c => c.page === guest.page && c.path === '/instance-update-status').length).toBeGreaterThan(0);
    await expect(guest.page.locator('body')).not.toContainText(operationId);
    stub.offline = false;
    stub.runtime = snapshot('completed');
    await expect(toggle(page)).toBeEnabled();
    await expect(field(page, 'effective')).toHaveText('Go');
    expect(submissions(stub)).toHaveLength(0);
});

test('stale_generation', async ({stub}) => {
    const page = await openSettings(stub);
    stub.onSubmit = route => {
        stub.runtime = snapshot(null, {source: 'go', generation: 8});
        return route.fulfill({status: 409, json: {code: 'stale_generation', error: 'La generazione richiesta non è più attuale.',
            active: null, authority: {runtime: 'go', generation: 8, operation_id: operationId}}});
    };
    await confirm(page);
    await expect(page.getByText('La generazione richiesta non è più attuale.')).toBeVisible();
    await expect(field(page, 'effective')).toHaveText('Go');
    await expect(field(page, 'intended')).toHaveText('Go');
    assertBody(submissions(stub)[0].body, 'go', 7);
    accept(stub, 'go', 8);
    await confirm(page);
    await expect(field(page, 'operation')).toHaveText(operationId);
    const requests = submissions(stub);
    expect(requests).toHaveLength(2);
    assertBody(requests[1].body, 'django', 8);
    expect(requests[1].body.idempotency_key).not.toBe(requests[0].body.idempotency_key);
});

for (const code of ['operation_in_progress', 'idempotency_conflict']) {
    test(code, async ({stub}) => {
        const page = await openSettings(stub);
        stub.onSubmit = route => {
            stub.runtime = snapshot('preflight');
            const payload = {code, active: stub.runtime.active, authority: {runtime: 'django', generation: 7, operation_id: null},
                error: code === 'operation_in_progress' ? 'Operazione in corso.' : 'Identificativo già usato.'};
            if (code === 'idempotency_conflict') payload.operation = stub.runtime.active;
            return route.fulfill({status: 409, json: payload});
        };
        await confirm(page);
        await expect(field(page, 'operation')).toHaveText(operationId);
        await expect(toggle(page)).toBeDisabled();
        await expect(field(page, 'effective')).toHaveText('Django');
        await expect(field(page, 'intended')).toHaveText('Go');
        expect(submissions(stub)).toHaveLength(1);
    });
}

test('blocked_dispositions', async ({stub}) => {
    stub.runtime = snapshot('preflight', {blocked: true, failed: true});
    const op = stub.runtime.operation;
    Object.assign(op, {task_name: 'PRIVATE_TASK_MARKER', args: ['PRIVATE_ARGUMENT_MARKER'],
        environment: {setting: 'PRIVATE_ENV_MARKER'}, password: 'NASC123'});
    op.blockers.items[0].provider_id = 'PRIVATE_PROVIDER_MARKER';
    op.progress.extra = 'PRIVATE_PROGRESS_MARKER';
    op.dispositions.extra = 'PRIVATE_COUNT_MARKER';
    const page = await openSettings(stub);
    await expect(field(page, 'blocked'), 'Settings must show blocked work separately').toHaveText('5');
    for (const [key, count] of Object.entries({staged: 17, transferred: 13, blocked: 5, ambiguous: 2})) await expect(field(page, key)).toHaveText(String(count));
    await expect(field(page, 'oldest')).toContainText(/73\s*(s|secondi)/);
    await expect(field(page, 'effective')).toHaveText('Django');
    await expect(field(page, 'intended')).toHaveText('Django');
    await expect(field(page, 'recovery')).toContainText('Riconcilia');
    await expect(page.getByText(op.blockers.action)).toBeVisible();
    expect(await page.locator('body').innerHTML()).not.toMatch(/NASC123|PRIVATE_(TASK|ARGUMENT|ENV|PROVIDER|PROGRESS|COUNT)_MARKER/);
});

test('pre_commit_failure', async ({stub}) => {
    stub.runtime = snapshot('target_preflight', {failed: true});
    stub.runtime.operation.error = 'Verifica i servizi di destinazione e riprova.';
    stub.runtime.operation.error_code = 'step_failed';
    const page = await openSettings(stub);
    await expect(field(page, 'last-completed'), 'A pre-commit failure must show the last completed stage')
        .toContainText('Arresto del runtime di origine');
    await expect(field(page, 'effective')).toHaveText('Django');
    await expect(field(page, 'intended')).toHaveText('Django');
    await expect(field(page, 'recovery')).toContainText('Riprova');
    await expect(page.getByText(stub.runtime.operation.error)).toBeVisible();
});

test('post_commit_failure', async ({stub}) => {
    stub.runtime = snapshot('verify', {failed: true});
    const page = await openSettings(stub);
    await expect(field(page, 'effective'), 'Failure after commit must identify the effective runtime').toHaveText('Go');
    await expect(field(page, 'last-completed')).toContainText('Attivazione');
    await expect(field(page, 'recovery')).toContainText('Ripristina');
    await expect(page.getByText(stub.runtime.operation.error)).toBeVisible();
    await expect(field(page, 'status')).not.toContainText('Completato');
    stub.runtime = snapshot('completed');
    await expect(field(page, 'status')).toContainText('Completato');
    await expect(field(page, 'effective')).toHaveText('Go');
});

test('stale_browser', async ({stub}) => {
    const page = await openSettings(stub, {stale: true, path: '/?api=v2' + settings.slice(1)});
    const paths = stub.calls.filter(c => c.path.startsWith('/api/')).map(c => c.path);
    expect(paths.filter(p => p.startsWith('/api/v2/')), 'A stale browser must use the stable API on its first load').toEqual([]);
    await expect(toggle(page)).not.toBeChecked();
    for (const key of Object.keys(retired)) {
        expect(await page.evaluate(k => [localStorage.getItem(k), sessionStorage.getItem(k)], key)).toEqual([null, null]);
    }
    expect(stub.calls.filter(c => c.method !== 'GET' && c.path !== '/instance-update-status')).toHaveLength(0);
});

test('other_route_refresh', async ({stub}) => {
    const {page, sockets} = await stub.open({owner: false, path: '/#/'});
    await expect(page.getByRole('heading', {name: 'Ciao fixture!'})).toBeVisible();
    await expect(page.getByRole('img', {name: 'Fixture 7', exact: true}).first()).toBeVisible();
    for (const path of ['/ws/notifications/', '/ws/health/']) {
        await expect.poll(() => sockets.some(s => s.path === path && s.generation === 7)).toBe(true);
    }
    // A different client initiates the switch. No local storage event, test event or navigation.
    stub.runtime = snapshot('stop_source');
    stub.offline = true;
    // Model a short maintenance window. Clients may wait for readiness before reconnecting.
    await stub.disconnect();
    await new Promise(resolve => setTimeout(resolve, 1500));
    stub.runtime = snapshot('completed');
    stub.offline = false;
    for (const path of ['/ws/notifications/', '/ws/health/']) {
        await expect.poll(() => sockets.some(s => s.path === path && s.generation === 8)).toBe(true);
    }
    await expect(page.getByRole('img', {name: 'Fixture 8', exact: true}).first(),
        'An open route must refresh runtime-dependent configuration after reconnect').toBeVisible();
    await expect(page).toHaveURL(/\/#\/$/);
    expect(submissions(stub)).toHaveLength(0);
});
