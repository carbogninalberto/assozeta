const memberCreationSourceContracts = [
        ['UI/src/components/Sidebar.svelte', '<span class="menu-text">Organizzazione</span>', 50],
        ['UI/src/routes.js', "'/members/add':", 18],
        ['UI/src/utils/Permissions.js', 'export const canPerformAction', 30],
        ['UI/src/utils/enumUtils.js', 'export const MembersListType', 18],
        ['UI/src/routes/association/Members/MembersList.svelte', "canPerformAction('association.members.create')", 12],
        ['UI/src/routes/association/Members/add/AddMember.svelte', 'newUserAccount.set({', 55],
        ['UI/src/routes/association/Members/add/AddMember.svelte', "text: 'Hai inserito i dati corretti?", 33],
        ['UI/src/routes/association/Members/add/sections/Section1.svelte', 'Informazioni del Profilo', 257],
        ['UI/src/routes/association/Members/add/sections/Section2.svelte', "Inserisci le informazioni dell'Associato", 182],
        ['UI/src/components/buttons/GenerateTaxCodeButton.svelte', 'async function calculateTaxCode()', 29],
        ['UI/src/routes/association/Members/add/sections/Section3.svelte', 'Firma del documento', 65],
        ['UI/src/components/signature/SmoothSignature.svelte', 'toDataURL(', 20],
        ['UI/src/routes/association/Members/add/sections/Section4.svelte', 'Certificato Medico', 63],
        ['UI/src/routes/association/Members/add/sections/Section6.svelte', 'Controlla le Informazioni', 260],
        ['BE/application/views/subscriptions_views.py', 'def subscription_add(request):', 46],
        ['BE/application/views/subscriptions_views.py', 'def subscription_calculate_tax_code(request):', 35],
        ['BE/application/utils/subscriptions_utils.py', 'def _create_subscription_impl(', 520],
        ['BE/application/serializers/subscriptions_serializers.py', 'class SubscriptionOptimizedSerializer(', 220],
        ['BE/application/models/subscriptions_models.py', 'class Subscription(', 65],
        ['BE/application/permissions_registry.py', "'subscription/add'", 10],
    ];
const memberApprovalSourceContracts = [
        ['UI/src/components/Sidebar.svelte', '<span class="menu-text">Organizzazione</span>', 50],
        ['UI/src/routes.js', "'/members/list':", 18],
        ['UI/src/utils/Permissions.js', 'export const canPerformAction', 30],
        ['UI/src/routes/association/Members/MembersList.svelte', 'const statusTextDictionary =', 9],
        ['UI/src/routes/association/Members/MembersList.svelte', 'let disableApproval =', 35],
        ['UI/src/routes/association/Members/MembersList.svelte', 'window.acceptSubscription =', 66],
        ['UI/src/components/buttons/ApproveButton.svelte', 'export let disabled =', 31],
        ['UI/src/routes/accounting/payment/PaymentList.svelte', 'window.markAsPaid =', 111],
        ['BE/application/views/subscriptions_views.py', 'def subscription_approve(request, uid):', 39],
        ['BE/application/models/subscriptions_models.py', 'class Subscription(', 65],
        ['BE/application/serializers/subscriptions_serializers.py', 'class SubscriptionOptimizedSerializer(', 220],
        ['BE/application/views/payment_views.py', 'def payment_list(', 69],
        ['BE/application/signals.py', 'def subscription_approved_callback(', 11],
        ['BE/application/permissions_registry.py', "'subscription/*/approve'", 8],
    ];

// Draft prose and image positions share the same recipe as the real capture.
// Only the generator, after validating real reports and source hashes, verifies it.
export const memberCaptureSpecs = {
    'members-create': ['images/faq/creazione-socio/', [
        'members-list-and-add-action', 'registration-type-and-no-new-account',
        'personal-data-and-fiscal-code', 'residence-and-contact-data',
        'fictional-signature-entered', 'medical-certificate-step-with-no-attachment',
        'registration-summary-before-saving', 'created-pending-registration-persists-after-reload',
    ]],
    'members-approve': ['images/libro-soci/approvazione/', [
        'signed-pending-request-before-approval', 'confirm-single-registration-approval',
        'accepted-registration-persists-after-reload', 'registration-fee-remains-unpaid-after-approval',
    ]],
};

