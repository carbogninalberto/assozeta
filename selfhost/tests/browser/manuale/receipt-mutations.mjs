import crypto from 'node:crypto';
import fs from 'node:fs';
import path from 'node:path';
import {scenario, expect} from './scenario.mjs';
import {coreLegacyAuthoredWorkflows} from '../../../../docs/manuale/core-legacy-authored-workflows.mjs';

await scenario({id: 'receipts-edit-delete', prefix: 'images/ricevute/modifica-eliminazione',
    sources: coreLegacyAuthoredWorkflows['receipts-edit-delete'].sources, actions: async ({page, api, open, actor, input, capture, report}) => {
    expect(input.fixture_profile).toBe('receipts-edit-delete');
    const id = input.older_receipt_id;
    expect(id).toBeTruthy();
    const profile = await api('profile/info');
    expect(profile.ok()).toBeTruthy();
    expect((await profile.json()).user_data.temporary_invoice_deletion).toBe(false);
    const invoice = async () => {
        const response = await api('invoice/list?query[invoice_id]=' + id);
        expect(response.ok()).toBeTruthy();
        return (await response.json()).data.invoice;
    };
    const payments = async () => {
        const response = await api('payment/list');
        expect(response.ok()).toBeTruthy();
        return Object.values((await response.json()).data);
    };
    const original = await invoice();
    expect(original.number).toBe(7);
    expect((Date.parse(input.reference_date + 'T12:00:00Z') - Date.parse(original.creation_date)) / 86400000).toBe(29);
    const paymentBefore = (await payments()).find(payment => payment.payment_id === input.payment_ids[0]);
    expect(paymentBefore.paid).toBe(true);
    expect(paymentBefore.invoice.invoice_id).toBe(id);
    const receiptList = async actor => {
        await actor.page.getByText('Documenti fiscali', {exact: true}).first().click();
        await actor.page.getByText('Ricevute', {exact: true}).first().click();
        await expect(actor.page).toHaveURL(/#\/invoice\/list$/);
        // Include the older receipt even when a custom reference date puts it
        // in the previous fiscal year. '-' removes the year constraint.
        await actor.page.getByRole('textbox', {name: 'Anno ricevute', exact: true}).click();
        await actor.page.getByText('Filtra Anno', {exact: true}).click();
        await expect(actor.page.locator('[data-row]')).toHaveCount(1);
    };
    await receiptList({page});
    await expect.poll(async () => Boolean((await invoice()).document_pdf), {timeout: 90000}).toBe(true);
    const initialPdf = (await invoice()).document_pdf;
    const row = page.locator('[data-row]').filter({hasText: 'Giulia Bianchi'});
    await expect(row).toBeVisible();
    await capture(page, 1, 'older-receipt-with-progressive-seven');
    const reader = await actor('reader');
    await receiptList(reader);
    const readRow = reader.page.locator('[data-row]').filter({hasText: 'Giulia Bianchi'});
    await expect(readRow.locator('button:is([title="Modifica"],[data-original-title="Modifica"])')).toBeDisabled();
    await expect(readRow.locator('button:is([title="Elimina Ricevuta"],[data-original-title="Elimina Ricevuta"])')).toBeDisabled();
    const readerUpdate = await reader.api(`invoice/${id}/update`, {method: 'PATCH', data: {number: 99}});
    expect(readerUpdate.status()).toBe(403);
    const readerDelete = await reader.api(`invoice/${id}/delete`, {method: 'POST', data: {}});
    expect(readerDelete.status()).toBe(403);
    expect((await invoice()).number).toBe(7);
    await row.locator('button:is([title="Modifica"],[data-original-title="Modifica"])').click();
    const edit = page.locator('#editModal-' + id);
    await expect(edit).toBeVisible();
    await expect(edit.locator('input[name="number"]')).toHaveValue('7');
    await edit.locator('input[name="number"]').fill('9');
    await capture(page, 2, 'edit-receipt-progressive-to-nine', edit.locator('.modal-content'));
    const updated = page.waitForResponse(response => response.url().endsWith(`/invoice/${id}/update`) && response.request().method() === 'PATCH');
    await edit.getByRole('button', {name: 'Salva', exact: true}).click();
    expect((await updated).status()).toBe(200);
    await expect(edit).not.toBeVisible();
    const edited = await invoice();
    expect(edited.number).toBe(9);
    expect(edited.creation_date).toBe(original.creation_date);
    await expect.poll(async () => (await invoice()).document_pdf, {timeout: 90000}).not.toBe(initialPdf);
    const persisted = await invoice();
    expect(persisted.document_pdf).toBeTruthy();
    // The real renderer input must carry the changed number. The assertion is
    // against the actual authenticated HTML endpoint, not a mock template.
    const html = await api(`document/invoice/${id}/view/`);
    expect(html.ok()).toBeTruthy();
    const rendererHtml = await html.text();
    expect(rendererHtml).toContain('Ricevuta n. 9');
    let pdfContents;
    const readinessStatuses = [];
    await expect.poll(async () => {
        const pdf = await api(`document/retrieve/${persisted.document_pdf}?download=true&token=${persisted.document_token}`);
        readinessStatuses.push(pdf.status());
        if (!pdf.ok()) return false;
        const contents = await pdf.body();
        if (contents.subarray(0, 5).toString() !== '%PDF-') return false;
        pdfContents = contents;
        return true;
    }, {timeout: 90000}).toBe(true);
    const relativePdf = 'receipt-pdfs/updated-receipt.pdf';
    const pdfFile = path.join(process.env.ASSOZETA_MANUAL_RUN, relativePdf);
    fs.mkdirSync(path.dirname(pdfFile), {recursive: true});
    fs.writeFileSync(pdfFile, pdfContents);
    report.updated_pdf = {path: relativePdf, bytes: pdfContents.length, sha256: crypto.createHash('sha256').update(pdfContents).digest('hex'),
        readiness_statuses: readinessStatuses};
    await page.reload();
    await page.getByRole('textbox', {name: 'Anno ricevute', exact: true}).click();
    await page.getByText('Filtra Anno', {exact: true}).click();
    await expect(row.locator('button:is([title="Ricevuta n.9"],[data-original-title="Ricevuta n.9"])')).toBeVisible();
    const beforeDelete = (await payments()).find(payment => payment.payment_id === input.payment_ids[0]);
    expect(beforeDelete.paid).toBe(true);
    expect(Number(beforeDelete.amount)).toBe(25);
    expect(beforeDelete.invoice.number).toBe(9);
    await capture(page, 3, 'edited-receipt-persists-after-reload');
    await row.locator('button:is([title="Elimina Ricevuta"],[data-original-title="Elimina Ricevuta"])').click();
    const confirmation = page.getByRole('dialog').filter({hasText: 'Vuoi eliminare definitivamente la ricevuta?'});
    await expect(confirmation).toBeVisible();
    await capture(page, 4, 'confirm-deletion-of-older-receipt', confirmation);
    const deleted = page.waitForResponse(response => response.url().endsWith(`/invoice/${id}/delete`) && response.request().method() === 'POST');
    await confirmation.getByRole('button', {name: 'Elimina', exact: true}).click();
    expect((await deleted).status()).toBe(200);
    await expect(page.locator('[data-row]')).toHaveCount(0);
    const deletedLookup = await api('invoice/list?query[invoice_id]=' + id);
    expect(deletedLookup.status()).toBe(404);
    const afterDelete = (await payments()).find(payment => payment.payment_id === input.payment_ids[0]);
    expect(afterDelete.paid).toBe(false);
    expect(afterDelete.invoice).toBeNull();
    expect(Number(afterDelete.amount)).toBe(25);
    await open('Pagamenti', '/#/payment/list');
    const paymentRow = page.locator('[data-row]').filter({hasText: 'Quota associativa Giulia Bianchi'});
    await expect(paymentRow.locator('button:is([title="Segna come pagato"],[data-original-title="Segna come pagato"])')).toBeVisible();
    await page.reload();
    await expect(paymentRow.locator('button:is([title="Segna come pagato"],[data-original-title="Segna come pagato"])')).toBeVisible();
    await capture(page, 5, 'payment-unpaid-after-receipt-deletion');
    report.receipt_mutation = {original_number: original.number, edited_number: persisted.number,
        date_preserved: persisted.creation_date === original.creation_date, pdf_regenerated: persisted.document_pdf !== initialPdf,
        renderer_has_updated_number: rendererHtml.includes('Ricevuta n. 9'), pdf_downloaded: pdfContents.subarray(0, 5).toString() === '%PDF-',
        reader_update_status: readerUpdate.status(), reader_delete_status: readerDelete.status(), deleted_lookup_status: deletedLookup.status(),
        payment_paid: afterDelete.paid, payment_invoice: afterDelete.invoice, payment_amount: Number(afterDelete.amount),
        unpaid_visible_after_reload: await paymentRow.locator('button:is([title="Segna come pagato"],[data-original-title="Segna come pagato"])').isVisible()};
    report.checks = ['29-day-old receipt fixture with temporary deletion flag false',
        'read-only collaborator cannot edit or delete the receipt', 'actual edit changes progressive 7 to 9 and persists',
        'renderer input and regenerated PDF correspond to the updated receipt',
        'actual receipt deletion succeeds beyond seven days with the flag disabled',
        'deleted receipt lookup returns 404; linked payment remains present with unchanged amount and becomes unpaid'];
}});
