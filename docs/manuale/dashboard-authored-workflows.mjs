// Canonical workflow metadata; prose belongs to the authored MDX.
export const dashboardAuthoredWorkflows = Object.freeze({
  "dashboard-personalize": {
    "version": 1,
    "script": "selfhost/tests/browser/manuale/dashboard.mjs",
    "prefix": "images/bacheca/personalizzazione/",
    "pages": [
      "docs/bacheca.mdx"
    ],
    "sources": [
      "UI/src/routes.js",
      "UI/src/routes/dashboard/Dashboard.svelte",
      "UI/src/routes/dashboard/EmptyDasbhoard.svelte",
      "UI/src/components/modals/WidgetModal.svelte",
      "UI/src/utils/Permissions.js",
      "UI/src/utils/userContext.js",
      "UI/src/components/widgets/Associates.svelte",
      "UI/src/components/widgets/Payments.svelte",
      "UI/src/components/widgets/BestCourses.svelte",
      "UI/src/components/widgets/Subscriptions.svelte",
      "UI/src/components/widgets/TodayLessons.svelte",
      "UI/src/components/widgets/ExpiringCarnets.svelte",
      "UI/src/components/widgets/SubscriptionsToApprove.svelte",
      "UI/src/components/widgets/ExpiringMedicalCertificates.svelte",
      "UI/src/components/widgets/IncomeAndExpenses.svelte",
      "UI/src/components/widgets/ExpiredPayments.svelte",
      "UI/src/components/widgets/ExpiredMedicalCertificates.svelte",
      "UI/src/components/widgets/StaffBoard.svelte",
      "BE/application/views/statistic_views.py",
      "BE/application/views/profile_views.py",
      "BE/application/serializers/auth_serializers.py",
      "BE/application/models/user_models.py",
      "BE/application/models/subscriptions_models.py",
      "BE/application/models/payment_models.py",
      "BE/application/models/courses_models.py",
      "BE/application/models/carnet_models.py",
      "BE/application/utils/api_utils.py",
      "BE/application/permissions_registry.py",
      "BE/application/impersonation.py",
      "BE/core/middleware.py"
    ],
    "dependencies": [
      "selfhost/tests/browser/manuale/dashboard-sources.mjs",
      "selfhost/tests/browser/manuale/dashboard.mjs",
      "selfhost/tests/browser/manuale/scenario.mjs",
      "selfhost/tests/browser/manuale/frame.mjs",
      "selfhost/tests/browser/manuale/redaction.mjs",
      "selfhost/tests/browser/playwright.manual.config.mjs",
      "BE/application/management/commands/seed_manuale.py",
      "UI/src/components/dashboard/",
      "UI/src/routes/dashboard/",
      "docs/manuale/dashboard-recipes.mjs"
    ],
    "checkpoints": [
      {
        "id": "default-dashboard-eight-widgets",
        "caption": "Bacheca iniziale con otto riquadri."
      },
      {
        "id": "edit-mode-size-remove-and-save-controls",
        "caption": "Controlli per larghezza, rimozione e salvataggio."
      },
      {
        "id": "add-widget-modal-expired-certificates-selected",
        "caption": "Scelta del widget Certificati medici scaduti."
      },
      {
        "id": "added-widget-resized-to-full-width",
        "caption": "Widget aggiunto alla larghezza massima."
      },
      {
        "id": "added-widget-and-width-persist-after-reload",
        "caption": "Widget e larghezza conservati dopo la ricarica."
      },
      {
        "id": "remove-added-widget-with-trash-control",
        "caption": "Cestino per rimuovere il riquadro dalla bacheca."
      },
      {
        "id": "removed-widget-absent-after-reload",
        "caption": "Bacheca dopo la rimozione salvata."
      },
      {
        "id": "default-restored-before-save",
        "caption": "Disposizione iniziale ripristinata prima del salvataggio."
      },
      {
        "id": "default-layout-persists-after-reload",
        "caption": "Disposizione iniziale conservata dopo la ricarica."
      },
      {
        "id": "reader-dashboard-staff-permission-limit",
        "caption": "Bacheca del collaboratore con limite sui messaggi dello staff."
      },
      {
        "id": "reader-personal-width-persists-owner-layout-unchanged",
        "caption": "Larghezza personale del collaboratore, distinta da quella del titolare."
      }
    ],
    "sections": [
      {
        "path": "docs/bacheca.mdx",
        "id": "personalizzare-la-bacheca",
        "title": "Personalizzare la bacheca",
        "checkpoints": [
          "edit-mode-size-remove-and-save-controls",
          "add-widget-modal-expired-certificates-selected",
          "added-widget-resized-to-full-width",
          "added-widget-and-width-persist-after-reload",
          "reader-personal-width-persists-owner-layout-unchanged"
        ],
        "images": [
          {
            "checkpoint": "edit-mode-size-remove-and-save-controls",
            "from": "/images/bacheca/personalizzazione/2.placeholder.svg"
          },
          {
            "checkpoint": "add-widget-modal-expired-certificates-selected",
            "from": "/images/bacheca/personalizzazione/3.placeholder.svg"
          },
          {
            "checkpoint": "added-widget-resized-to-full-width",
            "from": "/images/bacheca/personalizzazione/4.placeholder.svg"
          },
          {
            "checkpoint": "added-widget-and-width-persist-after-reload",
            "from": "/images/bacheca/personalizzazione/5.placeholder.svg"
          },
          {
            "checkpoint": "reader-personal-width-persists-owner-layout-unchanged",
            "from": "/images/bacheca/personalizzazione/11.placeholder.svg"
          }
        ],
        "insert_images": []
      },
      {
        "path": "docs/bacheca.mdx",
        "id": "ridimensionare-i-widget",
        "title": "Ridimensionare i widget",
        "checkpoints": [
          "edit-mode-size-remove-and-save-controls",
          "added-widget-resized-to-full-width",
          "added-widget-and-width-persist-after-reload"
        ],
        "images": [
          {
            "checkpoint": "edit-mode-size-remove-and-save-controls",
            "from": "/images/bacheca/personalizzazione/2.placeholder.svg"
          },
          {
            "checkpoint": "added-widget-resized-to-full-width",
            "from": "/images/bacheca/personalizzazione/4.placeholder.svg"
          },
          {
            "checkpoint": "added-widget-and-width-persist-after-reload",
            "from": "/images/bacheca/personalizzazione/5.placeholder.svg"
          }
        ],
        "insert_images": []
      },
      {
        "path": "docs/bacheca.mdx",
        "id": "aggiungere-un-widget",
        "title": "Aggiungere un widget",
        "checkpoints": [
          "edit-mode-size-remove-and-save-controls",
          "add-widget-modal-expired-certificates-selected",
          "added-widget-and-width-persist-after-reload"
        ],
        "images": [
          {
            "checkpoint": "edit-mode-size-remove-and-save-controls",
            "from": "/images/bacheca/personalizzazione/2.placeholder.svg"
          },
          {
            "checkpoint": "add-widget-modal-expired-certificates-selected",
            "from": "/images/bacheca/personalizzazione/3.placeholder.svg"
          },
          {
            "checkpoint": "added-widget-and-width-persist-after-reload",
            "from": "/images/bacheca/personalizzazione/5.placeholder.svg"
          }
        ],
        "insert_images": []
      },
      {
        "path": "docs/bacheca.mdx",
        "id": "rimuovere-un-widget",
        "title": "Rimuovere un widget",
        "checkpoints": [
          "remove-added-widget-with-trash-control",
          "removed-widget-absent-after-reload"
        ],
        "images": [
          {
            "checkpoint": "remove-added-widget-with-trash-control",
            "from": "/images/bacheca/personalizzazione/6.placeholder.svg"
          },
          {
            "checkpoint": "removed-widget-absent-after-reload",
            "from": "/images/bacheca/personalizzazione/7.placeholder.svg"
          }
        ],
        "insert_images": []
      },
      {
        "path": "docs/bacheca.mdx",
        "id": "ripristinare-il-layout-predefinito",
        "title": "Ripristinare il layout predefinito",
        "checkpoints": [
          "default-restored-before-save",
          "default-layout-persists-after-reload"
        ],
        "images": [
          {
            "checkpoint": "default-restored-before-save",
            "from": "/images/bacheca/personalizzazione/8.placeholder.svg"
          },
          {
            "checkpoint": "default-layout-persists-after-reload",
            "from": "/images/bacheca/personalizzazione/9.placeholder.svg"
          }
        ],
        "insert_images": []
      }
    ],
    "outcome": {
      "field": "dashboard_workflow",
      "expected": {
        "initial_widget_count": 8,
        "available_widget_count": 12,
        "initially_offered_widget_count": 4,
        "added_widget": "expiredmedicalcertificates",
        "added_default_size": 4,
        "added_saved_size": 12,
        "associates_saved_size": 4,
        "added_persisted_after_reload": true,
        "removed_persisted_after_reload": true,
        "reset_unsaved_left_database_unchanged": true,
        "reset_persisted_after_reload": true,
        "reader_layout_status": 200,
        "reader_payments_size": 8,
        "reader_layout_persisted_after_reload": true,
        "owner_layout_preserved_after_reader_save": true,
        "reader_staff_permission_message": true,
        "drag_exercised": false,
        "attendance_write_exercised": false,
        "widget_business_actions_exercised": false
      }
    }
  },
  "dashboard-reorder": {
    "version": 1,
    "script": "selfhost/tests/browser/manuale/dashboard-reorder.mjs",
    "prefix": "images/bacheca/riordinamento/",
    "pages": [
      "docs/bacheca.mdx"
    ],
    "sources": [
      "UI/src/routes.js",
      "UI/src/routes/dashboard/Dashboard.svelte",
      "UI/src/routes/dashboard/EmptyDasbhoard.svelte",
      "UI/src/components/modals/WidgetModal.svelte",
      "UI/src/utils/Permissions.js",
      "UI/src/utils/userContext.js",
      "UI/src/components/widgets/Associates.svelte",
      "UI/src/components/widgets/Payments.svelte",
      "UI/src/components/widgets/BestCourses.svelte",
      "UI/src/components/widgets/Subscriptions.svelte",
      "UI/src/components/widgets/TodayLessons.svelte",
      "UI/src/components/widgets/ExpiringCarnets.svelte",
      "UI/src/components/widgets/SubscriptionsToApprove.svelte",
      "UI/src/components/widgets/ExpiringMedicalCertificates.svelte",
      "UI/src/components/widgets/IncomeAndExpenses.svelte",
      "UI/src/components/widgets/ExpiredPayments.svelte",
      "UI/src/components/widgets/ExpiredMedicalCertificates.svelte",
      "UI/src/components/widgets/StaffBoard.svelte",
      "BE/application/views/statistic_views.py",
      "BE/application/views/profile_views.py",
      "BE/application/serializers/auth_serializers.py",
      "BE/application/models/user_models.py",
      "BE/application/models/subscriptions_models.py",
      "BE/application/models/payment_models.py",
      "BE/application/models/courses_models.py",
      "BE/application/models/carnet_models.py",
      "BE/application/utils/api_utils.py",
      "BE/application/permissions_registry.py",
      "BE/application/impersonation.py",
      "BE/core/middleware.py"
    ],
    "dependencies": [
      "selfhost/tests/browser/manuale/dashboard-sources.mjs",
      "selfhost/tests/browser/manuale/dashboard.mjs",
      "selfhost/tests/browser/manuale/scenario.mjs",
      "selfhost/tests/browser/manuale/frame.mjs",
      "selfhost/tests/browser/manuale/redaction.mjs",
      "selfhost/tests/browser/playwright.manual.config.mjs",
      "BE/application/management/commands/seed_manuale.py",
      "docs/manuale/dashboard-recipes.mjs"
    ],
    "checkpoints": [
      {
        "id": "dashboard-reorder-edit-before-drag",
        "caption": "Bacheca in modifica prima del trascinamento."
      },
      {
        "id": "dashboard-reorder-drag-before-save",
        "caption": "Ordine modificato prima del salvataggio."
      },
      {
        "id": "dashboard-reorder-order-persists-after-reload",
        "caption": "Nuovo ordine conservato dopo la ricarica."
      }
    ],
    "sections": [
      {
        "path": "docs/bacheca.mdx",
        "id": "riordinare-i-widget",
        "title": "Riordinare i widget",
        "checkpoints": [
          "dashboard-reorder-edit-before-drag",
          "dashboard-reorder-drag-before-save",
          "dashboard-reorder-order-persists-after-reload"
        ],
        "images": [
          {
            "checkpoint": "dashboard-reorder-edit-before-drag",
            "from": "/images/bacheca/riordinamento/1.placeholder.svg"
          },
          {
            "checkpoint": "dashboard-reorder-drag-before-save",
            "from": "/images/bacheca/riordinamento/2.placeholder.svg"
          },
          {
            "checkpoint": "dashboard-reorder-order-persists-after-reload",
            "from": "/images/bacheca/riordinamento/3.placeholder.svg"
          }
        ],
        "insert_images": []
      }
    ],
    "outcome": {
      "field": "dashboard_reorder",
      "expected": {
        "widgets": 8,
        "first_widget": "payments",
        "second_widget": "associates",
        "real_mouse_drag": true,
        "unsaved_database_unchanged": true,
        "order_persisted_after_reload": true,
        "sizes_preserved": true,
        "reader_layout_preserved": true
      }
    }
  }
});
