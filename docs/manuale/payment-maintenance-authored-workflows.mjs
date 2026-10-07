// Execution contracts only. Italian prose remains in the reviewed manual checkout.
import {receiptSources} from '../../selfhost/tests/browser/manuale/payments-sources.mjs';

const sources = [...new Set([...receiptSources,
    'UI/src/components/buttons/DeleteButton.svelte',
    'UI/src/components/tables/BKNDatatable.svelte',
    'UI/src/components/filters/FilterSelect.svelte',
    'UI/src/components/dropdowns/basic-dropdown.svelte',
    'UI/src/components/inputs/DateRangePicker.svelte',
    'UI/src/components/inputs/DateRangeCalendar.svelte',
    'UI/src/utils/ApiMiddleware.js', 'UI/src/store/stores.js',
    'UI/src/routes/association/course/overview/OverviewCourse.svelte',
    'UI/src/routes/association/course/overview/partials/course-subscriptions-list.svelte',
    'UI/src/routes/association/course/overview/modals/AddEditCourseSubscriptionModal.svelte',
    'BE/application/views/course_views.py', 'BE/application/serializers/courses_serializers.py',
    'BE/application/models/courses_models.py', 'BE/application/signals.py',
    'BE/application/views/profile_views.py', 'BE/application/serializers/auth_serializers.py',
    'BE/application/utils/excel_utils.py', 'BE/application/urls.py',
])];
const point = (id, caption) => ({id, caption});
export const paymentMaintenanceAuthoredWorkflows = Object.freeze({
    'payments-maintenance': {
        version: 1, script: 'selfhost/tests/browser/manuale/payments-maintenance.mjs',
        prefix: 'images/pagamenti/manutenzione-reale/', pages: ['docs/pagamenti.mdx'], sources,
        dependencies: [...sources, 'selfhost/tests/browser/manuale/payments-sources.mjs',
            'selfhost/tests/browser/manuale/payments-maintenance.mjs',
            'docs/manuale/payment-maintenance-authored-workflows.mjs'],
        checkpoints: [
            point('export-baseline-payments', 'Tre quote associative: importi, stati e date prima delle esportazioni.'),
            point('general-export-options', 'CSV e Excel dall’inizio, distinti dalla ricerca corrente.'),
            point('filtered-payment-export', 'Ricerca di Sara Conti: una sola quota nell’ambito corrente.'),
            point('petty-cash-period-options', 'Prima nota: dall’inizio, mese corrente, mese scorso e periodo selezionato.'),
            point('downloaded-exports-state-preserved', 'Download reali completati: la ricerca resta visibile e le quote sono invariate.'),
            point('reader-filtered-export', 'Collaboratore in lettura: ricerca di Sara ed esportazione corrente autorizzata.'),
            point('payment-columns-picker', 'Scelte delle colonne Pagamenti prima della modifica di Metodo.'),
            point('payment-method-column-hidden', 'Metodo nascosto nella tabella, con le quote ancora presenti.'),
            point('payment-column-reopened', 'Metodo rimane nascosto dopo riapertura e ricaricamento della pagina.'),
            point('reader-columns-preserved', 'Le preferenze del collaboratore restano indipendenti dalla modifica del proprietario.'),
            point('owned-linked-payment-before-deletion', 'Rata dimostrativa incassata, con ricevuta reale collegata, prima dell’eliminazione.'),
            point('payment-deletion-cancel-confirmation', 'Conferma dell’eliminazione singola, prima di scegliere Annulla.'),
            point('payment-deletion-confirmation', 'Seconda conferma della rata corretta, prima di scegliere Elimina.'),
            point('deleted-payment-absent-after-reload', 'La quota eliminata non compare dopo il ricaricamento; le altre quote sono conservate.'),
            point('linked-receipt-cancelled', 'Ricevuta della rata eliminata indicata come annullata nell’elenco.'),
            point('regenerated-cancelled-receipt-pdf', 'PDF reale rigenerato dopo l’annullamento, senza collegamenti o token di condivisione.'),
            point('installment-plan-after-payment-deletion', 'Piano della singola iscrizione dopo l’eliminazione: rate, importi e scadenze da controllare separatamente.'),
        ],
        sections: [
            {path: 'docs/pagamenti.mdx', id: 'esportare-i-pagamenti', title: 'Esportare i pagamenti',
                remove_draft_text: ['Il download, il contenuto delle diverse varianti e l’apertura del file attendono una prova completa.'],
                checkpoints: ['export-baseline-payments', 'general-export-options', 'filtered-payment-export',
                    'petty-cash-period-options', 'downloaded-exports-state-preserved', 'reader-filtered-export'], images: [],
                insert_images: [{step: 'Scegli il tipo di esportazione', checkpoint: 'general-export-options'},
                    {step: 'Esporta una ricerca specifica', checkpoint: 'filtered-payment-export'},
                    {step: 'Controlla il file', checkpoint: 'downloaded-exports-state-preserved'}]},
            {path: 'docs/pagamenti.mdx', id: 'personalizzare-le-colonne-della-tabella', title: 'Personalizzare le colonne della tabella',
                remove_draft_text: ['La persistenza e il comportamento ai diversi permessi attendono una prova.'],
                checkpoints: ['payment-columns-picker', 'payment-method-column-hidden', 'payment-column-reopened', 'reader-columns-preserved'], images: [],
                insert_images: [{step: 'Apri le scelte della tabella', checkpoint: 'payment-columns-picker'},
                    {step: 'Scegli cosa mostrare', checkpoint: 'payment-method-column-hidden'},
                    {step: 'Controlla la riapertura', checkpoint: 'payment-column-reopened'}]},
            {path: 'docs/pagamenti.mdx', id: 'eliminare-un-pagamento', title: 'Eliminare un pagamento',
                checkpoints: ['owned-linked-payment-before-deletion', 'payment-deletion-cancel-confirmation',
                    'payment-deletion-confirmation', 'deleted-payment-absent-after-reload', 'linked-receipt-cancelled',
                    'regenerated-cancelled-receipt-pdf', 'installment-plan-after-payment-deletion'], images: [],
                insert_images: [{step: 'Individua la registrazione', checkpoint: 'owned-linked-payment-before-deletion'},
                    {step: 'Rivedi la conferma', checkpoint: 'payment-deletion-confirmation'},
                    {step: 'Controlla i documenti e le quote', checkpoint: 'regenerated-cancelled-receipt-pdf'},
                    {step: 'Controlla i documenti e le quote', checkpoint: 'installment-plan-after-payment-deletion'}]},
        ],
        outcome: {field: 'payment_maintenance_authored_workflow', expected: {
            baseline_payments: 3, general_csv_rows: 3, general_excel_rows: 3, filtered_excel_rows: 1,
            petty_cash_all_rows: 2, petty_cash_current_rows: 2, petty_cash_previous_rows: 0,
            petty_cash_selected_rows: 2, exports_match_backend_values: true, general_exports_ignore_search: true,
            filtered_export_matches_search: true, reader_export_rows: 1, exports_preserve_payments: true,
            column_hidden_locally: true, column_saved_server: true, column_hidden_after_reopen: true,
            reader_preferences_preserved: true, reader_preferences_write_status: 403,
            column_preferences_restored: true, cancellation_preserves_payment: true,
            reader_payment_delete_status: 403, reader_denial_preserves_payment: true,
            payment_deleted_after_reload: true, receipt_cancelled: true, receipt_pdf_regenerated: true,
            receipt_pdf_downloaded_and_previewed: true,
            linked_installment_removed_other_rows_preserved: true, remaining_payment_preserved: true,
            unrelated_payments_preserved: true, owned_runtime_objects_removed: true,
        }},
    },
    "payment-categories-manage": {
    "version": 1,
    "script": "selfhost/tests/browser/manuale/payment-categories.mjs",
    "prefix": "images/pagamenti/causali-reali/",
    "pages": [
        "docs/pagamenti.mdx"
    ],
    "sources": [
        "UI/src/routes/accounting/payment/modals/AddEditModal.svelte",
        "UI/src/components/Sidebar.svelte",
        "UI/src/routes.js",
        "UI/src/utils/Permissions.js",
        "UI/src/routes/accounting/payment/PaymentList.svelte",
        "UI/src/routes/accounting/payment/category/Categories.svelte",
        "UI/src/routes/accounting/payment/category/modals/EditModal.svelte",
        "UI/src/routes/accounting/payment/category/catgoryUtils.js",
        "UI/src/components/buttons/EditButton.svelte",
        "UI/src/components/buttons/DeleteButton.svelte",
        "UI/src/components/tables/BKNDatatable.svelte",
        "UI/src/components/formBuilder/preview-blocks/smart-select-input.svelte",
        "BE/application/views/payment_views.py",
        "BE/application/serializers/payment_serializers.py",
        "BE/application/models/payment_models.py",
        "BE/application/permissions_registry.py",
        "BE/application/urls.py"
    ],
    "dependencies": [
        "UI/src/components/Sidebar.svelte",
        "UI/src/routes.js",
        "UI/src/utils/Permissions.js",
        "UI/src/routes/accounting/payment/PaymentList.svelte",
        "UI/src/routes/accounting/payment/category/Categories.svelte",
        "UI/src/routes/accounting/payment/category/modals/EditModal.svelte",
        "UI/src/routes/accounting/payment/category/catgoryUtils.js",
        "UI/src/components/buttons/EditButton.svelte",
        "UI/src/components/buttons/DeleteButton.svelte",
        "UI/src/components/tables/BKNDatatable.svelte",
        "UI/src/components/formBuilder/preview-blocks/smart-select-input.svelte",
        "BE/application/views/payment_views.py",
        "BE/application/serializers/payment_serializers.py",
        "BE/application/models/payment_models.py",
        "BE/application/permissions_registry.py",
        "BE/application/urls.py",
        "selfhost/tests/browser/manuale/payment-categories.mjs",
        "docs/manuale/payment-maintenance-authored-workflows.mjs"
    ],
    "checkpoints": [
        {
            "id": "categories-baseline",
            "caption": "Causali esistenti, prima della creazione di una voce dell’associazione."
        },
        {
            "id": "category-fields-before-save",
            "caption": "Nome, movimento, tipologia e gestione IVA della nuova causale."
        },
        {
            "id": "category-created-after-reload",
            "caption": "Nuova causale salvata e presente dopo la ricarica."
        },
        {
            "id": "category-edit-before-save",
            "caption": "Modifica della sola causale dimostrativa dell’associazione."
        },
        {
            "id": "category-edit-after-reload",
            "caption": "Nome e classificazione modificati, conservati dopo la ricarica."
        },
        {
            "id": "category-delete-cancel-confirmation",
            "caption": "Conferma dell’eliminazione prima di scegliere Annulla."
        },
        {
            "id": "category-delete-confirmation",
            "caption": "Conferma dell’eliminazione della causale dimostrativa."
        },
        {
            "id": "category-deleted-after-reload",
            "caption": "Causale indicata come Eliminata, senza cancellare i pagamenti esistenti."
        }
    ],
    "sections": [
        {
            "path": "docs/pagamenti.mdx",
            "id": "gestione-delle-causali",
            "title": "Gestione delle causali",
            "checkpoints": [
                "categories-baseline",
                "category-fields-before-save",
                "category-created-after-reload",
                "category-edit-before-save",
                "category-edit-after-reload",
                "category-delete-cancel-confirmation",
                "category-delete-confirmation",
                "category-deleted-after-reload"
            ],
            "images": [],
            "insert_images": [
                {
                    "step": "Apri le causali",
                    "checkpoint": "categories-baseline"
                },
                {
                    "step": "Compila la classificazione",
                    "checkpoint": "category-fields-before-save"
                },
                {
                    "step": "Salva e ricontrolla",
                    "checkpoint": "category-created-after-reload"
                },
                {
                    "step": "Modifica una causale propria",
                    "checkpoint": "category-edit-after-reload"
                },
                {
                    "step": "Rimuovi una voce non più utilizzata",
                    "checkpoint": "category-deleted-after-reload"
                }
            ]
        }
    ],
    "outcome": {
        "field": "payment_categories_manage_authored_workflow",
        "expected": {
            "created_reopened": true,
            "edited_reopened": true,
            "delete_cancel_preserved": true,
            "soft_deleted_reopened": true,
            "reader_write_denials": 3,
            "shared_category_write_denials": 2,
            "baseline_categories_preserved": true,
            "payments_preserved": true,
            "owned_category_soft_deleted": true
        }
    }
},
});

