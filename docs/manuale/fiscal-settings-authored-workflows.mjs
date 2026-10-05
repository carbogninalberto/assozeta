// Real workflow preparation only; publishing requires the passed seeded browser report.
export const fiscalSettingsAuthoredWorkflows = Object.freeze({
  "fiscal-year-settings": {
    "version": 1,
    "script": "selfhost/tests/browser/manuale/fiscal-year-settings.mjs",
    "prefix": "images/impostazioni/anno-fiscale/",
    "pages": [
      "docs/bilancio.mdx",
      "faq/come-generare-bilancio.mdx"
    ],
    "sources": [
      "UI/src/components/Sidebar.svelte",
      "UI/src/routes.js",
      "UI/src/utils/Permissions.js",
      "UI/src/routes/accounting/accounts/Accounts.svelte",
      "UI/src/routes/accounting/accounts/modals/EditModal.svelte",
      "UI/src/routes/accounting/accountsTransfer/AccountsTransfer.svelte",
      "UI/src/routes/accounting/balanceSheet/BalanceSheetEditor.svelte",
      "UI/src/routes/accounting/balanceSheet/balanceInputs/EditableSection.svelte",
      "UI/src/routes/accounting/balanceSheet/balanceInputs/NumericInput.svelte",
      "UI/src/routes/profile/sections/Settings.svelte",
      "BE/application/views/balance_sheet_views.py",
      "BE/application/serializers/balance_sheet.py",
      "BE/application/models/balance_sheet_models.py",
      "BE/application/utils/balance_sheet_utils.py",
      "BE/application/utils/api_utils.py",
      "BE/application/permissions_registry.py",
      "BE/application/impersonation.py",
      "BE/application/models/payment_models.py",
      "UI/src/routes/accounting/payment/category/Categories.svelte",
      "UI/src/routes/accounting/payment/category/modals/EditModal.svelte",
      "UI/src/routes/accounting/payment/modals/partials/meta-payment-categories.svelte",
      "BE/application/views/payment_views.py",
      "BE/application/serializers/payment_serializers.py",
      "BE/application/views/supplier_views.py",
      "UI/src/routes/accounting/suppliers-and-customers/partials/supplier-customer-form.svelte",
      "UI/src/routes/accounting/suppliers-and-customers/detail/sections/Info.svelte",
      "UI/src/routes/accounting/customersInvoice/CustomersInvoiceList.svelte",
      "UI/src/routes/accounting/customersInvoice/modals/EditModal.svelte",
      "UI/src/components/invoice/InvoiceForm.svelte",
      "UI/src/routes/accounting/suppliersInvoice/SuppliersInvoiceList.svelte",
      "UI/src/routes/accounting/suppliersInvoice/modals/EditModal.svelte",
      "BE/application/views/invoice_views.py",
      "BE/application/serializers/invoice_serializers.py",
      "BE/application/models/invoices_models.py",
      "BE/application/views/profile_views.py",
      "BE/application/serializers/auth_serializers.py",
      "BE/application/models/user_models.py",
      "BE/application/management/commands/seed_manuale.py"
    ],
    "dependencies": [
      "UI/src/components/Sidebar.svelte",
      "UI/src/routes.js",
      "UI/src/utils/Permissions.js",
      "UI/src/routes/accounting/accounts/Accounts.svelte",
      "UI/src/routes/accounting/accounts/modals/EditModal.svelte",
      "UI/src/routes/accounting/accountsTransfer/AccountsTransfer.svelte",
      "UI/src/routes/accounting/balanceSheet/BalanceSheetEditor.svelte",
      "UI/src/routes/accounting/balanceSheet/balanceInputs/EditableSection.svelte",
      "UI/src/routes/accounting/balanceSheet/balanceInputs/NumericInput.svelte",
      "UI/src/routes/profile/sections/Settings.svelte",
      "BE/application/views/balance_sheet_views.py",
      "BE/application/serializers/balance_sheet.py",
      "BE/application/models/balance_sheet_models.py",
      "BE/application/utils/balance_sheet_utils.py",
      "BE/application/utils/api_utils.py",
      "BE/application/permissions_registry.py",
      "BE/application/impersonation.py",
      "BE/application/models/payment_models.py",
      "UI/src/routes/accounting/payment/category/Categories.svelte",
      "UI/src/routes/accounting/payment/category/modals/EditModal.svelte",
      "UI/src/routes/accounting/payment/modals/partials/meta-payment-categories.svelte",
      "BE/application/views/payment_views.py",
      "BE/application/serializers/payment_serializers.py",
      "BE/application/views/supplier_views.py",
      "UI/src/routes/accounting/suppliers-and-customers/partials/supplier-customer-form.svelte",
      "UI/src/routes/accounting/suppliers-and-customers/detail/sections/Info.svelte",
      "UI/src/routes/accounting/customersInvoice/CustomersInvoiceList.svelte",
      "UI/src/routes/accounting/customersInvoice/modals/EditModal.svelte",
      "UI/src/components/invoice/InvoiceForm.svelte",
      "UI/src/routes/accounting/suppliersInvoice/SuppliersInvoiceList.svelte",
      "UI/src/routes/accounting/suppliersInvoice/modals/EditModal.svelte",
      "BE/application/views/invoice_views.py",
      "BE/application/serializers/invoice_serializers.py",
      "BE/application/models/invoices_models.py",
      "BE/application/views/profile_views.py",
      "BE/application/serializers/auth_serializers.py",
      "BE/application/models/user_models.py",
      "BE/application/management/commands/seed_manuale.py",
      "selfhost/tests/browser/manuale/fiscal-year-settings.mjs",
      "docs/manuale/fiscal-settings-authored-workflows.mjs"
    ],
    "checkpoints": [
      {
        "id": "fiscal-settings-original-and-options",
        "caption": "Preferenze originali e quattro tipi di anno fiscale."
      },
      {
        "id": "september-fiscal-settings-before-save",
        "caption": "Anno sportivo da settembre: preferenze prima del salvataggio."
      },
      {
        "id": "september-balance-period-after-reload",
        "caption": "Anno sportivo da settembre: periodo del rendiconto dopo il ricaricamento."
      },
      {
        "id": "june-fiscal-settings-before-save",
        "caption": "Anno sportivo da giugno: preferenze prima del salvataggio."
      },
      {
        "id": "june-balance-period-after-reload",
        "caption": "Anno sportivo da giugno: periodo del rendiconto dopo il ricaricamento."
      },
      {
        "id": "solar-fiscal-settings-before-save",
        "caption": "Anno solare: preferenze prima del salvataggio."
      },
      {
        "id": "solar-balance-period-after-reload",
        "caption": "Anno solare: periodo del rendiconto dopo il ricaricamento."
      },
      {
        "id": "custom-fiscal-settings-before-save",
        "caption": "Periodo personalizzato dal 15 luglio: preferenze prima del salvataggio."
      },
      {
        "id": "custom-balance-period-after-reload",
        "caption": "Periodo personalizzato dal 15 luglio: periodo del rendiconto dopo il ricaricamento."
      }
    ],
    "sections": [
      {
        "path": "docs/bilancio.mdx",
        "id": "anno-fiscale",
        "title": "Anno fiscale",
        "checkpoints": [
          "fiscal-settings-original-and-options",
          "solar-fiscal-settings-before-save",
          "custom-fiscal-settings-before-save",
          "custom-balance-period-after-reload"
        ],
        "images": [
          {
            "from": "/images/impostazioni/anno-fiscale/1.placeholder.svg",
            "checkpoint": "fiscal-settings-original-and-options"
          },
          {
            "from": "/images/impostazioni/anno-fiscale/6.placeholder.svg",
            "checkpoint": "solar-fiscal-settings-before-save"
          },
          {
            "from": "/images/impostazioni/anno-fiscale/8.placeholder.svg",
            "checkpoint": "custom-fiscal-settings-before-save"
          },
          {
            "from": "/images/impostazioni/anno-fiscale/9.placeholder.svg",
            "checkpoint": "custom-balance-period-after-reload"
          }
        ],
        "insert_images": []
      },
      {
        "path": "faq/come-generare-bilancio.mdx",
        "id": "allineamento-dell-anno-fiscale",
        "title": "Allineamento dell'anno fiscale",
        "checkpoints": [
          "fiscal-settings-original-and-options",
          "solar-fiscal-settings-before-save",
          "custom-fiscal-settings-before-save",
          "custom-balance-period-after-reload"
        ],
        "images": [
          {
            "from": "/images/impostazioni/anno-fiscale/1.placeholder.svg",
            "checkpoint": "fiscal-settings-original-and-options"
          },
          {
            "from": "/images/impostazioni/anno-fiscale/6.placeholder.svg",
            "checkpoint": "solar-fiscal-settings-before-save"
          },
          {
            "from": "/images/impostazioni/anno-fiscale/8.placeholder.svg",
            "checkpoint": "custom-fiscal-settings-before-save"
          },
          {
            "from": "/images/impostazioni/anno-fiscale/9.placeholder.svg",
            "checkpoint": "custom-balance-period-after-reload"
          }
        ],
        "insert_images": []
      }
    ],
    "outcome": {
      "field": "fiscal_year_settings_authored_workflow",
      "expected": {
        "solar_period_saved_and_reopened": true,
        "solar_other_settings_preserved": true,
        "solar_backend_period_matches_settings": true,
        "september_period_saved_and_reopened": true,
        "september_other_settings_preserved": true,
        "september_backend_period_matches_settings": true,
        "june_period_saved_and_reopened": true,
        "june_other_settings_preserved": true,
        "june_backend_period_matches_settings": true,
        "custom_period_saved_and_reopened": true,
        "custom_other_settings_preserved": true,
        "custom_backend_period_matches_settings": true,
        "reader_fiscal_write_denied": true,
        "reader_denial_preserved_settings": true,
        "members_and_payments_preserved": true,
        "original_settings_restored": true
      }
    }
  }
});