export function memberDraftPages() {
    const image = (number, caption) => `<Frame>![${caption}](/images/faq/creazione-socio/${number}.png)</Frame>`;
    const page = (path, title, id, body, numbers) => ({path, title, id, body, imageNumbers: numbers});
    const profile = `Mantieni **Tipo iscrizione → Socio e Tesserato** e il ruolo **Socio ordinario**.
    Controlla le quote visualizzate prima di proseguire: nell'associazione dell'esempio la quota associativa è **25,00 €**, quella di tesseramento **0,00 €**.
    Alla domanda **Crea nuovo account per il socio / tesserato?**, scegli **No**: questa guida registra la persona collegandola all'account dell'associazione.
    ${image(2, 'Tipo di iscrizione, quote e scelta di non creare un nuovo account')}`;
    const personal = `Inserisci **Nome**, **Cognome**, **Sesso**, **Data di nascita** e **Città di nascita**.
    Nell'esempio usiamo dati fittizi: **Marta Neri**, sesso **F**, nata il **01/01/2000** a **Roma**.
    Compila **Codice Fiscale** oppure premi **Genera codice fiscale**, il pulsante accanto al campo, dopo aver inserito questi dati.
    Controlla il risultato nel campo prima di proseguire.
    ${image(3, 'Dati anagrafici con il codice fiscale generato')}
    Inserisci anche **Indirizzo**, **Città di residenza** e **CAP**, poi i recapiti.
    Nell'esempio: **Via delle Attività 4**, **Roma**, **00100**, email **marta@example.test**.
    Usa dati e recapiti della persona che stai registrando; quelli della guida servono soltanto a mostrare il flusso.
    ${image(4, 'Residenza e recapiti della persona')}`;
    const signature = `Nel passaggio **Firma del documento**, raccogli la firma nell'area dedicata e controlla che il tratto sia visibile prima di continuare.
    La guida mostra una firma dimostrativa tracciata con il mouse.
    ${image(5, 'Area di firma compilata con un tratto dimostrativo')}`;
    const medical = `Il passaggio successivo riguarda il **Certificato Medico**.
    Nell'esempio non alleghiamo un documento e proseguiamo fino al riepilogo.
    ${image(6, 'Passaggio del certificato medico senza un allegato nell’esempio')}
    <Note>Questo esempio descrive la registrazione di un adulto. Le procedure per minori, caricamento e scadenza dei certificati richiedono guide specifiche.</Note>`;
    const summary = `Nel passaggio **Controlla le Informazioni**, rivedi il tipo di iscrizione, i dati della persona, la firma e gli eventuali allegati.
    ${image(7, 'Riepilogo della richiesta prima del salvataggio')}
    Premi **Salva**. Alla domanda **Hai inserito i dati corretti? Controlla prima di continuare.**, premi **Continua** per confermare.
    Torni nell'elenco delle iscrizioni: cerca **Marta Neri** e controlla che la riga sia presente anche dopo il ricaricamento.
    ${image(8, 'Nuova iscrizione presente nell’elenco dopo il ricaricamento')}
    <Note>Salvare questa richiesta firmata la lascia **in attesa di approvazione**. La creazione e l'approvazione sono operazioni distinte. Con l'incasso automatico disattivato, la quota di **25,00 €** resta da incassare.</Note>`;
    const book = 'docs/libro-soci.mdx';
    return [
        page('faq/come-si-crea-un-socio.mdx', 'Creare un socio, socio tesserato o tesserato',
            'creare-un-socio-socio-tesserato-o-tesserato', `# Creare un socio, socio tesserato o tesserato

Questa procedura accompagna la registrazione di un adulto come **Socio e Tesserato**, dall'apertura del modulo al controllo dei dati salvati.
L'esempio usa l'**Associazione Sportiva Aurora** e dati fittizi. Richiede il permesso di creazione delle iscrizioni.

<Steps>
  <Step title="Apri le iscrizioni e aggiungi una persona">
    Dal menu laterale apri **Organizzazione → Iscrizioni**. Premi **Aggiungi** per aprire **Nuovo Socio o Tesserato**.
    ${image(1, 'Elenco delle iscrizioni con il pulsante Aggiungi')}
  </Step>
  <Step title="Scegli il profilo e l'account">
    ${profile}
    Premi **Continua**.
  </Step>
  <Step title="Compila anagrafica e recapiti">
    ${personal}
    Premi **Continua** dopo aver controllato i campi.
  </Step>
  <Step title="Raccogli la firma">
    ${signature}
    Premi **Continua**.
  </Step>
  <Step title="Controlla il passaggio dei documenti">
    ${medical}
    Premi **Continua**.
  </Step>
  <Step title="Salva e verifica la nuova iscrizione">
    ${summary}
  </Step>
</Steps>

<Note>Un collaboratore con il solo accesso in lettura può consultare l'elenco, ma non vede il pulsante **Aggiungi** e non può creare l'iscrizione. Questa guida non copre la creazione di un account personale o l'approvazione della richiesta.</Note>`, [1, 2, 3, 4, 5, 6, 7, 8]),
        page(book, 'Aggiungi un Socio', 'aggiungi-un-socio', `## Aggiungi un Socio

Apri **Organizzazione → Iscrizioni** e premi **Aggiungi**. Il modulo guida la compilazione del profilo, dell'anagrafica, della firma, dei documenti e del riepilogo.
Per creare un'iscrizione serve il relativo permesso; un collaboratore in sola lettura può consultare l'elenco.

${image(1, 'Dove iniziare la creazione di una nuova iscrizione')}

[Segui la procedura completa con l'esempio di Marta Neri](/faq/come-si-crea-un-socio#creare-un-socio-socio-tesserato-o-tesserato).`, [1]),
        page(book, '1. Informazioni Profilo', '1-informazioni-profilo', `### 1. Informazioni Profilo

${profile}

La scelta **No** conserva il collegamento all'account dell'associazione. Il percorso con un nuovo account personale richiede una procedura distinta. Premi **Continua** per passare all'anagrafica.`, [2]),
        page(book, '2. Informazioni Anagrafiche', '2-informazioni-anagrafiche', `### 2. Informazioni Anagrafiche

${personal}

Controlla i campi contrassegnati con l'asterisco e premi **Continua**.`, [3, 4]),
        page(book, '3. Firma del Documento', '3-firma-del-documento', `### 3. Firma del Documento

${signature}

Premi **Continua** per raggiungere il passaggio del certificato medico.`, [5]),
        page(book, '4. Certificato Medico', '4-certificato-medico', `### 4. Certificato Medico

${medical}

Premi **Continua** per rivedere i dati prima del salvataggio.`, [6]),
        page(book, '5. Riepilogo e Creazione', '5-riepilogo-e-creazione', `### 5. Riepilogo e Creazione

${summary}`, [7, 8]),
        page('faq/come-calcolare-generare-codici-fiscali.mdx', 'Come funziona', 'come-funziona', `# Come funziona

<Steps>
  <Step title="Compila i dati necessari">
    Durante la creazione di un'iscrizione, raggiungi il passaggio **Inserisci le informazioni dell'Associato**.
    Inserisci **Nome**, **Cognome**, **Sesso**, **Data di nascita** e **Città di nascita**.
    Nell'esempio: **Marta Neri**, sesso **F**, nata il **01/01/2000** a **Roma**.
  </Step>
  <Step title="Genera e controlla">
    Premi **Genera codice fiscale**, il pulsante accanto al campo **Codice Fiscale**.
    Il risultato compare nel campo, dove puoi controllarlo prima di continuare la compilazione.
    ${image(3, 'Codice fiscale generato nel modulo di creazione dell’iscrizione')}
  </Step>
</Steps>

<Note>Il calcolo a partire dai dati anagrafici non conferma che il codice sia quello attribuito alla persona. Controllalo sul suo documento prima di salvare l'iscrizione.</Note>

[Prosegui con la registrazione completa](/faq/come-si-crea-un-socio#creare-un-socio-socio-tesserato-o-tesserato).`, [3]),
    ];
}

