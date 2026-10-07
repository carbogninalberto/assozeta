// Authored MDX is the only prose source. These descriptors seal code review only.
// No browser report, capture or runtime success is implied by a verified text reference.
export const communicationAuthReviewedSources = Object.freeze({
    "UI/src/routes/association/communication/messages/Messages.svelte": "7a5862099d6165b59b93bbb3ff1be6fa7703a85db6dbae7749aca198d115a43f",
    "UI/src/routes/association/communication/messages/modals/Send.svelte": "20aab21730a6af1f44cea41c3b6e2134b7f0d386897c7e81a1686d41c4c0b56b",
    "UI/src/utils/Functions.js": "c9ed79a8214d4e2ceb9d91e2cd3d52f42a32706f96e2890082ba5584c6787147",
    "BE/communications/models.py": "c87dda8f5fca11218b26cf3f6996090151b50d7bfb88f202b5d0935b40be857f",
    "BE/communications/views.py": "d8adf9cfd4b2dfcc3c0a7b2f9cae231722dc44ef6fbda4caffa3dbf2d98338b5",
    "BE/application/views/statistic_views.py": "c948d6e6c9d0846eca66f8d1c13ffa97d7e2071a7e4f27ebd2022ccd28623fb7",
    "BE/communications/serializers.py": "8fcc36f71d436a2cf821604a24dcb7325cbd70dfde2dbf557c8702e48d195f0d",
    "UI/src/routes/association/communication/settings/sections/EmailSMTP.svelte": "878c6421a023d8e0135b8c0e871c20cbc7391aee8268478b22327efabd9128bb",
    "UI/src/routes/association/communication/settings/Menu.svelte": "2a152a0eeaf3196dd1b569e4bd656da561f93836a84049fed6cbb41186f9e610",
    "UI/src/routes/association/communication/settings/sections/Stats.svelte": "f595cb94d796d748b44c26c321702fdd387118b039909ad6c6f89a76949d2a65",
    "BE/application/tasks.py": "fa819d44d7013b60455306b01d1e9a0f4a33e3f6e8759754316ff3cf937e6e8a",
    "UI/src/routes/association/communication/automation/Automation.svelte": "8a0b6012241c3a32ab2bc92dc119793579d08efd19934b6afaa7f73885d6e699",
    "UI/src/routes/association/communication/automation/editor/AutomationEditor.svelte": "03a5be42ad985df535861ee1c5dc2df4f97876b7ae5a43fdc313456907a15c6a",
    "BE/application/signals.py": "81f0445115b59c4aeb94cefcd40d3379657f5bc00f4719d8e4302d30cb941a2f",
    "UI/src/components/inputs/email-builder/EmailBuilder.svelte": "96085ea1094e0a8ef3c48e38b9ad17b31f9a1d2eb35447a42d10ed5b5de3352d",
    "UI/src/routes/profile/ProfileMenu.svelte": "160192309bf2770c1bcbc88045b1f68963dd6df947ba3a56d0c01a85266dc57d",
    "UI/src/routes/profile/sections/TwoFactor.svelte": "45f92360b4bd393d80d06c7f890ca290f172d7166eb8527eb586aa8802cd2a39",
    "BE/application/views/two_fa_views.py": "ac0542429d0b1216eeb00c19dd30aed8f8691395b11b0ca0d2618a7f60719d5e",
    "BE/application/views/auth_views.py": "ff45ee9f1e2665b0ab19e7ae832834a481256e4ff266d7d0cec3860a5854975c",
    "UI/src/routes/login/Login.svelte": "ec5c86c305013bff52314dfc0f8ead8d8174986fd0d9bc4adb183f970d3dd4d7",
    "UI/src/routes.js": "58bddc98548095f14f39fa3141277de8cd00e2133faf49f6761131b109d254b0",
    "UI/src/components/Sidebar.svelte": "5fd791241452c301697730caf21a841c29a186876c33834b579d7846610d2568",
    "UI/src/components/inputs/email-builder/emailbuilder.js": "1e96505ab77517ff72f491223a91992d80d33ad8b79a42d13868c844ab331f07"
});

