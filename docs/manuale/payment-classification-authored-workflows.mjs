// Local classification and amount arithmetic only. Fiscal eligibility and document output require separate evidence.
export const paymentClassificationAuthoredWorkflows = Object.freeze({
  "payment-classification-local": {
    "version": 1,
    "script": "selfhost/tests/browser/manuale/payment-classification.mjs",
    "prefix": "images/pagamenti/ripartizione-causali/",
    "pages": [
      "docs/pagamenti.mdx",
      "docs/contabilita-avanzata.mdx"
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
      "BE/application/urls.py",
      "BE/application/serializers/balance_sheet.py",
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
      "UI/src/routes/profile/sections/Settings.svelte",
      "UI/src/routes/accounting/payment/PaymentDrawer.svelte",
      "UI/src/routes/accounting/payment/partials/payment-overview.svelte",
      "UI/src/components/buttons/ApproveButton.svelte",
      "BE/application/models/user_models.py",
      "BE/application/services/invoice_service.py",
      "UI/src/routes/accounting/receipts/ReceiptList.svelte",
      "UI/src/routes/accounting/receipts/invoiceActionState.js",
      "UI/src/components/modals/InvoicePreviewModal.svelte",
      "UI/src/routes/accounting/receipts/modals/ShareModal.svelte",
      "BE/application/printing_tasks.py",
      "BE/docmanager/views/printing_views.py",
      "BE/docmanager/views/document_view.py",
      "BE/application/utils/printing.py",
      "BE/docmanager/tasks.py",
      "BE/templates/document/application/invoice.html",
      "UI/src/components/filters/FilterSelect.svelte",
      "UI/src/components/dropdowns/basic-dropdown.svelte",
      "UI/src/components/inputs/DateRangePicker.svelte",
      "UI/src/components/inputs/DateRangeCalendar.svelte",
      "UI/src/utils/ApiMiddleware.js",
      "UI/src/store/stores.js",
      "UI/src/routes/association/course/overview/OverviewCourse.svelte",
      "UI/src/routes/association/course/overview/partials/course-subscriptions-list.svelte",
      "UI/src/routes/association/course/overview/modals/AddEditCourseSubscriptionModal.svelte",
      "BE/application/views/course_views.py",
      "BE/application/serializers/courses_serializers.py",
      "BE/application/models/courses_models.py",
      "BE/application/signals.py",
      "BE/application/views/profile_views.py",
      "BE/application/serializers/auth_serializers.py",
      "BE/application/utils/excel_utils.py",
      "BE/application/management/commands/seed_manuale.py",
      "UI/src/utils/Functions.js",
      "UI/src/components/formBuilder/preview-blocks/currency-input.svelte"
    ],
    "dependencies": [
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
      "BE/application/urls.py",
      "BE/application/serializers/balance_sheet.py",
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
      "UI/src/routes/profile/sections/Settings.svelte",
      "UI/src/routes/accounting/payment/PaymentDrawer.svelte",
      "UI/src/routes/accounting/payment/partials/payment-overview.svelte",
      "UI/src/components/buttons/ApproveButton.svelte",
      "BE/application/models/user_models.py",
      "BE/application/services/invoice_service.py",
      "UI/src/routes/accounting/receipts/ReceiptList.svelte",
      "UI/src/routes/accounting/receipts/invoiceActionState.js",
      "UI/src/components/modals/InvoicePreviewModal.svelte",
      "UI/src/routes/accounting/receipts/modals/ShareModal.svelte",
      "BE/application/printing_tasks.py",
      "BE/docmanager/views/printing_views.py",
      "BE/docmanager/views/document_view.py",
      "BE/application/utils/printing.py",
      "BE/docmanager/tasks.py",
      "BE/templates/document/application/invoice.html",
      "UI/src/components/filters/FilterSelect.svelte",
      "UI/src/components/dropdowns/basic-dropdown.svelte",
      "UI/src/components/inputs/DateRangePicker.svelte",
      "UI/src/components/inputs/DateRangeCalendar.svelte",
      "UI/src/utils/ApiMiddleware.js",
      "UI/src/store/stores.js",
      "UI/src/routes/association/course/overview/OverviewCourse.svelte",
      "UI/src/routes/association/course/overview/partials/course-subscriptions-list.svelte",
      "UI/src/routes/association/course/overview/modals/AddEditCourseSubscriptionModal.svelte",
      "BE/application/views/course_views.py",
      "BE/application/serializers/courses_serializers.py",
      "BE/application/models/courses_models.py",
      "BE/application/signals.py",
      "BE/application/views/profile_views.py",
      "BE/application/serializers/auth_serializers.py",
      "BE/application/utils/excel_utils.py",
      "BE/application/management/commands/seed_manuale.py",
      "UI/src/utils/Functions.js",
      "UI/src/components/formBuilder/preview-blocks/currency-input.svelte",
      "docs/manuale/payment-classification-authored-workflows.mjs",
      "selfhost/tests/browser/manuale/payment-classification.mjs",
      "selfhost/tests/browser/manuale/scenario.mjs",
      "selfhost/tests/browser/manuale/frame.mjs",
      "selfhost/tests/browser/manuale/redaction.mjs",
      "BE/application/management/commands/seed_manuale.py"
    ],
    "checkpoints": [
      {
        "id": "classification-categories-baseline",
        "caption": "Causali personali e comuni prima della classificazione dimostrativa."
      },
      {
        "id": "tax-flag-enabled-before-save",
        "caption": "Entrata istituzionale con Detraibile fiscalmente selezionato prima del salvataggio."
      },
      {
        "id": "tax-category-persisted",
        "caption": "Causale personale salvata e conservata dopo la ricarica."
      },
      {
        "id": "tax-flag-preserved-during-rename",
        "caption": "Nome modificato con il contrassegno fiscale ancora selezionato."
      },
      {
        "id": "tax-flag-checked-after-reopen",
        "caption": "Contrassegno fiscale conservato dopo la modifica del solo nome e la riapertura."
      },
      {
        "id": "tax-flag-disabled-before-save",
        "caption": "Disattivazione del contrassegno prima di salvare."
      },
      {
        "id": "tax-flag-disabled-after-reopen",
        "caption": "Contrassegno disattivato anche dopo il ricaricamento."
      },
      {
        "id": "split-base-and-two-extras-before-save",
        "caption": "Importo principale 35 euro e due causali aggiuntive da 10 e 5 euro."
      },
      {
        "id": "split-total-fifty-after-reload",
        "caption": "Pagamento unico non incassato da 50 euro dopo il salvataggio."
      },
      {
        "id": "split-reopened-base-thirty-five",
        "caption": "Riapertura del pagamento: importo principale 35 euro, aggiuntive 10 e 5 euro."
      },
      {
        "id": "split-second-extra-removed-before-save",
        "caption": "Seconda causale rimossa nel modulo: rimangono 35 euro principali e 10 euro aggiuntivi."
      },
      {
        "id": "split-total-forty-five-after-reload",
        "caption": "Totale unico ridotto a 45 euro dopo il salvataggio e la ricarica."
      },
      {
        "id": "split-cancel-preserves-saved-payment",
        "caption": "Modifica annullata: il pagamento conserva il totale salvato da 45 euro."
      },
      {
        "id": "split-all-extras-removed-before-save",
        "caption": "Ultima causale aggiuntiva rimossa: rimane l’importo principale da 35 euro."
      },
      {
        "id": "split-single-base-after-reload",
        "caption": "Pagamento unico da 35 euro senza causali aggiuntive, conservato dopo la ricarica."
      },
      {
        "id": "classification-reader-readonly",
        "caption": "Collaboratore in lettura: causale visibile e comando di modifica disabilitato."
      }
    ],
    "sections": [
      {
        "path": "docs/contabilita-avanzata.mdx",
        "id": "causali-aggiuntive-nei-pagamenti",
        "title": "Causali aggiuntive nei pagamenti",
        "checkpoints": [
          "split-base-and-two-extras-before-save",
          "split-total-fifty-after-reload",
          "split-reopened-base-thirty-five",
          "split-second-extra-removed-before-save",
          "split-total-forty-five-after-reload",
          "split-cancel-preserves-saved-payment",
          "split-all-extras-removed-before-save",
          "split-single-base-after-reload",
          "classification-reader-readonly"
        ],
        "images": [
          {
            "from": "/images/pagamenti/ripartizione-causali/8.placeholder.svg",
            "checkpoint": "split-base-and-two-extras-before-save"
          },
          {
            "from": "/images/pagamenti/ripartizione-causali/9.placeholder.svg",
            "checkpoint": "split-total-fifty-after-reload"
          },
          {
            "from": "/images/pagamenti/ripartizione-causali/10.placeholder.svg",
            "checkpoint": "split-reopened-base-thirty-five"
          },
          {
            "from": "/images/pagamenti/ripartizione-causali/12.placeholder.svg",
            "checkpoint": "split-total-forty-five-after-reload"
          },
          {
            "from": "/images/pagamenti/ripartizione-causali/15.placeholder.svg",
            "checkpoint": "split-single-base-after-reload"
          }
        ],
        "insert_images": []
      },
      {
        "path": "docs/contabilita-avanzata.mdx",
        "id": "causali-detraibili-730",
        "title": "Causali detraibili 730",
        "checkpoints": [
          "classification-categories-baseline",
          "tax-flag-enabled-before-save",
          "tax-category-persisted",
          "tax-flag-preserved-during-rename",
          "tax-flag-checked-after-reopen",
          "tax-flag-disabled-before-save",
          "tax-flag-disabled-after-reopen",
          "classification-reader-readonly"
        ],
        "images": [
          {
            "from": "/images/pagamenti/ripartizione-causali/2.placeholder.svg",
            "checkpoint": "tax-flag-enabled-before-save"
          },
          {
            "from": "/images/pagamenti/ripartizione-causali/5.placeholder.svg",
            "checkpoint": "tax-flag-checked-after-reopen"
          },
          {
            "from": "/images/pagamenti/ripartizione-causali/7.placeholder.svg",
            "checkpoint": "tax-flag-disabled-after-reopen"
          },
          {
            "from": "/images/pagamenti/ripartizione-causali/16.placeholder.svg",
            "checkpoint": "classification-reader-readonly"
          }
        ],
        "insert_images": []
      },
      {
        "path": "docs/pagamenti.mdx",
        "id": "causali-detraibili-730",
        "title": "Causali detraibili 730",
        "checkpoints": [
          "classification-categories-baseline",
          "tax-flag-enabled-before-save",
          "tax-category-persisted",
          "tax-flag-preserved-during-rename",
          "tax-flag-checked-after-reopen",
          "tax-flag-disabled-before-save",
          "tax-flag-disabled-after-reopen",
          "classification-reader-readonly"
        ],
        "images": [
          {
            "from": "/images/pagamenti/ripartizione-causali/2.placeholder.svg",
            "checkpoint": "tax-flag-enabled-before-save"
          },
          {
            "from": "/images/pagamenti/ripartizione-causali/5.placeholder.svg",
            "checkpoint": "tax-flag-checked-after-reopen"
          },
          {
            "from": "/images/pagamenti/ripartizione-causali/7.placeholder.svg",
            "checkpoint": "tax-flag-disabled-after-reopen"
          },
          {
            "from": "/images/pagamenti/ripartizione-causali/16.placeholder.svg",
            "checkpoint": "classification-reader-readonly"
          }
        ],
        "insert_images": []
      }
    ],
    "outcome": {
      "field": "payment_classification_authored_workflow",
      "expected": {
        "tax_enabled_after_create": true,
        "tax_checked_after_reopen": true,
        "unrelated_rename_preserves_flag": true,
        "tax_disabled_after_reopen": true,
        "total_with_two_extras": 50,
        "base_after_reopen": 35,
        "total_after_one_removed": 45,
        "cancelled_edit_preserves_payment": true,
        "total_after_all_removed": 35,
        "reader_write_denials": 3,
        "no_payment_collected": true,
        "no_receipt_generated": true,
        "baseline_payments_preserved": true,
        "baseline_categories_preserved": true,
        "owned_payment_removed": true,
        "owned_category_soft_deleted": true
      }
    }
  }
});
