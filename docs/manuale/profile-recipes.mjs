import {readFileSync} from 'node:fs';
import {createHash} from 'node:crypto';

// Drafts remain pending until the generator validates real workflow reports.
// The source callback binds each projection to captured application bytes.
export const profileCaptureSpecs = {
    'members-profile-update': ['images/libro-soci/scheda/', [
        'registration-profile-and-navigation-tabs', 'edited-card-number-and-type-before-saving',
        'card-details-persist-after-profile-reload',
    ]],
    'members-medical-manage': ['images/certificati/profilo/', [
        'medical-tab-with-no-document', 'uploaded-demo-file-and-manual-expiration-before-saving',
        'document-and-expiration-persist-after-reload', 'competitive-certificate-setting-saved',
        'confirm-certificate-removal', 'medical-tab-empty-after-removal',
    ]],
};

const pending = '<Note>Bozza in attesa di prova: questi passaggi e le immagini devono essere confermati dalla cattura del flusso reale prima della pubblicazione.</Note>';
const image = (prefix, number, caption) => `<Frame>![${caption}](/${prefix}${number}.png)</Frame>`;
const draft = (path, title, id, body, imageNumbers) => ({path, title, id, body: body + '\n\n' + pending, imageNumbers});

export function profileDraftPages() {
    const [prefix] = profileCaptureSpecs['members-profile-update'];
    return [draft('docs/libro-soci.mdx', 'Anagrafica Smart', 'anagrafica-smart', `## Anagrafica Smart

La scheda di un'iscrizione raccoglie i dati dell'iscrizione e le sezioni collegate alla persona.
Qui seguiamo la modifica di **Numero tessera** e **Tipologia tessera** di **Giulia Bianchi**, una persona fittizia dell'**Associazione Sportiva Aurora**.
Per consultare la scheda serve il permesso di lettura delle iscrizioni; per salvare i metadati serve il permesso di modifica e un'iscrizione non archiviata.

<Steps>
  <Step title="Apri la scheda della persona">
    Dal menu laterale apri **Organizzazione → Iscrizioni**. Trova la riga della persona e premi il suo **nome e cognome**.
    Si apre una scheda laterale: controlla che il nome in alto corrisponda all'iscrizione che vuoi modificare.
    Nell'esempio apriamo **Giulia Bianchi**, già accettata.
    ${image(prefix, 1, 'Scheda laterale dell’iscrizione e schede di navigazione')}
  </Step>
  <Step title="Individua i metadati dell'iscrizione">
    Nella scheda iniziale cerca **Informazioni Iscrizione**. **Numero tessera** identifica la tessera, mentre **Tipologia tessera** ne descrive la tipologia: è distinta dal campo **Tipo iscrizione**.
    La stessa area mostra **Iscritto dal**, **Scade il**, **Tipo iscrizione** e, per i soci, **Ruolo socio**.
    In **Altre informazioni** trovi **Stato**, **Accettata/o il** e **Ritirata/o il**; più sotto trovi **Note** ed eventuali **Campi aggiuntivi**.
    Rivedi questi dati per riconoscere l'iscrizione corretta prima di procedere. Nell'esempio modifichiamo soltanto i due campi della tessera.
  </Step>
  <Step title="Modifica numero e tipologia tessera">
    Inserisci **42** in **Numero tessera** e **Tessera Aurora** in **Tipologia tessera**.
    Il numero è un campo testuale; la validazione del modulo ammette lettere, cifre e il carattere di sottolineatura, senza spazi.
    Usa i valori della tua iscrizione: quelli della guida sono dimostrativi.
    Controlla i campi prima di premere **Salva**, che diventa disponibile quando ci sono modifiche.
    ${image(prefix, 2, 'Numero e tipologia tessera modificati prima del salvataggio')}
  </Step>
  <Step title="Salva e controlla dopo il ricaricamento">
    Premi **Salva** nella scheda e attendi il completamento del salvataggio.
    Ricarica la pagina, torna in **Organizzazione → Iscrizioni** e premi nuovamente il nome della persona.
    Controlla che **Numero tessera** mostri **42** e **Tipologia tessera** mostri **Tessera Aurora**.
    Il nome rimane **Giulia Bianchi** e lo stato rimane **Accettata**.
    ${image(prefix, 3, 'Dati della tessera conservati dopo la riapertura della scheda')}
  </Step>
  <Step title="Passa alla sezione che ti serve">
    Nella barra delle schede puoi aprire **Certificato medico**, **Pagamenti** e **Documenti**.
    Per le iscrizioni che comprendono il tesseramento sono presenti anche **Corsi e Abbonamenti**, **Carnet** e **Registro Presenze**.
    [Segui la procedura per caricare il certificato e inserire la scadenza](/tutorials/come-gestire-certificati-medici#dal-profilo-dell-atleta).
    Le azioni nelle altre sezioni hanno procedure proprie: il salvataggio dei dati della tessera conclude questa modifica.
  </Step>
</Steps>

<Note>Questa procedura salva i metadati dell'iscrizione. La modifica dei dati personali della persona richiede la procedura dell'anagrafica. Un collaboratore in sola lettura può consultare la scheda, ma non dispone dell'azione di salvataggio.</Note>`, [1, 2, 3])];
}

