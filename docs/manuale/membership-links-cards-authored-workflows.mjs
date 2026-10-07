// Complete procedures alone belong in sections. These application-level results
// cannot promote guides that still require a native printer or external delivery.
import {registrationFormsSources} from '../../selfhost/tests/browser/manuale/registration-forms-sources.mjs';
import {memberProfileSources} from '../../selfhost/tests/browser/manuale/member-profile-sources.mjs';
const sources = [...new Set([...registrationFormsSources, ...memberProfileSources,
    'UI/package.json', 'UI/package-lock.json', 'UI/src/utils/ApiMiddleware.js', 'UI/src/store/stores.js',
    'UI/src/utils/BrandTheme.js', 'UI/src/components/buttons/simple-button.svelte',
    'UI/src/components/modals/BasicModal.svelte',
    'UI/src/components/formBuilder/preview-blocks/smart-select-input.svelte',
    'UI/src/components/formBuilder/preview-blocks/selectValue.js',
    'UI/src/routes/association/Members/detail/sections/subcomponents/Tessera.svelte',
    'UI/src/components/capacitor/attendance-scanner.svelte',
    'BE/core/settings.py', 'BE/core/authentication.py', 'BE/core/middleware.py',
    'BE/application/impersonation.py', 'BE/application/impersonation_scope.py',
    'BE/application/views/profile_views.py', 'BE/application/services/subscription_service.py',
    'BE/application/tests/test_manuale_cards.py', 'BE/application/management/commands/seed_manuale.py',
])];
const pending = (path, id, title, steps, reason) => ({path, id, title,
    status: 'needs_external_verification', steps, reason});
export const membershipLinksCardsAuthoredWorkflows = Object.freeze({
    'membership-links-cards': {
        version: 1, script: 'selfhost/tests/browser/manuale/membership-links-cards.mjs',
        prefix: 'images/libro-soci/link-tessera-reali/',
        pages: ['docs/libro-soci.mdx', 'faq/come-si-crea-un-socio.mdx', 'faq/come-stampare-tessere-atleti.mdx'],
        sources, dependencies: [...sources, 'selfhost/tests/browser/manuale/registration-forms-sources.mjs',
            'selfhost/tests/browser/manuale/member-profile-sources.mjs',
            'selfhost/tests/browser/manuale/organization-access-sources.mjs',
            'selfhost/tests/browser/manuale/membership-links-cards.mjs',
            'docs/manuale/membership-links-cards-authored-workflows.mjs'],
        checkpoints: [
            {id: 'membership-share-dialog-real-qr', caption: 'Condividi link iscrizioni: codice reale e indirizzo oscurati nella cattura.'},
            {id: 'membership-link-clipboard-confirmation', caption: 'Conferma della copia: gli appunti contengono esattamente l’indirizzo mostrato.'},
            {id: 'membership-authenticated-module-opened', caption: 'Apri apre il modulo reale dell’associazione in una nuova scheda autenticata.'},
            {id: 'membership-anonymous-module-options', caption: 'Lo stesso collegamento apre il modulo senza sessione, con le tipologie configurate.'},
            {id: 'membership-qr-printable-popup', caption: 'Pagina di stampa realmente prodotta dal comando QR code, prima della scelta di una stampante.'},
            {id: 'membership-card-correct-subscription', caption: 'Scheda della persona e periodo dell’iscrizione prima di Mostra Tessera.'},
            {id: 'membership-card-visible', caption: 'Tessera reale: nome, associazione, numero e scadenza verificati; identificativo QR oscurato.'},
            {id: 'membership-card-png-downloaded', caption: 'Tessera dopo il download reale tessera.png, verificato come immagine PNG.'},
            {id: 'membership-card-real-print-page', caption: 'Pagina temporanea realmente aperta da Stampa, con contenuti della tessera verificati.'},
            {id: 'membership-reader-card-controls', caption: 'Il lettore consulta la tessera; la generazione del link viene negata dall’API.'},
        ],
        sections: [],
        pending_sections: [
            pending('docs/libro-soci.mdx', 'iscrizioni-con-qr-code', 'Iscrizioni con QR code',
                ['Apri la condivisione', 'Controlla il modulo collegato', 'Prepara il QR code', 'Completa la stampa e controlla la lettura'],
                'QR, appunti, modulo e HTML di stampa provati; dialogo nativo, destinazione e copia cartacea richiedono verifica esterna.'),
            pending('faq/come-si-crea-un-socio.mdx', 'generazione-del-qr-code-e-del-link', 'Generazione del QR Code e del Link',
                ['Apri la condivisione', 'Controlla il modulo collegato', 'Prepara il QR code', 'Completa la stampa e controlla la lettura'],
                'Il PDF prodotto dal motore di stampa Chromium è un artefatto della prova, non una scelta verificata nel dialogo nativo.'),
            pending('faq/come-si-crea-un-socio.mdx', 'condivisione-del-link', 'Condivisione del Link',
                ['Copia l’indirizzo corrente', 'Controlla prima di distribuire', 'Completa l’invio nel servizio scelto', 'Spiega cosa fare dopo l’apertura'],
                'Copia e apertura locali provate; nessuna bozza inviata, consegna email o WhatsApp eseguita.'),
            pending('faq/come-stampare-tessere-atleti.mdx', 'stampare-la-tessera-di-un-singolo-atleta', 'Stampare la tessera di un singolo atleta',
                ['Apri l’iscrizione corretta', 'Mostra e controlla la tessera', 'Scegli il risultato', 'Verifica la stampa prima di conservarla'],
                'PNG e pagina di stampa provati; il PDF è prodotto separatamente dal motore Chromium. Dialogo e destinazione nativi restano esterni.'),
        ],
        outcome: {field: 'membership_links_cards_application_evidence', expected: {
            qr_pixels_decode_to_displayed_link: true, clipboard_equals_displayed_link: true,
            authenticated_popup_is_current_module: true, anonymous_module_configuration_matches: true,
            qr_print_popup_contains_same_code_and_link: true, qr_native_print_requested: true,
            qr_chromium_print_pdf_validated: true, card_person_period_and_number_match: true,
            card_qr_pixels_identify_subscription: true, real_card_png_downloaded: true,
            card_print_popup_matches_api: true, card_native_print_requested: true,
            token_authorized_anonymous_card_opened: true,
            card_chromium_print_pdf_validated: true, reader_token_generation_denied: true,
            missing_and_invalid_card_tokens_denied: true, business_records_preserved: true,
            configuration_preserved: true, local_downloads_cleaned: true,
            token_cleanup_delegated_to_owned_fixture_reset: true,
        }},
    },
});