export function memberPages(id, report, source) {
    if (id === 'members-approve') return memberApprovalPages(report, source);
    if (id !== 'members-create') return [];
    const [prefix, checkpoints] = memberCaptureSpecs[id];
    if (report.fixture_version !== 8 || report.fixture_profile !== 'baseline')
        throw new Error('Member creation requires fixture version 7 with the baseline profile');
    if (report.screenshots.length !== checkpoints.length || report.screenshots.some((image, index) =>
        image.path !== prefix + (index + 1) + '.png' || image.checkpoint !== checkpoints[index]))
        throw new Error('Member creation checkpoints do not match the reviewed steps');
    const expected = {type: 2, role: 1, status_flag: 2, account_created: false, owner_account_preserved: true,
        payment_amount: 25, payment_paid: false, signature_saved: true, fiscal_code_generated: true,
        medical_attached: false, persisted_after_reload: true, registrations: 4, reader_create_status: 403};
    if (Object.entries(expected).some(([key, value]) => report.member_creation?.[key] !== value))
        throw new Error('Member creation outcomes differ from the reviewed example');
    const refs = memberCreationSourceContracts;
    const evidence = refs.map(args => source(report, ...args));
    return memberDraftPages().map(({imageNumbers, ...page}) => ({...page, evidence,
        screenshots: imageNumbers.map(number => report.screenshots[number - 1])}));
}

