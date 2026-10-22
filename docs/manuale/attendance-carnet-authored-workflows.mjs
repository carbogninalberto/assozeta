// Canonical workflow metadata; prose belongs to the authored MDX.
export const attendanceCarnetAuthoredWorkflows = Object.freeze({
  "attendance-carnet-manual": {
    "version": 1,
    "script": "selfhost/tests/browser/manuale/attendance-carnet.mjs",
    "prefix": "images/registro-presenze/carnet-manuale/",
    "pages": [
      "docs/registro-presenze.mdx",
      "docs/carnet.mdx",
      "tutorials/come-gestire-registro-presenze-carnet.mdx",
      "docs/corsi.mdx"
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
      "BE/application/tasks.py"
    ],
    "dependencies": [
      "selfhost/tests/browser/manuale/attendance-carnet-sources.mjs",
      "selfhost/tests/browser/manuale/scenario.mjs",
      "selfhost/tests/browser/manuale/frame.mjs",
      "selfhost/tests/browser/manuale/redaction.mjs",
      "selfhost/tests/browser/playwright.manual.config.mjs",
      "BE/application/management/commands/seed_manuale.py",
      "UI/src/routes/association/course/",
      "docs/manuale/attendance-carnet-recipes.mjs"
    ],
    "checkpoints": [
      {
        "id": "course-enrollment-selected-current-member",
        "caption": "Selezione di Giulia per l’iscrizione al corso."
      },
      {
        "id": "course-enrollment-persists-and-course-fee-created",
        "caption": "Iscrizione conservata e quota corso creata."
      },
      {
        "id": "filled-five-lesson-carnet-wizard",
        "caption": "Nuovo carnet di cinque lezioni."
      },
      {
        "id": "created-public-carnet-without-assignment-payment",
        "caption": "Carnet creato prima dell’assegnazione."
      },
      {
        "id": "make-carnet-private-in-details",
        "caption": "Visibilità privata del carnet salvata."
      },
      {
        "id": "select-member-for-new-carnet-assignment",
        "caption": "Selezione della persona per l’assegnazione."
      },
      {
        "id": "assigned-carnet-five-of-five-with-unpaid-payment",
        "caption": "Cinque lezioni disponibili e pagamento del carnet in attesa."
      },
      {
        "id": "select-enrolled-course-for-carnet",
        "caption": "Scelta del corso già frequentato."
      },
      {
        "id": "linked-course-and-five-lessons-persist",
        "caption": "Collegamento e saldo conservati dopo la riapertura."
      },
      {
        "id": "approve-carnet-payment-without-pdf-or-email",
        "caption": "Conferma dell’incasso del carnet."
      },
      {
        "id": "create-single-lesson-with-date-and-time",
        "caption": "Lezione singola con nome, data e orario."
      },
      {
        "id": "published-single-lesson-in-attendance-register",
        "caption": "Lezione pubblicata e disponibile nel registro."
      },
      {
        "id": "manual-attendance-saved-for-giulia",
        "caption": "Presenza di Giulia salvata nella lezione."
      },
      {
        "id": "carnet-consumed-four-of-five-persists-after-reload",
        "caption": "Saldo 4/5 conservato dopo la ricarica."
      },
      {
        "id": "removed-attendance-restores-five-lessons-and-clears-usage",
        "caption": "Saldo 5/5 dopo la rimozione della presenza."
      }
    ],
    "sections": [
      {
        "path": "tutorials/come-gestire-registro-presenze-carnet.mdx",
        "id": "registro-lezioni-nel-carnet", "intent": "carnet.read",
        "title": "Registro lezioni nel carnet",
        "checkpoints": [
          "manual-attendance-saved-for-giulia",
          "carnet-consumed-four-of-five-persists-after-reload",
          "removed-attendance-restores-five-lessons-and-clears-usage"
        ],
        "images": [
          {
            "from": "/images/registro-presenze/carnet-manuale/13.placeholder.svg",
            "checkpoint": "manual-attendance-saved-for-giulia"
          },
          {
            "from": "/images/registro-presenze/carnet-manuale/14.placeholder.svg",
            "checkpoint": "carnet-consumed-four-of-five-persists-after-reload"
          },
          {
            "from": "/images/registro-presenze/carnet-manuale/15.placeholder.svg",
            "checkpoint": "removed-attendance-restores-five-lessons-and-clears-usage"
          }
        ],
        "insert_images": []
      },
      {
        "path": "docs/corsi.mdx",
        "id": "corso-con-pagamento-per-lezione", "intent": "courses.enroll",
        "title": "Corso con pagamento per lezione",
        "checkpoints": [
          "course-enrollment-selected-current-member",
          "course-enrollment-persists-and-course-fee-created",
          "select-member-for-new-carnet-assignment",
          "assigned-carnet-five-of-five-with-unpaid-payment",
          "select-enrolled-course-for-carnet",
          "linked-course-and-five-lessons-persist"
        ],
        "images": [
          {
            "checkpoint": "course-enrollment-selected-current-member",
            "from": "/images/registro-presenze/carnet-manuale/1.placeholder.svg"
          },
          {
            "checkpoint": "course-enrollment-persists-and-course-fee-created",
            "from": "/images/registro-presenze/carnet-manuale/2.placeholder.svg"
          },
          {
            "checkpoint": "select-member-for-new-carnet-assignment",
            "from": "/images/registro-presenze/carnet-manuale/6.placeholder.svg"
          },
          {
            "checkpoint": "assigned-carnet-five-of-five-with-unpaid-payment",
            "from": "/images/registro-presenze/carnet-manuale/7.placeholder.svg"
          },
          {
            "checkpoint": "select-enrolled-course-for-carnet",
            "from": "/images/registro-presenze/carnet-manuale/8.placeholder.svg"
          },
          {
            "checkpoint": "linked-course-and-five-lessons-persist",
            "from": "/images/registro-presenze/carnet-manuale/9.placeholder.svg"
          }
        ],
        "insert_images": []
      },
      {
        "path": "docs/corsi.mdx",
        "id": "calendario-delle-lezioni", "intent": "courses.calendar.create",
        "title": "Calendario delle lezioni",
        "checkpoints": [
          "create-single-lesson-with-date-and-time",
          "published-single-lesson-in-attendance-register"
        ],
        "images": [
          {
            "checkpoint": "create-single-lesson-with-date-and-time",
            "from": "/images/registro-presenze/carnet-manuale/11.placeholder.svg"
          },
          {
            "checkpoint": "published-single-lesson-in-attendance-register",
            "from": "/images/registro-presenze/carnet-manuale/12.placeholder.svg"
          }
        ],
        "insert_images": []
      },
      {
        "path": "docs/corsi.mdx",
        "id": "gestire-le-lezioni", "intent": "courses.calendar.create",
        "title": "Gestire le lezioni",
        "checkpoints": [
          "create-single-lesson-with-date-and-time",
          "published-single-lesson-in-attendance-register"
        ],
        "images": [
          {
            "checkpoint": "create-single-lesson-with-date-and-time",
            "from": "/images/registro-presenze/carnet-manuale/11.placeholder.svg"
          },
          {
            "checkpoint": "published-single-lesson-in-attendance-register",
            "from": "/images/registro-presenze/carnet-manuale/12.placeholder.svg"
          }
        ],
        "insert_images": []
      },
      {
        "path": "docs/corsi.mdx",
        "id": "lezione-singola", "intent": "courses.calendar.create",
        "title": "Lezione singola",
        "checkpoints": [
          "create-single-lesson-with-date-and-time",
          "published-single-lesson-in-attendance-register"
        ],
        "images": [
          {
            "checkpoint": "create-single-lesson-with-date-and-time",
            "from": "/images/registro-presenze/carnet-manuale/11.placeholder.svg"
          },
          {
            "checkpoint": "published-single-lesson-in-attendance-register",
            "from": "/images/registro-presenze/carnet-manuale/12.placeholder.svg"
          }
        ],
        "insert_images": []
      },
      {
        "path": "tutorials/come-gestire-registro-presenze-carnet.mdx",
        "id": "consigli-per-la-gestione-dei-carnet", "intent": "carnet.read",
        "title": "Consigli per la gestione dei carnet",
        "checkpoints": [
          "select-member-for-new-carnet-assignment",
          "assigned-carnet-five-of-five-with-unpaid-payment",
          "select-enrolled-course-for-carnet",
          "linked-course-and-five-lessons-persist",
          "approve-carnet-payment-without-pdf-or-email"
        ],
        "images": [
          {
            "checkpoint": "select-member-for-new-carnet-assignment",
            "from": "/images/registro-presenze/carnet-manuale/6.placeholder.svg"
          },
          {
            "checkpoint": "assigned-carnet-five-of-five-with-unpaid-payment",
            "from": "/images/registro-presenze/carnet-manuale/7.placeholder.svg"
          },
          {
            "checkpoint": "select-enrolled-course-for-carnet",
            "from": "/images/registro-presenze/carnet-manuale/8.placeholder.svg"
          },
          {
            "checkpoint": "linked-course-and-five-lessons-persist",
            "from": "/images/registro-presenze/carnet-manuale/9.placeholder.svg"
          },
          {
            "checkpoint": "approve-carnet-payment-without-pdf-or-email",
            "from": "/images/registro-presenze/carnet-manuale/10.placeholder.svg"
          }
        ],
        "insert_images": []
      },
      {
        "path": "tutorials/come-gestire-registro-presenze-carnet.mdx",
        "id": "indice", "intent": "attendance.read",
        "title": "Indice",
        "checkpoints": [
          "assigned-carnet-five-of-five-with-unpaid-payment",
          "manual-attendance-saved-for-giulia",
          "carnet-consumed-four-of-five-persists-after-reload",
          "removed-attendance-restores-five-lessons-and-clears-usage"
        ],
        "images": [
          {
            "checkpoint": "assigned-carnet-five-of-five-with-unpaid-payment",
            "from": "/images/registro-presenze/carnet-manuale/7.placeholder.svg"
          },
          {
            "checkpoint": "manual-attendance-saved-for-giulia",
            "from": "/images/registro-presenze/carnet-manuale/13.placeholder.svg"
          },
          {
            "checkpoint": "carnet-consumed-four-of-five-persists-after-reload",
            "from": "/images/registro-presenze/carnet-manuale/14.placeholder.svg"
          },
          {
            "checkpoint": "removed-attendance-restores-five-lessons-and-clears-usage",
            "from": "/images/registro-presenze/carnet-manuale/15.placeholder.svg"
          }
        ],
        "insert_images": []
      },
      {
        "path": "docs/carnet.mdx",
        "id": "cos-e-un-carnet", "intent": "carnet.read",
        "title": "Cos'è un carnet?",
        "checkpoints": [
          "select-member-for-new-carnet-assignment",
          "assigned-carnet-five-of-five-with-unpaid-payment"
        ],
        "images": [
          {
            "checkpoint": "select-member-for-new-carnet-assignment",
            "from": "/images/registro-presenze/carnet-manuale/6.placeholder.svg"
          },
          {
            "checkpoint": "assigned-carnet-five-of-five-with-unpaid-payment",
            "from": "/images/registro-presenze/carnet-manuale/7.placeholder.svg"
          }
        ],
        "insert_images": []
      },
      {
        "path": "docs/carnet.mdx",
        "id": "come-funziona-un-carnet", "intent": "carnet.read",
        "title": "Come funziona un carnet?",
        "checkpoints": [
          "select-member-for-new-carnet-assignment",
          "select-enrolled-course-for-carnet",
          "approve-carnet-payment-without-pdf-or-email",
          "manual-attendance-saved-for-giulia",
          "carnet-consumed-four-of-five-persists-after-reload"
        ],
        "images": [
          {
            "checkpoint": "select-member-for-new-carnet-assignment",
            "from": "/images/registro-presenze/carnet-manuale/6.placeholder.svg"
          },
          {
            "checkpoint": "select-enrolled-course-for-carnet",
            "from": "/images/registro-presenze/carnet-manuale/8.placeholder.svg"
          },
          {
            "checkpoint": "approve-carnet-payment-without-pdf-or-email",
            "from": "/images/registro-presenze/carnet-manuale/10.placeholder.svg"
          },
          {
            "checkpoint": "manual-attendance-saved-for-giulia",
            "from": "/images/registro-presenze/carnet-manuale/13.placeholder.svg"
          },
          {
            "checkpoint": "carnet-consumed-four-of-five-persists-after-reload",
            "from": "/images/registro-presenze/carnet-manuale/14.placeholder.svg"
          }
        ],
        "insert_images": []
      },
      {
        "path": "docs/carnet.mdx",
        "id": "carnet-pubblici-e-privati", "intent": "carnet.update",
        "title": "Carnet pubblici e privati",
        "checkpoints": [
          "make-carnet-private-in-details"
        ],
        "images": [
          {
            "checkpoint": "make-carnet-private-in-details",
            "from": "/images/registro-presenze/carnet-manuale/5.placeholder.svg"
          }
        ],
        "insert_images": []
      },
      {
        "path": "docs/carnet.mdx",
        "id": "creare-e-gestire-un-carnet", "intent": "carnet.create",
        "title": "Creare e gestire un carnet",
        "checkpoints": [
          "filled-five-lesson-carnet-wizard",
          "created-public-carnet-without-assignment-payment",
          "make-carnet-private-in-details"
        ],
        "images": [
          {
            "checkpoint": "filled-five-lesson-carnet-wizard",
            "from": "/images/registro-presenze/carnet-manuale/3.placeholder.svg"
          },
          {
            "checkpoint": "created-public-carnet-without-assignment-payment",
            "from": "/images/registro-presenze/carnet-manuale/4.placeholder.svg"
          },
          {
            "checkpoint": "make-carnet-private-in-details",
            "from": "/images/registro-presenze/carnet-manuale/5.placeholder.svg"
          }
        ],
        "insert_images": []
      },
      {
        "path": "docs/carnet.mdx",
        "id": "assegnare-un-carnet-ad-un-atleta", "intent": "carnet.assign",
        "title": "Assegnare un carnet ad un atleta",
        "checkpoints": [
          "select-member-for-new-carnet-assignment",
          "assigned-carnet-five-of-five-with-unpaid-payment",
          "approve-carnet-payment-without-pdf-or-email"
        ],
        "images": [
          {
            "checkpoint": "select-member-for-new-carnet-assignment",
            "from": "/images/registro-presenze/carnet-manuale/6.placeholder.svg"
          },
          {
            "checkpoint": "assigned-carnet-five-of-five-with-unpaid-payment",
            "from": "/images/registro-presenze/carnet-manuale/7.placeholder.svg"
          },
          {
            "checkpoint": "approve-carnet-payment-without-pdf-or-email",
            "from": "/images/registro-presenze/carnet-manuale/10.placeholder.svg"
          }
        ],
        "insert_images": []
      },
      {
        "path": "docs/carnet.mdx",
        "id": "assegnare-un-corso-ad-un-carnet", "intent": "carnet.assign",
        "title": "Assegnare un corso ad un carnet",
        "checkpoints": [
          "course-enrollment-selected-current-member",
          "course-enrollment-persists-and-course-fee-created",
          "select-enrolled-course-for-carnet",
          "linked-course-and-five-lessons-persist"
        ],
        "images": [
          {
            "checkpoint": "course-enrollment-selected-current-member",
            "from": "/images/registro-presenze/carnet-manuale/1.placeholder.svg"
          },
          {
            "checkpoint": "course-enrollment-persists-and-course-fee-created",
            "from": "/images/registro-presenze/carnet-manuale/2.placeholder.svg"
          },
          {
            "checkpoint": "select-enrolled-course-for-carnet",
            "from": "/images/registro-presenze/carnet-manuale/8.placeholder.svg"
          },
          {
            "checkpoint": "linked-course-and-five-lessons-persist",
            "from": "/images/registro-presenze/carnet-manuale/9.placeholder.svg"
          }
        ],
        "insert_images": []
      },
      {
        "path": "docs/registro-presenze.mdx",
        "id": "introduzione", "intent": "attendance.read",
        "title": "Introduzione",
        "checkpoints": [
          "create-single-lesson-with-date-and-time",
          "published-single-lesson-in-attendance-register"
        ],
        "images": [
          {
            "checkpoint": "create-single-lesson-with-date-and-time",
            "from": "/images/registro-presenze/carnet-manuale/11.placeholder.svg"
          },
          {
            "checkpoint": "published-single-lesson-in-attendance-register",
            "from": "/images/registro-presenze/carnet-manuale/12.placeholder.svg"
          }
        ],
        "insert_images": []
      },
      {
        "path": "docs/registro-presenze.mdx",
        "id": "segnare-le-presenze-manualmente", "intent": "attendance.update",
        "title": "Segnare le presenze manualmente",
        "checkpoints": [
          "published-single-lesson-in-attendance-register",
          "manual-attendance-saved-for-giulia",
          "carnet-consumed-four-of-five-persists-after-reload"
        ],
        "images": [
          {
            "checkpoint": "published-single-lesson-in-attendance-register",
            "from": "/images/registro-presenze/carnet-manuale/12.placeholder.svg"
          },
          {
            "checkpoint": "manual-attendance-saved-for-giulia",
            "from": "/images/registro-presenze/carnet-manuale/13.placeholder.svg"
          },
          {
            "checkpoint": "carnet-consumed-four-of-five-persists-after-reload",
            "from": "/images/registro-presenze/carnet-manuale/14.placeholder.svg"
          }
        ],
        "insert_images": []
      },
      {
        "path": "tutorials/come-gestire-registro-presenze-carnet.mdx",
        "id": "segnare-le-presenze-manualmente", "intent": "attendance.update",
        "title": "Segnare le presenze manualmente",
        "checkpoints": [
          "published-single-lesson-in-attendance-register",
          "manual-attendance-saved-for-giulia",
          "carnet-consumed-four-of-five-persists-after-reload"
        ],
        "images": [
          {
            "checkpoint": "published-single-lesson-in-attendance-register",
            "from": "/images/registro-presenze/carnet-manuale/12.placeholder.svg"
          },
          {
            "checkpoint": "manual-attendance-saved-for-giulia",
            "from": "/images/registro-presenze/carnet-manuale/13.placeholder.svg"
          },
          {
            "checkpoint": "carnet-consumed-four-of-five-persists-after-reload",
            "from": "/images/registro-presenze/carnet-manuale/14.placeholder.svg"
          }
        ],
        "insert_images": []
      },
      {
        "path": "docs/registro-presenze.mdx",
        "id": "interazione-con-i-carnet", "intent": "attendance.update",
        "title": "Interazione con i carnet",
        "checkpoints": [
          "approve-carnet-payment-without-pdf-or-email",
          "manual-attendance-saved-for-giulia",
          "carnet-consumed-four-of-five-persists-after-reload",
          "removed-attendance-restores-five-lessons-and-clears-usage"
        ],
        "images": [
          {
            "checkpoint": "approve-carnet-payment-without-pdf-or-email",
            "from": "/images/registro-presenze/carnet-manuale/10.placeholder.svg"
          },
          {
            "checkpoint": "manual-attendance-saved-for-giulia",
            "from": "/images/registro-presenze/carnet-manuale/13.placeholder.svg"
          },
          {
            "checkpoint": "carnet-consumed-four-of-five-persists-after-reload",
            "from": "/images/registro-presenze/carnet-manuale/14.placeholder.svg"
          },
          {
            "checkpoint": "removed-attendance-restores-five-lessons-and-clears-usage",
            "from": "/images/registro-presenze/carnet-manuale/15.placeholder.svg"
          }
        ],
        "insert_images": []
      },
      {
        "path": "docs/registro-presenze.mdx",
        "id": "scalamento-alla-presenza", "intent": "attendance.update",
        "title": "Scalamento alla presenza",
        "checkpoints": [
          "manual-attendance-saved-for-giulia",
          "carnet-consumed-four-of-five-persists-after-reload"
        ],
        "images": [
          {
            "checkpoint": "manual-attendance-saved-for-giulia",
            "from": "/images/registro-presenze/carnet-manuale/13.placeholder.svg"
          },
          {
            "checkpoint": "carnet-consumed-four-of-five-persists-after-reload",
            "from": "/images/registro-presenze/carnet-manuale/14.placeholder.svg"
          }
        ],
        "insert_images": []
      },
      {
        "path": "tutorials/come-gestire-registro-presenze-carnet.mdx",
        "id": "scalamento-delle-lezioni", "intent": "attendance.update",
        "title": "Scalamento delle lezioni",
        "checkpoints": [
          "approve-carnet-payment-without-pdf-or-email",
          "manual-attendance-saved-for-giulia",
          "carnet-consumed-four-of-five-persists-after-reload"
        ],
        "images": [
          {
            "checkpoint": "approve-carnet-payment-without-pdf-or-email",
            "from": "/images/registro-presenze/carnet-manuale/10.placeholder.svg"
          },
          {
            "checkpoint": "manual-attendance-saved-for-giulia",
            "from": "/images/registro-presenze/carnet-manuale/13.placeholder.svg"
          },
          {
            "checkpoint": "carnet-consumed-four-of-five-persists-after-reload",
            "from": "/images/registro-presenze/carnet-manuale/14.placeholder.svg"
          }
        ],
        "insert_images": []
      },
      {
        "path": "docs/registro-presenze.mdx",
        "id": "restituzione-alla-rimozione", "intent": "attendance.remove",
        "title": "Restituzione alla rimozione",
        "checkpoints": [
          "removed-attendance-restores-five-lessons-and-clears-usage"
        ],
        "images": [
          {
            "checkpoint": "removed-attendance-restores-five-lessons-and-clears-usage",
            "from": "/images/registro-presenze/carnet-manuale/15.placeholder.svg"
          }
        ],
        "insert_images": []
      },
      {
        "path": "tutorials/come-gestire-registro-presenze-carnet.mdx",
        "id": "ripristino-delle-lezioni", "intent": "attendance.remove",
        "title": "Ripristino delle lezioni",
        "checkpoints": [
          "removed-attendance-restores-five-lessons-and-clears-usage"
        ],
        "images": [
          {
            "checkpoint": "removed-attendance-restores-five-lessons-and-clears-usage",
            "from": "/images/registro-presenze/carnet-manuale/15.placeholder.svg"
          }
        ],
        "insert_images": []
      },
      {
        "path": "tutorials/come-gestire-registro-presenze-carnet.mdx",
        "id": "correggere-una-presenza-errata", "intent": "attendance.remove",
        "title": "Correggere una presenza errata",
        "checkpoints": [
          "removed-attendance-restores-five-lessons-and-clears-usage"
        ],
        "images": [
          {
            "checkpoint": "removed-attendance-restores-five-lessons-and-clears-usage",
            "from": "/images/registro-presenze/carnet-manuale/15.placeholder.svg"
          }
        ],
        "insert_images": []
      },
      {
        "path": "tutorials/come-gestire-registro-presenze-carnet.mdx",
        "id": "gestire-le-lezioni-perse-e-le-correzioni", "intent": "attendance.remove",
        "title": "Gestire le lezioni perse e le correzioni",
        "checkpoints": [
          "removed-attendance-restores-five-lessons-and-clears-usage"
        ],
        "images": [
          {
            "checkpoint": "removed-attendance-restores-five-lessons-and-clears-usage",
            "from": "/images/registro-presenze/carnet-manuale/15.placeholder.svg"
          }
        ],
        "insert_images": []
      },
      {
        "path": "tutorials/come-gestire-registro-presenze-carnet.mdx",
        "id": "visualizzare-il-registro-presenze", "intent": "attendance.read",
        "title": "Visualizzare il registro presenze",
        "checkpoints": [
          "create-single-lesson-with-date-and-time",
          "published-single-lesson-in-attendance-register"
        ],
        "images": [
          {
            "checkpoint": "create-single-lesson-with-date-and-time",
            "from": "/images/registro-presenze/carnet-manuale/11.placeholder.svg"
          },
          {
            "checkpoint": "published-single-lesson-in-attendance-register",
            "from": "/images/registro-presenze/carnet-manuale/12.placeholder.svg"
          }
        ],
        "insert_images": []
      },
      {
        "path": "tutorials/come-gestire-registro-presenze-carnet.mdx",
        "id": "come-funzionano-i-carnet-con-le-presenze", "intent": "attendance.update",
        "title": "Come funzionano i carnet con le presenze",
        "checkpoints": [
          "select-member-for-new-carnet-assignment",
          "select-enrolled-course-for-carnet",
          "approve-carnet-payment-without-pdf-or-email",
          "published-single-lesson-in-attendance-register",
          "manual-attendance-saved-for-giulia",
          "carnet-consumed-four-of-five-persists-after-reload",
          "removed-attendance-restores-five-lessons-and-clears-usage"
        ],
        "images": [
          {
            "checkpoint": "select-member-for-new-carnet-assignment",
            "from": "/images/registro-presenze/carnet-manuale/6.placeholder.svg"
          },
          {
            "checkpoint": "select-enrolled-course-for-carnet",
            "from": "/images/registro-presenze/carnet-manuale/8.placeholder.svg"
          },
          {
            "checkpoint": "approve-carnet-payment-without-pdf-or-email",
            "from": "/images/registro-presenze/carnet-manuale/10.placeholder.svg"
          },
          {
            "checkpoint": "published-single-lesson-in-attendance-register",
            "from": "/images/registro-presenze/carnet-manuale/12.placeholder.svg"
          },
          {
            "checkpoint": "manual-attendance-saved-for-giulia",
            "from": "/images/registro-presenze/carnet-manuale/13.placeholder.svg"
          },
          {
            "checkpoint": "carnet-consumed-four-of-five-persists-after-reload",
            "from": "/images/registro-presenze/carnet-manuale/14.placeholder.svg"
          },
          {
            "checkpoint": "removed-attendance-restores-five-lessons-and-clears-usage",
            "from": "/images/registro-presenze/carnet-manuale/15.placeholder.svg"
          }
        ],
        "insert_images": []
      },
      {
        "path": "tutorials/come-gestire-registro-presenze-carnet.mdx",
        "id": "aggiungere-una-presenza-mancante", "intent": "attendance.update",
        "title": "Aggiungere una presenza mancante",
        "checkpoints": [
          "published-single-lesson-in-attendance-register",
          "manual-attendance-saved-for-giulia",
          "carnet-consumed-four-of-five-persists-after-reload"
        ],
        "images": [
          {
            "checkpoint": "published-single-lesson-in-attendance-register",
            "from": "/images/registro-presenze/carnet-manuale/12.placeholder.svg"
          },
          {
            "checkpoint": "manual-attendance-saved-for-giulia",
            "from": "/images/registro-presenze/carnet-manuale/13.placeholder.svg"
          },
          {
            "checkpoint": "carnet-consumed-four-of-five-persists-after-reload",
            "from": "/images/registro-presenze/carnet-manuale/14.placeholder.svg"
          }
        ],
        "insert_images": []
      },
      {
        "path": "tutorials/come-gestire-registro-presenze-carnet.mdx",
        "id": "controllare-le-lezioni-rimanenti", "intent": "carnet.read",
        "title": "Controllare le lezioni rimanenti",
        "checkpoints": [
          "carnet-consumed-four-of-five-persists-after-reload",
          "removed-attendance-restores-five-lessons-and-clears-usage"
        ],
        "images": [
          {
            "checkpoint": "carnet-consumed-four-of-five-persists-after-reload",
            "from": "/images/registro-presenze/carnet-manuale/14.placeholder.svg"
          },
          {
            "checkpoint": "removed-attendance-restores-five-lessons-and-clears-usage",
            "from": "/images/registro-presenze/carnet-manuale/15.placeholder.svg"
          }
        ],
        "insert_images": []
      }
    ],
    "outcome": {
      "field": "attendance_carnet_workflow",
      "expected": {
        "initial_registrations": 0,
        "final_registrations": 1,
        "initial_carnets": 0,
        "final_carnets": 1,
        "initial_assignments": 0,
        "final_assignments": 1,
        "course_fee": 120,
        "course_payment_paid": false,
        "carnet_fee": 50,
        "carnet_private": true,
        "carnet_payment_paid": true,
        "payment_count": 5,
        "link_creates_payment": false,
        "calendar_published": true,
        "lesson_count": 1,
        "lessons_initial": 5,
        "lessons_after_checkin": 4,
        "usage_after_checkin": 1,
        "lessons_after_removal": 5,
        "usage_after_removal": 0,
        "attendance_after_removal": 0,
        "persisted_after_reload": true,
        "reader_write_denials": 6,
        "denial_left_state_unchanged": true,
        "email_sent": false,
        "automatic_attendance_tested": false,
        "privacy_persisted_after_reload": true
      }
    }
  },
  "attendance-carnet-maintenance": {
    "version": 1,
    "script": "selfhost/tests/browser/manuale/attendance-carnet-maintenance.mjs",
    "prefix": "images/registro-presenze/carnet-manutenzione/",
    "pages": [
      "docs/registro-presenze.mdx",
      "docs/carnet.mdx",
      "tutorials/come-gestire-registro-presenze-carnet.mdx"
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
      "UI/src/utils/ApiMiddleware.js",
      "UI/src/store/stores.js",
      "UI/src/utils/Functions.js",
      "UI/src/shim/modal.js",
      "UI/src/shim/form-validation.js",
      "UI/src/components/widgets/BaseNumberWidget.svelte",
      "UI/src/components/formBuilder/preview-blocks/selectValue.js",
      "UI/src/routes/association/Members/MembersList.svelte",
      "UI/src/routes/association/Members/detail/DetailDrawer.svelte",
      "UI/src/routes/association/Members/detail/Detail.svelte",
      "UI/src/routes/association/Members/detail/sections/Attendance.svelte",
      "UI/src/routes/association/Members/detail/sections/Carnet.svelte",
      "UI/src/routes/association/Members/detail/sections/modals/EditModal.svelte",
      "UI/src/components/buttons/EditButton.svelte",
      "UI/src/components/buttons/RepeatOnce.svelte",
      "UI/src/components/filters/FilterSelect.svelte",
      "UI/src/components/formBuilder/preview-blocks/smart-select-input.svelte",
      "UI/src/components/tables/BKNDatatable.svelte",
      "BE/application/views/statistic_views.py",
      "BE/application/views/subscriptions_views.py",
      "BE/application/serializers/subscriptions_serializers.py"
    ],
    "dependencies": [
      "selfhost/tests/browser/manuale/attendance-carnet.mjs",
      "selfhost/tests/browser/manuale/attendance-carnet-sources.mjs",
      "selfhost/tests/browser/manuale/member-profile-sources.mjs",
      "BE/application/tests/test_attendance_day_deletion_scope.py",
      "selfhost/tests/browser/manuale/scenario.mjs",
      "selfhost/tests/browser/manuale/frame.mjs",
      "selfhost/tests/browser/manuale/redaction.mjs",
      "selfhost/tests/browser/playwright.manual.config.mjs",
      "BE/application/management/commands/seed_manuale.py"
    ],
    "checkpoints": [
      {
        "id": "personal-attendance-history-all",
        "caption": "Storico personale con una presenza effettivamente registrata."
      },
      {
        "id": "personal-attendance-history-course-filter",
        "caption": "Filtro del corso nello storico personale."
      },
      {
        "id": "personal-attendance-history-search",
        "caption": "Ricerca senza corrispondenze nello storico."
      },
      {
        "id": "carnet-balance-edit-three",
        "caption": "Modifica delle lezioni rimanenti prima della conferma."
      },
      {
        "id": "carnet-balance-three-persists",
        "caption": "Saldo manuale di tre lezioni conservato dopo il rientro."
      },
      {
        "id": "carnet-balance-five-restored",
        "caption": "Ripristino del saldo precedente attraverso lo stesso modulo."
      },
      {
        "id": "carnet-topup-confirm-cancel",
        "caption": "Conferma di ricarica prima di annullare."
      },
      {
        "id": "carnet-topup-cancelled",
        "caption": "Annullamento senza una nuova assegnazione o un nuovo pagamento."
      },
      {
        "id": "carnet-topup-confirm-new-assignment",
        "caption": "Conferma della creazione di una nuova assegnazione."
      },
      {
        "id": "carnet-topup-new-row-and-payment",
        "caption": "Due assegnazioni distinte dopo la ricarica."
      },
      {
        "id": "attendance-empty-date-delete-cancel",
        "caption": "Conferma di eliminazione di una data vuota."
      },
      {
        "id": "attendance-empty-date-deleted-after-reload",
        "caption": "Data eliminata dal registro dopo il ricaricamento."
      },
      {
        "id": "carnet-maintenance-reader-controls",
        "caption": "Collaboratore in lettura con azioni di modifica disattivate."
      }
    ],
    "sections": [
      {
        "path": "docs/registro-presenze.mdx",
        "id": "consultare-lo-storico-presenze-di-un-atleta", "intent": "attendance.read",
        "title": "Consultare lo storico presenze di un atleta",
        "checkpoints": [
          "personal-attendance-history-all",
          "personal-attendance-history-course-filter",
          "personal-attendance-history-search"
        ],
        "images": [
          {
            "checkpoint": "personal-attendance-history-all",
            "from": "/images/registro-presenze/carnet-manutenzione/1.placeholder.svg"
          },
          {
            "checkpoint": "personal-attendance-history-course-filter",
            "from": "/images/registro-presenze/carnet-manutenzione/2.placeholder.svg"
          },
          {
            "checkpoint": "personal-attendance-history-search",
            "from": "/images/registro-presenze/carnet-manutenzione/3.placeholder.svg"
          }
        ],
        "insert_images": []
      },
      {
        "path": "tutorials/come-gestire-registro-presenze-carnet.mdx",
        "id": "modificare-le-lezioni-rimanenti", "intent": "carnet.balance.update",
        "title": "Modificare le lezioni rimanenti",
        "checkpoints": [
          "carnet-balance-edit-three",
          "carnet-balance-three-persists",
          "carnet-balance-five-restored"
        ],
        "images": [
          {
            "checkpoint": "carnet-balance-edit-three",
            "from": "/images/registro-presenze/carnet-manutenzione/4.placeholder.svg"
          },
          {
            "checkpoint": "carnet-balance-three-persists",
            "from": "/images/registro-presenze/carnet-manutenzione/5.placeholder.svg"
          },
          {
            "checkpoint": "carnet-balance-five-restored",
            "from": "/images/registro-presenze/carnet-manutenzione/6.placeholder.svg"
          }
        ],
        "insert_images": []
      },
      {
        "path": "docs/carnet.mdx",
        "id": "ricaricare-un-carnet", "intent": "carnet.topup",
        "title": "Ricaricare un carnet",
        "checkpoints": [
          "carnet-topup-confirm-cancel",
          "carnet-topup-cancelled",
          "carnet-topup-confirm-new-assignment",
          "carnet-topup-new-row-and-payment"
        ],
        "images": [
          {
            "checkpoint": "carnet-topup-confirm-cancel",
            "from": "/images/registro-presenze/carnet-manutenzione/7.placeholder.svg"
          },
          {
            "checkpoint": "carnet-topup-cancelled",
            "from": "/images/registro-presenze/carnet-manutenzione/8.placeholder.svg"
          },
          {
            "checkpoint": "carnet-topup-confirm-new-assignment",
            "from": "/images/registro-presenze/carnet-manutenzione/9.placeholder.svg"
          },
          {
            "checkpoint": "carnet-topup-new-row-and-payment",
            "from": "/images/registro-presenze/carnet-manutenzione/10.placeholder.svg"
          }
        ],
        "insert_images": []
      },
      {
        "path": "tutorials/come-gestire-registro-presenze-carnet.mdx",
        "id": "assegnare-un-nuovo-carnet", "intent": "carnet.assign",
        "title": "Assegnare un nuovo carnet",
        "checkpoints": [
          "carnet-topup-confirm-cancel",
          "carnet-topup-cancelled",
          "carnet-topup-confirm-new-assignment",
          "carnet-topup-new-row-and-payment"
        ],
        "images": [
          {
            "checkpoint": "carnet-topup-confirm-cancel",
            "from": "/images/registro-presenze/carnet-manutenzione/7.placeholder.svg"
          },
          {
            "checkpoint": "carnet-topup-cancelled",
            "from": "/images/registro-presenze/carnet-manutenzione/8.placeholder.svg"
          },
          {
            "checkpoint": "carnet-topup-confirm-new-assignment",
            "from": "/images/registro-presenze/carnet-manutenzione/9.placeholder.svg"
          },
          {
            "checkpoint": "carnet-topup-new-row-and-payment",
            "from": "/images/registro-presenze/carnet-manutenzione/10.placeholder.svg"
          }
        ],
        "insert_images": []
      },
      {
        "path": "docs/registro-presenze.mdx",
        "id": "eliminare-una-lezione-dal-registro", "intent": "attendance.date.delete",
        "title": "Eliminare una lezione dal registro",
        "checkpoints": [
          "attendance-empty-date-delete-cancel",
          "attendance-empty-date-deleted-after-reload"
        ],
        "images": [
          {
            "checkpoint": "attendance-empty-date-delete-cancel",
            "from": "/images/registro-presenze/carnet-manutenzione/11.placeholder.svg"
          },
          {
            "checkpoint": "attendance-empty-date-deleted-after-reload",
            "from": "/images/registro-presenze/carnet-manutenzione/12.placeholder.svg"
          }
        ],
        "insert_images": []
      }
    ],
    "outcome": {
      "field": "attendance_carnet_maintenance",
      "expected": {
        "personal_history_saved_presences": 1,
        "personal_history_last_30_days": 1,
        "personal_history_course_filter_matches": true,
        "personal_history_search_no_match_rows": 0,
        "personal_history_correction_removes_presence": true,
        "manual_balance_persisted": 3,
        "manual_balance_total_preserved": 5,
        "manual_balance_usage_preserved": 0,
        "invalid_balances_preserve_state": true,
        "manual_balance_restored": 5,
        "cancel_topup_preserves_records": true,
        "topup_assignments": 2,
        "topup_new_balance": 5,
        "topup_courses_copied": true,
        "topup_original_assignment_preserved": true,
        "topup_new_payment_amount": 50,
        "topup_new_payment_unpaid": true,
        "topup_payment_count": 6,
        "reader_write_denials": 3,
        "denials_preserve_state": true,
        "cancel_date_delete_preserves_row": true,
        "empty_date_deleted": true,
        "date_delete_calendar_event_preserved": true,
        "empty_date_delete_balances_preserved": true
      }
    }
  }
});
