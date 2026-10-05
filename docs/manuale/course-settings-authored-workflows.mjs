// Only a passed real scenario can promote these authored procedures.
// MDX owns the prose; checkpoints bind just the complete procedures below.
import {paymentSources, receiptSources} from '../../selfhost/tests/browser/manuale/payments-sources.mjs';
const courseSources = [
    ...paymentSources,
    'UI/src/components/Sidebar.svelte', 'UI/src/routes.js', 'UI/src/utils/Permissions.js',
    'UI/src/routes/association/course/CourseList.svelte',
    'UI/src/routes/association/course/add/AddCourse.svelte',
    'UI/src/routes/association/course/add/sections/Section1.svelte',
    'UI/src/components/eventgenerator/EventGenerator.svelte',
    'UI/src/components/eventgenerator/RateElement.svelte', 'UI/src/components/inputs/DateInput.svelte',
    'UI/src/components/buttons/DeleteButton.svelte',
    'UI/src/components/formBuilder/preview-blocks/smart-multiselect-input.svelte',
    'UI/src/routes/association/course/overview/OverviewCourse.svelte',
    'UI/src/routes/association/course/overview/components/CourseNavigationTab.svelte',
    'UI/src/routes/association/course/overview/modals/AddEditCourseSubscriptionModal.svelte',
    'UI/src/routes/association/course/overview/partials/course-subscriptions-list.svelte',
    'UI/src/routes/association/Members/modals/PaymentModal.svelte',
    'UI/src/routes/association/Members/detail/sections/Payments.svelte',
    'UI/src/routes/accounting/payment/PaymentDrawer.svelte',
    'UI/src/routes/profile/ProfileMenu.svelte', 'UI/src/utils/profileNavigation.js',
    'UI/src/routes/profile/sections/Settings.svelte',
    'BE/application/views/course_views.py', 'BE/application/serializers/courses_serializers.py',
    'BE/application/models/courses_models.py', 'BE/application/utils/subscriptions_utils.py',
    'BE/application/signals.py', 'BE/application/views/profile_views.py',
    'BE/application/serializers/auth_serializers.py', 'BE/application/models/user_models.py',
    'BE/application/views/payment_views.py', 'BE/application/serializers/payment_serializers.py',
    'BE/application/models/payment_models.py', 'BE/application/permissions_registry.py',
];
const settingsSources = [
    ...receiptSources,
    'UI/src/components/Sidebar.svelte', 'UI/src/routes.js', 'UI/src/utils/Permissions.js',
    'UI/src/routes/profile/ProfileMenu.svelte', 'UI/src/utils/profileNavigation.js',
    'UI/src/routes/profile/sections/Settings.svelte',
    'UI/src/routes/association/Members/detail/Detail.svelte',
    'UI/src/routes/association/Members/detail/sections/Info.svelte',
    'UI/src/routes/association/Members/detail/sections/subcomponents/Tessera.svelte',
    'UI/src/routes/association/Members/detail/sections/Payments.svelte',
    'UI/src/utils/BrandTheme.js',
    'BE/application/views/profile_views.py', 'BE/application/serializers/auth_serializers.py',
    'BE/application/serializers/subscriptions_serializers.py',
    'BE/application/models/user_models.py', 'BE/application/views/subscriptions_views.py',
    'BE/application/models/subscriptions_models.py', 'BE/application/views/payment_views.py',
    'BE/application/views/invoice_views.py', 'BE/application/models/invoices_models.py',
    'BE/application/services/invoice_service.py', 'BE/docmanager/views/printing_views.py',
    'BE/templates/document/application/invoice.html', 'BE/application/permissions_registry.py',
];
const coursePrefix = 'images/corsi/piano-rate-reale/';
const settingsPrefix = 'images/impostazioni/ricevute-tessera-reali/';
export const courseSettingsAuthoredWorkflows = Object.freeze({
    'course-installments-manage': {
        version: 1, script: 'selfhost/tests/browser/manuale/course-installments.mjs', prefix: coursePrefix,
        pages: ['docs/corsi.mdx', 'tutorials/come-impostare-pagamenti-rateizzati.mdx'],
        sources: [...new Set(courseSources)],
        dependencies: [...new Set(courseSources), 'selfhost/tests/browser/manuale/payments-sources.mjs', 'selfhost/tests/browser/manuale/course-installments.mjs',
            'docs/manuale/course-settings-authored-workflows.mjs'],
        checkpoints: [
            {id: 'uniform-installments-and-single-fee-before-save', caption: 'Corso Standard: tre rate da 100 euro, scadenze e alternativa da 280 euro.'},
            {id: 'new-installment-course-persists-after-reload', caption: 'Piano generale del nuovo corso salvato e riaperto.'},
            {id: 'different-amounts-in-creation-generator', caption: 'Generatore in creazione: tre importi differenti e scadenze corrette, totale 300 euro.'},
            {id: 'preparatory-rate-removal-updates-total', caption: 'Rimozione della terza riga prima di salvare: due rate e totale ricalcolato a 250 euro.'},
            {id: 'creation-alternative-persists-after-reload', caption: 'Nuovo piano da 150 e 100 euro con date conservate dopo salvataggio e riapertura.'},
            {id: 'distinct-amounts-in-course-editor', caption: 'Editor della scheda corso: importi distinti 150, 100 e 50 euro.'},
            {id: 'distinct-course-plan-persists-after-reload', caption: 'Importi distinti e scadenze conservati dopo la ricarica.'},
            {id: 'explicit-selection-includes-historical-installment', caption: 'Iscrizione di Giulia e Luca: selezione esplicita anche della rata con data passata.'},
            {id: 'assigned-installments-and-course-payments', caption: 'Pagamenti della singola iscrizione, con importi e corso corretti.'},
            {id: 'assigned-plan-reopened-for-correct-athlete', caption: 'Iscrizione di Giulia riaperta: tre rate assegnate con scadenze e importi corretti.'},
            {id: 'individual-selection-before-update', caption: 'Modifica del Piano rate della sola Giulia, senza intervenire sull’iscrizione di Luca.'},
            {id: 'individual-selection-persists-after-reload', caption: 'Selezione individuale salvata e riaperta; pagamenti già collegati conservati.'},
            {id: 'individual-missing-rate-selected', caption: 'Aggiunta della rata mancante alla sola iscrizione di Giulia, con data e importo del piano generale.'},
            {id: 'individual-added-rate-persists-after-reload', caption: 'Tre rate nella singola iscrizione dopo la riapertura; importi già registrati conservati.'},
            {id: 'individual-added-rate-and-existing-payment-amounts', caption: 'Nuovo pagamento della rata aggiunta e quote già collegate da 100 e 50 euro.'},
            {id: 'individual-payment-keeps-recorded-amount', caption: 'Il pagamento individuale conserva 100 euro anche se il modello generale indica 110 euro.'},
            {id: 'future-plan-operation-confirmation', caption: 'Conferma Assegna piano rate per una sola iscrizione.'},
            {id: 'future-plan-persists-after-reload', caption: 'Riassegnazione ordinaria: due rate non precedenti all’iscrizione.'},
            {id: 'full-plan-operation-confirmation', caption: 'Conferma Assegna piano rate completo per una sola iscrizione.'},
            {id: 'full-plan-persists-after-reload', caption: 'Piano completo: tre rate, compresa quella passata, dopo la ricarica.'},
            {id: 'single-fee-operation-confirmation', caption: 'Conferma del cambio in unica soluzione, senza incasso o addebito online.'},
            {id: 'single-fee-and-other-athlete-preserved', caption: 'Giulia ha la quota unica da 280 euro; Luca mantiene le sue tre rate.'},
            {id: 'ordinary-default-with-no-explicit-rates', caption: 'Nuova iscrizione di prova senza rate esplicitamente selezionate, con piano completo predefinito disattivato.'},
            {id: 'ordinary-default-plan-persists-after-reload', caption: 'Piano predefinito ordinario: le due scadenze future salvate e riaperte.'},
            {id: 'complete-plan-default-before-change', caption: 'Profilo, Generali: impostazione del piano rate completo prima della modifica.'},
            {id: 'complete-plan-default-before-save', caption: 'Assegna piano rate completo di default abilitato, prima di Salva.'},
            {id: 'complete-plan-default-persists-after-reload', caption: 'Impostazione del piano completo salvata e riaperta; iscrizioni esistenti conservate.'},
            {id: 'complete-default-with-no-explicit-rates', caption: 'Nuova iscrizione di prova senza selezione esplicita, con piano completo predefinito abilitato.'},
            {id: 'complete-default-plan-persists-after-reload', caption: 'Piano completo predefinito: tre rate conservate, compresa la scadenza precedente all’iscrizione.'},
            {id: 'complete-default-assignment-and-real-payments', caption: 'Tre pagamenti reali della nuova iscrizione di prova, ancora da incassare.'},
            {id: 'restored-default-preserves-existing-plans', caption: 'Impostazione originale ripristinata; i piani già assegnati mantengono le proprie rate.'},
        ],
        sections: [
            {path: 'docs/corsi.mdx', id: 'corso-con-le-rate', title: 'Corso con le rate',
                checkpoints: ['uniform-installments-and-single-fee-before-save', 'new-installment-course-persists-after-reload',
                    'different-amounts-in-creation-generator', 'creation-alternative-persists-after-reload',
                    'explicit-selection-includes-historical-installment', 'assigned-installments-and-course-payments',
                    'assigned-plan-reopened-for-correct-athlete'], images: [],
                insert_images: [{step: 'Prepara la quota', checkpoint: 'uniform-installments-and-single-fee-before-save'},
                    {step: 'Crea e controlla le rate', checkpoint: 'different-amounts-in-creation-generator'},
                    {step: 'Salva e assegna il piano', checkpoint: 'assigned-installments-and-course-payments'}]},
            {path: 'tutorials/come-impostare-pagamenti-rateizzati.mdx', id: 'come-configurare-le-rate', title: 'Come configurare le rate',
                checkpoints: ['uniform-installments-and-single-fee-before-save', 'new-installment-course-persists-after-reload',
                    'different-amounts-in-creation-generator', 'preparatory-rate-removal-updates-total',
                    'creation-alternative-persists-after-reload'],
                images: [{from: '/images/course-payment-additional/installments-pending.svg', checkpoint: 'different-amounts-in-creation-generator'}],
                insert_images: [{step: 'Genera le righe', checkpoint: 'uniform-installments-and-single-fee-before-save'},
                    {step: 'Rivedi tutte le scadenze', checkpoint: 'different-amounts-in-creation-generator'},
                    {step: 'Salva e riapri', checkpoint: 'creation-alternative-persists-after-reload'},
                    {step: 'Salva e riapri', checkpoint: 'preparatory-rate-removal-updates-total'}]},
            {path: 'tutorials/come-impostare-pagamenti-rateizzati.mdx', id: 'opzione-piano-rate-completo', title: 'Opzione "Piano rate completo"',
                checkpoints: ['ordinary-default-with-no-explicit-rates', 'ordinary-default-plan-persists-after-reload',
                    'complete-plan-default-before-change', 'complete-plan-default-before-save', 'complete-plan-default-persists-after-reload',
                    'complete-default-with-no-explicit-rates', 'complete-default-plan-persists-after-reload',
                    'complete-default-assignment-and-real-payments', 'restored-default-preserves-existing-plans'], images: [],
                insert_images: [{step: 'Apri le impostazioni', checkpoint: 'complete-plan-default-before-change'},
                    {step: 'Scegli e salva il comportamento', checkpoint: 'complete-plan-default-persists-after-reload'},
                    {step: 'Controlla una nuova assegnazione', checkpoint: 'complete-default-plan-persists-after-reload'},
                    {step: 'Controlla una nuova assegnazione', checkpoint: 'complete-default-assignment-and-real-payments'}]},
            {path: 'tutorials/come-impostare-pagamenti-rateizzati.mdx', id: 'modificare-il-piano-rate-per-un-singolo-atleta', title: 'Modificare il piano rate per un singolo atleta',
                checkpoints: ['individual-selection-before-update', 'individual-selection-persists-after-reload',
                    'individual-missing-rate-selected', 'individual-added-rate-persists-after-reload',
                    'individual-added-rate-and-existing-payment-amounts', 'individual-payment-keeps-recorded-amount'], images: [],
                insert_images: [{step: 'Apri l’iscrizione corretta', checkpoint: 'individual-selection-before-update'},
                    {step: 'Rivedi Piano rate', checkpoint: 'individual-missing-rate-selected'},
                    {step: 'Salva e confronta le quote', checkpoint: 'individual-added-rate-and-existing-payment-amounts'},
                    {step: 'Correggi l’importo nel percorso appropriato', checkpoint: 'individual-payment-keeps-recorded-amount'}]},
            {path: 'tutorials/come-impostare-pagamenti-rateizzati.mdx', id: 'attivare-i-pagamenti-a-rate', title: 'Attivare i pagamenti a rate',
                checkpoints: ['uniform-installments-and-single-fee-before-save', 'new-installment-course-persists-after-reload', 'explicit-selection-includes-historical-installment', 'assigned-installments-and-course-payments', 'assigned-plan-reopened-for-correct-athlete'], images: [],
                insert_images: [{step: 'Imposta quota e rateizzazione', checkpoint: 'uniform-installments-and-single-fee-before-save'},
                    {step: 'Controlla prima di salvare', checkpoint: 'assigned-installments-and-course-payments'}]},
            {path: 'docs/corsi.mdx', id: 'corso-con-le-rate-e-pagamento-in-unica-soluzione', title: 'Corso con le rate e pagamento in unica soluzione',
                checkpoints: ['uniform-installments-and-single-fee-before-save', 'new-installment-course-persists-after-reload', 'single-fee-operation-confirmation', 'single-fee-and-other-athlete-preserved'], images: [],
                insert_images: [{step: 'Imposta l’alternativa', checkpoint: 'uniform-installments-and-single-fee-before-save'},
                    {step: 'Applica la scelta al tesserato', checkpoint: 'single-fee-and-other-athlete-preserved'}]},
            {path: 'tutorials/come-impostare-pagamenti-rateizzati.mdx', id: 'offrire-anche-il-pagamento-in-un-unica-soluzione', title: "Offrire anche il pagamento in un'unica soluzione",
                checkpoints: ['uniform-installments-and-single-fee-before-save', 'new-installment-course-persists-after-reload', 'single-fee-operation-confirmation', 'single-fee-and-other-athlete-preserved'], images: [],
                insert_images: [{step: 'Configura l’importo alternativo', checkpoint: 'uniform-installments-and-single-fee-before-save'},
                    {step: 'Applica e controlla la scelta', checkpoint: 'single-fee-and-other-athlete-preserved'}]},
            {path: 'docs/corsi.mdx', id: 'gestire-i-pagamenti-del-corso', title: 'Gestire i pagamenti del corso',
                checkpoints: ['future-plan-operation-confirmation', 'future-plan-persists-after-reload', 'full-plan-operation-confirmation', 'full-plan-persists-after-reload', 'single-fee-operation-confirmation', 'single-fee-and-other-athlete-preserved'], images: [],
                insert_images: [{step: 'Scegli l’operazione disponibile', checkpoint: 'future-plan-operation-confirmation'},
                    {step: 'Rivedi conferma e risultato', checkpoint: 'full-plan-persists-after-reload'}]},
        ],
        outcome: {field: 'course_settings_authored_workflow', expected: {
            created_events: 3, uniform_plan_saved: true, edited_amounts_saved: true,
            explicitly_assigned_historical_rate: true, immediate_unpaid_payment_count: 6,
            individual_selection_saved: true, existing_installment_amount_preserved: true,
            ordinary_plan_count: 2, complete_plan_count: 3, single_fee_payment_count: 1,
            single_fee_amount: 280, control_athlete_installments: 3, control_athlete_unchanged: true,
            scoped_course_payments: true, baseline_course_unchanged: true, reader_writes_denied: true,
            creation_alternative_saved: true, preparatory_removal_updates_total: true, creation_alternative_removed: true,
            individual_addition_creates_payment: true, individual_removal_deletes_unpaid_payment: true,
            individual_payment_keeps_recorded_amount: true, default_ordinary_plan_count: 2, default_complete_plan_count: 3,
            default_saved_and_reopened: true, defaults_do_not_rewrite_existing_plans: true, reader_default_write_denied: true,
            original_default_restored: true, owned_trial_memberships_and_payments_removed: true,
        }},
    },
    'settings-receipts-card': {
        version: 1, script: 'selfhost/tests/browser/manuale/settings-receipts-card.mjs', prefix: settingsPrefix,
        pages: ['docs/impostazioni.mdx'], sources: [...new Set(settingsSources)],
        dependencies: [...new Set(settingsSources), 'selfhost/tests/browser/manuale/payments-sources.mjs', 'selfhost/tests/browser/manuale/settings-receipts-card.mjs',
            'selfhost/tests/browser/manuale/organization-access-sources.mjs', 'docs/manuale/course-settings-authored-workflows.mjs'],
        checkpoints: [
            {id: 'existing-receipts-before-numbering-change', caption: 'Elenco delle ricevute prima della modifica della numerazione.'},
            {id: 'receipt-numbering-and-date-before-save', caption: 'Numera Ricevute, base 100 e data uguale al pagamento prima di Salva.'},
            {id: 'receipt-preferences-persist-after-reload', caption: 'Preferenze delle ricevute conservate dopo la ricarica.'},
            {id: 'card-color-classic-layout-and-approval-preference', caption: 'Preferenze e anteprima di esempio: colore personalizzato e layout Classico.'},
            {id: 'card-preferences-persist-after-reload', caption: 'Approvazione, QR, colore e layout salvati e riaperti.'},
            {id: 'actual-approved-member-card', caption: 'Tessera reale di Giulia con il layout e il colore salvati.'},
            {id: 'actual-pending-member-card-observed', caption: 'Controllo della tessera di Luca con iscrizione In attesa; nessuna promessa di blocco universale.'},
            {id: 'approval-with-backdated-payment-and-no-email', caption: 'Incasso dimostrativo con data scelta, ricevuta richiesta e invio email disattivato.'},
            {id: 'new-receipt-number-and-date', caption: 'Nuova ricevuta numero 101 con data del pagamento.'},
            {id: 'actual-numbered-receipt-pdf', caption: 'Anteprima del PDF reale della ricevuta 101, senza link o token di condivisione.'},
        ],
        sections: [
            {path: 'docs/impostazioni.mdx', id: 'numera-ricevute', title: 'Numera ricevute',
                checkpoints: ['existing-receipts-before-numbering-change', 'receipt-numbering-and-date-before-save', 'receipt-preferences-persist-after-reload', 'new-receipt-number-and-date', 'actual-numbered-receipt-pdf'],
                images: [{from: '/images/impostazioni/configurazione/ricevute.placeholder.svg', checkpoint: 'receipt-numbering-and-date-before-save'}],
                insert_images: [{step: 'Salva e verifica un nuovo documento', checkpoint: 'actual-numbered-receipt-pdf'}]},
            {path: 'docs/impostazioni.mdx', id: 'data-uguale-per-ricevute-e-pagamenti', title: 'Data uguale per ricevute e pagamenti',
                checkpoints: ['receipt-numbering-and-date-before-save', 'receipt-preferences-persist-after-reload', 'approval-with-backdated-payment-and-no-email', 'new-receipt-number-and-date', 'actual-numbered-receipt-pdf'], images: [],
                insert_images: [{step: 'Scegli la regola di data', checkpoint: 'receipt-numbering-and-date-before-save'},
                    {step: 'Controlla il pagamento e la ricevuta', checkpoint: 'actual-numbered-receipt-pdf'}]},
            {path: 'docs/impostazioni.mdx', id: 'personalizza-tessera', title: 'Personalizza tessera',
                checkpoints: ['card-color-classic-layout-and-approval-preference', 'card-preferences-persist-after-reload', 'actual-approved-member-card', 'actual-pending-member-card-observed'],
                images: [{from: '/images/impostazioni/configurazione/tessera.placeholder.svg', checkpoint: 'card-color-classic-layout-and-approval-preference'}],
                insert_images: [{step: 'Salva e controlla una tessera', checkpoint: 'actual-approved-member-card'}]},
        ],
        outcome: {field: 'course_settings_authored_workflow', expected: {
            numbering_saved_and_reopened: true, date_rule_saved_and_reopened: true,
            card_preferences_saved_and_reopened: true, card_brand_reset_exercised: true,
            approved_member_card_rendered: true, pending_member_card_observed: true,
            approval_preference_is_not_universal_gate: true, new_receipt_number: 101,
            receipt_date_matches_payment: true, actual_pdf_downloaded: true, actual_pdf_previewed: true,
            receipt_email_disabled: true, existing_receipts_not_renumbered: true, reader_settings_write_denied: true,
        }},
    },
    "course-types-sharing": {
    "version": 1,
    "script": "selfhost/tests/browser/manuale/course-types-sharing.mjs",
    "prefix": "images/corsi/tipologie-condivisione-reali/",
    "pages": [
        "docs/corsi.mdx"
    ],
    "sources": [
        "BE/application/tasks.py",
        "UI/src/components/Sidebar.svelte",
        "UI/src/routes.js",
        "UI/src/utils/Permissions.js",
        "UI/src/routes/association/course/CourseList.svelte",
        "UI/src/routes/association/course/add/AddCourse.svelte",
        "UI/src/routes/association/course/add/sections/Section1.svelte",
        "UI/src/routes/association/course/add/sections/partials/multiple-quotes.svelte",
        "UI/src/routes/association/course/add/sections/partials/membership.svelte",
        "UI/src/routes/association/course/overview/OverviewCourse.svelte",
        "UI/src/routes/association/course/overview/components/CourseNavigationTab.svelte",
        "UI/src/routes/association/course/overview/components/Calendar.svelte",
        "UI/src/routes/association/course/overview/modals/AddEditCourseSubscriptionModal.svelte",
        "UI/src/routes/association/course/overview/modals/AddEditMembershipModal.svelte",
        "UI/src/routes/association/course/overview/partials/course-subscriptions-list.svelte",
        "UI/src/routes/association/course/overview/partials/membership-list.svelte",
        "UI/src/routes/calendar/SharedCalendar.svelte",
        "UI/src/utils/membershipForm.js",
        "UI/src/utils/eventCalendar.js",
        "UI/src/components/inputs/DateInput.svelte",
        "UI/src/components/formBuilder/preview-blocks/smart-select-input.svelte",
        "BE/application/views/course_views.py",
        "BE/application/views/attendee_views.py",
        "BE/application/serializers/courses_serializers.py",
        "BE/application/models/courses_models.py",
        "BE/application/models/attendee_models.py",
        "BE/application/utils/subscriptions_utils.py",
        "BE/application/signals.py",
        "BE/application/permissions_registry.py",
        "BE/application/urls.py",
        "selfhost/tests/browser/playwright.manual.config.mjs"
    ],
    "dependencies": [
        "UI/src/components/Sidebar.svelte",
        "UI/src/routes.js",
        "UI/src/utils/Permissions.js",
        "UI/src/routes/association/course/CourseList.svelte",
        "UI/src/routes/association/course/add/AddCourse.svelte",
        "UI/src/routes/association/course/add/sections/Section1.svelte",
        "UI/src/routes/association/course/add/sections/partials/multiple-quotes.svelte",
        "UI/src/routes/association/course/add/sections/partials/membership.svelte",
        "UI/src/routes/association/course/overview/OverviewCourse.svelte",
        "UI/src/routes/association/course/overview/components/CourseNavigationTab.svelte",
        "UI/src/routes/association/course/overview/components/Calendar.svelte",
        "UI/src/routes/association/course/overview/modals/AddEditCourseSubscriptionModal.svelte",
        "UI/src/routes/association/course/overview/modals/AddEditMembershipModal.svelte",
        "UI/src/routes/association/course/overview/partials/course-subscriptions-list.svelte",
        "UI/src/routes/association/course/overview/partials/membership-list.svelte",
        "UI/src/routes/calendar/SharedCalendar.svelte",
        "UI/src/utils/membershipForm.js",
        "UI/src/utils/eventCalendar.js",
        "UI/src/components/inputs/DateInput.svelte",
        "UI/src/components/formBuilder/preview-blocks/smart-select-input.svelte",
        "BE/application/views/course_views.py",
        "BE/application/views/attendee_views.py",
        "BE/application/serializers/courses_serializers.py",
        "BE/application/models/courses_models.py",
        "BE/application/models/attendee_models.py",
        "BE/application/utils/subscriptions_utils.py",
        "BE/application/signals.py",
        "BE/application/permissions_registry.py",
        "BE/application/urls.py",
        "selfhost/tests/browser/playwright.manual.config.mjs",
        "selfhost/tests/browser/manuale/course-types-sharing.mjs",
        "docs/manuale/course-settings-authored-workflows.mjs"
    ],
    "checkpoints": [
        {
            "id": "multiple-quotes-before-save",
            "caption": "Due quote alternative con titolo, causale e importo; terza riga preparatoria rimossa."
        },
        {
            "id": "multiple-quotes-after-reload",
            "caption": "Quote alternative salvate e riaperte nel corso."
        },
        {
            "id": "multiple-quote-assignment-before-save",
            "caption": "Assegnazione della quota Studenti alla sola Giulia."
        },
        {
            "id": "multiple-quote-assignment-after-reload",
            "caption": "Iscrizione salvata e pagamento della quota Studenti ancora da incassare."
        },
        {
            "id": "multiple-quote-change-warning",
            "caption": "Avviso della modifica di Quota: il pagamento non viene riscritto."
        },
        {
            "id": "multiple-quote-change-payment-preserved",
            "caption": "Quota dell’iscrizione modificata; pagamento registrato da 65 euro conservato."
        },
        {
            "id": "calendar-sharing-link-and-embed",
            "caption": "Condividi calendario: link e codice incorporabile, con collegamenti mascherati."
        },
        {
            "id": "calendar-shared-without-session",
            "caption": "Calendario del corso aperto senza sessione; una lezione dimostrativa visibile."
        },
        {
            "id": "membership-course-defaults-before-save",
            "caption": "Abbonamento mensile da 40 euro, dalla data di iscrizione, senza rinnovo automatico."
        },
        {
            "id": "membership-course-after-reload",
            "caption": "Condizioni generali dell’abbonamento salvate e riaperte."
        },
        {
            "id": "individual-membership-period-before-save",
            "caption": "Condizioni individuali di Giulia: due mesi, quota 35 euro, periodo calcolato."
        },
        {
            "id": "individual-membership-after-reload",
            "caption": "Abbonamento individuale conservato dopo la ricarica, con una quota non incassata."
        }
    ],
    "sections": [
        {
            "path": "docs/corsi.mdx",
            "id": "corso-con-quote-multiple",
            "title": "Corso con quote multiple",
            "checkpoints": [
                "multiple-quotes-before-save",
                "multiple-quotes-after-reload",
                "multiple-quote-assignment-before-save",
                "multiple-quote-assignment-after-reload",
                "multiple-quote-change-warning",
                "multiple-quote-change-payment-preserved"
            ],
            "images": [],
            "insert_images": [
                {
                    "step": "Compila le alternative",
                    "checkpoint": "multiple-quotes-before-save"
                },
                {
                    "step": "Salva e assegna una quota",
                    "checkpoint": "multiple-quote-assignment-before-save"
                },
                {
                    "step": "Salva e assegna una quota",
                    "checkpoint": "multiple-quote-assignment-after-reload"
                },
                {
                    "step": "Rivedi una quota già assegnata",
                    "checkpoint": "multiple-quote-change-warning"
                }
            ]
        },
        {
            "path": "docs/corsi.mdx",
            "id": "corso-di-tipo-abbonamento",
            "title": "Corso di tipo Abbonamento",
            "checkpoints": [
                "membership-course-defaults-before-save",
                "membership-course-after-reload",
                "individual-membership-period-before-save",
                "individual-membership-after-reload"
            ],
            "images": [],
            "insert_images": [
                {
                    "step": "Imposta quota e durata",
                    "checkpoint": "membership-course-defaults-before-save"
                },
                {
                    "step": "Decidi inizio e rinnovo",
                    "checkpoint": "membership-course-after-reload"
                },
                {
                    "step": "Personalizza il singolo abbonamento",
                    "checkpoint": "individual-membership-period-before-save"
                },
                {
                    "step": "Controlla periodo e pagamento",
                    "checkpoint": "individual-membership-after-reload"
                }
            ]
        },
        {
            "path": "docs/corsi.mdx",
            "id": "condivisione-del-calendario",
            "title": "Condivisione del calendario",
            "checkpoints": [
                "calendar-sharing-link-and-embed",
                "calendar-shared-without-session"
            ],
            "images": [],
            "insert_images": [
                {
                    "step": "Apri Condividi calendario",
                    "checkpoint": "calendar-sharing-link-and-embed"
                },
                {
                    "step": "Controlla la vista senza sessione",
                    "checkpoint": "calendar-shared-without-session"
                }
            ]
        }
    ],
    "outcome": {
        "field": "course_types_sharing_authored_workflow",
        "expected": {
            "multiple_quotes_saved_reopened": true,
            "selected_quote_creates_unpaid_payment": true,
            "quote_change_preserves_recorded_payment": true,
            "membership_defaults_saved_reopened": true,
            "individual_membership_period_saved": true,
            "individual_fee_creates_unpaid_payment": true,
            "calendar_link_matches_actual_course": true,
            "clipboard_matches_displayed_link": true,
            "embedded_source_matches_link": true,
            "anonymous_calendar_read": true,
            "anonymous_calendar_write_denied": true,
            "reader_write_denials": 5,
            "baseline_records_preserved": true,
            "owned_courses_and_unpaid_payments_removed": true
        }
    }
},
});