export function memberApprovalDraftPages() {
    return [{path: 'docs/libro-soci.mdx', title: "Ciclo di vita dell'iscrizione",
        id: 'ciclo-di-vita-dell-iscrizione', imageNumbers: [1, 2, 3, 4], body: `## Ciclo di vita dell'iscrizione

Nell'elenco **Organizzazione → Iscrizioni**, la colonna **Stato** distingue la richiesta appena inserita da quella approvata.
Le etichette mostrate nell'elenco sono **non firmata**, **in attesa**, **rifiutata**, **accettata** e **ritirata/o**.

La [procedura di creazione](/faq/come-si-crea-un-socio#creare-un-socio-socio-tesserato-o-tesserato) lascia la richiesta firmata di **Marta Neri** **in attesa**.
Per approvare questa richiesta, serve il permesso di modifica delle iscrizioni.

<Steps>
  <Step title="Trova la richiesta da approvare">
    Apri **Organizzazione → Iscrizioni** e trova la persona nell'elenco.
    Controlla che lo stato della richiesta dell'esempio sia **in attesa**.
    <Frame>![Richiesta firmata di Marta Neri in attesa di approvazione](/images/libro-soci/approvazione/1.png)</Frame>
  </Step>
  <Step title="Conferma l'approvazione">
    Nella riga premi il pulsante con il suggerimento **Approva Iscrizione**.
    Alla domanda **Vuoi accettare l'iscrizione?**, premi **Approva**.
    Se vuoi tornare all'elenco per ricontrollare i dati, premi **Annulla**.
    <Frame>![Conferma dell'approvazione di una singola iscrizione](/images/libro-soci/approvazione/2.png)</Frame>
  </Step>
  <Step title="Controlla lo stato salvato">
    La colonna **Stato** mostra **accettata**. Ricarica la pagina per controllare che la modifica sia conservata.
    Il pulsante di approvazione risulta disattivato per l'iscrizione già accettata.
    <Frame>![Iscrizione accettata presente dopo il ricaricamento](/images/libro-soci/approvazione/3.png)</Frame>
  </Step>
  <Step title="Controlla separatamente la quota">
    Apri **Pagamenti** per consultare il pagamento collegato alla persona.
    Nell'esempio, con l'incasso automatico disattivato, la quota di **25,00 €** rimane **In attesa** dopo l'approvazione dell'iscrizione.
    <Frame>![Quota associativa ancora da incassare dopo l'approvazione](/images/libro-soci/approvazione/4.png)</Frame>
    [Leggi come confermare l'incasso](/docs/pagamenti#segna-un-pagamento-come-pagato).
  </Step>
</Steps>

<Note>Un collaboratore in sola lettura vede la richiesta, ma il pulsante **Approva Iscrizione** è disattivato. Il rifiuto, il ritiro e l'approvazione di più persone insieme richiedono procedure distinte.</Note>`}];
}

function memberApprovalPages(report, source) {
    const [prefix, checkpoints] = memberCaptureSpecs['members-approve'];
    if (report.fixture_version !== 8 || report.fixture_profile !== 'baseline')
        throw new Error('Member approval requires fixture version 7 with the baseline profile');
    if (report.screenshots.length !== checkpoints.length || report.screenshots.some((image, index) =>
        image.path !== prefix + (index + 1) + '.png' || image.checkpoint !== checkpoints[index]))
        throw new Error('Member approval checkpoints do not match the reviewed steps');
    const expected = {initial_status: 2, final_status: 4, acceptance_date_saved: true, persisted_after_reload: true,
        payment_id_preserved: true, payment_amount: 25, payment_paid: false, reader_approve_status: 403,
        repeat_approve_status: 403, denial_left_state_unchanged: true};
    if (Object.entries(expected).some(([key, value]) => report.member_approval?.[key] !== value))
        throw new Error('Member approval outcomes differ from the reviewed example');
    const refs = memberApprovalSourceContracts;
    const evidence = refs.map(args => source(report, ...args));
    return memberApprovalDraftPages().map(({imageNumbers, ...page}) => ({...page, evidence,
        screenshots: imageNumbers.map(number => report.screenshots[number - 1])}));
}

