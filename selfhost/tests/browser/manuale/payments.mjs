import {scenario, expect} from './scenario.mjs';
import {coreLegacyAuthoredWorkflows} from '../../../../docs/manuale/core-legacy-authored-workflows.mjs';

await scenario({id: 'payments-create-edit-approve', prefix: 'images/pagamenti/registrazione',
    sources: coreLegacyAuthoredWorkflows['payments-create-edit-approve'].sources, actions: async ({page, api, open, actor, input, capture, report}) => {
    const payments = async () => {
        const response = await api('payment/list');
        expect(response.ok()).toBeTruthy();
        return Object.values((await response.json()).data);
    };
    const closeDrawer = async () => {
        const drawer = page.locator('.drawer').filter({hasText: 'Dettagli Pagamento'});
        await expect(drawer).toBeVisible();
        await drawer.locator('button.close').click();
        await expect(drawer).not.toBeVisible();
    };
    await open('Pagamenti', '/#/payment/list');
    await expect(page.locator('[data-row]')).toHaveCount(3);
    await page.getByRole('button', {name: 'Pagamento', exact: true}).click();
    const modal = page.getByRole('dialog', {name: 'Crea Nuovo Pagamento', exact: true});
    await expect(modal.locator('#payment_form')).toBeVisible();
    await modal.getByPlaceholder('Inserisci una descrizione').fill('Quota laboratorio creativo');
    await modal.locator('input[name="amount"]').fill('35,00');
    // Exercise the visible select, including the actual associate relationship.
    await modal.getByRole('textbox', {name: 'Intestato a', exact: true}).fill('Giulia');
    await modal.getByText('Giulia Bianchi', {exact: true}).click();
    await modal.getByRole('textbox', {name: 'Causale', exact: true}).click();
    await modal.getByText('Quote e attività associative', {exact: true}).click();
    await expect(modal.locator('input[name="payment_date"]')).toHaveValue('');
    await capture(page, 1, 'filled-incoming-cash-payment', modal.locator('.modal-content'));
    const creation = page.waitForResponse(response => response.url().endsWith('/payment/add') && response.request().method() === 'POST');
    await modal.getByRole('button', {name: 'Crea', exact: true}).click();
    const createdResponse = await creation;
    expect(createdResponse.status()).toBe(201);
    const created = await createdResponse.json();
    expect(created.paid).toBe(false);
    expect(created.invoice).toBeNull();
    expect(created.type).toBe('cash');
    expect(created.expense).toBe(false);
    expect(created.custom_accounts).toBe(input.cash_account_id);
    expect(created.payment_category).toBe(input.payment_category_id);
    expect(created.associate.first_name).toBe('Giulia');
    expect(Number(created.amount)).toBe(35);
    await closeDrawer();
    let row = page.locator('[data-row]').filter({hasText: 'Quota laboratorio creativo'});
    await expect(row).toBeVisible();
    await capture(page, 2, 'created-payment-in-list');
    await row.locator('button:is([title="Modifica"],[data-original-title="Modifica"])').click();
    const edit = page.getByRole('dialog', {name: 'Modifica Pagamento', exact: true});
    await expect(edit.locator('input[name="amount"]')).toBeVisible();
    await edit.locator('input[name="amount"]').fill('40,00');
    await capture(page, 3, 'edited-payment-amount', edit.locator('.modal-content'));
    const update = page.waitForResponse(response => response.url().endsWith(`/payment/${created.payment_id}/update`) && response.request().method() === 'PATCH');
    await edit.getByRole('button', {name: 'Modifica', exact: true}).click();
    expect((await update).status()).toBe(200);
    await closeDrawer();
    await page.reload();
    row = page.locator('[data-row]').filter({hasText: 'Quota laboratorio creativo'});
    await expect(row).toBeVisible();
    const persisted = (await payments()).find(payment => payment.payment_id === created.payment_id);
    expect(Number(persisted.amount)).toBe(40);
    expect(persisted.paid).toBe(false);
    await capture(page, 4, 'edited-payment-persists-after-reload');
    await row.locator('button:is([title="Segna come pagato"],[data-original-title="Segna come pagato"])').click();
    const approval = page.getByRole('dialog', {name: 'Incassare il pagamento?', exact: true});
    await expect(approval.locator('input[id^="generate_invoice_"]')).toBeChecked();
    await approval.getByText('Genera ricevuta', {exact: true}).click();
    await expect(approval.locator('input[id^="generate_invoice_"]')).not.toBeChecked();
    await expect(approval.locator('input[id^="send_receipt_email_"]')).not.toBeChecked();
    await approval.locator('input[name="payment_date"]').fill(input.reference_date);
    await capture(page, 5, 'approval-without-immediate-pdf', approval);
    const approved = page.waitForResponse(response => response.url().endsWith(`/payment/${created.payment_id}/approve`) && response.request().method() === 'POST');
    await approval.getByRole('button', {name: 'Incassa', exact: true}).click();
    const approvalResponse = await approved;
    expect(approvalResponse.status()).toBe(200);
    const paid = (await approvalResponse.json()).data.payment;
    expect(paid.paid).toBe(true);
    expect(paid.invoice.number).toBe(1);
    expect(paid.invoice.document_pdf).toBeNull();
    expect(paid.invoice_generating).toBeUndefined();
    await closeDrawer();
    await page.reload();
    row = page.locator('[data-row]').filter({hasText: 'Quota laboratorio creativo'});
    await expect(row.locator('button:is([title="Annulla pagamento"],[data-original-title="Annulla pagamento"])')).toBeVisible();
    const final = (await payments()).find(payment => payment.payment_id === created.payment_id);
    expect(final.paid).toBe(true);
    expect(final.payment_date.slice(0, 10)).toBe(input.reference_date);
    expect(final.invoice.invoice_id).toBe(paid.invoice.invoice_id);
    expect(final.invoice.document_pdf).toBeNull();
    await capture(page, 6, 'approved-payment-persists-with-receipt-record');
    const reader = await actor('reader');
    await reader.open('Pagamenti', '/#/payment/list');
    const readRow = reader.page.locator('[data-row]').filter({hasText: 'Quota laboratorio creativo'});
    await expect(readRow).toBeVisible();
    await expect(reader.page.getByRole('button', {name: 'Pagamento', exact: true})).toHaveCount(0);
    await expect(readRow.locator('button:is([title="Modifica"],[data-original-title="Modifica"])')).toBeDisabled();
    const readerCreate = await reader.api('payment/add', {method: 'POST', data: {description: 'Negato'}});
    expect(readerCreate.status()).toBe(403);
    const readerUpdate = await reader.api(`payment/${created.payment_id}/update`, {method: 'PATCH', data: {amount: 1}});
    expect(readerUpdate.status()).toBe(403);
    const readerApprove = await reader.api(`payment/${input.payment_ids[2]}/approve`, {method: 'POST', data: {generate_invoice: false}});
    expect(readerApprove.status()).toBe(403);
    const afterDenied = await payments();
    expect(afterDenied).toHaveLength(4);
    expect(Number(afterDenied.find(payment => payment.payment_id === created.payment_id).amount)).toBe(40);
    expect(afterDenied.find(payment => payment.payment_id === input.payment_ids[2]).paid).toBe(false);
    report.payment_lifecycle = {created_amount: Number(created.amount), created_paid: created.paid,
        edited_amount: Number(persisted.amount), edited_paid: persisted.paid, paid_after_reload: final.paid,
        receipt_number: final.invoice.number, receipt_pdf_created: Boolean(final.invoice.document_pdf),
        payment_date_preserved: final.payment_date.slice(0, 10) === input.reference_date,
        reader_create_status: readerCreate.status(), reader_update_status: readerUpdate.status(), reader_approve_status: readerApprove.status(),
        denied_writes_preserve_amount: Number(afterDenied.find(payment => payment.payment_id === created.payment_id).amount) === 40,
        other_payment_unpaid: afterDenied.find(payment => payment.payment_id === input.payment_ids[2]).paid === false};
    report.checks = ['actual incoming cash payment create and amount update', 'associate, account, category and amount persist',
        'approval defaults to PDF generation; explicitly unchecked in this scenario',
        'unchecked generation still creates a numbered receipt record but no PDF',
        'paid state and date persist after reload', 'read-only collaborator sees payments but all three write attempts are forbidden'];
}});
