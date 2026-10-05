// Real publication/save handlers, then recipient previews closed without sending.
import {scenario, expect} from './scenario.mjs';
import {setCheckbox} from './controls.mjs';
import {communicationCompleteAuthoredWorkflows} from '../../../../docs/manuale/communication-complete-authored-workflows.mjs';
const id = 'communication-message-compose';
const spec = communicationCompleteAuthoredWorkflows[id];
await scenario({id, prefix: spec.prefix.replace(/\/$/, ''), sources: spec.sources,
    actions: async ({page, api, context, actor, open, capture, report, input}) => {
        expect(input.fixture_version).toBe(8);
        expect(input.fixture_profile || 'baseline').toBe('baseline');
        const read = async route => {
            const response = await api(route); expect(response.status()).toBe(200); return response.json();
        };
        const baseline = await read('communications/messages/list');
        const baselineIds = new Set(baseline.map(row => row.message_id));
        const baselineLogs = await read('communications/email-logs/list');
        const postContent = 'La segreteria Aurora comunica: gli orari della prossima settimana sono disponibili presso la sede.';
        const emailContent = 'Gentile iscritto, la segreteria Aurora è disponibile per le informazioni sulle attività e sugli orari della prossima settimana.';
        const subject = 'Informazioni attività Aurora';
        expect(baseline.some(row => [postContent, emailContent].includes(row.message))).toBe(false);
        const proof = {}, owned = new Map(), taken = new Set();
        let finished = false, outgoingRequests = 0;
        const observe = request => {
            const url = new URL(request.url());
            if (url.origin === input.origin && (url.pathname === '/api/communications/send/email'
                || url.pathname.endsWith('/smtp/verify'))) outgoingRequests++;
        };
        context.on('request', observe);
        const take = async checkpoint => {
            const index = spec.checkpoints.findIndex(item => item.id === checkpoint);
            expect(index >= 0 && !taken.has(checkpoint)).toBe(true);
            await expect(page.locator('[data-sonner-toast]:visible')).toHaveCount(0, {timeout: 10000});
            await capture(page, index + 1, checkpoint); taken.add(checkpoint);
        };
        const row = messageId => page.locator('.datatable-row').filter({has: page.locator(`#action-col-${messageId}`)});
        // Capture the actual identifier added by each completed UI handler. This
        // avoids guessing names or deleting an existing message on cleanup.
        const identifyCreated = async (previous, type, content, expectedSubject) => {
            const previousIds = new Set(previous.map(item => item.message_id));
            const added = (await read('communications/messages/list')).filter(item => !previousIds.has(item.message_id));
            expect(added).toHaveLength(1);
            const created = added[0];
            expect(baselineIds.has(created.message_id)).toBe(false);
            expect(created.type).toBe(type);
            owned.set(created.message_id, {type, content, subject: expectedSubject});
            // These assertions intentionally expose the old read-only write
            // serializer regression instead of accepting empty saved fields.
            expect(created.message).toBe(content);
            if (expectedSubject !== undefined) expect(created.subject).toBe(expectedSubject);
            return created;
        };
        try {
            await open('Comunicazioni', '/#/communication/messages');
            await expect(page.getByText('Messaggi', {exact: true}).first()).toBeVisible();
            for (const href of ['/#/communication/messages', '/#/communication/automation', '/#/communication/configuration'])
                await expect(page.locator(`a[href="${href}"]`).first()).toBeVisible();
            await expect(page.locator('[data-target="#addModalPost"]')).toBeVisible();
            await expect(page.locator('[data-target="#addModal"]')).toBeVisible();
            proof.communication_navigation_choices_visible = true;
            await take('communication-message-list');
            await page.locator('[data-target="#addModalPost"]').click();
            let modal = page.locator('#addModalPost');
            await expect(modal).toBeVisible();
            await modal.locator('textarea[name="message"]').fill(postContent);
            await expect(modal.getByText(/Il post sarà visibile a tutti gli atleti/)).toBeVisible();
            await take('communication-post-content');
            let previous = await read('communications/messages/list');
            let saved = page.waitForResponse(response => new URL(response.url()).pathname
                === '/api/communications/send/post' && response.request().method() === 'POST');
            await modal.getByRole('button', {name: 'Pubblica', exact: true}).click();
            expect((await saved).status()).toBe(200);
            const post = await identifyCreated(previous, 'INSIDE_APP', postContent);
            await expect(modal).toBeHidden();
            await expect(row(post.message_id)).toBeVisible();
            await expect(row(post.message_id).getByText('Post', {exact: true})).toBeVisible();
            await expect(row(post.message_id).getByTitle('Invia', {exact: true})).toBeDisabled();
            proof.post_published_via_ui = true; proof.post_send_button_disabled = true;
            await take('communication-post-published');
            await page.reload(); await expect(row(post.message_id)).toBeVisible();
            await expect(row(post.message_id).getByTitle(postContent, {exact: true})).toBeVisible();
            proof.post_content_visible_by_title = true;
            expect((await read('communications/messages/list')).find(item => item.message_id === post.message_id)).toMatchObject({type: 'INSIDE_APP', message: postContent});
            const transactions = await read(`communications/messages/${post.message_id}/detail`);
            expect(transactions).toHaveLength(1);
            expect(transactions[0]).toMatchObject({message: post.message_id, recipient: input.association_id});
            proof.post_content_persists_after_reload = true; proof.post_transaction_association_scoped = true;
            await take('communication-post-after-reload');
            await page.locator('[data-target="#addModal"]').click();
            modal = page.locator('#addModal');
            await expect(modal).toBeVisible();
            await expect(modal.locator('select[name="type"]')).toHaveValue('EMAIL');
            await expect(modal.locator('select[name="type"] option')).toHaveCount(1);
            await modal.locator('input[name="subject"]').fill(subject);
            await modal.locator('textarea[name="message"]').fill(emailContent);
            await expect(modal.locator('input[name="select"]')).not.toBeChecked();
            await expect(modal.locator('textarea[name="email"]')).toHaveCount(0);
            await take('communication-email-content');
            previous = await read('communications/messages/list');
            saved = page.waitForResponse(response => new URL(response.url()).pathname
                === '/api/communications/messages/add' && response.request().method() === 'POST');
            await modal.getByRole('button', {name: 'Salva', exact: true}).click();
            expect((await saved).status()).toBe(200);
            const email = await identifyCreated(previous, 'EMAIL', emailContent, subject);
            await expect(modal).toBeHidden();
            await expect(row(email.message_id)).toBeVisible(); proof.email_saved_via_ui = true;
            await take('communication-email-saved');
            await page.reload(); await expect(row(email.message_id)).toBeVisible();
            await expect(row(email.message_id).getByText('Email', {exact: true})).toBeVisible();
            await expect(row(email.message_id).getByTitle(subject, {exact: true})).toBeVisible();
            await expect(row(email.message_id).getByTitle(emailContent, {exact: true})).toBeVisible();
            await expect(row(post.message_id).getByText('Post', {exact: true})).toBeVisible();
            proof.email_subject_content_visible_by_title = true;
            proof.saved_message_types_distinct = true;
            expect((await read('communications/messages/list')).find(item => item.message_id === email.message_id)).toMatchObject({type: 'EMAIL', message: emailContent, subject});
            expect((await api(`communications/messages/${email.message_id}/detail`)).status()).toBe(404);
            proof.email_subject_content_persist_after_reload = true; proof.email_has_no_transactions = true;
            await take('communication-email-after-reload');
            await row(email.message_id).getByTitle('Invia', {exact: true}).click();
            modal = page.locator(`#sendModal${email.message_id}`);
            await expect(modal).toBeVisible();
            await expect(modal.getByText('Invio di un messaggio', {exact: true})).toBeVisible();
            await modal.locator('textarea[name="email"]').fill('segreteria.prova@example.test');
            await take('communication-send-recipient-preview'); proof.recipient_dialog_opened = true;
            await modal.getByRole('button', {name: 'Chiudi', exact: true}).first().click();
            await expect(modal).toBeHidden();
            expect((await api(`communications/messages/${email.message_id}/detail`)).status()).toBe(404);
            proof.recipient_dialog_cancelled = true; await take('communication-send-cancelled');
            // Show the alternative send-now path only as a preview. No request
            // is submitted, so this image is not evidence of SMTP delivery.
            await page.locator('[data-target="#addModal"]').click(); modal = page.locator('#addModal');
            await modal.locator('input[name="subject"]').fill('Anteprima invio immediato');
            await modal.locator('textarea[name="message"]').fill('Questa anteprima di prova viene chiusa senza inviare alcuna email.');
            await setCheckbox(modal.locator('input[name="select"]'), true);
            await modal.locator('textarea[name="email"]').fill('segreteria.prova@example.test');
            await expect(modal.getByRole('button', {name: 'Invia', exact: true})).toBeVisible();
            await take('communication-immediate-email-preview');
            await modal.getByRole('button', {name: 'Chiudi', exact: true}).first().click();
            await expect(modal).toBeHidden(); proof.immediate_send_preview_closed = true;
            const beforeDenials = await read('communications/messages/list');
            const reader = await actor('reader');
            for (const [field, route, method, data] of [
                ['reader_create_email_status', 'communications/messages/add', 'POST', {type: 'EMAIL', subject: 'Vietato', message: 'Questa email di prova non deve essere salvata.'}],
                ['reader_create_post_status', 'communications/send/post', 'POST', {message: 'Questo post di prova non deve essere pubblicato.'}],
                ['reader_delete_email_status', `communications/messages/${email.message_id}/delete`, 'DELETE'],
                ['reader_delete_post_status', `communications/messages/${post.message_id}/delete`, 'DELETE'],
            ]) proof[field] = (await reader.api(route, {method, ...(data ? {data} : {})})).status();
            expect([proof.reader_create_email_status, proof.reader_create_post_status,
                proof.reader_delete_email_status, proof.reader_delete_post_status]).toEqual([403, 403, 403, 403]);
            expect(await read('communications/messages/list')).toEqual(beforeDenials);
            proof.reader_denials_preserve_state = true;
            expect(taken.size).toBe(spec.checkpoints.length); expect(outgoingRequests).toBe(0);
            proof.outgoing_requests = outgoingRequests;
            proof.email_logs_unchanged = (await read('communications/email-logs/list')).total_count === baselineLogs.total_count;
            finished = true;
        } finally {
            for (const [messageId, ownership] of owned) {
                const current = (await read('communications/messages/list')).find(item => item.message_id === messageId);
                expect(current).toBeDefined(); expect(current.type).toBe(ownership.type);
                expect(baselineIds.has(messageId)).toBe(false);
                expect((await api(`communications/messages/${messageId}/delete`, {method: 'DELETE'})).status()).toBe(200);
            }
            proof.owned_messages_removed = owned.size === 2;
            context.off('request', observe);
            expect(await read('communications/messages/list')).toEqual(baseline);
            proof.unrelated_messages_unchanged = true;
        }
        if (finished) {
            expect(proof).toEqual(spec.outcome.expected); report[spec.outcome.field] = proof;
            report.checks.push('Post published and association transaction checked; email saved with exact text and subject, reloaded without transactions; both recipient forms previewed and closed; reader denials; scoped cleanup.');
            report.gaps = [
                {intent: 'communications.email.send', status: 'needs_external_verification',
                    reason: 'Both send forms were closed; no SMTP, outgoing email request, delivery or mailbox verification.'},
                {intent: 'communications.post.athlete.visibility', status: 'pending',
                    reason: 'Owner list and association-scoped transaction verified; no signed-in athlete account was exercised.'},
                {intent: 'communications.collaborator.access', status: 'pending',
                    reason: 'Current permission registry lacks messages route mappings for custom-role collaborators.'},
            ];
        }
    }});