export function medicalDraftPages() {
    const [prefix] = profileCaptureSpecs['members-medical-manage'];
    const path = 'tutorials/come-gestire-certificati-medici.mdx';
    return [
        draft(path, "Dal profilo dell'atleta", 'dal-profilo-dell-atleta', `## Dal profilo dell'atleta

Usa la scheda dell'iscrizione per allegare il file e impostare la sua scadenza.
L'esempio riguarda **Giulia Bianchi**, adulta con un'iscrizione non archiviata, e richiede il permesso di modifica delle iscrizioni.
La guida usa il PDF fittizio **documento-medico-demo.pdf**, che contiene la dicitura **DOCUMENTO DIMOSTRATIVO**: serve soltanto a mostrare il caricamento.

<Steps>
  <Step title="Apri la sezione del certificato">
    Apri **Organizzazione → Iscrizioni**, trova la persona e premi il suo **nome e cognome**.
    Nella scheda laterale scegli **Certificato medico**.
    Nell'esempio la sezione è vuota e mostra il pulsante **Nuovo Certificato**.
    ${image(prefix, 1, 'Sezione Certificato medico prima del caricamento')}
  </Step>
  <Step title="Scegli e carica il file">
    Premi **Nuovo Certificato**. Nella finestra **Certificato Medico**, trascina il file nell'area **Trascina o premi per caricare il Certificato Medico**, oppure premi l'area e selezionalo dal dispositivo.
    Sono ammessi file **PDF** e **immagini** fino a **5 MB**; l'esempio carica **documento-medico-demo.pdf**.
    Attendi che **Caricamento in corso...** venga sostituito da **Documento caricato: documento-medico-demo.pdf**.
    Se compare un errore, controlla formato e dimensione e seleziona nuovamente il file. **Salva** resta disattivato finché il caricamento non riesce.
  </Step>
  <Step title="Completa il caricamento con la scadenza">
    Il messaggio di conferma indica che il **file è già allegato all'iscrizione**. Prima di terminare, compila **Data Scadenza** e premi **Salva** per applicare la data.
    Nell'esempio inseriamo manualmente **30/09/2027**: il caricamento del PDF non ricava una scadenza dal suo contenuto.
    ${image(prefix, 2, 'PDF dimostrativo caricato e scadenza inserita manualmente')}
    [Segui il controllo della data e del salvataggio](/tutorials/come-gestire-certificati-medici#impostare-la-data-di-scadenza).
  </Step>
</Steps>

<Note>Se chiudi la finestra dopo il caricamento riuscito, il file risulta già allegato, ma la nuova data richiede **Salva**. Per un documento già presente trovi **Sostituisci certificato**; il percorso illustrato qui parte da una sezione senza documento.</Note>`, [1, 2]),
        draft(path, 'Impostare la data di scadenza', 'impostare-la-data-di-scadenza', `# Impostare la data di scadenza

La data si inserisce nella finestra **Certificato Medico** dopo aver caricato il file.
Leggi la scadenza riportata sul documento e inseriscila nel campo **Data Scadenza**: il solo caricamento non completa questo passaggio.

<Steps>
  <Step title="Inserisci e controlla la data">
    Con il file caricato, seleziona la data nel campo **Data Scadenza**.
    Nell'esempio usiamo **30/09/2027**, una data dimostrativa inserita manualmente.
    Rivedi giorno, mese e anno prima del salvataggio. Il campo può mostrare una data iniziale: controllala sempre sul documento che stai gestendo.
    ${image(prefix, 2, 'Campo Data Scadenza compilato prima di salvare')}
  </Step>
  <Step title="Salva la scadenza">
    Premi **Salva** e attendi che la finestra si chiuda dopo il salvataggio riuscito.
    Se viene segnalato un errore, controlla la data e lo stato del caricamento prima di riprovare.
  </Step>
  <Step title="Verifica documento e data nella scheda">
    Ricarica la pagina e riapri **Giulia Bianchi → Certificato medico**.
    Controlla che la sezione mostri ancora il documento e **30/09/2027** come scadenza.
    Questa verifica conclude il caricamento e il salvataggio della data.
    ${image(prefix, 3, 'Documento e scadenza presenti dopo il ricaricamento')}
  </Step>
</Steps>

<Note>Il salvataggio della scadenza non dimostra l'invio di email. Questa procedura verifica il documento allegato e la data salvata; usa la data del documento senza ricavare una durata standard.</Note>`, [2, 3]),
        draft(path, 'Tieni traccia dei certificati agonistici', 'tieni-traccia-dei-certificati-agonistici', `## Tieni traccia dei certificati agonistici

La sezione **Certificato medico** contiene l'interruttore **Certificato medico agonistico** quando è presente un certificato.
Usalo per registrare la classificazione del documento che stai gestendo.

<Steps>
  <Step title="Apri il certificato già salvato">
    Apri **Organizzazione → Iscrizioni**, premi il nome della persona e scegli **Certificato medico**.
    Controlla documento e scadenza prima di cambiare l'impostazione.
  </Step>
  <Step title="Registra la classificazione agonistica">
    Se il documento è agonistico, attiva **Certificato medico agonistico**.
    L'interruttore salva la scelta al cambio: attendi la conferma dell'aggiornamento.
    Nell'esempio la sezione mostra **AGONISTICO** accanto alle informazioni del certificato, con la scadenza già inserita.
    ${image(prefix, 4, 'Impostazione agonistica salvata nella sezione del certificato')}
  </Step>
</Steps>

<Note>L'interruttore registra una classificazione; il caricamento e la scadenza restano passaggi separati. Un collaboratore in sola lettura vede l'impostazione, ma l'interruttore è disattivato.</Note>`, [4]),
        draft(path, 'Consigli per una gestione efficiente', 'consigli-per-una-gestione-efficiente', `# Consigli per una gestione efficiente

Prima di chiudere la gestione di un certificato, controlla tre elementi nella scheda: il **file allegato**, la **scadenza** e l'eventuale indicazione **AGONISTICO**.
Riaprire la scheda dopo il ricaricamento ti permette di verificare i dati conservati.
Quando devi aggiornare il documento, usa **Sostituisci certificato** e controlla la data nella finestra prima di salvare.

<Note>La rimozione è facoltativa e separata dal caricamento. Usala soltanto quando vuoi togliere il certificato dall'iscrizione; la conferma indica che l'azione è irreversibile.</Note>

<Steps>
  <Step title="Avvia la rimozione solo se necessaria">
    Nella sezione **Certificato medico** premi **Rimuovi**.
    Si apre la domanda **Sei sicuro di voler rimuovere il certificato?**.
    Premi **Annulla** per conservare il certificato, oppure **Rimuovi** per confermare la rimozione.
    ${image(prefix, 5, 'Conferma facoltativa della rimozione del certificato')}
  </Step>
  <Step title="Controlla il risultato della rimozione">
    Dopo la conferma e il completamento dell'operazione, la sezione torna senza certificato e mostra **Nuovo Certificato**.
    Nell'esempio questo è il risultato della rimozione, successiva ai controlli del file, della data e della classificazione agonistica.
    ${image(prefix, 6, 'Sezione nuovamente vuota dopo la rimozione')}
    Per allegare un altro documento riparti dalla [procedura di caricamento dal profilo](/tutorials/come-gestire-certificati-medici#dal-profilo-dell-atleta).
  </Step>
</Steps>

<Note>Per caricare, modificare la classificazione o rimuovere serve il permesso di modifica delle iscrizioni. Il percorso usa un documento fittizio e non verifica notifiche email, workflow o controlli della bacheca.</Note>`, [5, 6]),
    ];
}

