// Reviewed text-only behavior. Operational/legal guarantees remain explicit gaps.
export const referenceReviewedSources = Object.freeze({
    "BE/application/management/commands/seed_selfhost.py": "1ca816e3c6daa58905d66cf459ab08e3cf8479bcaa5bd42b7215737a666921c2",
    "BE/application/views/billing_views.py": "65292240bd56a557c5b4d582f18fa984ae3d9b311980275e7f3892886d0ecfaf",
    "BE/core/authentication.py": "4ea4b9041f1c19aef3105fa9518edb3fb880d1deddef46be7dff701c7a3da3d2",
    "BE/application/views/two_fa_views.py": "ac0542429d0b1216eeb00c19dd30aed8f8691395b11b0ca0d2618a7f60719d5e",
    "BE/application/views/auth_views.py": "ff45ee9f1e2665b0ab19e7ae832834a481256e4ff266d7d0cec3860a5854975c",
    "BE/instance/restore/views.py": "90de3a76030be2dc67d06e5a369c2c9faf17214a25927a308ea333d3f5e64b5a",
    "BE/instance/permissions.py": "ce413a53cb3dcf3cca2478c4df656078cd105f6faff868b53a4073e973885f58",
    "BE/application/views/payment_views.py": "4f45b99318afaa7189177f73cf82863f6f281dc8a2992b15dcf299b11f8e8c34"
});
const reviewedFiles = referenceReviewedSources;

const referenceSourceContracts = Object.freeze({
    "billingSeed": [
        "BE/application/management/commands/seed_selfhost.py",
        "class Command(BaseCommand):",
        42
    ],
    "billingCheckout": [
        "BE/application/views/billing_views.py",
        "def billing_checkout(request):",
        24
    ],
    "authentication": [
        "BE/core/authentication.py",
        "class ScopedJWTAuthentication(",
        15
    ],
    "twoFactor": [
        "BE/application/views/two_fa_views.py",
        "def two_fa_update(request):",
        54
    ],
    "login": [
        "BE/application/views/auth_views.py",
        "def oauth2_login(request):",
        182
    ],
    "recovery": [
        "BE/instance/restore/views.py",
        "class DataRestoreView(APIView):",
        72
    ],
    "recoveryPermission": [
        "BE/instance/permissions.py",
        "class IsInstanceOwner(BasePermission):",
        5
    ],
    "administrator": [
        "BE/instance/permissions.py",
        "def is_instance_administrator(",
        12
    ],
    "payment": [
        "BE/application/views/payment_views.py",
        "def payment_add(request):",
        24
    ]
});

