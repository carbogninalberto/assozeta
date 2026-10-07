// Complete legacy execution bindings; prose remains in the manual checkout.
export const coreLegacyAuthoredWorkflows = Object.freeze({
  "members-create": {
    "version": 1,
    "script": "selfhost/tests/browser/manuale/members-create.mjs",
    "prefix": "images/faq/creazione-socio/",
    "pages": [
      "faq/come-si-crea-un-socio.mdx",
      "docs/libro-soci.mdx",
      "faq/come-calcolare-generare-codici-fiscali.mdx"
    ],
    "sources": [
      "BE/application/models/payment_models.py",
      "BE/application/models/subscriptions_models.py",
      "BE/application/models/user_models.py",
      "BE/application/permissions_registry.py",
      "BE/application/serializers/subscriptions_serializers.py",
      "BE/application/serializers/user_serializers.py",
      "BE/application/utils/subscriptions_utils.py",
      "BE/application/views/subscriptions_views.py",
      "UI/src/components/Sidebar.svelte",
      "UI/src/components/buttons/GenerateTaxCodeButton.svelte",
      "UI/src/components/signature/SmoothSignature.svelte",
      "UI/src/routes.js",
      "UI/src/routes/association/Members/MembersList.svelte",
      "UI/src/routes/association/Members/add/AddMember.svelte",
      "UI/src/routes/association/Members/add/sections/Section1.svelte",
      "UI/src/routes/association/Members/add/sections/Section2.svelte",
      "UI/src/routes/association/Members/add/sections/Section3.svelte",
      "UI/src/routes/association/Members/add/sections/Section4.svelte",
      "UI/src/routes/association/Members/add/sections/Section6.svelte",
      "UI/src/store/stores.js",
      "UI/src/utils/Permissions.js",
      "UI/src/utils/enumUtils.js"
    ],
    "dependencies": [
      "selfhost/tests/browser/manuale/members-create.mjs",
      "docs/manuale/core-legacy-authored-workflows.mjs"
    ],
    "checkpoints": [
      {
        "id": "members-list-and-add-action",
        "caption": "Elenco delle iscrizioni e pulsante Aggiungi"
      },
      {
        "id": "registration-type-and-no-new-account",
        "caption": "Tipo di iscrizione, quote e scelta dell’account"
      },
      {
        "id": "personal-data-and-fiscal-code",
        "caption": "Dati anagrafici e codice fiscale generato"
      },
      {
        "id": "residence-and-contact-data",
        "caption": "Residenza e recapiti della persona"
      },
      {
        "id": "fictional-signature-entered",
        "caption": "Firma dimostrativa nel modulo di iscrizione"
      },
      {
        "id": "medical-certificate-step-with-no-attachment",
        "caption": "Passaggio del certificato medico senza allegato"
      },
      {
        "id": "registration-summary-before-saving",
        "caption": "Riepilogo della richiesta prima del salvataggio"
      },
      {
        "id": "created-pending-registration-persists-after-reload",
        "caption": "Nuova iscrizione in attesa, presente dopo il ricaricamento"
      }
    ],
    "sections": [
      {
        "path": "faq/come-si-crea-un-socio.mdx",
        "id": "creare-un-socio-socio-tesserato-o-tesserato",
        "title": "Creare un socio, socio tesserato o tesserato",
        "checkpoints": [
          "members-list-and-add-action",
          "registration-type-and-no-new-account",
          "personal-data-and-fiscal-code",
          "residence-and-contact-data",
          "fictional-signature-entered",
          "medical-certificate-step-with-no-attachment",
          "registration-summary-before-saving",
          "created-pending-registration-persists-after-reload"
        ],
        "images": [
          {
            "from": "/images/faq/creazione-socio/1.placeholder.svg",
            "checkpoint": "members-list-and-add-action"
          },
          {
            "from": "/images/faq/creazione-socio/2.placeholder.svg",
            "checkpoint": "registration-type-and-no-new-account"
          },
          {
            "from": "/images/faq/creazione-socio/3.placeholder.svg",
            "checkpoint": "personal-data-and-fiscal-code"
          },
          {
            "from": "/images/faq/creazione-socio/4.placeholder.svg",
            "checkpoint": "residence-and-contact-data"
          },
          {
            "from": "/images/faq/creazione-socio/5.placeholder.svg",
            "checkpoint": "fictional-signature-entered"
          },
          {
            "from": "/images/faq/creazione-socio/6.placeholder.svg",
            "checkpoint": "medical-certificate-step-with-no-attachment"
          },
          {
            "from": "/images/faq/creazione-socio/7.placeholder.svg",
            "checkpoint": "registration-summary-before-saving"
          },
          {
            "from": "/images/faq/creazione-socio/8.placeholder.svg",
            "checkpoint": "created-pending-registration-persists-after-reload"
          }
        ]
      },
      {
        "path": "docs/libro-soci.mdx",
        "id": "aggiungi-un-socio",
        "title": "Aggiungi un Socio",
        "checkpoints": [
          "members-list-and-add-action"
        ],
        "images": [
          {
            "from": "/images/faq/creazione-socio/1.placeholder.svg",
            "checkpoint": "members-list-and-add-action"
          }
        ]
      },
      {
        "path": "docs/libro-soci.mdx",
        "id": "1-informazioni-profilo",
        "title": "1. Informazioni Profilo",
        "checkpoints": [
          "registration-type-and-no-new-account"
        ],
        "images": [
          {
            "from": "/images/faq/creazione-socio/2.placeholder.svg",
            "checkpoint": "registration-type-and-no-new-account"
          }
        ]
      },
      {
        "path": "docs/libro-soci.mdx",
        "id": "2-informazioni-anagrafiche",
        "title": "2. Informazioni Anagrafiche",
        "checkpoints": [
          "personal-data-and-fiscal-code",
          "residence-and-contact-data"
        ],
        "images": [
          {
            "from": "/images/faq/creazione-socio/3.placeholder.svg",
            "checkpoint": "personal-data-and-fiscal-code"
          },
          {
            "from": "/images/faq/creazione-socio/4.placeholder.svg",
            "checkpoint": "residence-and-contact-data"
          }
        ]
      },
      {
        "path": "docs/libro-soci.mdx",
        "id": "3-firma-del-documento",
        "title": "3. Firma del Documento",
        "checkpoints": [
          "fictional-signature-entered"
        ],
        "images": [
          {
            "from": "/images/faq/creazione-socio/5.placeholder.svg",
            "checkpoint": "fictional-signature-entered"
          }
        ]
      },
      {
        "path": "docs/libro-soci.mdx",
        "id": "4-certificato-medico",
        "title": "4. Certificato Medico",
        "checkpoints": [
          "medical-certificate-step-with-no-attachment"
        ],
        "images": [
          {
            "from": "/images/faq/creazione-socio/6.placeholder.svg",
            "checkpoint": "medical-certificate-step-with-no-attachment"
          }
        ]
      },
      {
        "path": "docs/libro-soci.mdx",
        "id": "5-riepilogo-e-creazione",
        "title": "5. Riepilogo e Creazione",
        "checkpoints": [
          "registration-summary-before-saving",
          "created-pending-registration-persists-after-reload"
        ],
        "images": [
          {
            "from": "/images/faq/creazione-socio/7.placeholder.svg",
            "checkpoint": "registration-summary-before-saving"
          },
          {
            "from": "/images/faq/creazione-socio/8.placeholder.svg",
            "checkpoint": "created-pending-registration-persists-after-reload"
          }
        ]
      },
      {
        "path": "faq/come-calcolare-generare-codici-fiscali.mdx",
        "id": "come-funziona",
        "title": "Come funziona",
        "checkpoints": [
          "personal-data-and-fiscal-code"
        ],
        "images": [
          {
            "from": "/images/faq/creazione-socio/3.placeholder.svg",
            "checkpoint": "personal-data-and-fiscal-code"
          }
        ]
      }
    ],
    "outcome": {
      "field": "member_creation",
      "expected": {
        "type": 2,
        "role": 1,
        "status_flag": 2,
        "account_created": false,
        "owner_account_preserved": true,
        "payment_amount": 25,
        "payment_paid": false,
        "signature_saved": true,
        "fiscal_code_generated": true,
        "medical_attached": false,
        "persisted_after_reload": true,
        "registrations": 4,
        "reader_create_status": 403
      }
    }
  },
  "members-search": {
    "version": 1,
    "script": "selfhost/tests/browser/manuale/members-search.mjs",
    "prefix": "images/faq/ricerca-filtri/",
    "pages": [
      "docs/libro-soci.mdx",
      "faq/come-usare-ricerca-filtri-atleti.mdx"
    ],
    "sources": [
      "BE/application/models/subscriptions_models.py",
      "BE/application/permissions_registry.py",
      "BE/application/utils/subscriptions_utils.py",
      "BE/application/views/subscriptions_views.py",
      "UI/src/components/Sidebar.svelte",
      "UI/src/routes.js",
      "UI/src/routes/association/Members/MembersList.svelte",
      "UI/src/store/stores.js",
      "UI/src/utils/Permissions.js"
    ],
    "dependencies": [
      "selfhost/tests/browser/manuale/members-search.mjs",
      "docs/manuale/core-legacy-authored-workflows.mjs"
    ],
    "checkpoints": [
      {
        "id": "all-fixture-members",
        "caption": "Elenco delle iscrizioni dell'associazione"
      },
      {
        "id": "name-search-result",
        "caption": "Risultato della ricerca per nome"
      },
      {
        "id": "no-matching-member",
        "caption": "Ricerca senza corrispondenze"
      },
      {
        "id": "search-cleared",
        "caption": "Elenco ripristinato dopo la ricerca"
      }
    ],
    "sections": [
      {
        "path": "docs/libro-soci.mdx",
        "id": "introduzione",
        "title": "Introduzione",
        "checkpoints": [
          "all-fixture-members"
        ],
        "images": [
          {
            "from": "/images/faq/ricerca-filtri/1.placeholder.svg",
            "checkpoint": "all-fixture-members"
          }
        ],
        "intent": "members.read"
      },
      {
        "path": "faq/come-usare-ricerca-filtri-atleti.mdx",
        "id": "la-barra-di-ricerca",
        "title": "La barra di ricerca",
        "checkpoints": [
          "all-fixture-members",
          "name-search-result",
          "no-matching-member",
          "search-cleared"
        ],
        "images": [
          {
            "from": "/images/faq/ricerca-filtri/1.placeholder.svg",
            "checkpoint": "all-fixture-members"
          },
          {
            "from": "/images/faq/ricerca-filtri/2.placeholder.svg",
            "checkpoint": "name-search-result"
          },
          {
            "from": "/images/faq/ricerca-filtri/3.placeholder.svg",
            "checkpoint": "no-matching-member"
          },
          {
            "from": "/images/faq/ricerca-filtri/4.placeholder.svg",
            "checkpoint": "search-cleared"
          }
        ]
      }
    ],
    "outcome": {
      "field": "member_search",
      "expected": {
        "initial_rows": 3,
        "filtered_rows": 1,
        "unknown_name_rows": 0,
        "reset_rows": 3,
        "search_cleared": true
      }
    }
  },
  "tags-create-assign": {
    "version": 1,
    "script": "selfhost/tests/browser/manuale/capture.mjs",
    "prefix": "images/tutorials/tags/",
    "pages": [
      "tutorials/come-assegnare-i-tag-agli-atleti.mdx"
    ],
    "sources": [
      "BE/application/models/subscriptions_models.py",
      "BE/application/permissions_registry.py",
      "BE/application/services/subscription_service.py",
      "BE/application/urls.py",
      "BE/application/views/subscriptions_views.py",
      "UI/src/components/Sidebar.svelte",
      "UI/src/components/tables/BKNDatatable.svelte",
      "UI/src/routes.js",
      "UI/src/routes/association/Members/MembersList.svelte",
      "UI/src/utils/Permissions.js"
    ],
    "dependencies": [
      "selfhost/tests/browser/manuale/capture.mjs",
      "docs/manuale/core-legacy-authored-workflows.mjs"
    ],
    "checkpoints": [
      {
        "id": "members-list",
        "caption": "Elenco delle iscrizioni da cui selezionare Giulia"
      },
      {
        "id": "new-tag-name",
        "caption": "Nome del tag da creare nel menu Assegna Tag"
      },
      {
        "id": "tag-selected",
        "caption": "Principianti selezionato prima dell’applicazione"
      },
      {
        "id": "assignment-persisted",
        "caption": "Tag nella riga di Giulia dopo il ricaricamento"
      },
      {
        "id": "tag-unchecked-before-apply",
        "caption": "Tag non selezionato prima della conferma"
      },
      {
        "id": "tag-removal-persists-after-reload",
        "caption": "Riga di Giulia dopo la rimozione del collegamento e il ricaricamento"
      }
    ],
    "sections": [
      {
        "path": "tutorials/come-assegnare-i-tag-agli-atleti.mdx",
        "id": "indice",
        "title": "Indice",
        "checkpoints": [
          "members-list",
          "new-tag-name",
          "tag-selected",
          "assignment-persisted",
          "tag-unchecked-before-apply",
          "tag-removal-persists-after-reload"
        ],
        "images": [],
        "intent": "members.tags.read"
      },
      {
        "path": "tutorials/come-assegnare-i-tag-agli-atleti.mdx",
        "id": "creare-un-nuovo-tag",
        "title": "Creare un nuovo tag",
        "checkpoints": [
          "members-list",
          "new-tag-name",
          "tag-selected",
          "assignment-persisted"
        ],
        "images": [
          {
            "from": "/images/tutorials/tags/1.placeholder.svg",
            "checkpoint": "members-list"
          },
          {
            "from": "/images/tutorials/tags/2.placeholder.svg",
            "checkpoint": "new-tag-name"
          },
          {
            "from": "/images/tutorials/tags/3.placeholder.svg",
            "checkpoint": "tag-selected"
          },
          {
            "from": "/images/tutorials/tags/4.placeholder.svg",
            "checkpoint": "assignment-persisted"
          }
        ],
        "intent": "members.tags.assign"
      },
      {
        "path": "tutorials/come-assegnare-i-tag-agli-atleti.mdx",
        "id": "seleziona-gli-atleti",
        "title": "Seleziona gli atleti",
        "checkpoints": [
          "members-list"
        ],
        "images": [
          {
            "from": "/images/tutorials/tags/1.placeholder.svg",
            "checkpoint": "members-list"
          }
        ],
        "intent": "members.tags.assign"
      },
      {
        "path": "tutorials/come-assegnare-i-tag-agli-atleti.mdx",
        "id": "assegna-tag",
        "title": "Assegna Tag",
        "checkpoints": [
          "tag-selected",
          "assignment-persisted"
        ],
        "images": [
          {
            "from": "/images/tutorials/tags/3.placeholder.svg",
            "checkpoint": "tag-selected"
          },
          {
            "from": "/images/tutorials/tags/4.placeholder.svg",
            "checkpoint": "assignment-persisted"
          }
        ],
        "intent": "members.tags.assign"
      },
      {
        "path": "tutorials/come-assegnare-i-tag-agli-atleti.mdx",
        "id": "modificare-e-o-eliminare-uno-o-piu-tag",
        "title": "Modificare e/o eliminare uno o più tag",
        "checkpoints": [
          "tag-unchecked-before-apply",
          "tag-removal-persists-after-reload"
        ],
        "images": [
          {
            "from": "/images/tutorials/tags/5.placeholder.svg",
            "checkpoint": "tag-unchecked-before-apply"
          },
          {
            "from": "/images/tutorials/tags/6.placeholder.svg",
            "checkpoint": "tag-removal-persists-after-reload"
          }
        ],
        "intent": "members.tags.remove"
      }
    ],
    "outcome": {
      "field": "tags_workflow",
      "expected": {
        "created": true,
        "assigned_after_reload": true,
        "removed_after_reload": true,
        "tag_definition_preserved": true,
        "unrelated_member_unchanged": true,
        "reader_create_status": 403,
        "reader_assign_status": 403,
        "reader_unassign_status": 403
      }
    }
  },
  "payments-create-edit-approve": {
    "version": 1,
    "script": "selfhost/tests/browser/manuale/payments.mjs",
    "prefix": "images/pagamenti/registrazione/",
    "pages": [
      "docs/pagamenti.mdx"
    ],
    "sources": [
      "BE/application/models/invoices_models.py",
      "BE/application/models/payment_models.py",
      "BE/application/models/user_models.py",
      "BE/application/permissions_registry.py",
      "BE/application/serializers/payment_serializers.py",
      "BE/application/services/invoice_service.py",
      "BE/application/views/payment_views.py",
      "UI/src/components/Sidebar.svelte",
      "UI/src/components/buttons/ApproveButton.svelte",
      "UI/src/components/buttons/EditButton.svelte",
      "UI/src/components/formBuilder/preview-blocks/smart-select-input.svelte",
      "UI/src/routes.js",
      "UI/src/routes/accounting/payment/PaymentDrawer.svelte",
      "UI/src/routes/accounting/payment/PaymentList.svelte",
      "UI/src/routes/accounting/payment/modals/AddEditModal.svelte",
      "UI/src/routes/accounting/payment/partials/payment-overview.svelte",
      "UI/src/utils/Permissions.js"
    ],
    "dependencies": [
      "selfhost/tests/browser/manuale/payments.mjs",
      "docs/manuale/core-legacy-authored-workflows.mjs"
    ],
    "checkpoints": [
      {
        "id": "filled-incoming-cash-payment",
        "caption": "Modulo compilato per una nuova entrata in contanti"
      },
      {
        "id": "created-payment-in-list",
        "caption": "Pagamento creato nell'elenco"
      },
      {
        "id": "edited-payment-amount",
        "caption": "Modifica dell'importo prima dell'incasso"
      },
      {
        "id": "edited-payment-persists-after-reload",
        "caption": "Importo modificato e conservato dopo il ricaricamento"
      },
      {
        "id": "approval-without-immediate-pdf",
        "caption": "Conferma dell'incasso senza generazione immediata del PDF"
      },
      {
        "id": "approved-payment-persists-with-receipt-record",
        "caption": "Pagamento incassato e conservato nell'elenco"
      }
    ],
    "sections": [
      {
        "path": "docs/pagamenti.mdx",
        "id": "creare-un-pagamento",
        "title": "Creare un pagamento",
        "checkpoints": [
          "filled-incoming-cash-payment",
          "created-payment-in-list"
        ],
        "images": [
          {
            "from": "/images/pagamenti/registrazione/1.placeholder.svg",
            "checkpoint": "filled-incoming-cash-payment"
          },
          {
            "from": "/images/pagamenti/registrazione/2.placeholder.svg",
            "checkpoint": "created-payment-in-list"
          }
        ]
      },
      {
        "path": "docs/pagamenti.mdx",
        "id": "modificare-un-pagamento",
        "title": "Modificare un pagamento",
        "checkpoints": [
          "edited-payment-amount",
          "edited-payment-persists-after-reload"
        ],
        "images": [
          {
            "from": "/images/pagamenti/registrazione/3.placeholder.svg",
            "checkpoint": "edited-payment-amount"
          },
          {
            "from": "/images/pagamenti/registrazione/4.placeholder.svg",
            "checkpoint": "edited-payment-persists-after-reload"
          }
        ]
      },
      {
        "path": "docs/pagamenti.mdx",
        "id": "segna-un-pagamento-come-pagato",
        "title": "Segna un pagamento come \"pagato\"",
        "checkpoints": [
          "approval-without-immediate-pdf",
          "approved-payment-persists-with-receipt-record"
        ],
        "images": [
          {
            "from": "/images/pagamenti/registrazione/5.placeholder.svg",
            "checkpoint": "approval-without-immediate-pdf"
          },
          {
            "from": "/images/pagamenti/registrazione/6.placeholder.svg",
            "checkpoint": "approved-payment-persists-with-receipt-record"
          }
        ]
      }
    ],
    "outcome": {
      "field": "payment_lifecycle",
      "expected": {
        "created_amount": 35,
        "created_paid": false,
        "edited_amount": 40,
        "edited_paid": false,
        "paid_after_reload": true,
        "receipt_number": 1,
        "receipt_pdf_created": false,
        "payment_date_preserved": true,
        "reader_create_status": 403,
        "reader_update_status": 403,
        "reader_approve_status": 403,
        "denied_writes_preserve_amount": true,
        "other_payment_unpaid": true
      }
    }
  },
  "receipts-approve-download": {
    "version": 1,
    "script": "selfhost/tests/browser/manuale/receipts.mjs",
    "prefix": "images/ricevute/consultazione/",
    "pages": [
      "docs/ricevute.mdx"
    ],
    "sources": [
      "BE/application/models/invoices_models.py",
      "BE/application/models/payment_models.py",
      "BE/application/models/user_models.py",
      "BE/application/permissions_registry.py",
      "BE/application/printing_tasks.py",
      "BE/application/serializers/invoice_serializers.py",
      "BE/application/serializers/payment_serializers.py",
      "BE/application/services/invoice_service.py",
      "BE/application/utils/printing.py",
      "BE/application/views/invoice_views.py",
      "BE/application/views/payment_views.py",
      "BE/application/views/profile_views.py",
      "BE/docmanager/tasks.py",
      "BE/docmanager/views/document_view.py",
      "BE/docmanager/views/printing_views.py",
      "BE/templates/document/application/invoice.html",
      "UI/src/components/Sidebar.svelte",
      "UI/src/components/buttons/ApproveButton.svelte",
      "UI/src/components/buttons/EditButton.svelte",
      "UI/src/components/filters/FilterSelect.svelte",
      "UI/src/components/formBuilder/preview-blocks/smart-select-input.svelte",
      "UI/src/components/modals/InvoicePreviewModal.svelte",
      "UI/src/routes.js",
      "UI/src/routes/accounting/payment/PaymentDrawer.svelte",
      "UI/src/routes/accounting/payment/PaymentList.svelte",
      "UI/src/routes/accounting/payment/modals/AddEditModal.svelte",
      "UI/src/routes/accounting/payment/partials/payment-overview.svelte",
      "UI/src/routes/accounting/receipts/ReceiptList.svelte",
      "UI/src/routes/accounting/receipts/invoiceActionState.js",
      "UI/src/routes/accounting/receipts/modals/EditModal.svelte",
      "UI/src/routes/accounting/receipts/modals/ShareModal.svelte",
      "UI/src/routes/profile/sections/Settings.svelte",
      "UI/src/utils/Permissions.js"
    ],
    "dependencies": [
      "selfhost/tests/browser/manuale/receipts.mjs",
      "docs/manuale/core-legacy-authored-workflows.mjs"
    ],
    "checkpoints": [
      {
        "id": "approval-with-pdf-and-no-email",
        "caption": "Incasso di una quota associativa con generazione del PDF"
      },
      {
        "id": "receipt-list-with-generated-pdfs-and-progressives",
        "caption": "Due ricevute registrate con progressivi 1 e 2"
      },
      {
        "id": "receipt-pdf-preview-without-share-token",
        "caption": "Anteprima del PDF della ricevuta"
      },
      {
        "id": "copy-download-link-with-token-redacted",
        "caption": "Pulsante per copiare il link di download; il link è oscurato nell'immagine"
      }
    ],
    "sections": [
      {
        "path": "docs/ricevute.mdx",
        "id": "come-vengono-emesse-le-ricevute",
        "title": "Come vengono emesse le ricevute?",
        "checkpoints": [
          "approval-with-pdf-and-no-email",
          "receipt-list-with-generated-pdfs-and-progressives"
        ],
        "images": [
          {
            "from": "/images/ricevute/consultazione/1.placeholder.svg",
            "checkpoint": "approval-with-pdf-and-no-email"
          },
          {
            "from": "/images/ricevute/consultazione/2.placeholder.svg",
            "checkpoint": "receipt-list-with-generated-pdfs-and-progressives"
          }
        ]
      },
      {
        "path": "docs/ricevute.mdx",
        "id": "numerazione-progressiva",
        "title": "Numerazione progressiva",
        "checkpoints": [
          "receipt-list-with-generated-pdfs-and-progressives"
        ],
        "images": [
          {
            "from": "/images/ricevute/consultazione/2.placeholder.svg",
            "checkpoint": "receipt-list-with-generated-pdfs-and-progressives"
          }
        ]
      },
      {
        "path": "docs/ricevute.mdx",
        "id": "come-posso-scaricare-una-ricevuta",
        "title": "Come posso scaricare una ricevuta?",
        "checkpoints": [
          "receipt-list-with-generated-pdfs-and-progressives",
          "receipt-pdf-preview-without-share-token",
          "copy-download-link-with-token-redacted"
        ],
        "images": [
          {
            "from": "/images/ricevute/consultazione/2.placeholder.svg",
            "checkpoint": "receipt-list-with-generated-pdfs-and-progressives"
          },
          {
            "from": "/images/ricevute/consultazione/3.placeholder.svg",
            "checkpoint": "receipt-pdf-preview-without-share-token"
          },
          {
            "from": "/images/ricevute/consultazione/4.placeholder.svg",
            "checkpoint": "copy-download-link-with-token-redacted"
          }
        ]
      }
    ],
    "outcome": {
      "field": "receipt_download",
      "expected": {
        "receipt_numbers": [
          1,
          2
        ],
        "both_pdfs_downloaded": true,
        "preview_is_pdf": true,
        "clipboard_matches_download": true,
        "browser_download_matches_pdf": true,
        "membership_fee": 25,
        "activity_fee": 0,
        "reader_write_statuses": [
          403,
          403,
          403
        ],
        "receipt_number_preserved": true,
        "receipt_pdf_preserved": true
      }
    }
  },
  "receipts-edit-delete": {
    "version": 1,
    "script": "selfhost/tests/browser/manuale/receipt-mutations.mjs",
    "prefix": "images/ricevute/modifica-eliminazione/",
    "pages": [
      "docs/ricevute.mdx"
    ],
    "sources": [
      "BE/application/models/invoices_models.py",
      "BE/application/models/payment_models.py",
      "BE/application/models/user_models.py",
      "BE/application/permissions_registry.py",
      "BE/application/printing_tasks.py",
      "BE/application/serializers/auth_serializers.py",
      "BE/application/serializers/invoice_serializers.py",
      "BE/application/serializers/payment_serializers.py",
      "BE/application/services/invoice_service.py",
      "BE/application/utils/printing.py",
      "BE/application/views/invoice_views.py",
      "BE/application/views/payment_views.py",
      "BE/application/views/profile_views.py",
      "BE/docmanager/tasks.py",
      "BE/docmanager/views/document_view.py",
      "BE/docmanager/views/printing_views.py",
      "BE/templates/document/application/invoice.html",
      "UI/src/components/Sidebar.svelte",
      "UI/src/components/buttons/ApproveButton.svelte",
      "UI/src/components/buttons/EditButton.svelte",
      "UI/src/components/filters/FilterSelect.svelte",
      "UI/src/components/formBuilder/preview-blocks/smart-select-input.svelte",
      "UI/src/components/modals/InvoicePreviewModal.svelte",
      "UI/src/routes.js",
      "UI/src/routes/accounting/payment/PaymentDrawer.svelte",
      "UI/src/routes/accounting/payment/PaymentList.svelte",
      "UI/src/routes/accounting/payment/modals/AddEditModal.svelte",
      "UI/src/routes/accounting/payment/partials/payment-overview.svelte",
      "UI/src/routes/accounting/receipts/ReceiptList.svelte",
      "UI/src/routes/accounting/receipts/invoiceActionState.js",
      "UI/src/routes/accounting/receipts/modals/EditModal.svelte",
      "UI/src/routes/accounting/receipts/modals/ShareModal.svelte",
      "UI/src/routes/profile/sections/Settings.svelte",
      "UI/src/utils/Permissions.js"
    ],
    "dependencies": [
      "selfhost/tests/browser/manuale/receipt-mutations.mjs",
      "docs/manuale/core-legacy-authored-workflows.mjs"
    ],
    "checkpoints": [
      {
        "id": "older-receipt-with-progressive-seven",
        "caption": "Ricevuta di Giulia Bianchi con numero 7"
      },
      {
        "id": "edit-receipt-progressive-to-nine",
        "caption": "Nuovo progressivo nel modulo della ricevuta"
      },
      {
        "id": "edited-receipt-persists-after-reload",
        "caption": "Progressivo 9 conservato nell'elenco delle ricevute"
      },
      {
        "id": "confirm-deletion-of-older-receipt",
        "caption": "Conferma dell'eliminazione della ricevuta"
      },
      {
        "id": "payment-unpaid-after-receipt-deletion",
        "caption": "Pagamento tornato in attesa dopo l'eliminazione della ricevuta"
      }
    ],
    "sections": [
      {
        "path": "docs/ricevute.mdx",
        "id": "modifica-del-progressivo-della-ricevuta",
        "title": "Modifica del progressivo della ricevuta",
        "checkpoints": [
          "older-receipt-with-progressive-seven",
          "edit-receipt-progressive-to-nine",
          "edited-receipt-persists-after-reload"
        ],
        "images": [
          {
            "from": "/images/ricevute/modifica-eliminazione/1.placeholder.svg",
            "checkpoint": "older-receipt-with-progressive-seven"
          },
          {
            "from": "/images/ricevute/modifica-eliminazione/2.placeholder.svg",
            "checkpoint": "edit-receipt-progressive-to-nine"
          },
          {
            "from": "/images/ricevute/modifica-eliminazione/3.placeholder.svg",
            "checkpoint": "edited-receipt-persists-after-reload"
          }
        ]
      },
      {
        "path": "docs/ricevute.mdx",
        "id": "eliminare-una-ricevuta",
        "title": "Eliminare una ricevuta",
        "checkpoints": [
          "confirm-deletion-of-older-receipt",
          "payment-unpaid-after-receipt-deletion"
        ],
        "images": [
          {
            "from": "/images/ricevute/modifica-eliminazione/4.placeholder.svg",
            "checkpoint": "confirm-deletion-of-older-receipt"
          },
          {
            "from": "/images/ricevute/modifica-eliminazione/5.placeholder.svg",
            "checkpoint": "payment-unpaid-after-receipt-deletion"
          }
        ]
      }
    ],
    "outcome": {
      "field": "receipt_mutation",
      "expected": {
        "original_number": 7,
        "edited_number": 9,
        "date_preserved": true,
        "pdf_regenerated": true,
        "renderer_has_updated_number": true,
        "pdf_downloaded": true,
        "reader_update_status": 403,
        "reader_delete_status": 403,
        "deleted_lookup_status": 404,
        "payment_paid": false,
        "payment_invoice": null,
        "payment_amount": 25,
        "unpaid_visible_after_reload": true
      }
    },
    "fixture_profile": "receipts-edit-delete"
  },
  "courses-create-edit": {
    "version": 1,
    "script": "selfhost/tests/browser/manuale/courses.mjs",
    "prefix": "images/corsi/creazione-modifica/",
    "pages": [
      "docs/corsi.mdx"
    ],
    "sources": [
      "BE/application/models/courses_models.py",
      "BE/application/permissions_registry.py",
      "BE/application/views/course_views.py",
      "UI/src/components/Sidebar.svelte",
      "UI/src/routes.js",
      "UI/src/routes/association/course/CourseList.svelte",
      "UI/src/routes/association/course/add/AddCourse.svelte",
      "UI/src/routes/association/course/add/sections/Section1.svelte",
      "UI/src/routes/association/course/overview/OverviewCourse.svelte"
    ],
    "dependencies": [
      "selfhost/tests/browser/manuale/courses.mjs",
      "docs/manuale/core-legacy-authored-workflows.mjs"
    ],
    "checkpoints": [
      {
        "id": "filled-standard-course",
        "caption": "Dati di un nuovo corso Standard"
      },
      {
        "id": "saved-course-in-list",
        "caption": "Nuovo corso nell'elenco"
      },
      {
        "id": "edit-course-title",
        "caption": "Modifica del titolo di un corso"
      },
      {
        "id": "edited-course-persists-after-reload",
        "caption": "Titolo modificato dopo il ricaricamento"
      }
    ],
    "sections": [
      {
        "path": "docs/corsi.mdx",
        "id": "creare-un-corso",
        "title": "Creare un corso",
        "checkpoints": [
          "filled-standard-course",
          "saved-course-in-list"
        ],
        "images": [
          {
            "from": "/images/corsi/creazione-modifica/1.placeholder.svg",
            "checkpoint": "filled-standard-course"
          },
          {
            "from": "/images/corsi/creazione-modifica/2.placeholder.svg",
            "checkpoint": "saved-course-in-list"
          }
        ]
      },
      {
        "path": "docs/corsi.mdx",
        "id": "modificare-le-informazioni-del-corso",
        "title": "Modificare le informazioni del corso",
        "checkpoints": [
          "edit-course-title",
          "edited-course-persists-after-reload"
        ],
        "images": [
          {
            "from": "/images/corsi/creazione-modifica/3.placeholder.svg",
            "checkpoint": "edit-course-title"
          },
          {
            "from": "/images/corsi/creazione-modifica/4.placeholder.svg",
            "checkpoint": "edited-course-persists-after-reload"
          }
        ]
      }
    ],
    "outcome": {
      "field": "course_lifecycle",
      "expected": {
        "fee": 90,
        "original_title": "Yoga del mattino",
        "edited_title": "Yoga del mattino avanzato",
        "title_persisted": true,
        "fee_preserved": true,
        "reader_create_status": 403,
        "reader_update_status": 403,
        "denied_write_preserves_title": true
      }
    }
  }
});
