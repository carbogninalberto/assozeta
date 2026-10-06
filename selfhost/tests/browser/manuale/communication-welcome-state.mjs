// Activating a saved new_subscription rule does not schedule a cron or
// retrospectively emit an event. No new subscription is created in this scenario.
import {scenario, expect} from './scenario.mjs';
import {focusRegion, tableFocus} from './focus.mjs';
import {communicationCompleteAuthoredWorkflows} from '../../../../docs/manuale/communication-complete-authored-workflows.mjs';
// Seeded and frozen-clock rows share timestamps (or the list is unordered), so compare
// record sets: identical rows and contents, independent of physical row order.
const records = rows => (Array.isArray(rows) ? rows.map(row => JSON.stringify(row))
    : Object.entries(rows).map(entry => JSON.stringify(entry))).sort();
const id = 'communication-welcome-state';
const spec = communicationCompleteAuthoredWorkflows[id];
await scenario({id, prefix: spec.prefix.replace(/\/$/, ''), sources: spec.sources,
    actions: async ({page, api, context, actor, open, capture, report, input}) => {
        expect(input.fixture_version).toBe(8);
        expect(input.fixture_profile || 'baseline').toBe('baseline');
        const read = async route => {
            const response = await api(route); expect(response.status()).toBe(200); return response.json();
        };
        const baseline = await read('communications/workflows/list');
        const baselineLogs = await read('communications/email-logs/list');
        const name = 'Benvenuto nuova iscrizione - prova manuale';
        const subject = 'Benvenuto nell’Associazione Sportiva Aurora';
        const content = 'Benvenuto! La segreteria dell’Associazione Sportiva Aurora è a disposizione per gli orari e i documenti delle attività.';
        expect(baseline.some(row => row.name === name)).toBe(false);
        let workflowId, composer, baselineTree;
        let finished = false, outgoingRequests = 0;
        const proof = {};
        const taken = new Set();
        const observe = request => {
            const url = new URL(request.url());
            if (url.origin === input.origin && (url.pathname.startsWith('/api/communications/send/')
                || url.pathname.endsWith('/smtp/verify'))) outgoingRequests++;
        };
        context.on('request', observe);
        const take = async (checkpoint, target = page) => {
            const index = spec.checkpoints.findIndex(item => item.id === checkpoint);
            expect(index >= 0 && !taken.has(checkpoint)).toBe(true);
            await expect(target.locator('[data-sonner-toast]:visible')).toHaveCount(0, {timeout: 10000});
            let focus;
            if (['welcome-enable-confirmation', 'welcome-disable-confirmation'].includes(checkpoint)) {
                focus = target.locator('.swal2-popup');
                await expect(focus).toBeVisible();
                await expect(focus.getByRole('button', {name: 'Annulla', exact: true})).toBeVisible();
            } else if (await target.locator('[role="dialog"]:visible').count() === 1) {
                focus = target.locator('[role="dialog"]:visible');
            } else if (checkpoint === 'welcome-rule-name') {
                focus = focusRegion(target.locator('#automation-name'),
                    target.getByText('Configura', {exact: true}).first().locator('..'));
            } else if (checkpoint === 'welcome-composer-content-saved') {
                focus = focusRegion(target.locator('table[role="presentation"]').filter({hasText: content}).last(),
                    target.getByLabel('Contenuto', {exact: true}));
            } else {
                await expect(target.locator('.datatable-loading')).toHaveCount(0);
                await expect(row()).toBeVisible();
                focus = tableFocus(target, ['name', 'enabled'], row());
            }
            await capture(target, index + 1, checkpoint, focus); taken.add(checkpoint);
        };
        const details = () => read(`communications/workflows/${workflowId}/details`);
        const editor = () => page.locator('#portal-elements').filter({has: page.locator('#automation-name')});
        const row = () => page.locator('.datatable-body .datatable-row').filter({hasText: name});
        const updateResponse = () => page.waitForResponse(response => new URL(response.url()).pathname
            === `/api/communications/workflows/${workflowId}/update` && response.request().method() === 'PATCH');
        const closeDrawer = async drawer => {
            const saved = page.waitForResponse(response => {
                const route = new URL(response.url()).pathname;
                return route === '/api/communications/workflows/add' && response.request().method() === 'POST'
                    || workflowId && route === `/api/communications/workflows/${workflowId}/update`
                    && response.request().method() === 'PATCH';
            });
            await drawer.locator('.drawer-header button').click();
            const response = await saved; expect(response.status()).toBe(200);
            const body = await response.json(); if (!workflowId) workflowId = body.workflow_id;
            expect(typeof workflowId).toBe('string'); await expect(drawer).toHaveCount(0);
        };
        const assertEmail = async enabled => {
            const current = await details(); expect(current.enabled).toBe(enabled);
            expect(current.automation_tree.map(node => node.id)).toEqual(['trigger', 'message']);
            expect(current.automation_tree[0].value).toBe('new_subscription');
            expect(current.automation_tree[1].data).toMatchObject({recipients: 'me', subject});
            expect(current.automation_tree[1].data.content.includes(content)).toBe(true);
            return current;
        };
        try {
            await open('Comunicazioni', '/#/communication/automation');
            await page.getByText('Automazione', {exact: true}).click();
            await page.locator('#automation-name').fill(name);
            await page.locator('#automation-name').press('End');
            await take('welcome-rule-name');
            await editor().getByText('Configura', {exact: true}).first().click();
            let drawer = page.getByRole('dialog', {name: 'Evento di attivazione', exact: true});
            await drawer.locator('select').first().selectOption('new_subscription');
            await take('welcome-new-subscription-trigger');
            await closeDrawer(drawer); proof.created_via_ui = true;
            await editor().getByText('Azione Messaggio', {exact: true}).click();
            await editor().getByText('Configura', {exact: true}).nth(1).click();
            drawer = page.getByRole('dialog', {name: 'Invia un messaggio', exact: true});
            await drawer.locator('select').nth(0).selectOption('email');
            await drawer.locator('select').nth(1).selectOption('me');
            await drawer.locator('input[type="text"]').fill(subject);
            await take('welcome-email-recipient-subject');
            // The real handler saves first, then opens a delayed popup owned by
            // this page. Verify that save and the actual popup, without fallback navigation.
            const [openedComposer] = await Promise.all([
                page.waitForEvent('popup', {timeout: 60000}),
                (async () => {
                    const saved = updateResponse();
                    await drawer.getByRole('button', {name: 'Crea email', exact: true}).click();
                    expect((await saved).status()).toBe(200);
                })(),
            ]);
            composer = openedComposer;
            // Cover composer failures too; the common scenario observes owner.page only.
            const composerFailures = [];
            composer.on('pageerror', error => composerFailures.push(error.message));
            composer.on('response', response => {
                if (response.status() >= 400 && response.url().startsWith(input.origin))
                    composerFailures.push(new URL(response.url()).pathname + ':' + response.status());
            });
            await composer.waitForURL(/\/#\/email-builder\//);
            await composer.locator('table[role="presentation"] button').first().click();
            await composer.getByRole('button', {name: 'Testo', exact: true}).click();
            await composer.getByLabel('Contenuto', {exact: true}).fill(content);
            const composerSaved = composer.waitForResponse(response => new URL(response.url()).pathname
                === `/api/communications/workflows/${workflowId}/update` && response.request().method() === 'PATCH');
            await composer.getByRole('button', {name: 'Salva', exact: true}).click();
            expect((await composerSaved).status()).toBe(200);
            await expect(composer.getByText('Email salvata.', {exact: true})).toBeVisible();
            await expect.poll(async () => (await details()).automation_tree[1]?.data?.content?.includes(content)).toBe(true);
            await take('welcome-composer-content-saved', composer);
            await composer.close(); composer = null; await page.bringToFront();
            await expect(drawer.getByText(content, {exact: true})).toBeVisible();
            await closeDrawer(drawer);
            let saved = updateResponse();
            await editor().getByRole('button', {name: 'Salva', exact: true}).click();
            expect((await saved).status()).toBe(200);
            await editor().getByRole('button', {name: 'Chiudi', exact: true}).first().click();
            await expect(row().getByText('Spenta', {exact: true})).toBeVisible();
            await take('welcome-saved-disabled');
            await page.reload();
            await expect(row().getByText('Spenta', {exact: true})).toBeVisible();
            await row().getByTitle('Modifica', {exact: true}).click();
            const current = await assertEmail(false); baselineTree = current.automation_tree;
            await editor().getByText('Configura', {exact: true}).nth(1).click();
            drawer = page.getByRole('dialog', {name: 'Invia un messaggio', exact: true});
            await expect(drawer.locator('select').nth(1)).toHaveValue('me');
            await expect(drawer.locator('input[type="text"]')).toHaveValue(subject);
            await expect(drawer.getByText(content, {exact: true})).toBeVisible();
            await take('welcome-reopened-email-persists');
            await closeDrawer(drawer);
            await editor().getByRole('button', {name: 'Chiudi', exact: true}).first().click();
            await row().getByTitle('Attiva', {exact: true}).click();
            await expect(page.getByText("Vuoi attivare l'automazione?", {exact: true})).toBeVisible();
            await take('welcome-enable-confirmation');
            await page.getByRole('button', {name: 'Annulla', exact: true}).click();
            await expect(page.locator('.swal2-popup')).toBeHidden();
            await expect(row().getByText('Spenta', {exact: true})).toBeVisible();
            await assertEmail(false); proof.cancelled_enable_preserves_disabled = true;
            await take('welcome-enable-cancelled');
            await row().getByTitle('Attiva', {exact: true}).click();
            saved = updateResponse();
            await page.getByRole('button', {name: 'Attiva', exact: true}).click();
            expect((await saved).status()).toBe(200);
            await expect(row().getByText('Attiva', {exact: true})).toBeVisible();
            await assertEmail(true); proof.enabled_via_ui = true;
            await take('welcome-enabled');
            await page.reload();
            await expect(row().getByText('Attiva', {exact: true})).toBeVisible();
            await assertEmail(true); proof.enabled_persists_after_reload = true;
            await take('welcome-enabled-after-reload');
            // Actual PauseButton title is Stoppa; confirmation says Disattiva.
            await row().getByTitle('Stoppa', {exact: true}).click();
            await expect(page.getByText("Vuoi disattivare l'automazione?", {exact: true})).toBeVisible();
            await take('welcome-disable-confirmation');
            await page.getByRole('button', {name: 'Annulla', exact: true}).click();
            await expect(page.locator('.swal2-popup')).toBeHidden();
            await assertEmail(true); proof.cancelled_disable_preserves_enabled = true;
            await take('welcome-disable-cancelled');
            await row().getByTitle('Stoppa', {exact: true}).click();
            saved = updateResponse();
            await page.getByRole('button', {name: 'Disattiva', exact: true}).click();
            expect((await saved).status()).toBe(200);
            await expect(row().getByText('Spenta', {exact: true})).toBeVisible();
            await assertEmail(false); proof.disabled_via_ui = true;
            await take('welcome-disabled');
            await page.reload();
            await expect(row().getByText('Spenta', {exact: true})).toBeVisible();
            const final = await assertEmail(false); proof.disabled_persists_after_reload = true;
            expect(final.automation_tree).toEqual(baselineTree); proof.configuration_preserved_after_toggles = true;
            await take('welcome-disabled-after-reload');
            // Complete the final manual checklist in the visible editor as well
            // as through persisted backend data, after the last reload.
            await row().getByTitle('Modifica', {exact: true}).click();
            await expect(editor().locator('span').filter({hasText: /^Nuova iscrizione$/})).toBeVisible();
            await editor().getByText('Configura', {exact: true}).nth(1).click();
            drawer = page.getByRole('dialog', {name: 'Invia un messaggio', exact: true});
            await expect(drawer.locator('select').nth(1)).toHaveValue('me');
            await expect(drawer.locator('input[type="text"]')).toHaveValue(subject);
            await expect(drawer.getByText(content, {exact: true})).toBeVisible();
            await closeDrawer(drawer);
            await editor().getByRole('button', {name: 'Chiudi', exact: true}).first().click();
            await assertEmail(false); proof.configuration_reopened_after_disable = true;
            const reader = await actor('reader');
            const beforeDenials = await details();
            for (const [field, route, method, data] of [
                ['reader_update_status', `communications/workflows/${workflowId}/update`, 'PATCH', {enabled: true}],
                ['reader_delete_status', `communications/workflows/${workflowId}/delete`, 'DELETE'],
                ['reader_create_status', 'communications/workflows/add', 'POST', {name: 'Vietato', automation_tree: []}],
            ]) proof[field] = (await reader.api(route, {method, ...(data ? {data} : {})})).status();
            expect([proof.reader_update_status, proof.reader_delete_status, proof.reader_create_status]).toEqual([403, 403, 403]);
            expect(await details()).toEqual(beforeDenials); proof.reader_denials_preserve_state = true;
            expect(composerFailures).toEqual([]);
            expect(taken.size).toBe(spec.checkpoints.length);
            expect(outgoingRequests).toBe(0);
            Object.assign(proof, {
                subscription_trigger_persisted: final.automation_tree[0].value === 'new_subscription', message_count: 1,
                content_saved_via_composer: taken.has('welcome-composer-content-saved'),
                recipients_subject_content_persisted: taken.has('welcome-reopened-email-persists'),
                outgoing_requests: outgoingRequests,
                email_logs_unchanged: (await read('communications/email-logs/list')).total_count === baselineLogs.total_count,
            });
            finished = true;
        } finally {
            if (composer && !composer.isClosed()) await composer.close();
            if (workflowId) {
                const current = await details(); expect(current.name).toBe(name);
                // If a preceding check fails while active, disable only this owned
                // identifier before deleting it; no queued-delivery claim is made.
                if (current.enabled) expect((await api(`communications/workflows/${workflowId}/update`,
                    {method: 'PATCH', data: {enabled: false}})).status()).toBe(200);
                expect((await details()).enabled).toBe(false);
                expect((await api(`communications/workflows/${workflowId}/delete`, {method: 'DELETE'})).status()).toBe(200);
                proof.owned_workflow_removed = true;
            }
            context.off('request', observe);
            const after = await read('communications/workflows/list'); expect(records(after)).toEqual(records(baseline));
            proof.unrelated_workflows_unchanged = true;
        }
        if (finished) {
            expect(proof).toEqual(spec.outcome.expected); report[spec.outcome.field] = proof;
            report.checks.push('Welcome email composed and reopened; activate/deactivate and both cancellations through real UI; persisted flags and configuration; reader mutation denials; owned cleanup.');
            report.gaps = [
                {intent: 'communications.workflow.delivery', status: 'needs_external_verification',
                    reason: 'No later subscription event, worker execution, email delivery or SMTP verification was exercised.'},
                {intent: 'communications.collaborator.access', status: 'pending',
                    reason: 'Current permission registry has no workflow route mappings for custom-role collaborators; only owner and denied reader were exercised.'},
            ];
        }
    }});
