import {test, expect, settings, retired} from './p5-runtime-stub.js';
import {controller, operationId, snapshot} from '../../../UI/scripts/p5-runtime-fixtures.mjs';

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
    stub.runtime = snapshot('settle');
    const guest = await stub.open({owner: false});
    await expect(guest.page.getByText(/riservata al proprietario/)).toBeVisible();
    await expect(toggle(guest.page)).toHaveCount(0);
    await expect(guest.page.locator('body')).not.toContainText(operationId);
    stub.runtime = {...snapshot(), switch_available: false, unavailable_reason: 'Questa versione non supporta il cambio di runtime.'};
    const page = await openSettings(stub);
    await expect(toggle(page), 'Owner needs a named runtime switch even when unavailable').toBeVisible();
    await expect(toggle(page)).toBeDisabled();
    await expect(page.getByText(stub.runtime.unavailable_reason)).toBeVisible();
    const section = page.getByRole('region', {name: 'Runtime dell’istanza'});
    await expect(section.getByRole('alert')).toHaveCount(0);
    await expect(section.getByRole('dialog')).toHaveCount(0);
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
    // A response delivered without verified ownership must never disclose its payload.
    await guest.page.route('**/instance-update-status', route => route.fulfill({json: {
        ...controller(stub.runtime), is_owner: false,
    }}));
    const checked = guest.page.waitForRequest(request => new URL(request.url()).pathname === '/' && !request.isNavigationRequest());
    await guest.page.reload();
    await checked;
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

test('maintenance_revoked', async ({stub}) => {
    stub.offline = true;
    stub.runtime = snapshot('stop_source');
    // The current owner can change during a long maintenance window.
    const revoked = await stub.open();
    await expect(field(revoked.page, 'operation')).toHaveText(operationId);
    await revoked.page.route('**/instance-update-status', route => route.fulfill({status: 403, json: {}}));
    await revoked.page.waitForResponse(response => response.url().endsWith('/instance-update-status') && response.status() === 403);
    await expect(revoked.page.locator('body'), 'A denied owner must lose previously displayed operation details').not.toContainText(operationId);
});

test('cold_recovery', async ({stub}) => {
    stub.offline = true;
    stub.runtime = snapshot('verify', {failed: true});
    const {page} = await stub.open();
    await expect(field(page, 'operation')).toHaveText(operationId);
    await expect(field(page, 'effective'), 'Cold recovery must identify the effective runtime').toHaveText('Go');
    await expect(field(page, 'intended')).toHaveText('Go');
    await expect(field(page, 'last-completed')).toContainText('Attivazione');
    await expect(field(page, 'recovery')).toContainText('runtime-recover');
});

test('reversal_requires_password', async ({stub}) => {
    // The controller has cleared the recovery operation and now admits a new switch.
    stub.runtime = snapshot('completed');
    const page = await openSettings(stub);
    await expect(toggle(page)).toBeEnabled();
    await toggle(page).focus();
    await page.keyboard.press('Space');
    const dialog = page.getByRole('dialog', {name: 'Tornare a Django?'});
    await expect(dialog.getByRole('button', {name: 'Conferma cambio'})).toBeDisabled();
    // The shared dialog restores the opener in its native close handler.
    const dismissal = await dialog.evaluateHandle(node => ({
        done: new Promise(resolve => node.addEventListener('close', () => resolve(), {once: true})),
    }));
    await page.keyboard.press('Escape');
    await expect(dialog).toBeHidden();
    await dismissal.evaluate(({done}) => done);
    await dismissal.dispose();
    await expect(toggle(page)).toBeFocused();
    expect(submissions(stub)).toHaveLength(0);
    stub.onSubmit = route => route.fulfill({status: 400, json: {error: 'Password non corretta.'}});
    await confirm(page);
    await expect(dialog.getByRole('alert')).toHaveText('Password non corretta.');
    await expect(dialog.getByLabel('Conferma la tua password')).toHaveValue('');
    accept(stub, 'go', 8);
    await dialog.getByLabel('Conferma la tua password').fill('fixture');
    await dialog.getByRole('button', {name: 'Conferma cambio'}).click();
    await expect(dialog).toBeHidden();
    assertBody(submissions(stub)[1].body, 'django', 8);
    await expect(field(page, 'intended')).toHaveText('Django');
    await page.context().close();

    // A reachable API alone does not clear the controller's recovery interlock.
    stub.runtime = snapshot('verify', {failed: true});
    const pending = await openSettings(stub);
    await expect(field(pending, 'recovery')).toContainText('runtime-recover');
    await expect(toggle(pending), 'An active recovery must clear before another switch is available').toBeDisabled();
    expect(submissions(stub)).toHaveLength(2);
});

