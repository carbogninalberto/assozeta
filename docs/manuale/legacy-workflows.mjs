// Supplemental regression captures. Complete procedures are owned by archive-maintenance
// and medical-followups; these narrower scenarios cannot publish their prose.
export const legacyWorkflows = Object.freeze({
  "members-archive-restore": {
    "version": 1,
    "script": "selfhost/tests/browser/manuale/archive.mjs",
    "prefix": "images/faq/archiviazione/",
    "pages": [
      "docs/archivio.mdx",
      "faq/come-archiviare-dati.mdx"
    ],
    "sources": [
      "UI/src/components/Sidebar.svelte",
      "UI/src/routes.js",
      "UI/src/utils/Permissions.js",
      "UI/src/routes/association/Members/MembersList.svelte",
      "UI/src/routes/association/Members/MembersListArchive.svelte",
      "UI/src/routes/association/Members/shared/NavigationTab.svelte",
      "UI/src/components/buttons/ArchiveButton.svelte",
      "BE/application/views/subscriptions_views.py",
      "BE/application/views/payment_views.py",
      "BE/application/models/subscriptions_models.py",
      "BE/application/models/payment_models.py",
      "BE/application/permissions_registry.py"
    ],
    "dependencies": [
      "UI/src/routes/association/Members/",
      "BE/application/views/subscriptions_views.py",
      "BE/application/views/payment_views.py",
      "BE/application/models/subscriptions_models.py",
      "BE/application/models/payment_models.py",
      "docs/manuale/content-recipes.mjs",
      "docs/manuale/legacy-workflows.mjs"
    ],
    "checkpoints": [
      {
        "id": "select-members-to-archive",
        "caption": "Iscrizione selezionata per l'archiviazione"
      },
      {
        "id": "archive-confirmation",
        "caption": "Conferma dell'archiviazione"
      },
      {
        "id": "archived-member-and-restore-action",
        "caption": "Archivio delle iscrizioni in Assozeta"
      },
      {
        "id": "restore-confirmation",
        "caption": "Conferma del ritorno nell'elenco attivo"
      },
      {
        "id": "restored-member-persists-after-reload",
        "caption": "Iscrizione tornata nell'elenco attivo"
      }
    ],
    "sections": [],
    "outcome": {
      "field": "archive_workflow",
      "expected": {
        "archived_member_removed": true,
        "unpaid_payment_archived": true,
        "paid_payment_preserved": true,
        "restored_member_persists": true,
        "restoring_does_not_restore_payment": true,
        "reader_archive_status": 403
      }
    }
  },
  "members-medical-manage": {
    "version": 1,
    "script": "selfhost/tests/browser/manuale/member-medical.mjs",
    "prefix": "images/certificati/profilo/",
    "pages": [
      "tutorials/come-gestire-certificati-medici.mdx"
    ],
    "sources": [
      "UI/src/components/Sidebar.svelte",
      "UI/src/routes.js",
      "UI/src/utils/Permissions.js",
      "UI/src/routes/association/Members/MembersList.svelte",
      "UI/src/routes/association/Members/detail/DetailDrawer.svelte",
      "UI/src/routes/association/Members/detail/Detail.svelte",
      "UI/src/routes/association/Members/detail/sections/Info.svelte",
      "UI/src/components/drawer/basic-drawer.svelte",
      "BE/application/views/subscriptions_views.py",
      "BE/application/services/subscription_service.py",
      "BE/application/serializers/subscriptions_serializers.py",
      "BE/application/models/subscriptions_models.py",
      "BE/application/permissions_registry.py",
      "UI/src/routes/association/Members/detail/sections/Medical.svelte",
      "UI/src/routes/association/Members/detail/sections/modals/AddMedicalCertificate.svelte",
      "UI/src/routes/association/Members/detail/sections/modals/medicalCertificateUpload.js",
      "UI/src/components/formBuilder/preview-blocks/datepicker-input.svelte",
      "UI/src/components/inputs/DateInput.svelte",
      "UI/src/shim/dropzone.js",
      "UI/src/utils/ApiMiddleware.js"
    ],
    "dependencies": [
      "UI/src/routes/association/Members/",
      "BE/application/views/subscriptions_views.py",
      "BE/application/services/subscription_service.py",
      "BE/application/serializers/subscriptions_serializers.py",
      "BE/application/models/subscriptions_models.py",
      "selfhost/tests/browser/manuale/member-profile-sources.mjs",
      "UI/src/components/formBuilder/preview-blocks/datepicker-input.svelte",
      "UI/src/components/inputs/DateInput.svelte",
      "UI/src/shim/dropzone.js",
      "BE/docmanager/",
      "docs/manuale/profile-recipes.mjs",
      "docs/manuale/legacy-workflows.mjs"
    ],
    "checkpoints": [
      {
        "id": "medical-tab-with-no-document",
        "caption": "Sezione Certificato medico prima del caricamento"
      },
      {
        "id": "uploaded-demo-file-and-manual-expiration-before-saving",
        "caption": "Campo Data Scadenza compilato prima di salvare"
      },
      {
        "id": "document-and-expiration-persist-after-reload",
        "caption": "Documento e scadenza presenti dopo il ricaricamento"
      },
      {
        "id": "competitive-certificate-setting-saved",
        "caption": "Impostazione agonistica salvata nella sezione del certificato"
      },
      {
        "id": "confirm-certificate-removal",
        "caption": "Conferma facoltativa della rimozione del certificato"
      },
      {
        "id": "medical-tab-empty-after-removal",
        "caption": "Sezione nuovamente vuota dopo la rimozione"
      }
    ],
    "sections": [],
    "outcome": {
      "field": "medical_certificate",
      "expected": {
        "document_uploaded": true,
        "document_id_matches": true,
        "expiration": "2027-09-30",
        "persisted_after_reload": true,
        "competitive_setting_saved": true,
        "reader_update_status": 403,
        "denied_setting_unchanged": true,
        "removed_medical_is_null": true,
        "automatic_expiration_observed": false,
        "email_delivery_exercised": false,
        "input_fixture_sha256": "4ecd72d7384c2e1fbc4aae8bfd3601ca2be9f8b3ca2321f7333858b81a55aaf9"
      }
    }
  }
});
