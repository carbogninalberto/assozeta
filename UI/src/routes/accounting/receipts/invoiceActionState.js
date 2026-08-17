export const INVOICE_DIALOG_TYPES = Object.freeze({
    SHARE: 'share',
    EDIT: 'edit',
    PREVIEW: 'preview',
});

const supportedDialogTypes = new Set(Object.values(INVOICE_DIALOG_TYPES));

export function getInvoiceActionAvailability(
    row,
    {archived = false, canUpdate = false, canDelete = false} = {}
) {
    const imported = Boolean(row?.imported_from_associami);

    return {
        shareDisabled: row?.document_token == null || (!archived && imported),
        editVisible: !archived,
        editDisabled: archived || !row?.payment || !canUpdate || imported,
        previewDisabled: false,
        deleteDisabled: !canDelete,
    };
}

export function openInvoiceDialog(_state, type, row) {
    if (!supportedDialogTypes.has(type)) {
        throw new Error(`Unsupported invoice dialog: ${type}`);
    }

    return {type, row};
}

export function closeInvoiceDialog() {
    return {type: null, row: null};
}

export function buildInvoiceDocumentUrl(baseUrl, row, download) {
    return `${baseUrl}/${row.document_pdf}?download=${download ? 'true' : 'false'}&token=${row.document_token}`;
}

export function reloadInvoiceList(reload) {
    reload();
}