test('blocker_age', async ({stub}) => {
    stub.runtime = snapshot('stage_backlog', {blocked: true});
    const running = await openSettings(stub);
    await expect(field(running, 'status')).toHaveText('Bloccato');
    await expect(toggle(running)).toBeDisabled();
    await expect(field(running, 'operation')).toHaveText(operationId);
    await running.context().close();
    for (const [seconds, label] of [[73, '73 secondi'], [180, '3 minuti'], [10800, '3 ore'], [1, '1 secondo']]) {
        stub.runtime = snapshot('preflight', {failed: true, blocked: true});
        stub.runtime.operation.blockers.oldest_age_seconds = seconds;
        const page = await openSettings(stub);
        await expect(field(page, 'oldest'), 'Format the oldest blocked item as ' + label).toHaveText(label);
        await page.context().close();
    }
});

test('network_blips_keep_draft', async ({stub}) => {
    const {page, sockets} = await stub.open({path: settings.replace('tab=api', 'tab=email')});
    const draft = page.getByLabel('Server SMTP');
    await draft.fill('draft.example.test');
    const original = await draft.elementHandle();
    const origin = await page.evaluate(() => performance.timeOrigin);
    const connections = path => sockets.filter(socket => socket.path === path).length;
    const reads = () => stub.calls.filter(call => call.path === '/api/instance/config').length;
    await expect.poll(() => connections('/ws/health/')).toBe(1);
    await expect.poll(() => connections('/ws/notifications/')).toBe(1);
    const initial = reads();
    for (let i = 1; i <= 3; i++) {
        await stub.disconnect('/ws/health/');
        await expect(page.locator('.connection-status')).toBeVisible();
        await expect.poll(reads).toBe(initial + i);
        await expect(page.locator('.connection-status')).toBeHidden();
        expect(connections('/ws/health/')).toBe(i + 1);
        expect(connections('/ws/notifications/')).toBe(1);
        await expect(draft).toHaveValue('draft.example.test');
        expect(await original.evaluate(node => node.isConnected)).toBe(true);
    }
    // Advance beyond the longest scheduled health retry. A stable socket stays open.
    await new Promise(resolve => setTimeout(resolve, 9000));
    expect(connections('/ws/health/')).toBe(4);
    expect(reads()).toBe(initial + 3);
    expect(await page.evaluate(() => performance.timeOrigin)).toBe(origin);
});

test('validation_error_leaves_password_dialog', async ({stub}) => {
    const page = await openSettings(stub);
    stub.onSubmit = route => route.fulfill({status: 400, json: {error: 'Richiesta non valida.'}});
    await confirm(page);
    await expect(page.getByRole('dialog'), 'A validation failure must leave password confirmation').toBeHidden();
    await expect(page.getByText('Richiesta non valida.', {exact: true})).toBeVisible();
    expect(submissions(stub)).toHaveLength(1);
});

test('settings_access_revoked', async ({stub}) => {
    stub.runtime = snapshot('settle');
    const page = await openSettings(stub);
    await expect(field(page, 'operation')).toHaveText(operationId);
    await page.route('**/api/instance/admin/runtime', route => route.fulfill({status: 403, json: {error: 'Accesso negato.'}}));
    await page.route('**/api/instance/admin/updates', route => route.fulfill({status: 403, json: {}}));
    await page.route('**/instance-update-status', route => route.fulfill({status: 403, json: {}}));
    await page.waitForResponse(response => response.url().endsWith('/admin/runtime') && response.status() === 403);
    await expect(toggle(page), 'Revoked settings access must remove the owner switch').toHaveCount(0);
    await expect(page.locator('body')).not.toContainText(operationId);
});

test('maintenance_session_change', async ({stub}) => {
    stub.offline = true;
    stub.runtime = snapshot('stop_source');
    const {page} = await stub.open();
    await expect(field(page, 'operation')).toHaveText(operationId);
    const arrived = Promise.withResolvers();
    const release = Promise.withResolvers();
    await page.route('**/instance-update-status', async route => {
        // Hold the last owner reply until the browser has changed sessions.
        arrived.resolve();
        await release.promise;
        await route.fulfill({json: controller(stub.runtime)});
    });
    await arrived.promise;
    const processed = page.waitForRequest(request => new URL(request.url()).pathname === '/' && !request.isNavigationRequest());
    await page.evaluate(() => localStorage.setItem('sessionToken', JSON.stringify('fixture-member')));
    release.resolve();
    await processed;
    await expect(page.locator('body'), 'Owner progress belongs to the session that requested it').not.toContainText(operationId);
});

test('maintenance_session_outage', async ({stub}) => {
    stub.offline = true;
    stub.runtime = snapshot('stop_source');
    const {page} = await stub.open();
    await expect(field(page, 'operation')).toHaveText(operationId);
    await page.route('**/instance-update-status', route => route.fulfill({status: 503, json: {}}));
    await page.evaluate(() => localStorage.setItem('sessionToken', JSON.stringify('fixture-member')));
    await page.waitForResponse(response => response.url().endsWith('/instance-update-status') &&
        response.request().headers().authorization === 'Bearer fixture-member');
    await expect(page.locator('body'), 'A different session must have a public maintenance view during an outage').not.toContainText(operationId);
});
