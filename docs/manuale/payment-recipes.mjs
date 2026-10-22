// Profile, association and mapped-owner table mutations commit with their audit indexes;
// any index exception rolls back the mutation. Locked reads and selected columns preserve
// concurrent changes. Successful settings and registration workflows remain unchanged.
// Implementation contracts only. User-facing prose is maintained in Bakney/manuale MDX.
import {editorialDescriptors, referenceDescriptors} from './evidence-contracts.mjs';
const reviewed = {
  "UI/src/components/Sidebar.svelte": "da92d11c5ca4d82b3e923cb221c29ee348f0f4df57c79df38d2af9bc33bc487a",
  "UI/src/routes.js": "955b24fdb6a39ee1e4a59b37bc94ca10d3245c03af469e10114e3c369d706a59",
  "UI/src/utils/Permissions.js": "bf5966b4e5f67d53a7e59e84433e144bfeb5cbbea8a1842f13c10376f8405644",
  "UI/src/routes/accounting/payment/modals/AddEditModal.svelte": "b40fb3c86aa525f87e4f7268ddb7dede3f544e959d28e6baa227419d615a009e",
  "UI/src/routes/accounting/payment/PaymentList.svelte": "31d3e103a34980df77ffe1178198789c0411f819f3ade64baba89ab0dcf3870a",
  "BE/application/views/payment_views.py": "4f45b99318afaa7189177f73cf82863f6f281dc8a2992b15dcf299b11f8e8c34",
  "BE/application/serializers/payment_serializers.py": "abc31bd21b536db8cfe5972c24d0befed834914fb8bdeb832205c2e8b62b4e4f",
  "BE/application/models/user_models.py": "fa9e0b76b80640aedc0e070505c93a75ee5f8c6220a62c07d6fb2e047626c2f6",
  "BE/application/models/payment_models.py": "9dbcab8698adbde447a9c4f6eb52143409cf197fde3ce90d0aa8da56a1f8ebda",
  "BE/application/models/invoices_models.py": "5848c1bcdbafdd6d644f5dd70e82979095d1bdbe85c989075784e54418fc1af6",
  "BE/application/services/invoice_service.py": "98ba3acf0950b310fb5e47abbd8ba57cc9737b757fa461e5c2989ab18af95519",
  "BE/application/permissions_registry.py": "b8318ad63daa9452ac736b2152449b14c49f73bc1417622a4181b7c3ceb3b1fa",
  "UI/src/routes/accounting/receipts/ReceiptList.svelte": "69b7c0c6188590a5be6db126e497cc1e7aa9dabf5ef91e2e07f05499928b7e51",
  "UI/src/routes/accounting/receipts/invoiceActionState.js": "6c1c7673e6dd2e227bdd8f50ea6bea439662b15ebd414b4bd011d39b22432289",
  "UI/src/components/modals/InvoicePreviewModal.svelte": "d65ee4f5163e304ff2f4a5de11fbca7f724c388e7c1e652e3d1b44d98fc21ed6",
  "UI/src/routes/accounting/receipts/modals/ShareModal.svelte": "0c86445d001a0d9141f26b673471a5b8b1ed1b9f15dc22264e125dc34d61bda3",
  "BE/application/views/invoice_views.py": "b6db30b40d0c54c663d5d80a7037161bd68642aeb1056926f5d09baba08394b5",
  "BE/application/printing_tasks.py": "858df2f90a7788ca9c2a27ce43266b62d8ec9b1f86ab197570834975b6b2fde9",
  "BE/docmanager/views/printing_views.py": "f9127869836c46408d9e5f48fc16733c6fce66efedb2e2fa7f96344095e01753",
  "BE/docmanager/views/document_view.py": "990fa77d97d984ed0971b230ff8d20e36a8b4a65c2c21222f6b580cca5fb3b37",
  "BE/application/utils/printing.py": "58461a8b9022432f807ba7d912821aaae4ab2e44608c58d43e4280556ee7c760",
  "BE/templates/document/application/invoice.html": "5a0b5f66655c8b7d288a42dba051e9a9ff2cc3e49e7f6227f442ddd2c926039d",
  "BE/docmanager/tasks.py": "ffd749d2177f27b1dff05dd9245f96ac21579782e39cb6882aa46fe79befbc87",
  "UI/src/components/filters/FilterSelect.svelte": "556100c1c9aecd36f7aad7a3de0ad389ecd908517a8e8934586474ff111c35e5",
  "UI/src/routes/accounting/receipts/modals/EditModal.svelte": "33f23e11f3b2d8e8abc3ec748c8190915d6114f5128cadc7664d8824304f5873",
  "UI/src/routes/profile/sections/Settings.svelte": "aed683b4bbb10c7a226a6eed573d1d748defc4d8baa7c13fd121e833b6561901",
  "BE/application/views/profile_views.py": "f542c0007dfeaf0e2dcdd1ca1063d26a1b5f853b2c9eab0290b6ce1eefc8b130"
};
const contracts = [
  ["UI/src/components/Sidebar.svelte","<span class=\"menu-text\">Pagamenti</span>",53],
  ["UI/src/routes.js","'/payment/list/:id?':",30],
  ["UI/src/utils/Permissions.js","export const canPerformAction",30],
  ["UI/src/routes/accounting/payment/modals/AddEditModal.svelte","async function create(formData)",45],
  ["UI/src/routes/accounting/payment/modals/AddEditModal.svelte","async function update(formData)",40],
  ["UI/src/routes/accounting/payment/modals/AddEditModal.svelte","label: 'Descrizione'",86],
  ["UI/src/routes/accounting/payment/PaymentList.svelte","window.markAsPaid =",111],
  ["BE/application/views/payment_views.py","def payment_add(request):",112],
  ["BE/application/views/payment_views.py","def payment_update(request, uid):",160],
  ["BE/application/views/payment_views.py","def payment_approve(request, uid):",178],
  ["BE/application/serializers/payment_serializers.py","class PaymentOptimizedSerializer(",52],
  ["BE/application/models/user_models.py","auto_paid_payment =",7],
  ["BE/application/models/payment_models.py","class Payment(GroupModelMixin):",68],
  ["BE/application/models/invoices_models.py","class Invoice(GroupModelMixin):",49],
  ["BE/application/services/invoice_service.py","def get_next_invoice_number(",89],
  ["BE/application/permissions_registry.py","'payment/add'",14],
  ["UI/src/routes.js","'/invoice/list':",19],
  ["UI/src/routes/accounting/receipts/ReceiptList.svelte","const availability = getInvoiceActionAvailability(",60],
  ["UI/src/routes/accounting/receipts/invoiceActionState.js","export function getInvoiceActionAvailability(",17],
  ["UI/src/components/modals/InvoicePreviewModal.svelte","<iframe",8],
  ["UI/src/routes/accounting/receipts/ReceiptList.svelte","pdfLink={getInvoiceDocumentUrl(invoiceDialog.row, true)}",19],
  ["UI/src/routes/accounting/receipts/modals/ShareModal.svelte","<Clipboard",32],
  ["BE/application/views/payment_views.py","def payment_cancel(request, uid):",43],
  ["BE/application/views/invoice_views.py","def invoice_list(request):",161],
  ["BE/application/printing_tasks.py","def print_document_invoice(",11],
  ["BE/docmanager/views/printing_views.py","def document_invoice(request, uid):",52],
  ["BE/docmanager/views/document_view.py","def retrieve_document(request, uid):",80],
  ["BE/application/utils/printing.py","def print_and_store_pdf(",48],
  ["BE/templates/document/application/invoice.html","{% if enumerate_invoices is True %}",4],
  ["BE/docmanager/tasks.py","@shared_task(name='save_to_storage')",15],
  ["BE/application/permissions_registry.py","'invoice/list'",9],
  ["UI/src/components/Sidebar.svelte","<span class=\"menu-text\">Documenti fiscali</span>",32],
  ["UI/src/routes/accounting/receipts/ReceiptList.svelte","window.deleteInvoice =",43],
  ["UI/src/routes/accounting/receipts/ReceiptList.svelte","const availability = getInvoiceActionAvailability(",75],
  ["UI/src/routes/accounting/receipts/ReceiptList.svelte","label=\"Anno ricevute\"",8],
  ["UI/src/components/filters/FilterSelect.svelte","function change(event)",20],
  ["UI/src/routes/accounting/receipts/modals/EditModal.svelte","async function update(data)",38],
  ["UI/src/routes/accounting/receipts/modals/EditModal.svelte","name=\"number\"",20],
  ["UI/src/routes/profile/sections/Settings.svelte","bind:checked={settings.enumerate_invoices}",41],
  ["BE/application/views/profile_views.py","def profile_info(request):",34],
  ["BE/application/views/invoice_views.py","def invoice_update(request, uid):",62],
  ["BE/application/views/invoice_views.py","def invoice_delete(request, uid):",31],
  ["BE/application/models/user_models.py","temporary_invoice_deletion =",11],
  ["BE/application/models/user_models.py","def get_invoice_template(",9],
  ["BE/application/models/payment_models.py","invoice = models.ForeignKey(Invoice",19],
  ["BE/docmanager/views/printing_views.py","def document_invoice_view(request, uid):",217]
];
const sections = [
  {
    "path": "docs/pagamenti.mdx",
    "id": "creare-un-pagamento",
    "title": "Creare un pagamento",
    "status": "pending",
    "reason": "Procedura in attesa di esecuzione e revisione delle fonti.",
    "contracts": [0,1,2,3,4,5,6,7,8,9,10,11,12,13,14,15]
  },
  {
    "path": "docs/pagamenti.mdx",
    "id": "modificare-un-pagamento",
    "title": "Modificare un pagamento",
    "status": "pending",
    "reason": "Procedura in attesa di esecuzione e revisione delle fonti.",
    "contracts": [0,1,2,3,4,5,6,7,8,9,10,11,12,13,14,15]
  },
  {
    "path": "docs/pagamenti.mdx",
    "id": "segna-un-pagamento-come-pagato",
    "title": "Segna un pagamento come \"pagato\"",
    "status": "pending",
    "reason": "Procedura in attesa di esecuzione e revisione delle fonti.",
    "contracts": [0,1,2,3,4,5,6,7,8,9,10,11,12,13,14,15]
  },
  {
    "path": "docs/ricevute.mdx",
    "id": "come-vengono-emesse-le-ricevute",
    "title": "Come vengono emesse le ricevute?",
    "status": "pending",
    "reason": "Procedura in attesa di esecuzione e revisione delle fonti.",
    "contracts": [0,1,2,3,4,5,6,7,8,9,10,11,12,13,14,15,16,17,18,19,20,21,22,23,24,25,26,27,28,29,30,31,32,33,34,35,36,37,38,39,40,41,42,43,44,45]
  },
  {
    "path": "docs/ricevute.mdx",
    "id": "numerazione-progressiva",
    "title": "Numerazione progressiva",
    "status": "pending",
    "reason": "Procedura in attesa di esecuzione e revisione delle fonti.",
    "contracts": [0,1,2,3,4,5,6,7,8,9,10,11,12,13,14,15,16,17,18,19,20,21,22,23,24,25,26,27,28,29,30,31,32,33,34,35,36,37,38,39,40,41,42,43,44,45]
  },
  {
    "path": "docs/ricevute.mdx",
    "id": "come-posso-scaricare-una-ricevuta",
    "title": "Come posso scaricare una ricevuta?",
    "status": "pending",
    "reason": "Procedura in attesa di esecuzione e revisione delle fonti.",
    "contracts": [0,1,2,3,4,5,6,7,8,9,10,11,12,13,14,15,16,17,18,19,20,21,22,23,24,25,26,27,28,29,30,31,32,33,34,35,36,37,38,39,40,41,42,43,44,45]
  },
  {
    "path": "docs/ricevute.mdx",
    "id": "modifica-del-progressivo-della-ricevuta",
    "title": "Modifica del progressivo della ricevuta",
    "status": "pending",
    "reason": "Procedura in attesa di esecuzione e revisione delle fonti.",
    "contracts": [0,1,2,3,4,5,6,7,8,9,10,11,12,13,14,15,16,17,18,19,20,21,22,23,24,25,26,27,28,29,30,31,32,33,34,35,36,37,38,39,40,41,42,43,44,45]
  },
  {
    "path": "docs/ricevute.mdx",
    "id": "eliminare-una-ricevuta",
    "title": "Eliminare una ricevuta",
    "status": "pending",
    "reason": "Procedura in attesa di esecuzione e revisione delle fonti.",
    "contracts": [0,1,2,3,4,5,6,7,8,9,10,11,12,13,14,15,16,17,18,19,20,21,22,23,24,25,26,27,28,29,30,31,32,33,34,35,36,37,38,39,40,41,42,43,44,45]
  },
  {
    "path": "docs/ricevute.mdx",
    "id": "configurazioni-possibili",
    "title": "Configurazioni possibili",
    "status": "pending",
    "reason": "Procedura in attesa di esecuzione e revisione delle fonti.",
    "contracts": [0,1,2,3,4,5,6,7,8,9,10,11,12,13,14,15,16,17,18,19,20,21,22,23,24,25,26,27,28,29,30,31,32,33,34,35,36,37,38,39,40,41,42,43,44,45]
  }
];
export function paymentEditorialContracts() {
    return editorialDescriptors("docs/manuale/payment-recipes.mjs", sections, contracts, reviewed);
}
