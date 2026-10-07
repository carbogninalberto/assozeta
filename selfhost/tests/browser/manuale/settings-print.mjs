import {setCheckbox} from './controls.mjs';
import crypto from 'node:crypto';
import fs from 'node:fs';
import path from 'node:path';
import {scenario, expect} from './scenario.mjs';
import {reloadOrganizationProfile} from './organization-access-sources.mjs';
import {settingsPrintAuthoredWorkflows} from '../../../../docs/manuale/settings-print-authored-workflows.mjs';

const id = 'settings-print';
const spec = settingsPrintAuthoredWorkflows[id];
const signatureSvg = '<svg xmlns="http://www.w3.org/2000/svg" width="280" height="80" viewBox="0 0 280 80"><path d="M15 48 Q45 8 65 48 T105 48 T155 48 T205 48" fill="none" stroke="#224477" stroke-width="3"/><text x="15" y="72" font-size="12" fill="#224477">FIRMA DIMOSTRATIVA</text></svg>';
const stampSvg = '<svg xmlns="http://www.w3.org/2000/svg" width="260" height="80" viewBox="0 0 260 80"><rect x="8" y="8" width="244" height="64" rx="8" fill="none" stroke="#224477" stroke-width="3"/><text x="24" y="46" font-family="sans-serif" font-size="16" fill="#224477">TIMBRO DIMOSTRATIVO</text></svg>';
const dataUri = svg => 'data:image/svg+xml;base64,' + Buffer.from(svg).toString('base64');
const signatureUpload = {name: 'firma-dimostrativa.svg', mimeType: 'image/svg+xml', buffer: Buffer.from(signatureSvg)};
const stampUpload = {name: 'timbro-dimostrativo.svg', mimeType: 'image/svg+xml', buffer: Buffer.from(stampSvg)};
const headerText = 'Associazione Aurora - intestazione dimostrativa';
const footerText = 'Documento dimostrativo - segreteria Aurora';
const digest = bytes => crypto.createHash('sha256').update(bytes).digest('hex');

