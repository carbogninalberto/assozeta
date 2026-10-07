import crypto from 'node:crypto';
import fs from 'node:fs';
import path from 'node:path';
import {scenario, expect} from './scenario.mjs';
import {setCheckbox} from './controls.mjs';
import {reloadOrganizationProfile} from './organization-access-sources.mjs';
import {courseSettingsAuthoredWorkflows} from '../../../../docs/manuale/course-settings-authored-workflows.mjs';

const id = 'settings-receipts-card';
const spec = courseSettingsAuthoredWorkflows[id];
await scenario({id, prefix: spec.prefix.replace(/\/$/, ''), sources: spec.sources,
    actions: async ({page, api, context, open, actor, input, capture, report}) => {
        expect(input.fixture_version).toBe(8);
        expect(input.fixture_profile || 'baseline').toBe('baseline');
        const read = async route => {
            const response = await api(route);
            expect(response.status()).toBe(200);
            return response.json();
        };
        const take = async (checkpoint, focus, options) => {
            const index = spec.checkpoints.findIndex(item => item.id === checkpoint);
            expect(index).toBeGreaterThanOrEqual(0);
            await expect(page.locator('[data-sonner-toast]:visible')).toHaveCount(0, {timeout: 15000});
            await capture(page, index + 1, checkpoint, focus, options);
        };
        const settings = async () => (await read('profile/settings')).settings;
        const invoice = async uuid => (await read('invoice/list?query[invoice_id]=' + uuid)).data.invoice;
        const payments = async () => Object.values((await read('payment/list?pagination[perpage]=100')).data);
        const before = await settings();
        expect(before.enumerate_invoices).toBe(true);
        expect(before.starting_number_invoices).toBe(0);
        expect(before.payment_date_equal_invoice_date).toBe(false);
        // Generate an existing real receipt from a fixture payment already marked paid.
        // This UI action renders a document and never sends an email.
        await open('Pagamenti', '/#/payment/list');
        const giuliaPayment = page.locator('[data-row]').filter({hasText: 'Quota associativa Giulia Bianchi'});
        await giuliaPayment.getByText('Quota associativa Giulia Bianchi', {exact: true}).click();
        const details = page.locator('.drawer').filter({hasText: 'Dettagli Pagamento'});
        await expect(details).toBeVisible();
        const generated = page.waitForResponse(response => new URL(response.url()).pathname === `/api/payment/${input.payment_ids[0]}/generate-invoice`
            && response.request().method() === 'POST');
        await details.getByRole('button', {name: 'Genera Ricevuta', exact: true}).click();
        expect((await generated).status()).toBe(200);
        await details.locator('button.close').click();
        await expect(details).not.toBeVisible();
        const existingPayment = (await payments()).find(payment => payment.payment_id === input.payment_ids[0]);
        expect(existingPayment.invoice).toBeTruthy();
        const existingId = existingPayment.invoice.invoice_id;
        const existing = await invoice(existingId);
        expect(existing.number).toBe(1);
        const openReceipts = async () => {
            await page.getByText('Documenti fiscali', {exact: true}).first().click();
            await page.getByText('Ricevute', {exact: true}).first().click();
            await expect(page).toHaveURL(/#\/invoice\/list$/);
            await page.getByRole('textbox', {name: 'Anno ricevute', exact: true}).click();
            await page.getByText('Filtra Anno', {exact: true}).click();
        };
        await openReceipts();
        await expect(page.locator('[data-row]')).toHaveCount(1);
        await expect.poll(async () => Boolean((await invoice(existingId)).document_pdf), {timeout: 90000}).toBe(true);
        await take('existing-receipts-before-numbering-change');
        await open('Impostazioni', '/#/profile');
        await page.getByText('Generali', {exact: true}).click();
        const receiptBlock = label => page.locator('.form-group').filter({has: page.locator('label').filter({hasText: new RegExp('^' + label + '$')})});
        const receiptCheckbox = label => receiptBlock(label).locator('input[type="checkbox"]');
        const numbering = receiptCheckbox('Numera Ricevute');
        const dateRule = receiptCheckbox('Data uguale per ricevute e pagamenti');
        const baseNumber = receiptBlock('Numero iniziale ricevute').locator('input[type="number"]');
        await expect(numbering).toBeChecked();
        await expect(baseNumber).toHaveValue('0');
        await baseNumber.fill('100');
        await setCheckbox(dateRule, true);
        await page.getByText('Ricevute', {exact: true}).last().scrollIntoViewIfNeeded();
        await take('receipt-numbering-and-date-before-save');
        const save = async () => {
            const response = page.waitForResponse(response => new URL(response.url()).pathname === '/api/profile/settings'
                && response.request().method() === 'POST');
            await page.locator('#bkn_form_password_update_submit').click();
            expect((await response).status()).toBe(200);
            await expect(page.locator('#bkn_form_password_update_submit')).toBeDisabled();
        };
        await save();
        await page.reload();
        await expect(numbering).toBeChecked(); await expect(baseNumber).toHaveValue('100'); await expect(dateRule).toBeChecked();
        let saved = await settings();
        expect(saved.enumerate_invoices).toBe(true); expect(saved.starting_number_invoices).toBe(100);
        expect(saved.payment_date_equal_invoice_date).toBe(true);
        await page.getByText('Ricevute', {exact: true}).last().scrollIntoViewIfNeeded();
        await take('receipt-preferences-persist-after-reload');
        // Card labels are siblings of their controls rather than associated labels.
        const cardControl = (label, selector) => page.getByText(label, {exact: true})
            .locator('xpath=following-sibling::div[1]').locator(selector);
        const approvalOnly = cardControl('Rendi disponibile la tessera solo su approvazione', 'input[type="checkbox"]');
        const qr = cardControl('Mostra QR code nella tessera', 'input[type="checkbox"]');
        const color = cardControl('Colore della tessera', 'input[type="color"]');
        const layout = cardControl('Layout della tessera', 'select');
        await setCheckbox(approvalOnly, true);
        await setCheckbox(qr, false);
        await color.fill('#2255aa');
        await layout.selectOption('classic');
        const card = page.locator('#card-to-print');
        await expect(card).toContainText('Mario');
        await expect(card.locator('img.qrcode')).toHaveCount(0);
        await page.getByText('Personalizza tessera', {exact: true}).scrollIntoViewIfNeeded();
        await take('card-color-classic-layout-and-approval-preference');
        await save();
        await reloadOrganizationProfile({page, api, context, expect});
        await expect(approvalOnly).toBeChecked(); await expect(qr).not.toBeChecked();
        await expect(color).toHaveValue('#2255aa'); await expect(layout).toHaveValue('classic');
        saved = await settings();
        expect(saved.membership_card_configuration).toEqual({emit_only_on_approval: true,
            customized_template: {show_qr_code: false, template: 'classic', color: '#2255aa'}});
        await page.getByRole('button', {name: 'Usa il colore dell’istanza', exact: true}).click();
        await save();
        await reloadOrganizationProfile({page, api, context, expect});
        expect((await settings()).membership_card_configuration.customized_template.color).toBeNull();
        await color.fill('#2255aa'); await setCheckbox(qr, true); await save();
        await reloadOrganizationProfile({page, api, context, expect});
        await expect(qr).toBeChecked(); await expect(layout).toHaveValue('classic'); await expect(color).toHaveValue('#2255aa');
        await expect(card.locator('img.qrcode')).toBeVisible();
        saved = await settings();
        expect(saved.membership_card_configuration).toEqual({emit_only_on_approval: true,
            customized_template: {show_qr_code: true, template: 'classic', color: '#2255aa'}});
        await page.getByText('Personalizza tessera', {exact: true}).scrollIntoViewIfNeeded();
        await take('card-preferences-persist-after-reload');
        const memberCard = async (uuid, name) => {
            await page.goto(input.origin + '/#/members/list/detail/' + uuid);
            await page.getByRole('button', {name: 'Mostra Tessera', exact: true}).click();
            await expect(page.locator('#card-to-print')).toContainText(name);
            await expect(page.locator('#card-to-print img.qrcode')).toBeVisible();
            expect(await page.locator('#card-to-print').evaluate(node => [...node.querySelectorAll('[style]')]
                .some(child => getComputedStyle(child).backgroundColor === 'rgb(34, 85, 170)'))).toBe(true);
        };
        await memberCard(input.subscription_ids[0], 'Giulia');
        await take('actual-approved-member-card', page.locator('#card-to-print'));
        const [pendingMember] = await Promise.all([
            page.waitForResponse(response => new URL(response.url()).pathname === `/api/subscription/${input.subscription_ids[1]}/info`),
            page.goto(input.origin + '/#/members/list/detail/' + input.subscription_ids[1]),
        ]);
        expect(pendingMember.status()).toBe(200);
        await expect(page.getByText('Luca Verdi', {exact: true}).first()).toBeVisible();
        const stateField = page.locator('.form-group').filter({has: page.locator('label[for="status_flag"]')});
        await stateField.locator('input:not([type="hidden"])').click();
        await page.locator('.list-item').getByText('In attesa', {exact: true}).click();
        const pendingSave = page.waitForResponse(response => new URL(response.url()).pathname === `/api/subscription/${input.subscription_ids[1]}/update`
            && response.request().method() === 'PATCH');
        await page.locator('#subscription_form').getByRole('button', {name: 'Salva', exact: true}).click();
        expect((await pendingSave).status()).toBe(200);
        const subscriptions = Object.values((await read('subscription/list?pagination[perpage]=100')).data);
        expect(subscriptions.find(member => member.subscription_id === input.subscription_ids[1]).status_flag).toBe(2);
        await page.reload();
        await page.getByRole('button', {name: 'Mostra Tessera', exact: true}).click();
        await expect(page.locator('#card-to-print')).toContainText('Luca');
        await expect(page.locator('#card-to-print img.qrcode')).toBeVisible();
        await take('actual-pending-member-card-observed', page.locator('#card-to-print'));
        // The saved preference is real, but the association member view renders
        // the pending member's card. Do not turn this observation into a gate claim.
        expect((await settings()).membership_card_configuration.emit_only_on_approval).toBe(true);
        await open('Pagamenti', '/#/payment/list');
        const sara = page.locator('[data-row]').filter({hasText: 'Quota associativa Sara Conti'});
        await sara.locator('button:is([title="Segna come pagato"],[data-original-title="Segna come pagato"])').click();
        const approval = page.getByRole('dialog', {name: 'Incassare il pagamento?', exact: true});
        const paymentDate = new Date(input.reference_date + 'T12:00:00Z');
        paymentDate.setUTCDate(paymentDate.getUTCDate() - 1);
        const chosenDate = paymentDate.toISOString().slice(0, 10);
        await approval.locator('input[name="payment_date"]').fill(chosenDate);
        await expect(approval.locator('input[id^="generate_invoice_"]')).toBeChecked();
        await expect(approval.locator('input[id^="send_receipt_email_"]')).not.toBeChecked();
        await take('approval-with-backdated-payment-and-no-email', approval);
        const approved = page.waitForResponse(response => new URL(response.url()).pathname === `/api/payment/${input.payment_ids[2]}/approve`
            && response.request().method() === 'POST');
        await approval.getByRole('button', {name: 'Incassa', exact: true}).click();
        const approvedResponse = await approved;
        expect(approvedResponse.status()).toBe(200);
        expect(approvedResponse.request().postDataJSON().send_receipt_email).toBe(false);
        const paid = (await approvedResponse.json()).data.payment;
        expect(paid.paid).toBe(true); expect(paid.invoice.number).toBe(101);
        expect(paid.payment_date.slice(0, 10)).toBe(chosenDate);
        await expect(details).toBeVisible(); await details.locator('button.close').click();
        // Approval already queued the PDF. The receipts list queues another one for any
        // receipt still without a document, which can replace the PDF after it is read.
        await expect.poll(async () => Boolean((await invoice(paid.invoice.invoice_id)).document_pdf), {timeout: 90000}).toBe(true);
        await openReceipts();
        await expect(page.locator('[data-row]')).toHaveCount(2);
        const receipt = await invoice(paid.invoice.invoice_id);
        expect(receipt.number).toBe(101); expect(receipt.creation_date.slice(0, 10)).toBe(chosenDate);
        const preserved = await invoice(existingId);
        expect(preserved.number).toBe(existing.number); expect(preserved.creation_date).toBe(existing.creation_date);
        await page.reload();
        await page.getByRole('textbox', {name: 'Anno ricevute', exact: true}).click();
        await page.getByText('Filtra Anno', {exact: true}).click();
        const receiptRow = page.locator('[data-row]').filter({hasText: 'Sara Conti'});
        await expect(receiptRow.locator('button:is([title="Ricevuta n.101"],[data-original-title="Ricevuta n.101"])')).toBeVisible();
        await take('new-receipt-number-and-date');
        const html = await api(`document/invoice/${receipt.invoice_id}/view/`);
        expect(html.status()).toBe(200); expect(await html.text()).toContain('Ricevuta n. 101');
        let pdfContents;
        await expect.poll(async () => {
            const pdf = await api(`document/retrieve/${receipt.document_pdf}?download=true&token=${receipt.document_token}`);
            if (!pdf.ok()) return false;
            expect(pdf.headers()['content-type']).toContain('application/pdf');
            const body = await pdf.body();
            if (body.subarray(0, 5).toString() !== '%PDF-') return false;
            pdfContents = body;
            return true;
        }, {timeout: 90000}).toBe(true);
        const relativePdf = 'receipt-pdfs/settings-number-101.pdf';
        const pdfFile = path.join(process.env.ASSOZETA_MANUAL_RUN, relativePdf);
        fs.mkdirSync(path.dirname(pdfFile), {recursive: true}); fs.writeFileSync(pdfFile, pdfContents);
        report.receipt_pdf = {path: relativePdf, bytes: pdfContents.length,
            sha256: crypto.createHash('sha256').update(pdfContents).digest('hex')};
        const previewResponse = page.waitForResponse(response => {
            const url = new URL(response.url());
            return url.pathname === '/api/document/retrieve/' + receipt.document_pdf && url.searchParams.get('download') === 'false';
        });
        await receiptRow.locator('button:is([title="Ricevuta n.101"],[data-original-title="Ricevuta n.101"])').click();
        const preview = page.getByRole('dialog', {name: 'Ricevuta n.101', exact: true});
        await expect(preview.locator('iframe')).toBeVisible();
        const previewResult = await previewResponse;
        expect(previewResult.status()).toBe(200);
        expect(previewResult.headers()['content-type']).toContain('application/pdf');
        const previewSource = new URL(await preview.locator('iframe').getAttribute('src'), input.origin);
        expect(previewSource.origin).toBe(input.origin);
        expect(previewSource.pathname).toBe('/api/document/retrieve/' + receipt.document_pdf);
        expect(previewSource.searchParams.get('download')).toBe('false');
        const previewFile = await page.context().request.get(previewSource.href);
        expect(previewFile.status()).toBe(200);
        expect(previewFile.headers()['content-type']).toContain('application/pdf');
        expect(crypto.createHash('sha256').update(await previewFile.body()).digest('hex')).toBe(report.receipt_pdf.sha256);
        const secretFields = page.locator('.modal:visible input[type="text"]');
        await take('actual-numbered-receipt-pdf', preview.locator('iframe'),
            {mask: [secretFields], redactionReason: 'receipt capability link excluded from crop and masked in Full HD master'});
        await preview.getByRole('button', {name: 'Chiudi', exact: true}).first().click();
        const reader = await actor('reader');
        await reader.open('Impostazioni', '/#/profile');
        await reader.page.getByText('Generali', {exact: true}).click();
        const readerNumber = reader.page.locator('.form-group').filter({has: reader.page.getByText('Numero iniziale ricevute', {exact: true})}).locator('input[type="number"]');
        await expect(readerNumber).toBeDisabled();
        expect((await reader.api('profile/settings', {method: 'POST', data: {...saved, starting_number_invoices: 999}})).status()).toBe(403);
        const afterDenial = await settings();
        expect(afterDenial.starting_number_invoices).toBe(100);
        expect(afterDenial.membership_card_configuration).toEqual(saved.membership_card_configuration);
        report[spec.outcome.field] = {
            numbering_saved_and_reopened: true, date_rule_saved_and_reopened: true,
            card_preferences_saved_and_reopened: true, card_brand_reset_exercised: true,
            approved_member_card_rendered: true, pending_member_card_observed: true,
            approval_preference_is_not_universal_gate: true, new_receipt_number: receipt.number,
            receipt_date_matches_payment: receipt.creation_date.slice(0, 10) === paid.payment_date.slice(0, 10),
            actual_pdf_downloaded: pdfContents.subarray(0, 5).toString() === '%PDF-', actual_pdf_previewed: true,
            receipt_email_disabled: true, existing_receipts_not_renumbered: preserved.number === existing.number,
            reader_settings_write_denied: true,
        };
        expect(report[spec.outcome.field]).toEqual(spec.outcome.expected);
        report.checks = ['UI generates an existing paid-payment receipt before changing the numbering base',
            'numbering base and receipt date rule persist through authenticated reload',
            'card approval/QR/color/classic-layout preferences persist; instance-color reset is saved',
            'real approved and pending member cards are inspected without claiming a universal approval gate',
            'UI approval with email disabled emits number 101 with the chosen payment date',
            'actual receipt renderer, PDF bytes and browser preview succeed; existing number remains unchanged',
            'restricted reader cannot change settings'];
    }});
