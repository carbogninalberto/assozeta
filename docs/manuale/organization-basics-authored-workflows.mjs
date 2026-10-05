// Prepared complete local procedures. Promotion requires a passed real-backend run.
export const organizationBasicsAuthoredWorkflows = Object.freeze({
  "instructors-create-edit-hours": {
    "version": 1,
    "script": "selfhost/tests/browser/manuale/instructors-create-edit-hours.mjs",
    "prefix": "images/istruttori/creazione-ore/",
    "pages": [
      "docs/istruttori.mdx",
      "tutorials/come-impostare-gestire-istruttori.mdx"
    ],
    "sources": [
      "BE/application/models/user_models.py",
      "BE/application/permissions_registry.py",
      "BE/application/serializers/user_serializers.py",
      "BE/application/utils/instructors_utils.py",
      "BE/application/views/instructor_views.py",
      "BE/core/settings.py",
      "UI/src/components/InplaceTabs.svelte",
      "UI/src/components/Sidebar.svelte",
      "UI/src/components/buttons/EditButton.svelte",
      "UI/src/components/tables/BKNDatatable.svelte",
      "UI/src/routes.js",
      "UI/src/routes/association/course/instructor/InstructorList.svelte",
      "UI/src/routes/association/course/instructor/add/AddInstructor.svelte",
      "UI/src/routes/association/course/instructor/add/sections/Section1.svelte",
      "UI/src/routes/association/course/instructor/info/Instructor.svelte",
      "UI/src/routes/association/course/instructor/info/LessonsHoursCard.svelte",
      "UI/src/routes/association/course/instructor/info/modals/AddEditModal.svelte",
      "UI/src/routes/association/course/instructor/info/modals/instructorPeriod.js",
      "UI/src/routes/association/course/instructor/modals/EditModal.svelte",
      "UI/src/utils/Functions.js",
      "UI/src/utils/Permissions.js"
    ],
    "dependencies": [
      "BE/application/models/user_models.py",
      "BE/application/permissions_registry.py",
      "BE/application/serializers/user_serializers.py",
      "BE/application/utils/instructors_utils.py",
      "BE/application/views/instructor_views.py",
      "BE/core/settings.py",
      "UI/src/components/InplaceTabs.svelte",
      "UI/src/components/Sidebar.svelte",
      "UI/src/components/buttons/EditButton.svelte",
      "UI/src/components/tables/BKNDatatable.svelte",
      "UI/src/routes.js",
      "UI/src/routes/association/course/instructor/InstructorList.svelte",
      "UI/src/routes/association/course/instructor/add/AddInstructor.svelte",
      "UI/src/routes/association/course/instructor/add/sections/Section1.svelte",
      "UI/src/routes/association/course/instructor/info/Instructor.svelte",
      "UI/src/routes/association/course/instructor/info/LessonsHoursCard.svelte",
      "UI/src/routes/association/course/instructor/info/modals/AddEditModal.svelte",
      "UI/src/routes/association/course/instructor/info/modals/instructorPeriod.js",
      "UI/src/routes/association/course/instructor/modals/EditModal.svelte",
      "UI/src/utils/Functions.js",
      "UI/src/utils/Permissions.js",
      "docs/manuale/instructor-recipes.mjs",
      "docs/manuale/organization-basics-authored-workflows.mjs",
      "selfhost/tests/browser/manuale/instructor-sources.mjs",
      "selfhost/tests/browser/manuale/instructors-create-edit-hours.mjs"
    ],
    "checkpoints": [
      {
        "id": "empty-instructor-list-and-create-action",
        "caption": "Empty instructor list and create action."
      },
      {
        "id": "instructor-required-and-personal-data",
        "caption": "Instructor required and personal data."
      },
      {
        "id": "instructor-contract-default-rates-and-unlinked-account",
        "caption": "Instructor contract default rates and unlinked account."
      },
      {
        "id": "created-instructor-persists-after-reload",
        "caption": "Created instructor persists after reload."
      },
      {
        "id": "edit-instructor-default-hourly-rate",
        "caption": "Edit instructor default hourly rate."
      },
      {
        "id": "instructor-compensation-card-before-hours",
        "caption": "Instructor compensation card before hours."
      },
      {
        "id": "hourly-hours-default-rate-unpaid-and-calculated-total",
        "caption": "Hourly hours default rate unpaid and calculated total."
      },
      {
        "id": "saved-hours-unpaid-and-summary-persist-after-reload",
        "caption": "Saved hours unpaid and summary persist after reload."
      }
    ],
    "sections": [
      {
        "path": "docs/istruttori.mdx",
        "id": "aggiungere-un-istruttore",
        "title": "Aggiungere un Istruttore",
        "checkpoints": [
          "empty-instructor-list-and-create-action",
          "instructor-required-and-personal-data",
          "instructor-contract-default-rates-and-unlinked-account",
          "created-instructor-persists-after-reload"
        ],
        "images": [
          {
            "from": "/images/istruttori/creazione-ore/1.placeholder.svg",
            "checkpoint": "empty-instructor-list-and-create-action"
          },
          {
            "from": "/images/istruttori/creazione-ore/2.placeholder.svg",
            "checkpoint": "instructor-required-and-personal-data"
          },
          {
            "from": "/images/istruttori/creazione-ore/3.placeholder.svg",
            "checkpoint": "instructor-contract-default-rates-and-unlinked-account"
          },
          {
            "from": "/images/istruttori/creazione-ore/4.placeholder.svg",
            "checkpoint": "created-instructor-persists-after-reload"
          }
        ],
        "insert_images": []
      },
      {
        "path": "docs/istruttori.mdx",
        "id": "informazioni-obbligatorie",
        "title": "Informazioni obbligatorie",
        "checkpoints": [
          "instructor-required-and-personal-data"
        ],
        "images": [
          {
            "from": "/images/istruttori/creazione-ore/2.placeholder.svg",
            "checkpoint": "instructor-required-and-personal-data"
          }
        ],
        "insert_images": []
      },
      {
        "path": "docs/istruttori.mdx",
        "id": "informazioni-anagrafiche",
        "title": "Informazioni anagrafiche",
        "checkpoints": [
          "instructor-required-and-personal-data"
        ],
        "images": [
          {
            "from": "/images/istruttori/creazione-ore/2.placeholder.svg",
            "checkpoint": "instructor-required-and-personal-data"
          }
        ],
        "insert_images": []
      },
      {
        "path": "docs/istruttori.mdx",
        "id": "informazioni-contratto",
        "title": "Informazioni contratto",
        "checkpoints": [
          "instructor-contract-default-rates-and-unlinked-account"
        ],
        "images": [
          {
            "from": "/images/istruttori/creazione-ore/3.placeholder.svg",
            "checkpoint": "instructor-contract-default-rates-and-unlinked-account"
          }
        ],
        "insert_images": []
      },
      {
        "path": "docs/istruttori.mdx",
        "id": "tariffe-predefinite",
        "title": "Tariffe predefinite",
        "checkpoints": [
          "instructor-contract-default-rates-and-unlinked-account"
        ],
        "images": [
          {
            "from": "/images/istruttori/creazione-ore/3.placeholder.svg",
            "checkpoint": "instructor-contract-default-rates-and-unlinked-account"
          }
        ],
        "insert_images": []
      },
      {
        "path": "docs/istruttori.mdx",
        "id": "account-collaboratore-associato",
        "title": "Account collaboratore associato",
        "checkpoints": [
          "instructor-contract-default-rates-and-unlinked-account",
          "created-instructor-persists-after-reload"
        ],
        "images": [
          {
            "from": "/images/istruttori/creazione-ore/3.placeholder.svg",
            "checkpoint": "instructor-contract-default-rates-and-unlinked-account"
          },
          {
            "from": "/images/istruttori/creazione-ore/4.placeholder.svg",
            "checkpoint": "created-instructor-persists-after-reload"
          }
        ],
        "insert_images": []
      },
      {
        "path": "docs/istruttori.mdx",
        "id": "modificare-un-istruttore",
        "title": "Modificare un Istruttore",
        "checkpoints": [
          "edit-instructor-default-hourly-rate"
        ],
        "images": [
          {
            "from": "/images/istruttori/creazione-ore/5.placeholder.svg",
            "checkpoint": "edit-instructor-default-hourly-rate"
          }
        ],
        "insert_images": []
      },
      {
        "path": "docs/istruttori.mdx",
        "id": "la-scheda-dell-istruttore",
        "title": "La scheda dell'Istruttore",
        "checkpoints": [
          "instructor-compensation-card-before-hours",
          "saved-hours-unpaid-and-summary-persist-after-reload"
        ],
        "images": [
          {
            "from": "/images/istruttori/creazione-ore/6.placeholder.svg",
            "checkpoint": "instructor-compensation-card-before-hours"
          },
          {
            "from": "/images/istruttori/creazione-ore/8.placeholder.svg",
            "checkpoint": "saved-hours-unpaid-and-summary-persist-after-reload"
          }
        ],
        "insert_images": []
      },
      {
        "path": "docs/istruttori.mdx",
        "id": "registrare-le-ore-lavorate",
        "title": "Registrare le ore lavorate",
        "checkpoints": [
          "instructor-compensation-card-before-hours",
          "hourly-hours-default-rate-unpaid-and-calculated-total",
          "saved-hours-unpaid-and-summary-persist-after-reload"
        ],
        "images": [
          {
            "from": "/images/istruttori/creazione-ore/6.placeholder.svg",
            "checkpoint": "instructor-compensation-card-before-hours"
          },
          {
            "from": "/images/istruttori/creazione-ore/7.placeholder.svg",
            "checkpoint": "hourly-hours-default-rate-unpaid-and-calculated-total"
          },
          {
            "from": "/images/istruttori/creazione-ore/8.placeholder.svg",
            "checkpoint": "saved-hours-unpaid-and-summary-persist-after-reload"
          }
        ],
        "insert_images": []
      },
      {
        "path": "docs/istruttori.mdx",
        "id": "campi-principali",
        "title": "Campi principali",
        "checkpoints": [
          "hourly-hours-default-rate-unpaid-and-calculated-total"
        ],
        "images": [
          {
            "from": "/images/istruttori/creazione-ore/7.placeholder.svg",
            "checkpoint": "hourly-hours-default-rate-unpaid-and-calculated-total"
          }
        ],
        "insert_images": []
      },
      {
        "path": "docs/istruttori.mdx",
        "id": "tipi-di-compenso",
        "title": "Tipi di compenso",
        "checkpoints": [
          "hourly-hours-default-rate-unpaid-and-calculated-total"
        ],
        "images": [
          {
            "from": "/images/istruttori/creazione-ore/7.placeholder.svg",
            "checkpoint": "hourly-hours-default-rate-unpaid-and-calculated-total"
          }
        ],
        "insert_images": []
      },
      {
        "path": "docs/istruttori.mdx",
        "id": "compenso-orario",
        "title": "Compenso orario",
        "checkpoints": [
          "hourly-hours-default-rate-unpaid-and-calculated-total",
          "saved-hours-unpaid-and-summary-persist-after-reload"
        ],
        "images": [
          {
            "from": "/images/istruttori/creazione-ore/7.placeholder.svg",
            "checkpoint": "hourly-hours-default-rate-unpaid-and-calculated-total"
          },
          {
            "from": "/images/istruttori/creazione-ore/8.placeholder.svg",
            "checkpoint": "saved-hours-unpaid-and-summary-persist-after-reload"
          }
        ],
        "insert_images": []
      },
      {
        "path": "docs/istruttori.mdx",
        "id": "note-e-salvataggio",
        "title": "Note e salvataggio",
        "checkpoints": [
          "hourly-hours-default-rate-unpaid-and-calculated-total",
          "saved-hours-unpaid-and-summary-persist-after-reload"
        ],
        "images": [
          {
            "from": "/images/istruttori/creazione-ore/7.placeholder.svg",
            "checkpoint": "hourly-hours-default-rate-unpaid-and-calculated-total"
          },
          {
            "from": "/images/istruttori/creazione-ore/8.placeholder.svg",
            "checkpoint": "saved-hours-unpaid-and-summary-persist-after-reload"
          }
        ],
        "insert_images": []
      },
      {
        "path": "docs/istruttori.mdx",
        "id": "stato-dei-compensi",
        "title": "Stato dei compensi",
        "checkpoints": [
          "saved-hours-unpaid-and-summary-persist-after-reload"
        ],
        "images": [
          {
            "from": "/images/istruttori/creazione-ore/8.placeholder.svg",
            "checkpoint": "saved-hours-unpaid-and-summary-persist-after-reload"
          }
        ],
        "insert_images": []
      },
      {
        "path": "tutorials/come-impostare-gestire-istruttori.mdx",
        "id": "aggiungere-un-nuovo-istruttore",
        "title": "Aggiungere un nuovo istruttore",
        "checkpoints": [
          "empty-instructor-list-and-create-action",
          "instructor-required-and-personal-data",
          "instructor-contract-default-rates-and-unlinked-account",
          "created-instructor-persists-after-reload"
        ],
        "images": [
          {
            "from": "/images/istruttori/creazione-ore/1.placeholder.svg",
            "checkpoint": "empty-instructor-list-and-create-action"
          },
          {
            "from": "/images/istruttori/creazione-ore/2.placeholder.svg",
            "checkpoint": "instructor-required-and-personal-data"
          },
          {
            "from": "/images/istruttori/creazione-ore/3.placeholder.svg",
            "checkpoint": "instructor-contract-default-rates-and-unlinked-account"
          },
          {
            "from": "/images/istruttori/creazione-ore/4.placeholder.svg",
            "checkpoint": "created-instructor-persists-after-reload"
          }
        ],
        "insert_images": []
      },
      {
        "path": "tutorials/come-impostare-gestire-istruttori.mdx",
        "id": "tipologie-di-compenso",
        "title": "Tipologie di compenso",
        "checkpoints": [
          "instructor-contract-default-rates-and-unlinked-account",
          "hourly-hours-default-rate-unpaid-and-calculated-total"
        ],
        "images": [
          {
            "from": "/images/istruttori/creazione-ore/3.placeholder.svg",
            "checkpoint": "instructor-contract-default-rates-and-unlinked-account"
          },
          {
            "from": "/images/istruttori/creazione-ore/7.placeholder.svg",
            "checkpoint": "hourly-hours-default-rate-unpaid-and-calculated-total"
          }
        ],
        "insert_images": []
      },
      {
        "path": "tutorials/come-impostare-gestire-istruttori.mdx",
        "id": "registrare-le-ore-lavorate",
        "title": "Registrare le ore lavorate",
        "checkpoints": [
          "instructor-compensation-card-before-hours",
          "hourly-hours-default-rate-unpaid-and-calculated-total",
          "saved-hours-unpaid-and-summary-persist-after-reload"
        ],
        "images": [
          {
            "from": "/images/istruttori/creazione-ore/6.placeholder.svg",
            "checkpoint": "instructor-compensation-card-before-hours"
          },
          {
            "from": "/images/istruttori/creazione-ore/7.placeholder.svg",
            "checkpoint": "hourly-hours-default-rate-unpaid-and-calculated-total"
          },
          {
            "from": "/images/istruttori/creazione-ore/8.placeholder.svg",
            "checkpoint": "saved-hours-unpaid-and-summary-persist-after-reload"
          }
        ],
        "insert_images": []
      },
      {
        "path": "tutorials/come-impostare-gestire-istruttori.mdx",
        "id": "riepilogo-ore",
        "title": "Riepilogo ore",
        "checkpoints": [
          "instructor-compensation-card-before-hours",
          "saved-hours-unpaid-and-summary-persist-after-reload"
        ],
        "images": [
          {
            "from": "/images/istruttori/creazione-ore/6.placeholder.svg",
            "checkpoint": "instructor-compensation-card-before-hours"
          },
          {
            "from": "/images/istruttori/creazione-ore/8.placeholder.svg",
            "checkpoint": "saved-hours-unpaid-and-summary-persist-after-reload"
          }
        ],
        "insert_images": []
      },
      {
        "path": "tutorials/come-impostare-gestire-istruttori.mdx",
        "id": "modificare-i-dati",
        "title": "Modificare i dati",
        "checkpoints": [
          "edit-instructor-default-hourly-rate"
        ],
        "images": [
          {
            "from": "/images/istruttori/creazione-ore/5.placeholder.svg",
            "checkpoint": "edit-instructor-default-hourly-rate"
          }
        ],
        "insert_images": []
      }
    ],
    "outcome": {
      "field": "instructor_workflow",
      "expected": {
        "initial_instructors": 0,
        "final_instructors": 1,
        "owner_preserved": true,
        "associated_account": false,
        "initial_hourly_rate": 15,
        "hourly_rate": 18,
        "percentage_rate": 20,
        "hours": 3,
        "amount": 54,
        "compensation_type": "hourly",
        "paid": false,
        "payment_created": false,
        "document_created": false,
        "total_amount_to_pay": 54,
        "total_amount_paid": 0,
        "persisted_after_reload": true,
        "reader_create_status": 403,
        "reader_update_status": 403,
        "reader_hours_create_status": 403,
        "denial_left_state_unchanged": true,
        "owned_resources_cleaned": true
      }
    }
  },
  "organization-settings": {
    "version": 1,
    "script": "selfhost/tests/browser/manuale/organization-settings.mjs",
    "prefix": "images/impostazioni/organizzazione-anno/",
    "pages": [
      "docs/impostazioni.mdx",
      "faq/come-cambiare-anno-sportivo-fiscale.mdx",
      "tutorials/come-creare-moduli-iscrizione-personalizzati.mdx"
    ],
    "sources": [
      "BE/application/models/subscriptions_models.py",
      "BE/application/models/user_models.py",
      "BE/application/permissions_registry.py",
      "BE/application/serializers/auth_serializers.py",
      "BE/application/serializers/subscriptions_serializers.py",
      "BE/application/urls.py",
      "BE/application/utils/api_utils.py",
      "BE/application/views/archive_views.py",
      "BE/application/views/profile_views.py",
      "BE/application/views/search_views.py",
      "BE/application/views/subscriptions_views.py",
      "BE/docmanager/views/printing_views.py",
      "UI/src/components/Sidebar.svelte",
      "UI/src/components/formBuilder/composer-preview.svelte",
      "UI/src/components/formBuilder/composer-sidebar.svelte",
      "UI/src/components/formBuilder/inputs.js",
      "UI/src/components/formBuilder/preview-blocks/text-input.svelte",
      "UI/src/components/modals/ShareModuleSubscriptionLink.svelte",
      "UI/src/routes.js",
      "UI/src/routes/association/Members/MembersBook.svelte",
      "UI/src/routes/association/Members/detail/sections/Cloud.svelte",
      "UI/src/routes/association/Members/subscription/Template.svelte",
      "UI/src/routes/association/Members/subscription/partials/AdditionalFields.svelte",
      "UI/src/routes/association/Members/subscription/partials/ModuleSections.svelte",
      "UI/src/routes/association/Members/subscription/partials/MultipleQuotesSubscription.svelte",
      "UI/src/routes/association/Members/subscription/partials/QuoteIscrizione.svelte",
      "UI/src/routes/association/Members/subscription/partials/TypesModule.svelte",
      "UI/src/routes/association/Members/subscription/share/link-share-header.svelte",
      "UI/src/routes/association/archive/TemplatesList.svelte",
      "UI/src/routes/association/archive/detail/partials/template-form.svelte",
      "UI/src/routes/association/archive/modals/GenerateFromTemplateModal.svelte",
      "UI/src/routes/profile/ProfileMenu.svelte",
      "UI/src/routes/profile/sections/Account.svelte",
      "UI/src/routes/profile/sections/Settings.svelte",
      "UI/src/routes/subscribe/Subscribe.svelte",
      "UI/src/routes/subscribe/wizard/Step0.svelte",
      "UI/src/routes/subscribe/wizard/Step1.svelte",
      "UI/src/routes/subscribe/wizard/Step2.svelte",
      "UI/src/routes/subscribe/wizard/partials/additional-fields.svelte",
      "UI/src/utils/Permissions.js"
    ],
    "dependencies": [
      "BE/application/models/subscriptions_models.py",
      "BE/application/models/user_models.py",
      "BE/application/permissions_registry.py",
      "BE/application/serializers/auth_serializers.py",
      "BE/application/serializers/subscriptions_serializers.py",
      "BE/application/utils/api_utils.py",
      "BE/application/views/profile_views.py",
      "BE/application/views/subscriptions_views.py",
      "UI/src/components/Sidebar.svelte",
      "UI/src/routes.js",
      "UI/src/routes/profile/ProfileMenu.svelte",
      "UI/src/routes/profile/sections/Account.svelte",
      "UI/src/routes/profile/sections/Settings.svelte",
      "UI/src/utils/Permissions.js",
      "docs/manuale/organization-access-recipes.mjs",
      "docs/manuale/organization-basics-authored-workflows.mjs",
      "selfhost/tests/browser/manuale/organization-access-sources.mjs",
      "selfhost/tests/browser/manuale/organization-settings.mjs"
    ],
    "checkpoints": [
      {
        "id": "organization-info-original-address-and-owner-controls",
        "caption": "Organization info original address and owner controls."
      },
      {
        "id": "organization-address-website-abbreviation-and-sport-before-save",
        "caption": "Organization address website abbreviation and sport before save."
      },
      {
        "id": "organization-info-persists-after-authenticated-reload",
        "caption": "Organization info persists after authenticated reload."
      },
      {
        "id": "general-settings-fiscal-presets-and-separate-season",
        "caption": "General settings fiscal presets and separate season."
      },
      {
        "id": "custom-fiscal-year-starts-october-fifteen",
        "caption": "Custom fiscal year starts october fifteen."
      },
      {
        "id": "short-sport-season-and-registration-duration-before-save",
        "caption": "Short sport season and registration duration before save."
      },
      {
        "id": "saved-fiscal-and-season-settings-persist-after-reload",
        "caption": "Saved fiscal and season settings persist after reload."
      },
      {
        "id": "reader-can-consult-fiscal-season-settings-with-disabled-writes",
        "caption": "Reader can consult fiscal season settings with disabled writes."
      }
    ],
    "sections": [
      {
        "path": "docs/impostazioni.mdx",
        "id": "informazioni-dell-organizzazione",
        "title": "Informazioni dell'organizzazione",
        "checkpoints": [
          "organization-info-original-address-and-owner-controls",
          "organization-address-website-abbreviation-and-sport-before-save",
          "organization-info-persists-after-authenticated-reload"
        ],
        "images": [
          {
            "from": "/images/impostazioni/organizzazione-anno/1.placeholder.svg",
            "checkpoint": "organization-info-original-address-and-owner-controls"
          },
          {
            "from": "/images/impostazioni/organizzazione-anno/2.placeholder.svg",
            "checkpoint": "organization-address-website-abbreviation-and-sport-before-save"
          },
          {
            "from": "/images/impostazioni/organizzazione-anno/3.placeholder.svg",
            "checkpoint": "organization-info-persists-after-authenticated-reload"
          }
        ],
        "insert_images": []
      },
      {
        "path": "docs/impostazioni.mdx",
        "id": "anno-fiscale",
        "title": "Anno fiscale",
        "checkpoints": [
          "general-settings-fiscal-presets-and-separate-season",
          "custom-fiscal-year-starts-october-fifteen",
          "saved-fiscal-and-season-settings-persist-after-reload",
          "reader-can-consult-fiscal-season-settings-with-disabled-writes"
        ],
        "images": [
          {
            "from": "/images/impostazioni/organizzazione-anno/4.placeholder.svg",
            "checkpoint": "general-settings-fiscal-presets-and-separate-season"
          },
          {
            "from": "/images/impostazioni/organizzazione-anno/5.placeholder.svg",
            "checkpoint": "custom-fiscal-year-starts-october-fifteen"
          },
          {
            "from": "/images/impostazioni/organizzazione-anno/7.placeholder.svg",
            "checkpoint": "saved-fiscal-and-season-settings-persist-after-reload"
          },
          {
            "from": "/images/impostazioni/organizzazione-anno/8.placeholder.svg",
            "checkpoint": "reader-can-consult-fiscal-season-settings-with-disabled-writes"
          }
        ],
        "insert_images": []
      },
      {
        "path": "docs/impostazioni.mdx",
        "id": "stagione-sportiva",
        "title": "Stagione sportiva",
        "checkpoints": [
          "general-settings-fiscal-presets-and-separate-season",
          "short-sport-season-and-registration-duration-before-save",
          "saved-fiscal-and-season-settings-persist-after-reload"
        ],
        "images": [
          {
            "from": "/images/impostazioni/organizzazione-anno/4.placeholder.svg",
            "checkpoint": "general-settings-fiscal-presets-and-separate-season"
          },
          {
            "from": "/images/impostazioni/organizzazione-anno/6.placeholder.svg",
            "checkpoint": "short-sport-season-and-registration-duration-before-save"
          },
          {
            "from": "/images/impostazioni/organizzazione-anno/7.placeholder.svg",
            "checkpoint": "saved-fiscal-and-season-settings-persist-after-reload"
          }
        ],
        "insert_images": []
      },
      {
        "path": "docs/impostazioni.mdx",
        "id": "durata-delle-iscrizioni-e-tesseramenti",
        "title": "Durata delle iscrizioni e tesseramenti",
        "checkpoints": [
          "short-sport-season-and-registration-duration-before-save",
          "saved-fiscal-and-season-settings-persist-after-reload"
        ],
        "images": [
          {
            "from": "/images/impostazioni/organizzazione-anno/6.placeholder.svg",
            "checkpoint": "short-sport-season-and-registration-duration-before-save"
          },
          {
            "from": "/images/impostazioni/organizzazione-anno/7.placeholder.svg",
            "checkpoint": "saved-fiscal-and-season-settings-persist-after-reload"
          }
        ],
        "insert_images": []
      },
      {
        "path": "faq/come-cambiare-anno-sportivo-fiscale.mdx",
        "id": "cambiare-l-anno-sportivo-e-fiscale",
        "title": "Cambiare l'anno sportivo e fiscale",
        "checkpoints": [
          "general-settings-fiscal-presets-and-separate-season"
        ],
        "images": [
          {
            "from": "/images/impostazioni/organizzazione-anno/4.placeholder.svg",
            "checkpoint": "general-settings-fiscal-presets-and-separate-season"
          }
        ],
        "insert_images": []
      },
      {
        "path": "faq/come-cambiare-anno-sportivo-fiscale.mdx",
        "id": "qual-e-la-differenza",
        "title": "Qual è la differenza?",
        "checkpoints": [
          "general-settings-fiscal-presets-and-separate-season"
        ],
        "images": [
          {
            "from": "/images/impostazioni/organizzazione-anno/4.placeholder.svg",
            "checkpoint": "general-settings-fiscal-presets-and-separate-season"
          }
        ],
        "insert_images": []
      },
      {
        "path": "faq/come-cambiare-anno-sportivo-fiscale.mdx",
        "id": "opzioni-disponibili",
        "title": "Opzioni disponibili",
        "checkpoints": [
          "custom-fiscal-year-starts-october-fifteen",
          "short-sport-season-and-registration-duration-before-save"
        ],
        "images": [
          {
            "from": "/images/impostazioni/organizzazione-anno/5.placeholder.svg",
            "checkpoint": "custom-fiscal-year-starts-october-fifteen"
          },
          {
            "from": "/images/impostazioni/organizzazione-anno/6.placeholder.svg",
            "checkpoint": "short-sport-season-and-registration-duration-before-save"
          }
        ],
        "insert_images": []
      },
      {
        "path": "faq/come-cambiare-anno-sportivo-fiscale.mdx",
        "id": "come-cambiare-l-anno-fiscale",
        "title": "Come cambiare l'anno fiscale",
        "checkpoints": [
          "general-settings-fiscal-presets-and-separate-season",
          "custom-fiscal-year-starts-october-fifteen",
          "saved-fiscal-and-season-settings-persist-after-reload",
          "reader-can-consult-fiscal-season-settings-with-disabled-writes"
        ],
        "images": [
          {
            "from": "/images/impostazioni/organizzazione-anno/4.placeholder.svg",
            "checkpoint": "general-settings-fiscal-presets-and-separate-season"
          },
          {
            "from": "/images/impostazioni/organizzazione-anno/5.placeholder.svg",
            "checkpoint": "custom-fiscal-year-starts-october-fifteen"
          },
          {
            "from": "/images/impostazioni/organizzazione-anno/7.placeholder.svg",
            "checkpoint": "saved-fiscal-and-season-settings-persist-after-reload"
          },
          {
            "from": "/images/impostazioni/organizzazione-anno/8.placeholder.svg",
            "checkpoint": "reader-can-consult-fiscal-season-settings-with-disabled-writes"
          }
        ],
        "insert_images": []
      },
      {
        "path": "faq/come-cambiare-anno-sportivo-fiscale.mdx",
        "id": "come-cambiare-la-stagione-sportiva",
        "title": "Come cambiare la stagione sportiva",
        "checkpoints": [
          "general-settings-fiscal-presets-and-separate-season",
          "short-sport-season-and-registration-duration-before-save",
          "saved-fiscal-and-season-settings-persist-after-reload"
        ],
        "images": [
          {
            "from": "/images/impostazioni/organizzazione-anno/4.placeholder.svg",
            "checkpoint": "general-settings-fiscal-presets-and-separate-season"
          },
          {
            "from": "/images/impostazioni/organizzazione-anno/6.placeholder.svg",
            "checkpoint": "short-sport-season-and-registration-duration-before-save"
          },
          {
            "from": "/images/impostazioni/organizzazione-anno/7.placeholder.svg",
            "checkpoint": "saved-fiscal-and-season-settings-persist-after-reload"
          }
        ],
        "insert_images": []
      },
      {
        "path": "faq/come-cambiare-anno-sportivo-fiscale.mdx",
        "id": "cosa-cambia-dopo-la-modifica",
        "title": "Cosa cambia dopo la modifica",
        "checkpoints": [
          "saved-fiscal-and-season-settings-persist-after-reload"
        ],
        "images": [
          {
            "from": "/images/impostazioni/organizzazione-anno/7.placeholder.svg",
            "checkpoint": "saved-fiscal-and-season-settings-persist-after-reload"
          }
        ],
        "insert_images": []
      },
      {
        "path": "tutorials/come-creare-moduli-iscrizione-personalizzati.mdx",
        "id": "configurare-l-anno-sportivo-l-anno-fiscale-e-le-causali-per-i-pagamenti",
        "title": "Configurare l'anno sportivo, l'anno fiscale e le causali per i pagamenti",
        "checkpoints": [
          "custom-fiscal-year-starts-october-fifteen",
          "saved-fiscal-and-season-settings-persist-after-reload"
        ],
        "images": [
          {
            "from": "/images/impostazioni/organizzazione-anno/5.placeholder.svg",
            "checkpoint": "custom-fiscal-year-starts-october-fifteen"
          },
          {
            "from": "/images/impostazioni/organizzazione-anno/7.placeholder.svg",
            "checkpoint": "saved-fiscal-and-season-settings-persist-after-reload"
          }
        ],
        "insert_images": []
      },
      {
        "path": "tutorials/come-creare-moduli-iscrizione-personalizzati.mdx",
        "id": "durata-di-iscrizioni-e-tesseramenti",
        "title": "Durata di iscrizioni e Tesseramenti",
        "checkpoints": [
          "short-sport-season-and-registration-duration-before-save",
          "saved-fiscal-and-season-settings-persist-after-reload"
        ],
        "images": [
          {
            "from": "/images/impostazioni/organizzazione-anno/6.placeholder.svg",
            "checkpoint": "short-sport-season-and-registration-duration-before-save"
          },
          {
            "from": "/images/impostazioni/organizzazione-anno/7.placeholder.svg",
            "checkpoint": "saved-fiscal-and-season-settings-persist-after-reload"
          }
        ],
        "insert_images": []
      }
    ],
    "outcome": {
      "field": "organization_settings",
      "expected": {
        "organization": "Associazione Sportiva Aurora",
        "address": "Via dello Sport 14",
        "cap": "00101",
        "website": "https://aurora.example.test",
        "abbreviated": "Aurora",
        "sport": "Ginnastica",
        "fiscal_type": "4",
        "fiscal_month": 10,
        "fiscal_day": 15,
        "season_month": 9,
        "season_day": 1,
        "short_season": true,
        "season_end_month": 6,
        "season_end_day": 30,
        "subscription_duration": 3,
        "membership_duration": 3,
        "persisted_after_reload": true,
        "existing_registrations_unchanged": true,
        "reader_settings_write_status": 403,
        "denial_left_settings_unchanged": true,
        "baseline_restored": true,
        "scoped_cleanup_completed": true
      }
    }
  },
  "collaborator-permissions": {
    "version": 1,
    "script": "selfhost/tests/browser/manuale/collaborator-permissions.mjs",
    "prefix": "images/collaboratori/permessi/",
    "pages": [
      "docs/collaboratori.mdx",
      "faq/come-invitare-collaboratori.mdx"
    ],
    "sources": [
      "BE/application/models/user_models.py",
      "BE/application/permissions_registry.py",
      "BE/application/serializers/auth_serializers.py",
      "BE/application/serializers/collaborators_serializers.py",
      "BE/application/views/collaborator_views.py",
      "BE/application/views/profile_views.py",
      "BE/instance/permissions.py",
      "UI/src/components/PermissionsComponent.svelte",
      "UI/src/components/Sidebar.svelte",
      "UI/src/routes.js",
      "UI/src/routes/connectedCollaborators/CollaboratorActions.svelte",
      "UI/src/routes/connectedCollaborators/ConnectedCollaborators.svelte",
      "UI/src/routes/connectedCollaborators/modals/EditModal.svelte",
      "UI/src/routes/profile/ProfileMenu.svelte",
      "UI/src/routes/profile/sections/Account.svelte",
      "UI/src/utils/Permissions.js"
    ],
    "dependencies": [
      "BE/application/models/user_models.py",
      "BE/application/permissions_registry.py",
      "BE/application/serializers/auth_serializers.py",
      "BE/application/serializers/collaborators_serializers.py",
      "BE/application/views/collaborator_views.py",
      "BE/application/views/profile_views.py",
      "BE/instance/permissions.py",
      "UI/src/components/PermissionsComponent.svelte",
      "UI/src/components/Sidebar.svelte",
      "UI/src/routes.js",
      "UI/src/routes/connectedCollaborators/CollaboratorActions.svelte",
      "UI/src/routes/connectedCollaborators/ConnectedCollaborators.svelte",
      "UI/src/routes/connectedCollaborators/modals/EditModal.svelte",
      "UI/src/routes/profile/ProfileMenu.svelte",
      "UI/src/routes/profile/sections/Account.svelte",
      "UI/src/utils/Permissions.js",
      "docs/manuale/organization-access-recipes.mjs",
      "docs/manuale/organization-basics-authored-workflows.mjs",
      "selfhost/tests/browser/manuale/collaborator-permissions.mjs",
      "selfhost/tests/browser/manuale/organization-access-sources.mjs"
    ],
    "checkpoints": [
      {
        "id": "existing-accepted-custom-collaborator-and-edit-action",
        "caption": "Existing accepted custom collaborator and edit action."
      },
      {
        "id": "custom-reader-profile-shows-disabled-organization-fields",
        "caption": "Custom reader profile shows disabled organization fields."
      },
      {
        "id": "owner-adds-only-update-settings-to-existing-custom-permissions",
        "caption": "Owner adds only update settings to existing custom permissions."
      },
      {
        "id": "collaborator-current-profile-after-grant-and-own-name-edit",
        "caption": "Collaborator current profile after grant and own name edit."
      },
      {
        "id": "authorized-own-profile-write-persists-and-owner-organization-is-preserved",
        "caption": "Authorized own profile write persists and owner organization is preserved."
      },
      {
        "id": "revoked-update-permission-restores-reader-disabled-organization-controls",
        "caption": "Revoked update permission restores reader disabled organization controls."
      },
      {
        "id": "invitation-form-prepared-with-fictitious-email-without-dispatch",
        "caption": "Invitation form prepared with fictitious email without dispatch."
      },
      {
        "id": "full-access-role-persists-and-allows-own-profile-write",
        "caption": "Full access role persists and allows own profile write."
      },
      {
        "id": "full-access-returned-to-original-custom-permissions",
        "caption": "Full access returned to original custom permissions."
      }
    ],
    "sections": [
      {
        "path": "docs/collaboratori.mdx",
        "id": "introduzione",
        "title": "Introduzione",
        "checkpoints": [
          "existing-accepted-custom-collaborator-and-edit-action"
        ],
        "images": [
          {
            "from": "/images/collaboratori/permessi/1.placeholder.svg",
            "checkpoint": "existing-accepted-custom-collaborator-and-edit-action"
          }
        ],
        "insert_images": []
      },
      {
        "path": "docs/collaboratori.mdx",
        "id": "livelli-di-permessi",
        "title": "Livelli di permessi",
        "checkpoints": [
          "owner-adds-only-update-settings-to-existing-custom-permissions",
          "full-access-role-persists-and-allows-own-profile-write"
        ],
        "images": [
          {
            "from": "/images/collaboratori/permessi/3.placeholder.svg",
            "checkpoint": "owner-adds-only-update-settings-to-existing-custom-permissions"
          },
          {
            "from": "/images/collaboratori/permessi/8.placeholder.svg",
            "checkpoint": "full-access-role-persists-and-allows-own-profile-write"
          }
        ],
        "insert_images": []
      },
      {
        "path": "docs/collaboratori.mdx",
        "id": "accesso-completo",
        "title": "Accesso Completo",
        "checkpoints": [
          "existing-accepted-custom-collaborator-and-edit-action",
          "full-access-role-persists-and-allows-own-profile-write",
          "full-access-returned-to-original-custom-permissions"
        ],
        "images": [
          {
            "from": "/images/collaboratori/permessi/1.placeholder.svg",
            "checkpoint": "existing-accepted-custom-collaborator-and-edit-action"
          },
          {
            "from": "/images/collaboratori/permessi/8.placeholder.svg",
            "checkpoint": "full-access-role-persists-and-allows-own-profile-write"
          },
          {
            "from": "/images/collaboratori/permessi/9.placeholder.svg",
            "checkpoint": "full-access-returned-to-original-custom-permissions"
          }
        ],
        "insert_images": []
      },
      {
        "path": "docs/collaboratori.mdx",
        "id": "personalizzato",
        "title": "Personalizzato",
        "checkpoints": [
          "owner-adds-only-update-settings-to-existing-custom-permissions",
          "revoked-update-permission-restores-reader-disabled-organization-controls"
        ],
        "images": [
          {
            "from": "/images/collaboratori/permessi/3.placeholder.svg",
            "checkpoint": "owner-adds-only-update-settings-to-existing-custom-permissions"
          },
          {
            "from": "/images/collaboratori/permessi/6.placeholder.svg",
            "checkpoint": "revoked-update-permission-restores-reader-disabled-organization-controls"
          }
        ],
        "insert_images": []
      },
      {
        "path": "docs/collaboratori.mdx",
        "id": "modificare-i-permessi-di-un-collaboratore",
        "title": "Modificare i permessi di un collaboratore",
        "checkpoints": [
          "existing-accepted-custom-collaborator-and-edit-action",
          "owner-adds-only-update-settings-to-existing-custom-permissions",
          "collaborator-current-profile-after-grant-and-own-name-edit",
          "authorized-own-profile-write-persists-and-owner-organization-is-preserved",
          "revoked-update-permission-restores-reader-disabled-organization-controls"
        ],
        "images": [
          {
            "from": "/images/collaboratori/permessi/1.placeholder.svg",
            "checkpoint": "existing-accepted-custom-collaborator-and-edit-action"
          },
          {
            "from": "/images/collaboratori/permessi/3.placeholder.svg",
            "checkpoint": "owner-adds-only-update-settings-to-existing-custom-permissions"
          },
          {
            "from": "/images/collaboratori/permessi/4.placeholder.svg",
            "checkpoint": "collaborator-current-profile-after-grant-and-own-name-edit"
          },
          {
            "from": "/images/collaboratori/permessi/5.placeholder.svg",
            "checkpoint": "authorized-own-profile-write-persists-and-owner-organization-is-preserved"
          },
          {
            "from": "/images/collaboratori/permessi/6.placeholder.svg",
            "checkpoint": "revoked-update-permission-restores-reader-disabled-organization-controls"
          }
        ],
        "insert_images": []
      },
      {
        "path": "faq/come-invitare-collaboratori.mdx",
        "id": "livelli-di-accesso",
        "title": "Livelli di accesso",
        "checkpoints": [
          "owner-adds-only-update-settings-to-existing-custom-permissions",
          "full-access-role-persists-and-allows-own-profile-write"
        ],
        "images": [
          {
            "from": "/images/collaboratori/permessi/3.placeholder.svg",
            "checkpoint": "owner-adds-only-update-settings-to-existing-custom-permissions"
          },
          {
            "from": "/images/collaboratori/permessi/8.placeholder.svg",
            "checkpoint": "full-access-role-persists-and-allows-own-profile-write"
          }
        ],
        "insert_images": []
      },
      {
        "path": "faq/come-invitare-collaboratori.mdx",
        "id": "modificare-i-permessi",
        "title": "Modificare i permessi",
        "checkpoints": [
          "existing-accepted-custom-collaborator-and-edit-action",
          "owner-adds-only-update-settings-to-existing-custom-permissions",
          "collaborator-current-profile-after-grant-and-own-name-edit",
          "authorized-own-profile-write-persists-and-owner-organization-is-preserved",
          "revoked-update-permission-restores-reader-disabled-organization-controls"
        ],
        "images": [
          {
            "from": "/images/collaboratori/permessi/1.placeholder.svg",
            "checkpoint": "existing-accepted-custom-collaborator-and-edit-action"
          },
          {
            "from": "/images/collaboratori/permessi/3.placeholder.svg",
            "checkpoint": "owner-adds-only-update-settings-to-existing-custom-permissions"
          },
          {
            "from": "/images/collaboratori/permessi/4.placeholder.svg",
            "checkpoint": "collaborator-current-profile-after-grant-and-own-name-edit"
          },
          {
            "from": "/images/collaboratori/permessi/5.placeholder.svg",
            "checkpoint": "authorized-own-profile-write-persists-and-owner-organization-is-preserved"
          },
          {
            "from": "/images/collaboratori/permessi/6.placeholder.svg",
            "checkpoint": "revoked-update-permission-restores-reader-disabled-organization-controls"
          }
        ],
        "insert_images": []
      }
    ],
    "outcome": {
      "field": "collaborator_permissions",
      "expected": {
        "existing_actor": true,
        "initial_role": 3,
        "final_role": 3,
        "granted_permission": "other.settings.update",
        "only_requested_permission_added": true,
        "persisted_after_owner_reload": true,
        "reader_profile_write_before_status": 403,
        "reader_permission_write_status": 403,
        "authorized_own_profile_write_status": 200,
        "authorized_name": "Matteo",
        "authorized_own_profile_persisted": true,
        "owner_and_organization_preserved": true,
        "revoked_existing_token_write_status": 403,
        "baseline_permissions_restored": true,
        "baseline_name_restored": true,
        "invitation_prepared": true,
        "invitation_dispatches": 0,
        "invitation_records_unchanged": true,
        "full_access_role_saved_and_reopened": true,
        "full_access_real_profile_write": true,
        "full_access_returned_to_custom": true,
        "scoped_cleanup_completed": true
      }
    }
  },
  "registration-default-category": {
    "version": 1,
    "script": "selfhost/tests/browser/manuale/registration-default-category.mjs",
    "prefix": "images/impostazioni/causale-iscrizioni/",
    "pages": [
      "tutorials/come-creare-moduli-iscrizione-personalizzati.mdx"
    ],
    "sources": [
      "BE/application/models/payment_models.py",
      "BE/application/models/subscriptions_models.py",
      "BE/application/models/user_models.py",
      "BE/application/permissions_registry.py",
      "BE/application/serializers/auth_serializers.py",
      "BE/application/serializers/payment_serializers.py",
      "BE/application/serializers/subscriptions_serializers.py",
      "BE/application/serializers/user_serializers.py",
      "BE/application/urls.py",
      "BE/application/utils/api_utils.py",
      "BE/application/utils/subscriptions_utils.py",
      "BE/application/views/archive_views.py",
      "BE/application/views/payment_views.py",
      "BE/application/views/profile_views.py",
      "BE/application/views/search_views.py",
      "BE/application/views/subscriptions_views.py",
      "BE/docmanager/views/printing_views.py",
      "UI/src/components/Sidebar.svelte",
      "UI/src/components/buttons/GenerateTaxCodeButton.svelte",
      "UI/src/components/formBuilder/composer-preview.svelte",
      "UI/src/components/formBuilder/composer-sidebar.svelte",
      "UI/src/components/formBuilder/inputs.js",
      "UI/src/components/formBuilder/preview-blocks/smart-select-input.svelte",
      "UI/src/components/formBuilder/preview-blocks/text-input.svelte",
      "UI/src/components/modals/ShareModuleSubscriptionLink.svelte",
      "UI/src/components/signature/SmoothSignature.svelte",
      "UI/src/routes.js",
      "UI/src/routes/accounting/payment/PaymentList.svelte",
      "UI/src/routes/association/Members/MembersBook.svelte",
      "UI/src/routes/association/Members/MembersList.svelte",
      "UI/src/routes/association/Members/add/AddMember.svelte",
      "UI/src/routes/association/Members/add/sections/Section1.svelte",
      "UI/src/routes/association/Members/add/sections/Section2.svelte",
      "UI/src/routes/association/Members/add/sections/Section3.svelte",
      "UI/src/routes/association/Members/add/sections/Section4.svelte",
      "UI/src/routes/association/Members/add/sections/Section6.svelte",
      "UI/src/routes/association/Members/detail/sections/Cloud.svelte",
      "UI/src/routes/association/Members/subscription/Template.svelte",
      "UI/src/routes/association/Members/subscription/partials/AdditionalFields.svelte",
      "UI/src/routes/association/Members/subscription/partials/ModuleSections.svelte",
      "UI/src/routes/association/Members/subscription/partials/MultipleQuotesSubscription.svelte",
      "UI/src/routes/association/Members/subscription/partials/QuoteIscrizione.svelte",
      "UI/src/routes/association/Members/subscription/partials/TypesModule.svelte",
      "UI/src/routes/association/Members/subscription/share/link-share-header.svelte",
      "UI/src/routes/association/archive/TemplatesList.svelte",
      "UI/src/routes/association/archive/detail/partials/template-form.svelte",
      "UI/src/routes/association/archive/modals/GenerateFromTemplateModal.svelte",
      "UI/src/routes/profile/ProfileMenu.svelte",
      "UI/src/routes/profile/sections/Account.svelte",
      "UI/src/routes/profile/sections/Settings.svelte",
      "UI/src/routes/subscribe/Subscribe.svelte",
      "UI/src/routes/subscribe/wizard/Step0.svelte",
      "UI/src/routes/subscribe/wizard/Step1.svelte",
      "UI/src/routes/subscribe/wizard/Step2.svelte",
      "UI/src/routes/subscribe/wizard/partials/additional-fields.svelte",
      "UI/src/store/stores.js",
      "UI/src/utils/Permissions.js",
      "UI/src/utils/enumUtils.js"
    ],
    "dependencies": [
      "BE/application/models/payment_models.py",
      "BE/application/models/subscriptions_models.py",
      "BE/application/models/user_models.py",
      "BE/application/permissions_registry.py",
      "BE/application/serializers/auth_serializers.py",
      "BE/application/serializers/payment_serializers.py",
      "BE/application/serializers/subscriptions_serializers.py",
      "BE/application/serializers/user_serializers.py",
      "BE/application/utils/api_utils.py",
      "BE/application/utils/subscriptions_utils.py",
      "BE/application/views/payment_views.py",
      "BE/application/views/profile_views.py",
      "BE/application/views/subscriptions_views.py",
      "UI/src/components/Sidebar.svelte",
      "UI/src/components/buttons/GenerateTaxCodeButton.svelte",
      "UI/src/components/formBuilder/preview-blocks/smart-select-input.svelte",
      "UI/src/components/signature/SmoothSignature.svelte",
      "UI/src/routes.js",
      "UI/src/routes/accounting/payment/PaymentList.svelte",
      "UI/src/routes/association/Members/MembersList.svelte",
      "UI/src/routes/association/Members/add/AddMember.svelte",
      "UI/src/routes/association/Members/add/sections/Section1.svelte",
      "UI/src/routes/association/Members/add/sections/Section2.svelte",
      "UI/src/routes/association/Members/add/sections/Section3.svelte",
      "UI/src/routes/association/Members/add/sections/Section4.svelte",
      "UI/src/routes/association/Members/add/sections/Section6.svelte",
      "UI/src/routes/profile/ProfileMenu.svelte",
      "UI/src/routes/profile/sections/Account.svelte",
      "UI/src/routes/profile/sections/Settings.svelte",
      "UI/src/store/stores.js",
      "UI/src/utils/Permissions.js",
      "UI/src/utils/enumUtils.js",
      "docs/manuale/organization-basics-authored-workflows.mjs",
      "selfhost/tests/browser/manuale/member-create-flow.mjs",
      "selfhost/tests/browser/manuale/registration-default-category.mjs"
    ],
    "checkpoints": [
      {
        "id": "default-registration-category-before-edit",
        "caption": "Default registration category before edit."
      },
      {
        "id": "default-registration-category-before-save",
        "caption": "Default registration category before save."
      },
      {
        "id": "default-registration-category-persists-after-reload",
        "caption": "Default registration category persists after reload."
      },
      {
        "id": "registration-using-default-category-summary",
        "caption": "Registration using default category summary."
      },
      {
        "id": "registration-payment-with-saved-default-category",
        "caption": "Registration payment with saved default category."
      }
    ],
    "sections": [
      {
        "path": "tutorials/come-creare-moduli-iscrizione-personalizzati.mdx",
        "id": "causali-predefinite",
        "title": "Causali predefinite",
        "checkpoints": [
          "default-registration-category-before-edit",
          "default-registration-category-persists-after-reload",
          "registration-payment-with-saved-default-category"
        ],
        "images": [
          {
            "from": "/images/impostazioni/causale-iscrizioni/1.placeholder.svg",
            "checkpoint": "default-registration-category-before-edit"
          },
          {
            "from": "/images/impostazioni/causale-iscrizioni/3.placeholder.svg",
            "checkpoint": "default-registration-category-persists-after-reload"
          },
          {
            "from": "/images/impostazioni/causale-iscrizioni/5.placeholder.svg",
            "checkpoint": "registration-payment-with-saved-default-category"
          }
        ],
        "insert_images": []
      }
    ],
    "outcome": {
      "field": "registration_default_category_authored_workflow",
      "expected": {
        "default_category_saved_and_reopened": true,
        "other_settings_preserved": true,
        "existing_payments_preserved_before_signup": true,
        "new_registration_has_unpaid_quota": true,
        "new_quota_uses_saved_default_category": true,
        "reader_settings_write_denied": true,
        "baseline_other_records_preserved": true,
        "settings_and_created_registration_payment_restored": true
      }
    }
  },
  "organization-document-templates": {
    "version": 1,
    "script": "selfhost/tests/browser/manuale/organization-document-templates.mjs",
    "prefix": "images/archivio/modelli-reali/",
    "pages": [
      "tutorials/come-creare-moduli-iscrizione-personalizzati.mdx"
    ],
    "sources": [
      "BE/application/models/subscriptions_models.py",
      "BE/application/models/user_models.py",
      "BE/application/models/utils.py",
      "BE/application/permissions_registry.py",
      "BE/application/serializers/auth_serializers.py",
      "BE/application/serializers/subscriptions_serializers.py",
      "BE/application/serializers/user_serializers.py",
      "BE/application/services/subscription_service.py",
      "BE/application/urls.py",
      "BE/application/utils/printing.py",
      "BE/application/views/archive_views.py",
      "BE/application/views/profile_views.py",
      "BE/application/views/search_views.py",
      "BE/application/views/subscriptions_views.py",
      "BE/docmanager/models.py",
      "BE/docmanager/urls.py",
      "BE/docmanager/views/document_view.py",
      "BE/docmanager/views/printing_views.py",
      "BE/templates/document/application/model.html",
      "UI/src/components/Sidebar.svelte",
      "UI/src/components/drawer/basic-drawer.svelte",
      "UI/src/components/formBuilder/composer-preview.svelte",
      "UI/src/components/formBuilder/composer-sidebar.svelte",
      "UI/src/components/formBuilder/inputs.js",
      "UI/src/components/formBuilder/preview-blocks/checkbox-select.svelte",
      "UI/src/components/formBuilder/preview-blocks/smart-select-input.svelte",
      "UI/src/components/formBuilder/preview-blocks/text-input.svelte",
      "UI/src/components/inputs/BottomBarFixedSave.svelte",
      "UI/src/components/inputs/MentionList.svelte",
      "UI/src/components/inputs/TipTapEditor.svelte",
      "UI/src/components/inputs/mentionSuggestions.js",
      "UI/src/components/inputs/suggestion.js",
      "UI/src/components/modals/ShareModuleSubscriptionLink.svelte",
      "UI/src/routes.js",
      "UI/src/routes/association/Members/MembersBook.svelte",
      "UI/src/routes/association/Members/MembersList.svelte",
      "UI/src/routes/association/Members/detail/Detail.svelte",
      "UI/src/routes/association/Members/detail/DetailDrawer.svelte",
      "UI/src/routes/association/Members/detail/sections/Cloud.svelte",
      "UI/src/routes/association/Members/detail/sections/Info.svelte",
      "UI/src/routes/association/Members/subscription/Template.svelte",
      "UI/src/routes/association/Members/subscription/partials/AdditionalFields.svelte",
      "UI/src/routes/association/Members/subscription/partials/ModuleSections.svelte",
      "UI/src/routes/association/Members/subscription/partials/MultipleQuotesSubscription.svelte",
      "UI/src/routes/association/Members/subscription/partials/QuoteIscrizione.svelte",
      "UI/src/routes/association/Members/subscription/partials/TypesModule.svelte",
      "UI/src/routes/association/Members/subscription/share/link-share-header.svelte",
      "UI/src/routes/association/archive/Archive.svelte",
      "UI/src/routes/association/archive/TemplatesList.svelte",
      "UI/src/routes/association/archive/detail/AddTemplatesDrawer.svelte",
      "UI/src/routes/association/archive/detail/Detail.svelte",
      "UI/src/routes/association/archive/detail/TemplatesDrawer.svelte",
      "UI/src/routes/association/archive/detail/partials/template-form.svelte",
      "UI/src/routes/association/archive/modals/GenerateFromTemplateModal.svelte",
      "UI/src/routes/association/archive/shared/NavigationTab.svelte",
      "UI/src/routes/profile/sections/Settings.svelte",
      "UI/src/routes/subscribe/Subscribe.svelte",
      "UI/src/routes/subscribe/wizard/Step0.svelte",
      "UI/src/routes/subscribe/wizard/Step1.svelte",
      "UI/src/routes/subscribe/wizard/Step2.svelte",
      "UI/src/routes/subscribe/wizard/partials/additional-fields.svelte",
      "UI/src/utils/Permissions.js"
    ],
    "dependencies": [
      "BE/application/models/subscriptions_models.py",
      "BE/application/models/user_models.py",
      "BE/application/models/utils.py",
      "BE/application/permissions_registry.py",
      "BE/application/serializers/auth_serializers.py",
      "BE/application/serializers/subscriptions_serializers.py",
      "BE/application/serializers/user_serializers.py",
      "BE/application/services/subscription_service.py",
      "BE/application/urls.py",
      "BE/application/utils/printing.py",
      "BE/application/views/archive_views.py",
      "BE/application/views/profile_views.py",
      "BE/application/views/search_views.py",
      "BE/application/views/subscriptions_views.py",
      "BE/docmanager/models.py",
      "BE/docmanager/urls.py",
      "BE/docmanager/views/document_view.py",
      "BE/docmanager/views/printing_views.py",
      "BE/templates/document/application/model.html",
      "UI/src/components/Sidebar.svelte",
      "UI/src/components/drawer/basic-drawer.svelte",
      "UI/src/components/formBuilder/composer-preview.svelte",
      "UI/src/components/formBuilder/composer-sidebar.svelte",
      "UI/src/components/formBuilder/inputs.js",
      "UI/src/components/formBuilder/preview-blocks/checkbox-select.svelte",
      "UI/src/components/formBuilder/preview-blocks/smart-select-input.svelte",
      "UI/src/components/formBuilder/preview-blocks/text-input.svelte",
      "UI/src/components/inputs/BottomBarFixedSave.svelte",
      "UI/src/components/inputs/MentionList.svelte",
      "UI/src/components/inputs/TipTapEditor.svelte",
      "UI/src/components/inputs/mentionSuggestions.js",
      "UI/src/components/inputs/suggestion.js",
      "UI/src/components/modals/ShareModuleSubscriptionLink.svelte",
      "UI/src/routes.js",
      "UI/src/routes/association/Members/MembersBook.svelte",
      "UI/src/routes/association/Members/MembersList.svelte",
      "UI/src/routes/association/Members/detail/Detail.svelte",
      "UI/src/routes/association/Members/detail/DetailDrawer.svelte",
      "UI/src/routes/association/Members/detail/sections/Cloud.svelte",
      "UI/src/routes/association/Members/detail/sections/Info.svelte",
      "UI/src/routes/association/Members/subscription/Template.svelte",
      "UI/src/routes/association/Members/subscription/partials/AdditionalFields.svelte",
      "UI/src/routes/association/Members/subscription/partials/ModuleSections.svelte",
      "UI/src/routes/association/Members/subscription/partials/MultipleQuotesSubscription.svelte",
      "UI/src/routes/association/Members/subscription/partials/QuoteIscrizione.svelte",
      "UI/src/routes/association/Members/subscription/partials/TypesModule.svelte",
      "UI/src/routes/association/Members/subscription/share/link-share-header.svelte",
      "UI/src/routes/association/archive/Archive.svelte",
      "UI/src/routes/association/archive/TemplatesList.svelte",
      "UI/src/routes/association/archive/detail/AddTemplatesDrawer.svelte",
      "UI/src/routes/association/archive/detail/Detail.svelte",
      "UI/src/routes/association/archive/detail/TemplatesDrawer.svelte",
      "UI/src/routes/association/archive/detail/partials/template-form.svelte",
      "UI/src/routes/association/archive/modals/GenerateFromTemplateModal.svelte",
      "UI/src/routes/association/archive/shared/NavigationTab.svelte",
      "UI/src/routes/profile/sections/Settings.svelte",
      "UI/src/routes/subscribe/Subscribe.svelte",
      "UI/src/routes/subscribe/wizard/Step0.svelte",
      "UI/src/routes/subscribe/wizard/Step1.svelte",
      "UI/src/routes/subscribe/wizard/Step2.svelte",
      "UI/src/routes/subscribe/wizard/partials/additional-fields.svelte",
      "UI/src/utils/Permissions.js",
      "docs/manuale/organization-basics-authored-workflows.mjs",
      "selfhost/tests/browser/manuale/member-profile-sources.mjs",
      "selfhost/tests/browser/manuale/organization-document-templates.mjs"
    ],
    "checkpoints": [
      {
        "id": "templates-list-before-create",
        "caption": "Templates list before create."
      },
      {
        "id": "template-required-fields-validation",
        "caption": "Template required fields validation."
      },
      {
        "id": "template-name-layout-and-dynamic-person-before-save",
        "caption": "Template name layout and dynamic person before save."
      },
      {
        "id": "template-created-after-reload",
        "caption": "Template created after reload."
      },
      {
        "id": "template-reopened-before-generation",
        "caption": "Template reopened before generation."
      },
      {
        "id": "member-documents-before-generation",
        "caption": "Member documents before generation."
      },
      {
        "id": "member-template-selected-before-generation",
        "caption": "Member template selected before generation."
      },
      {
        "id": "generated-document-persists-in-member-files",
        "caption": "Generated document persists in member files."
      },
      {
        "id": "generated-document-real-renderer-with-person",
        "caption": "Generated document real renderer with person."
      },
      {
        "id": "generated-document-after-download",
        "caption": "Generated document after download."
      },
      {
        "id": "reader-document-generation-action-absent",
        "caption": "Reader document generation action absent."
      }
    ],
    "sections": [
      {
        "path": "tutorials/come-creare-moduli-iscrizione-personalizzati.mdx",
        "id": "creare-un-modulo-completamente-personalizzato",
        "title": "Creare un modulo completamente personalizzato",
        "checkpoints": [
          "templates-list-before-create",
          "template-name-layout-and-dynamic-person-before-save",
          "template-reopened-before-generation"
        ],
        "images": [
          {
            "from": "/images/archivio/modelli-reali/1.placeholder.svg",
            "checkpoint": "templates-list-before-create"
          },
          {
            "from": "/images/archivio/modelli-reali/3.placeholder.svg",
            "checkpoint": "template-name-layout-and-dynamic-person-before-save"
          },
          {
            "from": "/images/archivio/modelli-reali/5.placeholder.svg",
            "checkpoint": "template-reopened-before-generation"
          }
        ],
        "insert_images": []
      },
      {
        "path": "tutorials/come-creare-moduli-iscrizione-personalizzati.mdx",
        "id": "come-assegnare-un-modulo-personalizzato",
        "title": "Come assegnare un modulo personalizzato?",
        "checkpoints": [
          "member-documents-before-generation",
          "member-template-selected-before-generation",
          "generated-document-persists-in-member-files",
          "generated-document-after-download"
        ],
        "images": [
          {
            "from": "/images/archivio/modelli-reali/6.placeholder.svg",
            "checkpoint": "member-documents-before-generation"
          },
          {
            "from": "/images/archivio/modelli-reali/7.placeholder.svg",
            "checkpoint": "member-template-selected-before-generation"
          },
          {
            "from": "/images/archivio/modelli-reali/8.placeholder.svg",
            "checkpoint": "generated-document-persists-in-member-files"
          },
          {
            "from": "/images/archivio/modelli-reali/10.placeholder.svg",
            "checkpoint": "generated-document-after-download"
          }
        ],
        "insert_images": []
      }
    ],
    "outcome": {
      "field": "organization_document_templates_authored_workflow",
      "expected": {
        "empty_fields_block_backend_create": true,
        "template_real_create_and_mention_persisted": true,
        "template_reopened_with_layout_and_mention": true,
        "one_generated_file_attached_and_reopened": true,
        "real_renderer_resolves_person_and_custom_layout": true,
        "actual_ui_pdf_download_matches_persisted_file": true,
        "reader_template_writes_denied": true,
        "baseline_records_preserved": true,
        "owned_template_and_generated_file_removed": true
      }
    }
  }
});