await scenario({id, prefix: spec.prefix.replace(/\/$/, ''), sources: spec.sources,
    actions: async ({page, api, context, open, actor, input, capture, report}) => {
        expect(input.fixture_version).toBe(8);
        expect(input.fixture_profile || 'baseline').toBe('baseline');
        const read = async route => {
            const response = await api(route);
            expect(response.status()).toBe(200);
            return response.json();
        };
        const profile = async () => (await read('profile/info')).user_data;
        const payments = async () => Object.values((await read('payment/list?pagination[perpage]=100')).data).sort((a,b)=>a.payment_id.localeCompare(b.payment_id));
        const invoice = async uuid => (await read('invoice/list?query[invoice_id]=' + uuid)).data.invoice;
        const take = async (checkpoint, focus, options) => {
            const index = spec.checkpoints.findIndex(item => item.id === checkpoint);
            expect(index).toBeGreaterThanOrEqual(0);
            await expect(page.locator('[data-sonner-toast]:visible')).toHaveCount(0, {timeout: 15000});
            await capture(page, index + 1, checkpoint, focus, options);
        };
        const original = await profile();
        const baselinePayments = await payments();
        const originalPrint = original.sport_association;
        const effectiveTemplate = originalPrint.invoice_template || 'invoice.html';
        expect(['invoice.html', 'invoice_classic.html']).toContain(effectiveTemplate);
        const originalPayload = {user_data: {first_name: original.first_name, last_name: original.last_name,
            username: original.username, email: original.email, avatar_image: null, sport_association: originalPrint}};
        const ownedPaymentIds = new Set();
        const ownedInvoiceIds = new Set();
        const ownedDocumentIds = new Set();
        report.print_pdfs = [];
        let drawnSignature;
        let earlierPdf;
        let originalError;
        const openAccount = async () => {
            await open('Impostazioni', '/#/profile');
            await page.getByText('Informazioni Account', {exact: true}).click();
            await expect(page.locator('#bkn_form_account_update')).toBeVisible();
        };
        const signatureBlock = () => page.locator('.form-group.row').filter({has: page.getByText('Firma del presidente', {exact: true})});
        const stampBlock = () => page.locator('.form-group.row').filter({has: page.getByText('Timbro', {exact: true})});
        const assertAssetsLoad = async () => {
            for (const block of [signatureBlock(), stampBlock()]) {
                await expect(block.locator('img')).toBeVisible();
                await expect.poll(() => block.locator('img').evaluate(image => image.complete && image.naturalWidth > 0)).toBe(true);
            }
        };
        const editorBlock = label => page.getByRole('heading', {name: label, exact: true})
            .locator('xpath=ancestor::div[contains(@class,"row")][1]/following-sibling::div[1]');
        const headerEditor = () => editorBlock('Intestazione stampe').locator('[contenteditable="true"]');
        const footerEditor = () => editorBlock('Piè di pagina stampe').locator('[contenteditable="true"]');
        const saveAccount = async () => {
            const response = page.waitForResponse(response => new URL(response.url()).pathname === '/api/profile/update'
                && response.request().method() === 'PATCH');
            await page.locator('#bkn_form_account_update_submit').click();
            const saved = await response;
            expect(saved.status()).toBe(200);
            await expect(page.locator('#bkn_form_account_update_submit')).toBeDisabled();
            await reloadOrganizationProfile({page, api, context, expect});
            await expect(page.locator('#bkn_form_account_update')).toBeVisible();
            return saved.request().postDataJSON().user_data.sport_association;
        };
        const assertTemplatesPreserved = association => {
            expect(association.invoice_template).toBe(originalPrint.invoice_template);
            expect(association.subscription_template).toBe(originalPrint.subscription_template);
        };
        const openReceipts = async () => {
            await page.getByText('Documenti fiscali', {exact: true}).first().click();
            await page.getByText('Ricevute', {exact: true}).first().click();
            await expect(page).toHaveURL(/#\/invoice\/list$/);
            await page.getByRole('textbox', {name: 'Anno ricevute', exact: true}).click();
            await page.getByText('Filtra Anno', {exact: true}).click();
        };
        const createPrintReceipt = async (description, approvalCheckpoint) => {
            await open('Pagamenti', '/#/payment/list');
            await page.getByRole('button', {name: 'Pagamento', exact: true}).click();
            const modal = page.getByRole('dialog', {name: 'Crea Nuovo Pagamento', exact: true});
            await expect(modal.locator('#payment_form')).toBeVisible();
            await modal.getByPlaceholder('Inserisci una descrizione').fill(description);
            await modal.locator('input[name="amount"]').fill('10,00');
            await modal.getByRole('textbox', {name: 'Intestato a', exact: true}).fill('Giulia');
            await modal.getByText('Giulia Bianchi', {exact: true}).click();
            await modal.getByRole('textbox', {name: 'Causale', exact: true}).click();
            await modal.getByText('Quote e attività associative', {exact: true}).click();
            const creation = page.waitForResponse(response => new URL(response.url()).pathname === '/api/payment/add'
                && response.request().method() === 'POST');
            await modal.getByRole('button', {name: 'Crea', exact: true}).click();
            const createdResponse = await creation;
            const created = await createdResponse.json();
            if (created.payment_id) ownedPaymentIds.add(created.payment_id);
            expect(createdResponse.status()).toBe(201); expect(created.payment_id).toBeTruthy();
            expect(created.paid).toBe(false); expect(created.invoice).toBeNull();
            expect(Number(created.amount)).toBe(10);
            const details = page.locator('.drawer').filter({hasText: 'Dettagli Pagamento'});
            await expect(details).toBeVisible(); await details.locator('button.close').click();
            await expect(details).not.toBeVisible();
            const row = page.locator('[data-row]').filter({hasText: description});
            await row.locator('button:is([title="Segna come pagato"],[data-original-title="Segna come pagato"])').click();
            const approval = page.getByRole('dialog', {name: 'Incassare il pagamento?', exact: true});
            await setCheckbox(approval.locator('input[id^="generate_invoice_"]'),true);
            await setCheckbox(approval.locator('input[id^="send_receipt_email_"]'),false);
            await approval.locator('input[name="payment_date"]').fill(input.reference_date);
            if (approvalCheckpoint) await take(approvalCheckpoint, approval);
            const paidResponse = page.waitForResponse(response => new URL(response.url()).pathname === `/api/payment/${created.payment_id}/approve`
                && response.request().method() === 'POST');
            await approval.getByRole('button', {name: 'Incassa', exact: true}).click();
            const saved = await paidResponse;
            const paid = (await saved.json()).data.payment;
            if (paid.invoice?.invoice_id) ownedInvoiceIds.add(paid.invoice.invoice_id);
            expect(saved.status()).toBe(200);
            expect(saved.request().postDataJSON()).toMatchObject({generate_invoice: true, send_receipt_email: false,
                payment_date: input.reference_date});
            expect(paid.paid).toBe(true); expect(paid.invoice?.invoice_id).toBeTruthy();
            await expect(details).toBeVisible(); await details.locator('button.close').click();
            await expect(details).not.toBeVisible();
            await expect.poll(async () => {
                const receipt = await invoice(paid.invoice.invoice_id);
                if (receipt.document_pdf) ownedDocumentIds.add(receipt.document_pdf);
                return Boolean(receipt.document_pdf);
            }, {timeout: 90000}).toBe(true);
            return invoice(paid.invoice.invoice_id);
        };
        const retrievePdf = async receipt => {
            const response = await api(`document/retrieve/${receipt.document_pdf}?download=true&token=${receipt.document_token}`);
            expect(response.status()).toBe(200); expect(response.headers()['content-type']).toContain('application/pdf');
            const bytes = await response.body();
            expect(bytes.subarray(0, 5).toString()).toBe('%PDF-');
            return bytes;
        };
        const previewPdf = async (receipt, name, checkpoint) => {
            const bytes = await retrievePdf(receipt);
            const relative = 'receipt-pdfs/settings-print/' + name + '.pdf';
            const file = path.join(process.env.ASSOZETA_MANUAL_RUN, relative);
            fs.mkdirSync(path.dirname(file), {recursive: true}); fs.writeFileSync(file, bytes);
            report.print_pdfs.push({path: relative, bytes: bytes.length, sha256: digest(bytes),
                effective_template: effectiveTemplate, kind: name});
            await openReceipts();
            const previewResponse = page.waitForResponse(response => {
                const url = new URL(response.url());
                return url.pathname === '/api/document/retrieve/' + receipt.document_pdf && url.searchParams.get('download') === 'false';
            });
            await page.locator(`button:is([title="Ricevuta n.${receipt.number}"],[data-original-title="Ricevuta n.${receipt.number}"])`).click();
            const preview = page.getByRole('dialog', {name: `Ricevuta n.${receipt.number}`, exact: true});
            await expect(preview.locator('iframe')).toBeVisible();
            const result = await previewResponse;
            expect(result.status()).toBe(200);
            expect(result.headers()['content-type']).toContain('application/pdf');
            const source = new URL(await preview.locator('iframe').getAttribute('src'), input.origin);
            expect(source.origin).toBe(input.origin);
            expect(source.pathname).toBe('/api/document/retrieve/' + receipt.document_pdf);
            expect(source.searchParams.get('download')).toBe('false');
            const actualFile = await page.context().request.get(source.href);
            expect(actualFile.status()).toBe(200);
            expect(actualFile.headers()['content-type']).toContain('application/pdf');
            expect(digest(await actualFile.body())).toBe(digest(bytes));
            await take(checkpoint, preview.locator('iframe'), {mask: [page.locator('.modal:visible input[type="text"]')],
                redactionReason: 'receipt capability link excluded from crop and masked in the Full HD master'});
            await preview.getByRole('button', {name: 'Chiudi', exact: true}).first().click();
            return bytes;
        };
        // Attach authentication solely to the actual same-origin renderer URL;
        // continue to the real server without substituting any HTML or response.
        const rendererPattern = input.origin + '/api/document/invoice/*/view/';
        const subscriptionRendererPattern = input.origin + '/api/document/subscription/*/view/';
        const authenticateRenderer = route => route.fallback({headers: {...route.request().headers(),
            Authorization: 'Bearer ' + input.token}});
        await page.route(rendererPattern, authenticateRenderer);
        await page.route(subscriptionRendererPattern, authenticateRenderer);
        const inspectRenderer = async (receipt, blank) => {
            const url = `/api/document/invoice/${receipt.invoice_id}/view/`;
            const response = await page.goto(input.origin + url);
            expect(response.status()).toBe(200);
            await expect(page.locator('.content-wrapper')).toContainText(/Giulia/i);
            const html = await response.text();
            expect(html).not.toContain(dataUri(signatureSvg)); expect(html).not.toContain(dataUri(stampSvg));
            expect(html).not.toContain(drawnSignature);
            if (blank) {
                await expect(page.locator('.divHeader')).toHaveCount(0); await expect(page.locator('.divFooter')).toHaveCount(0);
                expect(html).not.toContain(headerText); expect(html).not.toContain(footerText);
                await take('blank-receipt-without-header-footer', page.locator('.content-wrapper'));
            } else {
                await expect(page.locator('.divHeader')).toHaveText(headerText);
                await expect(page.locator('.divFooter')).toHaveText(footerText);
                const geometry = await page.evaluate(() => {
                    const header = document.querySelector('.divHeader').getBoundingClientRect();
                    const footer = document.querySelector('.divFooter').getBoundingClientRect();
                    const content = document.querySelector('.content-wrapper').getBoundingClientRect();
                    return {header_bottom: header.bottom, content_top: content.top,
                        content_bottom: content.bottom, footer_top: footer.top};
                });
                expect(geometry.header_bottom).toBeLessThanOrEqual(geometry.content_top);
                expect(geometry.content_bottom).toBeLessThanOrEqual(geometry.footer_top);
                await take('new-receipt-header-content', page.locator('.divHeader'));
                await take('new-receipt-footer-content', page.locator('.divFooter'));
            }
            await page.goto(input.origin + '/#/');
            await expect(page.getByText('Organizzazione', {exact: true})).toBeVisible();
        };
        try {
            await openAccount();
            await signatureBlock().scrollIntoViewIfNeeded();
            await take('organization-print-fields-before-change');
            await page.locator('#signature').setInputFiles(signatureUpload);
            await page.locator('#stamp').setInputFiles(stampUpload);
            await expect(signatureBlock().locator('img')).toHaveAttribute('src', dataUri(signatureSvg));
            await expect(stampBlock().locator('img')).toHaveAttribute('src', dataUri(stampSvg));
            await assertAssetsLoad();
            await signatureBlock().scrollIntoViewIfNeeded(); await take('uploaded-print-assets-before-save');
            const uploaded = await saveAccount();
            expect(uploaded.president_signature).toBe(dataUri(signatureSvg)); expect(uploaded.stamp).toBe(dataUri(stampSvg));
            let saved = (await profile()).sport_association;
            expect(saved.president_signature).toBe(dataUri(signatureSvg)); expect(saved.stamp).toBe(dataUri(stampSvg));
            await expect(signatureBlock().locator('img')).toHaveAttribute('src', saved.president_signature);
            await expect(stampBlock().locator('img')).toHaveAttribute('src', saved.stamp);
            await assertAssetsLoad();
            await signatureBlock().scrollIntoViewIfNeeded(); await take('uploaded-print-assets-after-reload');
            await page.getByRole('button', {name: 'Scrivi firma', exact: true}).click();
            const signatureModal = page.locator('#signature-modal-president-signature-settings');
            const canvas = signatureModal.locator('canvas');
            await expect(canvas).toBeVisible();
            await expect.poll(() => canvas.evaluate(element => element.width > 0 && element.height > 0)).toBe(true);
            await canvas.scrollIntoViewIfNeeded();
            const box = await canvas.boundingBox();
            await page.mouse.move(box.x + box.width * .15, box.y + box.height * .5); await page.mouse.down();
            for (let index = 1; index <= 32; index++)
                await page.mouse.move(box.x + box.width * (.15 + index * .018), box.y + box.height * (.5 + Math.sin(index * .6) * .15));
            await page.mouse.up();
            expect(await canvas.evaluate(element => Array.from(element.getContext('2d')
                .getImageData(0, 0, element.width, element.height).data).some((value, index) => index % 4 === 3 && value > 0))).toBe(true);
            await take('drawn-president-signature-before-accepting', signatureModal.locator('.modal-content'));
            drawnSignature = await canvas.evaluate(element => element.toDataURL());
            await signatureModal.getByRole('button', {name: 'Firma', exact: true}).click();
            await expect(signatureModal).not.toBeVisible();
            await expect(signatureBlock().locator('img')).toHaveAttribute('src', drawnSignature);
            const drawn = await saveAccount(); expect(drawn.president_signature).toBe(drawnSignature);
            saved = (await profile()).sport_association;
            expect(saved.president_signature).toBe(drawnSignature); expect(saved.stamp).toBe(dataUri(stampSvg));
            await expect(signatureBlock().locator('img')).toHaveAttribute('src', drawnSignature);
            await assertAssetsLoad();
            await signatureBlock().scrollIntoViewIfNeeded(); await take('drawn-signature-after-reload');
            await headerEditor().fill(headerText); await footerEditor().fill(footerText);
            await take('print-header-before-save', editorBlock('Intestazione stampe'));
            await take('print-footer-before-save', editorBlock('Piè di pagina stampe'));
            const texts = await saveAccount();
            expect(texts.document_header).toBe('<p>' + headerText + '</p>');
            expect(texts.invoice_footer).toBe('<p>' + footerText + '</p>');
            saved = (await profile()).sport_association;
            expect(saved.document_header).toBe(texts.document_header); expect(saved.invoice_footer).toBe(texts.invoice_footer);
            assertTemplatesPreserved(saved);
            await expect(headerEditor()).toHaveText(headerText); await expect(footerEditor()).toHaveText(footerText);
            await take('print-header-after-reload', editorBlock('Intestazione stampe'));
            await take('print-footer-after-reload', editorBlock('Piè di pagina stampe'));
            const reader = await actor('reader');
            await reader.open('Impostazioni', '/#/profile');
            await reader.page.getByText('Informazioni Account', {exact: true}).click();
            await expect(reader.page.locator('.ProseMirror')).toHaveCount(0);
            await expect(reader.page.getByText(headerText, {exact: true})).toBeVisible();
            expect((await reader.api('profile/update', {method: 'PATCH', data: {user_data: {...originalPayload.user_data,
                sport_association: {...saved, president_signature: null, stamp: null, document_header: '<p>Scrittura negata</p>', invoice_footer: null}}}})).status()).toBe(403);
            expect((await profile()).sport_association).toEqual(saved);
            const receipt = await createPrintReceipt('Stampa dimostrativa con intestazione', 'owned-print-payment-email-disabled');
            await inspectRenderer(receipt, false);
            earlierPdf = {receipt, bytes: await previewPdf(receipt, 'with-print-texts', 'real-new-receipt-pdf')};
            await openAccount(); await headerEditor().fill(''); await footerEditor().fill('');
            await saveAccount();
            saved = (await profile()).sport_association;
            expect(['', '<p></p>', '<p><br></p>']).toContain(saved.document_header);
            expect(['', '<p></p>', '<p><br></p>']).toContain(saved.invoice_footer);
            await expect(headerEditor()).toHaveText(''); await expect(footerEditor()).toHaveText('');
            await take('blank-print-texts-after-reload', editorBlock('Piè di pagina stampe'));
            const blankReceipt = await createPrintReceipt('Stampa dimostrativa senza intestazione');
            await inspectRenderer(blankReceipt, true);
            await previewPdf(blankReceipt, 'without-print-texts', 'blank-receipt-pdf');
            expect(digest(await retrievePdf(earlierPdf.receipt))).toBe(digest(earlierPdf.bytes));
            expect((await invoice(earlierPdf.receipt.invoice_id)).document_pdf).toBe(earlierPdf.receipt.document_pdf);
            await openAccount();
            await page.getByRole('button', {name: 'Rimuovi firma', exact: true}).click();
            await page.getByRole('button', {name: 'Rimuovi timbro', exact: true}).click();
            const removed = await saveAccount(); expect(removed.president_signature).toBeNull(); expect(removed.stamp).toBeNull();
            saved = (await profile()).sport_association;
            expect(saved.president_signature).toBeNull(); expect(saved.stamp).toBeNull(); assertTemplatesPreserved(saved);
            await expect(signatureBlock().locator('img')).toHaveCount(0); await expect(stampBlock().locator('img')).toHaveCount(0);
            await signatureBlock().scrollIntoViewIfNeeded(); await take('print-assets-removed-after-reload');
            // Exercise both independent template selectors and their real server renderers.
            // Existing PDFs are deliberately not regenerated by changing a preference.
            const rendererDigests = {};
            for (const [variant, invoiceTemplate, subscriptionTemplate] of [
                ['classic', 'invoice_classic.html', 'subscription_classic.html'],
                ['standard', 'invoice.html', 'subscription.html'],
            ]) {
                await openAccount();
                await setCheckbox(page.locator(`input[name="invoice_template"][value="${invoiceTemplate}"]`),true);
                await setCheckbox(page.locator(`input[name="subscription_template"][value="${subscriptionTemplate}"]`),true);
                await page.getByRole('heading', {name: 'Modello Ricevute', exact: true}).scrollIntoViewIfNeeded();
                await take('document-templates-' + variant + '-before-save');
                await saveAccount();
                const chosen = (await profile()).sport_association;
                expect(chosen.invoice_template).toBe(invoiceTemplate); expect(chosen.subscription_template).toBe(subscriptionTemplate);
                await expect(page.locator(`input[name="invoice_template"][value="${invoiceTemplate}"]`)).toBeChecked();
                await expect(page.locator(`input[name="subscription_template"][value="${subscriptionTemplate}"]`)).toBeChecked();
                await page.getByRole('heading', {name: 'Modello Ricevute', exact: true}).scrollIntoViewIfNeeded();
                await take('document-templates-' + variant + '-after-reload');
                const actual = [];
                for (const [kind, rendererUrl] of [['receipt', `/api/document/invoice/${earlierPdf.receipt.invoice_id}/view/`],
                    ['subscription', `/api/document/subscription/${input.subscription_ids[0]}/view/`]]) {
                    const response = await page.goto(input.origin + rendererUrl);
                    expect(response.status()).toBe(200); await expect(page.locator('.content-wrapper')).toContainText(/Giulia/i);
                    const html = await response.text();
                    expect(html.includes('/* Reset and base styles */')).toBe(variant === 'classic');
                    actual.push(digest(Buffer.from(html)));
                    await take('document-' + kind + '-' + variant + '-renderer', page.locator('.content-wrapper'));
                    await page.goto(input.origin + '/#/'); await expect(page.getByText('Organizzazione', {exact: true})).toBeVisible();
                }
                rendererDigests[variant] = actual;
                expect(digest(await retrievePdf(earlierPdf.receipt))).toBe(digest(earlierPdf.bytes));
            }
            expect(rendererDigests.classic[0]).not.toBe(rendererDigests.standard[0]);
            expect(rendererDigests.classic[1]).not.toBe(rendererDigests.standard[1]);
            const currentTemplates = (await profile()).sport_association;
            expect((await reader.api('profile/update', {method: 'PATCH', data: {user_data: {
                ...originalPayload.user_data, sport_association: {...currentTemplates, invoice_template: 'invoice_classic.html', subscription_template: 'subscription_classic.html'}}}})).status()).toBe(403);
            expect((await profile()).sport_association).toEqual(currentTemplates);
            await openAccount();
            await setCheckbox(page.locator(`input[name="invoice_template"][value="${originalPrint.invoice_template || 'invoice.html'}"]`),true);
            await setCheckbox(page.locator(`input[name="subscription_template"][value="${originalPrint.subscription_template || 'subscription.html'}"]`),true);
            if (currentTemplates.invoice_template !== (originalPrint.invoice_template || 'invoice.html')
                || currentTemplates.subscription_template !== (originalPrint.subscription_template || 'subscription.html')) await saveAccount();
            assertTemplatesPreserved((await profile()).sport_association);

        } catch (error) {
            originalError = error;
            throw error;
        } finally {
            // Attempt all owned cleanup actions; preserve the original failure.
            const failures = [];
            const clean = async (route, options, statuses) => {
                try {
                    const response = await api(route, options);
                    if (!statuses.includes(response.status())) failures.push(route + ': ' + response.status());
                } catch { failures.push(route + ': cleanup unavailable'); }
            };
            await clean('profile/update', {method: 'PATCH', data: originalPayload}, [200]);
            for (const receiptId of ownedInvoiceIds)
                await clean(`invoice/${receiptId}/delete`, {method: 'POST', data: {}}, [200, 404]);
            for (const paymentId of ownedPaymentIds)
                await clean(`payment/${paymentId}/delete`, {method: 'DELETE'}, [200, 404]);
            for (const documentId of ownedDocumentIds) {
                await clean(`document/${documentId}/delete`, {method: 'DELETE'}, [200, 404]);
                await clean(`document/retrieve/${documentId}?download=true`, {}, [404]);
            }
            try { await page.unroute(rendererPattern, authenticateRenderer);
                await page.unroute(subscriptionRendererPattern, authenticateRenderer); }
            catch { failures.push('renderer route cleanup unavailable'); }
            if (failures.length) {
                report.cleanup_failures = failures;
                if (!originalError) throw new Error('Owned print-settings cleanup failed');
            }
        }
        expect((await profile()).sport_association).toEqual(originalPrint);
        const finalPayments = await payments();
        expect(finalPayments).toEqual(baselinePayments);
        for (const receiptId of ownedInvoiceIds)
            expect((await api('invoice/list?query[invoice_id]=' + receiptId)).status()).toBe(404);
        await openAccount(); await reloadOrganizationProfile({page, api, context, expect});
        await signatureBlock().scrollIntoViewIfNeeded(); await take('original-print-settings-restored');
        report[spec.outcome.field] = {
            uploaded_signature_saved_and_reopened: true, stamp_saved_and_reopened: true,
            drawn_signature_saved_and_reopened: true, signature_and_stamp_removed_after_reopen: true,
            header_saved_and_reopened: true, footer_saved_and_reopened: true,
            actual_renderer_has_header_and_footer: true, renderer_texts_do_not_overlap_content: true,
            actual_stock_receipt_does_not_insert_stored_signature_or_stamp: true,
            actual_new_pdf_downloaded: true, actual_new_pdf_previewed: true,
            blank_header_and_footer_omitted_from_new_print: true, blank_print_pdf_downloaded_and_previewed: true,
            earlier_pdf_preserved_after_settings_change: true, receipt_email_disabled: true,
            reader_profile_write_status: 403, reader_denial_preserves_configuration: true,
            document_template_selections_preserved: true, document_templates_saved_and_reopened: true,
            both_actual_receipt_templates_rendered: true, both_actual_subscription_templates_rendered: true,
            template_changes_preserve_earlier_pdf: true, reader_template_write_denied: true, original_configuration_restored: true,
            fixture_payments_preserved: true, owned_payments_receipts_and_documents_removed: true,
        };
        expect(report[spec.outcome.field]).toEqual(spec.outcome.expected);
        report.checks = ['fictional SVG signature and stamp uploaded, saved and reopened through actual profile controls',
            'fictional drawing accepted from the signature canvas, saved and reopened; cryptographic signing not exercised',
            'header/footer text persists and the authenticated stock renderer places it separately from receipt content',
            'the stock receipt does not automatically insert separately stored president signature or stamp',
            'real renderer-generated PDFs are downloaded and previewed with capability links masked; no email requested',
            'blank editor values are omitted from a new document and previously generated PDF bytes remain unchanged',
            'UI removal persists as null; reader write is denied; original profile and document selections restored',
            'only owned payments, receipts and document IDs cleaned up; fixture payments unchanged'];
    }});