function reviewedPageTemplates() {
    const billingSeed = referenceSourceContracts.billingSeed;
    const billingCheckout = referenceSourceContracts.billingCheckout;
    const authentication = referenceSourceContracts.authentication;
    const twoFactor = referenceSourceContracts.twoFactor;
    const login = referenceSourceContracts.login;
    const recovery = referenceSourceContracts.recovery;
    const recoveryPermission = referenceSourceContracts.recoveryPermission;
    const administrator = referenceSourceContracts.administrator;
    const payment = referenceSourceContracts.payment;
    const section = (id, status, body, evidence, features = [], reason = '') => ({id, status, body, evidence, features, reason});
    const external = 'Deployment, hosting, backup retention, encryption and legal/privacy practices require operational evidence outside application source.';
    return [
        {path: 'faq/quali-sono-piani-abbonamento.mdx', title: 'Quali sono i piani di abbonamento?',
         description: 'Piano della piattaforma self-hosted e differenza rispetto ai pagamenti degli iscritti.', sections: [
            section('piani-di-abbonamento', 'verified', `# Piani di abbonamento

Questa guida riguarda l'abbonamento alla **piattaforma Assozeta** nelle installazioni self-hosted.
Il piano configurato all'inizializzazione è **Piano Pro**, con importi mensile e annuale pari a zero.
Quando l'associazione principale è configurata, il piano viene associato al suo proprietario con una validità di lunga durata.

<Note>La quota di iscrizione o di un corso è un pagamento dell'associazione, distinto dall'abbonamento alla piattaforma.</Note>`, [billingSeed, payment], ['self_hosted']),
            section('confronto-tra-i-piani', 'verified', `## Confronto tra i piani

In questa installazione self-hosted non viene proposto un acquisto tra **Base**, **Pro** e **Teams**: l'acquisto alla piattaforma è disattivato.
La tabella dei piani del precedente manuale pubblico non è un riferimento per attivare funzioni su questa installazione.`, [billingSeed, billingCheckout], ['self_hosted']),
            section('piano-base', 'unsupported', `## Piano Base

Il percorso di acquisto del Piano Base descritto dal precedente manuale non è disponibile nella configurazione self-hosted qui documentata.
Per le funzioni gestionali consulta i singoli capitoli; non assumere i limiti o le condizioni commerciali del servizio pubblico.`, [billingSeed, billingCheckout], ['self_hosted'], 'Public Base purchase/table does not describe the self-hosted configuration.'),
            section('piano-pro', 'verified', `## Piano Pro

**Piano Pro** è il nome del piano creato dall'inizializzazione self-hosted. Gli importi registrati sono zero.
Il nome non conferma che un servizio esterno sia già configurato: per pagamenti online e collegamenti usa le rispettive guide e verifica la configurazione effettiva dell'installazione.`, [billingSeed], ['self_hosted']),
            section('piano-teams', 'unsupported', `## Piano Teams

La procedura di acquisto del Piano Teams del servizio pubblico non si applica a questa installazione self-hosted.
Per lavorare insieme usa il capitolo **Collaboratori** e i permessi effettivamente assegnati agli account.`, [billingCheckout], ['self_hosted'], 'Public Teams purchase is disabled for the documented self-hosted installation.'),
            section('come-cambiare-piano', 'verified', `## Come cambiare piano

L'acquisto dell'abbonamento alla piattaforma è disattivato in questa versione self-hosted: la richiesta di attivazione viene rifiutata.
Non seguire qui i vecchi passaggi di acquisto mensile o annuale del servizio pubblico.

Per registrare o incassare una quota dell'associazione apri invece le guide **Pagamenti** e **Ricevute**.`, [billingCheckout, payment], ['self_hosted']),
        ]},
        {path: 'faq/i-miei-dati-sono-al-sicuro.mdx', title: 'I miei dati sono al sicuro?',
         description: 'Controlli di accesso presenti e informazioni da verificare con il gestore dell’installazione.', sections: [
            section('la-sicurezza-dei-tuoi-dati-e-la-nostra-priorita', 'verified', `# La sicurezza dei tuoi dati è la nostra priorità

Assozeta controlla l'identità dell'account nelle richieste autenticate.
L'accesso a due fattori e le autorizzazioni del ripristino sono controlli presenti nell'applicazione; i dettagli sono descritti nelle sezioni di questa guida.

<Note>Il codice dell'applicazione non conferma da solo dove sono ospitati i dati, la frequenza dei backup o gli adempimenti del gestore. Verifica queste informazioni per la tua installazione.</Note>`, [authentication, twoFactor, recoveryPermission]),
            section('conformita-gdpr-e-gestione-responsabile-dei-dati', 'needs_external_verification', `## Conformità GDPR e gestione responsabile dei dati

Chiedi al gestore dell'installazione l'informativa, i ruoli privacy, le finalità del trattamento e le eventuali condizioni di conservazione o trasferimento dei dati.
Questa guida non conferma piena conformità al GDPR, conservazione esclusiva in Italia o pratiche commerciali di terze parti: servono informazioni sulla gestione effettiva.

<Warning>Le garanzie del precedente manuale del servizio pubblico non vengono trasferite automaticamente all'installazione self-hosted.</Warning>`, [], [], external),
            section('robusta-politica-di-backup', 'needs_external_verification', `## Robusta politica di backup

Verifica con il gestore **quali dati vengono copiati**, **con quale frequenza**, **per quanto tempo** e **come viene provato il ripristino**.
Il ripristino dell'installazione self-hosted è riservato al proprietario dell'installazione o a un amministratore; un collaboratore ordinario non può avviarlo.

<Note>La presenza della funzione di ripristino non garantisce copie ogni quattro ore, conservazione per trenta giorni o una politica già attiva. Questa guida lascia tali condizioni da verificare.</Note>`, [recovery, recoveryPermission, administrator], ['self_hosted'], external),
            section('misure-di-sicurezza-avanzate', 'verified', `## Misure di sicurezza avanzate

Quando l'autenticazione a due fattori è attiva, l'accesso con email e password richiede anche un codice temporaneo valido.
L'attivazione richiede la conferma di un codice temporaneo: conserva l'accesso alla tua applicazione di autenticazione.

Il ripristino dell'installazione self-hosted richiede il proprietario dell'installazione o un amministratore.
Per i dettagli operativi consulta la guida sull'autenticazione a due fattori e chiedi al gestore la procedura di recupero dell'installazione.`, [authentication, twoFactor, login, recovery, recoveryPermission, administrator], ['self_hosted']),
            section('infrastruttura-di-archiviazione-affidabile', 'needs_external_verification', `## Infrastruttura di archiviazione affidabile

Il gestore della tua installazione deve indicare dove vengono conservati database, documenti e copie di backup, chi può accedervi e quali protezioni sono configurate.
Questa pagina non conferma l'uso di Aruba Cloud, più datacenter in Italia, backup cifrati o distribuzione geografica. Tali condizioni richiedono evidenze dell'infrastruttura effettiva.`, [], [], external),
        ]},
    ];
}

function reviewedPages(source) {
    const byContract = new Map(Object.values(referenceSourceContracts).map(args => [args, source(...args)]));
    return reviewedPageTemplates().map(page => ({...page, sections: page.sections.map(section => ({
        ...section, evidence: section.evidence.map(contract => byContract.get(contract)),
    }))}));
}

export function referencePages(readSource) {
    return reviewedPages((...args) => {
        const reference = readSource(...args);
        if (reference.canonical_source_sha256 !== reviewedFiles[reference.path])
            throw new Error('Text-only claim evidence changed; review required: ' + reference.path);
        return reference;
    });
}

export function referenceDraftPages() {
    return reviewedPageTemplates().flatMap(page => page.sections.map(section => ({path: page.path,
        title: section.body.match(/^#{1,6} +(.+)$/m)[1], id: section.id, body: section.body,
        status: section.status, reason: section.reason})));
}

// Pure discovery never calls verified builders or fabricates source evidence.
const editorialDescriptor = (page, refs, reviewed, recipe_module, status = page.status, reason = page.reason) => ({
    path: page.path, id: page.id, title: page.title,
    status: ['unsupported', 'needs_external_verification'].includes(status) ? status : 'pending',
    reason: reason || 'Editorial draft metadata only; implementation and workflow proof remain required.',
    recipe_module, verified: false,
    source_contracts: refs.map(([path, symbol, length]) => ({path, symbol, length, reviewed_sha256: reviewed[path]})),
});

export function referenceEditorialContracts() {
    const module = "docs/manuale/reference-recipes.mjs";
    return reviewedPageTemplates().flatMap(page => page.sections.map(section => editorialDescriptor({
        path: page.path, id: section.id, title: section.body.match(/^#{1,6} +(.+)$/m)[1],
        status: section.status, reason: section.reason,
    }, section.evidence, referenceReviewedSources, module)));
}