const profileRefs = [
    ['UI/src/components/Sidebar.svelte', '<span class="menu-text">Organizzazione</span>', 50],
    ['UI/src/routes.js', "'/members/list':", 18],
    ['UI/src/utils/Permissions.js', 'export const canPerformAction', 30],
    ['UI/src/routes/association/Members/MembersList.svelte', 'let basicDrawer = new DetailDrawer({', 20],
    ['UI/src/routes/association/Members/detail/DetailDrawer.svelte', '<BasicDrawer', 7],
    ['UI/src/routes/association/Members/detail/Detail.svelte', 'function switchTab(tab)', 42],
    ['UI/src/routes/association/Members/detail/Detail.svelte', 'class="nav nav-tabs', 122],
    ['UI/src/routes/association/Members/detail/sections/Info.svelte', 'function updateInfo()', 93],
    ['UI/src/routes/association/Members/detail/sections/Info.svelte', 'subscription_number: {', 12],
    ['UI/src/routes/association/Members/detail/sections/Info.svelte', "canPerformAction('association.members.update')", 12],
    ['UI/src/routes/association/Members/detail/sections/Info.svelte', 'Informazioni Iscrizione', 213],
    ['BE/application/views/subscriptions_views.py', 'def subscription_update(request, uid):', 49],
    ['BE/application/services/subscription_service.py', 'def update_subscription(subscription, data):', 46],
    ['BE/application/serializers/subscriptions_serializers.py', 'class SubscriptionInfoSerializer(', 43],
    ['BE/application/models/subscriptions_models.py', 'subscription_number =', 14],
    ['BE/application/permissions_registry.py', "'subscription/*/update'", 14],
];
const medicalRefs = [
    ['UI/src/routes/association/Members/detail/sections/Medical.svelte', 'async function updateCertificateSettings(e)', 23],
    ['UI/src/routes/association/Members/detail/sections/Medical.svelte', 'Informazioni certificato medico', 151],
    ['UI/src/routes/association/Members/detail/sections/Medical.svelte', 'Nuovo Certificato', 9],
    ['UI/src/routes/association/Members/detail/sections/modals/AddMedicalCertificate.svelte', 'async function handleSubmit(e)', 47],
    ['UI/src/routes/association/Members/detail/sections/modals/AddMedicalCertificate.svelte', 'async function receiveFile(file)', 50],
    ['UI/src/routes/association/Members/detail/sections/modals/AddMedicalCertificate.svelte', 'async function initializeDropzone(', 34],
    ['UI/src/routes/association/Members/detail/sections/modals/AddMedicalCertificate.svelte', 'Trascina o premi per caricare', 75],
    ['UI/src/routes/association/Members/detail/sections/modals/medicalCertificateUpload.js', 'export function validateCertificateFile(file)', 9],
    ['UI/src/routes/association/Members/detail/sections/modals/medicalCertificateUpload.js', 'export async function uploadCertificate(', 10],
    ['BE/application/services/subscription_service.py', 'def upload_medical_certificate(', 48],
    ['BE/application/services/subscription_service.py', 'def attach_certificate_to_subscription(', 33],
    ['BE/application/views/subscriptions_views.py', 'def subscription_medical_certificate_upload(', 45],
    ['BE/application/views/subscriptions_views.py', 'def subscription_medical_certificate_set_certificate_expiration(', 93],
    ['BE/application/views/subscriptions_views.py', 'def subscription_medical_certificate_edit(', 27],
    ['BE/application/permissions_registry.py', "'subscription/*/medical-certificate/upload'", 5],
];

