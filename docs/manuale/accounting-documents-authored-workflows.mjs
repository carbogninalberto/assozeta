// Explicit local scenarios; no fiscal, rendering or delivery proof.
export const accountingDocumentsAuthoredWorkflows = Object.freeze({
    "accounting-suppliers-manage": {
        "version": 1,
        "script": "selfhost/tests/browser/manuale/accounting-suppliers.mjs",
        "prefix": "images/contabilita/anagrafiche-reali/",
        "pages": [
            "docs/contabilita-avanzata.mdx"
        ],
        "sources": [
            "UI/src/components/Sidebar.svelte",
            "UI/src/routes.js",
            "UI/src/utils/Permissions.js",
            "UI/src/components/tables/BKNDatatable.svelte",
            "BE/application/views/supplier_views.py",
            "BE/application/models/payment_models.py",
            "BE/application/serializers/payment_serializers.py",
            "BE/application/permissions_registry.py",
            "BE/application/urls.py",
            "UI/src/routes/accounting/suppliers-and-customers/SuppliersAndCustomers.svelte",
            "UI/src/routes/accounting/suppliers-and-customers/SupplierDrawer.svelte",
            "UI/src/routes/accounting/suppliers-and-customers/detail/Detail.svelte",
            "UI/src/routes/accounting/suppliers-and-customers/detail/sections/Info.svelte",
            "UI/src/routes/accounting/suppliers-and-customers/partials/supplier-customer-form.svelte",
            "UI/src/components/drawer/basic-drawer.svelte",
            "UI/src/components/inputs/BottomBarFixedSave.svelte",
            "UI/src/components/formBuilder/preview-blocks/smart-select-input.svelte",
            "BE/application/views/payment_views.py"
        ],
        "dependencies": [
            "selfhost/tests/browser/manuale/scenario.mjs",
            "selfhost/tests/browser/manuale/frame.mjs",
            "selfhost/tests/browser/manuale/redaction.mjs",
            "selfhost/tests/browser/playwright.manual.config.mjs",
            "BE/application/management/commands/seed_manuale.py",
            "BE/application/management/commands/run_manuale_instance.py",
            "docs/manuale/accounting-documents-authored-workflows.mjs",
            "selfhost/tests/browser/manuale/accounting-suppliers.mjs"
        ],
        "checkpoints": [
            {
                "id": "suppliers-list-baseline",
                "caption": "Elenco Fornitori e Clienti prima delle anagrafiche di prova."
            },
            {
                "id": "supplier-filled-before-save",
                "caption": "Nuovo fornitore con nome tipo e recapiti fittizi."
            },
            {
                "id": "supplier-created-after-reload",
                "caption": "Fornitore conservato dopo il ricaricamento."
            },
            {
                "id": "customer-filled-before-save",
                "caption": "Nuovo cliente nel pannello Nuova anagrafica."
            },
            {
                "id": "customer-created-after-reload",
                "caption": "Cliente conservato nell’elenco dopo il ricaricamento."
            },
            {
                "id": "supplier-edit-filled",
                "caption": "Recapiti del fornitore aggiornati prima del salvataggio."
            },
            {
                "id": "supplier-edit-reopened",
                "caption": "Modifica salvata e riletta nel pannello del fornitore."
            },
            {
                "id": "supplier-delete-cancel",
                "caption": "Richiesta di eliminazione prima di Annulla."
            },
            {
                "id": "supplier-delete-confirm",
                "caption": "Conferma per eliminare la sola anagrafica di prova."
            },
            {
                "id": "supplier-deleted-after-reload",
                "caption": "Fornitore assente e cliente di controllo conservato."
            },
            {
                "id": "suppliers-baseline-restored",
                "caption": "Anagrafiche di prova rimosse con elenco iniziale conservato."
            }
        ],
        "sections": [
            {
                "path": "docs/contabilita-avanzata.mdx",
                "id": "fornitori-e-clienti",
                "title": "Fornitori e clienti",
                "checkpoints": [
                    "suppliers-list-baseline",
                    "supplier-created-after-reload",
                    "customer-created-after-reload"
                ],
                "images": [
                    {
                        "from": "/images/contabilita/anagrafiche-reali/1.placeholder.svg",
                        "checkpoint": "suppliers-list-baseline"
                    },
                    {
                        "from": "/images/contabilita/anagrafiche-reali/3.placeholder.svg",
                        "checkpoint": "supplier-created-after-reload"
                    },
                    {
                        "from": "/images/contabilita/anagrafiche-reali/5.placeholder.svg",
                        "checkpoint": "customer-created-after-reload"
                    }
                ],
                "insert_images": []
            },
            {
                "path": "docs/contabilita-avanzata.mdx",
                "id": "aggiungere-un-fornitore-o-cliente",
                "title": "Aggiungere un fornitore o cliente",
                "checkpoints": [
                    "supplier-filled-before-save",
                    "supplier-created-after-reload",
                    "customer-filled-before-save",
                    "customer-created-after-reload"
                ],
                "images": [
                    {
                        "from": "/images/contabilita/anagrafiche-reali/2.placeholder.svg",
                        "checkpoint": "supplier-filled-before-save"
                    },
                    {
                        "from": "/images/contabilita/anagrafiche-reali/3.placeholder.svg",
                        "checkpoint": "supplier-created-after-reload"
                    },
                    {
                        "from": "/images/contabilita/anagrafiche-reali/4.placeholder.svg",
                        "checkpoint": "customer-filled-before-save"
                    },
                    {
                        "from": "/images/contabilita/anagrafiche-reali/5.placeholder.svg",
                        "checkpoint": "customer-created-after-reload"
                    }
                ],
                "insert_images": []
            },
            {
                "path": "docs/contabilita-avanzata.mdx",
                "id": "modificare-un-fornitore-o-cliente",
                "title": "Modificare un fornitore o cliente",
                "checkpoints": [
                    "supplier-edit-filled",
                    "supplier-edit-reopened"
                ],
                "images": [
                    {
                        "from": "/images/contabilita/anagrafiche-reali/6.placeholder.svg",
                        "checkpoint": "supplier-edit-filled"
                    },
                    {
                        "from": "/images/contabilita/anagrafiche-reali/7.placeholder.svg",
                        "checkpoint": "supplier-edit-reopened"
                    }
                ],
                "insert_images": []
            },
            {
                "path": "docs/contabilita-avanzata.mdx",
                "id": "eliminare-un-fornitore-o-cliente",
                "title": "Eliminare un fornitore o cliente",
                "checkpoints": [
                    "supplier-delete-cancel",
                    "supplier-delete-confirm",
                    "supplier-deleted-after-reload"
                ],
                "images": [
                    {
                        "from": "/images/contabilita/anagrafiche-reali/8.placeholder.svg",
                        "checkpoint": "supplier-delete-cancel"
                    },
                    {
                        "from": "/images/contabilita/anagrafiche-reali/9.placeholder.svg",
                        "checkpoint": "supplier-delete-confirm"
                    },
                    {
                        "from": "/images/contabilita/anagrafiche-reali/10.placeholder.svg",
                        "checkpoint": "supplier-deleted-after-reload"
                    }
                ],
                "insert_images": []
            }
        ],
        "outcome": {
            "field": "accounting_documents_authored_workflow",
            "expected": {
                "supplier_created_via_ui": true,
                "customer_created_via_ui": true,
                "both_types_persist_after_reload": true,
                "supplier_edit_reopened": true,
                "delete_cancel_preserved_record": true,
                "supplier_deleted_after_reload": true,
                "control_customer_preserved": true,
                "reader_write_denials": 4,
                "reader_denials_preserve_state": true,
                "baseline_suppliers_preserved": true,
                "baseline_payments_preserved": true,
                "owned_suppliers_removed": true
            }
        }
    },
    "accounting-supplier-invoices-manage": {
        "version": 1,
        "script": "selfhost/tests/browser/manuale/accounting-supplier-invoices.mjs",
        "prefix": "images/contabilita/fatture-passive-locali/",
        "pages": [
            "docs/contabilita-avanzata.mdx"
        ],
        "sources": [
            "UI/src/components/Sidebar.svelte",
            "UI/src/routes.js",
            "UI/src/utils/Permissions.js",
            "UI/src/components/tables/BKNDatatable.svelte",
            "BE/application/views/supplier_views.py",
            "BE/application/models/payment_models.py",
            "BE/application/serializers/payment_serializers.py",
            "BE/application/permissions_registry.py",
            "BE/application/urls.py",
            "BE/application/views/invoice_views.py",
            "BE/application/serializers/invoice_serializers.py",
            "BE/application/models/invoices_models.py",
            "BE/application/serializers/balance_sheet.py",
            "UI/src/routes/accounting/suppliersInvoice/SuppliersInvoiceList.svelte",
            "UI/src/routes/accounting/suppliersInvoice/modals/EditModal.svelte",
            "UI/src/components/inputs/DateInput.svelte",
            "UI/src/routes/accounting/payment/category/Categories.svelte",
            "BE/application/views/payment_views.py",
            "BE/application/views/balance_sheet_views.py",
            "BE/application/models/balance_sheet_models.py"
        ],
        "dependencies": [
            "selfhost/tests/browser/manuale/scenario.mjs",
            "selfhost/tests/browser/manuale/frame.mjs",
            "selfhost/tests/browser/manuale/redaction.mjs",
            "selfhost/tests/browser/playwright.manual.config.mjs",
            "BE/application/management/commands/seed_manuale.py",
            "BE/application/management/commands/run_manuale_instance.py",
            "docs/manuale/accounting-documents-authored-workflows.mjs",
            "selfhost/tests/browser/manuale/accounting-supplier-invoices.mjs"
        ],
        "checkpoints": [
            {
                "id": "supplier-invoice-fields-before-save",
                "caption": "Nuova fattura fornitore con importo conto scadenza e stato Non pagata."
            },
            {
                "id": "supplier-invoice-created-after-reload",
                "caption": "Fattura passiva salvata e presente nell’elenco."
            },
            {
                "id": "supplier-invoice-reopened-immutable-fields",
                "caption": "Fattura riaperta con importo conto e fornitore non modificabili."
            },
            {
                "id": "supplier-invoice-edit-before-save",
                "caption": "Identificativo note e scadenza prima del salvataggio."
            },
            {
                "id": "supplier-invoice-edit-after-reload",
                "caption": "Fattura aggiornata dopo il ricaricamento."
            },
            {
                "id": "supplier-invoice-paid-before-save",
                "caption": "Stato Pagata selezionato prima del salvataggio."
            },
            {
                "id": "supplier-invoice-paid-after-reload",
                "caption": "Fattura e spesa collegate con stato Pagata."
            },
            {
                "id": "supplier-invoice-paid-reopened",
                "caption": "Stato Pagata conservato dopo la riapertura."
            },
            {
                "id": "supplier-invoice-unpaid-restored",
                "caption": "Fattura di prova riportata a Non pagata dopo il controllo."
            }
        ],
        "sections": [
            {
                "path": "docs/contabilita-avanzata.mdx",
                "id": "fatture-passive",
                "title": "Fatture passive",
                "checkpoints": [
                    "supplier-invoice-created-after-reload",
                    "supplier-invoice-reopened-immutable-fields"
                ],
                "images": [
                    {
                        "from": "/images/contabilita/fatture-passive-locali/2.placeholder.svg",
                        "checkpoint": "supplier-invoice-created-after-reload"
                    },
                    {
                        "from": "/images/contabilita/fatture-passive-locali/3.placeholder.svg",
                        "checkpoint": "supplier-invoice-reopened-immutable-fields"
                    }
                ],
                "insert_images": []
            },
            {
                "path": "docs/contabilita-avanzata.mdx",
                "id": "registrare-una-fattura-passiva",
                "title": "Registrare una fattura passiva",
                "checkpoints": [
                    "supplier-invoice-fields-before-save",
                    "supplier-invoice-created-after-reload",
                    "supplier-invoice-reopened-immutable-fields"
                ],
                "images": [
                    {
                        "from": "/images/contabilita/fatture-passive-locali/1.placeholder.svg",
                        "checkpoint": "supplier-invoice-fields-before-save"
                    },
                    {
                        "from": "/images/contabilita/fatture-passive-locali/2.placeholder.svg",
                        "checkpoint": "supplier-invoice-created-after-reload"
                    },
                    {
                        "from": "/images/contabilita/fatture-passive-locali/3.placeholder.svg",
                        "checkpoint": "supplier-invoice-reopened-immutable-fields"
                    }
                ],
                "insert_images": []
            },
            {
                "path": "docs/contabilita-avanzata.mdx",
                "id": "modificare-una-fattura-passiva",
                "title": "Modificare una fattura passiva",
                "checkpoints": [
                    "supplier-invoice-reopened-immutable-fields",
                    "supplier-invoice-edit-before-save",
                    "supplier-invoice-edit-after-reload"
                ],
                "images": [
                    {
                        "from": "/images/contabilita/fatture-passive-locali/3.placeholder.svg",
                        "checkpoint": "supplier-invoice-reopened-immutable-fields"
                    },
                    {
                        "from": "/images/contabilita/fatture-passive-locali/4.placeholder.svg",
                        "checkpoint": "supplier-invoice-edit-before-save"
                    },
                    {
                        "from": "/images/contabilita/fatture-passive-locali/5.placeholder.svg",
                        "checkpoint": "supplier-invoice-edit-after-reload"
                    }
                ],
                "insert_images": []
            },
            {
                "path": "docs/contabilita-avanzata.mdx",
                "id": "segnare-una-fattura-passiva-come-pagata",
                "title": "Segnare una fattura passiva come pagata",
                "checkpoints": [
                    "supplier-invoice-paid-before-save",
                    "supplier-invoice-paid-after-reload",
                    "supplier-invoice-paid-reopened"
                ],
                "images": [
                    {
                        "from": "/images/contabilita/fatture-passive-locali/6.placeholder.svg",
                        "checkpoint": "supplier-invoice-paid-before-save"
                    },
                    {
                        "from": "/images/contabilita/fatture-passive-locali/7.placeholder.svg",
                        "checkpoint": "supplier-invoice-paid-after-reload"
                    },
                    {
                        "from": "/images/contabilita/fatture-passive-locali/8.placeholder.svg",
                        "checkpoint": "supplier-invoice-paid-reopened"
                    }
                ],
                "insert_images": []
            }
        ],
        "outcome": {
            "field": "accounting_documents_authored_workflow",
            "expected": {
                "invoice_created_via_ui": true,
                "invoice_payment_link_preserved": true,
                "expense_amount": 12,
                "created_unpaid": true,
                "readonly_amount_account_supplier": true,
                "invoice_edit_reopened": true,
                "expense_amount_preserved_after_edit": true,
                "paid_via_ui_persisted": true,
                "paid_expense_persisted": true,
                "paid_state_reopened": true,
                "unpaid_state_restored": true,
                "reader_write_denials": 3,
                "reader_denials_preserve_state": true,
                "owned_invoice_removed": true,
                "owned_expense_removed": true,
                "owned_supplier_removed": true,
                "baseline_invoices_preserved": true,
                "baseline_payments_preserved": true,
                "baseline_accounts_preserved": true,
                "renderer_or_delivery_exercised": false
            }
        }
    }
});

