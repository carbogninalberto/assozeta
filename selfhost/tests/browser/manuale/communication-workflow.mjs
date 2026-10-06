// All workflow mutations use the application handlers; the rule is never enabled.
import {scenario, expect} from './scenario.mjs';
import {focusRegion, tableFocus, textFocus} from './focus.mjs';
import {communicationAuthAuthoredWorkflows} from '../../../../docs/manuale/communication-auth-authored-workflows.mjs';
// Seeded and frozen-clock rows share timestamps (or the list is unordered), so compare
// record sets: identical rows and contents, independent of physical row order.
const records = rows => (Array.isArray(rows) ? rows.map(row => JSON.stringify(row))
    : Object.entries(rows).map(entry => JSON.stringify(entry))).sort();
const id = 'communication-workflow-manage';
const spec = communicationAuthAuthoredWorkflows[id];

await scenario({id, prefix: spec.prefix.replace(/\/$/, ''), sources: spec.sources,
    actions: async ({page, api, context, open, capture, report, input}) => {
        expect(input.fixture_version).toBe(8);
        expect(input.fixture_profile || 'baseline').toBe('baseline');
        const read = async route => {
            const response = await api(route);
            expect(response.status()).toBe(200);
            return response.json();
        };
        const baseline = await read('communications/workflows/list');
        const baselineIds = baseline.map(row => row.automation_workflow_id).sort();
        const baselineLogs = await read('communications/email-logs/list');
        const name = 'Comunicazione stagione - prova manuale';
        expect(baseline.some(row => row.name === name)).toBe(false);
        let workflowId;
        let finished = false;
        const proof = {};
        let composer;
        let outgoingRequests = 0;
        let enabledRequests = 0;
        const browserFailures = [];
        const observeRequest = request => {
            const url = new URL(request.url());
            if (url.origin !== input.origin || !url.pathname.startsWith('/api/communications/')) return;
            if (url.pathname.startsWith('/api/communications/send/') || url.pathname.endsWith('/smtp/verify')) outgoingRequests++;
            if (request.method() === 'PATCH' || request.method() === 'POST') {
                try { if (request.postDataJSON()?.enabled === true) enabledRequests++; } catch { /* non-JSON */ }
            }
        };
        context.on('request', observeRequest);
        const checkpoints = new Set();
        const take = async (checkpoint, target = page) => {
            const index = spec.checkpoints.findIndex(item => item.id === checkpoint);
            expect(index >= 0 && !checkpoints.has(checkpoint)).toBe(true);
            await expect(target.locator('[data-sonner-toast]:visible')).toHaveCount(0, {timeout: 10000});
            let focus;
            const drawer = target.locator('[role="dialog"]:visible');
            if (await drawer.count() === 1) focus = drawer;
            else if (checkpoint === 'workflow-list-before-creation') {
                focus = focusRegion(target.getByRole('heading', {name: /^Automazioni/}),
                    target.getByRole('textbox', {name: 'Cerca nella tabella', exact: true}),
                    textFocus(target.getByText('Nessun dato disponibile', {exact: true})));
            } else if (checkpoint === 'workflow-saved-disabled-list') {
                focus = tableFocus(target, ['name', 'enabled'], target.locator('[data-row]').filter({hasText: name}));
            } else if (checkpoint === 'workflow-editor-with-name') {
                focus = focusRegion(target.locator('#automation-name'),
                    target.getByText('Configura', {exact: true}).first().locator('..'));
            } else if (checkpoint === 'workflow-email-content-saved') {
                focus = focusRegion(target.locator('table[role="presentation"]').filter({hasText: await target.getByLabel('Contenuto', {exact: true}).inputValue()}).last(), target.getByLabel('Contenuto', {exact: true}));
            }
            await capture(target, index + 1, checkpoint, focus);
            checkpoints.add(checkpoint);
        };
        const details = () => read(`communications/workflows/${workflowId}/details`);
        const editor = () => page.locator('#portal-elements').filter({has: page.locator('#automation-name')});
        const closeDrawerAndSave = async drawer => {
            const saved = page.waitForResponse(response => {
                const route = new URL(response.url()).pathname;
                return route === '/api/communications/workflows/add' && response.request().method() === 'POST'
                    || workflowId && route === `/api/communications/workflows/${workflowId}/update`
                    && response.request().method() === 'PATCH';
            });
            await drawer.locator('.drawer-header button').click();
            const response = await saved;
            expect(response.status()).toBe(200);
            const body = await response.json();
            if (!workflowId) workflowId = body.workflow_id;
            expect(typeof workflowId === 'string').toBe(true);
            proof.created_via_ui = true;
            await expect(drawer).toHaveCount(0);
            expect((await details()).enabled).toBe(false);
        };
        const configureEmail = async (nodeIndex, subject, content, checkpoint, captureComposer = false) => {
            await editor().getByText('Configura', {exact: true}).nth(nodeIndex).click();
            const drawer = page.getByRole('dialog', {name: 'Invia un messaggio', exact: true});
            await expect(drawer).toBeVisible();
            await drawer.locator('select').nth(0).selectOption('email');
            await drawer.locator('select').nth(1).selectOption('me');
            await drawer.locator('input[type="text"]').fill(subject);
            await take(checkpoint);
            const opened = context.waitForEvent('page');
            await drawer.getByRole('button', {name: 'Crea email', exact: true}).click();
            composer = await opened;
            composer.on('pageerror', () => browserFailures.push('Email composer page error'));
            composer.on('response', response => {
                if (response.status() >= 400 && response.url().startsWith(input.origin))
                    browserFailures.push(new URL(response.url()).pathname + ':' + response.status());
            });
            await composer.waitForURL(/\/#\/email-builder\//);
            await expect(composer.getByRole('button', {name: 'Salva', exact: true})).toBeVisible();
            // The empty canvas has an add-block button; Testo opens its actual editor.
            await composer.locator('table[role="presentation"] button').first().click();
            await composer.getByRole('button', {name: 'Testo', exact: true}).click();
            await composer.getByLabel('Contenuto', {exact: true}).fill(content);
            const saved = composer.waitForResponse(response => new URL(response.url()).pathname
                === `/api/communications/workflows/${workflowId}/update` && response.request().method() === 'PATCH');
            await composer.getByRole('button', {name: 'Salva', exact: true}).click();
            expect((await saved).status()).toBe(200);
            await expect(composer.getByText('Email salvata.', {exact: true})).toBeVisible();
            await expect.poll(async () => (await details()).automation_tree[nodeIndex]?.data?.content?.includes(content)).toBe(true);
            if (captureComposer) await take('workflow-email-content-saved', composer);
            await composer.close(); composer = null;
            await page.bringToFront();
            // The composer's real BroadcastChannel refreshes the saved node.
            await expect(drawer.getByText(content, {exact: true})).toBeVisible();
            await closeDrawerAndSave(drawer);
        };
        try {
            await open('Comunicazioni', '/#/communication/automation');
            await expect(page).toHaveURL(input.origin + '/#/communication/automation');
            await expect(page.getByRole('heading', {name: /^Automazioni/})).toBeVisible();
            await expect(page.locator('.datatable-loading')).toHaveCount(0);
            await expect(page.locator('.datatable-body')).toBeVisible();
            await take('workflow-list-before-creation');
            await page.getByText('Automazione', {exact: true}).click();
            await expect(page.locator('#automation-name')).toBeVisible();
            await page.locator('#automation-name').fill(name);
            await page.locator('#automation-name').press('End'); // the name handler uses keyup
            await take('workflow-editor-with-name');
            await editor().getByText('Configura', {exact: true}).first().click();
            let drawer = page.getByRole('dialog', {name: 'Evento di attivazione', exact: true});
            await drawer.locator('select').first().selectOption('cron');
            const futureDate = new Date(input.reference_date + 'T12:00:00Z');
            futureDate.setUTCDate(futureDate.getUTCDate() + 30);
            const future = futureDate.toISOString().slice(0, 10) + 'T12:00';
            await drawer.locator('input[type="datetime-local"]').fill(future);
            await drawer.locator('select').nth(1).selectOption('approved');
            await take('workflow-trigger-future-date');
            await closeDrawerAndSave(drawer);
            let persisted = await details();
            expect(persisted.automation_tree[0]).toMatchObject({id: 'trigger', value: 'cron', data: {time: future, target: 'approved'}});
            proof.future_trigger_persisted = persisted.automation_tree[0].data.time === future;
            await editor().getByText('Configura', {exact: true}).first().click();
            drawer = page.getByRole('dialog', {name: 'Evento di attivazione', exact: true});
            await drawer.locator('select').first().selectOption('new_subscription');
            await take('workflow-trigger-new-subscription');
            await closeDrawerAndSave(drawer);
            expect((await details()).automation_tree[0].value).toBe('new_subscription');
            await editor().getByText('Azione Messaggio', {exact: true}).click();
            let firstSubject = 'Informazioni per la nuova iscrizione';
            const firstContent = 'Benvenuto nell’Associazione Sportiva Aurora. La segreteria ti accompagnerà nei prossimi passi.';
            await configureEmail(1, firstSubject, firstContent, 'workflow-first-email-configured', true);
            await editor().getByText('Rimani in Attesa', {exact: true}).click();
            await editor().getByText('Configura', {exact: true}).nth(2).click();
            drawer = page.getByRole('dialog').filter({has: page.getByRole('heading', {name: 'Rimani in attesa', exact: true})});
            await drawer.locator('input[type="number"]').fill('2');
            await drawer.locator('select').selectOption('days');
            await take('workflow-wait-two-days');
            await closeDrawerAndSave(drawer);
            await editor().getByText('Azione Messaggio', {exact: true}).click();
            const secondSubject = 'I prossimi passi per la tua iscrizione';
            const secondContent = 'Questa è una seconda comunicazione di prova. Contatta la segreteria per le informazioni sulle attività.';
            await configureEmail(3, secondSubject, secondContent, 'workflow-second-email-configured');
            const saved = page.waitForResponse(response => new URL(response.url()).pathname
                === `/api/communications/workflows/${workflowId}/update` && response.request().method() === 'PATCH');
            await editor().getByRole('button', {name: 'Salva', exact: true}).click();
            expect((await saved).status()).toBe(200);
            await editor().getByRole('button', {name: 'Chiudi', exact: true}).first().click();
            const row = page.locator('.datatable-body .datatable-row').filter({hasText: name});
            await expect(row.getByText('Spenta', {exact: true})).toBeVisible();
            await take('workflow-saved-disabled-list');
            await page.reload();
            await expect(row.getByText('Spenta', {exact: true})).toBeVisible();
            await row.getByTitle('Modifica', {exact: true}).click();
            await expect(page.locator('#automation-name')).toHaveText(name);
            persisted = await details();
            expect(persisted.enabled).toBe(false);
            expect(persisted.automation_tree.map(node => node.id)).toEqual(['trigger', 'message', 'wait', 'message']);
            expect(persisted.automation_tree[0].value).toBe('new_subscription');
            expect(persisted.automation_tree[1].data).toMatchObject({recipients: 'me', subject: firstSubject});
            expect(persisted.automation_tree[1].data.content.includes(firstContent)).toBe(true);
            expect(persisted.automation_tree[2].data).toMatchObject({amount: 2, type: 'days'});
            expect(persisted.automation_tree[3].data).toMatchObject({recipients: 'me', subject: secondSubject});
            expect(persisted.automation_tree[3].data.content.includes(secondContent)).toBe(true);
            await editor().getByText('Configura', {exact: true}).nth(1).click();
            drawer = page.getByRole('dialog', {name: 'Invia un messaggio', exact: true});
            await expect(drawer.locator('select').nth(1)).toHaveValue('me');
            await expect(drawer.locator('input[type="text"]')).toHaveValue(firstSubject);
            await expect(drawer.getByText(firstContent, {exact: true})).toBeVisible();
            await take('workflow-reopened-first-email');
            await closeDrawerAndSave(drawer);
            await editor().getByText('Configura', {exact: true}).nth(2).click();
            drawer = page.getByRole('dialog').filter({has: page.getByRole('heading', {name: 'Rimani in attesa', exact: true})});
            await expect(drawer.locator('input[type="number"]')).toHaveValue('2');
            await expect(drawer.locator('select')).toHaveValue('days');
            await take('workflow-reopened-wait');
            await closeDrawerAndSave(drawer);
            // Change a saved, still-disabled rule through the actual drawer.
            await editor().getByText('Configura', {exact: true}).nth(1).click();
            drawer = page.getByRole('dialog', {name: 'Invia un messaggio', exact: true});
            firstSubject = 'Informazioni aggiornate per la nuova iscrizione';
            await drawer.locator('input[type="text"]').fill(firstSubject);
            await take('workflow-existing-message-edited');
            await closeDrawerAndSave(drawer);
            const finalSave = page.waitForResponse(response => new URL(response.url()).pathname
                === `/api/communications/workflows/${workflowId}/update` && response.request().method() === 'PATCH');
            await editor().getByRole('button', {name: 'Salva', exact: true}).click();
            expect((await finalSave).status()).toBe(200);
            await editor().getByRole('button', {name: 'Chiudi', exact: true}).first().click();
            await page.reload();
            await expect(row.getByText('Spenta', {exact: true})).toBeVisible();
            await row.getByTitle('Modifica', {exact: true}).click();
            await editor().getByText('Configura', {exact: true}).nth(1).click();
            drawer = page.getByRole('dialog', {name: 'Invia un messaggio', exact: true});
            await expect(drawer.locator('input[type="text"]')).toHaveValue(firstSubject);
            await expect(drawer.locator('select').nth(1)).toHaveValue('me');
            await expect(drawer.getByText(firstContent, {exact: true})).toBeVisible();
            await take('workflow-existing-message-edit-persists');
            await closeDrawerAndSave(drawer);
            persisted = await details();
            expect(persisted.enabled).toBe(false);
            expect(persisted.automation_tree[1].data.subject).toBe(firstSubject);
            proof.existing_message_changed_via_ui = checkpoints.has('workflow-existing-message-edited');
            proof.existing_message_change_persisted = persisted.automation_tree[1].data.subject === firstSubject
                && checkpoints.has('workflow-existing-message-edit-persists');
            expect(checkpoints.size).toBe(spec.checkpoints.length);
            expect(outgoingRequests).toBe(0);
            expect(enabledRequests).toBe(0);
            expect(browserFailures).toEqual([]);
            expect((await read('communications/email-logs/list')).total_count).toBe(baselineLogs.total_count);
            Object.assign(proof, {
                disabled_throughout: !persisted.enabled && enabledRequests === 0,
                subscription_trigger_persisted: persisted.automation_tree[0].value === 'new_subscription',
                message_count: persisted.automation_tree.filter(node => node.id === 'message').length,
                email_content_saved_via_composer: persisted.automation_tree[1].data.content.includes(firstContent)
                    && persisted.automation_tree[3].data.content.includes(secondContent),
                recipients_and_subjects_persisted: persisted.automation_tree[1].data.recipients === 'me'
                    && persisted.automation_tree[1].data.subject === firstSubject
                    && persisted.automation_tree[3].data.recipients === 'me'
                    && persisted.automation_tree[3].data.subject === secondSubject,
                wait_days: persisted.automation_tree[2].data.amount,
                node_order_persisted: persisted.automation_tree.map(node => node.id).join(',') === 'trigger,message,wait,message',
                reloaded_and_reopened: checkpoints.has('workflow-reopened-first-email') && checkpoints.has('workflow-reopened-wait'),
                outgoing_requests: outgoingRequests,
                email_logs_unchanged: (await read('communications/email-logs/list')).total_count === baselineLogs.total_count,
            });
            finished = true;
        } finally {
            if (composer && !composer.isClosed()) await composer.close();
            // Delete only the identifier returned by this UI creation.
            if (workflowId) {
                const current = await details();
                expect(current.enabled).toBe(false);
                expect(current.name).toBe(name);
                const removed = await api(`communications/workflows/${workflowId}/delete`, {method: 'DELETE'});
                expect(removed.status()).toBe(200);
                proof.owned_workflow_removed = removed.status() === 200;
            }
            context.off('request', observeRequest);
            const after = await read('communications/workflows/list');
            expect(after.map(row => row.automation_workflow_id).sort()).toEqual(baselineIds);
            expect(records(after)).toEqual(records(baseline));
            proof.unrelated_workflows_unchanged = JSON.stringify(records(after)) === JSON.stringify(records(baseline));
        }
        if (finished) {
            expect(proof).toEqual(spec.outcome.expected);
            report[spec.outcome.field] = proof;
            report.checks.push('Disabled workflow created and edited through UI; two composer saves, trigger and wait persisted after reload; scoped removal; no activation or outgoing request.');
            report.gaps = [
                {intent: 'communications.workflow.enable', status: 'pending', reason: 'Activation was not exercised: the email rule remained disabled.'},
                {intent: 'communications.workflow.delivery', status: 'needs_external_verification', reason: 'No email delivery, scheduled execution or elapsed wait was exercised.'},
            ];
        }
    }});
