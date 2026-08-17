import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import test from 'node:test';

import {
    INVOICE_DIALOG_TYPES,
    buildInvoiceDocumentUrl,
    closeInvoiceDialog,
    getInvoiceActionAvailability,
    openInvoiceDialog,
    reloadInvoiceList,
} from './invoiceActionState.js';

const invoice = {
    invoice_id: 42,
    document_pdf: 'invoice-42.pdf',
    document_token: 'token-42',
    imported_from_associami: false,
    payment: {payment_id: 7},
};

test('active invoice action availability preserves permission and imported-invoice rules', () => {
    assert.deepEqual(
        getInvoiceActionAvailability(invoice, {canUpdate: true, canDelete: true}),
        {
            shareDisabled: false,
            editVisible: true,
            editDisabled: false,
            previewDisabled: false,
            deleteDisabled: false,
        }
    );

    assert.equal(
        getInvoiceActionAvailability({...invoice, document_token: null}, {canUpdate: true, canDelete: true})
            .shareDisabled,
        true
    );
    assert.equal(
        getInvoiceActionAvailability({...invoice, imported_from_associami: true}, {canUpdate: true, canDelete: true})
            .shareDisabled,
        true
    );
    assert.equal(
        getInvoiceActionAvailability({...invoice, payment: null}, {canUpdate: true, canDelete: true}).editDisabled,
        true
    );
    assert.equal(getInvoiceActionAvailability(invoice, {canUpdate: false, canDelete: true}).editDisabled, true);
    assert.equal(getInvoiceActionAvailability(invoice, {canUpdate: true, canDelete: false}).deleteDisabled, true);
});

test('archive preserves its distinct share rule and has no edit action', () => {
    const availability = getInvoiceActionAvailability(
        {...invoice, imported_from_associami: true},
        {archived: true, canUpdate: false, canDelete: true}
    );

    assert.equal(availability.shareDisabled, false);
    assert.equal(availability.editVisible, false);
    assert.equal(availability.editDisabled, true);
    assert.equal(availability.previewDisabled, false);
    assert.equal(availability.deleteDisabled, false);
});

test('dialog state contains at most one selected invoice and can be cleared', () => {
    let state = closeInvoiceDialog();
    assert.deepEqual(state, {type: null, row: null});

    state = openInvoiceDialog(state, INVOICE_DIALOG_TYPES.SHARE, invoice);
    assert.equal(state.type, INVOICE_DIALOG_TYPES.SHARE);
    assert.equal(state.row, invoice);

    const replacement = {...invoice, invoice_id: 99};
    state = openInvoiceDialog(state, INVOICE_DIALOG_TYPES.EDIT, replacement);
    assert.equal(state.type, INVOICE_DIALOG_TYPES.EDIT);
    assert.equal(state.row, replacement);

    assert.deepEqual(closeInvoiceDialog(state), {type: null, row: null});
    assert.throws(() => openInvoiceDialog(state, 'unsupported', invoice), /Unsupported invoice dialog/);
});

test('share and preview links preserve the document path and token', () => {
    const baseUrl = '/api/document/retrieve';

    assert.equal(
        buildInvoiceDocumentUrl(baseUrl, invoice, true),
        '/api/document/retrieve/invoice-42.pdf?download=true&token=token-42'
    );
    assert.equal(
        buildInvoiceDocumentUrl(baseUrl, invoice, false),
        '/api/document/retrieve/invoice-42.pdf?download=false&token=token-42'
    );
});

test('successful edit delegates exactly one table reload', () => {
    let reloads = 0;
    reloadInvoiceList(() => {
        reloads += 1;
    });
    assert.equal(reloads, 1);
});

test('invoice row templates do not construct heavyweight modal components', () => {
    const routeFiles = [
        {name: 'ReceiptList.svelte', editModalCount: 1},
        {name: 'ReceiptListArchive.svelte', editModalCount: 0},
    ];

    for (const routeFile of routeFiles) {
        const source = readFileSync(new URL(routeFile.name, import.meta.url), 'utf8');
        assert.doesNotMatch(source, /new\s+(ShareModal|EditModal|InvoicePreviewModal)\s*\(/);
        assert.match(source, /\{#if invoiceDialog\.row\}/);
        assert.equal(source.match(/<ShareModal\b/g)?.length, 1);
        assert.equal(source.match(/<InvoicePreviewModal\b/g)?.length, 1);
        assert.equal(source.match(/<EditModal\b/g)?.length ?? 0, routeFile.editModalCount);
    }
});

test('edit reload ownership remains in the page instead of EditModal', () => {
    const modalSource = readFileSync(new URL('modals/EditModal.svelte', import.meta.url), 'utf8');
    assert.doesNotMatch(modalSource, /datatableHandle/);
});

test('Intestato a remains permanently visible in active and archived invoice tables', () => {
    for (const routeFile of ['ReceiptList.svelte', 'ReceiptListArchive.svelte']) {
        const source = readFileSync(new URL(routeFile, import.meta.url), 'utf8');
        assert.match(source, /field:\s*'user'[\s\S]{0,180}autoHide:\s*false/);
    }
});