export function profilePages(id, report, source) {
    const spec = profileCaptureSpecs[id];
    if (!spec) return [];
    const [prefix, checkpoints] = spec;
    if (report?.fixture_version !== 8 || report.fixture_profile !== 'baseline')
        throw new Error('Member profile/medical workflows require fixture version 7 with the baseline profile');
    if (!Array.isArray(report.screenshots) || report.screenshots.length !== checkpoints.length ||
        report.screenshots.some((capture, index) => !capture || capture.path !== prefix + (index + 1) + '.png' ||
            capture.checkpoint !== checkpoints[index]))
        throw new Error('Member profile/medical checkpoints do not match the reviewed steps: ' + id);
    const medical = id === 'members-medical-manage';
    const expected = medical ? {
        document_uploaded: true, document_id_matches: true, expiration: '2027-09-30', persisted_after_reload: true,
        competitive_setting_saved: true, reader_update_status: 403, denied_setting_unchanged: true,
        removed_medical_is_null: true, automatic_expiration_observed: false, email_delivery_exercised: false,
    } : {
        number: '42', card_type: 'Tessera Aurora', person_preserved: true, status_preserved: true,
        persisted_after_reload: true, reader_update_status: 403, denial_left_number_unchanged: true,
    };
    const outcome = report[medical ? 'medical_certificate' : 'member_profile'];
    if (Object.entries(expected).some(([key, value]) => outcome?.[key] !== value))
        throw new Error('Member profile/medical outcomes differ from the reviewed example: ' + id);
    if (medical) {
        const file = new URL('../../selfhost/tests/browser/manuale/fixtures/documento-medico-demo.pdf', import.meta.url);
        const hash = createHash('sha256').update(readFileSync(file)).digest('hex');
        if (outcome.input_fixture_sha256 !== hash)
            throw new Error('Medical input fixture hash differs from the reviewed demo PDF');
    }
    const evidence = [...profileRefs, ...(medical ? medicalRefs : [])].map(args => source(report, ...args));
    return (medical ? medicalDraftPages() : profileDraftPages()).map(({imageNumbers, ...page}) => ({
        ...page, body: page.body.replace('\n\n' + pending, ''), evidence,
        screenshots: imageNumbers.map(number => report.screenshots[number - 1]),
    }));
}

