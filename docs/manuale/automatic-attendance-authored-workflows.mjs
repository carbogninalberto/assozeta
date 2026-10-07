// Prepared real UI/task contracts. Promotion requires complete runtime reports, never source inventory alone.
export const automaticAttendanceAuthoredWorkflows=Object.freeze({
  "attendance-automatic": {
    "version": 1,
    "script": "selfhost/tests/browser/manuale/attendance-automatic.mjs",
    "prefix": "images/registro-presenze/automatiche/",
    "pages": [
      "docs/registro-presenze.mdx",
      "tutorials/come-gestire-registro-presenze-carnet.mdx",
      "docs/carnet.mdx"
    ],
    "sources": [
      "UI/src/components/Sidebar.svelte",
      "UI/src/routes.js",
      "UI/src/utils/Permissions.js",
      "UI/src/routes/association/course/overview/OverviewCourse.svelte",
      "UI/src/routes/association/course/overview/components/CourseNavigationTab.svelte",
      "UI/src/routes/association/course/overview/partials/course-subscriptions-list.svelte",
      "UI/src/routes/association/course/overview/modals/AddEditCourseSubscriptionModal.svelte",
      "UI/src/routes/association/course/overview/components/Calendar.svelte",
      "UI/src/routes/association/course/overview/components/modals/AddCalendarEvent.svelte",
      "UI/src/utils/eventCalendar.js",
      "UI/src/routes/association/course/overview/components/Registry.svelte",
      "UI/src/routes/association/course/overview/components/modals/MarkAttendees.svelte",
      "UI/src/routes/association/course/carnet/CarnetList.svelte",
      "UI/src/routes/association/course/carnet/add/AddCarnet.svelte",
      "UI/src/routes/association/course/carnet/add/sections/Section1.svelte",
      "UI/src/routes/association/course/carnet/add/sections/Section2.svelte",
      "UI/src/routes/association/course/carnet/detail/CarnetDetail.svelte",
      "UI/src/routes/association/course/carnet/detail/sections/Info.svelte",
      "UI/src/routes/association/course/carnet/detail/sections/Usage.svelte",
      "UI/src/routes/association/course/carnet/detail/modals/AddCarnetModal.svelte",
      "UI/src/routes/accounting/payment/PaymentList.svelte",
      "BE/application/urls.py",
      "BE/application/permissions_registry.py",
      "BE/application/views/course_views.py",
      "BE/application/views/carnet_views.py",
      "BE/application/views/attendee_views.py",
      "BE/application/views/payment_views.py",
      "BE/application/utils/subscriptions_utils.py",
      "BE/application/utils/attendance_utils.py",
      "BE/application/serializers/courses_serializers.py",
      "BE/application/serializers/carnet_serializers.py",
      "BE/application/models/courses_models.py",
      "BE/application/models/carnet_models.py",
      "BE/application/models/attendee_models.py",
      "BE/application/models/payment_models.py",
      "BE/application/models/user_models.py",
      "BE/application/tasks.py",
      "UI/src/App.svelte",
      "UI/src/utils/userContext.js",
      "UI/src/utils/ApiMiddleware.js",
      "UI/src/store/stores.js",
      "UI/src/routes/profile/ProfileMenu.svelte",
      "UI/src/routes/profile/sections/Settings.svelte",
      "UI/src/components/inputs/BottomBarFixedSave.svelte",
      "UI/src/routes/userdashboard/Dashboard.svelte",
      "BE/application/views/profile_views.py",
      "BE/application/views/statistic_views.py",
      "BE/application/services/jwt_token_service.py",
      "BE/application/models/subscriptions_models.py",
      "BE/application/impersonation.py",
      "BE/application/impersonation_scope.py",
      "BE/core/middleware.py",
      "BE/application/permissions.py",
      "BE/core/celery.py",
      "BE/instance/models.py"
    ],
    "dependencies": [
      "selfhost/tests/browser/manuale/scenario.mjs",
      "selfhost/tests/browser/manuale/expected-denials.mjs",
      "selfhost/tests/browser/manuale/expected-denials.test.mjs",
      "selfhost/tests/browser/manuale/frame.mjs",
      "selfhost/tests/browser/manuale/redaction.mjs",
      "selfhost/tests/browser/playwright.manual.config.mjs",
      "selfhost/tests/browser/manuale/attendance-task-harness.mjs",
      "selfhost/tests/browser/manuale/attendance-carnet-sources.mjs",
      "BE/application/management/commands/seed_manuale.py",
      "BE/application/management/commands/run_manuale_attendance.py",
      "BE/application/tests/test_attendance_absence_scope.py",
      "BE/application/tests/test_automatic_attendance_results.py"
    ],
    "checkpoints": [
      {
        "id": "automatic-setting-off",
        "caption": "Opzione automatica disattivata nelle impostazioni Generali."
      },
      {
        "id": "automatic-setting-on-before-save",
        "caption": "Opzione attivata prima del salvataggio."
      },
      {
        "id": "automatic-setting-on-persists",
        "caption": "Opzione attiva conservata dopo la riapertura."
      },
      {
        "id": "automatic-setting-reader-disabled",
        "caption": "Collaboratore in lettura con impostazione disattivata alla modifica."
      },
      {
        "id": "athlete-upcoming-owned-lessons",
        "caption": "Area personale dell’atleta con le prossime lezioni di Giulia e Sara."
      },
      {
        "id": "athlete-absence-confirmation",
        "caption": "Conferma della segnalazione di assenza."
      },
      {
        "id": "athlete-absence-persists",
        "caption": "Assenza di Sara conservata dopo la riapertura."
      },
      {
        "id": "athlete-presence-correction",
        "caption": "Giulia nuovamente presente dopo la correzione della scelta."
      },
      {
        "id": "automatic-register-four-real-presences",
        "caption": "Registro dopo l’elaborazione automatica: quattro presenze."
      },
      {
        "id": "automatic-carnet-consumed-once",
        "caption": "Carnet di Giulia con una lezione consumata."
      },
      {
        "id": "automatic-absence-preserves-carnet",
        "caption": "Carnet di Sara invariato grazie all’assenza prevista."
      },
      {
        "id": "automatic-setting-disabled-persists",
        "caption": "Automazione disattivata e scelta conservata dopo la riapertura."
      }
    ],
    "sections": [
      {
        "path": "docs/registro-presenze.mdx",
        "id": "attivare-le-presenze-automatiche",
        "title": "Attivare le presenze automatiche",
        "checkpoints": [
          "automatic-setting-on-before-save",
          "automatic-setting-on-persists",
          "automatic-setting-disabled-persists"
        ],
        "images": [
          {
            "checkpoint": "automatic-setting-on-before-save",
            "from": "/images/registro-presenze/automatiche/2.placeholder.svg"
          },
          {
            "checkpoint": "automatic-setting-on-persists",
            "from": "/images/registro-presenze/automatiche/3.placeholder.svg"
          },
          {
            "checkpoint": "automatic-setting-disabled-persists",
            "from": "/images/registro-presenze/automatiche/12.placeholder.svg"
          }
        ],
        "insert_images": []
      },
      {
        "path": "tutorials/come-gestire-registro-presenze-carnet.mdx",
        "id": "come-si-attivano-le-presenze-automatiche",
        "title": "Come si attivano le presenze automatiche",
        "checkpoints": [
          "automatic-setting-on-before-save",
          "automatic-setting-on-persists",
          "automatic-setting-disabled-persists"
        ],
        "images": [
          {
            "checkpoint": "automatic-setting-on-before-save",
            "from": "/images/registro-presenze/automatiche/2.placeholder.svg"
          },
          {
            "checkpoint": "automatic-setting-on-persists",
            "from": "/images/registro-presenze/automatiche/3.placeholder.svg"
          },
          {
            "checkpoint": "automatic-setting-disabled-persists",
            "from": "/images/registro-presenze/automatiche/12.placeholder.svg"
          }
        ],
        "insert_images": []
      },
      {
        "path": "docs/registro-presenze.mdx",
        "id": "presenze-automatiche-pro-teams",
        "title": "Presenze automatiche (Pro/Teams)",
        "checkpoints": [
          "automatic-setting-on-persists",
          "athlete-absence-persists",
          "athlete-presence-correction",
          "automatic-register-four-real-presences",
          "automatic-carnet-consumed-once",
          "automatic-absence-preserves-carnet"
        ],
        "images": [
          {
            "checkpoint": "automatic-setting-on-persists",
            "from": "/images/registro-presenze/automatiche/3.placeholder.svg"
          },
          {
            "checkpoint": "athlete-absence-persists",
            "from": "/images/registro-presenze/automatiche/7.placeholder.svg"
          },
          {
            "checkpoint": "athlete-presence-correction",
            "from": "/images/registro-presenze/automatiche/8.placeholder.svg"
          },
          {
            "checkpoint": "automatic-register-four-real-presences",
            "from": "/images/registro-presenze/automatiche/9.placeholder.svg"
          },
          {
            "checkpoint": "automatic-carnet-consumed-once",
            "from": "/images/registro-presenze/automatiche/10.placeholder.svg"
          },
          {
            "checkpoint": "automatic-absence-preserves-carnet",
            "from": "/images/registro-presenze/automatiche/11.placeholder.svg"
          }
        ],
        "insert_images": []
      },
      {
        "path": "docs/registro-presenze.mdx",
        "id": "come-funziona",
        "title": "Come funziona",
        "checkpoints": [
          "automatic-setting-on-persists",
          "athlete-absence-persists",
          "athlete-presence-correction",
          "automatic-register-four-real-presences",
          "automatic-carnet-consumed-once",
          "automatic-absence-preserves-carnet"
        ],
        "images": [
          {
            "checkpoint": "automatic-setting-on-persists",
            "from": "/images/registro-presenze/automatiche/3.placeholder.svg"
          },
          {
            "checkpoint": "athlete-absence-persists",
            "from": "/images/registro-presenze/automatiche/7.placeholder.svg"
          },
          {
            "checkpoint": "athlete-presence-correction",
            "from": "/images/registro-presenze/automatiche/8.placeholder.svg"
          },
          {
            "checkpoint": "automatic-register-four-real-presences",
            "from": "/images/registro-presenze/automatiche/9.placeholder.svg"
          },
          {
            "checkpoint": "automatic-carnet-consumed-once",
            "from": "/images/registro-presenze/automatiche/10.placeholder.svg"
          },
          {
            "checkpoint": "automatic-absence-preserves-carnet",
            "from": "/images/registro-presenze/automatiche/11.placeholder.svg"
          }
        ],
        "insert_images": []
      },
      {
        "path": "tutorials/come-gestire-registro-presenze-carnet.mdx",
        "id": "presenze-automatiche-pro-teams",
        "title": "Presenze automatiche (Pro/Teams)",
        "checkpoints": [
          "automatic-setting-on-persists",
          "athlete-absence-persists",
          "athlete-presence-correction",
          "automatic-register-four-real-presences",
          "automatic-carnet-consumed-once",
          "automatic-absence-preserves-carnet"
        ],
        "images": [
          {
            "checkpoint": "automatic-setting-on-persists",
            "from": "/images/registro-presenze/automatiche/3.placeholder.svg"
          },
          {
            "checkpoint": "athlete-absence-persists",
            "from": "/images/registro-presenze/automatiche/7.placeholder.svg"
          },
          {
            "checkpoint": "athlete-presence-correction",
            "from": "/images/registro-presenze/automatiche/8.placeholder.svg"
          },
          {
            "checkpoint": "automatic-register-four-real-presences",
            "from": "/images/registro-presenze/automatiche/9.placeholder.svg"
          },
          {
            "checkpoint": "automatic-carnet-consumed-once",
            "from": "/images/registro-presenze/automatiche/10.placeholder.svg"
          },
          {
            "checkpoint": "automatic-absence-preserves-carnet",
            "from": "/images/registro-presenze/automatiche/11.placeholder.svg"
          }
        ],
        "insert_images": []
      },
      {
        "path": "tutorials/come-gestire-registro-presenze-carnet.mdx",
        "id": "come-funziona",
        "title": "Come funziona",
        "checkpoints": [
          "automatic-setting-on-persists",
          "athlete-absence-persists",
          "athlete-presence-correction",
          "automatic-register-four-real-presences",
          "automatic-carnet-consumed-once",
          "automatic-absence-preserves-carnet"
        ],
        "images": [
          {
            "checkpoint": "automatic-setting-on-persists",
            "from": "/images/registro-presenze/automatiche/3.placeholder.svg"
          },
          {
            "checkpoint": "athlete-absence-persists",
            "from": "/images/registro-presenze/automatiche/7.placeholder.svg"
          },
          {
            "checkpoint": "athlete-presence-correction",
            "from": "/images/registro-presenze/automatiche/8.placeholder.svg"
          },
          {
            "checkpoint": "automatic-register-four-real-presences",
            "from": "/images/registro-presenze/automatiche/9.placeholder.svg"
          },
          {
            "checkpoint": "automatic-carnet-consumed-once",
            "from": "/images/registro-presenze/automatiche/10.placeholder.svg"
          },
          {
            "checkpoint": "automatic-absence-preserves-carnet",
            "from": "/images/registro-presenze/automatiche/11.placeholder.svg"
          }
        ],
        "insert_images": []
      },
      {
        "path": "docs/registro-presenze.mdx",
        "id": "segnalazione-assenze-da-parte-degli-atleti",
        "title": "Segnalazione assenze da parte degli atleti",
        "checkpoints": [
          "athlete-upcoming-owned-lessons",
          "athlete-absence-confirmation",
          "athlete-absence-persists",
          "athlete-presence-correction"
        ],
        "images": [
          {
            "checkpoint": "athlete-upcoming-owned-lessons",
            "from": "/images/registro-presenze/automatiche/5.placeholder.svg"
          },
          {
            "checkpoint": "athlete-absence-confirmation",
            "from": "/images/registro-presenze/automatiche/6.placeholder.svg"
          },
          {
            "checkpoint": "athlete-absence-persists",
            "from": "/images/registro-presenze/automatiche/7.placeholder.svg"
          },
          {
            "checkpoint": "athlete-presence-correction",
            "from": "/images/registro-presenze/automatiche/8.placeholder.svg"
          }
        ],
        "insert_images": []
      },
      {
        "path": "docs/carnet.mdx",
        "id": "registro-presenze-automatico-con-carnet",
        "title": "Registro presenze automatico con carnet",
        "checkpoints": [
          "automatic-setting-on-persists",
          "athlete-absence-persists",
          "automatic-register-four-real-presences",
          "automatic-carnet-consumed-once",
          "automatic-absence-preserves-carnet"
        ],
        "images": [
          {
            "checkpoint": "automatic-setting-on-persists",
            "from": "/images/registro-presenze/automatiche/3.placeholder.svg"
          },
          {
            "checkpoint": "athlete-absence-persists",
            "from": "/images/registro-presenze/automatiche/7.placeholder.svg"
          },
          {
            "checkpoint": "automatic-register-four-real-presences",
            "from": "/images/registro-presenze/automatiche/9.placeholder.svg"
          },
          {
            "checkpoint": "automatic-carnet-consumed-once",
            "from": "/images/registro-presenze/automatiche/10.placeholder.svg"
          },
          {
            "checkpoint": "automatic-absence-preserves-carnet",
            "from": "/images/registro-presenze/automatiche/11.placeholder.svg"
          }
        ],
        "insert_images": []
      },
      {
        "path": "docs/carnet.mdx",
        "id": "blocco-per-assenza-prevista",
        "title": "Blocco per assenza prevista",
        "checkpoints": [
          "athlete-upcoming-owned-lessons",
          "athlete-absence-confirmation",
          "athlete-absence-persists",
          "athlete-presence-correction",
          "automatic-absence-preserves-carnet"
        ],
        "images": [
          {
            "checkpoint": "athlete-upcoming-owned-lessons",
            "from": "/images/registro-presenze/automatiche/5.placeholder.svg"
          },
          {
            "checkpoint": "athlete-absence-confirmation",
            "from": "/images/registro-presenze/automatiche/6.placeholder.svg"
          },
          {
            "checkpoint": "athlete-absence-persists",
            "from": "/images/registro-presenze/automatiche/7.placeholder.svg"
          },
          {
            "checkpoint": "athlete-presence-correction",
            "from": "/images/registro-presenze/automatiche/8.placeholder.svg"
          },
          {
            "checkpoint": "automatic-absence-preserves-carnet",
            "from": "/images/registro-presenze/automatiche/11.placeholder.svg"
          }
        ],
        "insert_images": []
      }
    ],
    "outcome": {
      "field": "automatic_attendance",
      "expected": {
        "settings_saved_reloaded": true,
        "reader_denied": true,
        "athlete_owned_lessons": 2,
        "athlete_absence_saved": true,
        "absence_cancel_preserves_state": true,
        "athlete_presence_corrected": true,
        "athlete_other_member_denied": true,
        "real_task_invoked": true,
        "automatic_participants": 4,
        "paid_carnet_balance": 4,
        "absence_carnet_balance": 5,
        "unpaid_carnet_unchanged": true,
        "exhausted_not_present": true,
        "disabled_not_consumed": true,
        "multiple_lowest_balance_consumed": true,
        "unpaid_lowest_blocks_paid_alternative": true,
        "no_carnet_present": true,
        "repeat_task_preserves_state": true,
        "disable_saved_reloaded": true
      }
    }
  },
  "attendance-carnet-variants": {
    "version": 1,
    "script": "selfhost/tests/browser/manuale/attendance-carnet-variants.mjs",
    "prefix": "images/registro-presenze/varianti-carnet/",
    "pages": [
      "docs/carnet.mdx",
      "docs/registro-presenze.mdx"
    ],
    "sources": [
      "UI/src/components/Sidebar.svelte",
      "UI/src/routes.js",
      "UI/src/utils/Permissions.js",
      "UI/src/routes/association/course/overview/OverviewCourse.svelte",
      "UI/src/routes/association/course/overview/components/CourseNavigationTab.svelte",
      "UI/src/routes/association/course/overview/partials/course-subscriptions-list.svelte",
      "UI/src/routes/association/course/overview/modals/AddEditCourseSubscriptionModal.svelte",
      "UI/src/routes/association/course/overview/components/Calendar.svelte",
      "UI/src/routes/association/course/overview/components/modals/AddCalendarEvent.svelte",
      "UI/src/utils/eventCalendar.js",
      "UI/src/routes/association/course/overview/components/Registry.svelte",
      "UI/src/routes/association/course/overview/components/modals/MarkAttendees.svelte",
      "UI/src/routes/association/course/carnet/CarnetList.svelte",
      "UI/src/routes/association/course/carnet/add/AddCarnet.svelte",
      "UI/src/routes/association/course/carnet/add/sections/Section1.svelte",
      "UI/src/routes/association/course/carnet/add/sections/Section2.svelte",
      "UI/src/routes/association/course/carnet/detail/CarnetDetail.svelte",
      "UI/src/routes/association/course/carnet/detail/sections/Info.svelte",
      "UI/src/routes/association/course/carnet/detail/sections/Usage.svelte",
      "UI/src/routes/association/course/carnet/detail/modals/AddCarnetModal.svelte",
      "UI/src/routes/accounting/payment/PaymentList.svelte",
      "BE/application/urls.py",
      "BE/application/permissions_registry.py",
      "BE/application/views/course_views.py",
      "BE/application/views/carnet_views.py",
      "BE/application/views/attendee_views.py",
      "BE/application/views/payment_views.py",
      "BE/application/utils/subscriptions_utils.py",
      "BE/application/utils/attendance_utils.py",
      "BE/application/serializers/courses_serializers.py",
      "BE/application/serializers/carnet_serializers.py",
      "BE/application/models/courses_models.py",
      "BE/application/models/carnet_models.py",
      "BE/application/models/attendee_models.py",
      "BE/application/models/payment_models.py",
      "BE/application/models/user_models.py",
      "BE/application/tasks.py",
      "UI/src/App.svelte",
      "UI/src/utils/userContext.js",
      "UI/src/utils/ApiMiddleware.js",
      "UI/src/store/stores.js",
      "UI/src/routes/profile/ProfileMenu.svelte",
      "UI/src/routes/profile/sections/Settings.svelte",
      "UI/src/components/inputs/BottomBarFixedSave.svelte",
      "UI/src/routes/userdashboard/Dashboard.svelte",
      "BE/application/views/profile_views.py",
      "BE/application/views/statistic_views.py",
      "BE/application/services/jwt_token_service.py",
      "BE/application/models/subscriptions_models.py",
      "BE/application/impersonation.py",
      "BE/application/impersonation_scope.py",
      "BE/core/middleware.py",
      "BE/application/permissions.py",
      "BE/core/celery.py",
      "BE/instance/models.py"
    ],
    "dependencies": [
      "selfhost/tests/browser/manuale/scenario.mjs",
      "selfhost/tests/browser/manuale/expected-denials.mjs",
      "selfhost/tests/browser/manuale/expected-denials.test.mjs",
      "selfhost/tests/browser/manuale/frame.mjs",
      "selfhost/tests/browser/manuale/redaction.mjs",
      "selfhost/tests/browser/playwright.manual.config.mjs",
      "selfhost/tests/browser/manuale/attendance-task-harness.mjs",
      "selfhost/tests/browser/manuale/attendance-carnet-sources.mjs",
      "BE/application/management/commands/seed_manuale.py",
      "BE/application/management/commands/run_manuale_attendance.py",
      "BE/application/tests/test_attendance_absence_scope.py",
      "BE/application/tests/test_automatic_attendance_results.py"
    ],
    "checkpoints": [
      {
        "id": "carnet-variants-eight-owned-enrollments",
        "caption": "Registro con gli iscritti dei diversi casi di carnet."
      },
      {
        "id": "carnet-unpaid-real-ui-refusal",
        "caption": "Rifiuto della presenza per il carnet non pagato."
      },
      {
        "id": "carnet-exhausted-real-ui-refusal",
        "caption": "Avviso del carnet esaurito e presenza non registrata."
      },
      {
        "id": "carnet-lowest-unpaid-blocks-alternative",
        "caption": "Carnet con saldo minore non pagato: l’altro pacchetto non viene consumato."
      },
      {
        "id": "carnet-multiple-real-presence",
        "caption": "Presenza salvata e consumo del saldo positivo minore."
      },
      {
        "id": "carnet-multiple-removal-restores",
        "caption": "Presenza rimossa e saldo ripristinato."
      },
      {
        "id": "carnet-disabled-presence-without-consumption",
        "caption": "Presenza salvata senza consumare il carnet disabilitato."
      },
      {
        "id": "carnet-no-package-presence",
        "caption": "Presenza salvata per la persona senza carnet."
      },
      {
        "id": "carnet-variants-no-residual-consumptions",
        "caption": "Registro riaperto dopo le correzioni, senza consumi residui."
      }
    ],
    "sections": [
      {
        "path": "docs/carnet.mdx",
        "id": "logica-di-scalamento-lezioni",
        "title": "Logica di scalamento lezioni",
        "checkpoints": [
          "carnet-variants-eight-owned-enrollments",
          "carnet-unpaid-real-ui-refusal",
          "carnet-exhausted-real-ui-refusal",
          "carnet-lowest-unpaid-blocks-alternative",
          "carnet-multiple-real-presence",
          "carnet-multiple-removal-restores",
          "carnet-disabled-presence-without-consumption",
          "carnet-no-package-presence",
          "carnet-variants-no-residual-consumptions"
        ],
        "images": [
          {
            "checkpoint": "carnet-variants-eight-owned-enrollments",
            "from": "/images/registro-presenze/varianti-carnet/1.placeholder.svg"
          },
          {
            "checkpoint": "carnet-unpaid-real-ui-refusal",
            "from": "/images/registro-presenze/varianti-carnet/2.placeholder.svg"
          },
          {
            "checkpoint": "carnet-exhausted-real-ui-refusal",
            "from": "/images/registro-presenze/varianti-carnet/3.placeholder.svg"
          },
          {
            "checkpoint": "carnet-lowest-unpaid-blocks-alternative",
            "from": "/images/registro-presenze/varianti-carnet/4.placeholder.svg"
          },
          {
            "checkpoint": "carnet-multiple-real-presence",
            "from": "/images/registro-presenze/varianti-carnet/5.placeholder.svg"
          },
          {
            "checkpoint": "carnet-multiple-removal-restores",
            "from": "/images/registro-presenze/varianti-carnet/6.placeholder.svg"
          },
          {
            "checkpoint": "carnet-disabled-presence-without-consumption",
            "from": "/images/registro-presenze/varianti-carnet/7.placeholder.svg"
          },
          {
            "checkpoint": "carnet-no-package-presence",
            "from": "/images/registro-presenze/varianti-carnet/8.placeholder.svg"
          },
          {
            "checkpoint": "carnet-variants-no-residual-consumptions",
            "from": "/images/registro-presenze/varianti-carnet/9.placeholder.svg"
          }
        ],
        "insert_images": []
      },
      {
        "path": "docs/registro-presenze.mdx",
        "id": "casi-particolari-con-i-carnet",
        "title": "Casi particolari con i carnet",
        "checkpoints": [
          "carnet-variants-eight-owned-enrollments",
          "carnet-unpaid-real-ui-refusal",
          "carnet-exhausted-real-ui-refusal",
          "carnet-lowest-unpaid-blocks-alternative",
          "carnet-multiple-real-presence",
          "carnet-multiple-removal-restores",
          "carnet-disabled-presence-without-consumption",
          "carnet-no-package-presence",
          "carnet-variants-no-residual-consumptions"
        ],
        "images": [
          {
            "checkpoint": "carnet-variants-eight-owned-enrollments",
            "from": "/images/registro-presenze/varianti-carnet/1.placeholder.svg"
          },
          {
            "checkpoint": "carnet-unpaid-real-ui-refusal",
            "from": "/images/registro-presenze/varianti-carnet/2.placeholder.svg"
          },
          {
            "checkpoint": "carnet-exhausted-real-ui-refusal",
            "from": "/images/registro-presenze/varianti-carnet/3.placeholder.svg"
          },
          {
            "checkpoint": "carnet-lowest-unpaid-blocks-alternative",
            "from": "/images/registro-presenze/varianti-carnet/4.placeholder.svg"
          },
          {
            "checkpoint": "carnet-multiple-real-presence",
            "from": "/images/registro-presenze/varianti-carnet/5.placeholder.svg"
          },
          {
            "checkpoint": "carnet-multiple-removal-restores",
            "from": "/images/registro-presenze/varianti-carnet/6.placeholder.svg"
          },
          {
            "checkpoint": "carnet-disabled-presence-without-consumption",
            "from": "/images/registro-presenze/varianti-carnet/7.placeholder.svg"
          },
          {
            "checkpoint": "carnet-no-package-presence",
            "from": "/images/registro-presenze/varianti-carnet/8.placeholder.svg"
          },
          {
            "checkpoint": "carnet-variants-no-residual-consumptions",
            "from": "/images/registro-presenze/varianti-carnet/9.placeholder.svg"
          }
        ],
        "insert_images": []
      }
    ],
    "outcome": {
      "field": "carnet_variants",
      "expected": {
        "unpaid_ui_denied": true,
        "exhausted_ui_denied": true,
        "lowest_unpaid_blocks_paid_alternative": true,
        "lowest_positive_balance_consumed": true,
        "other_assignment_unchanged": true,
        "removal_restores_original_state": true,
        "disabled_assignment_not_consumed": true,
        "no_carnet_presence_saved": true,
        "reader_denied": true,
        "reload_preserves_final_state": true,
        "receipt_or_email_requested": false
      }
    }
  },
  "dashboard-display": {
    "version": 1,
    "script": "selfhost/tests/browser/manuale/dashboard-display.mjs",
    "prefix": "images/bacheca/lettura-widget/",
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
      "BE/core/middleware.py",
      "UI/src/utils/ECharts.js"
    ],
    "dependencies": [
      "selfhost/tests/browser/manuale/scenario.mjs",
      "selfhost/tests/browser/manuale/expected-denials.mjs",
      "selfhost/tests/browser/manuale/expected-denials.test.mjs",
      "selfhost/tests/browser/manuale/frame.mjs",
      "selfhost/tests/browser/manuale/redaction.mjs",
      "selfhost/tests/browser/playwright.manual.config.mjs",
      "selfhost/tests/browser/manuale/attendance-carnet-sources.mjs",
      "BE/application/management/commands/seed_manuale.py",
      "BE/application/tests/test_attendance_absence_scope.py",
      "BE/application/tests/test_automatic_attendance_results.py",
      "selfhost/tests/browser/manuale/dashboard-sources.mjs"
    ],
    "checkpoints": [
      {
        "id": "dashboard-new-subscriptions-visible",
        "caption": "Tre nuove iscrizioni nel riepilogo."
      },
      {
        "id": "dashboard-paid-amount-visible",
        "caption": "Pagamenti incassati: cinquanta euro nell’esempio."
      },
      {
        "id": "dashboard-current-membership-states-visible",
        "caption": "Tre tesseramenti accettati e validi oggi."
      },
      {
        "id": "dashboard-active-course-rank-visible",
        "caption": "Classifica dei corsi attivi: corso senza iscritti."
      },
      {
        "id": "dashboard-income-expense-periods-visible",
        "caption": "Riepilogo con entrate e uscite dei tre periodi."
      },
      {
        "id": "dashboard-empty-todaylessons",
        "caption": "Registro presenze lezioni: nessuna lezione della giornata."
      },
      {
        "id": "dashboard-empty-expiringcarnets",
        "caption": "Carnet in esaurimento: nessuna voce nell’esempio iniziale."
      },
      {
        "id": "dashboard-empty-subscriptionstoapprove",
        "caption": "Iscrizioni da approvare: elenco vuoto nell’esempio iniziale."
      },
      {
        "id": "dashboard-empty-expiringmedicalcertificates",
        "caption": "Certificati in scadenza: elenco vuoto nell’esempio iniziale."
      },
      {
        "id": "dashboard-empty-expiredPayments",
        "caption": "Pagamenti scaduti: elenco vuoto nella selezione del widget."
      },
      {
        "id": "dashboard-empty-expiredmedicalcertificates",
        "caption": "Certificati scaduti: elenco vuoto nell’esempio iniziale."
      },
      {
        "id": "dashboard-display-initial-eight-widgets",
        "caption": "Bacheca iniziale con otto widget."
      },
      {
        "id": "dashboard-display-twelve-widgets-after-reload",
        "caption": "Tutti i dodici widget dopo il salvataggio."
      },
      {
        "id": "dashboard-display-four-available-widgets",
        "caption": "Quattro widget ancora disponibili nella finestra di scelta."
      }
    ],
    "sections": [
      {
        "path": "docs/bacheca.mdx",
        "id": "introduzione",
        "title": "Introduzione",
        "checkpoints": [
          "dashboard-display-initial-eight-widgets"
        ],
        "images": [
          {
            "checkpoint": "dashboard-display-initial-eight-widgets",
            "from": "/images/bacheca/lettura-widget/12.placeholder.svg"
          }
        ],
        "insert_images": []
      },
      {
        "path": "docs/bacheca.mdx",
        "id": "widget-disponibili",
        "title": "Widget disponibili",
        "checkpoints": [
          "dashboard-display-four-available-widgets",
          "dashboard-display-twelve-widgets-after-reload"
        ],
        "images": [
          {
            "checkpoint": "dashboard-display-four-available-widgets",
            "from": "/images/bacheca/lettura-widget/14.placeholder.svg"
          },
          {
            "checkpoint": "dashboard-display-twelve-widgets-after-reload",
            "from": "/images/bacheca/lettura-widget/13.placeholder.svg"
          }
        ],
        "insert_images": []
      },
      {
        "path": "docs/bacheca.mdx",
        "id": "soci",
        "title": "Soci",
        "checkpoints": [
          "dashboard-new-subscriptions-visible"
        ],
        "images": [
          {
            "checkpoint": "dashboard-new-subscriptions-visible",
            "from": "/images/bacheca/lettura-widget/1.placeholder.svg"
          }
        ],
        "insert_images": []
      },
      {
        "path": "docs/bacheca.mdx",
        "id": "pagamenti-incassati",
        "title": "Pagamenti incassati",
        "checkpoints": [
          "dashboard-paid-amount-visible"
        ],
        "images": [
          {
            "checkpoint": "dashboard-paid-amount-visible",
            "from": "/images/bacheca/lettura-widget/2.placeholder.svg"
          }
        ],
        "insert_images": []
      },
      {
        "path": "docs/bacheca.mdx",
        "id": "migliori-3-corsi-per-iscritti",
        "title": "Migliori 3 corsi per iscritti",
        "checkpoints": [
          "dashboard-active-course-rank-visible"
        ],
        "images": [
          {
            "checkpoint": "dashboard-active-course-rank-visible",
            "from": "/images/bacheca/lettura-widget/4.placeholder.svg"
          }
        ],
        "insert_images": []
      },
      {
        "path": "docs/bacheca.mdx",
        "id": "iscrizioni-anno-corrente",
        "title": "Iscrizioni anno corrente",
        "checkpoints": [
          "dashboard-current-membership-states-visible"
        ],
        "images": [
          {
            "checkpoint": "dashboard-current-membership-states-visible",
            "from": "/images/bacheca/lettura-widget/3.placeholder.svg"
          }
        ],
        "insert_images": []
      },
      {
        "path": "docs/bacheca.mdx",
        "id": "entrate-e-uscite",
        "title": "Entrate e uscite",
        "checkpoints": [
          "dashboard-income-expense-periods-visible"
        ],
        "images": [
          {
            "checkpoint": "dashboard-income-expense-periods-visible",
            "from": "/images/bacheca/lettura-widget/5.placeholder.svg"
          }
        ],
        "insert_images": []
      }
    ],
    "outcome": {
      "field": "dashboard_display",
      "expected": {
        "visible_widgets": 12,
        "new_subscriptions": 3,
        "paid_amount": 50,
        "accepted_current_subscriptions": 3,
        "active_courses_ranked": 1,
        "course_enrollments": 0,
        "income_each_period": 50,
        "expenses_each_period": 0,
        "empty_lists_match_real_api": 6,
        "persisted_layout_after_reload": true
      }
    }
  }
});