// Additional complete bindings from the exhaustive guide review.
const exhaustiveGuideBindings = [
  {
    "workflow_id": "payment-categories-manage",
    "section": {
      "path": "docs/contabilita-avanzata.mdx",
      "id": "causali-di-pagamento",
      "title": "Causali di pagamento",
      "checkpoints": [
        "categories-baseline",
        "category-created-after-reload",
        "category-deleted-after-reload"
      ],
      "images": [
        {
          "from": "/images/pagamenti/causali-reali/1.placeholder.svg",
          "checkpoint": "categories-baseline"
        },
        {
          "from": "/images/pagamenti/causali-reali/3.placeholder.svg",
          "checkpoint": "category-created-after-reload"
        },
        {
          "from": "/images/pagamenti/causali-reali/8.placeholder.svg",
          "checkpoint": "category-deleted-after-reload"
        }
      ],
      "insert_images": []
    },
    "sources": [
      "BE/application/serializers/balance_sheet.py"
    ]
  },
  {
    "workflow_id": "payment-categories-manage",
    "section": {
      "path": "docs/contabilita-avanzata.mdx",
      "id": "tipologie-di-causali",
      "title": "Tipologie di causali",
      "checkpoints": [
        "category-fields-before-save",
        "category-edit-before-save",
        "category-edit-after-reload"
      ],
      "images": [
        {
          "from": "/images/pagamenti/causali-reali/2.placeholder.svg",
          "checkpoint": "category-fields-before-save"
        },
        {
          "from": "/images/pagamenti/causali-reali/4.placeholder.svg",
          "checkpoint": "category-edit-before-save"
        },
        {
          "from": "/images/pagamenti/causali-reali/5.placeholder.svg",
          "checkpoint": "category-edit-after-reload"
        }
      ],
      "insert_images": []
    },
    "sources": [
      "BE/application/serializers/balance_sheet.py"
    ]
  },
  {
    "workflow_id": "payment-categories-manage",
    "section": {
      "path": "docs/contabilita-avanzata.mdx",
      "id": "creare-una-causale",
      "title": "Creare una causale",
      "checkpoints": [
        "category-fields-before-save",
        "category-created-after-reload"
      ],
      "images": [
        {
          "from": "/images/pagamenti/causali-reali/2.placeholder.svg",
          "checkpoint": "category-fields-before-save"
        },
        {
          "from": "/images/pagamenti/causali-reali/3.placeholder.svg",
          "checkpoint": "category-created-after-reload"
        }
      ],
      "insert_images": []
    },
    "sources": [
      "BE/application/serializers/balance_sheet.py"
    ]
  },
  {
    "workflow_id": "payment-categories-manage",
    "section": {
      "path": "docs/contabilita-avanzata.mdx",
      "id": "modificare-una-causale",
      "title": "Modificare una causale",
      "checkpoints": [
        "category-edit-before-save",
        "category-edit-after-reload"
      ],
      "images": [
        {
          "from": "/images/pagamenti/causali-reali/4.placeholder.svg",
          "checkpoint": "category-edit-before-save"
        },
        {
          "from": "/images/pagamenti/causali-reali/5.placeholder.svg",
          "checkpoint": "category-edit-after-reload"
        }
      ],
      "insert_images": []
    },
    "sources": [
      "BE/application/serializers/balance_sheet.py"
    ]
  }
];
for (const item of exhaustiveGuideBindings) {
    const spec = paymentMaintenanceAuthoredWorkflows[item.workflow_id];
    const section = item.section;
    if (!spec || spec.sections.some(old => old.path === section.path && old.id === section.id))
        throw new Error('Duplicate or unknown additional procedure: ' + item.workflow_id + ':' + section.id);
    spec.sections.push(section);
    spec.pages = [...new Set([...spec.pages, section.path])];
    spec.sources = [...new Set([...spec.sources, ...(item.sources || [])])];
    spec.dependencies = [...new Set([...spec.dependencies, ...(item.sources || [])])];
}

