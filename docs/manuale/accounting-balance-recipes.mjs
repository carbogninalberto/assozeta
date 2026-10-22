// Implementation contracts only. User-facing prose is maintained in Bakney/manuale MDX.
import {editorialDescriptors, referenceDescriptors} from './evidence-contracts.mjs';
const reviewed = {
  "UI/src/components/Sidebar.svelte": "da92d11c5ca4d82b3e923cb221c29ee348f0f4df57c79df38d2af9bc33bc487a",
  "UI/src/routes.js": "955b24fdb6a39ee1e4a59b37bc94ca10d3245c03af469e10114e3c369d706a59",
  "UI/src/utils/Permissions.js": "bf5966b4e5f67d53a7e59e84433e144bfeb5cbbea8a1842f13c10376f8405644",
  "UI/src/routes/accounting/accounts/Accounts.svelte": "c8cd2533480465f1abd729ac3b822dfd2d1b967a48140d1081d6bc6664bf3d56",
  "UI/src/routes/accounting/accounts/modals/EditModal.svelte": "a1b92bebde85ff40520c7f563d7cbec3c6786f47426c12f8a77566cd85a9a413",
  "UI/src/routes/accounting/accountsTransfer/AccountsTransfer.svelte": "79b536b9887e5c70e99f6ba7e57f4c540ee659866e7b1236da9e803d7c52de0f",
  "UI/src/routes/accounting/balanceSheet/BalanceSheetEditor.svelte": "2af140853dabbcc5b989229cead80dc0e9bb5f16c94a0450ed4bb6dab563740d",
  "UI/src/routes/accounting/balanceSheet/balanceInputs/EditableSection.svelte": "b7d892a8b298248a9f028c3e3c9988a42848bf3b0b5153aa08690111b71eef25",
  "UI/src/routes/accounting/balanceSheet/balanceInputs/NumericInput.svelte": "a50bcd1a4fab2f6da29ca726604bc69228c3b12dcc45cafae45a1d9a4d20ae73",
  "UI/src/routes/profile/sections/Settings.svelte": "aed683b4bbb10c7a226a6eed573d1d748defc4d8baa7c13fd121e833b6561901",
  "BE/application/views/balance_sheet_views.py": "2156393491297da7d0414f693564f905b295bb7ed33cefe1452182e85cf26070",
  "BE/application/serializers/balance_sheet.py": "718b937c896e289fcf232351859da538de98ea829511f1b93baefdbf45d7019e",
  "BE/application/models/balance_sheet_models.py": "08aef0835594ebea11087743e7e327289c2682a0944bc492eb9b2739c72b2aca",
  "BE/application/utils/balance_sheet_utils.py": "16edc940ec32f5a0ec086edd1a8b1852af19994a4f8499441297b2b7e79cd317",
  "BE/application/utils/api_utils.py": "5db286fc73e92356b476514f4d6561a3300b36668ee99831ca1b197835a5cabf",
  "BE/application/permissions_registry.py": "b8318ad63daa9452ac736b2152449b14c49f73bc1417622a4181b7c3ceb3b1fa",
  "BE/application/impersonation.py": "04337e8ece4eb8027f396392835170e56ff176a06b2565b70c87455417b5035e",
  "BE/application/models/payment_models.py": "9dbcab8698adbde447a9c4f6eb52143409cf197fde3ce90d0aa8da56a1f8ebda",
  "UI/src/routes/accounting/payment/category/Categories.svelte": "e358b8768d55f757cada0af4dabd2443e2168248057f5442f428a6bed58ad768",
  "UI/src/routes/accounting/payment/category/modals/EditModal.svelte": "270b2663c133c813a352959e9b4177ae4f6361ce879537242a7d9af06bed51a8",
  "UI/src/routes/accounting/payment/modals/partials/meta-payment-categories.svelte": "f308dd00d75f43f09aa620da3aea449bfa3a3fc42f60e037c6d72ac670a50602",
  "BE/application/views/payment_views.py": "4f45b99318afaa7189177f73cf82863f6f281dc8a2992b15dcf299b11f8e8c34",
  "BE/application/serializers/payment_serializers.py": "abc31bd21b536db8cfe5972c24d0befed834914fb8bdeb832205c2e8b62b4e4f",
  "BE/application/views/supplier_views.py": "a332d25f18ba6b713cf75785aa58194bdf1165e313f8c3981efd33f3d3628321",
  "UI/src/routes/accounting/suppliers-and-customers/partials/supplier-customer-form.svelte": "05845d83a87bccabdf06c33a7cef340d146030056e6209f629da5f3ed9f92d0f",
  "UI/src/routes/accounting/suppliers-and-customers/detail/sections/Info.svelte": "424373aefff1e91347540e0c6f6236fd0a8bafc7e1859e559c3d3420b88602e6",
  "UI/src/routes/accounting/customersInvoice/CustomersInvoiceList.svelte": "5d9c9df2b2e8c6d57cbfb7736ca4da707b8b0ced3954317e456e2c52ddf5aade",
  "UI/src/routes/accounting/customersInvoice/modals/EditModal.svelte": "9af750273290f04d6237392c0306a167616732dc5b1cf14887d76b073aacb8c6",
  "UI/src/components/invoice/InvoiceForm.svelte": "2038f91a400229decbec387a661cdc95336afea0c2c2c565253826db67955b30",
  "UI/src/routes/accounting/suppliersInvoice/SuppliersInvoiceList.svelte": "d75d7352440b1a92ffe9b392b0cb0757605a96e3f64d638891a9db209872c5d5",
  "UI/src/routes/accounting/suppliersInvoice/modals/EditModal.svelte": "edaee3b087e7bc1d36b324e8328cb337979af4c2ac9ccea2bc4959ba0f86a6f6",
  "BE/application/views/invoice_views.py": "b6db30b40d0c54c663d5d80a7037161bd68642aeb1056926f5d09baba08394b5",
  "BE/application/serializers/invoice_serializers.py": "3a52147b36fe7977661358f629cfc45101aa59d15aae06f5f7078c1662ff3fb9",
  "BE/application/models/invoices_models.py": "5848c1bcdbafdd6d644f5dd70e82979095d1bdbe85c989075784e54418fc1af6"
};
const contracts = [
  ["UI/src/components/Sidebar.svelte","<span class=\"menu-text\">Gestione</span>",122],
  ["UI/src/routes.js","'/balance-sheet/list':",79],
  ["UI/src/utils/Permissions.js","export const canPerformAction",32],
  ["UI/src/routes/accounting/accounts/Accounts.svelte","async function create(data)",248],
  ["UI/src/routes/accounting/accounts/Accounts.svelte","text: 'Vuoi eliminare il conto?'",55],
  ["UI/src/routes/accounting/accounts/modals/EditModal.svelte","async function update(data)",224],
  ["UI/src/routes/accounting/accountsTransfer/AccountsTransfer.svelte","async function create(data)",336],
  ["UI/src/routes/accounting/accountsTransfer/AccountsTransfer.svelte","text: 'Vuoi eliminare il giroconto?'",57],
  ["UI/src/routes/accounting/balanceSheet/BalanceSheetEditor.svelte","async function fetchData()",118],
  ["UI/src/routes/accounting/balanceSheet/BalanceSheetEditor.svelte","Esporta PDF/Excel",325],
  ["UI/src/routes/accounting/balanceSheet/balanceInputs/EditableSection.svelte","export let editRow",195],
  ["UI/src/routes/accounting/balanceSheet/balanceInputs/NumericInput.svelte","function updateValue(event)",60],
  ["UI/src/routes/profile/sections/Settings.svelte",">Anno fiscale</label>",160],
  ["BE/application/views/balance_sheet_views.py","def balance_sheet(request):",282],
  ["BE/application/views/balance_sheet_views.py","def balance_sheet_accounts_list(request):",344],
  ["BE/application/serializers/balance_sheet.py","class CustomAccountSerializer(",206],
  ["BE/application/models/balance_sheet_models.py","class BalanceSheet(",88],
  ["BE/application/utils/balance_sheet_utils.py","def generate_balance_sheet(",163],
  ["BE/application/utils/api_utils.py","def get_range_from_year_and_starting_date(",68],
  ["BE/application/permissions_registry.py","('GET', 'balance-sheet')",12],
  ["BE/application/permissions_registry.py","def check_collaborator_permission(request):",62],
  ["BE/application/impersonation.py","def resolve_request_identity(",62],
  ["BE/application/models/payment_models.py","class PaymentCategory(",94],
  ["UI/src/routes/accounting/payment/category/Categories.svelte","async function create(data)",434],
  ["UI/src/routes/accounting/payment/category/modals/EditModal.svelte","async function update(data)",253],
  ["UI/src/routes/accounting/payment/modals/partials/meta-payment-categories.svelte","function addMetaPaymentCategory(",149],
  ["BE/application/views/payment_views.py","def payment_category_add(request):",87],
  ["BE/application/serializers/payment_serializers.py","class SupplierSerializer(",23],
  ["BE/application/views/supplier_views.py","def supplier_add(request):",135],
  ["UI/src/routes/accounting/suppliers-and-customers/partials/supplier-customer-form.svelte","function initForm()",380],
  ["UI/src/routes/accounting/suppliers-and-customers/detail/sections/Info.svelte","async function editData(e)",71],
  ["UI/src/routes/accounting/customersInvoice/CustomersInvoiceList.svelte","async function create(data)",260],
  ["UI/src/routes/accounting/customersInvoice/modals/EditModal.svelte","async function update(data)",149],
  ["UI/src/components/invoice/InvoiceForm.svelte","<label>Numero</label>",532],
  ["UI/src/routes/accounting/suppliersInvoice/SuppliersInvoiceList.svelte","async function create(data)",436],
  ["UI/src/routes/accounting/suppliersInvoice/modals/EditModal.svelte","async function update(data)",202],
  ["BE/application/views/invoice_views.py","def invoice_suppliers_add(request):",133],
  ["BE/application/views/invoice_views.py","def invoice_customers_add(request):",103],
  ["BE/application/serializers/invoice_serializers.py","class CustomerInvoiceSerializer(",67],
  ["BE/application/models/invoices_models.py","class CustomerInvoice(",159]
];
const sections = [
  {
    "path": "docs/contabilita-avanzata.mdx",
    "id": "panoramica",
    "title": "Panoramica",
    "status": "pending",
    "reason": "Procedura non esercitata in questo flusso; occorrono salvataggio, controllo dopo il ricaricamento e prova dei permessi.",
    "contracts": [0,1,2,3,4,5,6,7,8,9,10,11,12,13,14,15,16,17,18,19,20,21,22,23,24,25,26,27,28,29,30,31,32,33,34,35,36,37,38,39]
  },
  {
    "path": "docs/contabilita-avanzata.mdx",
    "id": "fatture-attive",
    "title": "Fatture attive",
    "status": "pending",
    "reason": "Procedura non esercitata in questo flusso; occorrono salvataggio, controllo dopo il ricaricamento e prova dei permessi.",
    "contracts": [0,1,2,3,4,5,6,7,8,9,10,11,12,13,14,15,16,17,18,19,20,21,22,23,24,25,26,27,28,29,30,31,32,33,34,35,36,37,38,39]
  },
  {
    "path": "docs/contabilita-avanzata.mdx",
    "id": "creare-una-fattura-attiva",
    "title": "Creare una fattura attiva",
    "status": "needs_external_verification",
    "reason": "Creazione e documenti non esercitati; la trasmissione esterna richiede una verifica autorizzata.",
    "contracts": [0,1,2,3,4,5,6,7,8,9,10,11,12,13,14,15,16,17,18,19,20,21,22,23,24,25,26,27,28,29,30,31,32,33,34,35,36,37,38,39]
  },
  {
    "path": "docs/contabilita-avanzata.mdx",
    "id": "modificare-una-fattura-attiva",
    "title": "Modificare una fattura attiva",
    "status": "unsupported",
    "reason": "La pagina Fatture Attive non monta un comando di modifica o incasso; il solo endpoint backend non costituisce una procedura disponibile.",
    "contracts": [0,1,2,3,4,5,6,7,8,9,10,11,12,13,14,15,16,17,18,19,20,21,22,23,24,25,26,27,28,29,30,31,32,33,34,35,36,37,38,39]
  },
  {
    "path": "docs/contabilita-avanzata.mdx",
    "id": "segnare-una-fattura-come-pagata",
    "title": "Segnare una fattura come pagata",
    "status": "unsupported",
    "reason": "La pagina Fatture Attive non monta un comando di modifica o incasso; il solo endpoint backend non costituisce una procedura disponibile.",
    "contracts": [0,1,2,3,4,5,6,7,8,9,10,11,12,13,14,15,16,17,18,19,20,21,22,23,24,25,26,27,28,29,30,31,32,33,34,35,36,37,38,39]
  },
  {
    "path": "docs/contabilita-avanzata.mdx",
    "id": "fatture-passive",
    "title": "Fatture passive",
    "status": "pending",
    "reason": "Procedura non esercitata in questo flusso; occorrono salvataggio, controllo dopo il ricaricamento e prova dei permessi.",
    "contracts": [0,1,2,3,4,5,6,7,8,9,10,11,12,13,14,15,16,17,18,19,20,21,22,23,24,25,26,27,28,29,30,31,32,33,34,35,36,37,38,39]
  },
  {
    "path": "docs/contabilita-avanzata.mdx",
    "id": "registrare-una-fattura-passiva",
    "title": "Registrare una fattura passiva",
    "status": "pending",
    "reason": "Procedura non esercitata in questo flusso; occorrono salvataggio, controllo dopo il ricaricamento e prova dei permessi.",
    "contracts": [0,1,2,3,4,5,6,7,8,9,10,11,12,13,14,15,16,17,18,19,20,21,22,23,24,25,26,27,28,29,30,31,32,33,34,35,36,37,38,39]
  },
  {
    "path": "docs/contabilita-avanzata.mdx",
    "id": "modificare-una-fattura-passiva",
    "title": "Modificare una fattura passiva",
    "status": "pending",
    "reason": "Procedura non esercitata in questo flusso; occorrono salvataggio, controllo dopo il ricaricamento e prova dei permessi.",
    "contracts": [0,1,2,3,4,5,6,7,8,9,10,11,12,13,14,15,16,17,18,19,20,21,22,23,24,25,26,27,28,29,30,31,32,33,34,35,36,37,38,39]
  },
  {
    "path": "docs/contabilita-avanzata.mdx",
    "id": "segnare-una-fattura-passiva-come-pagata",
    "title": "Segnare una fattura passiva come pagata",
    "status": "pending",
    "reason": "Procedura non esercitata in questo flusso; occorrono salvataggio, controllo dopo il ricaricamento e prova dei permessi.",
    "contracts": [0,1,2,3,4,5,6,7,8,9,10,11,12,13,14,15,16,17,18,19,20,21,22,23,24,25,26,27,28,29,30,31,32,33,34,35,36,37,38,39]
  },
  {
    "path": "docs/contabilita-avanzata.mdx",
    "id": "fornitori-e-clienti",
    "title": "Fornitori e clienti",
    "status": "pending",
    "reason": "Procedura non esercitata in questo flusso; occorrono salvataggio, controllo dopo il ricaricamento e prova dei permessi.",
    "contracts": [0,1,2,3,4,5,6,7,8,9,10,11,12,13,14,15,16,17,18,19,20,21,22,23,24,25,26,27,28,29,30,31,32,33,34,35,36,37,38,39]
  },
  {
    "path": "docs/contabilita-avanzata.mdx",
    "id": "aggiungere-un-fornitore-o-cliente",
    "title": "Aggiungere un fornitore o cliente",
    "status": "pending",
    "reason": "Procedura non esercitata in questo flusso; occorrono salvataggio, controllo dopo il ricaricamento e prova dei permessi.",
    "contracts": [0,1,2,3,4,5,6,7,8,9,10,11,12,13,14,15,16,17,18,19,20,21,22,23,24,25,26,27,28,29,30,31,32,33,34,35,36,37,38,39]
  },
  {
    "path": "docs/contabilita-avanzata.mdx",
    "id": "modificare-un-fornitore-o-cliente",
    "title": "Modificare un fornitore o cliente",
    "status": "pending",
    "reason": "Procedura non esercitata in questo flusso; occorrono salvataggio, controllo dopo il ricaricamento e prova dei permessi.",
    "contracts": [0,1,2,3,4,5,6,7,8,9,10,11,12,13,14,15,16,17,18,19,20,21,22,23,24,25,26,27,28,29,30,31,32,33,34,35,36,37,38,39]
  },
  {
    "path": "docs/contabilita-avanzata.mdx",
    "id": "eliminare-un-fornitore-o-cliente",
    "title": "Eliminare un fornitore o cliente",
    "status": "pending",
    "reason": "Procedura non esercitata in questo flusso; occorrono salvataggio, controllo dopo il ricaricamento e prova dei permessi.",
    "contracts": [0,1,2,3,4,5,6,7,8,9,10,11,12,13,14,15,16,17,18,19,20,21,22,23,24,25,26,27,28,29,30,31,32,33,34,35,36,37,38,39]
  },
  {
    "path": "docs/contabilita-avanzata.mdx",
    "id": "conti-finanziari",
    "title": "Conti finanziari",
    "status": "pending",
    "reason": "Procedura non esercitata in questo flusso; occorrono salvataggio, controllo dopo il ricaricamento e prova dei permessi.",
    "contracts": [0,1,2,3,4,5,6,7,8,9,10,11,12,13,14,15,16,17,18,19,20,21,22,23,24,25,26,27,28,29,30,31,32,33,34,35,36,37,38,39]
  },
  {
    "path": "docs/contabilita-avanzata.mdx",
    "id": "tipologie-di-conto",
    "title": "Tipologie di conto",
    "status": "pending",
    "reason": "Procedura non esercitata in questo flusso; occorrono salvataggio, controllo dopo il ricaricamento e prova dei permessi.",
    "contracts": [0,1,2,3,4,5,6,7,8,9,10,11,12,13,14,15,16,17,18,19,20,21,22,23,24,25,26,27,28,29,30,31,32,33,34,35,36,37,38,39]
  },
  {
    "path": "docs/contabilita-avanzata.mdx",
    "id": "creare-un-nuovo-conto",
    "title": "Creare un nuovo conto",
    "status": "pending",
    "reason": "Procedura non esercitata in questo flusso; occorrono salvataggio, controllo dopo il ricaricamento e prova dei permessi.",
    "contracts": [0,1,2,3,4,5,6,7,8,9,10,11,12,13,14,15,16,17,18,19,20,21,22,23,24,25,26,27,28,29,30,31,32,33,34,35,36,37,38,39]
  },
  {
    "path": "docs/contabilita-avanzata.mdx",
    "id": "modificare-un-conto",
    "title": "Modificare un conto",
    "status": "pending",
    "reason": "Procedura non esercitata in questo flusso; occorrono salvataggio, controllo dopo il ricaricamento e prova dei permessi.",
    "contracts": [0,1,2,3,4,5,6,7,8,9,10,11,12,13,14,15,16,17,18,19,20,21,22,23,24,25,26,27,28,29,30,31,32,33,34,35,36,37,38,39]
  },
  {
    "path": "docs/contabilita-avanzata.mdx",
    "id": "giroconti",
    "title": "Giroconti",
    "status": "pending",
    "reason": "Procedura non esercitata in questo flusso; occorrono salvataggio, controllo dopo il ricaricamento e prova dei permessi.",
    "contracts": [0,1,2,3,4,5,6,7,8,9,10,11,12,13,14,15,16,17,18,19,20,21,22,23,24,25,26,27,28,29,30,31,32,33,34,35,36,37,38,39]
  },
  {
    "path": "docs/contabilita-avanzata.mdx",
    "id": "creare-un-giroconto",
    "title": "Creare un giroconto",
    "status": "pending",
    "reason": "Procedura non esercitata in questo flusso; occorrono salvataggio, controllo dopo il ricaricamento e prova dei permessi.",
    "contracts": [0,1,2,3,4,5,6,7,8,9,10,11,12,13,14,15,16,17,18,19,20,21,22,23,24,25,26,27,28,29,30,31,32,33,34,35,36,37,38,39]
  },
  {
    "path": "docs/contabilita-avanzata.mdx",
    "id": "eliminare-un-giroconto",
    "title": "Eliminare un giroconto",
    "status": "pending",
    "reason": "Procedura non esercitata in questo flusso; occorrono salvataggio, controllo dopo il ricaricamento e prova dei permessi.",
    "contracts": [0,1,2,3,4,5,6,7,8,9,10,11,12,13,14,15,16,17,18,19,20,21,22,23,24,25,26,27,28,29,30,31,32,33,34,35,36,37,38,39]
  },
  {
    "path": "docs/contabilita-avanzata.mdx",
    "id": "causali-di-pagamento",
    "title": "Causali di pagamento",
    "status": "pending",
    "reason": "Procedura non esercitata in questo flusso; occorrono salvataggio, controllo dopo il ricaricamento e prova dei permessi.",
    "contracts": [0,1,2,3,4,5,6,7,8,9,10,11,12,13,14,15,16,17,18,19,20,21,22,23,24,25,26,27,28,29,30,31,32,33,34,35,36,37,38,39]
  },
  {
    "path": "docs/contabilita-avanzata.mdx",
    "id": "tipologie-di-causali",
    "title": "Tipologie di causali",
    "status": "pending",
    "reason": "Procedura non esercitata in questo flusso; occorrono salvataggio, controllo dopo il ricaricamento e prova dei permessi.",
    "contracts": [0,1,2,3,4,5,6,7,8,9,10,11,12,13,14,15,16,17,18,19,20,21,22,23,24,25,26,27,28,29,30,31,32,33,34,35,36,37,38,39]
  },
  {
    "path": "docs/contabilita-avanzata.mdx",
    "id": "creare-una-causale",
    "title": "Creare una causale",
    "status": "pending",
    "reason": "Procedura non esercitata in questo flusso; occorrono salvataggio, controllo dopo il ricaricamento e prova dei permessi.",
    "contracts": [0,1,2,3,4,5,6,7,8,9,10,11,12,13,14,15,16,17,18,19,20,21,22,23,24,25,26,27,28,29,30,31,32,33,34,35,36,37,38,39]
  },
  {
    "path": "docs/contabilita-avanzata.mdx",
    "id": "modificare-una-causale",
    "title": "Modificare una causale",
    "status": "pending",
    "reason": "Procedura non esercitata in questo flusso; occorrono salvataggio, controllo dopo il ricaricamento e prova dei permessi.",
    "contracts": [0,1,2,3,4,5,6,7,8,9,10,11,12,13,14,15,16,17,18,19,20,21,22,23,24,25,26,27,28,29,30,31,32,33,34,35,36,37,38,39]
  },
  {
    "path": "docs/contabilita-avanzata.mdx",
    "id": "causali-aggiuntive-nei-pagamenti",
    "title": "Causali aggiuntive nei pagamenti",
    "status": "pending",
    "reason": "Procedura non esercitata in questo flusso; occorrono salvataggio, controllo dopo il ricaricamento e prova dei permessi.",
    "contracts": [0,1,2,3,4,5,6,7,8,9,10,11,12,13,14,15,16,17,18,19,20,21,22,23,24,25,26,27,28,29,30,31,32,33,34,35,36,37,38,39]
  },
  {
    "path": "docs/contabilita-avanzata.mdx",
    "id": "causali-detraibili-730",
    "title": "Causali detraibili 730",
    "status": "pending",
    "reason": "Procedura non esercitata in questo flusso; occorrono salvataggio, controllo dopo il ricaricamento e prova dei permessi.",
    "contracts": [0,1,2,3,4,5,6,7,8,9,10,11,12,13,14,15,16,17,18,19,20,21,22,23,24,25,26,27,28,29,30,31,32,33,34,35,36,37,38,39]
  },
  {
    "path": "docs/bilancio.mdx",
    "id": "bilancio",
    "title": "Bilancio",
    "status": "pending",
    "reason": "Procedura non esercitata in questo flusso; occorrono salvataggio, controllo dopo il ricaricamento e prova dei permessi.",
    "contracts": [0,1,2,3,4,5,6,7,8,9,10,11,12,13,14,15,16,17,18,19,20,21,22,23,24,25,26,27,28,29,30,31,32,33,34,35,36,37,38,39]
  },
  {
    "path": "docs/bilancio.mdx",
    "id": "anno-fiscale",
    "title": "Anno fiscale",
    "status": "pending",
    "reason": "Procedura non esercitata in questo flusso; occorrono salvataggio, controllo dopo il ricaricamento e prova dei permessi.",
    "contracts": [0,1,2,3,4,5,6,7,8,9,10,11,12,13,14,15,16,17,18,19,20,21,22,23,24,25,26,27,28,29,30,31,32,33,34,35,36,37,38,39]
  },
  {
    "path": "docs/bilancio.mdx",
    "id": "conti-economici",
    "title": "Conti economici",
    "status": "pending",
    "reason": "Procedura non esercitata in questo flusso; occorrono salvataggio, controllo dopo il ricaricamento e prova dei permessi.",
    "contracts": [0,1,2,3,4,5,6,7,8,9,10,11,12,13,14,15,16,17,18,19,20,21,22,23,24,25,26,27,28,29,30,31,32,33,34,35,36,37,38,39]
  },
  {
    "path": "docs/bilancio.mdx",
    "id": "giroconti",
    "title": "Giroconti",
    "status": "pending",
    "reason": "Procedura non esercitata in questo flusso; occorrono salvataggio, controllo dopo il ricaricamento e prova dei permessi.",
    "contracts": [0,1,2,3,4,5,6,7,8,9,10,11,12,13,14,15,16,17,18,19,20,21,22,23,24,25,26,27,28,29,30,31,32,33,34,35,36,37,38,39]
  },
  {
    "path": "docs/bilancio.mdx",
    "id": "pubblicazione-del-bilancio",
    "title": "Pubblicazione del bilancio",
    "status": "pending",
    "reason": "Procedura non esercitata in questo flusso; occorrono salvataggio, controllo dopo il ricaricamento e prova dei permessi.",
    "contracts": [0,1,2,3,4,5,6,7,8,9,10,11,12,13,14,15,16,17,18,19,20,21,22,23,24,25,26,27,28,29,30,31,32,33,34,35,36,37,38,39]
  },
  {
    "path": "docs/bilancio.mdx",
    "id": "risorse-correlate",
    "title": "Risorse correlate",
    "status": "pending",
    "reason": "Procedura non esercitata in questo flusso; occorrono salvataggio, controllo dopo il ricaricamento e prova dei permessi.",
    "contracts": [0,1,2,3,4,5,6,7,8,9,10,11,12,13,14,15,16,17,18,19,20,21,22,23,24,25,26,27,28,29,30,31,32,33,34,35,36,37,38,39]
  },
  {
    "path": "faq/come-generare-bilancio.mdx",
    "id": "cos-e-il-bilancio",
    "title": "Cos'è il bilancio",
    "status": "pending",
    "reason": "Procedura non esercitata in questo flusso; occorrono salvataggio, controllo dopo il ricaricamento e prova dei permessi.",
    "contracts": [0,1,2,3,4,5,6,7,8,9,10,11,12,13,14,15,16,17,18,19,20,21,22,23,24,25,26,27,28,29,30,31,32,33,34,35,36,37,38,39]
  },
  {
    "path": "faq/come-generare-bilancio.mdx",
    "id": "configurare-i-conti-economici",
    "title": "Configurare i conti economici",
    "status": "pending",
    "reason": "Procedura non esercitata in questo flusso; occorrono salvataggio, controllo dopo il ricaricamento e prova dei permessi.",
    "contracts": [0,1,2,3,4,5,6,7,8,9,10,11,12,13,14,15,16,17,18,19,20,21,22,23,24,25,26,27,28,29,30,31,32,33,34,35,36,37,38,39]
  },
  {
    "path": "faq/come-generare-bilancio.mdx",
    "id": "i-giroconti-trasferimenti-tra-conti",
    "title": "I giroconti (trasferimenti tra conti)",
    "status": "pending",
    "reason": "Procedura non esercitata in questo flusso; occorrono salvataggio, controllo dopo il ricaricamento e prova dei permessi.",
    "contracts": [0,1,2,3,4,5,6,7,8,9,10,11,12,13,14,15,16,17,18,19,20,21,22,23,24,25,26,27,28,29,30,31,32,33,34,35,36,37,38,39]
  },
  {
    "path": "faq/come-generare-bilancio.mdx",
    "id": "generare-e-consultare-il-bilancio",
    "title": "Generare e consultare il bilancio",
    "status": "pending",
    "reason": "Procedura non esercitata in questo flusso; occorrono salvataggio, controllo dopo il ricaricamento e prova dei permessi.",
    "contracts": [0,1,2,3,4,5,6,7,8,9,10,11,12,13,14,15,16,17,18,19,20,21,22,23,24,25,26,27,28,29,30,31,32,33,34,35,36,37,38,39]
  },
  {
    "path": "faq/come-generare-bilancio.mdx",
    "id": "allineamento-dell-anno-fiscale",
    "title": "Allineamento dell'anno fiscale",
    "status": "pending",
    "reason": "Procedura non esercitata in questo flusso; occorrono salvataggio, controllo dopo il ricaricamento e prova dei permessi.",
    "contracts": [0,1,2,3,4,5,6,7,8,9,10,11,12,13,14,15,16,17,18,19,20,21,22,23,24,25,26,27,28,29,30,31,32,33,34,35,36,37,38,39]
  },
  {
    "path": "faq/come-generare-bilancio.mdx",
    "id": "pubblicare-il-bilancio",
    "title": "Pubblicare il bilancio",
    "status": "pending",
    "reason": "Procedura non esercitata in questo flusso; occorrono salvataggio, controllo dopo il ricaricamento e prova dei permessi.",
    "contracts": [0,1,2,3,4,5,6,7,8,9,10,11,12,13,14,15,16,17,18,19,20,21,22,23,24,25,26,27,28,29,30,31,32,33,34,35,36,37,38,39]
  }
];
export function accountingBalanceEditorialContracts() {
    return editorialDescriptors("docs/manuale/accounting-balance-recipes.mjs", sections, contracts, reviewed);
}