// Conservative full-file contexts of the reused legacy chapter contracts.
const additionalReviewedCaptureContexts = [
  {
    "id": "accounting-suppliers-manage",
    "sources": [
      "BE/application/impersonation.py",
      "BE/application/models/balance_sheet_models.py",
      "BE/application/models/invoices_models.py",
      "BE/application/serializers/balance_sheet.py",
      "BE/application/serializers/invoice_serializers.py",
      "BE/application/utils/api_utils.py",
      "BE/application/utils/balance_sheet_utils.py",
      "BE/application/views/balance_sheet_views.py",
      "BE/application/views/invoice_views.py",
      "UI/src/components/invoice/InvoiceForm.svelte",
      "UI/src/routes/accounting/accounts/Accounts.svelte",
      "UI/src/routes/accounting/accounts/modals/EditModal.svelte",
      "UI/src/routes/accounting/accountsTransfer/AccountsTransfer.svelte",
      "UI/src/routes/accounting/balanceSheet/BalanceSheetEditor.svelte",
      "UI/src/routes/accounting/balanceSheet/balanceInputs/EditableSection.svelte",
      "UI/src/routes/accounting/balanceSheet/balanceInputs/NumericInput.svelte",
      "UI/src/routes/accounting/customersInvoice/CustomersInvoiceList.svelte",
      "UI/src/routes/accounting/customersInvoice/modals/EditModal.svelte",
      "UI/src/routes/accounting/payment/category/Categories.svelte",
      "UI/src/routes/accounting/payment/category/modals/EditModal.svelte",
      "UI/src/routes/accounting/payment/modals/partials/meta-payment-categories.svelte",
      "UI/src/routes/accounting/suppliersInvoice/SuppliersInvoiceList.svelte",
      "UI/src/routes/accounting/suppliersInvoice/modals/EditModal.svelte",
      "UI/src/routes/profile/sections/Settings.svelte"
    ],
    "script": "selfhost/tests/browser/manuale/accounting-suppliers.mjs"
  },
  {
    "id": "accounting-supplier-invoices-manage",
    "sources": [
      "BE/application/impersonation.py",
      "BE/application/utils/api_utils.py",
      "BE/application/utils/balance_sheet_utils.py",
      "UI/src/components/invoice/InvoiceForm.svelte",
      "UI/src/routes/accounting/accounts/Accounts.svelte",
      "UI/src/routes/accounting/accounts/modals/EditModal.svelte",
      "UI/src/routes/accounting/accountsTransfer/AccountsTransfer.svelte",
      "UI/src/routes/accounting/balanceSheet/BalanceSheetEditor.svelte",
      "UI/src/routes/accounting/balanceSheet/balanceInputs/EditableSection.svelte",
      "UI/src/routes/accounting/balanceSheet/balanceInputs/NumericInput.svelte",
      "UI/src/routes/accounting/customersInvoice/CustomersInvoiceList.svelte",
      "UI/src/routes/accounting/customersInvoice/modals/EditModal.svelte",
      "UI/src/routes/accounting/payment/category/modals/EditModal.svelte",
      "UI/src/routes/accounting/payment/modals/partials/meta-payment-categories.svelte",
      "UI/src/routes/accounting/suppliers-and-customers/detail/sections/Info.svelte",
      "UI/src/routes/accounting/suppliers-and-customers/partials/supplier-customer-form.svelte",
      "UI/src/routes/profile/sections/Settings.svelte"
    ],
    "script": "selfhost/tests/browser/manuale/accounting-supplier-invoices.mjs"
  }
];
for (const item of additionalReviewedCaptureContexts) {
    const spec = accountingDocumentsAuthoredWorkflows[item.id];
    spec.sources = [...new Set([...spec.sources, ...item.sources])];
    spec.dependencies = [...new Set([...spec.dependencies, ...item.sources])];
}