const sourceKeys = Object.freeze(["message_types", "message_form", "message_validation", "message_permissions", "message_save", "post_form", "post_validation", "send_form", "import_emails", "message_model", "post_api", "post_dashboard", "staff_model", "email_api", "email_validation", "email_config", "email_service", "smtp_verify", "smtp_fields", "smtp_controls", "settings_menu", "email_stats", "email_logs", "email_reset", "workflow_list", "workflow_buttons", "workflow_new", "workflow_init", "workflow_add", "workflow_save", "workflow_trigger", "workflow_trigger_close", "workflow_email", "workflow_wait", "workflow_steps", "workflow_disabled", "workflow_footer", "workflow_model", "workflow_validation", "workflow_update", "workflow_signals", "workflow_execution", "email_builder", "profile_menu", "twofa_page", "twofa_update", "twofa_setup", "twofa_fields", "twofa_apps", "twofa_backend", "twofa_secret", "login_otp", "login_dialog", "communication_routes", "communication_settings_route", "communication_navigation", "email_builder_save_button", "messages_write_api", "message_input", "message_read"]);
export const communicationAuthSourceContracts = Object.freeze([
    [
        "UI/src/routes/association/communication/messages/Messages.svelte",
        "const typeDictionary = {",
        7
    ],
    [
        "UI/src/routes/association/communication/messages/Messages.svelte",
        "Creazione di un messaggio",
        89
    ],
    [
        "UI/src/routes/association/communication/messages/Messages.svelte",
        "function initForm()",
        65
    ],
    [
        "UI/src/routes/association/communication/messages/Messages.svelte",
        "canPerformAction('association.communication.messages.create')",
        25
    ],
    [
        "UI/src/routes/association/communication/messages/Messages.svelte",
        "async function create(data)",
        36
    ],
    [
        "UI/src/routes/association/communication/messages/Messages.svelte",
        "Crea un post",
        31
    ],
    [
        "UI/src/routes/association/communication/messages/Messages.svelte",
        "function initPostForm()",
        40
    ],
    [
        "UI/src/routes/association/communication/messages/modals/Send.svelte",
        "export let type =",
        132
    ],
    [
        "UI/src/utils/Functions.js",
        "export const getAthletesEmails =",
        5
    ],
    [
        "BE/communications/models.py",
        "class Message(models.Model):",
        17
    ],
    [
        "BE/communications/views.py",
        "def configuration_send_post(request):",
        23
    ],
    [
        "BE/application/views/statistic_views.py",
        "communication_message_transaction_posts = MessageTransaction.objects.filter(",
        25
    ],
    [
        "BE/communications/models.py",
        "class StaffBoardMessage(models.Model):",
        21
    ],
    [
        "BE/communications/views.py",
        "def configuration_send_email(request):",
        60
    ],
    [
        "BE/communications/serializers.py",
        "class EmailSerializer(serializers.Serializer):",
        21
    ],
    [
        "BE/communications/models.py",
        "class CommunicationConfiguration(models.Model):",
        34
    ],
    [
        "BE/communications/models.py",
        "def send_email(self, subject, body, recipient_list,",
        88
    ],
    [
        "BE/communications/models.py",
        "def verify_smtp(self, send_email=True):",
        32
    ],
    [
        "UI/src/routes/association/communication/settings/sections/EmailSMTP.svelte",
        "SMTP Host</label>",
        137
    ],
    [
        "UI/src/routes/association/communication/settings/sections/EmailSMTP.svelte",
        "Verifica SMTP",
        12
    ],
    [
        "UI/src/routes/association/communication/settings/Menu.svelte",
        "let pages = ['email', 'stats'];",
        91
    ],
    [
        "UI/src/routes/association/communication/settings/sections/Stats.svelte",
        "async function fetchData()",
        120
    ],
    [
        "BE/communications/views.py",
        "def communication_email_logs_list(request):",
        21
    ],
    [
        "BE/application/tasks.py",
        "def reset_email_balance():",
        13
    ],
    [
        "UI/src/routes/association/communication/automation/Automation.svelte",
        "field: 'enabled'",
        25
    ],
    [
        "UI/src/routes/association/communication/automation/Automation.svelte",
        "if (row.enabled) {",
        175
    ],
    [
        "UI/src/routes/association/communication/automation/Automation.svelte",
        "push('/communication/automation/editor');",
        5
    ],
    [
        "UI/src/routes/association/communication/automation/editor/AutomationEditor.svelte",
        "let triggerAvailable = true;",
        41
    ],
    [
        "UI/src/routes/association/communication/automation/editor/AutomationEditor.svelte",
        "function addEvent(type)",
        46
    ],
    [
        "UI/src/routes/association/communication/automation/editor/AutomationEditor.svelte",
        "async function save()",
        37
    ],
    [
        "UI/src/routes/association/communication/automation/editor/AutomationEditor.svelte",
        "<optgroup label=\"Eventi Generici\">",
        39
    ],
    [
        "UI/src/routes/association/communication/automation/editor/AutomationEditor.svelte",
        "title=\"Evento di attivazione\"",
        12
    ],
    [
        "UI/src/routes/association/communication/automation/editor/AutomationEditor.svelte",
        "title=\"Invia un messaggio\"",
        116
    ],
    [
        "UI/src/routes/association/communication/automation/editor/AutomationEditor.svelte",
        "{:else if node.id == 'wait'}",
        104
    ],
    [
        "UI/src/routes/association/communication/automation/editor/AutomationEditor.svelte",
        "<h6>scegli il prossimo step</h6>",
        46
    ],
    [
        "UI/src/routes/association/communication/automation/editor/AutomationEditor.svelte",
        "{#if automationData?.enabled && !automationData?.onlyView}",
        19
    ],
    [
        "UI/src/routes/association/communication/automation/editor/AutomationEditor.svelte",
        "on:click={() => push('/communication/automation')}",
        9
    ],
    [
        "BE/communications/models.py",
        "class AutomationWorkflow(models.Model):",
        115
    ],
    [
        "BE/communications/serializers.py",
        "class AutomationWorkflowSerializer(serializers.ModelSerializer):",
        19
    ],
    [
        "BE/communications/views.py",
        "def communication_workflows_update(request, workflow_id):",
        41
    ],
    [
        "BE/application/signals.py",
        "def new_subscription_callback(sender, **kwargs):",
        35
    ],
    [
        "BE/application/tasks.py",
        "def workflow_block_execute(workflow_id, subscription_id=None, idx=None):",
        85
    ],
    [
        "UI/src/components/inputs/email-builder/EmailBuilder.svelte",
        "async function save()",
        45
    ],
    [
        "UI/src/routes/profile/ProfileMenu.svelte",
        "on:click|preventDefault={() => changeSubPage('twofa')}",
        7
    ],
    [
        "UI/src/routes/profile/sections/TwoFactor.svelte",
        "let twoFaData = {",
        15
    ],
    [
        "UI/src/routes/profile/sections/TwoFactor.svelte",
        "async function updateTwoFactors()",
        43
    ],
    [
        "UI/src/routes/profile/sections/TwoFactor.svelte",
        "async function generateQrCode()",
        26
    ],
    [
        "UI/src/routes/profile/sections/TwoFactor.svelte",
        "Abilita Doppio Fattore",
        62
    ],
    [
        "UI/src/routes/profile/sections/TwoFactor.svelte",
        "Scannerizza il codice QR con un'applicazione",
        3
    ],
    [
        "BE/application/views/two_fa_views.py",
        "def two_fa_update(request):",
        51
    ],
    [
        "BE/application/views/two_fa_views.py",
        "def two_fa_setup(request):",
        21
    ],
    [
        "BE/application/views/auth_views.py",
        "if user.two_fa:",
        9
    ],
    [
        "UI/src/routes/login/Login.svelte",
        "response.msg == 'OTP code required.'",
        17
    ],
    [
        "UI/src/routes.js",
        "'/communication/messages': wrap({",
        98
    ],
    [
        "UI/src/routes.js",
        "'/communication/configuration': wrap({",
        19
    ],
    [
        "UI/src/components/Sidebar.svelte",
        "<span class=\"menu-text\">Comunicazioni</span>",
        94
    ],
    [
        "UI/src/components/inputs/email-builder/emailbuilder.js",
        "children: 'Salva',",
        12
    ],
    [
        "BE/communications/views.py",
        "def communication_messages_add(request):",
        19
    ],
    [
        "BE/communications/serializers.py",
        "class MessageInputSerializer(serializers.ModelSerializer):",
        12
    ],
    [
        "BE/communications/serializers.py",
        "class MessageSerializer(serializers.ModelSerializer):",
        16
    ]
].map(contract => Object.freeze(contract)));

