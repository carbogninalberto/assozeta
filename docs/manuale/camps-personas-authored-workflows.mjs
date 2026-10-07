// Complete authored local procedures, pending actual real-backend captures.
// Native print destinations, account creation, delivery and provider transactions remain explicit limits.
export const campsPersonasAuthoredWorkflows = Object.freeze({
  "camps-enrollment-local": {
    "version": 1,
    "script": "selfhost/tests/browser/manuale/camps-enrollment-local.mjs",
    "prefix": "images/camp-e-ritiri/iscrizioni-reali/",
    "fixture_profile": "member-transfer",
    "pages": [
      "docs/camp-e-ritiri.mdx"
    ],
    "sources": [
      "BE/application/impersonation.py",
      "BE/application/mixin.py",
      "BE/application/models/attendee_models.py",
      "BE/application/models/carnet_models.py",
      "BE/application/models/courses_models.py",
      "BE/application/models/invoices_models.py",
      "BE/application/models/payment_models.py",
      "BE/application/models/subscriptions_models.py",
      "BE/application/models/user_models.py",
      "BE/application/permissions_registry.py",
      "BE/application/serializers/auth_serializers.py",
      "BE/application/serializers/camps_and_retreats_serializers.py",
      "BE/application/serializers/carnet_serializers.py",
      "BE/application/serializers/courses_serializers.py",
      "BE/application/serializers/invoice_serializers.py",
      "BE/application/serializers/payment_serializers.py",
      "BE/application/serializers/subscriptions_serializers.py",
      "BE/application/services/invoice_service.py",
      "BE/application/signals.py",
      "BE/application/tasks.py",
      "BE/application/urls.py",
      "BE/application/utils/attendance_utils.py",
      "BE/application/utils/camps_and_retreats_utils.py",
      "BE/application/utils/global_calendar.py",
      "BE/application/utils/subscriptions_utils.py",
      "BE/application/views/attendee_views.py",
      "BE/application/views/billing_views.py",
      "BE/application/views/camp_and_retreats_views.py",
      "BE/application/views/carnet_views.py",
      "BE/application/views/course_views.py",
      "BE/application/views/google_views.py",
      "BE/application/views/instructor_views.py",
      "BE/application/views/invoice_views.py",
      "BE/application/views/payment_views.py",
      "BE/application/views/subscriptions_views.py",
      "BE/core/middleware.py",
      "UI/src/App.svelte",
      "UI/src/components/Sidebar.svelte",
      "UI/src/components/Tabs.svelte",
      "UI/src/components/buttons/ApproveButton.svelte",
      "UI/src/components/buttons/DeleteButton.svelte",
      "UI/src/components/buttons/EditButton.svelte",
      "UI/src/components/buttons/MoveToArchiveButton.svelte",
      "UI/src/components/formBuilder/preview-blocks/smart-select-input.svelte",
      "UI/src/components/inputs/DateInput.svelte",
      "UI/src/components/inputs/DateRangeCalendar.svelte",
      "UI/src/components/inputs/DateRangePicker.svelte",
      "UI/src/components/tables/BKNDatatable.svelte",
      "UI/src/routes.js",
      "UI/src/routes/accounting/payment/PaymentDrawer.svelte",
      "UI/src/routes/accounting/payment/PaymentList.svelte",
      "UI/src/routes/accounting/payment/modals/AddEditModal.svelte",
      "UI/src/routes/accounting/payment/partials/payment-overview.svelte",
      "UI/src/routes/activities/payment/PaymentList.svelte",
      "UI/src/routes/association/Members/modals/PaymentModal.svelte",
      "UI/src/routes/association/course/CourseList.svelte",
      "UI/src/routes/association/course/CourseListArchive.svelte",
      "UI/src/routes/association/course/campsAndRetreats/CampsAndRetreatsList.svelte",
      "UI/src/routes/association/course/campsAndRetreats/modals/AddModal.svelte",
      "UI/src/routes/association/course/campsAndRetreats/overview/CampsAndRetreatsOverview.svelte",
      "UI/src/routes/association/course/campsAndRetreats/overview/CampsAndRetreatsPeriodOverview.svelte",
      "UI/src/routes/association/course/campsAndRetreats/overview/modals/AddModal.svelte",
      "UI/src/routes/association/course/campsAndRetreats/overview/modals/AddServiceModal.svelte",
      "UI/src/routes/association/course/campsAndRetreats/overview/modals/AddSubscriptionModal.svelte",
      "UI/src/routes/association/course/campsAndRetreats/overview/modals/EditServiceModal.svelte",
      "UI/src/routes/association/course/campsAndRetreats/overview/modals/EditSubscriptionModal.svelte",
      "UI/src/routes/association/course/campsAndRetreats/overview/modals/camps-and-retreats-periods-form.svelte",
      "UI/src/routes/association/course/campsAndRetreats/overview/modals/camps-and-retreats-services-form.svelte",
      "UI/src/routes/association/course/campsAndRetreats/overview/modals/camps-and-retreats-subscriptions-form.svelte",
      "UI/src/routes/association/course/campsAndRetreats/overview/modals/components/PeriodAndServiceSelector.svelte",
      "UI/src/routes/association/course/carnet/CarnetList.svelte",
      "UI/src/routes/association/course/carnet/add/AddCarnet.svelte",
      "UI/src/routes/association/course/carnet/add/sections/Section1.svelte",
      "UI/src/routes/association/course/carnet/add/sections/Section2.svelte",
      "UI/src/routes/association/course/carnet/detail/CarnetDetail.svelte",
      "UI/src/routes/association/course/carnet/detail/modals/AddCarnetModal.svelte",
      "UI/src/routes/association/course/carnet/detail/sections/Info.svelte",
      "UI/src/routes/association/course/carnet/detail/sections/Usage.svelte",
      "UI/src/routes/association/course/overview/OverviewCourse.svelte",
      "UI/src/routes/association/course/overview/components/Calendar.svelte",
      "UI/src/routes/association/course/overview/components/CourseNavigationTab.svelte",
      "UI/src/routes/association/course/overview/components/Registry.svelte",
      "UI/src/routes/association/course/overview/components/modals/AddCalendarEvent.svelte",
      "UI/src/routes/association/course/overview/components/modals/EditCalendarEvent.svelte",
      "UI/src/routes/association/course/overview/components/modals/MarkAttendees.svelte",
      "UI/src/routes/association/course/overview/modals/AddEditCourseSubscriptionModal.svelte",
      "UI/src/routes/association/course/overview/partials/course-subscriptions-list.svelte",
      "UI/src/routes/association/course/shared/NavigationTab.svelte",
      "UI/src/routes/calendar/Calendar.svelte",
      "UI/src/routes/calendar/SharedCalendar.svelte",
      "UI/src/routes/calendar/modals/AddCalendarEvent.svelte",
      "UI/src/routes/forms/CampsAndRetreatsForm.svelte",
      "UI/src/routes/profile/sections/Integrations.svelte",
      "UI/src/shim/form-validation.js",
      "UI/src/shim/modal.js",
      "UI/src/store/stores.js",
      "UI/src/utils/ApiMiddleware.js",
      "UI/src/utils/Functions.js",
      "UI/src/utils/Permissions.js",
      "UI/src/utils/calendarLessonNavigation.js",
      "UI/src/utils/dateValues.js",
      "UI/src/utils/eventCalendar.js",
      "UI/src/utils/userContext.js"
    ],
    "dependencies": [
      "BE/application/impersonation.py",
      "BE/application/management/commands/seed_manuale.py",
      "BE/application/mixin.py",
      "BE/application/models/attendee_models.py",
      "BE/application/models/carnet_models.py",
      "BE/application/models/courses_models.py",
      "BE/application/models/invoices_models.py",
      "BE/application/models/payment_models.py",
      "BE/application/models/subscriptions_models.py",
      "BE/application/models/user_models.py",
      "BE/application/permissions_registry.py",
      "BE/application/serializers/auth_serializers.py",
      "BE/application/serializers/camps_and_retreats_serializers.py",
      "BE/application/serializers/carnet_serializers.py",
      "BE/application/serializers/courses_serializers.py",
      "BE/application/serializers/invoice_serializers.py",
      "BE/application/serializers/payment_serializers.py",
      "BE/application/serializers/subscriptions_serializers.py",
      "BE/application/services/invoice_service.py",
      "BE/application/signals.py",
      "BE/application/tasks.py",
      "BE/application/tests/test_camp_public_enrollment_scope.py",
      "BE/application/urls.py",
      "BE/application/utils/attendance_utils.py",
      "BE/application/utils/camps_and_retreats_utils.py",
      "BE/application/utils/global_calendar.py",
      "BE/application/utils/subscriptions_utils.py",
      "BE/application/views/attendee_views.py",
      "BE/application/views/billing_views.py",
      "BE/application/views/camp_and_retreats_views.py",
      "BE/application/views/carnet_views.py",
      "BE/application/views/course_views.py",
      "BE/application/views/google_views.py",
      "BE/application/views/instructor_views.py",
      "BE/application/views/invoice_views.py",
      "BE/application/views/payment_views.py",
      "BE/application/views/subscriptions_views.py",
      "BE/core/middleware.py",
      "UI/src/App.svelte",
      "UI/src/components/Sidebar.svelte",
      "UI/src/components/Tabs.svelte",
      "UI/src/components/buttons/ApproveButton.svelte",
      "UI/src/components/buttons/DeleteButton.svelte",
      "UI/src/components/buttons/EditButton.svelte",
      "UI/src/components/buttons/MoveToArchiveButton.svelte",
      "UI/src/components/formBuilder/preview-blocks/smart-select-input.svelte",
      "UI/src/components/inputs/DateInput.svelte",
      "UI/src/components/inputs/DateRangeCalendar.svelte",
      "UI/src/components/inputs/DateRangePicker.svelte",
      "UI/src/components/tables/BKNDatatable.svelte",
      "UI/src/routes.js",
      "UI/src/routes/accounting/payment/PaymentDrawer.svelte",
      "UI/src/routes/accounting/payment/PaymentList.svelte",
      "UI/src/routes/accounting/payment/modals/AddEditModal.svelte",
      "UI/src/routes/accounting/payment/partials/payment-overview.svelte",
      "UI/src/routes/activities/payment/PaymentList.svelte",
      "UI/src/routes/association/Members/modals/PaymentModal.svelte",
      "UI/src/routes/association/course/CourseList.svelte",
      "UI/src/routes/association/course/CourseListArchive.svelte",
      "UI/src/routes/association/course/campsAndRetreats/CampsAndRetreatsList.svelte",
      "UI/src/routes/association/course/campsAndRetreats/modals/AddModal.svelte",
      "UI/src/routes/association/course/campsAndRetreats/overview/CampsAndRetreatsOverview.svelte",
      "UI/src/routes/association/course/campsAndRetreats/overview/CampsAndRetreatsPeriodOverview.svelte",
      "UI/src/routes/association/course/campsAndRetreats/overview/modals/AddModal.svelte",
      "UI/src/routes/association/course/campsAndRetreats/overview/modals/AddServiceModal.svelte",
      "UI/src/routes/association/course/campsAndRetreats/overview/modals/AddSubscriptionModal.svelte",
      "UI/src/routes/association/course/campsAndRetreats/overview/modals/EditServiceModal.svelte",
      "UI/src/routes/association/course/campsAndRetreats/overview/modals/EditSubscriptionModal.svelte",
      "UI/src/routes/association/course/campsAndRetreats/overview/modals/camps-and-retreats-periods-form.svelte",
      "UI/src/routes/association/course/campsAndRetreats/overview/modals/camps-and-retreats-services-form.svelte",
      "UI/src/routes/association/course/campsAndRetreats/overview/modals/camps-and-retreats-subscriptions-form.svelte",
      "UI/src/routes/association/course/campsAndRetreats/overview/modals/components/PeriodAndServiceSelector.svelte",
      "UI/src/routes/association/course/carnet/CarnetList.svelte",
      "UI/src/routes/association/course/carnet/add/AddCarnet.svelte",
      "UI/src/routes/association/course/carnet/add/sections/Section1.svelte",
      "UI/src/routes/association/course/carnet/add/sections/Section2.svelte",
      "UI/src/routes/association/course/carnet/detail/CarnetDetail.svelte",
      "UI/src/routes/association/course/carnet/detail/modals/AddCarnetModal.svelte",
      "UI/src/routes/association/course/carnet/detail/sections/Info.svelte",
      "UI/src/routes/association/course/carnet/detail/sections/Usage.svelte",
      "UI/src/routes/association/course/overview/OverviewCourse.svelte",
      "UI/src/routes/association/course/overview/components/Calendar.svelte",
      "UI/src/routes/association/course/overview/components/CourseNavigationTab.svelte",
      "UI/src/routes/association/course/overview/components/Registry.svelte",
      "UI/src/routes/association/course/overview/components/modals/AddCalendarEvent.svelte",
      "UI/src/routes/association/course/overview/components/modals/EditCalendarEvent.svelte",
      "UI/src/routes/association/course/overview/components/modals/MarkAttendees.svelte",
      "UI/src/routes/association/course/overview/modals/AddEditCourseSubscriptionModal.svelte",
      "UI/src/routes/association/course/overview/partials/course-subscriptions-list.svelte",
      "UI/src/routes/association/course/shared/NavigationTab.svelte",
      "UI/src/routes/calendar/Calendar.svelte",
      "UI/src/routes/calendar/SharedCalendar.svelte",
      "UI/src/routes/calendar/modals/AddCalendarEvent.svelte",
      "UI/src/routes/forms/CampsAndRetreatsForm.svelte",
      "UI/src/routes/profile/sections/Integrations.svelte",
      "UI/src/shim/form-validation.js",
      "UI/src/shim/modal.js",
      "UI/src/store/stores.js",
      "UI/src/utils/ApiMiddleware.js",
      "UI/src/utils/Functions.js",
      "UI/src/utils/Permissions.js",
      "UI/src/utils/calendarLessonNavigation.js",
      "UI/src/utils/dateValues.js",
      "UI/src/utils/eventCalendar.js",
      "UI/src/utils/userContext.js",
      "docs/manuale/camps-personas-authored-workflows.mjs",
      "selfhost/tests/browser/manuale/camps-enrollment-local.mjs",
      "selfhost/tests/browser/manuale/expected-denials.mjs",
      "selfhost/tests/browser/manuale/frame.mjs",
      "selfhost/tests/browser/manuale/redaction.mjs",
      "selfhost/tests/browser/manuale/scenario.mjs",
      "selfhost/tests/browser/playwright.manual.config.mjs"
    ],
    "checkpoints": [
      {
        "id": "camp-existing-period-and-empty-enrollments",
        "caption": "Camp configurato con periodo da 90 euro e servizio facoltativo da 15 euro, prima delle iscrizioni."
      },
      {
        "id": "camp-manual-adult-period-and-service-before-submit",
        "caption": "Luca selezionato: periodo e Pranzo prima di Iscrivi."
      },
      {
        "id": "camp-manual-enrollment-persists-with-unpaid-fee",
        "caption": "Iscrizione di Luca conservata dopo la ricarica, con quota complessiva non pagata."
      },
      {
        "id": "camp-unpaid-service-removal-before-save",
        "caption": "Servizio Pranzo deselezionato prima della modifica della quota aperta."
      },
      {
        "id": "camp-edited-unpaid-enrollment-after-reload",
        "caption": "Periodo conservato e servizio rimosso: nuova quota da 90 euro dopo la riapertura."
      },
      {
        "id": "camp-combined-quota-in-payment-list",
        "caption": "Quota camp da 105 euro ancora in attesa nei Pagamenti di Luca."
      },
      {
        "id": "camp-collection-confirmation-no-receipt-or-email",
        "caption": "Conferma dell’incasso con generazione del PDF della ricevuta e invio email disattivati."
      },
      {
        "id": "camp-paid-period-locked-in-edit",
        "caption": "Periodo pagato con selettori non modificabili."
      },
      {
        "id": "camp-public-link-anonymous-login-gate",
        "caption": "Collegamento camp senza sessione: accesso o creazione account richiesti."
      },
      {
        "id": "camp-public-authenticated-person-period-service",
        "caption": "Modulo pubblico aperto con il vero account destinatario: Giulia, periodo e Pranzo."
      },
      {
        "id": "camp-public-unpaid-quota-after-reload",
        "caption": "Pagina dei pagamenti dell’atleta dopo l’iscrizione pubblica e la ricarica."
      },
      {
        "id": "camp-reader-enrollments-write-controls-disabled",
        "caption": "Collaboratore in sola lettura: azioni camp disabilitate."
      },
      {
        "id": "camp-enrollment-removal-confirmation",
        "caption": "Conferma della rimozione della singola iscrizione; Annulla disponibile."
      },
      {
        "id": "camp-unpaid-enrollment-removed-after-reload",
        "caption": "Giulia rimossa dal camp: Luca rimane e la quota aperta di Giulia è assente."
      },
      {
        "id": "camp-enrollments-removed-camp-and-paid-quota-preserved",
        "caption": "Nessun iscritto rimasto; camp e periodo ancora presenti, incasso conservato."
      }
    ],
    "sections": [
      {
        "path": "docs/camp-e-ritiri.mdx",
        "id": "iscrivere-gli-atleti",
        "title": "Iscrivere gli atleti",
        "checkpoints": [
          "camp-existing-period-and-empty-enrollments",
          "camp-manual-enrollment-persists-with-unpaid-fee",
          "camp-reader-enrollments-write-controls-disabled"
        ],
        "images": [
          {
            "from": "/images/camp-e-ritiri/iscrizioni-reali/1.placeholder.svg",
            "checkpoint": "camp-existing-period-and-empty-enrollments"
          },
          {
            "from": "/images/camp-e-ritiri/iscrizioni-reali/3.placeholder.svg",
            "checkpoint": "camp-manual-enrollment-persists-with-unpaid-fee"
          },
          {
            "from": "/images/camp-e-ritiri/iscrizioni-reali/12.placeholder.svg",
            "checkpoint": "camp-reader-enrollments-write-controls-disabled"
          }
        ],
        "insert_images": []
      },
      {
        "path": "docs/camp-e-ritiri.mdx",
        "id": "aggiungere-un-iscritto-manualmente",
        "title": "Aggiungere un iscritto manualmente",
        "checkpoints": [
          "camp-manual-adult-period-and-service-before-submit",
          "camp-manual-enrollment-persists-with-unpaid-fee",
          "camp-combined-quota-in-payment-list"
        ],
        "images": [
          {
            "from": "/images/camp-e-ritiri/iscrizioni-reali/2.placeholder.svg",
            "checkpoint": "camp-manual-adult-period-and-service-before-submit"
          },
          {
            "from": "/images/camp-e-ritiri/iscrizioni-reali/3.placeholder.svg",
            "checkpoint": "camp-manual-enrollment-persists-with-unpaid-fee"
          },
          {
            "from": "/images/camp-e-ritiri/iscrizioni-reali/6.placeholder.svg",
            "checkpoint": "camp-combined-quota-in-payment-list"
          }
        ],
        "insert_images": []
      },
      {
        "path": "docs/camp-e-ritiri.mdx",
        "id": "modulo-di-iscrizione-pubblico",
        "title": "Modulo di iscrizione pubblico",
        "checkpoints": [
          "camp-public-link-anonymous-login-gate",
          "camp-public-authenticated-person-period-service",
          "camp-public-unpaid-quota-after-reload"
        ],
        "images": [
          {
            "from": "/images/camp-e-ritiri/iscrizioni-reali/9.placeholder.svg",
            "checkpoint": "camp-public-link-anonymous-login-gate"
          },
          {
            "from": "/images/camp-e-ritiri/iscrizioni-reali/10.placeholder.svg",
            "checkpoint": "camp-public-authenticated-person-period-service"
          },
          {
            "from": "/images/camp-e-ritiri/iscrizioni-reali/11.placeholder.svg",
            "checkpoint": "camp-public-unpaid-quota-after-reload"
          }
        ],
        "insert_images": []
      },
      {
        "path": "docs/camp-e-ritiri.mdx",
        "id": "modificare-un-iscrizione",
        "title": "Modificare un'iscrizione",
        "checkpoints": [
          "camp-unpaid-service-removal-before-save",
          "camp-edited-unpaid-enrollment-after-reload",
          "camp-paid-period-locked-in-edit"
        ],
        "images": [
          {
            "from": "/images/camp-e-ritiri/iscrizioni-reali/4.placeholder.svg",
            "checkpoint": "camp-unpaid-service-removal-before-save"
          },
          {
            "from": "/images/camp-e-ritiri/iscrizioni-reali/5.placeholder.svg",
            "checkpoint": "camp-edited-unpaid-enrollment-after-reload"
          },
          {
            "from": "/images/camp-e-ritiri/iscrizioni-reali/8.placeholder.svg",
            "checkpoint": "camp-paid-period-locked-in-edit"
          }
        ],
        "insert_images": []
      },
      {
        "path": "docs/camp-e-ritiri.mdx",
        "id": "rimuovere-un-iscrizione",
        "title": "Rimuovere un'iscrizione",
        "checkpoints": [
          "camp-enrollment-removal-confirmation",
          "camp-unpaid-enrollment-removed-after-reload",
          "camp-enrollments-removed-camp-and-paid-quota-preserved"
        ],
        "images": [
          {
            "from": "/images/camp-e-ritiri/iscrizioni-reali/13.placeholder.svg",
            "checkpoint": "camp-enrollment-removal-confirmation"
          },
          {
            "from": "/images/camp-e-ritiri/iscrizioni-reali/14.placeholder.svg",
            "checkpoint": "camp-unpaid-enrollment-removed-after-reload"
          },
          {
            "from": "/images/camp-e-ritiri/iscrizioni-reali/15.placeholder.svg",
            "checkpoint": "camp-enrollments-removed-camp-and-paid-quota-preserved"
          }
        ],
        "insert_images": []
      },
      {
        "path": "docs/camp-e-ritiri.mdx",
        "id": "gestire-i-pagamenti",
        "title": "Gestire i pagamenti",
        "checkpoints": [
          "camp-combined-quota-in-payment-list",
          "camp-edited-unpaid-enrollment-after-reload",
          "camp-collection-confirmation-no-receipt-or-email",
          "camp-paid-period-locked-in-edit"
        ],
        "images": [
          {
            "from": "/images/camp-e-ritiri/iscrizioni-reali/6.placeholder.svg",
            "checkpoint": "camp-combined-quota-in-payment-list"
          },
          {
            "from": "/images/camp-e-ritiri/iscrizioni-reali/5.placeholder.svg",
            "checkpoint": "camp-edited-unpaid-enrollment-after-reload"
          },
          {
            "from": "/images/camp-e-ritiri/iscrizioni-reali/7.placeholder.svg",
            "checkpoint": "camp-collection-confirmation-no-receipt-or-email"
          },
          {
            "from": "/images/camp-e-ritiri/iscrizioni-reali/8.placeholder.svg",
            "checkpoint": "camp-paid-period-locked-in-edit"
          }
        ],
        "insert_images": []
      },
      {
        "path": "docs/camp-e-ritiri.mdx",
        "id": "flusso-di-lavoro-consigliato",
        "title": "Flusso di lavoro consigliato",
        "checkpoints": [
          "camp-existing-period-and-empty-enrollments",
          "camp-manual-enrollment-persists-with-unpaid-fee",
          "camp-public-authenticated-person-period-service",
          "camp-paid-period-locked-in-edit"
        ],
        "images": [
          {
            "from": "/images/camp-e-ritiri/iscrizioni-reali/1.placeholder.svg",
            "checkpoint": "camp-existing-period-and-empty-enrollments"
          },
          {
            "from": "/images/camp-e-ritiri/iscrizioni-reali/3.placeholder.svg",
            "checkpoint": "camp-manual-enrollment-persists-with-unpaid-fee"
          },
          {
            "from": "/images/camp-e-ritiri/iscrizioni-reali/10.placeholder.svg",
            "checkpoint": "camp-public-authenticated-person-period-service"
          },
          {
            "from": "/images/camp-e-ritiri/iscrizioni-reali/8.placeholder.svg",
            "checkpoint": "camp-paid-period-locked-in-edit"
          }
        ],
        "insert_images": []
      }
    ],
    "outcome": {
      "field": "camps_enrollment_local_authored_workflow",
      "expected": {
        "manual_adult_enrollment_and_service_saved": true,
        "manual_combined_fee_unpaid": true,
        "unpaid_edit_recreates_correct_payment": true,
        "camp_fee_collected_without_external_delivery": true,
        "paid_period_edit_controls_locked": true,
        "public_camp_requires_authenticated_account": true,
        "participant_cannot_enroll_another_unrelated_person": true,
        "participant_real_public_form_saved": true,
        "participant_public_payment_unpaid": true,
        "reader_camp_enrollment_writes_denied": true,
        "removal_cancel_preserves_enrollments": true,
        "unpaid_enrollment_removal_removes_its_payment": true,
        "paid_enrollment_removal_preserves_collected_payment": true,
        "other_registrations_and_baseline_payments_preserved": true,
        "owned_camp_and_its_generated_payments_removed": true,
        "participant_transfer_reset_delegated": true
      }
    }
  },
  "course-enrollment-calendar-navigation": {
    "version": 1,
    "script": "selfhost/tests/browser/manuale/course-enrollment-calendar-navigation.mjs",
    "prefix": "images/corsi/iscritti-calendario-reali/",
    "fixture_profile": "baseline",
    "pages": [
      "docs/calendario.mdx",
      "docs/corsi.mdx"
    ],
    "sources": [
      "BE/application/impersonation.py",
      "BE/application/mixin.py",
      "BE/application/models/attendee_models.py",
      "BE/application/models/carnet_models.py",
      "BE/application/models/courses_models.py",
      "BE/application/models/invoices_models.py",
      "BE/application/models/payment_models.py",
      "BE/application/models/subscriptions_models.py",
      "BE/application/models/user_models.py",
      "BE/application/permissions_registry.py",
      "BE/application/serializers/camps_and_retreats_serializers.py",
      "BE/application/serializers/carnet_serializers.py",
      "BE/application/serializers/courses_serializers.py",
      "BE/application/serializers/invoice_serializers.py",
      "BE/application/serializers/payment_serializers.py",
      "BE/application/serializers/subscriptions_serializers.py",
      "BE/application/services/invoice_service.py",
      "BE/application/signals.py",
      "BE/application/tasks.py",
      "BE/application/urls.py",
      "BE/application/utils/attendance_utils.py",
      "BE/application/utils/camps_and_retreats_utils.py",
      "BE/application/utils/global_calendar.py",
      "BE/application/utils/subscriptions_utils.py",
      "BE/application/views/attendee_views.py",
      "BE/application/views/camp_and_retreats_views.py",
      "BE/application/views/carnet_views.py",
      "BE/application/views/course_views.py",
      "BE/application/views/google_views.py",
      "BE/application/views/instructor_views.py",
      "BE/application/views/invoice_views.py",
      "BE/application/views/payment_views.py",
      "BE/application/views/subscriptions_views.py",
      "BE/core/middleware.py",
      "UI/src/App.svelte",
      "UI/src/components/Sidebar.svelte",
      "UI/src/components/Tabs.svelte",
      "UI/src/components/buttons/ApproveButton.svelte",
      "UI/src/components/buttons/DeleteButton.svelte",
      "UI/src/components/buttons/EditButton.svelte",
      "UI/src/components/buttons/MoveToArchiveButton.svelte",
      "UI/src/components/formBuilder/preview-blocks/smart-select-input.svelte",
      "UI/src/components/inputs/DateInput.svelte",
      "UI/src/components/inputs/DateRangeCalendar.svelte",
      "UI/src/components/inputs/DateRangePicker.svelte",
      "UI/src/components/tables/BKNDatatable.svelte",
      "UI/src/routes.js",
      "UI/src/routes/accounting/payment/PaymentDrawer.svelte",
      "UI/src/routes/accounting/payment/PaymentList.svelte",
      "UI/src/routes/accounting/payment/modals/AddEditModal.svelte",
      "UI/src/routes/accounting/payment/partials/payment-overview.svelte",
      "UI/src/routes/association/Members/modals/PaymentModal.svelte",
      "UI/src/routes/association/course/CourseList.svelte",
      "UI/src/routes/association/course/CourseListArchive.svelte",
      "UI/src/routes/association/course/campsAndRetreats/CampsAndRetreatsList.svelte",
      "UI/src/routes/association/course/campsAndRetreats/modals/AddModal.svelte",
      "UI/src/routes/association/course/campsAndRetreats/overview/CampsAndRetreatsOverview.svelte",
      "UI/src/routes/association/course/campsAndRetreats/overview/CampsAndRetreatsPeriodOverview.svelte",
      "UI/src/routes/association/course/campsAndRetreats/overview/modals/AddModal.svelte",
      "UI/src/routes/association/course/campsAndRetreats/overview/modals/AddServiceModal.svelte",
      "UI/src/routes/association/course/campsAndRetreats/overview/modals/EditServiceModal.svelte",
      "UI/src/routes/association/course/campsAndRetreats/overview/modals/camps-and-retreats-periods-form.svelte",
      "UI/src/routes/association/course/campsAndRetreats/overview/modals/camps-and-retreats-services-form.svelte",
      "UI/src/routes/association/course/campsAndRetreats/overview/modals/camps-and-retreats-subscriptions-form.svelte",
      "UI/src/routes/association/course/campsAndRetreats/overview/modals/components/PeriodAndServiceSelector.svelte",
      "UI/src/routes/association/course/carnet/CarnetList.svelte",
      "UI/src/routes/association/course/carnet/add/AddCarnet.svelte",
      "UI/src/routes/association/course/carnet/add/sections/Section1.svelte",
      "UI/src/routes/association/course/carnet/add/sections/Section2.svelte",
      "UI/src/routes/association/course/carnet/detail/CarnetDetail.svelte",
      "UI/src/routes/association/course/carnet/detail/modals/AddCarnetModal.svelte",
      "UI/src/routes/association/course/carnet/detail/sections/Info.svelte",
      "UI/src/routes/association/course/carnet/detail/sections/Usage.svelte",
      "UI/src/routes/association/course/overview/OverviewCourse.svelte",
      "UI/src/routes/association/course/overview/components/Calendar.svelte",
      "UI/src/routes/association/course/overview/components/CourseNavigationTab.svelte",
      "UI/src/routes/association/course/overview/components/Registry.svelte",
      "UI/src/routes/association/course/overview/components/modals/AddCalendarEvent.svelte",
      "UI/src/routes/association/course/overview/components/modals/EditCalendarEvent.svelte",
      "UI/src/routes/association/course/overview/components/modals/MarkAttendees.svelte",
      "UI/src/routes/association/course/overview/modals/AddEditCourseSubscriptionModal.svelte",
      "UI/src/routes/association/course/overview/partials/course-subscriptions-list.svelte",
      "UI/src/routes/association/course/shared/NavigationTab.svelte",
      "UI/src/routes/calendar/Calendar.svelte",
      "UI/src/routes/calendar/SharedCalendar.svelte",
      "UI/src/routes/calendar/modals/AddCalendarEvent.svelte",
      "UI/src/routes/forms/CampsAndRetreatsForm.svelte",
      "UI/src/routes/profile/sections/Integrations.svelte",
      "UI/src/shim/form-validation.js",
      "UI/src/shim/modal.js",
      "UI/src/store/stores.js",
      "UI/src/utils/ApiMiddleware.js",
      "UI/src/utils/Functions.js",
      "UI/src/utils/Permissions.js",
      "UI/src/utils/calendarLessonNavigation.js",
      "UI/src/utils/dateValues.js",
      "UI/src/utils/eventCalendar.js",
      "UI/src/utils/userContext.js"
    ],
    "dependencies": [
      "BE/application/impersonation.py",
      "BE/application/management/commands/seed_manuale.py",
      "BE/application/mixin.py",
      "BE/application/models/attendee_models.py",
      "BE/application/models/carnet_models.py",
      "BE/application/models/courses_models.py",
      "BE/application/models/invoices_models.py",
      "BE/application/models/payment_models.py",
      "BE/application/models/subscriptions_models.py",
      "BE/application/models/user_models.py",
      "BE/application/permissions_registry.py",
      "BE/application/serializers/camps_and_retreats_serializers.py",
      "BE/application/serializers/carnet_serializers.py",
      "BE/application/serializers/courses_serializers.py",
      "BE/application/serializers/invoice_serializers.py",
      "BE/application/serializers/payment_serializers.py",
      "BE/application/serializers/subscriptions_serializers.py",
      "BE/application/services/invoice_service.py",
      "BE/application/signals.py",
      "BE/application/tasks.py",
      "BE/application/urls.py",
      "BE/application/utils/attendance_utils.py",
      "BE/application/utils/camps_and_retreats_utils.py",
      "BE/application/utils/global_calendar.py",
      "BE/application/utils/subscriptions_utils.py",
      "BE/application/views/attendee_views.py",
      "BE/application/views/camp_and_retreats_views.py",
      "BE/application/views/carnet_views.py",
      "BE/application/views/course_views.py",
      "BE/application/views/google_views.py",
      "BE/application/views/instructor_views.py",
      "BE/application/views/invoice_views.py",
      "BE/application/views/payment_views.py",
      "BE/application/views/subscriptions_views.py",
      "BE/core/middleware.py",
      "UI/src/App.svelte",
      "UI/src/components/Sidebar.svelte",
      "UI/src/components/Tabs.svelte",
      "UI/src/components/buttons/ApproveButton.svelte",
      "UI/src/components/buttons/DeleteButton.svelte",
      "UI/src/components/buttons/EditButton.svelte",
      "UI/src/components/buttons/MoveToArchiveButton.svelte",
      "UI/src/components/formBuilder/preview-blocks/smart-select-input.svelte",
      "UI/src/components/inputs/DateInput.svelte",
      "UI/src/components/inputs/DateRangeCalendar.svelte",
      "UI/src/components/inputs/DateRangePicker.svelte",
      "UI/src/components/tables/BKNDatatable.svelte",
      "UI/src/routes.js",
      "UI/src/routes/accounting/payment/PaymentDrawer.svelte",
      "UI/src/routes/accounting/payment/PaymentList.svelte",
      "UI/src/routes/accounting/payment/modals/AddEditModal.svelte",
      "UI/src/routes/accounting/payment/partials/payment-overview.svelte",
      "UI/src/routes/association/Members/modals/PaymentModal.svelte",
      "UI/src/routes/association/course/CourseList.svelte",
      "UI/src/routes/association/course/CourseListArchive.svelte",
      "UI/src/routes/association/course/campsAndRetreats/CampsAndRetreatsList.svelte",
      "UI/src/routes/association/course/campsAndRetreats/modals/AddModal.svelte",
      "UI/src/routes/association/course/campsAndRetreats/overview/CampsAndRetreatsOverview.svelte",
      "UI/src/routes/association/course/campsAndRetreats/overview/CampsAndRetreatsPeriodOverview.svelte",
      "UI/src/routes/association/course/campsAndRetreats/overview/modals/AddModal.svelte",
      "UI/src/routes/association/course/campsAndRetreats/overview/modals/AddServiceModal.svelte",
      "UI/src/routes/association/course/campsAndRetreats/overview/modals/EditServiceModal.svelte",
      "UI/src/routes/association/course/campsAndRetreats/overview/modals/camps-and-retreats-periods-form.svelte",
      "UI/src/routes/association/course/campsAndRetreats/overview/modals/camps-and-retreats-services-form.svelte",
      "UI/src/routes/association/course/campsAndRetreats/overview/modals/camps-and-retreats-subscriptions-form.svelte",
      "UI/src/routes/association/course/campsAndRetreats/overview/modals/components/PeriodAndServiceSelector.svelte",
      "UI/src/routes/association/course/carnet/CarnetList.svelte",
      "UI/src/routes/association/course/carnet/add/AddCarnet.svelte",
      "UI/src/routes/association/course/carnet/add/sections/Section1.svelte",
      "UI/src/routes/association/course/carnet/add/sections/Section2.svelte",
      "UI/src/routes/association/course/carnet/detail/CarnetDetail.svelte",
      "UI/src/routes/association/course/carnet/detail/modals/AddCarnetModal.svelte",
      "UI/src/routes/association/course/carnet/detail/sections/Info.svelte",
      "UI/src/routes/association/course/carnet/detail/sections/Usage.svelte",
      "UI/src/routes/association/course/overview/OverviewCourse.svelte",
      "UI/src/routes/association/course/overview/components/Calendar.svelte",
      "UI/src/routes/association/course/overview/components/CourseNavigationTab.svelte",
      "UI/src/routes/association/course/overview/components/Registry.svelte",
      "UI/src/routes/association/course/overview/components/modals/AddCalendarEvent.svelte",
      "UI/src/routes/association/course/overview/components/modals/EditCalendarEvent.svelte",
      "UI/src/routes/association/course/overview/components/modals/MarkAttendees.svelte",
      "UI/src/routes/association/course/overview/modals/AddEditCourseSubscriptionModal.svelte",
      "UI/src/routes/association/course/overview/partials/course-subscriptions-list.svelte",
      "UI/src/routes/association/course/shared/NavigationTab.svelte",
      "UI/src/routes/calendar/Calendar.svelte",
      "UI/src/routes/calendar/SharedCalendar.svelte",
      "UI/src/routes/calendar/modals/AddCalendarEvent.svelte",
      "UI/src/routes/forms/CampsAndRetreatsForm.svelte",
      "UI/src/routes/profile/sections/Integrations.svelte",
      "UI/src/shim/form-validation.js",
      "UI/src/shim/modal.js",
      "UI/src/store/stores.js",
      "UI/src/utils/ApiMiddleware.js",
      "UI/src/utils/Functions.js",
      "UI/src/utils/Permissions.js",
      "UI/src/utils/calendarLessonNavigation.js",
      "UI/src/utils/dateValues.js",
      "UI/src/utils/eventCalendar.js",
      "UI/src/utils/userContext.js",
      "docs/manuale/camps-personas-authored-workflows.mjs",
      "selfhost/tests/browser/manuale/course-enrollment-calendar-navigation.mjs",
      "selfhost/tests/browser/manuale/expected-denials.mjs",
      "selfhost/tests/browser/manuale/frame.mjs",
      "selfhost/tests/browser/manuale/redaction.mjs",
      "selfhost/tests/browser/manuale/scenario.mjs",
      "selfhost/tests/browser/playwright.manual.config.mjs"
    ],
    "checkpoints": [
      {
        "id": "course-current-athlete-selected-before-enrollment",
        "caption": "Giulia dell’anno corrente selezionata prima di Aggiungi."
      },
      {
        "id": "course-athlete-and-unpaid-fee-after-reload",
        "caption": "Iscrizione al corso riaperta: atleta presente e quota da120 euro in attesa."
      },
      {
        "id": "course-lesson-ready-for-publication",
        "caption": "Lezione singola compilata con nome, data e orario prima di Crea."
      },
      {
        "id": "course-published-lesson-in-register",
        "caption": "Lezione salvata nel Registro delle presenze del corso."
      },
      {
        "id": "course-direct-event-route-opens-correct-lesson",
        "caption": "Collegamento alla lezione apre il dettaglio del corso corretto."
      },
      {
        "id": "general-calendar-lesson-has-attendance-navigation",
        "caption": "Lezione nel calendario generale con il comando Gestisci Presenze."
      },
      {
        "id": "general-calendar-presence-saved-for-correct-person",
        "caption": "Modifica Presenze: Giulia selezionata e salvata."
      },
      {
        "id": "general-calendar-presence-persists-after-reload",
        "caption": "La medesima presenza riappare riaprendo la lezione dal calendario."
      },
      {
        "id": "general-calendar-reader-attendance-disabled",
        "caption": "Registro consultabile in sola lettura con selettore disabilitato."
      },
      {
        "id": "general-calendar-current-view-and-print-action",
        "caption": "Vista corrente del calendario generale e comando Stampa."
      },
      {
        "id": "course-athlete-removal-confirmation",
        "caption": "Conferma di eliminazione dell’iscrizione al corso, con avviso sulle rate non pagate."
      },
      {
        "id": "course-athlete-removed-association-and-calendar-preserved",
        "caption": "Atleta rimosso soltanto dal corso; iscrizione associativa e lezione conservate."
      }
    ],
    "sections": [
      {
        "path": "docs/corsi.mdx",
        "id": "aggiungere-o-rimuovere-atleti",
        "title": "Aggiungere o rimuovere atleti",
        "checkpoints": [
          "course-current-athlete-selected-before-enrollment",
          "course-athlete-and-unpaid-fee-after-reload",
          "course-athlete-removal-confirmation",
          "course-athlete-removed-association-and-calendar-preserved"
        ],
        "images": [
          {
            "from": "/images/corsi/iscritti-calendario-reali/1.placeholder.svg",
            "checkpoint": "course-current-athlete-selected-before-enrollment"
          },
          {
            "from": "/images/corsi/iscritti-calendario-reali/2.placeholder.svg",
            "checkpoint": "course-athlete-and-unpaid-fee-after-reload"
          },
          {
            "from": "/images/corsi/iscritti-calendario-reali/11.placeholder.svg",
            "checkpoint": "course-athlete-removal-confirmation"
          },
          {
            "from": "/images/corsi/iscritti-calendario-reali/12.placeholder.svg",
            "checkpoint": "course-athlete-removed-association-and-calendar-preserved"
          }
        ],
        "insert_images": []
      },
      {
        "path": "docs/corsi.mdx",
        "id": "pubblicare-il-calendario",
        "title": "Pubblicare il calendario",
        "checkpoints": [
          "course-lesson-ready-for-publication",
          "course-published-lesson-in-register",
          "course-direct-event-route-opens-correct-lesson"
        ],
        "images": [
          {
            "from": "/images/corsi/iscritti-calendario-reali/3.placeholder.svg",
            "checkpoint": "course-lesson-ready-for-publication"
          },
          {
            "from": "/images/corsi/iscritti-calendario-reali/4.placeholder.svg",
            "checkpoint": "course-published-lesson-in-register"
          },
          {
            "from": "/images/corsi/iscritti-calendario-reali/5.placeholder.svg",
            "checkpoint": "course-direct-event-route-opens-correct-lesson"
          }
        ],
        "insert_images": []
      },
      {
        "path": "docs/calendario.mdx",
        "id": "gestire-le-presenze-dal-calendario",
        "title": "Gestire le presenze dal calendario",
        "checkpoints": [
          "general-calendar-lesson-has-attendance-navigation",
          "general-calendar-presence-saved-for-correct-person",
          "general-calendar-presence-persists-after-reload",
          "general-calendar-reader-attendance-disabled"
        ],
        "images": [
          {
            "from": "/images/corsi/iscritti-calendario-reali/6.placeholder.svg",
            "checkpoint": "general-calendar-lesson-has-attendance-navigation"
          },
          {
            "from": "/images/corsi/iscritti-calendario-reali/7.placeholder.svg",
            "checkpoint": "general-calendar-presence-saved-for-correct-person"
          },
          {
            "from": "/images/corsi/iscritti-calendario-reali/8.placeholder.svg",
            "checkpoint": "general-calendar-presence-persists-after-reload"
          },
          {
            "from": "/images/corsi/iscritti-calendario-reali/9.placeholder.svg",
            "checkpoint": "general-calendar-reader-attendance-disabled"
          }
        ],
        "insert_images": []
      },
      {
        "path": "docs/calendario.mdx",
        "id": "stampare-il-calendario",
        "title": "Stampare il calendario",
        "checkpoints": [
          "general-calendar-current-view-and-print-action"
        ],
        "images": [
          {
            "from": "/images/corsi/iscritti-calendario-reali/10.placeholder.svg",
            "checkpoint": "general-calendar-current-view-and-print-action"
          }
        ],
        "insert_images": []
      }
    ],
    "outcome": {
      "field": "course_enrollment_calendar_navigation_authored_workflow",
      "expected": {
        "ui_enrollment_saved_with_unpaid120": true,
        "lesson_ui_save_publishes_calendar_and_register": true,
        "real_event_direct_route_opens_correct_course_lesson": true,
        "general_calendar_attendance_persists_in_same_register": true,
        "reader_attendance_write_denied_without_mutation": true,
        "actual_calendar_native_print_initiated_with_a3_landscape": true,
        "course_removal_cancel_preserves_enrollment": true,
        "course_removal_preserves_single_fee_and_association": true,
        "reader_course_enrollment_write_denied": true,
        "owned_course_cleanup_preserves_original_records": true
      }
    }
  },
  "members-profile-update": {
    "version": 1,
    "script": "selfhost/tests/browser/manuale/member-profile.mjs",
    "prefix": "images/libro-soci/scheda/",
    "fixture_profile": "baseline",
    "pages": [
      "docs/libro-soci.mdx"
    ],
    "sources": [
      "BE/application/models/subscriptions_models.py",
      "BE/application/permissions_registry.py",
      "BE/application/serializers/subscriptions_serializers.py",
      "BE/application/services/subscription_service.py",
      "BE/application/views/subscriptions_views.py",
      "UI/src/components/Sidebar.svelte",
      "UI/src/components/drawer/basic-drawer.svelte",
      "UI/src/routes.js",
      "UI/src/routes/association/Members/MembersList.svelte",
      "UI/src/routes/association/Members/detail/Detail.svelte",
      "UI/src/routes/association/Members/detail/DetailDrawer.svelte",
      "UI/src/routes/association/Members/detail/sections/Info.svelte",
      "UI/src/utils/Permissions.js"
    ],
    "dependencies": [
      "BE/application/management/commands/seed_manuale.py",
      "BE/application/models/subscriptions_models.py",
      "BE/application/permissions_registry.py",
      "BE/application/serializers/subscriptions_serializers.py",
      "BE/application/services/subscription_service.py",
      "BE/application/views/subscriptions_views.py",
      "UI/src/components/Sidebar.svelte",
      "UI/src/components/drawer/basic-drawer.svelte",
      "UI/src/routes.js",
      "UI/src/routes/association/Members/MembersList.svelte",
      "UI/src/routes/association/Members/detail/Detail.svelte",
      "UI/src/routes/association/Members/detail/DetailDrawer.svelte",
      "UI/src/routes/association/Members/detail/sections/Info.svelte",
      "UI/src/utils/Permissions.js",
      "docs/manuale/camps-personas-authored-workflows.mjs",
      "selfhost/tests/browser/manuale/frame.mjs",
      "selfhost/tests/browser/manuale/member-profile-sources.mjs",
      "selfhost/tests/browser/manuale/member-profile.mjs",
      "selfhost/tests/browser/manuale/redaction.mjs",
      "selfhost/tests/browser/manuale/scenario.mjs",
      "selfhost/tests/browser/playwright.manual.config.mjs"
    ],
    "checkpoints": [
      {
        "id": "registration-profile-and-navigation-tabs",
        "caption": "Scheda laterale della persona con sezioni di navigazione."
      },
      {
        "id": "edited-card-number-and-type-before-saving",
        "caption": "Numero tessera42 e Tipologia Tessera Aurora prima di Salva."
      },
      {
        "id": "card-details-persist-after-profile-reload",
        "caption": "Metadati della tessera conservati dopo la ricarica."
      }
    ],
    "sections": [
      {
        "path": "docs/libro-soci.mdx",
        "id": "anagrafica-smart",
        "title": "Anagrafica Smart",
        "checkpoints": [
          "registration-profile-and-navigation-tabs",
          "edited-card-number-and-type-before-saving",
          "card-details-persist-after-profile-reload"
        ],
        "images": [
          {
            "from": "/images/libro-soci/scheda/1.placeholder.svg",
            "checkpoint": "registration-profile-and-navigation-tabs"
          },
          {
            "from": "/images/libro-soci/scheda/2.placeholder.svg",
            "checkpoint": "edited-card-number-and-type-before-saving"
          },
          {
            "from": "/images/libro-soci/scheda/3.placeholder.svg",
            "checkpoint": "card-details-persist-after-profile-reload"
          }
        ],
        "insert_images": []
      }
    ],
    "outcome": {
      "field": "member_profile",
      "expected": {
        "number": "42",
        "card_type": "Tessera Aurora",
        "person_preserved": true,
        "status_preserved": true,
        "persisted_after_reload": true,
        "reader_update_status": 403,
        "denial_left_number_unchanged": true
      }
    }
  }
});
