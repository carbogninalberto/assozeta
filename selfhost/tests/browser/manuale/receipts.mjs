import crypto from 'node:crypto';
import fs from 'node:fs';
import {scenario, expect} from './scenario.mjs';
import {navigateToDownload} from './download.mjs';
import {coreLegacyAuthoredWorkflows} from '../../../../docs/manuale/core-legacy-authored-workflows.mjs';

await scenario({id: 'receipts-approve-download', prefix: 'images/ricevute/consultazione',
    sources: coreLegacyAuthoredWorkflows['receipts-approve-download'].sources, actions: async ({page, api, open, actor, input, capture, report}) => {
    // Real API setup: exercise a receipt lacking its PDF so the list's repair
    // path is proved too. No production endpoint is intercepted or simulated.
    const luca = input.payment_ids[1];
    expect((await api(`payment/${luca}/cancel`, {method: 'POST', data: {}})).status()).toBe(200);
    const withoutPdf = await api(`payment/${luca}/approve`, {method: 'POST', data: {
        payment_date: input.reference_date, generate_invoice: false, send_receipt_email: false,
    }});
    expect(withoutPdf.status()).toBe(200);
    const first = (await withoutPdf.json()).data.payment.invoice;
    expect(first.number).toBe(1);
    expect(first.document_pdf).toBeNull();
    await open('Pagamenti', '/#/payment/list');
    const row = page.locator('[data-row]').filter({hasText: 'Quota associativa Sara Conti'});
    await expect(row).toBeVisible();
    await row.locator('button:is([title="Segna come pagato"],[data-original-title="Segna come pagato"])').click();
    const approval = page.getByRole('dialog', {name: 'Incassare il pagamento?', exact: true});
    await expect(approval.locator('input[id^="generate_invoice_"]')).toBeChecked();
    await expect(approval.locator('input[id^="send_receipt_email_"]')).not.toBeChecked();
    await approval.locator('input[name="payment_date"]').fill(input.reference_date);
    await capture(page, 1, 'approval-with-pdf-and-no-email', approval);
    const approved = page.waitForResponse(response => response.url().endsWith(`/payment/${input.payment_ids[2]}/approve`) && response.request().method() === 'POST');
    await approval.getByRole('button', {name: 'Incassa', exact: true}).click();
    const response = await approved;
    expect(response.status()).toBe(200);
    const paid = (await response.json()).data.payment;
    expect(paid.paid).toBe(true);
    expect(paid.invoice_generating).toBe(true);
    expect(paid.invoice.number).toBe(2);
    const drawer = page.locator('.drawer').filter({hasText: 'Dettagli Pagamento'});
    await expect(drawer).toBeVisible();
    await drawer.locator('button.close').click();
    await expect(drawer).not.toBeVisible();
    await page.getByText('Documenti fiscali', {exact: true}).first().click();
    await page.getByText('Ricevute', {exact: true}).first().click();
    await expect(page).toHaveURL(/#\/invoice\/list$/);
    await expect(page.locator('[data-row]')).toHaveCount(2);
    const invoice = async id => {
        const response = await api('invoice/list?query[invoice_id]=' + id);
        expect(response.ok()).toBeTruthy();
        return (await response.json()).data.invoice;
    };
    // Single-invoice lookup does not enqueue rendering. The list navigation
    // above must have triggered the first PDF; approval triggered the second.
    for (const id of [first.invoice_id, paid.invoice.invoice_id]) {
        await expect.poll(async () => Boolean((await invoice(id)).document_pdf), {timeout: 90000}).toBe(true);
    }
    await page.reload();
    await expect(page.locator('[data-row]')).toHaveCount(2);
    const sara = page.locator('[data-row]').filter({hasText: 'Sara Conti'});
    await expect(sara).toBeVisible();
    await capture(page, 2, 'receipt-list-with-generated-pdfs-and-progressives');
    const receipt = await invoice(paid.invoice.invoice_id);
    expect(Number(receipt.membership_fee)).toBe(25);
    expect(Number(receipt.activity_fee)).toBe(0);
    // Download both actual files, including the list-generated one. Reports
    // retain hashes and lengths, never capability tokens or private URLs.
    report.pdf_documents = [];
    for (const id of [first.invoice_id, paid.invoice.invoice_id]) {
        const entry = await invoice(id);
        const pdf = await api(`document/retrieve/${entry.document_pdf}?download=true&token=${entry.document_token}`);
        expect(pdf.ok()).toBeTruthy();
        expect(pdf.headers()['content-type']).toContain('application/pdf');
        expect(pdf.headers()['content-disposition']).toContain('attachment');
        const contents = await pdf.body();
        expect(contents.subarray(0, 5).toString()).toBe('%PDF-');
        report.pdf_documents.push({number: entry.number, bytes: contents.length,
            sha256: crypto.createHash('sha256').update(contents).digest('hex')});
    }
    const previewFetch = page.waitForResponse(response => {
        const url = new URL(response.url());
        return url.pathname === '/api/document/retrieve/' + receipt.document_pdf && url.searchParams.get('download') === 'false';
    });
    await sara.locator('button:is([title="Ricevuta n.2"],[data-original-title="Ricevuta n.2"])').click();
    const preview = page.getByRole('dialog', {name: 'Ricevuta n.2', exact: true});
    const frame = preview.locator('iframe');
    await expect(frame).toBeVisible();
    const frameSource = await frame.getAttribute('src');
    const source = new URL(frameSource, input.origin);
    expect(source.origin).toBe(input.origin);
    expect(source.pathname).toBe('/api/document/retrieve/' + receipt.document_pdf);
    expect(source.searchParams.get('download')).toBe('false');
    const previewResponse = await previewFetch;
    expect(previewResponse.ok()).toBeTruthy();
    expect(previewResponse.headers()['content-type']).toContain('application/pdf');
    // Chromium's native PDF viewer can discard the navigation response body.
    // Read the same actual capability URL and bind its bytes to the generated file.
    const previewFile = await page.context().request.get(source.href);
    expect(previewFile.ok()).toBeTruthy();
    expect(previewFile.headers()['content-type']).toContain('application/pdf');
    const previewContents = await previewFile.body();
    expect(previewContents.subarray(0, 5).toString()).toBe('%PDF-');
    expect(crypto.createHash('sha256').update(previewContents).digest('hex')).toBe(report.pdf_documents[1].sha256);
    // The share-link box contains a capability token. Crop to the PDF frame.
    await capture(page, 3, 'receipt-pdf-preview-without-share-token', frame);
    await preview.getByRole('button', {name: 'Chiudi', exact: true}).first().click();
    await expect(preview).not.toBeVisible();
    await page.context().grantPermissions(['clipboard-read', 'clipboard-write']);
    await sara.locator('button:is([title="Condividi ricevuta"],[data-original-title="Condividi ricevuta"])').click();
    const share = page.locator('#shareModal-' + receipt.invoice_id);
    await expect(share).toBeVisible();
    const linkInput = share.locator('input[type="text"]');
    const downloadUrl = await linkInput.inputValue();
    const parsedDownload = new URL(downloadUrl, input.origin);
    expect(parsedDownload.pathname).toBe('/api/document/retrieve/' + receipt.document_pdf);
    expect(parsedDownload.searchParams.get('download')).toBe('true');
    await share.locator('a[data-clipboard="true"]').click();
    await expect.poll(() => page.evaluate(() => navigator.clipboard.readText())).toBe(downloadUrl);
    await capture(page, 4, 'copy-download-link-with-token-redacted', share.locator('.modal-content'),
        {mask: [linkInput], redactionReason: 'receipt capability link'});
    const downloadPage = await page.context().newPage();
    const download = await navigateToDownload(downloadPage, parsedDownload.href, 'Receipt');
    expect(await download.failure()).toBeNull();
    const downloaded = fs.readFileSync(await download.path());
    expect(downloaded.subarray(0, 5).toString()).toBe('%PDF-');
    expect(crypto.createHash('sha256').update(downloaded).digest('hex')).toBe(report.pdf_documents[1].sha256);
    await downloadPage.close();
    await share.getByRole('button', {name: 'Close', exact: true}).click();
    await expect(share).not.toBeVisible();
    const reader = await actor('reader');
    await reader.page.getByText('Documenti fiscali', {exact: true}).first().click();
    await reader.page.getByText('Ricevute', {exact: true}).first().click();
    await expect(reader.page).toHaveURL(/#\/invoice\/list$/);
    const readRow = reader.page.locator('[data-row]').filter({hasText: 'Sara Conti'});
    await expect(readRow).toBeVisible();
    await expect(readRow.locator('button:is([title="Modifica"],[data-original-title="Modifica"])')).toBeDisabled();
    await expect(readRow.locator('button:is([title="Elimina Ricevuta"],[data-original-title="Elimina Ricevuta"])')).toBeDisabled();
    const readerWriteStatuses = [];
    for (const [path, method, data] of [
        [`invoice/${receipt.invoice_id}/update`, 'PATCH', {number: 99}],
        [`invoice/${receipt.invoice_id}/delete`, 'POST', {}],
        [`payment/${input.payment_ids[2]}/generate-invoice`, 'POST', {}],
    ]) {
        const denial = await reader.api(path, {method, data});
        readerWriteStatuses.push(denial.status());
        expect(denial.status()).toBe(403);
    }
    const unchanged = await invoice(receipt.invoice_id);
    expect(unchanged.number).toBe(2);
    expect(unchanged.document_pdf).toBe(receipt.document_pdf);
    report.receipt_download = {receipt_numbers: report.pdf_documents.map(pdf => pdf.number),
        both_pdfs_downloaded: report.pdf_documents.length === 2 && report.pdf_documents.every(pdf => pdf.bytes > 5),
        preview_is_pdf: previewContents.subarray(0, 5).toString() === '%PDF-',
        clipboard_matches_download: await page.evaluate(() => navigator.clipboard.readText()) === downloadUrl,
        browser_download_matches_pdf: crypto.createHash('sha256').update(downloaded).digest('hex') === report.pdf_documents[1].sha256,
        membership_fee: Number(receipt.membership_fee), activity_fee: Number(receipt.activity_fee), reader_write_statuses: readerWriteStatuses,
        receipt_number_preserved: unchanged.number === receipt.number, receipt_pdf_preserved: unchanged.document_pdf === receipt.document_pdf};
    report.checks = ['real checked approval dispatches receipt rendering without email delivery',
        'unchecked approval still creates a receipt record; opening the receipt list generates its missing PDF',
        'two approvals produce progressives 1 and 2 in the configured current fiscal year',
        'persisted receipt amounts match the payments', 'actual generated PDF bytes download and preview successfully',
        'actual UI copies a download link; the browser downloads the same PDF bytes',
        'read-only collaborator can view receipts but cannot update, delete, or generate one'];
}});
