// Reuse the complete real registration template procedure; no duplicate execution.
export const registrationInitialAuthoredWorkflows = Object.freeze({
  "registration-forms-manage": {
    "version": 1,
    "script": "selfhost/tests/browser/manuale/registration-forms.mjs",
    "prefix": "images/tutorials/moduli-iscrizione/",
    "pages": [
      "docs/introduzione.mdx"
    ],
    "sources": [
      "UI/src/components/Sidebar.svelte",
      "UI/src/routes.js",
      "UI/src/routes/association/Members/subscription/Template.svelte",
      "UI/src/routes/association/Members/subscription/partials/TypesModule.svelte",
      "UI/src/routes/association/Members/subscription/partials/QuoteIscrizione.svelte",
      "UI/src/routes/association/Members/subscription/partials/MultipleQuotesSubscription.svelte",
      "UI/src/routes/association/Members/subscription/partials/ModuleSections.svelte",
      "UI/src/routes/association/Members/subscription/partials/AdditionalFields.svelte",
      "UI/src/components/formBuilder/composer-sidebar.svelte",
      "UI/src/components/formBuilder/composer-preview.svelte",
      "UI/src/components/formBuilder/inputs.js",
      "UI/src/components/formBuilder/preview-blocks/text-input.svelte",
      "UI/src/routes/association/Members/MembersBook.svelte",
      "UI/src/components/modals/ShareModuleSubscriptionLink.svelte",
      "UI/src/routes/association/Members/subscription/share/link-share-header.svelte",
      "UI/src/routes/subscribe/Subscribe.svelte",
      "UI/src/routes/subscribe/wizard/Step0.svelte",
      "UI/src/routes/subscribe/wizard/Step1.svelte",
      "UI/src/routes/subscribe/wizard/partials/additional-fields.svelte",
      "UI/src/routes/subscribe/wizard/Step2.svelte",
      "UI/src/utils/Permissions.js",
      "BE/application/urls.py",
      "BE/application/views/profile_views.py",
      "BE/application/views/search_views.py",
      "BE/application/serializers/auth_serializers.py",
      "BE/application/models/user_models.py",
      "BE/application/permissions_registry.py",
      "UI/src/routes/profile/sections/Settings.svelte",
      "UI/src/routes/association/archive/TemplatesList.svelte",
      "UI/src/routes/association/archive/detail/partials/template-form.svelte",
      "UI/src/routes/association/Members/detail/sections/Cloud.svelte",
      "UI/src/routes/association/archive/modals/GenerateFromTemplateModal.svelte",
      "BE/application/views/archive_views.py",
      "BE/docmanager/views/printing_views.py",
      "UI/src/App.svelte",
      "UI/src/components/Header.svelte",
      "UI/src/routes/manuale/Manuale.svelte",
      "UI/src/routes/manuale/ManualContent.svelte",
      "UI/src/routes/manuale/manualPresentation.js",
      "BE/application/manuale/views.py"
    ],
    "dependencies": [
      "selfhost/tests/browser/manuale/registration-forms.mjs",
      "selfhost/tests/browser/manuale/registration-forms-sources.mjs",
      "docs/manuale/registration-initial-authored-workflows.mjs"
    ],
    "checkpoints": [
      {
        "id": "owner-registration-settings-and-three-enabled-types",
        "caption": "Modulo e tipologie iniziali"
      },
      {
        "id": "only-socio-e-tesserato-enabled-before-save",
        "caption": "Socio e Tesserato selezionato"
      },
      {
        "id": "single-association-fee-thirty-before-save",
        "caption": "Quota semplice configurata"
      },
      {
        "id": "custom-section-title-text-and-both-only-visibility",
        "caption": "Sezione informativa configurata"
      },
      {
        "id": "optional-shirt-size-field-label-placeholder-and-help",
        "caption": "Campo facoltativo configurato"
      },
      {
        "id": "registration-type-persists-after-reload",
        "caption": "Tipologia conservata"
      },
      {
        "id": "custom-section-uppercase-title-and-visibility-persist",
        "caption": "Sezione conservata"
      },
      {
        "id": "custom-field-properties-persist-after-reload",
        "caption": "Campo conservato"
      },
      {
        "id": "members-book-current-registration-link-action",
        "caption": "Comando del link iscrizioni"
      },
      {
        "id": "registration-share-dialog-clipboard-and-options",
        "caption": "Finestra del link corrente"
      },
      {
        "id": "public-registration-start-shows-configured-single-type",
        "caption": "Modulo online aperto"
      },
      {
        "id": "public-registration-personal-data-shows-shirt-size-without-submission",
        "caption": "Campo online facoltativo"
      },
      {
        "id": "reader-registration-save-disabled",
        "caption": "Salva disattivato in sola lettura"
      }
    ],
    "sections": [
      {
        "path": "docs/introduzione.mdx",
        "id": "per-iniziare",
        "title": "Per iniziare",
        "checkpoints": [
          "owner-registration-settings-and-three-enabled-types",
          "only-socio-e-tesserato-enabled-before-save",
          "single-association-fee-thirty-before-save",
          "custom-section-title-text-and-both-only-visibility",
          "optional-shirt-size-field-label-placeholder-and-help",
          "registration-type-persists-after-reload",
          "custom-section-uppercase-title-and-visibility-persist",
          "custom-field-properties-persist-after-reload",
          "members-book-current-registration-link-action",
          "registration-share-dialog-clipboard-and-options",
          "public-registration-start-shows-configured-single-type",
          "public-registration-personal-data-shows-shirt-size-without-submission",
          "reader-registration-save-disabled"
        ],
        "images": [
          {
            "from": "/images/tutorials/moduli-iscrizione/1.placeholder.svg",
            "checkpoint": "owner-registration-settings-and-three-enabled-types"
          },
          {
            "from": "/images/tutorials/moduli-iscrizione/2.placeholder.svg",
            "checkpoint": "only-socio-e-tesserato-enabled-before-save"
          },
          {
            "from": "/images/tutorials/moduli-iscrizione/3.placeholder.svg",
            "checkpoint": "single-association-fee-thirty-before-save"
          },
          {
            "from": "/images/tutorials/moduli-iscrizione/4.placeholder.svg",
            "checkpoint": "custom-section-title-text-and-both-only-visibility"
          },
          {
            "from": "/images/tutorials/moduli-iscrizione/5.placeholder.svg",
            "checkpoint": "optional-shirt-size-field-label-placeholder-and-help"
          },
          {
            "from": "/images/tutorials/moduli-iscrizione/6.placeholder.svg",
            "checkpoint": "registration-type-persists-after-reload"
          },
          {
            "from": "/images/tutorials/moduli-iscrizione/7.placeholder.svg",
            "checkpoint": "custom-section-uppercase-title-and-visibility-persist"
          },
          {
            "from": "/images/tutorials/moduli-iscrizione/8.placeholder.svg",
            "checkpoint": "custom-field-properties-persist-after-reload"
          },
          {
            "from": "/images/tutorials/moduli-iscrizione/9.placeholder.svg",
            "checkpoint": "members-book-current-registration-link-action"
          },
          {
            "from": "/images/tutorials/moduli-iscrizione/10.placeholder.svg",
            "checkpoint": "registration-share-dialog-clipboard-and-options"
          },
          {
            "from": "/images/tutorials/moduli-iscrizione/11.placeholder.svg",
            "checkpoint": "public-registration-start-shows-configured-single-type"
          },
          {
            "from": "/images/tutorials/moduli-iscrizione/12.placeholder.svg",
            "checkpoint": "public-registration-personal-data-shows-shirt-size-without-submission"
          },
          {
            "from": "/images/tutorials/moduli-iscrizione/13.placeholder.svg",
            "checkpoint": "reader-registration-save-disabled"
          }
        ],
        "insert_images": []
      }
    ],
    "outcome": {
      "field": "registration_forms_workflow",
      "expected": {
        "configured_type": "associate-membership",
        "subscription_fee": "30.00",
        "membership_fee": "0.00",
        "quotes_enabled": true,
        "multiple_fees_exercised": false,
        "section_name": "MATERIALE PER ALLENAMENTO",
        "section_text": "Porta borraccia e abbigliamento comodo.",
        "section_both_visible": true,
        "section_members_visible": false,
        "section_athletes_visible": false,
        "field_type": "text",
        "field_label": "Taglia maglietta",
        "field_placeholder": "Esempio: M",
        "field_helper": "Indica la taglia desiderata.",
        "field_required": false,
        "persisted_after_reload": true,
        "copied_link_matches_display": true,
        "copied_link_is_current_registration": true,
        "public_config_matches_saved": true,
        "public_custom_field_visible": true,
        "reader_template_write_status": 403,
        "reader_save_disabled": true,
        "denied_write_preserved_configuration": true,
        "registration_submissions": 0,
        "invitation_dispatches": 0,
        "external_share_actions": 0,
        "subscription_records_unchanged": true,
        "existing_payment_ids_unchanged": true,
        "baseline_restored": true
      }
    }
  }
});
