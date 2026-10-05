// Prepared source-grounded workflows; no capture or execution proof is fabricated here.
export const settingsPrintAuthoredWorkflows = Object.freeze({
    "settings-print": {
        "version": 1,
        "script": "selfhost/tests/browser/manuale/settings-print.mjs",
        "prefix": "images/impostazioni/stampe-reali/",
        "pages": [
            "docs/impostazioni.mdx"
        ],
        "sources": [
            "UI/src/components/Sidebar.svelte",
            "UI/src/routes.js",
            "UI/src/utils/Permissions.js",
            "UI/src/routes/accounting/payment/PaymentList.svelte",
            "UI/src/routes/accounting/payment/modals/AddEditModal.svelte",
            "UI/src/routes/accounting/payment/PaymentDrawer.svelte",
            "UI/src/routes/accounting/payment/partials/payment-overview.svelte",
            "UI/src/components/formBuilder/preview-blocks/smart-select-input.svelte",
            "UI/src/components/buttons/ApproveButton.svelte",
            "UI/src/components/buttons/EditButton.svelte",
            "BE/application/views/payment_views.py",
            "BE/application/serializers/payment_serializers.py",
            "BE/application/models/payment_models.py",
            "BE/application/models/user_models.py",
            "BE/application/models/invoices_models.py",
            "BE/application/services/invoice_service.py",
            "BE/application/permissions_registry.py",
            "UI/src/routes/accounting/receipts/ReceiptList.svelte",
            "UI/src/routes/accounting/receipts/invoiceActionState.js",
            "UI/src/components/modals/InvoicePreviewModal.svelte",
            "UI/src/routes/accounting/receipts/modals/ShareModal.svelte",
            "BE/application/views/invoice_views.py",
            "BE/application/serializers/invoice_serializers.py",
            "BE/application/printing_tasks.py",
            "BE/docmanager/views/printing_views.py",
            "BE/docmanager/views/document_view.py",
            "BE/application/utils/printing.py",
            "BE/docmanager/tasks.py",
            "BE/templates/document/application/invoice.html",
            "UI/src/routes/profile/ProfileMenu.svelte",
            "UI/src/utils/profileNavigation.js",
            "UI/src/routes/profile/sections/Account.svelte",
            "UI/src/routes/profile/sections/modals/SignatureModal.svelte",
            "UI/src/components/signature/SmoothSignature.svelte",
            "UI/src/components/formBuilder/preview-blocks/file-input.svelte",
            "UI/src/components/inputs/TipTapEditor.svelte",
            "UI/src/components/inputs/editorCustom.js",
            "UI/src/components/inputs/suggestion.js",
            "UI/src/components/inputs/mentionSuggestions.js",
            "UI/src/components/inputs/MentionList.svelte",
            "BE/application/views/profile_views.py",
            "BE/application/serializers/auth_serializers.py",
            "BE/application/models/utils.py",
            "BE/application/urls.py",
            "BE/templates/document/application/invoice_classic.html",
            "BE/templates/document/application/subscription.html",
            "BE/templates/document/application/subscription_classic.html",
            "BE/docmanager/urls.py",
            "BE/docmanager/models.py"
        ],
        "dependencies": [
            "UI/src/components/Sidebar.svelte",
            "UI/src/routes.js",
            "UI/src/utils/Permissions.js",
            "UI/src/routes/accounting/payment/PaymentList.svelte",
            "UI/src/routes/accounting/payment/modals/AddEditModal.svelte",
            "UI/src/routes/accounting/payment/PaymentDrawer.svelte",
            "UI/src/routes/accounting/payment/partials/payment-overview.svelte",
            "UI/src/components/formBuilder/preview-blocks/smart-select-input.svelte",
            "UI/src/components/buttons/ApproveButton.svelte",
            "UI/src/components/buttons/EditButton.svelte",
            "BE/application/views/payment_views.py",
            "BE/application/serializers/payment_serializers.py",
            "BE/application/models/payment_models.py",
            "BE/application/models/user_models.py",
            "BE/application/models/invoices_models.py",
            "BE/application/services/invoice_service.py",
            "BE/application/permissions_registry.py",
            "UI/src/routes/accounting/receipts/ReceiptList.svelte",
            "UI/src/routes/accounting/receipts/invoiceActionState.js",
            "UI/src/components/modals/InvoicePreviewModal.svelte",
            "UI/src/routes/accounting/receipts/modals/ShareModal.svelte",
            "BE/application/views/invoice_views.py",
            "BE/application/serializers/invoice_serializers.py",
            "BE/application/printing_tasks.py",
            "BE/docmanager/views/printing_views.py",
            "BE/docmanager/views/document_view.py",
            "BE/application/utils/printing.py",
            "BE/docmanager/tasks.py",
            "BE/templates/document/application/invoice.html",
            "UI/src/routes/profile/ProfileMenu.svelte",
            "UI/src/utils/profileNavigation.js",
            "UI/src/routes/profile/sections/Account.svelte",
            "UI/src/routes/profile/sections/modals/SignatureModal.svelte",
            "UI/src/components/signature/SmoothSignature.svelte",
            "UI/src/components/formBuilder/preview-blocks/file-input.svelte",
            "UI/src/components/inputs/TipTapEditor.svelte",
            "UI/src/components/inputs/editorCustom.js",
            "UI/src/components/inputs/suggestion.js",
            "UI/src/components/inputs/mentionSuggestions.js",
            "UI/src/components/inputs/MentionList.svelte",
            "BE/application/views/profile_views.py",
            "BE/application/serializers/auth_serializers.py",
            "BE/application/models/utils.py",
            "BE/application/urls.py",
            "BE/templates/document/application/invoice_classic.html",
            "BE/templates/document/application/subscription.html",
            "BE/templates/document/application/subscription_classic.html",
            "BE/docmanager/urls.py",
            "BE/docmanager/models.py",
            "selfhost/tests/browser/manuale/payments-sources.mjs",
            "selfhost/tests/browser/manuale/organization-access-sources.mjs",
            "selfhost/tests/browser/manuale/settings-print.mjs",
            "docs/manuale/settings-print-authored-workflows.mjs"
        ],
        "checkpoints": [
            {
                "id": "organization-print-fields-before-change",
                "caption": "Informazioni Account: firma del presidente e timbro prima della modifica."
            },
            {
                "id": "uploaded-print-assets-before-save",
                "caption": "Firma e timbro dimostrativi distinti, caricati attraverso i rispettivi campi."
            },
            {
                "id": "uploaded-print-assets-after-reload",
                "caption": "Immagini dimostrative salvate e presenti dopo la riapertura delle Informazioni Account."
            },
            {
                "id": "drawn-president-signature-before-accepting",
                "caption": "Scrivi firma: tratto dimostrativo disegnato nel riquadro, prima di Firma."
            },
            {
                "id": "drawn-signature-after-reload",
                "caption": "Firma disegnata salvata e riaperta; il timbro conserva la propria immagine."
            },
            {
                "id": "print-header-before-save",
                "caption": "Intestazione stampe compilata nel proprio editor prima di Salva."
            },
            {
                "id": "print-footer-before-save",
                "caption": "Piè di pagina stampe compilato nel secondo editor, separato dal Testo Extra Ricevute."
            },
            {
                "id": "print-header-after-reload",
                "caption": "Testo dell’intestazione salvato e riaperto."
            },
            {
                "id": "print-footer-after-reload",
                "caption": "Testo del piè di pagina salvato e riaperto."
            },
            {
                "id": "owned-print-payment-email-disabled",
                "caption": "Pagamento dimostrativo: nuova ricevuta richiesta e invio email disattivato."
            },
            {
                "id": "new-receipt-header-content",
                "caption": "Nuova stampa reale: intestazione e contenuto della ricevuta non si sovrappongono."
            },
            {
                "id": "new-receipt-footer-content",
                "caption": "Piè di pagina della nuova stampa, dopo il contenuto della ricevuta."
            },
            {
                "id": "real-new-receipt-pdf",
                "caption": "Anteprima del PDF reale appena generato, senza link o token di condivisione."
            },
            {
                "id": "blank-print-texts-after-reload",
                "caption": "Editor vuoti salvati e riaperti: i testi possono essere omessi dalle nuove stampe."
            },
            {
                "id": "blank-receipt-without-header-footer",
                "caption": "Seconda nuova stampa: intestazione e piè di pagina vuoti non vengono visualizzati."
            },
            {
                "id": "blank-receipt-pdf",
                "caption": "PDF reale della nuova ricevuta generata con i testi di stampa vuoti."
            },
            {
                "id": "print-assets-removed-after-reload",
                "caption": "Rimuovi firma e Rimuovi timbro: assenza delle immagini confermata dopo Salva e riapertura."
            },
            {
                "id": "document-templates-classic-before-save",
                "caption": "Modelli Ricevute e Iscrizione: selezioni Classico, prima di Salva."
            },
            {
                "id": "document-templates-classic-after-reload",
                "caption": "Modelli Classico salvati e selezionati dopo la ricarica."
            },
            {
                "id": "document-receipt-classic-renderer",
                "caption": "Stampa reale della ricevuta di Giulia con il modello Classico."
            },
            {
                "id": "document-subscription-classic-renderer",
                "caption": "Stampa reale dell’iscrizione di Giulia con il modello Classico."
            },
            {
                "id": "document-templates-standard-before-save",
                "caption": "Modelli Ricevute e Iscrizione: selezioni Standard, prima di Salva."
            },
            {
                "id": "document-templates-standard-after-reload",
                "caption": "Modelli Standard salvati e selezionati dopo la ricarica."
            },
            {
                "id": "document-receipt-standard-renderer",
                "caption": "Stampa reale della ricevuta di Giulia con il modello Standard."
            },
            {
                "id": "document-subscription-standard-renderer",
                "caption": "Stampa reale dell’iscrizione di Giulia con il modello Standard."
            },
            {
                "id": "original-print-settings-restored",
                "caption": "Configurazione iniziale ripristinata dopo la prova, con modelli documenti conservati."
            }
        ],
        "sections": [
            {
                "path": "docs/impostazioni.mdx",
                "id": "modelli-documenti",
                "title": "Modelli documenti",
                "checkpoints": [
                    "document-templates-classic-before-save",
                    "document-templates-classic-after-reload",
                    "document-receipt-classic-renderer",
                    "document-subscription-classic-renderer",
                    "document-templates-standard-before-save",
                    "document-templates-standard-after-reload",
                    "document-receipt-standard-renderer",
                    "document-subscription-standard-renderer"
                ],
                "images": [],
                "insert_images": [
                    {
                        "step": "Scegli il tipo di documento",
                        "checkpoint": "document-templates-classic-before-save"
                    },
                    {
                        "step": "Seleziona e salva il modello",
                        "checkpoint": "document-templates-classic-after-reload"
                    },
                    {
                        "step": "Controlla la stampa effettiva",
                        "checkpoint": "document-receipt-classic-renderer"
                    },
                    {
                        "step": "Controlla la stampa effettiva",
                        "checkpoint": "document-subscription-classic-renderer"
                    },
                    {
                        "step": "Ripeti il controllo dopo un cambio",
                        "checkpoint": "document-templates-standard-after-reload"
                    }
                ]
            },
            {
                "path": "docs/impostazioni.mdx",
                "id": "firma-del-presidente-e-timbro",
                "title": "Firma del presidente e timbro",
                "checkpoints": [
                    "organization-print-fields-before-change",
                    "uploaded-print-assets-before-save",
                    "uploaded-print-assets-after-reload",
                    "drawn-president-signature-before-accepting",
                    "drawn-signature-after-reload",
                    "new-receipt-header-content",
                    "new-receipt-footer-content",
                    "real-new-receipt-pdf",
                    "print-assets-removed-after-reload"
                ],
                "images": [
                    {
                        "from": "/images/impostazioni/configurazione/firma.placeholder.svg",
                        "checkpoint": "organization-print-fields-before-change"
                    }
                ],
                "insert_images": [
                    {
                        "step": "Carica o disegna la firma",
                        "checkpoint": "uploaded-print-assets-before-save"
                    },
                    {
                        "step": "Carica o disegna la firma",
                        "checkpoint": "drawn-president-signature-before-accepting"
                    },
                    {
                        "step": "Salva e controlla il documento",
                        "checkpoint": "drawn-signature-after-reload"
                    },
                    {
                        "step": "Salva e controlla il documento",
                        "checkpoint": "real-new-receipt-pdf"
                    },
                    {
                        "step": "Rimuovi firma e timbro",
                        "checkpoint": "print-assets-removed-after-reload"
                    }
                ]
            },
            {
                "path": "docs/impostazioni.mdx",
                "id": "intestazione-e-pie-di-pagina-stampe",
                "title": "Intestazione e piè di pagina stampe",
                "checkpoints": [
                    "print-header-before-save",
                    "print-footer-before-save",
                    "print-header-after-reload",
                    "print-footer-after-reload",
                    "new-receipt-header-content",
                    "new-receipt-footer-content",
                    "real-new-receipt-pdf",
                    "blank-print-texts-after-reload",
                    "blank-receipt-without-header-footer",
                    "blank-receipt-pdf"
                ],
                "images": [
                    {
                        "from": "/images/impostazioni/configurazione/stampe.placeholder.svg",
                        "checkpoint": "print-header-before-save"
                    }
                ],
                "insert_images": [
                    {
                        "step": "Compila i testi",
                        "checkpoint": "print-footer-before-save"
                    },
                    {
                        "step": "Salva la pagina e riaprila",
                        "checkpoint": "print-header-after-reload"
                    },
                    {
                        "step": "Salva la pagina e riaprila",
                        "checkpoint": "print-footer-after-reload"
                    },
                    {
                        "step": "Controlla una nuova stampa",
                        "checkpoint": "real-new-receipt-pdf"
                    },
                    {
                        "step": "Controlla una nuova stampa",
                        "checkpoint": "blank-receipt-pdf"
                    }
                ]
            }
        ],
        "outcome": {
            "field": "settings_print_authored_workflow",
            "expected": {
                "uploaded_signature_saved_and_reopened": true,
                "stamp_saved_and_reopened": true,
                "drawn_signature_saved_and_reopened": true,
                "signature_and_stamp_removed_after_reopen": true,
                "header_saved_and_reopened": true,
                "footer_saved_and_reopened": true,
                "actual_renderer_has_header_and_footer": true,
                "renderer_texts_do_not_overlap_content": true,
                "actual_stock_receipt_does_not_insert_stored_signature_or_stamp": true,
                "actual_new_pdf_downloaded": true,
                "actual_new_pdf_previewed": true,
                "blank_header_and_footer_omitted_from_new_print": true,
                "blank_print_pdf_downloaded_and_previewed": true,
                "earlier_pdf_preserved_after_settings_change": true,
                "receipt_email_disabled": true,
                "reader_profile_write_status": 403,
                "reader_denial_preserves_configuration": true,
                "document_template_selections_preserved": true,
                "document_templates_saved_and_reopened": true,
                "both_actual_receipt_templates_rendered": true,
                "both_actual_subscription_templates_rendered": true,
                "template_changes_preserve_earlier_pdf": true,
                "reader_template_write_denied": true,
                "original_configuration_restored": true,
                "fixture_payments_preserved": true,
                "owned_payments_receipts_and_documents_removed": true
            }
        }
    },
    "settings-signup-checkout": {
        "version": 1,
        "script": "selfhost/tests/browser/manuale/settings-signup-checkout.mjs",
        "prefix": "images/impostazioni/moduli-checkout-reali/",
        "pages": [
            "docs/impostazioni.mdx",
            "tutorials/come-creare-moduli-iscrizione-personalizzati.mdx"
        ],
        "sources": [
            "UI/src/routes/association/Members/subscription/partials/MultipleQuotesSubscription.svelte",
            "UI/src/routes/association/Members/subscription/share/link-share-header.svelte",
            "BE/application/urls.py",
            "UI/src/routes/profile/sections/Settings.svelte",
            "UI/src/routes/association/archive/TemplatesList.svelte",
            "UI/src/routes/association/archive/detail/partials/template-form.svelte",
            "UI/src/routes/association/Members/detail/sections/Cloud.svelte",
            "UI/src/routes/association/archive/modals/GenerateFromTemplateModal.svelte",
            "BE/application/views/archive_views.py",
            "UI/src/components/Sidebar.svelte",
            "UI/src/routes.js",
            "UI/src/utils/Permissions.js",
            "UI/src/routes/profile/ProfileMenu.svelte",
            "UI/src/routes/profile/sections/Account.svelte",
            "UI/src/routes/subscribe/Subscribe.svelte",
            "UI/src/routes/subscribe/wizard/Step0.svelte",
            "UI/src/routes/subscribe/wizard/Step1.svelte",
            "UI/src/routes/subscribe/wizard/Step1minor.svelte",
            "UI/src/routes/subscribe/wizard/Step2.svelte",
            "UI/src/routes/association/Members/add/AddMemberDrawer.svelte",
            "UI/src/routes/association/Members/add/AddMember.svelte",
            "UI/src/routes/stripe/Checkout.svelte",
            "UI/src/routes/stripe/CartCheckout.svelte",
            "UI/src/components/inputs/TipTapEditor.svelte",
            "BE/application/views/profile_views.py",
            "BE/application/views/stripe_views.py",
            "BE/application/serializers/auth_serializers.py",
            "BE/application/models/user_models.py",
            "BE/application/utils/stripe_utils.py",
            "BE/application/permissions_registry.py",
            "selfhost/tests/browser/manuale/organization-access-sources.mjs",
            "selfhost/tests/browser/playwright.manual.config.mjs",
            "UI/src/routes/subscribe/wizard/Step3.svelte",
            "UI/src/routes/subscribe/wizard/Step4.svelte",
            "UI/src/routes/subscribe/wizard/partials/additional-fields.svelte",
            "UI/src/routes/association/Members/subscription/Template.svelte",
            "UI/src/routes/association/Members/subscription/partials/TypesModule.svelte",
            "UI/src/routes/association/Members/subscription/partials/QuoteIscrizione.svelte",
            "UI/src/routes/association/Members/subscription/partials/ModuleSections.svelte",
            "UI/src/routes/association/Members/subscription/partials/AdditionalFields.svelte",
            "UI/src/routes/association/Members/MembersBook.svelte",
            "UI/src/components/modals/ShareModuleSubscriptionLink.svelte",
            "UI/src/components/formBuilder/composer-preview.svelte",
            "UI/src/components/formBuilder/composer-sidebar.svelte",
            "UI/src/components/formBuilder/inputs.js",
            "UI/src/components/formBuilder/module-render.svelte",
            "UI/src/components/formBuilder/preview-blocks/text-input.svelte",
            "UI/src/components/buttons/GenerateTaxCodeButton.svelte",
            "UI/src/components/signature/SmoothSignature.svelte",
            "BE/application/views/subscriptions_views.py",
            "BE/application/views/search_views.py",
            "BE/application/serializers/subscriptions_serializers.py",
            "BE/application/utils/subscriptions_utils.py",
            "BE/application/models/subscriptions_models.py",
            "BE/application/services/subscription_service.py",
            "BE/docmanager/views/printing_views.py",
            "BE/docmanager/urls.py",
            "BE/templates/document/application/subscription.html",
            "BE/application/management/commands/seed_manuale.py"
        ],
        "dependencies": [
            "UI/src/components/Sidebar.svelte",
            "UI/src/routes.js",
            "UI/src/utils/Permissions.js",
            "UI/src/routes/profile/ProfileMenu.svelte",
            "UI/src/routes/profile/sections/Account.svelte",
            "UI/src/routes/subscribe/Subscribe.svelte",
            "UI/src/routes/subscribe/wizard/Step0.svelte",
            "UI/src/routes/subscribe/wizard/Step1.svelte",
            "UI/src/routes/subscribe/wizard/Step1minor.svelte",
            "UI/src/routes/subscribe/wizard/Step2.svelte",
            "UI/src/routes/association/Members/add/AddMemberDrawer.svelte",
            "UI/src/routes/association/Members/add/AddMember.svelte",
            "UI/src/routes/stripe/Checkout.svelte",
            "UI/src/routes/stripe/CartCheckout.svelte",
            "UI/src/components/inputs/TipTapEditor.svelte",
            "BE/application/views/profile_views.py",
            "BE/application/views/stripe_views.py",
            "BE/application/serializers/auth_serializers.py",
            "BE/application/models/user_models.py",
            "BE/application/utils/stripe_utils.py",
            "BE/application/permissions_registry.py",
            "selfhost/tests/browser/manuale/organization-access-sources.mjs",
            "selfhost/tests/browser/playwright.manual.config.mjs",
            "selfhost/tests/browser/manuale/settings-signup-checkout.mjs",
            "docs/manuale/settings-print-authored-workflows.mjs",
            "UI/src/routes/subscribe/wizard/Step3.svelte",
            "UI/src/routes/subscribe/wizard/Step4.svelte",
            "UI/src/routes/subscribe/wizard/partials/additional-fields.svelte",
            "UI/src/routes/association/Members/subscription/Template.svelte",
            "UI/src/routes/association/Members/subscription/partials/TypesModule.svelte",
            "UI/src/routes/association/Members/subscription/partials/QuoteIscrizione.svelte",
            "UI/src/routes/association/Members/subscription/partials/ModuleSections.svelte",
            "UI/src/routes/association/Members/subscription/partials/AdditionalFields.svelte",
            "UI/src/routes/association/Members/MembersBook.svelte",
            "UI/src/components/modals/ShareModuleSubscriptionLink.svelte",
            "UI/src/components/formBuilder/composer-preview.svelte",
            "UI/src/components/formBuilder/composer-sidebar.svelte",
            "UI/src/components/formBuilder/inputs.js",
            "UI/src/components/formBuilder/module-render.svelte",
            "UI/src/components/formBuilder/preview-blocks/text-input.svelte",
            "UI/src/components/buttons/GenerateTaxCodeButton.svelte",
            "UI/src/components/signature/SmoothSignature.svelte",
            "BE/application/views/subscriptions_views.py",
            "BE/application/views/search_views.py",
            "BE/application/serializers/subscriptions_serializers.py",
            "BE/application/utils/subscriptions_utils.py",
            "BE/application/models/subscriptions_models.py",
            "BE/application/services/subscription_service.py",
            "BE/docmanager/views/printing_views.py",
            "BE/docmanager/urls.py",
            "BE/templates/document/application/subscription.html",
            "BE/application/management/commands/seed_manuale.py"
        ],
        "checkpoints": [
            {
                "id": "signup-requirements-before-save",
                "caption": "Cinque obblighi nei moduli autonomi, prima di Salva."
            },
            {
                "id": "signup-requirements-after-reload",
                "caption": "Obblighi salvati e presenti dopo la riapertura delle Informazioni Account."
            },
            {
                "id": "signup-module-type-before-save",
                "caption": "Modulo Iscrizione: sola tipologia Socio e Tesserato, prima di Salva."
            },
            {
                "id": "signup-simple-fee-before-save",
                "caption": "Quote semplici: 30 euro associativi e zero tesseramento, prima di Salva."
            },
            {
                "id": "signup-added-clause-before-save",
                "caption": "Titolo, testo e visibilità della sezione Materiale per allenamento."
            },
            {
                "id": "signup-optional-field-before-save",
                "caption": "Campo Taglia maglietta con etichetta, aiuto e obbligatorietà disattivata."
            },
            {
                "id": "signup-module-type-after-reload",
                "caption": "La sola tipologia Socio e Tesserato è conservata dopo la ricarica."
            },
            {
                "id": "signup-simple-fee-after-reload",
                "caption": "Quota associativa di 30 euro e tesseramento a zero salvati e riaperti."
            },
            {
                "id": "signup-added-clause-after-reload",
                "caption": "Sezione Materiale per allenamento salvata, con visibilità per Socio e Tesserato."
            },
            {
                "id": "signup-optional-field-after-reload",
                "caption": "Proprietà del campo Taglia maglietta facoltativo conservate dopo la ricarica."
            },
            {
                "id": "signup-required-custom-field-before-save",
                "caption": "Variante obbligatoria del campo Taglia maglietta, prima di Salva."
            },
            {
                "id": "signup-required-custom-field-after-reload",
                "caption": "Campo Taglia maglietta obbligatorio salvato e riaperto."
            },
            {
                "id": "signup-current-module-share-dialog",
                "caption": "Link corrente e QR dimostrativi mascherati nel riquadro Condividi link iscrizioni."
            },
            {
                "id": "signup-current-module-real-preview",
                "caption": "Apri mostra il modulo reale Aurora con la sola tipologia Socio e Tesserato."
            },
            {
                "id": "public-signup-required-fields",
                "caption": "Modulo pubblico senza account: recapiti richiesti alla persona che si iscrive."
            },
            {
                "id": "public-signup-empty-required-fields",
                "caption": "Modulo pubblico: errori per telefono ed email dell’atleta lasciati vuoti."
            },
            {
                "id": "public-signup-minor-required-fields",
                "caption": "Modulo pubblico del minorenne: recapiti del tutore richiesti."
            },
            {
                "id": "public-signup-empty-custom-field-blocks",
                "caption": "Il campo aggiuntivo obbligatorio lasciato vuoto blocca il passaggio successivo."
            },
            {
                "id": "public-signup-valid-profile-and-custom-field",
                "caption": "Dati dimostrativi di Elisa Ferri e Taglia maglietta M nel modulo autonomo."
            },
            {
                "id": "public-signup-required-signature-blocks",
                "caption": "Clausola configurata visibile; Continua disabilitato senza la firma obbligatoria."
            },
            {
                "id": "public-signup-cleared-signature-blocks",
                "caption": "Cancella firma riporta Continua allo stato disabilitato."
            },
            {
                "id": "public-signup-fictional-signature",
                "caption": "Tratto dimostrativo inserito nel canvas prima di proseguire."
            },
            {
                "id": "public-signup-summary-before-submit",
                "caption": "Riepilogo della richiesta: firma presente, quota associativa 30 euro e nessun certificato allegato."
            },
            {
                "id": "public-signup-pending-request-after-reload",
                "caption": "Elisa Ferri salvata come richiesta firmata In attesa, dopo la ricarica."
            },
            {
                "id": "public-signup-stored-signature-in-renderer",
                "caption": "Firma salvata nella stampa reale dell’iscrizione di Elisa Ferri."
            },
            {
                "id": "checkout-information-before-save",
                "caption": "Testo informativo Checkout prima di Salva, senza richiedere un pagamento."
            },
            {
                "id": "checkout-information-after-reload",
                "caption": "Testo Checkout salvato e riaperto; il pagamento su Stripe resta da verificare."
            }
        ],
        "sections": [
            {
                "path": "docs/impostazioni.mdx",
                "id": "moduli-d-iscrizione",
                "title": "Moduli d'iscrizione",
                "checkpoints": [
                    "signup-requirements-before-save",
                    "signup-requirements-after-reload",
                    "public-signup-required-fields",
                    "public-signup-empty-required-fields",
                    "public-signup-minor-required-fields",
                    "public-signup-empty-custom-field-blocks",
                    "public-signup-valid-profile-and-custom-field",
                    "public-signup-required-signature-blocks",
                    "public-signup-cleared-signature-blocks",
                    "public-signup-fictional-signature",
                    "public-signup-summary-before-submit",
                    "public-signup-pending-request-after-reload",
                    "public-signup-stored-signature-in-renderer"
                ],
                "images": [],
                "insert_images": [
                    {
                        "step": "Scegli gli obblighi necessari",
                        "checkpoint": "signup-requirements-before-save"
                    },
                    {
                        "step": "Salva e riapri le impostazioni",
                        "checkpoint": "signup-requirements-after-reload"
                    },
                    {
                        "step": "Prova il modulo condiviso",
                        "checkpoint": "public-signup-empty-required-fields"
                    },
                    {
                        "step": "Prova il modulo condiviso",
                        "checkpoint": "public-signup-minor-required-fields"
                    },
                    {
                        "step": "Controlla il passaggio della firma",
                        "checkpoint": "public-signup-required-signature-blocks"
                    },
                    {
                        "step": "Controlla il passaggio della firma",
                        "checkpoint": "public-signup-fictional-signature"
                    },
                    {
                        "step": "Concludi e verifica la richiesta",
                        "checkpoint": "public-signup-summary-before-submit"
                    },
                    {
                        "step": "Concludi e verifica la richiesta",
                        "checkpoint": "public-signup-pending-request-after-reload"
                    }
                ]
            },
            {
                "path": "tutorials/come-creare-moduli-iscrizione-personalizzati.mdx",
                "id": "configurare-il-modulo-d-iscrizione",
                "title": "Configurare il modulo d'iscrizione",
                "checkpoints": [
                    "signup-module-type-before-save",
                    "signup-module-type-after-reload",
                    "signup-current-module-real-preview"
                ],
                "images": [],
                "insert_images": [
                    {
                        "step": "Scegli le tipologie da raccogliere",
                        "checkpoint": "signup-module-type-before-save"
                    },
                    {
                        "step": "Salva e ricontrolla",
                        "checkpoint": "signup-module-type-after-reload"
                    },
                    {
                        "step": "Salva e ricontrolla",
                        "checkpoint": "signup-current-module-real-preview"
                    }
                ]
            },
            {
                "path": "tutorials/come-creare-moduli-iscrizione-personalizzati.mdx",
                "id": "cosa-scegliere-per-le-iscrizioni",
                "title": "Cosa scegliere per le iscrizioni?",
                "checkpoints": [
                    "signup-module-type-before-save",
                    "signup-module-type-after-reload",
                    "signup-current-module-real-preview"
                ],
                "images": [],
                "insert_images": [
                    {
                        "step": "Scegli le tipologie da raccogliere",
                        "checkpoint": "signup-module-type-before-save"
                    },
                    {
                        "step": "Salva e ricontrolla",
                        "checkpoint": "signup-module-type-after-reload"
                    },
                    {
                        "step": "Salva e ricontrolla",
                        "checkpoint": "signup-current-module-real-preview"
                    },
                    {
                        "step": "Confronta il percorso pubblico",
                        "checkpoint": "signup-current-module-real-preview"
                    }
                ]
            },
            {
                "path": "tutorials/come-creare-moduli-iscrizione-personalizzati.mdx",
                "id": "impostare-le-quote",
                "title": "Impostare le quote",
                "checkpoints": [
                    "signup-simple-fee-before-save",
                    "signup-simple-fee-after-reload",
                    "public-signup-summary-before-submit",
                    "public-signup-pending-request-after-reload"
                ],
                "images": [],
                "insert_images": [
                    {
                        "step": "Imposta la quota associativa semplice",
                        "checkpoint": "signup-simple-fee-before-save"
                    },
                    {
                        "step": "Verifica gli importi conservati",
                        "checkpoint": "signup-simple-fee-after-reload"
                    },
                    {
                        "step": "Verifica gli importi conservati",
                        "checkpoint": "public-signup-summary-before-submit"
                    },
                    {
                        "step": "Controlla lo stato del pagamento",
                        "checkpoint": "public-signup-pending-request-after-reload"
                    }
                ]
            },
            {
                "path": "tutorials/come-creare-moduli-iscrizione-personalizzati.mdx",
                "id": "visualizzare-un-anteprima-del-modulo-d-iscrizione-digitale",
                "title": "Visualizzare un'anteprima del modulo d'iscrizione digitale",
                "checkpoints": [
                    "signup-module-type-after-reload",
                    "signup-current-module-share-dialog",
                    "signup-current-module-real-preview",
                    "public-signup-empty-custom-field-blocks"
                ],
                "images": [],
                "insert_images": [
                    {
                        "step": "Salva la configurazione",
                        "checkpoint": "signup-module-type-after-reload"
                    },
                    {
                        "step": "Apri il link corrente",
                        "checkpoint": "signup-current-module-share-dialog"
                    },
                    {
                        "step": "Osserva la nuova scheda",
                        "checkpoint": "signup-current-module-real-preview"
                    },
                    {
                        "step": "Controlla i dati richiesti",
                        "checkpoint": "public-signup-empty-custom-field-blocks"
                    }
                ]
            },
            {
                "path": "tutorials/come-creare-moduli-iscrizione-personalizzati.mdx",
                "id": "personalizzare-il-modulo-d-iscrizione",
                "title": "Personalizzare il modulo d'iscrizione",
                "checkpoints": [
                    "signup-added-clause-before-save",
                    "signup-optional-field-before-save",
                    "signup-added-clause-after-reload",
                    "signup-required-custom-field-after-reload",
                    "public-signup-valid-profile-and-custom-field",
                    "public-signup-summary-before-submit"
                ],
                "images": [],
                "insert_images": [
                    {
                        "step": "Aggiungi titolo e testo",
                        "checkpoint": "signup-added-clause-before-save"
                    },
                    {
                        "step": "Compila le proprietà del campo",
                        "checkpoint": "signup-optional-field-before-save"
                    },
                    {
                        "step": "Salva entrambe le parti",
                        "checkpoint": "signup-added-clause-after-reload"
                    },
                    {
                        "step": "Salva entrambe le parti",
                        "checkpoint": "signup-required-custom-field-after-reload"
                    },
                    {
                        "step": "Controlla la compilazione reale",
                        "checkpoint": "public-signup-valid-profile-and-custom-field"
                    },
                    {
                        "step": "Controlla la compilazione reale",
                        "checkpoint": "public-signup-summary-before-submit"
                    }
                ]
            },
            {
                "path": "tutorials/come-creare-moduli-iscrizione-personalizzati.mdx",
                "id": "sezioni-del-modulo-d-iscrizione",
                "title": "Sezioni del modulo d'iscrizione",
                "checkpoints": [
                    "signup-added-clause-before-save",
                    "signup-added-clause-after-reload",
                    "public-signup-required-signature-blocks"
                ],
                "images": [],
                "insert_images": [
                    {
                        "step": "Aggiungi titolo e testo",
                        "checkpoint": "signup-added-clause-before-save"
                    },
                    {
                        "step": "Salva e riapri la sezione",
                        "checkpoint": "signup-added-clause-after-reload"
                    },
                    {
                        "step": "Controlla la clausola prima della firma",
                        "checkpoint": "public-signup-required-signature-blocks"
                    }
                ]
            },
            {
                "path": "tutorials/come-creare-moduli-iscrizione-personalizzati.mdx",
                "id": "campi-aggiuntivi",
                "title": "Campi aggiuntivi",
                "checkpoints": [
                    "signup-optional-field-before-save",
                    "signup-optional-field-after-reload",
                    "signup-required-custom-field-after-reload",
                    "public-signup-empty-custom-field-blocks",
                    "public-signup-valid-profile-and-custom-field"
                ],
                "images": [],
                "insert_images": [
                    {
                        "step": "Compila le proprietà del campo",
                        "checkpoint": "signup-optional-field-before-save"
                    },
                    {
                        "step": "Salva e scegli l’obbligatorietà",
                        "checkpoint": "signup-optional-field-after-reload"
                    },
                    {
                        "step": "Salva e scegli l’obbligatorietà",
                        "checkpoint": "signup-required-custom-field-after-reload"
                    },
                    {
                        "step": "Prova il campo nel modulo pubblico",
                        "checkpoint": "public-signup-empty-custom-field-blocks"
                    },
                    {
                        "step": "Prova il campo nel modulo pubblico",
                        "checkpoint": "public-signup-valid-profile-and-custom-field"
                    }
                ]
            },
            {
                "path": "tutorials/come-creare-moduli-iscrizione-personalizzati.mdx",
                "id": "conclusione",
                "title": "Conclusione",
                "checkpoints": [
                    "signup-current-module-real-preview",
                    "public-signup-summary-before-submit",
                    "public-signup-pending-request-after-reload",
                    "public-signup-stored-signature-in-renderer"
                ],
                "images": [],
                "insert_images": [
                    {
                        "step": "Rivedi configurazione e accesso",
                        "checkpoint": "signup-current-module-real-preview"
                    },
                    {
                        "step": "Rileggi prima di inviare",
                        "checkpoint": "public-signup-summary-before-submit"
                    },
                    {
                        "step": "Controlla il risultato nell’associazione",
                        "checkpoint": "public-signup-pending-request-after-reload"
                    },
                    {
                        "step": "Controlla il risultato nell’associazione",
                        "checkpoint": "public-signup-stored-signature-in-renderer"
                    }
                ]
            },
            {
                "path": "faq/come-condividere-il-link-iscrizioni.mdx",
                "id": "condividere-il-link-per-le-iscrizioni",
                "title": "Condividere il link per le iscrizioni",
                "checkpoints": [
                    "signup-current-module-share-dialog",
                    "signup-current-module-real-preview"
                ],
                "images": [
                    {
                        "from": "/images/impostazioni/moduli-checkout-reali/13.placeholder.svg",
                        "checkpoint": "signup-current-module-share-dialog"
                    },
                    {
                        "from": "/images/impostazioni/moduli-checkout-reali/14.placeholder.svg",
                        "checkpoint": "signup-current-module-real-preview"
                    }
                ],
                "insert_images": []
            },
            {
                "path": "faq/come-condividere-il-link-iscrizioni.mdx",
                "id": "dove-trovo-il-link-di-iscrizione",
                "title": "Dove trovo il link di iscrizione?",
                "checkpoints": [
                    "signup-current-module-share-dialog",
                    "signup-current-module-real-preview"
                ],
                "images": [
                    {
                        "from": "/images/impostazioni/moduli-checkout-reali/13.placeholder.svg",
                        "checkpoint": "signup-current-module-share-dialog"
                    },
                    {
                        "from": "/images/impostazioni/moduli-checkout-reali/14.placeholder.svg",
                        "checkpoint": "signup-current-module-real-preview"
                    }
                ],
                "insert_images": []
            },
            {
                "path": "faq/come-condividere-il-link-iscrizioni.mdx",
                "id": "modalita-di-condivisione",
                "title": "Modalità di condivisione",
                "checkpoints": [
                    "signup-current-module-share-dialog",
                    "signup-current-module-real-preview"
                ],
                "images": [
                    {
                        "from": "/images/impostazioni/moduli-checkout-reali/13.placeholder.svg",
                        "checkpoint": "signup-current-module-share-dialog"
                    },
                    {
                        "from": "/images/impostazioni/moduli-checkout-reali/14.placeholder.svg",
                        "checkpoint": "signup-current-module-real-preview"
                    }
                ],
                "insert_images": []
            },
            {
                "path": "faq/come-condividere-il-link-iscrizioni.mdx",
                "id": "1-copia-il-link",
                "title": "1. Copia il link",
                "checkpoints": [
                    "signup-current-module-share-dialog",
                    "signup-current-module-real-preview"
                ],
                "images": [
                    {
                        "from": "/images/impostazioni/moduli-checkout-reali/13.placeholder.svg",
                        "checkpoint": "signup-current-module-share-dialog"
                    },
                    {
                        "from": "/images/impostazioni/moduli-checkout-reali/14.placeholder.svg",
                        "checkpoint": "signup-current-module-real-preview"
                    }
                ],
                "insert_images": []
            },
            {
                "path": "faq/come-condividere-il-link-iscrizioni.mdx",
                "id": "cosa-vede-chi-apre-il-link",
                "title": "Cosa vede chi apre il link?",
                "checkpoints": [
                    "public-signup-valid-profile-and-custom-field",
                    "public-signup-fictional-signature",
                    "public-signup-pending-request-after-reload"
                ],
                "images": [
                    {
                        "from": "/images/impostazioni/moduli-checkout-reali/19.placeholder.svg",
                        "checkpoint": "public-signup-valid-profile-and-custom-field"
                    },
                    {
                        "from": "/images/impostazioni/moduli-checkout-reali/22.placeholder.svg",
                        "checkpoint": "public-signup-fictional-signature"
                    },
                    {
                        "from": "/images/impostazioni/moduli-checkout-reali/24.placeholder.svg",
                        "checkpoint": "public-signup-pending-request-after-reload"
                    }
                ],
                "insert_images": []
            },
            {
                "path": "tutorials/come-creare-moduli-iscrizione-personalizzati.mdx",
                "id": "indice",
                "title": "Indice",
                "checkpoints": [
                    "signup-current-module-share-dialog"
                ],
                "images": [
                    {
                        "from": "/images/impostazioni/moduli-checkout-reali/13.placeholder.svg",
                        "checkpoint": "signup-current-module-share-dialog"
                    }
                ],
                "insert_images": []
            }
        ],
        "outcome": {
            "field": "settings_signup_checkout_authored_workflow",
            "expected": {
                "five_requirements_saved_reopened": true,
                "public_configuration_matches": true,
                "adult_contact_validation": true,
                "minor_contact_validation": true,
                "module_type_saved_reopened": true,
                "simple_fee_saved_reopened": true,
                "added_clause_saved_reopened": true,
                "optional_custom_field_saved_reopened": true,
                "required_custom_field_saved_reopened": true,
                "public_module_matches_saved_configuration": true,
                "current_link_copied_and_opened": true,
                "required_custom_field_blocks": true,
                "required_signature_blocks": true,
                "cleared_signature_blocks_again": true,
                "drawn_signature_enables_continue": true,
                "public_signup_persisted": true,
                "pending_signed_status": 2,
                "unpaid_signup_amount": 30,
                "custom_field_value_persisted": true,
                "stored_signature_pixels_match": true,
                "no_new_account_created": true,
                "baseline_records_preserved": true,
                "owned_registration_and_unpaid_quota_removed": true,
                "checkout_text_saved_reopened": true,
                "reader_write_status": 403,
                "reader_template_write_status": 403,
                "reader_signup_write_status": 403,
                "reader_denial_preserves_configuration": true,
                "original_configuration_restored": true,
                "business_records_preserved": true
            }
        }
    }
});