const sections = [
    {
        "path": "docs/comunicazioni.mdx",
        "title": "Comunicazioni",
        "id": "comunicazioni",
        "status": "pending",
        "reason": "Procedura descritta dalle sorgenti; mancano esercizio in browser, controlli di persistenza e schermate reali.",
        "intent": "communications.navigation",
        "contracts": [
            "message_permissions",
            "message_types",
            "staff_model",
            "workflow_new",
            "settings_menu",
            "communication_navigation",
            "communication_routes",
            "communication_settings_route"
        ]
    },
    {
        "path": "docs/comunicazioni.mdx",
        "title": "Post",
        "id": "post",
        "status": "pending",
        "reason": "Procedura descritta dalle sorgenti; mancano esercizio in browser, controlli di persistenza e schermate reali.",
        "intent": "communications.post.publish",
        "contracts": [
            "post_form",
            "post_validation",
            "post_api",
            "post_dashboard"
        ]
    },
    {
        "path": "docs/comunicazioni.mdx",
        "title": "Messaggi",
        "id": "messaggi",
        "status": "pending",
        "reason": "La sezione descrive ora la procedura completa di salvataggio e riapertura; richiede il flusso reale e non una sola verifica testuale.",
        "intent": "communications.email.describe",
        "contracts": [
            "message_types",
            "message_form",
            "message_validation",
            "message_save",
            "email_api",
            "email_service",
            "messages_write_api",
            "message_input",
            "message_read"
        ]
    },
    {
        "path": "docs/comunicazioni.mdx",
        "title": "Come inviare una comunicazione SMS/email?",
        "id": "come-inviare-una-comunicazione-sms-email",
        "status": "needs_external_verification",
        "reason": "I moduli di invio sono ispezionabili localmente, ma consegna SMTP e ricezione nella casella richiedono la verifica esterna; SMS non è un canale disponibile.",
        "intent": "communications.email.send",
        "contracts": [
            "message_form",
            "send_form",
            "import_emails",
            "email_api",
            "email_validation",
            "email_logs"
        ]
    },
    {
        "path": "docs/comunicazioni.mdx",
        "title": "Quanti SMS posso inviare?",
        "id": "quanti-sms-posso-inviare",
        "status": "unsupported",
        "reason": "Il percorso del precedente manuale non è disponibile nel selettore o nei canali della versione corrente.",
        "intent": "communications.sms.unavailable",
        "contracts": [
            "message_model",
            "message_types",
            "message_form",
            "settings_menu"
        ]
    },
    {
        "path": "docs/comunicazioni.mdx",
        "title": "Automazioni via Workflow",
        "id": "automazioni-via-workflow",
        "status": "verified",
        "reason": "Descrizione testuale verificata nelle sorgenti; non attesta una procedura eseguita in browser.",
        "intent": "communications.workflow.describe",
        "contracts": [
            "workflow_init",
            "workflow_model",
            "workflow_execution"
        ]
    },
    {
        "path": "docs/comunicazioni.mdx",
        "title": "Trigger disponibili",
        "id": "trigger-disponibili",
        "status": "verified",
        "reason": "Descrizione testuale verificata nelle sorgenti; non attesta una procedura eseguita in browser.",
        "intent": "communications.workflow.triggers.describe",
        "contracts": [
            "workflow_trigger",
            "workflow_signals"
        ]
    },
    {
        "path": "docs/comunicazioni.mdx",
        "title": "Blocchi del workflow",
        "id": "blocchi-del-workflow",
        "status": "verified",
        "reason": "Descrizione testuale verificata nelle sorgenti; non attesta una procedura eseguita in browser.",
        "intent": "communications.workflow.blocks.describe",
        "contracts": [
            "workflow_steps",
            "workflow_add",
            "workflow_email",
            "workflow_wait",
            "workflow_validation",
            "workflow_execution"
        ]
    },
    {
        "path": "docs/comunicazioni.mdx",
        "title": "Limiti delle comunicazioni",
        "id": "limiti-delle-comunicazioni",
        "status": "needs_external_verification",
        "reason": "Servizio, recapito, programmazione, privacy o recupero richiedono evidenze esterne alla sola applicazione.",
        "intent": "communications.email.limits",
        "contracts": [
            "email_config",
            "email_service",
            "email_reset",
            "email_logs"
        ]
    },
    {
        "path": "docs/comunicazioni.mdx",
        "title": "Come creare un workflow?",
        "id": "come-creare-un-workflow",
        "status": "pending",
        "reason": "Procedura descritta dalle sorgenti; mancano esercizio in browser, controlli di persistenza e schermate reali.",
        "intent": "communications.workflow.create",
        "contracts": [
            "workflow_new",
            "workflow_save",
            "workflow_trigger_close",
            "workflow_steps",
            "workflow_footer",
            "workflow_model",
            "workflow_execution"
        ]
    },
    {
        "path": "docs/comunicazioni.mdx",
        "title": "Risorse correlate",
        "id": "risorse-correlate",
        "status": "verified",
        "reason": "Descrizione testuale verificata nelle sorgenti; non attesta una procedura eseguita in browser.",
        "intent": "communications.references",
        "contracts": [
            "workflow_new",
            "workflow_trigger",
            "workflow_steps"
        ]
    },
    {
        "path": "faq/come-inviare-email-nuovi-iscritti.mdx",
        "title": "Prima di iniziare",
        "id": "prima-di-iniziare",
        "status": "needs_external_verification",
        "reason": "Servizio, recapito, programmazione, privacy o recupero richiedono evidenze esterne alla sola applicazione.",
        "intent": "communications.welcome.prepare",
        "contracts": [
            "workflow_trigger",
            "workflow_model",
            "smtp_fields",
            "smtp_controls",
            "smtp_verify",
            "settings_menu",
            "email_stats",
            "email_config",
            "email_service",
            "communication_settings_route"
        ]
    },
    {
        "path": "faq/come-inviare-email-nuovi-iscritti.mdx",
        "title": "Creare un'automazione per inviare un'email ai nuovi iscritti",
        "id": "creare-un-automazione-per-inviare-un-email-ai-nuovi-iscritti",
        "status": "pending",
        "reason": "Procedura descritta dalle sorgenti; mancano esercizio in browser, controlli di persistenza e schermate reali.",
        "intent": "communications.welcome.create",
        "contracts": [
            "workflow_new",
            "workflow_init",
            "workflow_trigger",
            "workflow_trigger_close",
            "workflow_steps",
            "workflow_email",
            "email_builder",
            "workflow_save",
            "workflow_footer",
            "workflow_buttons",
            "workflow_signals",
            "email_builder_save_button"
        ]
    },
    {
        "path": "faq/come-inviare-email-nuovi-iscritti.mdx",
        "title": "Note e consigli",
        "id": "note-e-consigli",
        "status": "needs_external_verification",
        "reason": "Servizio, recapito, programmazione, privacy o recupero richiedono evidenze esterne alla sola applicazione.",
        "intent": "communications.welcome.delivery",
        "contracts": [
            "workflow_execution",
            "workflow_buttons",
            "workflow_disabled",
            "email_stats",
            "email_logs",
            "email_service"
        ]
    },
    {
        "path": "tutorials/come-impostare-automazioni-workflow.mdx",
        "title": "Indice",
        "id": "indice",
        "status": "verified",
        "reason": "Descrizione testuale verificata nelle sorgenti; non attesta una procedura eseguita in browser.",
        "intent": "communications.workflow.contents",
        "contracts": [
            "workflow_init",
            "workflow_steps",
            "workflow_buttons"
        ]
    },
    {
        "path": "tutorials/come-impostare-automazioni-workflow.mdx",
        "title": "Introduzione ai workflow",
        "id": "introduzione-ai-workflow",
        "status": "verified",
        "reason": "Descrizione testuale verificata nelle sorgenti; non attesta una procedura eseguita in browser.",
        "intent": "communications.workflow.introduction",
        "contracts": [
            "workflow_init",
            "workflow_model",
            "workflow_add",
            "workflow_execution"
        ]
    },
    {
        "path": "tutorials/come-impostare-automazioni-workflow.mdx",
        "title": "Trigger disponibili",
        "id": "trigger-disponibili",
        "status": "verified",
        "reason": "Descrizione testuale verificata nelle sorgenti; non attesta una procedura eseguita in browser.",
        "intent": "communications.workflow.triggers.describe",
        "contracts": [
            "workflow_trigger",
            "workflow_signals"
        ]
    },
    {
        "path": "tutorials/come-impostare-automazioni-workflow.mdx",
        "title": "Destinatari nel trigger \"Data programmata\"",
        "id": "destinatari-nel-trigger-data-programmata",
        "status": "needs_external_verification",
        "reason": "Servizio, recapito, programmazione, privacy o recupero richiedono evidenze esterne alla sola applicazione.",
        "intent": "communications.workflow.scheduled.targets",
        "contracts": [
            "workflow_trigger",
            "workflow_model"
        ]
    },
    {
        "path": "tutorials/come-impostare-automazioni-workflow.mdx",
        "title": "Azioni disponibili",
        "id": "azioni-disponibili",
        "status": "verified",
        "reason": "Descrizione testuale verificata nelle sorgenti; non attesta una procedura eseguita in browser.",
        "intent": "communications.workflow.actions.describe",
        "contracts": [
            "workflow_steps",
            "workflow_add",
            "workflow_email",
            "workflow_wait",
            "workflow_validation",
            "workflow_execution"
        ]
    },
    {
        "path": "tutorials/come-impostare-automazioni-workflow.mdx",
        "title": "Destinatari delle azioni",
        "id": "destinatari-delle-azioni",
        "status": "verified",
        "reason": "Descrizione testuale verificata nelle sorgenti; non attesta una procedura eseguita in browser.",
        "intent": "communications.workflow.recipients.describe",
        "contracts": [
            "workflow_email",
            "workflow_execution",
            "workflow_model"
        ]
    },
    {
        "path": "tutorials/come-impostare-automazioni-workflow.mdx",
        "title": "Creare un workflow passo dopo passo",
        "id": "creare-un-workflow-passo-dopo-passo",
        "status": "pending",
        "reason": "Procedura descritta dalle sorgenti; mancano esercizio in browser, controlli di persistenza e schermate reali.",
        "intent": "communications.workflow.create",
        "contracts": [
            "workflow_new",
            "workflow_init",
            "workflow_save",
            "workflow_trigger_close",
            "workflow_model"
        ]
    },
    {
        "path": "tutorials/come-impostare-automazioni-workflow.mdx",
        "title": "Configurare il trigger",
        "id": "configurare-il-trigger",
        "status": "pending",
        "reason": "Procedura descritta dalle sorgenti; mancano esercizio in browser, controlli di persistenza e schermate reali.",
        "intent": "communications.workflow.trigger.configure",
        "contracts": [
            "workflow_trigger",
            "workflow_trigger_close",
            "workflow_model"
        ]
    },
    {
        "path": "tutorials/come-impostare-automazioni-workflow.mdx",
        "title": "Aggiungere le azioni",
        "id": "aggiungere-le-azioni",
        "status": "pending",
        "reason": "Procedura descritta dalle sorgenti; mancano esercizio in browser, controlli di persistenza e schermate reali.",
        "intent": "communications.workflow.steps.add",
        "contracts": [
            "workflow_steps",
            "workflow_add",
            "workflow_email",
            "workflow_wait",
            "workflow_save"
        ]
    },
    {
        "path": "tutorials/come-impostare-automazioni-workflow.mdx",
        "title": "Se scegli \"Invio email\":",
        "id": "se-scegli-invio-email",
        "status": "pending",
        "reason": "Procedura descritta dalle sorgenti; mancano esercizio in browser, controlli di persistenza e schermate reali.",
        "intent": "communications.workflow.email.configure",
        "contracts": [
            "workflow_steps",
            "workflow_email",
            "email_builder",
            "workflow_save",
            "workflow_footer",
            "email_builder_save_button"
        ]
    },
    {
        "path": "tutorials/come-impostare-automazioni-workflow.mdx",
        "title": "Se scegli \"Invio SMS\":",
        "id": "se-scegli-invio-sms",
        "status": "unsupported",
        "reason": "Il percorso del precedente manuale non è disponibile nel selettore o nei canali della versione corrente.",
        "intent": "communications.workflow.sms.unavailable",
        "contracts": [
            "workflow_email",
            "workflow_validation"
        ]
    },
    {
        "path": "tutorials/come-impostare-automazioni-workflow.mdx",
        "title": "Se scegli \"Attesa\":",
        "id": "se-scegli-attesa",
        "status": "pending",
        "reason": "Procedura descritta dalle sorgenti; mancano esercizio in browser, controlli di persistenza e schermate reali.",
        "intent": "communications.workflow.wait.configure",
        "contracts": [
            "workflow_steps",
            "workflow_add",
            "workflow_wait",
            "workflow_save",
            "workflow_execution"
        ]
    },
    {
        "path": "tutorials/come-impostare-automazioni-workflow.mdx",
        "title": "Esempi di workflow utili",
        "id": "esempi-di-workflow-utili",
        "status": "verified",
        "reason": "Descrizione testuale verificata nelle sorgenti; non attesta una procedura eseguita in browser.",
        "intent": "communications.workflow.examples.describe",
        "contracts": [
            "workflow_trigger",
            "workflow_steps"
        ]
    },
    {
        "path": "tutorials/come-impostare-automazioni-workflow.mdx",
        "title": "Email di benvenuto",
        "id": "email-di-benvenuto",
        "status": "pending",
        "reason": "Procedura descritta dalle sorgenti; mancano esercizio in browser, controlli di persistenza e schermate reali.",
        "intent": "communications.welcome.example",
        "contracts": [
            "workflow_trigger",
            "workflow_email",
            "workflow_signals",
            "workflow_execution"
        ]
    },
    {
        "path": "tutorials/come-impostare-automazioni-workflow.mdx",
        "title": "Promemoria pagamento dopo l'iscrizione",
        "id": "promemoria-pagamento-dopo-l-iscrizione",
        "status": "unsupported",
        "reason": "Il percorso del precedente manuale non è disponibile nel selettore o nei canali della versione corrente.",
        "intent": "communications.workflow.course.payment.unsupported",
        "contracts": [
            "workflow_trigger",
            "workflow_add",
            "workflow_execution"
        ]
    },
    {
        "path": "tutorials/come-impostare-automazioni-workflow.mdx",
        "title": "Promemoria certificato medico",
        "id": "promemoria-certificato-medico",
        "status": "unsupported",
        "reason": "Il percorso del precedente manuale non è disponibile nel selettore o nei canali della versione corrente.",
        "intent": "communications.workflow.medical.unsupported",
        "contracts": [
            "workflow_trigger"
        ]
    },
    {
        "path": "tutorials/come-impostare-automazioni-workflow.mdx",
        "title": "Conferma pagamento",
        "id": "conferma-pagamento",
        "status": "unsupported",
        "reason": "Il percorso del precedente manuale non è disponibile nel selettore o nei canali della versione corrente.",
        "intent": "communications.workflow.payment.unsupported",
        "contracts": [
            "workflow_trigger"
        ]
    },
    {
        "path": "tutorials/come-impostare-automazioni-workflow.mdx",
        "title": "Comunicazione di inizio stagione",
        "id": "comunicazione-di-inizio-stagione",
        "status": "needs_external_verification",
        "reason": "Servizio, recapito, programmazione, privacy o recupero richiedono evidenze esterne alla sola applicazione.",
        "intent": "communications.workflow.season.schedule",
        "contracts": [
            "workflow_trigger",
            "workflow_model",
            "workflow_execution"
        ]
    },
    {
        "path": "tutorials/come-impostare-automazioni-workflow.mdx",
        "title": "Attivare, disattivare e modificare i workflow",
        "id": "attivare-disattivare-e-modificare-i-workflow",
        "status": "verified",
        "reason": "Descrizione testuale verificata nelle sorgenti; non attesta una procedura eseguita in browser.",
        "intent": "communications.workflow.state.describe",
        "contracts": [
            "workflow_list",
            "workflow_buttons",
            "workflow_update"
        ]
    },
    {
        "path": "tutorials/come-impostare-automazioni-workflow.mdx",
        "title": "Attivare un workflow",
        "id": "attivare-un-workflow",
        "status": "pending",
        "reason": "Procedura descritta dalle sorgenti; mancano esercizio in browser, controlli di persistenza e schermate reali.",
        "intent": "communications.workflow.enable",
        "contracts": [
            "workflow_buttons",
            "workflow_update",
            "workflow_model",
            "workflow_signals"
        ]
    },
    {
        "path": "tutorials/come-impostare-automazioni-workflow.mdx",
        "title": "Disattivare un workflow",
        "id": "disattivare-un-workflow",
        "status": "pending",
        "reason": "Procedura descritta dalle sorgenti; mancano esercizio in browser, controlli di persistenza e schermate reali.",
        "intent": "communications.workflow.disable",
        "contracts": [
            "workflow_buttons",
            "workflow_update",
            "workflow_execution"
        ]
    },
    {
        "path": "tutorials/come-impostare-automazioni-workflow.mdx",
        "title": "Modificare un workflow",
        "id": "modificare-un-workflow",
        "status": "pending",
        "reason": "Procedura descritta dalle sorgenti; mancano esercizio in browser, controlli di persistenza e schermate reali.",
        "intent": "communications.workflow.update",
        "contracts": [
            "workflow_buttons",
            "workflow_disabled",
            "workflow_save",
            "workflow_email",
            "email_builder",
            "workflow_footer"
        ]
    },
    {
        "path": "tutorials/come-impostare-automazioni-workflow.mdx",
        "title": "Limiti e informazioni utili",
        "id": "limiti-e-informazioni-utili",
        "status": "needs_external_verification",
        "reason": "Servizio, recapito, programmazione, privacy o recupero richiedono evidenze esterne alla sola applicazione.",
        "intent": "communications.workflow.operational.limits",
        "contracts": [
            "email_config",
            "email_service",
            "workflow_execution"
        ]
    },
    {
        "path": "tutorials/come-impostare-automazioni-workflow.mdx",
        "title": "Limiti di invio",
        "id": "limiti-di-invio",
        "status": "needs_external_verification",
        "reason": "Servizio, recapito, programmazione, privacy o recupero richiedono evidenze esterne alla sola applicazione.",
        "intent": "communications.email.limits",
        "contracts": [
            "email_config",
            "email_service",
            "email_reset",
            "message_types",
            "workflow_validation"
        ]
    },
    {
        "path": "tutorials/come-impostare-automazioni-workflow.mdx",
        "title": "Informazioni importanti",
        "id": "informazioni-importanti",
        "status": "needs_external_verification",
        "reason": "Servizio, recapito, programmazione, privacy o recupero richiedono evidenze esterne alla sola applicazione.",
        "intent": "communications.workflow.execution",
        "contracts": [
            "workflow_save",
            "workflow_trigger_close",
            "workflow_email",
            "workflow_model",
            "workflow_execution",
            "email_service",
            "email_logs"
        ]
    },
    {
        "path": "tutorials/come-impostare-automazioni-workflow.mdx",
        "title": "Conclusione",
        "id": "conclusione",
        "status": "pending",
        "reason": "Procedura descritta dalle sorgenti; mancano esercizio in browser, controlli di persistenza e schermate reali.",
        "intent": "communications.workflow.review",
        "contracts": [
            "workflow_trigger",
            "workflow_email",
            "workflow_save",
            "workflow_buttons"
        ]
    },
    {
        "path": "faq/come-abilitare-autenticazione-due-fattori.mdx",
        "title": "Abilitare l'autenticazione a due fattori (2FA)",
        "id": "abilitare-l-autenticazione-a-due-fattori-2fa",
        "status": "verified",
        "reason": "Descrizione testuale verificata nelle sorgenti; non attesta una procedura eseguita in browser.",
        "intent": "account.twofa.describe",
        "contracts": [
            "twofa_page",
            "twofa_backend",
            "login_otp"
        ]
    },
    {
        "path": "faq/come-abilitare-autenticazione-due-fattori.mdx",
        "title": "Perché attivare il 2FA?",
        "id": "perche-attivare-il-2fa",
        "status": "verified",
        "reason": "Descrizione testuale verificata nelle sorgenti; non attesta una procedura eseguita in browser.",
        "intent": "account.twofa.security.describe",
        "contracts": [
            "twofa_secret",
            "twofa_backend",
            "login_otp"
        ]
    },
    {
        "path": "faq/come-abilitare-autenticazione-due-fattori.mdx",
        "title": "App compatibili",
        "id": "app-compatibili",
        "status": "verified",
        "reason": "Descrizione testuale verificata nelle sorgenti; non attesta una procedura eseguita in browser.",
        "intent": "account.twofa.apps.describe",
        "contracts": [
            "twofa_apps",
            "twofa_secret",
            "twofa_backend"
        ]
    },
    {
        "path": "faq/come-abilitare-autenticazione-due-fattori.mdx",
        "title": "Come attivare il 2FA",
        "id": "come-attivare-il-2fa",
        "status": "pending",
        "reason": "Procedura descritta dalle sorgenti; mancano esercizio in browser, controlli di persistenza e schermate reali.",
        "intent": "account.twofa.enable",
        "contracts": [
            "profile_menu",
            "twofa_page",
            "twofa_setup",
            "twofa_fields",
            "twofa_update",
            "twofa_backend",
            "twofa_secret"
        ]
    },
    {
        "path": "faq/come-abilitare-autenticazione-due-fattori.mdx",
        "title": "Come funziona il login con il 2FA",
        "id": "come-funziona-il-login-con-il-2fa",
        "status": "pending",
        "reason": "Procedura descritta dalle sorgenti; mancano esercizio in browser, controlli di persistenza e schermate reali.",
        "intent": "account.twofa.login",
        "contracts": [
            "login_dialog",
            "login_otp"
        ]
    },
    {
        "path": "faq/come-abilitare-autenticazione-due-fattori.mdx",
        "title": "Come disattivare il 2FA",
        "id": "come-disattivare-il-2fa",
        "status": "pending",
        "reason": "Procedura descritta dalle sorgenti; mancano esercizio in browser, controlli di persistenza e schermate reali.",
        "intent": "account.twofa.disable",
        "contracts": [
            "profile_menu",
            "twofa_page",
            "twofa_update",
            "twofa_backend"
        ]
    },
    {
        "path": "faq/come-abilitare-autenticazione-due-fattori.mdx",
        "title": "Hai perso l'accesso all'app?",
        "id": "hai-perso-l-accesso-all-app",
        "status": "needs_external_verification",
        "reason": "Servizio, recapito, programmazione, privacy o recupero richiedono evidenze esterne alla sola applicazione.",
        "intent": "account.twofa.recovery",
        "contracts": [
            "twofa_backend",
            "login_otp"
        ]
    }
];

export const communicationAuthReferenceSections = Object.freeze(sections.map(({contracts, ...descriptor}) =>
    Object.freeze(descriptor)));

// Draft discovery exposes the existing descriptors without invoking source verification.
export const communicationAuthSourceContractKeys = sourceKeys;
export const communicationAuthEditorialSections = Object.freeze(sections.map(section =>
    Object.freeze({...section, verified: false, contracts: Object.freeze([...section.contracts])})));

export function communicationAuthReferences(source) {
    if (typeof source !== 'function') throw new TypeError('Communication/auth references require a source reader');
    const references = new Map();
    for (const [index, contract] of communicationAuthSourceContracts.entries()) {
        const reference = source(...contract);
        if (!reference || reference.path !== contract[0] ||
            reference.canonical_source_sha256 !== communicationAuthReviewedSources[contract[0]])
            throw new Error('Communication/auth source evidence changed; review required: ' + contract[0]);
        references.set(sourceKeys[index], reference);
    }
    return sections.map(({contracts, ...descriptor}) => ({...descriptor,
        evidence: contracts.map(key => references.get(key))}));
}
