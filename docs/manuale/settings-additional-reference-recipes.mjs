// Prose belongs to authored MDX; this module only records reviewed implementation evidence.
export const settingsAdditionalReviewedSources = Object.freeze({
  "UI/src/routes/profile/ProfileMenu.svelte": "160192309bf2770c1bcbc88045b1f68963dd6df947ba3a56d0c01a85266dc57d",
  "UI/src/routes/profile/sections/Settings.svelte": "f620ddf62f5a04e8b8b978674e32338496dcd55e15a290a9cb8804b21e28fe2e",
  "UI/src/routes/profile/sections/Account.svelte": "d1ca0c771a8044a47daaad294ba07ead7618e8cda9c1123ea6dd9c3a9791c200",
  "BE/application/views/profile_views.py": "37aca983a9edb10673bd76786b7f8efce7adfeaa129e9d4848df85ead31936d1",
  "BE/application/models/user_models.py": "3834a5655391ca750830d03175d7f663276697e50ab3fcfffcfd9ad45b7b931c",
  "BE/application/permissions_registry.py": "b8318ad63daa9452ac736b2152449b14c49f73bc1417622a4181b7c3ceb3b1fa",
  "BE/application/services/invoice_service.py": "98ba3acf0950b310fb5e47abbd8ba57cc9737b757fa461e5c2989ab18af95519",
  "BE/docmanager/views/printing_views.py": "f9127869836c46408d9e5f48fc16733c6fce66efedb2e2fa7f96344095e01753",
  "BE/application/views/payment_views.py": "4f45b99318afaa7189177f73cf82863f6f281dc8a2992b15dcf299b11f8e8c34",
  "UI/src/routes/subscribe/wizard/Step1.svelte": "50bbcd39de0d80293f59f6a0953a2498efd488ebf062582ae6b964c4b9a4c4f5",
  "UI/src/routes/subscribe/wizard/Step2.svelte": "871d4ff3b52672efd93b0348c3b587d355aeaa31edd60696d777e1b5db35a128",
  "UI/src/routes/subscribe/wizard/Step0.svelte": "764b6c9c39b7845650f0e8fec792c051d6d9fe259f4fd32a9e630726fc5ad737",
  "BE/application/tasks.py": "fa819d44d7013b60455306b01d1e9a0f4a33e3f6e8759754316ff3cf937e6e8a",
  "BE/core/celery.py": "c3973415cd7756df545453f0c7032c63830d1acea3d551e8debf9ede7347492d",
  "BE/application/serializers/courses_serializers.py": "45dc937b0da921f8466e4c21d22bedb8e74a52bd62b66f41f60e3c42b0320632",
  "BE/application/views/course_views.py": "5d32e168d9af6100c280b0d0043ca381742f7c2cc300e94f4edd1fd89cb9fc98",
  "BE/application/utils/subscriptions_utils.py": "ab922f87b8b1dd491bba41dd86eaabdefd1127c08bdaeb7be4f990657effd22a",
  "UI/src/routes/profile/sections/Stripe.svelte": "d9afbbae8b0b6532a42d4a5db00a034b7d94be48e1e30abaf5390ac4f26f954a",
  "UI/src/routes/profile/sections/TwoFactor.svelte": "45f92360b4bd393d80d06c7f890ca290f172d7166eb8527eb586aa8802cd2a39",
  "BE/application/views/two_fa_views.py": "ac0542429d0b1216eeb00c19dd30aed8f8691395b11b0ca0d2618a7f60719d5e"
});
const contracts = {
  "menu": [
    "UI/src/routes/profile/ProfileMenu.svelte",
    "const pages = profilePages;",
    12
  ],
  "settings_load": [
    "UI/src/routes/profile/sections/Settings.svelte",
    "async function fetchInfo()",
    47
  ],
  "settings_save": [
    "UI/src/routes/profile/sections/Settings.svelte",
    "async function updateSettings()",
    29
  ],
  "account_save": [
    "UI/src/routes/profile/sections/Account.svelte",
    "const updateAccountInformation = function ()",
    65
  ],
  "profile_update": [
    "BE/application/views/profile_views.py",
    "def profile_update(request):",
    125
  ],
  "settings_api": [
    "BE/application/views/profile_views.py",
    "def profile_settings(request):",
    110
  ],
  "model": [
    "BE/application/models/user_models.py",
    "enumerate_invoices =",
    70
  ],
  "permissions": [
    "BE/application/permissions_registry.py",
    "'profile/update': 'other.settings.update'",
    43
  ],
  "signature": [
    "UI/src/routes/profile/sections/Account.svelte",
    "Firma del presidente",
    111
  ],
  "print_text": [
    "UI/src/routes/profile/sections/Account.svelte",
    "Intestazione stampe",
    61
  ],
  "receipts": [
    "UI/src/routes/profile/sections/Settings.svelte",
    ">Numera Ricevute</label>",
    110
  ],
  "numbering": [
    "BE/application/services/invoice_service.py",
    "def get_next_invoice_number(",
    95
  ],
  "printed_invoice": [
    "BE/docmanager/views/printing_views.py",
    "enumerate_invoices = user.enumerate_invoices",
    14
  ],
  "hide_category": [
    "BE/docmanager/views/printing_views.py",
    "hide_category_name = invoice.sport_association.user.hide_category_name",
    23
  ],
  "dates": [
    "BE/application/views/payment_views.py",
    "if request.user.payment_date_equal_invoice_date:",
    5
  ],
  "categories": [
    "UI/src/routes/profile/sections/Settings.svelte",
    "label: 'Causale predefinita iscrizioni'",
    43
  ],
  "models": [
    "UI/src/routes/profile/sections/Account.svelte",
    "Modello Ricevute",
    174
  ],
  "extra": [
    "BE/docmanager/views/printing_views.py",
    "extra_text_invoices = invoice.sport_association.extra_text_invoices",
    18
  ],
  "card": [
    "UI/src/routes/profile/sections/Settings.svelte",
    ">Personalizza tessera</h1>",
    93
  ],
  "mandatory": [
    "UI/src/routes/profile/sections/Account.svelte",
    "Moduli d'iscrizione",
    119
  ],
  "public_fields": [
    "UI/src/routes/subscribe/wizard/Step1.svelte",
    "?.mandatory_email",
    24
  ],
  "public_signature": [
    "UI/src/routes/subscribe/wizard/Step2.svelte",
    "mandatory_signature",
    20
  ],
  "account_options": [
    "UI/src/routes/profile/sections/Settings.svelte",
    ">Modulo di iscrizione anonimo</label>",
    87
  ],
  "public_account": [
    "UI/src/routes/subscribe/wizard/Step0.svelte",
    "{#if !wizardData.sportAssociationData.user.disable_account_creation}",
    68
  ],
  "checkout": [
    "UI/src/routes/profile/sections/Account.svelte",
    ">Checkout</h5>",
    20
  ],
  "options": [
    "UI/src/routes/profile/sections/Settings.svelte",
    ">Pagamenti Online</label>",
    133
  ],
  "attendance": [
    "BE/application/tasks.py",
    "def auto_mark_attendance():",
    124
  ],
  "archive": [
    "BE/application/tasks.py",
    "def auto_archive_subscription():",
    32
  ],
  "schedules": [
    "BE/core/celery.py",
    "'auto-archive-subscription':",
    18
  ],
  "auto_paid": [
    "BE/application/views/payment_views.py",
    "def payment_add(request):",
    107
  ],
  "selected_rates": [
    "BE/application/serializers/courses_serializers.py",
    "if events:",
    80
  ],
  "rates": [
    "BE/application/views/course_views.py",
    "request.user.full_installments_plan:",
    28
  ],
  "zero_creation": [
    "BE/application/utils/subscriptions_utils.py",
    "if fee_amount > 0 or sport_association.user.show_zero_payments:",
    20
  ],
  "zero_display": [
    "BE/application/views/payment_views.py",
    "show_zero_payments = subscription.sport_association.user.show_zero_payments",
    23
  ],
  "dark": [
    "UI/src/routes/profile/sections/Settings.svelte",
    "bind:checked={settings.dark_mode}",
    20
  ],
  "stripe": [
    "UI/src/routes/profile/sections/Stripe.svelte",
    "{#if selfHosted}",
    54
  ],
  "stripe_methods": [
    "UI/src/routes/profile/sections/Stripe.svelte",
    "stripe_available_methods?.map",
    12
  ],
  "2fa": [
    "UI/src/routes/profile/sections/TwoFactor.svelte",
    "async function updateTwoFactors()",
    33
  ],
  "2fa_backend": [
    "BE/application/views/two_fa_views.py",
    "def two_fa_update(request):",
    58
  ]
};
export const settingsAdditionalSourceContracts = Object.freeze(Object.values(contracts));
const sections = [
    {
        "path": "docs/impostazioni.mdx",
        "title": "Introduzione",
        "id": "introduzione",
        "status": "verified",
        "reason": "Descrizione testuale controllata nelle sorgenti; non attesta una procedura eseguita.",
        "intent": "settings.introduzione",
        "contracts": [
            "menu",
            "permissions",
            "account_save",
            "settings_save"
        ]
    },
    {
        "path": "docs/impostazioni.mdx",
        "title": "Firma del presidente e timbro",
        "id": "firma-del-presidente-e-timbro",
        "status": "pending",
        "reason": "Procedura da esercitare e fotografare con backend reale.",
        "intent": "settings.firma.del.presidente.e.timbro",
        "contracts": [
            "signature",
            "profile_update"
        ]
    },
    {
        "path": "docs/impostazioni.mdx",
        "title": "Intestazione e piè di pagina stampe",
        "id": "intestazione-e-pie-di-pagina-stampe",
        "status": "pending",
        "reason": "Procedura da esercitare e fotografare con backend reale.",
        "intent": "settings.intestazione.e.pie.di.pagina.stampe",
        "contracts": [
            "print_text",
            "profile_update",
            "printed_invoice"
        ]
    },
    {
        "path": "docs/impostazioni.mdx",
        "title": "Ricevute",
        "id": "ricevute",
        "status": "verified",
        "reason": "Descrizione testuale controllata nelle sorgenti; non attesta una procedura eseguita.",
        "intent": "settings.ricevute",
        "contracts": [
            "receipts",
            "printed_invoice",
            "models"
        ]
    },
    {
        "path": "docs/impostazioni.mdx",
        "title": "Numera ricevute",
        "id": "numera-ricevute",
        "status": "pending",
        "reason": "Procedura da esercitare e fotografare con backend reale.",
        "intent": "settings.numera.ricevute",
        "contracts": [
            "receipts",
            "numbering",
            "printed_invoice",
            "settings_api"
        ]
    },
    {
        "path": "docs/impostazioni.mdx",
        "title": "Data uguale per ricevute e pagamenti",
        "id": "data-uguale-per-ricevute-e-pagamenti",
        "status": "pending",
        "reason": "Procedura da esercitare e fotografare con backend reale.",
        "intent": "settings.data.uguale.per.ricevute.e.pagamenti",
        "contracts": [
            "receipts",
            "dates",
            "settings_api"
        ]
    },
    {
        "path": "docs/impostazioni.mdx",
        "title": "Nascondi nome causale",
        "id": "nascondi-nome-causale",
        "status": "verified",
        "reason": "Descrizione testuale controllata nelle sorgenti; non attesta una procedura eseguita.",
        "intent": "settings.nascondi.nome.causale",
        "contracts": [
            "receipts",
            "hide_category",
            "settings_api"
        ]
    },
    {
        "path": "docs/impostazioni.mdx",
        "title": "Causale predefinita",
        "id": "causale-predefinita",
        "status": "verified",
        "reason": "Descrizione testuale controllata nelle sorgenti; non attesta una procedura eseguita.",
        "intent": "settings.causale.predefinita",
        "contracts": [
            "categories",
            "settings_api",
            "model"
        ]
    },
    {
        "path": "docs/impostazioni.mdx",
        "title": "Modelli documenti",
        "id": "modelli-documenti",
        "status": "pending",
        "reason": "Procedura da esercitare e fotografare con backend reale.",
        "intent": "settings.modelli.documenti",
        "contracts": [
            "models",
            "profile_update"
        ]
    },
    {
        "path": "docs/impostazioni.mdx",
        "title": "Testo extra ricevute",
        "id": "testo-extra-ricevute",
        "status": "verified",
        "reason": "Descrizione testuale controllata nelle sorgenti; non attesta una procedura eseguita.",
        "intent": "settings.testo.extra.ricevute",
        "contracts": [
            "print_text",
            "extra",
            "profile_update"
        ]
    },
    {
        "path": "docs/impostazioni.mdx",
        "title": "Personalizza tessera",
        "id": "personalizza-tessera",
        "status": "pending",
        "reason": "Procedura da esercitare e fotografare con backend reale.",
        "intent": "settings.personalizza.tessera",
        "contracts": [
            "card",
            "settings_api"
        ]
    },
    {
        "path": "docs/impostazioni.mdx",
        "title": "Moduli d'iscrizione",
        "id": "moduli-d-iscrizione",
        "status": "pending",
        "reason": "Procedura da esercitare e fotografare con backend reale.",
        "intent": "settings.moduli.d.iscrizione",
        "contracts": [
            "mandatory",
            "public_fields",
            "public_signature",
            "profile_update"
        ]
    },
    {
        "path": "docs/impostazioni.mdx",
        "title": "Modulo di iscrizione anonimo",
        "id": "modulo-di-iscrizione-anonimo",
        "status": "verified",
        "reason": "Descrizione testuale controllata nelle sorgenti; non attesta una procedura eseguita.",
        "intent": "settings.modulo.di.iscrizione.anonimo",
        "contracts": [
            "account_options",
            "public_account",
            "settings_api"
        ]
    },
    {
        "path": "docs/impostazioni.mdx",
        "title": "Account obbligatorio",
        "id": "account-obbligatorio",
        "status": "verified",
        "reason": "Descrizione testuale controllata nelle sorgenti; non attesta una procedura eseguita.",
        "intent": "settings.account.obbligatorio",
        "contracts": [
            "account_options",
            "public_account",
            "settings_api"
        ]
    },
    {
        "path": "docs/impostazioni.mdx",
        "title": "Checkout",
        "id": "checkout",
        "status": "needs_external_verification",
        "reason": "Il testo può essere salvato localmente; la sua visualizzazione nel checkout richiede disponibilità dei pagamenti online e creazione di una sessione Stripe reale.",
        "intent": "settings.checkout",
        "contracts": [
            "checkout",
            "profile_update"
        ]
    },
    {
        "path": "docs/impostazioni.mdx",
        "title": "Altre impostazioni",
        "id": "altre-impostazioni",
        "status": "verified",
        "reason": "Descrizione testuale controllata nelle sorgenti; non attesta una procedura eseguita.",
        "intent": "settings.altre.impostazioni",
        "contracts": [
            "options",
            "settings_api",
            "schedules"
        ]
    },
    {
        "path": "docs/impostazioni.mdx",
        "title": "Pagamenti online",
        "id": "pagamenti-online",
        "status": "verified",
        "reason": "Descrizione testuale controllata nelle sorgenti; non attesta una procedura eseguita.",
        "intent": "settings.pagamenti.online",
        "contracts": [
            "options",
            "stripe",
            "settings_api"
        ]
    },
    {
        "path": "docs/impostazioni.mdx",
        "title": "Presenze automatiche",
        "id": "presenze-automatiche",
        "status": "needs_external_verification",
        "reason": "Esecuzione programmata o servizio esterno da verificare nell’installazione.",
        "intent": "settings.presenze.automatiche",
        "contracts": [
            "options",
            "attendance",
            "schedules"
        ]
    },
    {
        "path": "docs/impostazioni.mdx",
        "title": "Archiviazione automatica",
        "id": "archiviazione-automatica",
        "status": "needs_external_verification",
        "reason": "Esecuzione programmata o servizio esterno da verificare nell’installazione.",
        "intent": "settings.archiviazione.automatica",
        "contracts": [
            "options",
            "archive",
            "schedules"
        ]
    },
    {
        "path": "docs/impostazioni.mdx",
        "title": "Pagamenti segnati come pagati",
        "id": "pagamenti-segnati-come-pagati",
        "status": "verified",
        "reason": "Descrizione testuale controllata nelle sorgenti; non attesta una procedura eseguita.",
        "intent": "settings.pagamenti.segnati.come.pagati",
        "contracts": [
            "options",
            "auto_paid",
            "settings_api"
        ]
    },
    {
        "path": "docs/impostazioni.mdx",
        "title": "Piano rate completo",
        "id": "piano-rate-completo",
        "status": "verified",
        "reason": "Descrizione testuale controllata nelle sorgenti; non attesta una procedura eseguita.",
        "intent": "settings.piano.rate.completo",
        "contracts": [
            "options",
            "rates",
            "selected_rates",
            "settings_api"
        ]
    },
    {
        "path": "docs/impostazioni.mdx",
        "title": "Pagamenti di importo zero",
        "id": "pagamenti-di-importo-zero",
        "status": "verified",
        "reason": "Descrizione testuale controllata nelle sorgenti; non attesta una procedura eseguita.",
        "intent": "settings.pagamenti.di.importo.zero",
        "contracts": [
            "options",
            "zero_creation",
            "zero_display"
        ]
    },
    {
        "path": "docs/impostazioni.mdx",
        "title": "Modalità scura",
        "id": "modalita-scura",
        "status": "verified",
        "reason": "Descrizione testuale controllata nelle sorgenti; non attesta una procedura eseguita.",
        "intent": "settings.modalita.scura",
        "contracts": [
            "dark",
            "settings_load",
            "settings_api"
        ]
    },
    {
        "path": "docs/impostazioni.mdx",
        "title": "Stripe - Pagamenti online",
        "id": "stripe-pagamenti-online",
        "status": "needs_external_verification",
        "reason": "Esecuzione programmata o servizio esterno da verificare nell’installazione.",
        "intent": "settings.stripe.pagamenti.online",
        "contracts": [
            "stripe",
            "stripe_methods",
            "profile_update"
        ]
    },
    {
        "path": "docs/impostazioni.mdx",
        "title": "Autenticazione a due fattori (2FA)",
        "id": "autenticazione-a-due-fattori-2fa",
        "status": "verified",
        "reason": "Descrizione testuale controllata nelle sorgenti; non attesta una procedura eseguita.",
        "intent": "settings.autenticazione.a.due.fattori.2fa",
        "contracts": [
            "2fa",
            "2fa_backend",
            "menu"
        ]
    }
];
export const settingsAdditionalReferenceSections = Object.freeze(sections.map(({contracts, ...section}) => Object.freeze(section)));
// Draft discovery exposes the existing descriptors without invoking source verification.
export const settingsAdditionalSourceContractKeys = Object.freeze(Object.keys(contracts));
export const settingsAdditionalEditorialSections = Object.freeze(sections.map(section =>
    Object.freeze({...section, verified: false, contracts: Object.freeze([...section.contracts])})));

export function settingsAdditionalReferences(source) {
 const evidence = new Map(Object.entries(contracts).map(([key, contract]) => {
  const reference = source(...contract);
  if (!reference || reference.path !== contract[0] || reference.canonical_source_sha256 !== settingsAdditionalReviewedSources[contract[0]]) throw new Error("Settings evidence changed; review required: " + contract[0]);
  return [key, reference];
 }));
 return sections.map(({contracts, ...section}) => ({...section, evidence: contracts.map(key => evidence.get(key))}));
}
