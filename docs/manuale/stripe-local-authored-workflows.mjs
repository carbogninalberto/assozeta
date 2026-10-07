// Local settings and list filters. No provider credentials, probe or transaction.
const tutorial = 'tutorials/come-impostare-stripe-pagamenti-online.mdx';
const faq = 'faq/come-configurare-i-pagamenti-online.mdx';
const points = [
    ['online-setting-disabled', 'Pagamenti Online disattivato nelle impostazioni generali.'],
    ['online-setting-enabled-before-save', 'Opzione selezionata prima del salvataggio.'],
    ['online-setting-enabled-after-reload', 'Opzione attiva conservata dopo il ricaricamento.'],
    ['online-setting-disabled-before-save', 'Opzione deselezionata prima del salvataggio.'],
    ['online-setting-disabled-after-reload', 'Opzione disattivata conservata dopo il ricaricamento.'],
    ['online-setting-reader-disabled', 'Collaboratore in sola lettura: interruttore e salvataggio disabilitati.'],
    ['stripe-filter-baseline', 'Elenco dei pagamenti prima di scegliere un metodo.'],
    ['stripe-filter-options', 'Filtro Metodo pagamento con la voce Stripe.'],
    ['stripe-filter-empty-result', 'Nessuna registrazione Stripe nel periodo della dimostrazione.'],
    ['stripe-filter-paid-empty-result', 'Filtri Stripe e Pagato applicati insieme.'],
    ['stripe-filter-pending-empty-result', 'Filtri Stripe e In attesa applicati insieme.'],
    ['stripe-filter-reset', 'Metodo e stato azzerati: tornano i pagamenti iniziali.'],
    ['stripe-filter-reader-empty-result', 'Consultazione del filtro Stripe da parte del collaboratore.'],
];
const section = (path, id, title, indexes, steps) => ({path, id, title,
    checkpoints: indexes.map(i => points[i][0]),
    images: steps.map(([, i]) => ({checkpoint: points[i][0],
        from: `/images/pagamenti/stripe-controlli-locali/${i + 1}.placeholder.svg`})), insert_images: []});
const sources = [
    'UI/src/routes/profile/sections/Settings.svelte',
    'UI/src/routes/profile/sections/InstanceIntegration.svelte',
    'UI/src/routes/activities/payment/PaymentList.svelte',
    'UI/src/routes/accounting/payment/PaymentList.svelte',
    'UI/src/components/filters/FilterSelect.svelte',
    'UI/src/components/formBuilder/preview-blocks/smart-select-input.svelte',
    'UI/src/components/tables/BKNDatatable.svelte',
    'UI/src/utils/Permissions.js', 'UI/src/utils/ApiMiddleware.js',
    'UI/src/components/Sidebar.svelte', 'UI/src/routes.js',
    'BE/application/views/profile_views.py', 'BE/application/serializers/auth_serializers.py',
    'BE/application/views/payment_views.py', 'BE/application/utils/stripe_utils.py',
    'BE/application/views/stripe_views.py', 'BE/application/permissions_registry.py',
    'BE/instance/permissions.py', 'BE/core/middleware.py',
];
export const stripeLocalAuthoredWorkflows = Object.freeze({
    'stripe-local-settings-filters': {
        version: 1, script: 'selfhost/tests/browser/manuale/stripe-local-settings-filters.mjs',
        prefix: 'images/pagamenti/stripe-controlli-locali/',
        pages: [tutorial, faq, 'docs/pagamenti.mdx'], sources,
        dependencies: ['docs/manuale/stripe-local-authored-workflows.mjs',
            'selfhost/tests/browser/manuale/stripe-local-settings-filters.mjs',
            'selfhost/tests/browser/manuale/scenario.mjs', 'selfhost/tests/browser/manuale/frame.mjs',
            'selfhost/tests/browser/manuale/redaction.mjs', 'selfhost/tests/browser/playwright.manual.config.mjs',
            'BE/application/management/commands/seed_manuale.py',
            'BE/application/management/commands/run_manuale_instance.py'],
        checkpoints: points.map(([id, caption]) => ({id, caption})),
        sections: [
            section(tutorial, 'attivare-i-pagamenti-online', 'Attivare i pagamenti online', [0, 1, 2],
                [['Apri le impostazioni generali', 0], ['Attiva e salva', 1], ['Riapri e controlla', 2]]),
            section(faq, 'posso-disattivare-i-pagamenti-online', 'Posso disattivare i pagamenti online?', [2, 3, 4, 5],
                [['Disattiva l’opzione', 3], ['Salva e ricontrolla', 4]]),
            section('docs/pagamenti.mdx', 'come-vedere-i-pagamenti-avvenuti-via-stripe', 'Come vedere i pagamenti avvenuti via Stripe', [6, 7, 8, 9, 10, 11, 12],
                [['Apri il filtro del metodo', 7], ['Scegli Stripe', 8], ['Distingui pagato e in attesa', 9], ['Ripristina la consultazione', 11]]),
            section(tutorial, 'monitorare-i-pagamenti-stripe', 'Monitorare i pagamenti Stripe', [6, 7, 8, 9, 10, 11, 12],
                [['Consulta le registrazioni', 6], ['Isola metodo e stato', 9], ['Controlla anche un risultato vuoto', 8]]),
        ],
        outcome: {field: 'stripe_local_authored_workflow', expected: {
            integration_disabled_before_and_after: true, enabled_setting_persisted: true,
            disabled_setting_persisted: true, reader_toggle_disabled: true,
            reader_setting_write_status: 403, reader_denial_preserved_setting: true,
            baseline_payment_count: 3, stripe_filter_empty: true, stripe_paid_filter_empty: true, stripe_pending_filter_empty: true,
            reader_stripe_filter_empty: true, filter_reset_restored_rows: true,
            payments_unchanged: true, settings_restored: true, provider_actions: 0,
        }},
    },
});