// Discovery baseline only; this metadata cannot grant source or workflow verification.
export const profileReviewedSources = Object.freeze({
    "UI/src/components/Sidebar.svelte": "9f8344602ae269b5dbbf90dfd6f75e5154f3d5774ad544ac41105b74af2e333c",
    "UI/src/routes.js": "58bddc98548095f14f39fa3141277de8cd00e2133faf49f6761131b109d254b0",
    "UI/src/utils/Permissions.js": "100a24508b57b6c73a323ab4f128ee2c79aef3212a2cd0178172199d59e51854",
    "UI/src/routes/association/Members/MembersList.svelte": "2205d4071d49dc3c96bad02299dd157668037807f33811b395f811197841650a",
    "UI/src/routes/association/Members/detail/DetailDrawer.svelte": "27863e56e04a266fa1184d72397972b66de35c1e9d097fc9bab29dfe0b493f18",
    "UI/src/routes/association/Members/detail/Detail.svelte": "f7b31cc364221cb6c1acff533dec9ea286a926bbc0fc6a70075e4b5593874bb6",
    "UI/src/routes/association/Members/detail/sections/Info.svelte": "720d401c86e498d7efdba75321ce399fe2788033cf453690b3c6bf6869d5cff7",
    "BE/application/views/subscriptions_views.py": "7ef946a94b5d6e55f1ccdfd2de22fa9532101449587cc08753c4bde36b133881",
    "BE/application/services/subscription_service.py": "95a5be71f2a72859d80e4f19dbcbcb2c8e61d43180152e7258cffe7ce772201d",
    "BE/application/serializers/subscriptions_serializers.py": "1e53f7d66701653856b6d5701f5bd80a6d5febb06872a27abc9a50615859f451",
    "BE/application/models/subscriptions_models.py": "b6b592e5ced8b685df6196e877ad75679bdf650c0d79774de9c537455eab465a",
    "BE/application/permissions_registry.py": "b8318ad63daa9452ac736b2152449b14c49f73bc1417622a4181b7c3ceb3b1fa",
    "UI/src/routes/association/Members/detail/sections/Medical.svelte": "79d036985cf8fd39eccb9885255b1ccfb9baa4dfa5c657a0bc3d0efcee07e150",
    "UI/src/routes/association/Members/detail/sections/modals/AddMedicalCertificate.svelte": "95803afa6d9e428067e18c4f560c5e70bffa01a57360f0ee8eac746bd2dcf3e8",
    "UI/src/routes/association/Members/detail/sections/modals/medicalCertificateUpload.js": "8b78ae7aaf29c309c5eaf6d8f2ed00078a085beba54ef7316675a585f2a83f21"
});

// Pure discovery never calls verified builders or fabricates source evidence.
const editorialDescriptor = (page, refs, reviewed, recipe_module, status = page.status, reason = page.reason) => ({
    path: page.path, id: page.id, title: page.title,
    status: ['unsupported', 'needs_external_verification'].includes(status) ? status : 'pending',
    reason: reason || 'Editorial draft metadata only; implementation and workflow proof remain required.',
    recipe_module, verified: false,
    source_contracts: refs.map(([path, symbol, length]) => ({path, symbol, length, reviewed_sha256: reviewed[path]})),
});

export function profileEditorialContracts() {
    const module = "docs/manuale/profile-recipes.mjs";
    return [...profileDraftPages().map(page => editorialDescriptor(page, profileRefs, profileReviewedSources, module)),
        ...medicalDraftPages().map(page => editorialDescriptor(page, [...profileRefs, ...medicalRefs], profileReviewedSources, module))];
}