// Additional complete bindings from the exhaustive guide review.
const exhaustiveGuideBindings = [
  {
    "workflow_id": "course-types-sharing",
    "section": {
      "path": "docs/calendario.mdx",
      "id": "link-diretto",
      "title": "Link diretto",
      "checkpoints": [
        "calendar-sharing-link-and-embed",
        "calendar-shared-without-session"
      ],
      "images": [
        {
          "from": "/images/corsi/tipologie-condivisione-reali/7.placeholder.svg",
          "checkpoint": "calendar-sharing-link-and-embed"
        },
        {
          "from": "/images/corsi/tipologie-condivisione-reali/8.placeholder.svg",
          "checkpoint": "calendar-shared-without-session"
        }
      ],
      "insert_images": []
    },
    "sources": [
      "BE/application/models/attendee_models.py",
      "BE/application/models/courses_models.py",
      "BE/application/permissions_registry.py",
      "BE/application/serializers/camps_and_retreats_serializers.py",
      "BE/application/serializers/courses_serializers.py",
      "BE/application/signals.py",
      "BE/application/tasks.py",
      "BE/application/urls.py",
      "BE/application/utils/camps_and_retreats_utils.py",
      "BE/application/utils/global_calendar.py",
      "BE/application/utils/subscriptions_utils.py",
      "BE/application/views/attendee_views.py",
      "BE/application/views/camp_and_retreats_views.py",
      "BE/application/views/course_views.py",
      "BE/application/views/google_views.py",
      "UI/src/components/Sidebar.svelte",
      "UI/src/components/formBuilder/preview-blocks/smart-select-input.svelte",
      "UI/src/components/inputs/DateInput.svelte",
      "UI/src/components/inputs/DateRangeCalendar.svelte",
      "UI/src/components/inputs/DateRangePicker.svelte",
      "UI/src/routes.js",
      "UI/src/routes/association/course/CourseList.svelte",
      "UI/src/routes/association/course/add/AddCourse.svelte",
      "UI/src/routes/association/course/add/sections/Section1.svelte",
      "UI/src/routes/association/course/add/sections/partials/membership.svelte",
      "UI/src/routes/association/course/add/sections/partials/multiple-quotes.svelte",
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
      "UI/src/routes/association/course/overview/OverviewCourse.svelte",
      "UI/src/routes/association/course/overview/components/Calendar.svelte",
      "UI/src/routes/association/course/overview/components/CourseNavigationTab.svelte",
      "UI/src/routes/association/course/overview/modals/AddEditCourseSubscriptionModal.svelte",
      "UI/src/routes/association/course/overview/modals/AddEditMembershipModal.svelte",
      "UI/src/routes/association/course/overview/partials/course-subscriptions-list.svelte",
      "UI/src/routes/association/course/overview/partials/membership-list.svelte",
      "UI/src/routes/calendar/Calendar.svelte",
      "UI/src/routes/calendar/SharedCalendar.svelte",
      "UI/src/routes/calendar/modals/AddCalendarEvent.svelte",
      "UI/src/routes/forms/CampsAndRetreatsForm.svelte",
      "UI/src/routes/profile/sections/Integrations.svelte",
      "UI/src/utils/Functions.js",
      "UI/src/utils/Permissions.js",
      "UI/src/utils/eventCalendar.js",
      "UI/src/utils/membershipForm.js",
      "selfhost/tests/browser/playwright.manual.config.mjs"
    ]
  }
];
for (const item of exhaustiveGuideBindings) {
    const spec = courseSettingsAuthoredWorkflows[item.workflow_id];
    const section = item.section;
    if (!spec || spec.sections.some(old => old.path === section.path && old.id === section.id))
        throw new Error('Duplicate or unknown additional procedure: ' + item.workflow_id + ':' + section.id);
    spec.sections.push(section);
    spec.pages = [...new Set([...spec.pages, section.path])];
    spec.sources = [...new Set([...spec.sources, ...(item.sources || [])])];
    spec.dependencies = [...new Set([...spec.dependencies, ...(item.sources || [])])];
}
