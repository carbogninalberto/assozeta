// Prepared source-grounded workflows; no capture or execution proof is fabricated here.
export const medicalFollowupsAuthoredWorkflows = Object.freeze({
    "medical-followups": {
        "version": 1,
        "script": "selfhost/tests/browser/manuale/medical-followups.mjs",
        "prefix": "images/certificati/controlli-reali/",
        "pages": [
            "tutorials/come-gestire-certificati-medici.mdx",
            "faq/come-gestire-i-certificati-medici.mdx"
        ],
        "sources": [
            "UI/src/components/Sidebar.svelte",
            "UI/src/routes.js",
            "UI/src/utils/Permissions.js",
            "UI/src/utils/enumUtils.js",
            "UI/src/store/stores.js",
            "UI/src/routes/association/Members/MembersList.svelte",
            "UI/src/routes/association/Members/add/AddMember.svelte",
            "UI/src/routes/association/Members/add/sections/Section1.svelte",
            "UI/src/routes/association/Members/add/sections/Section2.svelte",
            "UI/src/routes/association/Members/add/sections/Section3.svelte",
            "UI/src/routes/association/Members/add/sections/Section4.svelte",
            "UI/src/routes/association/Members/add/sections/Section6.svelte",
            "UI/src/components/signature/SmoothSignature.svelte",
            "UI/src/components/buttons/GenerateTaxCodeButton.svelte",
            "BE/application/views/subscriptions_views.py",
            "BE/application/utils/subscriptions_utils.py",
            "BE/application/serializers/user_serializers.py",
            "BE/application/serializers/subscriptions_serializers.py",
            "BE/application/models/subscriptions_models.py",
            "BE/application/models/user_models.py",
            "BE/application/models/payment_models.py",
            "BE/application/permissions_registry.py",
            "UI/src/routes/association/Members/detail/DetailDrawer.svelte",
            "UI/src/routes/association/Members/detail/Detail.svelte",
            "UI/src/routes/association/Members/detail/sections/Info.svelte",
            "UI/src/components/drawer/basic-drawer.svelte",
            "BE/application/services/subscription_service.py",
            "UI/src/routes/association/Members/detail/sections/Medical.svelte",
            "UI/src/routes/association/Members/detail/sections/modals/AddMedicalCertificate.svelte",
            "UI/src/routes/association/Members/detail/sections/modals/medicalCertificateUpload.js",
            "UI/src/components/formBuilder/preview-blocks/datepicker-input.svelte",
            "UI/src/components/inputs/DateInput.svelte",
            "UI/src/shim/dropzone.js",
            "UI/src/utils/ApiMiddleware.js",
            "UI/src/routes/dashboard/Dashboard.svelte",
            "UI/src/routes/dashboard/EmptyDasbhoard.svelte",
            "UI/src/components/modals/WidgetModal.svelte",
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
            "BE/application/models/courses_models.py",
            "BE/application/models/carnet_models.py",
            "BE/application/utils/api_utils.py",
            "BE/application/impersonation.py",
            "BE/core/middleware.py",
            "UI/src/routes/profile/ProfileMenu.svelte",
            "UI/src/utils/profileNavigation.js",
            "UI/src/routes/profile/sections/Settings.svelte",
            "UI/src/components/filters/CheckboxFilters.svelte",
            "UI/src/components/filters/QueryFilter.svelte",
            "UI/src/components/filters/QueryFilterTag.svelte",
            "UI/src/components/filters/filterState.js",
            "UI/src/components/modals/PrintingModal.svelte",
            "UI/src/components/modals/BasicModal.svelte",
            "BE/application/utils/printing.py",
            "BE/application/utils/excel_utils.py",
            "BE/application/views/printing_views.py",
            "BE/docmanager/download_tokens.py",
            "BE/docmanager/views/document_view.py",
            "BE/docmanager/urls.py",
            "BE/docmanager/models.py",
            "BE/application/urls.py",
            "BE/application/tasks.py",
            "UI/src/routes/subscribe-multiple/wizard/Step3.svelte",
            "UI/src/routes/subscribe-multiple/SubscribeMultiple.svelte"
        ],
        "dependencies": [
            "UI/src/components/Sidebar.svelte",
            "UI/src/routes.js",
            "UI/src/utils/Permissions.js",
            "UI/src/utils/enumUtils.js",
            "UI/src/store/stores.js",
            "UI/src/routes/association/Members/MembersList.svelte",
            "UI/src/routes/association/Members/add/AddMember.svelte",
            "UI/src/routes/association/Members/add/sections/Section1.svelte",
            "UI/src/routes/association/Members/add/sections/Section2.svelte",
            "UI/src/routes/association/Members/add/sections/Section3.svelte",
            "UI/src/routes/association/Members/add/sections/Section4.svelte",
            "UI/src/routes/association/Members/add/sections/Section6.svelte",
            "UI/src/components/signature/SmoothSignature.svelte",
            "UI/src/components/buttons/GenerateTaxCodeButton.svelte",
            "BE/application/views/subscriptions_views.py",
            "BE/application/utils/subscriptions_utils.py",
            "BE/application/serializers/user_serializers.py",
            "BE/application/serializers/subscriptions_serializers.py",
            "BE/application/models/subscriptions_models.py",
            "BE/application/models/user_models.py",
            "BE/application/models/payment_models.py",
            "BE/application/permissions_registry.py",
            "UI/src/routes/association/Members/detail/DetailDrawer.svelte",
            "UI/src/routes/association/Members/detail/Detail.svelte",
            "UI/src/routes/association/Members/detail/sections/Info.svelte",
            "UI/src/components/drawer/basic-drawer.svelte",
            "BE/application/services/subscription_service.py",
            "UI/src/routes/association/Members/detail/sections/Medical.svelte",
            "UI/src/routes/association/Members/detail/sections/modals/AddMedicalCertificate.svelte",
            "UI/src/routes/association/Members/detail/sections/modals/medicalCertificateUpload.js",
            "UI/src/components/formBuilder/preview-blocks/datepicker-input.svelte",
            "UI/src/components/inputs/DateInput.svelte",
            "UI/src/shim/dropzone.js",
            "UI/src/utils/ApiMiddleware.js",
            "UI/src/routes/dashboard/Dashboard.svelte",
            "UI/src/routes/dashboard/EmptyDasbhoard.svelte",
            "UI/src/components/modals/WidgetModal.svelte",
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
            "BE/application/models/courses_models.py",
            "BE/application/models/carnet_models.py",
            "BE/application/utils/api_utils.py",
            "BE/application/impersonation.py",
            "BE/core/middleware.py",
            "UI/src/routes/profile/ProfileMenu.svelte",
            "UI/src/utils/profileNavigation.js",
            "UI/src/routes/profile/sections/Settings.svelte",
            "UI/src/components/filters/CheckboxFilters.svelte",
            "UI/src/components/filters/QueryFilter.svelte",
            "UI/src/components/filters/QueryFilterTag.svelte",
            "UI/src/components/filters/filterState.js",
            "UI/src/components/modals/PrintingModal.svelte",
            "UI/src/components/modals/BasicModal.svelte",
            "BE/application/utils/printing.py",
            "BE/application/utils/excel_utils.py",
            "BE/application/views/printing_views.py",
            "BE/docmanager/download_tokens.py",
            "BE/docmanager/views/document_view.py",
            "BE/docmanager/urls.py",
            "BE/docmanager/models.py",
            "BE/application/urls.py",
            "BE/application/tasks.py",
            "UI/src/routes/subscribe-multiple/wizard/Step3.svelte",
            "UI/src/routes/subscribe-multiple/SubscribeMultiple.svelte",
            "selfhost/tests/browser/manuale/member-sources.mjs",
            "selfhost/tests/browser/manuale/member-profile-sources.mjs",
            "selfhost/tests/browser/manuale/dashboard-sources.mjs",
            "selfhost/tests/browser/manuale/fixtures/documento-medico-demo.pdf",
            "selfhost/tests/browser/manuale/medical-followups.mjs",
            "docs/manuale/medical-followups-authored-workflows.mjs"
        ],
        "checkpoints": [
            {
                "id": "wizard-medical-step-correct-person",
                "caption": "Nuova domanda dimostrativa: passaggio Certificato Medico."
            },
            {
                "id": "wizard-invalid-file-rejected",
                "caption": "File non ammesso respinto senza inviare un caricamento."
            },
            {
                "id": "wizard-real-upload-and-expiration",
                "caption": "PDF dimostrativo caricato realmente e scadenza manuale prima del riepilogo."
            },
            {
                "id": "wizard-removed-file-preserves-expiration",
                "caption": "Allegato selezionato rimosso: data conservata, nessun documento da inviare."
            },
            {
                "id": "wizard-summary-before-submission",
                "caption": "Riepilogo della nuova domanda prima della conferma."
            },
            {
                "id": "wizard-certificate-after-reload",
                "caption": "Documento e scadenza presenti nella domanda creata, dopo riapertura."
            },
            {
                "id": "profile-medical-empty-before-upload",
                "caption": "Scheda della persona: Nuovo Certificato prima del caricamento."
            },
            {
                "id": "profile-upload-confirmation-before-save",
                "caption": "File allegato immediatamente; data controllata prima di Salva."
            },
            {
                "id": "profile-saved-certificate-after-reload",
                "caption": "Allegato e scadenza persistiti dopo Salva e ricaricamento."
            },
            {
                "id": "replacement-real-file-before-save",
                "caption": "Nuovo PDF dimostrativo caricato tramite Sostituisci certificato."
            },
            {
                "id": "replacement-saved-after-reload",
                "caption": "Nuovo documento e scadenza dopo la riapertura della scheda."
            },
            {
                "id": "date-only-editor-preserves-existing-date",
                "caption": "Modifica scadenza: data salvata proposta, senza campo di caricamento."
            },
            {
                "id": "date-only-saved-same-document",
                "caption": "Data modificata e riaperta, con lo stesso documento allegato."
            },
            {
                "id": "competitive-classification-after-reload",
                "caption": "File, scadenza e classificazione agonistica conservati insieme."
            },
            {
                "id": "reader-medical-write-controls-absent",
                "caption": "Collaboratore di sola lettura: nessuna sostituzione o modifica della scadenza."
            },
            {
                "id": "medical-notifications-before-disable",
                "caption": "Profilo, Generali, Altro: interruttore delle notifiche prima della modifica."
            },
            {
                "id": "medical-notifications-disabled-after-reload",
                "caption": "Invia notifiche certificati medici disattivato e salvato dopo la riapertura."
            },
            {
                "id": "reader-medical-notifications-disabled-control",
                "caption": "Collaboratore di sola lettura: configurazione delle notifiche non modificabile."
            },
            {
                "id": "members-certificate-column-current-state",
                "caption": "Tesserati: certificato valido della persona e dati mancanti delle altre iscrizioni."
            },
            {
                "id": "medical-dashboard-add-expired-widget",
                "caption": "Gestione widget: Certificati medici scaduti selezionato."
            },
            {
                "id": "medical-dashboard-both-widgets-saved",
                "caption": "Bacheca salvata con entrambi i gruppi sanitari."
            },
            {
                "id": "expiring-widget-opened-person-date",
                "caption": "Modifica dal widget in scadenza apre la persona e la stessa data salvata."
            },
            {
                "id": "missing-certificate-filter-results",
                "caption": "Certificato non presente: le iscrizioni prive di certificato, senza la persona con documento."
            },
            {
                "id": "expired-certificate-filter-results",
                "caption": "Certificato scaduto: documento della persona con data precedente a oggi."
            },
            {
                "id": "expired-widget-current-owned-person",
                "caption": "Certificati medici scaduti: persona con documento e scadenza di ieri."
            },
            {
                "id": "today-list-versus-widget-boundary",
                "caption": "Scadenza di oggi: filtro elenco scaduto e widget in scadenza selezionano diversamente."
            },
            {
                "id": "expiring-report-real-pdf-preview",
                "caption": "Stampa reale in scadenza: anteprima PDF e controllo del gruppo."
            },
            {
                "id": "expiring-report-real-excel-download",
                "caption": "Stampa in scadenza: foglio reale scaricato e righe controllate."
            },
            {
                "id": "expired-report-real-pdf-preview",
                "caption": "Stampa reale dei certificati scaduti: anteprima PDF."
            },
            {
                "id": "expired-report-real-excel-download",
                "caption": "Stampa scaduti: foglio reale scaricato e righe controllate."
            },
            {
                "id": "medical-configuration-restored",
                "caption": "Impostazioni e widget iniziali ripristinati dopo la procedura."
            }
        ],
        "sections": [
            {
                "path": "tutorials/come-gestire-certificati-medici.mdx",
                "id": "durante-l-iscrizione",
                "title": "Durante l'iscrizione",
                "checkpoints": [
                    "wizard-medical-step-correct-person",
                    "wizard-invalid-file-rejected",
                    "wizard-real-upload-and-expiration",
                    "wizard-removed-file-preserves-expiration",
                    "wizard-summary-before-submission",
                    "wizard-certificate-after-reload"
                ],
                "images": [
                    {
                        "from": "/images/tutorials/come-gestire-certificati-medici/1.placeholder.svg",
                        "checkpoint": "wizard-real-upload-and-expiration"
                    }
                ],
                "insert_images": [
                    {
                        "step": "Raggiungi il passaggio Certificato Medico",
                        "checkpoint": "wizard-medical-step-correct-person"
                    },
                    {
                        "step": "Completa la domanda e verifica la scheda",
                        "checkpoint": "wizard-summary-before-submission"
                    },
                    {
                        "step": "Completa la domanda e verifica la scheda",
                        "checkpoint": "wizard-certificate-after-reload"
                    }
                ]
            },
            {
                "path": "tutorials/come-gestire-certificati-medici.mdx",
                "id": "disattivare-le-notifiche",
                "title": "Disattivare le notifiche",
                "checkpoints": [
                    "medical-notifications-before-disable",
                    "medical-notifications-disabled-after-reload",
                    "reader-medical-notifications-disabled-control"
                ],
                "images": [],
                "insert_images": [
                    {
                        "step": "Apri le impostazioni generali",
                        "checkpoint": "medical-notifications-before-disable"
                    },
                    {
                        "step": "Disattiva e salva",
                        "checkpoint": "medical-notifications-disabled-after-reload"
                    }
                ]
            },
            {
                "path": "tutorials/come-gestire-certificati-medici.mdx",
                "id": "visualizzare-i-certificati-in-scadenza",
                "title": "Visualizzare i certificati in scadenza",
                "checkpoints": [
                    "medical-dashboard-add-expired-widget",
                    "medical-dashboard-both-widgets-saved",
                    "expiring-widget-opened-person-date",
                    "expired-widget-current-owned-person",
                    "expiring-report-real-pdf-preview",
                    "expiring-report-real-excel-download",
                    "expired-report-real-pdf-preview",
                    "expired-report-real-excel-download"
                ],
                "images": [
                    {
                        "from": "/images/tutorials/come-gestire-certificati-medici/2.placeholder.svg",
                        "checkpoint": "medical-dashboard-both-widgets-saved"
                    }
                ],
                "insert_images": [
                    {
                        "step": "Apri i widget sanitari della bacheca",
                        "checkpoint": "medical-dashboard-add-expired-widget"
                    },
                    {
                        "step": "Apri la persona da controllare",
                        "checkpoint": "expiring-widget-opened-person-date"
                    },
                    {
                        "step": "Controlla il gruppo prima di stampare",
                        "checkpoint": "expiring-report-real-pdf-preview"
                    },
                    {
                        "step": "Controlla il gruppo prima di stampare",
                        "checkpoint": "expired-report-real-pdf-preview"
                    }
                ]
            },
            {
                "path": "faq/come-gestire-i-certificati-medici.mdx",
                "id": "caricare-un-certificato-medico",
                "title": "Caricare un certificato medico",
                "checkpoints": [
                    "profile-medical-empty-before-upload",
                    "profile-upload-confirmation-before-save",
                    "profile-saved-certificate-after-reload",
                    "replacement-real-file-before-save",
                    "replacement-saved-after-reload"
                ],
                "images": [
                    {
                        "from": "/images/certificati/profilo/1.placeholder.svg",
                        "checkpoint": "profile-medical-empty-before-upload"
                    },
                    {
                        "from": "/images/certificati/profilo/2.placeholder.svg",
                        "checkpoint": "profile-upload-confirmation-before-save"
                    },
                    {
                        "from": "/images/certificati/profilo/3.placeholder.svg",
                        "checkpoint": "profile-saved-certificate-after-reload"
                    }
                ],
                "insert_images": []
            },
            {
                "path": "faq/come-gestire-i-certificati-medici.mdx",
                "id": "controllare-lo-stato-dei-certificati",
                "title": "Controllare lo stato dei certificati",
                "checkpoints": [
                    "members-certificate-column-current-state",
                    "competitive-classification-after-reload",
                    "missing-certificate-filter-results",
                    "expired-certificate-filter-results",
                    "expired-widget-current-owned-person",
                    "today-list-versus-widget-boundary",
                    "medical-dashboard-both-widgets-saved"
                ],
                "images": [],
                "insert_images": [
                    {
                        "step": "Controlla l'elenco delle iscrizioni",
                        "checkpoint": "members-certificate-column-current-state"
                    },
                    {
                        "step": "Apri la situazione completa",
                        "checkpoint": "competitive-classification-after-reload"
                    },
                    {
                        "step": "Confronta elenco e bacheca",
                        "checkpoint": "missing-certificate-filter-results"
                    },
                    {
                        "step": "Confronta elenco e bacheca",
                        "checkpoint": "expired-certificate-filter-results"
                    },
                    {
                        "step": "Confronta elenco e bacheca",
                        "checkpoint": "today-list-versus-widget-boundary"
                    }
                ]
            },
            {
                "path": "faq/come-gestire-i-certificati-medici.mdx",
                "id": "aggiornare-un-certificato-medico",
                "title": "Aggiornare un certificato medico",
                "checkpoints": [
                    "replacement-real-file-before-save",
                    "replacement-saved-after-reload",
                    "date-only-editor-preserves-existing-date",
                    "date-only-saved-same-document",
                    "competitive-classification-after-reload",
                    "reader-medical-write-controls-absent"
                ],
                "images": [
                    {
                        "from": "/images/certificati/profilo/5.placeholder.svg",
                        "checkpoint": "replacement-real-file-before-save"
                    }
                ],
                "insert_images": [
                    {
                        "step": "Aggiorna una data senza cambiare file",
                        "checkpoint": "date-only-editor-preserves-existing-date"
                    },
                    {
                        "step": "Aggiorna una data senza cambiare file",
                        "checkpoint": "date-only-saved-same-document"
                    },
                    {
                        "step": "Controlla anche la classificazione",
                        "checkpoint": "competitive-classification-after-reload"
                    }
                ]
            },
            {
                "path": "tutorials/come-gestire-certificati-medici.mdx",
                "id": "dal-profilo-dell-atleta",
                "title": "Dal profilo dell'atleta",
                "checkpoints": [
                    "profile-medical-empty-before-upload",
                    "profile-upload-confirmation-before-save",
                    "profile-saved-certificate-after-reload"
                ],
                "images": [
                    {
                        "from": "/images/certificati/controlli-reali/7.placeholder.svg",
                        "checkpoint": "profile-medical-empty-before-upload"
                    },
                    {
                        "from": "/images/certificati/controlli-reali/8.placeholder.svg",
                        "checkpoint": "profile-upload-confirmation-before-save"
                    },
                    {
                        "from": "/images/certificati/controlli-reali/9.placeholder.svg",
                        "checkpoint": "profile-saved-certificate-after-reload"
                    }
                ],
                "insert_images": []
            },
            {
                "path": "tutorials/come-gestire-certificati-medici.mdx",
                "id": "impostare-la-data-di-scadenza",
                "title": "Impostare la data di scadenza",
                "checkpoints": [
                    "profile-saved-certificate-after-reload",
                    "date-only-editor-preserves-existing-date",
                    "date-only-saved-same-document"
                ],
                "images": [
                    {
                        "from": "/images/certificati/controlli-reali/9.placeholder.svg",
                        "checkpoint": "profile-saved-certificate-after-reload"
                    },
                    {
                        "from": "/images/certificati/controlli-reali/12.placeholder.svg",
                        "checkpoint": "date-only-editor-preserves-existing-date"
                    },
                    {
                        "from": "/images/certificati/controlli-reali/13.placeholder.svg",
                        "checkpoint": "date-only-saved-same-document"
                    }
                ],
                "insert_images": []
            },
            {
                "path": "tutorials/come-gestire-certificati-medici.mdx",
                "id": "consigli-per-una-gestione-efficiente",
                "title": "Consigli per una gestione efficiente",
                "checkpoints": [
                    "missing-certificate-filter-results",
                    "medical-dashboard-both-widgets-saved",
                    "today-list-versus-widget-boundary"
                ],
                "images": [
                    {
                        "from": "/images/certificati/controlli-reali/23.placeholder.svg",
                        "checkpoint": "missing-certificate-filter-results"
                    },
                    {
                        "from": "/images/certificati/controlli-reali/21.placeholder.svg",
                        "checkpoint": "medical-dashboard-both-widgets-saved"
                    },
                    {
                        "from": "/images/certificati/controlli-reali/26.placeholder.svg",
                        "checkpoint": "today-list-versus-widget-boundary"
                    }
                ],
                "insert_images": []
            },
            {
                "path": "tutorials/come-gestire-certificati-medici.mdx",
                "id": "tieni-traccia-dei-certificati-agonistici",
                "title": "Tieni traccia dei certificati agonistici",
                "checkpoints": [
                    "date-only-saved-same-document",
                    "competitive-classification-after-reload"
                ],
                "images": [
                    {
                        "from": "/images/certificati/controlli-reali/13.placeholder.svg",
                        "checkpoint": "date-only-saved-same-document"
                    },
                    {
                        "from": "/images/certificati/controlli-reali/14.placeholder.svg",
                        "checkpoint": "competitive-classification-after-reload"
                    }
                ],
                "insert_images": []
            }
        ],
        "outcome": {
            "field": "medical_followups_authored_workflow",
            "expected": {
                "wizard_invalid_file_did_not_upload": true,
                "wizard_removed_attachment_preserved_date": true,
                "wizard_submitted_actual_certificate_and_expiration": true,
                "wizard_document_download_matches_fixture": true,
                "wizard_document_and_date_reopened": true,
                "profile_upload_attaches_before_date_save": true,
                "profile_document_download_matches_fixture": true,
                "profile_document_and_date_reopened": true,
                "replacement_changes_document_id": true,
                "replacement_document_download_matches_fixture": true,
                "replacement_document_and_date_reopened": true,
                "date_only_preserves_document_and_certificate_ids": true,
                "competitive_classification_reopened": true,
                "reader_registration_create_status": 403,
                "reader_medical_upload_status": 403,
                "reader_medical_expiration_status": 403,
                "reader_medical_classification_status": 403,
                "reader_medical_denial_preserved_state": true,
                "notifications_disabled_and_reopened": true,
                "reader_notifications_status": 403,
                "reader_notifications_denial_preserved_settings": true,
                "both_medical_widgets_saved_and_reopened": true,
                "widget_opened_exact_person_and_date": true,
                "missing_filter_excludes_document_owner": true,
                "expired_filter_includes_yesterday": true,
                "today_list_expired_widget_expiring_distinction": true,
                "expiring_report_pdf_downloaded_and_previewed": true,
                "expiring_report_excel_content_matches": true,
                "expired_report_pdf_downloaded_and_previewed": true,
                "expired_report_excel_content_matches": true,
                "original_settings_and_dashboard_restored": true,
                "baseline_registrations_and_payments_preserved": true,
                "owned_registration_and_payment_removed": true,
                "owned_attached_documents_removed": true,
                "private_cleanup_manifest_recorded": true
            }
        }
    }
});
