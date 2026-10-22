// Canonical workflow metadata; prose belongs to the authored MDX.
export const accountingCalendarAuthoredWorkflows = Object.freeze({
  "accounting-balance-manage": {
    "version": 1,
    "script": "selfhost/tests/browser/manuale/accounting-balance.mjs",
    "prefix": "images/contabilita/bilancio/",
    "pages": [
      "docs/contabilita-avanzata.mdx",
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
      "BE/application/models/invoices_models.py"
    ],
    "dependencies": [
      "selfhost/tests/browser/manuale/scenario.mjs",
      "selfhost/tests/browser/manuale/frame.mjs",
      "selfhost/tests/browser/manuale/redaction.mjs",
      "selfhost/tests/browser/playwright.manual.config.mjs",
      "BE/application/management/commands/seed_manuale.py",
      "BE/application/management/commands/run_manuale_instance.py",
      "docs/manuale/accounting-calendar-authored-workflows.mjs",
      "selfhost/tests/browser/manuale/accounting-balance.mjs",
      "selfhost/tests/browser/manuale/accounting-balance-sources.mjs",
      "docs/manuale/accounting-balance-recipes.mjs"
    ],
    "checkpoints": [
      {
        "id": "baseline-cash-account-and-current-balance",
        "caption": "Cassa Aurora e i saldi iniziale e attuale"
      },
      {
        "id": "new-bank-account-filled-before-save",
        "caption": "Tipo Banca selezionato nel nuovo conto"
      },
      {
        "id": "bank-account-created-and-persisted-after-reload",
        "caption": "Conto bancario salvato e riletto nell’elenco"
      },
      {
        "id": "account-edit-initial-balance-before-save",
        "caption": "Correzione del saldo iniziale nella finestra di modifica"
      },
      {
        "id": "edited-bank-account-persists-after-reload",
        "caption": "Saldo iniziale corretto del conto bancario"
      },
      {
        "id": "transfer-filled-with-distinct-accounts",
        "caption": "Giroconto con origine e destinazione distinte"
      },
      {
        "id": "transfer-persists-after-reload",
        "caption": "Giroconto conservato dopo il ricaricamento"
      },
      {
        "id": "cash-bank-balances-reflect-internal-transfer",
        "caption": "Saldi dopo lo spostamento di 20 euro"
      },
      {
        "id": "balance-current-year-six-available-years",
        "caption": "Rendiconto, periodo selezionato e scelta dell’anno sociale"
      },
      {
        "id": "manual-balance-row-description-and-two-amounts",
        "caption": "Riga manuale con descrizione e importi prima del salvataggio"
      },
      {
        "id": "saved-manual-row-persists-after-reload",
        "caption": "Riga manuale e importi conservati dopo il ricaricamento"
      },
      {
        "id": "publication-confirmation-is-reversible",
        "caption": "Conferma della pubblicazione con indicazione che è annullabile"
      },
      {
        "id": "published-balance-persists-after-reload",
        "caption": "Rendiconto pubblicato conservato dopo il ricaricamento"
      },
      {
        "id": "unpublish-confirmation",
        "caption": "Conferma per annullare la pubblicazione"
      },
      {
        "id": "draft-and-manual-row-restored-after-unpublish-reload",
        "caption": "Bozza ripristinata e riga manuale conservata"
      },
      {
        "id": "transfer-delete-confirmation",
        "caption": "Conferma per eliminare il giroconto"
      },
      {
        "id": "deleted-transfer-absent-and-balances-restored",
        "caption": "Giroconto eliminato e saldi ripristinati"
      },
      {
        "id": "unused-bank-account-deleted-baseline-restored",
        "caption": "Conto bancario inutilizzato rimosso e cassa iniziale conservata"
      }
    ],
    "sections": [
      {
        "path": "docs/contabilita-avanzata.mdx",
        "id": "conti-finanziari", "intent": "accounting.conti-finanziari",
        "title": "Conti finanziari",
        "checkpoints": [
          "baseline-cash-account-and-current-balance",
          "bank-account-created-and-persisted-after-reload",
          "edited-bank-account-persists-after-reload"
        ],
        "images": [
          {
            "from": "/images/contabilita/bilancio/1.placeholder.svg",
            "checkpoint": "baseline-cash-account-and-current-balance"
          },
          {
            "from": "/images/contabilita/bilancio/3.placeholder.svg",
            "checkpoint": "bank-account-created-and-persisted-after-reload"
          },
          {
            "from": "/images/contabilita/bilancio/5.placeholder.svg",
            "checkpoint": "edited-bank-account-persists-after-reload"
          }
        ],
        "insert_images": []
      },
      {
        "path": "docs/contabilita-avanzata.mdx",
        "id": "tipologie-di-conto", "intent": "accounting.tipologie-di-conto",
        "title": "Tipologie di conto",
        "checkpoints": [
          "new-bank-account-filled-before-save",
          "bank-account-created-and-persisted-after-reload"
        ],
        "images": [
          {
            "from": "/images/contabilita/bilancio/2.placeholder.svg",
            "checkpoint": "new-bank-account-filled-before-save"
          }
        ],
        "insert_images": []
      },
      {
        "path": "docs/contabilita-avanzata.mdx",
        "id": "creare-un-nuovo-conto", "intent": "accounting.creare-un-nuovo-conto",
        "title": "Creare un nuovo conto",
        "checkpoints": [
          "baseline-cash-account-and-current-balance",
          "new-bank-account-filled-before-save",
          "bank-account-created-and-persisted-after-reload"
        ],
        "images": [
          {
            "from": "/images/contabilita/bilancio/1.placeholder.svg",
            "checkpoint": "baseline-cash-account-and-current-balance"
          },
          {
            "from": "/images/contabilita/bilancio/2.placeholder.svg",
            "checkpoint": "new-bank-account-filled-before-save"
          },
          {
            "from": "/images/contabilita/bilancio/3.placeholder.svg",
            "checkpoint": "bank-account-created-and-persisted-after-reload"
          }
        ],
        "insert_images": []
      },
      {
        "path": "docs/contabilita-avanzata.mdx",
        "id": "modificare-un-conto", "intent": "accounting.modificare-un-conto",
        "title": "Modificare un conto",
        "checkpoints": [
          "account-edit-initial-balance-before-save",
          "edited-bank-account-persists-after-reload",
          "unused-bank-account-deleted-baseline-restored"
        ],
        "images": [
          {
            "from": "/images/contabilita/bilancio/4.placeholder.svg",
            "checkpoint": "account-edit-initial-balance-before-save"
          },
          {
            "from": "/images/contabilita/bilancio/5.placeholder.svg",
            "checkpoint": "edited-bank-account-persists-after-reload"
          },
          {
            "from": "/images/contabilita/bilancio/18.placeholder.svg",
            "checkpoint": "unused-bank-account-deleted-baseline-restored"
          }
        ],
        "insert_images": []
      },
      {
        "path": "docs/contabilita-avanzata.mdx",
        "id": "giroconti", "intent": "accounting.giroconti",
        "title": "Giroconti",
        "checkpoints": [
          "transfer-filled-with-distinct-accounts",
          "transfer-persists-after-reload",
          "cash-bank-balances-reflect-internal-transfer"
        ],
        "images": [
          {
            "from": "/images/contabilita/bilancio/6.placeholder.svg",
            "checkpoint": "transfer-filled-with-distinct-accounts"
          },
          {
            "from": "/images/contabilita/bilancio/7.placeholder.svg",
            "checkpoint": "transfer-persists-after-reload"
          },
          {
            "from": "/images/contabilita/bilancio/8.placeholder.svg",
            "checkpoint": "cash-bank-balances-reflect-internal-transfer"
          }
        ],
        "insert_images": []
      },
      {
        "path": "docs/contabilita-avanzata.mdx",
        "id": "creare-un-giroconto", "intent": "accounting.creare-un-giroconto",
        "title": "Creare un giroconto",
        "checkpoints": [
          "transfer-filled-with-distinct-accounts",
          "transfer-persists-after-reload",
          "cash-bank-balances-reflect-internal-transfer"
        ],
        "images": [
          {
            "from": "/images/contabilita/bilancio/6.placeholder.svg",
            "checkpoint": "transfer-filled-with-distinct-accounts"
          },
          {
            "from": "/images/contabilita/bilancio/7.placeholder.svg",
            "checkpoint": "transfer-persists-after-reload"
          },
          {
            "from": "/images/contabilita/bilancio/8.placeholder.svg",
            "checkpoint": "cash-bank-balances-reflect-internal-transfer"
          }
        ],
        "insert_images": []
      },
      {
        "path": "docs/contabilita-avanzata.mdx",
        "id": "eliminare-un-giroconto", "intent": "accounting.eliminare-un-giroconto",
        "title": "Eliminare un giroconto",
        "checkpoints": [
          "transfer-delete-confirmation",
          "deleted-transfer-absent-and-balances-restored"
        ],
        "images": [
          {
            "from": "/images/contabilita/bilancio/16.placeholder.svg",
            "checkpoint": "transfer-delete-confirmation"
          },
          {
            "from": "/images/contabilita/bilancio/17.placeholder.svg",
            "checkpoint": "deleted-transfer-absent-and-balances-restored"
          }
        ],
        "insert_images": []
      },
      {
        "path": "docs/bilancio.mdx",
        "id": "bilancio", "intent": "accounting.bilancio",
        "title": "Bilancio",
        "checkpoints": [
          "balance-current-year-six-available-years",
          "manual-balance-row-description-and-two-amounts",
          "saved-manual-row-persists-after-reload"
        ],
        "images": [
          {
            "from": "/images/contabilita/bilancio/9.placeholder.svg",
            "checkpoint": "balance-current-year-six-available-years"
          },
          {
            "from": "/images/contabilita/bilancio/10.placeholder.svg",
            "checkpoint": "manual-balance-row-description-and-two-amounts"
          },
          {
            "from": "/images/contabilita/bilancio/11.placeholder.svg",
            "checkpoint": "saved-manual-row-persists-after-reload"
          }
        ],
        "insert_images": []
      },
      {
        "path": "docs/bilancio.mdx",
        "id": "conti-economici", "intent": "accounting.conti-economici",
        "title": "Conti economici",
        "checkpoints": [
          "baseline-cash-account-and-current-balance",
          "new-bank-account-filled-before-save",
          "bank-account-created-and-persisted-after-reload"
        ],
        "images": [
          {
            "from": "/images/contabilita/bilancio/1.placeholder.svg",
            "checkpoint": "baseline-cash-account-and-current-balance"
          },
          {
            "from": "/images/contabilita/bilancio/2.placeholder.svg",
            "checkpoint": "new-bank-account-filled-before-save"
          },
          {
            "from": "/images/contabilita/bilancio/3.placeholder.svg",
            "checkpoint": "bank-account-created-and-persisted-after-reload"
          }
        ],
        "insert_images": []
      },
      {
        "path": "docs/bilancio.mdx",
        "id": "giroconti", "intent": "accounting.giroconti",
        "title": "Giroconti",
        "checkpoints": [
          "transfer-filled-with-distinct-accounts",
          "transfer-persists-after-reload",
          "cash-bank-balances-reflect-internal-transfer",
          "transfer-delete-confirmation",
          "deleted-transfer-absent-and-balances-restored"
        ],
        "images": [
          {
            "from": "/images/contabilita/bilancio/6.placeholder.svg",
            "checkpoint": "transfer-filled-with-distinct-accounts"
          },
          {
            "from": "/images/contabilita/bilancio/7.placeholder.svg",
            "checkpoint": "transfer-persists-after-reload"
          },
          {
            "from": "/images/contabilita/bilancio/8.placeholder.svg",
            "checkpoint": "cash-bank-balances-reflect-internal-transfer"
          },
          {
            "from": "/images/contabilita/bilancio/16.placeholder.svg",
            "checkpoint": "transfer-delete-confirmation"
          },
          {
            "from": "/images/contabilita/bilancio/17.placeholder.svg",
            "checkpoint": "deleted-transfer-absent-and-balances-restored"
          }
        ],
        "insert_images": []
      },
      {
        "path": "docs/bilancio.mdx",
        "id": "pubblicazione-del-bilancio", "intent": "accounting.balance.publish",
        "title": "Pubblicazione del bilancio",
        "checkpoints": [
          "saved-manual-row-persists-after-reload",
          "publication-confirmation-is-reversible",
          "published-balance-persists-after-reload",
          "unpublish-confirmation",
          "draft-and-manual-row-restored-after-unpublish-reload"
        ],
        "images": [
          {
            "from": "/images/contabilita/bilancio/11.placeholder.svg",
            "checkpoint": "saved-manual-row-persists-after-reload"
          },
          {
            "from": "/images/contabilita/bilancio/12.placeholder.svg",
            "checkpoint": "publication-confirmation-is-reversible"
          },
          {
            "from": "/images/contabilita/bilancio/13.placeholder.svg",
            "checkpoint": "published-balance-persists-after-reload"
          },
          {
            "from": "/images/contabilita/bilancio/14.placeholder.svg",
            "checkpoint": "unpublish-confirmation"
          },
          {
            "from": "/images/contabilita/bilancio/15.placeholder.svg",
            "checkpoint": "draft-and-manual-row-restored-after-unpublish-reload"
          }
        ],
        "insert_images": []
      },
      {
        "path": "faq/come-generare-bilancio.mdx",
        "id": "cos-e-il-bilancio", "intent": "accounting.cos-e-il-bilancio",
        "title": "Cos'è il bilancio",
        "checkpoints": [
          "saved-manual-row-persists-after-reload"
        ],
        "images": [
          {
            "from": "/images/contabilita/bilancio/11.placeholder.svg",
            "checkpoint": "saved-manual-row-persists-after-reload"
          }
        ],
        "insert_images": []
      },
      {
        "path": "faq/come-generare-bilancio.mdx",
        "id": "configurare-i-conti-economici", "intent": "accounting.configurare-i-conti-economici",
        "title": "Configurare i conti economici",
        "checkpoints": [
          "baseline-cash-account-and-current-balance",
          "new-bank-account-filled-before-save",
          "bank-account-created-and-persisted-after-reload",
          "account-edit-initial-balance-before-save",
          "edited-bank-account-persists-after-reload"
        ],
        "images": [
          {
            "from": "/images/contabilita/bilancio/1.placeholder.svg",
            "checkpoint": "baseline-cash-account-and-current-balance"
          },
          {
            "from": "/images/contabilita/bilancio/2.placeholder.svg",
            "checkpoint": "new-bank-account-filled-before-save"
          },
          {
            "from": "/images/contabilita/bilancio/3.placeholder.svg",
            "checkpoint": "bank-account-created-and-persisted-after-reload"
          },
          {
            "from": "/images/contabilita/bilancio/4.placeholder.svg",
            "checkpoint": "account-edit-initial-balance-before-save"
          },
          {
            "from": "/images/contabilita/bilancio/5.placeholder.svg",
            "checkpoint": "edited-bank-account-persists-after-reload"
          }
        ],
        "insert_images": []
      },
      {
        "path": "faq/come-generare-bilancio.mdx",
        "id": "i-giroconti-trasferimenti-tra-conti", "intent": "accounting.i-giroconti-trasferimenti-tra-conti",
        "title": "I giroconti (trasferimenti tra conti)",
        "checkpoints": [
          "transfer-filled-with-distinct-accounts",
          "transfer-persists-after-reload",
          "cash-bank-balances-reflect-internal-transfer",
          "transfer-delete-confirmation",
          "deleted-transfer-absent-and-balances-restored"
        ],
        "images": [
          {
            "from": "/images/contabilita/bilancio/6.placeholder.svg",
            "checkpoint": "transfer-filled-with-distinct-accounts"
          },
          {
            "from": "/images/contabilita/bilancio/7.placeholder.svg",
            "checkpoint": "transfer-persists-after-reload"
          },
          {
            "from": "/images/contabilita/bilancio/8.placeholder.svg",
            "checkpoint": "cash-bank-balances-reflect-internal-transfer"
          },
          {
            "from": "/images/contabilita/bilancio/16.placeholder.svg",
            "checkpoint": "transfer-delete-confirmation"
          },
          {
            "from": "/images/contabilita/bilancio/17.placeholder.svg",
            "checkpoint": "deleted-transfer-absent-and-balances-restored"
          }
        ],
        "insert_images": []
      },
      {
        "path": "faq/come-generare-bilancio.mdx",
        "id": "generare-e-consultare-il-bilancio", "intent": "accounting.generare-e-consultare-il-bilancio",
        "title": "Generare e consultare il bilancio",
        "checkpoints": [
          "balance-current-year-six-available-years",
          "manual-balance-row-description-and-two-amounts",
          "saved-manual-row-persists-after-reload"
        ],
        "images": [
          {
            "from": "/images/contabilita/bilancio/9.placeholder.svg",
            "checkpoint": "balance-current-year-six-available-years"
          },
          {
            "from": "/images/contabilita/bilancio/10.placeholder.svg",
            "checkpoint": "manual-balance-row-description-and-two-amounts"
          },
          {
            "from": "/images/contabilita/bilancio/11.placeholder.svg",
            "checkpoint": "saved-manual-row-persists-after-reload"
          }
        ],
        "insert_images": []
      },
      {
        "path": "faq/come-generare-bilancio.mdx",
        "id": "pubblicare-il-bilancio", "intent": "accounting.balance.publish",
        "title": "Pubblicare il bilancio",
        "checkpoints": [
          "saved-manual-row-persists-after-reload",
          "publication-confirmation-is-reversible",
          "published-balance-persists-after-reload",
          "unpublish-confirmation",
          "draft-and-manual-row-restored-after-unpublish-reload"
        ],
        "images": [
          {
            "from": "/images/contabilita/bilancio/11.placeholder.svg",
            "checkpoint": "saved-manual-row-persists-after-reload"
          },
          {
            "from": "/images/contabilita/bilancio/12.placeholder.svg",
            "checkpoint": "publication-confirmation-is-reversible"
          },
          {
            "from": "/images/contabilita/bilancio/13.placeholder.svg",
            "checkpoint": "published-balance-persists-after-reload"
          },
          {
            "from": "/images/contabilita/bilancio/14.placeholder.svg",
            "checkpoint": "unpublish-confirmation"
          },
          {
            "from": "/images/contabilita/bilancio/15.placeholder.svg",
            "checkpoint": "draft-and-manual-row-restored-after-unpublish-reload"
          }
        ],
        "insert_images": []
      }
    ],
    "outcome": {
      "field": "accounting_balance_manage",
      "expected": {
        "baseline_cash": 50,
        "bank_created_initial_balance": 100,
        "bank_saved_initial_balance": 125,
        "transfer_amount": 20,
        "cash_after_transfer": 30,
        "bank_after_transfer": 145,
        "liquid_total_after_transfer": 175,
        "automatic_income": 50,
        "available_year_count": 6,
        "manual_row_description": "Contributo dimostrativo direttivo",
        "manual_institutional": 15,
        "manual_commercial": 5,
        "resulting_income": 70,
        "manual_row_persisted_after_reload": true,
        "published_persisted_after_reload": true,
        "unpublish_persisted_after_reload": true,
        "automatic_payments_unchanged": true,
        "transfer_deleted_after_reload": true,
        "bank_deleted_after_reload": true,
        "baseline_cash_restored": 50,
        "reader_read_status": 403,
        "reader_write_status": 403,
        "export_exercised": false,
        "invoices_exercised": false,
        "suppliers_exercised": false,
        "categories_exercised": false,
        "fiscal_change_exercised": false,
        "associated_member_visibility_exercised": false,
        "account_type_options_visible": true,
        "cancelled_transfer_delete_preserves_balances": true
      }
    }
  },
  "camps-calendar-manage": {
    "version": 1,
    "script": "selfhost/tests/browser/manuale/camps-calendar.mjs",
    "prefix": "images/camp-calendario/gestione/",
    "pages": [
      "docs/camp-e-ritiri.mdx",
      "docs/calendario.mdx",
      "faq/come-collegare-google-calendar.mdx"
    ],
    "sources": [
      "UI/src/components/Sidebar.svelte",
      "UI/src/routes.js",
      "UI/src/utils/Permissions.js",
      "UI/src/utils/Functions.js",
      "UI/src/routes/association/course/campsAndRetreats/CampsAndRetreatsList.svelte",
      "UI/src/routes/association/course/campsAndRetreats/modals/AddModal.svelte",
      "UI/src/routes/association/course/campsAndRetreats/overview/CampsAndRetreatsOverview.svelte",
      "UI/src/routes/association/course/campsAndRetreats/overview/CampsAndRetreatsPeriodOverview.svelte",
      "UI/src/routes/association/course/campsAndRetreats/overview/modals/AddModal.svelte",
      "UI/src/routes/association/course/campsAndRetreats/overview/modals/camps-and-retreats-periods-form.svelte",
      "UI/src/components/inputs/DateRangePicker.svelte",
      "UI/src/components/inputs/DateRangeCalendar.svelte",
      "UI/src/routes/association/course/campsAndRetreats/overview/modals/AddServiceModal.svelte",
      "UI/src/routes/association/course/campsAndRetreats/overview/modals/EditServiceModal.svelte",
      "UI/src/routes/association/course/campsAndRetreats/overview/modals/camps-and-retreats-services-form.svelte",
      "UI/src/routes/association/course/campsAndRetreats/overview/modals/camps-and-retreats-subscriptions-form.svelte",
      "UI/src/routes/association/course/campsAndRetreats/overview/modals/components/PeriodAndServiceSelector.svelte",
      "UI/src/routes/forms/CampsAndRetreatsForm.svelte",
      "BE/application/views/camp_and_retreats_views.py",
      "BE/application/serializers/camps_and_retreats_serializers.py",
      "BE/application/utils/camps_and_retreats_utils.py",
      "BE/application/models/courses_models.py",
      "BE/application/permissions_registry.py",
      "UI/src/routes/calendar/Calendar.svelte",
      "UI/src/routes/calendar/modals/AddCalendarEvent.svelte",
      "UI/src/routes/association/course/overview/OverviewCourse.svelte",
      "UI/src/routes/association/course/overview/components/Calendar.svelte",
      "UI/src/routes/calendar/SharedCalendar.svelte",
      "BE/application/views/attendee_views.py",
      "BE/application/utils/global_calendar.py",
      "BE/application/models/attendee_models.py",
      "UI/src/routes/profile/sections/Integrations.svelte",
      "BE/application/views/google_views.py",
      "BE/application/urls.py"
    ],
    "dependencies": [
      "selfhost/tests/browser/manuale/scenario.mjs",
      "selfhost/tests/browser/manuale/frame.mjs",
      "selfhost/tests/browser/manuale/redaction.mjs",
      "selfhost/tests/browser/playwright.manual.config.mjs",
      "BE/application/management/commands/seed_manuale.py",
      "BE/application/management/commands/run_manuale_instance.py",
      "docs/manuale/accounting-calendar-authored-workflows.mjs",
      "selfhost/tests/browser/manuale/camps-calendar.mjs",
      "selfhost/tests/browser/manuale/camps-calendar-sources.mjs",
      "docs/manuale/camps-calendar-recipes.mjs"
    ],
    "checkpoints": [
      {
        "id": "camp-list-empty-and-create-control",
        "caption": "Elenco Camp e Ritiri e pulsante di creazione"
      },
      {
        "id": "camp-title-description-before-create",
        "caption": "Titolo e descrizione del nuovo ritiro prima della creazione"
      },
      {
        "id": "camp-created-and-public-link-visible-after-reload",
        "caption": "Camp creato e pagina di gestione dopo il ricaricamento"
      },
      {
        "id": "period-selected-range-fee-capacity-before-create",
        "caption": "Intervallo, quota e numero massimo del periodo prima di Crea"
      },
      {
        "id": "created-period-and-dates-persist-after-reload",
        "caption": "Periodo aggiunto e presente dopo il ricaricamento"
      },
      {
        "id": "period-updated-dates-fee-and-unlimited-capacity-before-save",
        "caption": "Nuove date, quota e limite vuoto prima del salvataggio"
      },
      {
        "id": "updated-period-persists-after-reload",
        "caption": "Periodo aggiornato e conservato dopo il ricaricamento"
      },
      {
        "id": "service-title-fee-income-category-before-create",
        "caption": "Servizio Pranzo con costo e causale di entrata prima di Crea"
      },
      {
        "id": "service-created-and-income-category-persists-after-reload",
        "caption": "Servizio aggiunto e causale conservata dopo il ricaricamento"
      },
      {
        "id": "service-edit-title-cost-and-delete-control",
        "caption": "Modifica del servizio con i comandi Salva ed Elimina"
      },
      {
        "id": "edited-service-persists-after-reload",
        "caption": "Nome e costo del servizio aggiornati dopo il ricaricamento"
      },
      {
        "id": "deleted-service-absent-after-reload",
        "caption": "Servizio eliminato e assente dopo il ricaricamento"
      },
      {
        "id": "reader-consults-period-without-create-or-save-controls",
        "caption": "Periodo consultato dal collaboratore senza i comandi di creazione e salvataggio"
      },
      {
        "id": "general-calendar-default-day-list-and-toolbar",
        "caption": "Calendario generale nella vista lista giorno e barra dei comandi"
      },
      {
        "id": "general-calendar-month-view-and-navigation",
        "caption": "Griglia mensile"
      },
      {
        "id": "general-calendar-week-view",
        "caption": "Griglia settimanale per orari"
      },
      {
        "id": "general-calendar-day-view",
        "caption": "Griglia del giorno"
      },
      {
        "id": "general-calendar-week-list-view",
        "caption": "Lista della settimana"
      },
      {
        "id": "global-event-name-dates-description-without-course-or-reminder",
        "caption": "Evento generale con nome, orari e descrizione prima del salvataggio"
      },
      {
        "id": "global-event-persists-after-reload",
        "caption": "Evento generale salvato e presente dopo il ricaricamento"
      },
      {
        "id": "global-event-edit-title-description-and-fixed-dates",
        "caption": "Modifica del nome e della descrizione con le date disattivate"
      },
      {
        "id": "updated-global-event-persists-after-reload",
        "caption": "Evento aggiornato e presente dopo il ricaricamento"
      },
      {
        "id": "new-event-with-course-selected-before-save",
        "caption": "Nuovo evento con il corso Ginnastica per tutti selezionato"
      },
      {
        "id": "global-calendar-shows-persisted-course-lesson-and-global-event",
        "caption": "Calendario generale con la lezione del corso e l’appuntamento generale"
      },
      {
        "id": "course-calendar-contains-lesson-created-from-general-calendar",
        "caption": "Calendario del corso con la stessa lezione salvata"
      },
      {
        "id": "course-share-modal-direct-link-embed-whatsapp-and-email-controls",
        "caption": "Finestra Condividi calendario con collegamento e codice per il sito"
      },
      {
        "id": "reader-sees-global-event-with-disabled-save-and-delete",
        "caption": "Evento aperto dal collaboratore con salvataggio ed eliminazione disattivati"
      },
      {
        "id": "deleted-global-event-absent-course-lesson-preserved-after-reload",
        "caption": "Evento generale eliminato e lezione del corso ancora presente"
      }
    ],
    "sections": [
      {
        "path": "docs/camp-e-ritiri.mdx",
        "id": "cos-e-un-camp-o-ritiro", "intent": "camps.read",
        "title": "Cos'è un Camp o Ritiro",
        "checkpoints": [
          "camp-list-empty-and-create-control"
        ],
        "images": [
          {
            "from": "/images/camp-calendario/gestione/1.placeholder.svg",
            "checkpoint": "camp-list-empty-and-create-control"
          }
        ],
        "insert_images": []
      },
      {
        "path": "docs/camp-e-ritiri.mdx",
        "id": "creare-un-camp-o-ritiro", "intent": "camps.create",
        "title": "Creare un Camp o Ritiro",
        "checkpoints": [
          "camp-list-empty-and-create-control",
          "camp-title-description-before-create",
          "camp-created-and-public-link-visible-after-reload"
        ],
        "images": [
          {
            "from": "/images/camp-calendario/gestione/1.placeholder.svg",
            "checkpoint": "camp-list-empty-and-create-control"
          },
          {
            "from": "/images/camp-calendario/gestione/2.placeholder.svg",
            "checkpoint": "camp-title-description-before-create"
          },
          {
            "from": "/images/camp-calendario/gestione/3.placeholder.svg",
            "checkpoint": "camp-created-and-public-link-visible-after-reload"
          }
        ],
        "insert_images": []
      },
      {
        "path": "docs/camp-e-ritiri.mdx",
        "id": "gestire-i-periodi", "intent": "camps.periods.update",
        "title": "Gestire i periodi",
        "checkpoints": [
          "period-updated-dates-fee-and-unlimited-capacity-before-save",
          "updated-period-persists-after-reload",
          "reader-consults-period-without-create-or-save-controls"
        ],
        "images": [
          {
            "from": "/images/camp-calendario/gestione/6.placeholder.svg",
            "checkpoint": "period-updated-dates-fee-and-unlimited-capacity-before-save"
          },
          {
            "from": "/images/camp-calendario/gestione/7.placeholder.svg",
            "checkpoint": "updated-period-persists-after-reload"
          },
          {
            "from": "/images/camp-calendario/gestione/13.placeholder.svg",
            "checkpoint": "reader-consults-period-without-create-or-save-controls"
          }
        ],
        "insert_images": []
      },
      {
        "path": "docs/camp-e-ritiri.mdx",
        "id": "aggiungere-un-periodo", "intent": "camps.periods.create",
        "title": "Aggiungere un periodo",
        "checkpoints": [
          "period-selected-range-fee-capacity-before-create",
          "created-period-and-dates-persist-after-reload"
        ],
        "images": [
          {
            "from": "/images/camp-calendario/gestione/4.placeholder.svg",
            "checkpoint": "period-selected-range-fee-capacity-before-create"
          },
          {
            "from": "/images/camp-calendario/gestione/5.placeholder.svg",
            "checkpoint": "created-period-and-dates-persist-after-reload"
          }
        ],
        "insert_images": []
      },
      {
        "path": "docs/camp-e-ritiri.mdx",
        "id": "modificare-un-periodo", "intent": "camps.periods.update",
        "title": "Modificare un periodo",
        "checkpoints": [
          "period-updated-dates-fee-and-unlimited-capacity-before-save",
          "updated-period-persists-after-reload",
          "reader-consults-period-without-create-or-save-controls"
        ],
        "images": [
          {
            "from": "/images/camp-calendario/gestione/6.placeholder.svg",
            "checkpoint": "period-updated-dates-fee-and-unlimited-capacity-before-save"
          },
          {
            "from": "/images/camp-calendario/gestione/7.placeholder.svg",
            "checkpoint": "updated-period-persists-after-reload"
          },
          {
            "from": "/images/camp-calendario/gestione/13.placeholder.svg",
            "checkpoint": "reader-consults-period-without-create-or-save-controls"
          }
        ],
        "insert_images": []
      },
      {
        "path": "docs/camp-e-ritiri.mdx",
        "id": "gestire-i-servizi", "intent": "camps.services.create",
        "title": "Gestire i servizi",
        "checkpoints": [
          "service-title-fee-income-category-before-create",
          "service-created-and-income-category-persists-after-reload"
        ],
        "images": [
          {
            "from": "/images/camp-calendario/gestione/8.placeholder.svg",
            "checkpoint": "service-title-fee-income-category-before-create"
          },
          {
            "from": "/images/camp-calendario/gestione/9.placeholder.svg",
            "checkpoint": "service-created-and-income-category-persists-after-reload"
          }
        ],
        "insert_images": []
      },
      {
        "path": "docs/camp-e-ritiri.mdx",
        "id": "aggiungere-un-servizio", "intent": "camps.services.create",
        "title": "Aggiungere un servizio",
        "checkpoints": [
          "service-title-fee-income-category-before-create",
          "service-created-and-income-category-persists-after-reload"
        ],
        "images": [
          {
            "from": "/images/camp-calendario/gestione/8.placeholder.svg",
            "checkpoint": "service-title-fee-income-category-before-create"
          },
          {
            "from": "/images/camp-calendario/gestione/9.placeholder.svg",
            "checkpoint": "service-created-and-income-category-persists-after-reload"
          }
        ],
        "insert_images": []
      },
      {
        "path": "docs/camp-e-ritiri.mdx",
        "id": "modificare-o-eliminare-un-servizio", "intent": "camps.services.update",
        "title": "Modificare o eliminare un servizio",
        "checkpoints": [
          "service-edit-title-cost-and-delete-control",
          "edited-service-persists-after-reload",
          "deleted-service-absent-after-reload"
        ],
        "images": [
          {
            "from": "/images/camp-calendario/gestione/10.placeholder.svg",
            "checkpoint": "service-edit-title-cost-and-delete-control"
          },
          {
            "from": "/images/camp-calendario/gestione/11.placeholder.svg",
            "checkpoint": "edited-service-persists-after-reload"
          },
          {
            "from": "/images/camp-calendario/gestione/12.placeholder.svg",
            "checkpoint": "deleted-service-absent-after-reload"
          }
        ],
        "insert_images": []
      },
      {
        "path": "docs/calendario.mdx",
        "id": "il-calendario-generale", "intent": "calendar.read",
        "title": "Il calendario generale",
        "checkpoints": [
          "general-calendar-default-day-list-and-toolbar",
          "reader-sees-global-event-with-disabled-save-and-delete"
        ],
        "images": [
          {
            "from": "/images/camp-calendario/gestione/14.placeholder.svg",
            "checkpoint": "general-calendar-default-day-list-and-toolbar"
          },
          {
            "from": "/images/camp-calendario/gestione/27.placeholder.svg",
            "checkpoint": "reader-sees-global-event-with-disabled-save-and-delete"
          }
        ],
        "insert_images": []
      },
      {
        "path": "docs/calendario.mdx",
        "id": "modalita-di-visualizzazione", "intent": "calendar.read",
        "title": "Modalità di visualizzazione",
        "checkpoints": [
          "general-calendar-default-day-list-and-toolbar",
          "general-calendar-month-view-and-navigation",
          "general-calendar-week-view",
          "general-calendar-day-view",
          "general-calendar-week-list-view"
        ],
        "images": [
          {
            "from": "/images/camp-calendario/gestione/14.placeholder.svg",
            "checkpoint": "general-calendar-default-day-list-and-toolbar"
          },
          {
            "from": "/images/camp-calendario/gestione/15.placeholder.svg",
            "checkpoint": "general-calendar-month-view-and-navigation"
          },
          {
            "from": "/images/camp-calendario/gestione/16.placeholder.svg",
            "checkpoint": "general-calendar-week-view"
          },
          {
            "from": "/images/camp-calendario/gestione/17.placeholder.svg",
            "checkpoint": "general-calendar-day-view"
          },
          {
            "from": "/images/camp-calendario/gestione/18.placeholder.svg",
            "checkpoint": "general-calendar-week-list-view"
          }
        ],
        "insert_images": []
      },
      {
        "path": "docs/calendario.mdx",
        "id": "vista-mese", "intent": "calendar.read",
        "title": "Vista Mese",
        "checkpoints": [
          "general-calendar-month-view-and-navigation"
        ],
        "images": [
          {
            "from": "/images/camp-calendario/gestione/15.placeholder.svg",
            "checkpoint": "general-calendar-month-view-and-navigation"
          }
        ],
        "insert_images": []
      },
      {
        "path": "docs/calendario.mdx",
        "id": "vista-settimana", "intent": "calendar.read",
        "title": "Vista Settimana",
        "checkpoints": [
          "general-calendar-week-view"
        ],
        "images": [
          {
            "from": "/images/camp-calendario/gestione/16.placeholder.svg",
            "checkpoint": "general-calendar-week-view"
          }
        ],
        "insert_images": []
      },
      {
        "path": "docs/calendario.mdx",
        "id": "vista-giorno", "intent": "calendar.read",
        "title": "Vista Giorno",
        "checkpoints": [
          "general-calendar-day-view"
        ],
        "images": [
          {
            "from": "/images/camp-calendario/gestione/17.placeholder.svg",
            "checkpoint": "general-calendar-day-view"
          }
        ],
        "insert_images": []
      },
      {
        "path": "docs/calendario.mdx",
        "id": "vista-lista", "intent": "calendar.read",
        "title": "Vista Lista",
        "checkpoints": [
          "general-calendar-default-day-list-and-toolbar",
          "general-calendar-week-list-view"
        ],
        "images": [
          {
            "from": "/images/camp-calendario/gestione/14.placeholder.svg",
            "checkpoint": "general-calendar-default-day-list-and-toolbar"
          },
          {
            "from": "/images/camp-calendario/gestione/18.placeholder.svg",
            "checkpoint": "general-calendar-week-list-view"
          }
        ],
        "insert_images": []
      },
      {
        "path": "docs/calendario.mdx",
        "id": "navigare-nel-calendario", "intent": "calendar.read",
        "title": "Navigare nel calendario",
        "checkpoints": [
          "general-calendar-month-view-and-navigation"
        ],
        "images": [
          {
            "from": "/images/camp-calendario/gestione/15.placeholder.svg",
            "checkpoint": "general-calendar-month-view-and-navigation"
          }
        ],
        "insert_images": []
      },
      {
        "path": "docs/calendario.mdx",
        "id": "creare-un-evento-o-promemoria", "intent": "calendar.events.create",
        "title": "Creare un evento o promemoria",
        "checkpoints": [
          "general-calendar-default-day-list-and-toolbar",
          "global-event-name-dates-description-without-course-or-reminder",
          "global-event-persists-after-reload",
          "new-event-with-course-selected-before-save",
          "global-calendar-shows-persisted-course-lesson-and-global-event",
          "course-calendar-contains-lesson-created-from-general-calendar"
        ],
        "images": [
          {
            "from": "/images/camp-calendario/gestione/14.placeholder.svg",
            "checkpoint": "general-calendar-default-day-list-and-toolbar"
          },
          {
            "from": "/images/camp-calendario/gestione/19.placeholder.svg",
            "checkpoint": "global-event-name-dates-description-without-course-or-reminder"
          },
          {
            "from": "/images/camp-calendario/gestione/20.placeholder.svg",
            "checkpoint": "global-event-persists-after-reload"
          },
          {
            "from": "/images/camp-calendario/gestione/23.placeholder.svg",
            "checkpoint": "new-event-with-course-selected-before-save"
          },
          {
            "from": "/images/camp-calendario/gestione/24.placeholder.svg",
            "checkpoint": "global-calendar-shows-persisted-course-lesson-and-global-event"
          },
          {
            "from": "/images/camp-calendario/gestione/25.placeholder.svg",
            "checkpoint": "course-calendar-contains-lesson-created-from-general-calendar"
          }
        ],
        "insert_images": []
      },
      {
        "path": "docs/calendario.mdx",
        "id": "modificare-o-eliminare-un-evento", "intent": "calendar.events.update",
        "title": "Modificare o eliminare un evento",
        "checkpoints": [
          "global-event-edit-title-description-and-fixed-dates",
          "updated-global-event-persists-after-reload",
          "deleted-global-event-absent-course-lesson-preserved-after-reload",
          "reader-sees-global-event-with-disabled-save-and-delete"
        ],
        "images": [
          {
            "from": "/images/camp-calendario/gestione/21.placeholder.svg",
            "checkpoint": "global-event-edit-title-description-and-fixed-dates"
          },
          {
            "from": "/images/camp-calendario/gestione/22.placeholder.svg",
            "checkpoint": "updated-global-event-persists-after-reload"
          },
          {
            "from": "/images/camp-calendario/gestione/27.placeholder.svg",
            "checkpoint": "reader-sees-global-event-with-disabled-save-and-delete"
          },
          {
            "from": "/images/camp-calendario/gestione/28.placeholder.svg",
            "checkpoint": "deleted-global-event-absent-course-lesson-preserved-after-reload"
          }
        ],
        "insert_images": []
      },
      {
        "path": "docs/calendario.mdx",
        "id": "esportare-il-calendario", "intent": "calendar.export",
        "title": "Esportare il calendario",
        "checkpoints": [
          "global-calendar-shows-persisted-course-lesson-and-global-event"
        ],
        "images": [
          {
            "from": "/images/camp-calendario/gestione/24.placeholder.svg",
            "checkpoint": "global-calendar-shows-persisted-course-lesson-and-global-event"
          }
        ],
        "insert_images": []
      },
      {
        "path": "docs/calendario.mdx",
        "id": "condivisione-del-calendario-con-gli-atleti", "intent": "calendar.share.read",
        "title": "Condivisione del calendario con gli atleti",
        "checkpoints": [
          "course-share-modal-direct-link-embed-whatsapp-and-email-controls"
        ],
        "images": [
          {
            "from": "/images/camp-calendario/gestione/26.placeholder.svg",
            "checkpoint": "course-share-modal-direct-link-embed-whatsapp-and-email-controls"
          }
        ],
        "insert_images": []
      }
    ],
    "outcome": {
      "field": "camps_calendar_manage",
      "expected": {
        "camp_title": "Ritiro Aurora 2026",
        "camp_persisted_after_reload": true,
        "period_initial_start": "2026-09-28",
        "period_initial_end": "2026-09-29",
        "period_updated_start": "2026-09-29",
        "period_updated_end": "2026-09-30",
        "period_fee": "90.00",
        "period_unlimited_capacity": true,
        "period_persisted_after_reload": true,
        "service_initial_fee": "15.00",
        "service_updated_fee": "18.00",
        "service_category_preserved": true,
        "service_persisted_after_reload": true,
        "service_deleted_after_reload": true,
        "no_camp_enrollments_or_payments": true,
        "reader_camp_update_status": 403,
        "global_event_created_after_reload": true,
        "global_event_updated_after_reload": true,
        "global_event_dates_preserved": true,
        "global_event_deleted_after_reload": true,
        "course_lesson_persisted_after_reload": true,
        "course_calendar_published": true,
        "course_lesson_survived_global_deletion": true,
        "views_exercised": "month,week,day,listDay,listWeek",
        "anonymous_course_calendar_status": 200,
        "share_panel_exercised": true,
        "ics_download_contains_course_lesson": true,
        "ics_download_excludes_global_event": true,
        "reader_global_update_status": 403,
        "reminders_enabled": false,
        "camp_registration_exercised": false,
        "attendance_write_exercised": false,
        "print_dialog_exercised": false,
        "external_share_dispatch_exercised": false,
        "google_link_export_revoke_exercised": false,
        "view_selection_buttons_active": true,
        "previous_next_today_navigation_preserves_events": true
      }
    }
  }
});
