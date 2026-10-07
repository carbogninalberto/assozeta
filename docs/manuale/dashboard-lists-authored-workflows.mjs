// Prepared real list workflows. Fixtures and source review never attest browser execution.
export const dashboardListsAuthoredWorkflows=Object.freeze({
  "dashboard-lists": {
    "version": 1,
    "script": "selfhost/tests/browser/manuale/dashboard-lists.mjs",
    "prefix": "images/bacheca/liste-operative/",
    "pages": [
      "docs/bacheca.mdx"
    ],
    "sources": [
      "BE/application/impersonation.py",
      "BE/application/models/attendee_models.py",
      "BE/application/models/carnet_models.py",
      "BE/application/models/courses_models.py",
      "BE/application/models/payment_models.py",
      "BE/application/models/subscriptions_models.py",
      "BE/application/models/user_models.py",
      "BE/application/permissions_registry.py",
      "BE/application/serializers/auth_serializers.py",
      "BE/application/serializers/payment_serializers.py",
      "BE/application/serializers/subscriptions_serializers.py",
      "BE/application/services/invoice_service.py",
      "BE/application/signals.py",
      "BE/application/urls.py",
      "BE/application/utils/api_utils.py",
      "BE/application/utils/attendance_utils.py",
      "BE/application/utils/subscriptions_utils.py",
      "BE/application/views/attendee_views.py",
      "BE/application/views/payment_views.py",
      "BE/application/views/profile_views.py",
      "BE/application/views/statistic_views.py",
      "BE/application/views/subscriptions_views.py",
      "UI/src/components/buttons/ApproveButton.svelte",
      "UI/src/components/modals/WidgetModal.svelte",
      "UI/src/components/widgets/Associates.svelte",
      "UI/src/components/widgets/BestCourses.svelte",
      "UI/src/components/widgets/ExpiredMedicalCertificates.svelte",
      "UI/src/components/widgets/ExpiredPayments.svelte",
      "UI/src/components/widgets/ExpiringCarnets.svelte",
      "UI/src/components/widgets/ExpiringMedicalCertificates.svelte",
      "UI/src/components/widgets/IncomeAndExpenses.svelte",
      "UI/src/components/widgets/Payments.svelte",
      "UI/src/components/widgets/StaffBoard.svelte",
      "UI/src/components/widgets/Subscriptions.svelte",
      "UI/src/components/widgets/SubscriptionsToApprove.svelte",
      "UI/src/components/widgets/TodayLessons.svelte",
      "UI/src/layouts/DashboardLayout.svelte",
      "UI/src/routes.js",
      "UI/src/routes/association/Members/detail/Detail.svelte",
      "UI/src/routes/association/Members/detail/DetailDrawer.svelte",
      "UI/src/routes/association/Members/detail/sections/Carnet.svelte",
      "UI/src/routes/association/Members/detail/sections/Medical.svelte",
      "UI/src/routes/association/Members/detail/sections/modals/AddMedicalCertificate.svelte",
      "UI/src/routes/association/course/overview/components/modals/MarkAttendees.svelte",
      "UI/src/routes/dashboard/Dashboard.svelte",
      "UI/src/utils/Permissions.js"
    ],
    "dependencies": [
      "selfhost/tests/browser/manuale/scenario.mjs",
      "selfhost/tests/browser/manuale/expected-denials.mjs",
      "selfhost/tests/browser/manuale/frame.mjs",
      "selfhost/tests/browser/manuale/redaction.mjs",
      "selfhost/tests/browser/playwright.manual.config.mjs",
      "selfhost/tests/browser/manuale/dashboard-lists-harness.mjs",
      "BE/application/management/commands/seed_manuale.py",
      "BE/application/management/commands/run_manuale_dashboard_lists.py",
      "BE/application/tests/test_dashboard_expiring_carnets.py"
    ],
    "checkpoints": [
      {
        "id": "dashboard-list-today-lesson",
        "caption": "Lezione di oggi con zero presenze su due iscritti"
      },
      {
        "id": "dashboard-list-attendance-editor",
        "caption": "Elenco degli iscritti nel registro aperto dalla bacheca"
      },
      {
        "id": "dashboard-list-attendance-persists",
        "caption": "Presenza di Giulia conservata dopo la riapertura"
      },
      {
        "id": "dashboard-list-reader-attendance-disabled",
        "caption": "Registro in lettura per il collaboratore"
      },
      {
        "id": "dashboard-list-carnet-boundaries",
        "caption": "Carnet con zero, una e tre lezioni residue"
      },
      {
        "id": "dashboard-list-carnet-member-link",
        "caption": "Scheda carnet di Giulia aperta dal riquadro"
      },
      {
        "id": "dashboard-list-pending-and-unsigned",
        "caption": "Iscrizioni in attesa e non firmate da gestire"
      },
      {
        "id": "dashboard-list-approval-cancelled",
        "caption": "Annullamento della conferma senza modificare lo stato"
      },
      {
        "id": "dashboard-list-reader-approval-rejected",
        "caption": "Approva negato al collaboratore in lettura"
      },
      {
        "id": "dashboard-list-approval-confirmation",
        "caption": "Conferma prima di approvare Marta"
      },
      {
        "id": "dashboard-list-approved-removed",
        "caption": "Marta approvata e rimossa dall’elenco delle richieste"
      },
      {
        "id": "dashboard-list-rejection-confirmation",
        "caption": "Conferma prima di rifiutare Paolo"
      },
      {
        "id": "dashboard-list-rejected-removed",
        "caption": "Elenco vuoto dopo approvazione e rifiuto"
      },
      {
        "id": "dashboard-list-medical-zero-thirty",
        "caption": "Certificati che scadono oggi e fra trenta giorni"
      },
      {
        "id": "dashboard-list-medical-expired-and-archived",
        "caption": "Certificati scaduti, incluso il caso archiviato"
      },
      {
        "id": "dashboard-list-medical-member-link",
        "caption": "Scheda del certificato scaduto di Emma"
      },
      {
        "id": "dashboard-list-medical-date-editor",
        "caption": "Correzione della scadenza nella scheda personale"
      },
      {
        "id": "dashboard-list-medical-corrected-after-reload",
        "caption": "Emma passa nel riquadro dei certificati in scadenza"
      },
      {
        "id": "dashboard-list-reader-medical-readonly",
        "caption": "Certificato consultabile dal collaboratore senza modifica"
      },
      {
        "id": "dashboard-list-payment-date-boundaries",
        "caption": "Entrate con ritardo di ventinove giorni, oggi e fra sette giorni"
      },
      {
        "id": "dashboard-list-payment-cancelled",
        "caption": "Annullamento dell’incasso senza cambiare i pagamenti"
      },
      {
        "id": "dashboard-list-payment-collection-confirmation",
        "caption": "Conferma della data di incasso"
      },
      {
        "id": "dashboard-list-paid-entry-removed",
        "caption": "Pagamento saldato rimosso dal riquadro dopo riapertura"
      },
      {
        "id": "dashboard-list-reader-payment-action-hidden",
        "caption": "Collaboratore senza comando di incasso"
      },
      {
        "id": "dashboard-list-fixture-layout-restored",
        "caption": "Bacheca originale dopo rimozione dei dati dimostrativi"
      }
    ],
    "sections": [
      {
        "path": "docs/bacheca.mdx",
        "id": "lezioni-di-oggi",
        "title": "Lezioni di oggi",
        "checkpoints": [
          "dashboard-list-today-lesson",
          "dashboard-list-attendance-editor",
          "dashboard-list-attendance-persists",
          "dashboard-list-reader-attendance-disabled"
        ],
        "images": [
          {
            "checkpoint": "dashboard-list-today-lesson",
            "from": "/images/bacheca/liste-operative/1.placeholder.svg"
          },
          {
            "checkpoint": "dashboard-list-attendance-editor",
            "from": "/images/bacheca/liste-operative/2.placeholder.svg"
          },
          {
            "checkpoint": "dashboard-list-attendance-persists",
            "from": "/images/bacheca/liste-operative/3.placeholder.svg"
          },
          {
            "checkpoint": "dashboard-list-reader-attendance-disabled",
            "from": "/images/bacheca/liste-operative/4.placeholder.svg"
          }
        ],
        "insert_images": []
      },
      {
        "path": "docs/bacheca.mdx",
        "id": "carnet-in-esaurimento",
        "title": "Carnet in esaurimento",
        "checkpoints": [
          "dashboard-list-carnet-boundaries",
          "dashboard-list-carnet-member-link"
        ],
        "images": [
          {
            "checkpoint": "dashboard-list-carnet-boundaries",
            "from": "/images/bacheca/liste-operative/5.placeholder.svg"
          },
          {
            "checkpoint": "dashboard-list-carnet-member-link",
            "from": "/images/bacheca/liste-operative/6.placeholder.svg"
          }
        ],
        "insert_images": []
      },
      {
        "path": "docs/bacheca.mdx",
        "id": "iscrizioni-da-approvare",
        "title": "Iscrizioni da approvare",
        "checkpoints": [
          "dashboard-list-pending-and-unsigned",
          "dashboard-list-approval-cancelled",
          "dashboard-list-reader-approval-rejected",
          "dashboard-list-approval-confirmation",
          "dashboard-list-approved-removed",
          "dashboard-list-rejection-confirmation",
          "dashboard-list-rejected-removed"
        ],
        "images": [
          {
            "checkpoint": "dashboard-list-pending-and-unsigned",
            "from": "/images/bacheca/liste-operative/7.placeholder.svg"
          },
          {
            "checkpoint": "dashboard-list-approval-cancelled",
            "from": "/images/bacheca/liste-operative/8.placeholder.svg"
          },
          {
            "checkpoint": "dashboard-list-reader-approval-rejected",
            "from": "/images/bacheca/liste-operative/9.placeholder.svg"
          },
          {
            "checkpoint": "dashboard-list-approval-confirmation",
            "from": "/images/bacheca/liste-operative/10.placeholder.svg"
          },
          {
            "checkpoint": "dashboard-list-approved-removed",
            "from": "/images/bacheca/liste-operative/11.placeholder.svg"
          },
          {
            "checkpoint": "dashboard-list-rejection-confirmation",
            "from": "/images/bacheca/liste-operative/12.placeholder.svg"
          },
          {
            "checkpoint": "dashboard-list-rejected-removed",
            "from": "/images/bacheca/liste-operative/13.placeholder.svg"
          }
        ],
        "insert_images": []
      },
      {
        "path": "docs/bacheca.mdx",
        "id": "certificati-medici-in-scadenza",
        "title": "Certificati medici in scadenza",
        "checkpoints": [
          "dashboard-list-medical-zero-thirty",
          "dashboard-list-medical-corrected-after-reload",
          "dashboard-list-reader-medical-readonly"
        ],
        "images": [
          {
            "checkpoint": "dashboard-list-medical-zero-thirty",
            "from": "/images/bacheca/liste-operative/14.placeholder.svg"
          },
          {
            "checkpoint": "dashboard-list-medical-corrected-after-reload",
            "from": "/images/bacheca/liste-operative/18.placeholder.svg"
          },
          {
            "checkpoint": "dashboard-list-reader-medical-readonly",
            "from": "/images/bacheca/liste-operative/19.placeholder.svg"
          }
        ],
        "insert_images": []
      },
      {
        "path": "docs/bacheca.mdx",
        "id": "certificati-medici-scaduti",
        "title": "Certificati medici scaduti",
        "checkpoints": [
          "dashboard-list-medical-expired-and-archived",
          "dashboard-list-medical-member-link",
          "dashboard-list-medical-date-editor",
          "dashboard-list-medical-corrected-after-reload"
        ],
        "images": [
          {
            "checkpoint": "dashboard-list-medical-expired-and-archived",
            "from": "/images/bacheca/liste-operative/15.placeholder.svg"
          },
          {
            "checkpoint": "dashboard-list-medical-member-link",
            "from": "/images/bacheca/liste-operative/16.placeholder.svg"
          },
          {
            "checkpoint": "dashboard-list-medical-date-editor",
            "from": "/images/bacheca/liste-operative/17.placeholder.svg"
          },
          {
            "checkpoint": "dashboard-list-medical-corrected-after-reload",
            "from": "/images/bacheca/liste-operative/18.placeholder.svg"
          }
        ],
        "insert_images": []
      },
      {
        "path": "docs/bacheca.mdx",
        "id": "pagamenti-scaduti",
        "title": "Pagamenti scaduti",
        "checkpoints": [
          "dashboard-list-payment-date-boundaries",
          "dashboard-list-payment-cancelled",
          "dashboard-list-payment-collection-confirmation",
          "dashboard-list-paid-entry-removed",
          "dashboard-list-reader-payment-action-hidden"
        ],
        "images": [
          {
            "checkpoint": "dashboard-list-payment-date-boundaries",
            "from": "/images/bacheca/liste-operative/20.placeholder.svg"
          },
          {
            "checkpoint": "dashboard-list-payment-cancelled",
            "from": "/images/bacheca/liste-operative/21.placeholder.svg"
          },
          {
            "checkpoint": "dashboard-list-payment-collection-confirmation",
            "from": "/images/bacheca/liste-operative/22.placeholder.svg"
          },
          {
            "checkpoint": "dashboard-list-paid-entry-removed",
            "from": "/images/bacheca/liste-operative/23.placeholder.svg"
          },
          {
            "checkpoint": "dashboard-list-reader-payment-action-hidden",
            "from": "/images/bacheca/liste-operative/24.placeholder.svg"
          }
        ],
        "insert_images": []
      }
    ],
    "outcome": {
      "field": "dashboard_lists",
      "expected": {
        "today_only_and_presence_persisted": true,
        "reader_attendance_denied_unchanged": true,
        "carnet_zero_recent_one_three_only": true,
        "pending_cancel_and_reader_denial_unchanged": true,
        "approve_and_reject_persisted": true,
        "medical_date_boundaries_and_saved_correction": true,
        "reader_medical_denied_unchanged": true,
        "payment_window_cancel_and_collection_persisted": true,
        "reader_payment_denied_unchanged": true,
        "owned_fixture_removed_and_layouts_restored": true
      }
    }
  }
});