// Additional complete bindings from the exhaustive guide review.
const exhaustiveGuideBindings = [
  {
    "workflow_id": "membership-links-cards",
    "section": {
      "path": "faq/come-condividere-il-link-iscrizioni.mdx",
      "id": "4-qr-code",
      "title": "4. QR Code",
      "checkpoints": [
        "membership-share-dialog-real-qr",
        "membership-qr-printable-popup"
      ],
      "images": [
        {
          "from": "/images/libro-soci/link-tessera-reali/1.placeholder.svg",
          "checkpoint": "membership-share-dialog-real-qr"
        },
        {
          "from": "/images/libro-soci/link-tessera-reali/5.placeholder.svg",
          "checkpoint": "membership-qr-printable-popup"
        }
      ],
      "insert_images": []
    },
    "sources": [
      "BE/application/impersonation.py",
      "BE/application/impersonation_scope.py",
      "BE/application/management/commands/seed_manuale.py",
      "BE/application/models/subscriptions_models.py",
      "BE/application/models/user_models.py",
      "BE/application/permissions_registry.py",
      "BE/application/serializers/auth_serializers.py",
      "BE/application/serializers/subscriptions_serializers.py",
      "BE/application/services/subscription_service.py",
      "BE/application/tests/test_manuale_cards.py",
      "BE/application/urls.py",
      "BE/application/views/archive_views.py",
      "BE/application/views/profile_views.py",
      "BE/application/views/search_views.py",
      "BE/application/views/subscriptions_views.py",
      "BE/core/authentication.py",
      "BE/core/middleware.py",
      "BE/core/settings.py",
      "BE/docmanager/views/printing_views.py",
      "UI/package-lock.json",
      "UI/package.json",
      "UI/src/components/Sidebar.svelte",
      "UI/src/components/buttons/simple-button.svelte",
      "UI/src/components/capacitor/attendance-scanner.svelte",
      "UI/src/components/drawer/basic-drawer.svelte",
      "UI/src/components/formBuilder/composer-preview.svelte",
      "UI/src/components/formBuilder/composer-sidebar.svelte",
      "UI/src/components/formBuilder/inputs.js",
      "UI/src/components/formBuilder/preview-blocks/selectValue.js",
      "UI/src/components/formBuilder/preview-blocks/smart-select-input.svelte",
      "UI/src/components/formBuilder/preview-blocks/text-input.svelte",
      "UI/src/components/modals/BasicModal.svelte",
      "UI/src/components/modals/ShareModuleSubscriptionLink.svelte",
      "UI/src/routes.js",
      "UI/src/routes/association/Members/MembersBook.svelte",
      "UI/src/routes/association/Members/MembersList.svelte",
      "UI/src/routes/association/Members/detail/Detail.svelte",
      "UI/src/routes/association/Members/detail/DetailDrawer.svelte",
      "UI/src/routes/association/Members/detail/sections/Cloud.svelte",
      "UI/src/routes/association/Members/detail/sections/Info.svelte",
      "UI/src/routes/association/Members/detail/sections/subcomponents/Tessera.svelte",
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
      "UI/src/routes/profile/sections/Settings.svelte",
      "UI/src/routes/subscribe/Subscribe.svelte",
      "UI/src/routes/subscribe/wizard/Step0.svelte",
      "UI/src/routes/subscribe/wizard/Step1.svelte",
      "UI/src/routes/subscribe/wizard/Step2.svelte",
      "UI/src/routes/subscribe/wizard/partials/additional-fields.svelte",
      "UI/src/store/stores.js",
      "UI/src/utils/ApiMiddleware.js",
      "UI/src/utils/BrandTheme.js",
      "UI/src/utils/Permissions.js"
    ]
  }
];
for (const item of exhaustiveGuideBindings) {
    const spec = membershipLinksCardsAuthoredWorkflows[item.workflow_id];
    const section = item.section;
    if (!spec || spec.sections.some(old => old.path === section.path && old.id === section.id))
        throw new Error('Duplicate or unknown additional procedure: ' + item.workflow_id + ':' + section.id);
    spec.sections.push(section);
    spec.pages = [...new Set([...spec.pages, section.path])];
    spec.sources = [...new Set([...spec.sources, ...(item.sources || [])])];
    spec.dependencies = [...new Set([...spec.dependencies, ...(item.sources || [])])];
}