// Conservative full-file contexts of the reused legacy chapter contracts.
const additionalReviewedCaptureContexts = [
  {
    "id": "payment-categories-manage",
    "sources": [
      "BE/application/impersonation.py",
      "BE/application/models/balance_sheet_models.py",
      "BE/application/models/invoices_models.py",
      "BE/application/serializers/invoice_serializers.py",
      "BE/application/utils/api_utils.py",
      "BE/application/utils/balance_sheet_utils.py",
      "BE/application/views/balance_sheet_views.py",
      "BE/application/views/invoice_views.py",
      "BE/application/views/supplier_views.py",
      "UI/src/components/invoice/InvoiceForm.svelte",
      "UI/src/routes/accounting/accounts/Accounts.svelte",
      "UI/src/routes/accounting/accounts/modals/EditModal.svelte",
      "UI/src/routes/accounting/accountsTransfer/AccountsTransfer.svelte",
      "UI/src/routes/accounting/balanceSheet/BalanceSheetEditor.svelte",
      "UI/src/routes/accounting/balanceSheet/balanceInputs/EditableSection.svelte",
      "UI/src/routes/accounting/balanceSheet/balanceInputs/NumericInput.svelte",
      "UI/src/routes/accounting/customersInvoice/CustomersInvoiceList.svelte",
      "UI/src/routes/accounting/customersInvoice/modals/EditModal.svelte",
      "UI/src/routes/accounting/payment/modals/partials/meta-payment-categories.svelte",
      "UI/src/routes/accounting/suppliers-and-customers/detail/sections/Info.svelte",
      "UI/src/routes/accounting/suppliers-and-customers/partials/supplier-customer-form.svelte",
      "UI/src/routes/accounting/suppliersInvoice/SuppliersInvoiceList.svelte",
      "UI/src/routes/accounting/suppliersInvoice/modals/EditModal.svelte",
      "UI/src/routes/profile/sections/Settings.svelte"
    ],
    "script": "selfhost/tests/browser/manuale/payment-categories.mjs"
  }
];
for (const item of additionalReviewedCaptureContexts) {
    const spec = paymentMaintenanceAuthoredWorkflows[item.id];
    spec.sources = [...new Set([...spec.sources, ...item.sources])];
    spec.dependencies = [...new Set([...spec.dependencies, ...item.sources])];
}