// Discovery baseline only; this metadata cannot grant source or workflow verification.
export const memberReviewedSources = Object.freeze({
    "UI/src/components/Sidebar.svelte": "9f8344602ae269b5dbbf90dfd6f75e5154f3d5774ad544ac41105b74af2e333c",
    "UI/src/routes.js": "58bddc98548095f14f39fa3141277de8cd00e2133faf49f6761131b109d254b0",
    "UI/src/utils/Permissions.js": "100a24508b57b6c73a323ab4f128ee2c79aef3212a2cd0178172199d59e51854",
    "UI/src/utils/enumUtils.js": "75b14f5fad15f456e7ad15ae691978698c5ef973bfb1ead9105c48fe815dd811",
    "UI/src/routes/association/Members/MembersList.svelte": "2205d4071d49dc3c96bad02299dd157668037807f33811b395f811197841650a",
    "UI/src/routes/association/Members/add/AddMember.svelte": "61ccbc7d9274ec1d929e84e89008708838ee108c8266ca6370a23220f1534ce4",
    "UI/src/routes/association/Members/add/sections/Section1.svelte": "f4c6741669d9daeea510dc527de7609a7d8b77b131b5294f89c469140e16d7ca",
    "UI/src/routes/association/Members/add/sections/Section2.svelte": "e18a2e868b99abdd02742ade333eeecdd3432f8b3b1c2b38a66e92f3274a8ca3",
    "UI/src/components/buttons/GenerateTaxCodeButton.svelte": "f57750d056ce12d21cda56441a618afc7da047b2802514b4b51fc69113f564c1",
    "UI/src/routes/association/Members/add/sections/Section3.svelte": "432de3a3765b310628f44046a3848f7a14ba8c066670d5530474bdf09993e323",
    "UI/src/components/signature/SmoothSignature.svelte": "c451cf360e2c9bc68340407e1169ce3b6f91d07ae364932e7fe5253c354e4ff6",
    "UI/src/routes/association/Members/add/sections/Section4.svelte": "645c70a5c555ba94b378df755549fa8455d6e4ee64fbb1cb108a3ffad3da5863",
    "UI/src/routes/association/Members/add/sections/Section6.svelte": "97c429b3fe14f919e8c877251eb7bd9d794afdf0da88b61c74758ddace4d5a46",
    "BE/application/views/subscriptions_views.py": "7ef946a94b5d6e55f1ccdfd2de22fa9532101449587cc08753c4bde36b133881",
    "BE/application/utils/subscriptions_utils.py": "ab922f87b8b1dd491bba41dd86eaabdefd1127c08bdaeb7be4f990657effd22a",
    "BE/application/serializers/subscriptions_serializers.py": "1e53f7d66701653856b6d5701f5bd80a6d5febb06872a27abc9a50615859f451",
    "BE/application/models/subscriptions_models.py": "b6b592e5ced8b685df6196e877ad75679bdf650c0d79774de9c537455eab465a",
    "BE/application/permissions_registry.py": "b8318ad63daa9452ac736b2152449b14c49f73bc1417622a4181b7c3ceb3b1fa",
    "UI/src/components/buttons/ApproveButton.svelte": "4531bd12ab2764ec6069c516c8797660122deea749f26516ea0851dad58f4f0f",
    "UI/src/routes/accounting/payment/PaymentList.svelte": "34017cc9d3bb103ff10b326cf95876190ce6060886c018816bd85bf8bbaa64fe",
    "BE/application/views/payment_views.py": "4f45b99318afaa7189177f73cf82863f6f281dc8a2992b15dcf299b11f8e8c34",
    "BE/application/signals.py": "81f0445115b59c4aeb94cefcd40d3379657f5bc00f4719d8e4302d30cb941a2f"
});

// Pure discovery never calls verified builders or fabricates source evidence.
const editorialDescriptor = (page, refs, reviewed, recipe_module, status = page.status, reason = page.reason) => ({
    path: page.path, id: page.id, title: page.title,
    status: ['unsupported', 'needs_external_verification'].includes(status) ? status : 'pending',
    reason: reason || 'Editorial draft metadata only; implementation and workflow proof remain required.',
    recipe_module, verified: false,
    source_contracts: refs.map(([path, symbol, length]) => ({path, symbol, length, reviewed_sha256: reviewed[path]})),
});

export function memberEditorialContracts() {
    const module = "docs/manuale/member-recipes.mjs";
    return [...memberDraftPages().map(page => editorialDescriptor(page, memberCreationSourceContracts, memberReviewedSources, module)),
        ...memberApprovalDraftPages().map(page => editorialDescriptor(page, memberApprovalSourceContracts, memberReviewedSources, module))];
}
