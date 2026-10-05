// Draft editorial provenance only; this map does not alter runtime verification.
export const instructorReviewedSources = Object.freeze({
    "BE/application/models/user_models.py": "3834a5655391ca750830d03175d7f663276697e50ab3fcfffcfd9ad45b7b931c",
    "BE/application/permissions_registry.py": "b8318ad63daa9452ac736b2152449b14c49f73bc1417622a4181b7c3ceb3b1fa",
    "BE/application/serializers/user_serializers.py": "b6c13df61ad39bf22722f3fc69659129fd0cedbbda588e7cbc727989b00e07f6",
    "BE/application/views/instructor_views.py": "3d957e0a987583744dda1db2d0040eb8b0abdc90b5bf85546debc4a0405d189c",
    "BE/core/settings.py": "346fd7a15f10dcd25c3adef653e9c983e33b99037036e30f7ec192edc1ab1835",
    "UI/src/components/Sidebar.svelte": "9f8344602ae269b5dbbf90dfd6f75e5154f3d5774ad544ac41105b74af2e333c",
    "UI/src/components/buttons/EditButton.svelte": "052f6ca21b3a85dfd713d62d96ccf7c81d8b4af93e15aa109a02e6fcbc74dfe9",
    "UI/src/routes.js": "58bddc98548095f14f39fa3141277de8cd00e2133faf49f6761131b109d254b0",
    "UI/src/routes/association/course/instructor/InstructorList.svelte": "1bb5636275f07a54fbde9266959cdfa3518d5f88f1274d513379a345797f60ff",
    "UI/src/routes/association/course/instructor/add/AddInstructor.svelte": "1797eb719e0c25e696388720bd3258f08a0561694be9568759a2228a8ae599b8",
    "UI/src/routes/association/course/instructor/add/sections/Section1.svelte": "991fbfebdbf28eb862ce140436ee8444b3ec16dd2c187bad61cce31600b23a28",
    "UI/src/routes/association/course/instructor/info/Instructor.svelte": "1501b33bbe3fc27b751a3d0450038c6b47e78dd8e1c0b781fc43ca1a394fe6a5",
    "UI/src/routes/association/course/instructor/info/modals/AddEditModal.svelte": "19133f13c71dbcf769df0371d32248cff54c7fa2e4291d42c5c28969bd1152e9",
    "UI/src/routes/association/course/instructor/modals/EditModal.svelte": "002185a9e3f93ba4e6260b0f529f7e153494aac6df71d829f6d2320f319b48dc",
    "UI/src/utils/Functions.js": "c9ed79a8214d4e2ceb9d91e2cd3d52f42a32706f96e2890082ba5584c6787147",
    "UI/src/utils/Permissions.js": "100a24508b57b6c73a323ab4f128ee2c79aef3212a2cd0178172199d59e51854"
});

// Pure reviewed drafts; capture validation and source verification precede installation.
export const instructorCaptureSpecs = {
    'instructors-create-edit-hours': ['images/istruttori/creazione-ore/', [
        'empty-instructor-list-and-create-action', 'instructor-required-and-personal-data',
        'instructor-contract-default-rates-and-unlinked-account', 'created-instructor-persists-after-reload',
        'edit-instructor-default-hourly-rate', 'instructor-compensation-card-before-hours',
        'hourly-hours-default-rate-unpaid-and-calculated-total', 'saved-hours-unpaid-and-summary-persist-after-reload',
    ]],
};

export const instructorExpectedOutcome = Object.freeze({initial_instructors: 0, final_instructors: 1,
    owner_preserved: true, associated_account: false, initial_hourly_rate: 15, hourly_rate: 18,
    percentage_rate: 20, hours: 3, amount: 54, compensation_type: 'hourly', paid: false,
    payment_created: false, document_created: false, total_amount_to_pay: 54, total_amount_paid: 0,
    persisted_after_reload: true, reader_create_status: 403, reader_update_status: 403,
    reader_hours_create_status: 403, denial_left_state_unchanged: true});

const pendingProof = '<Note>Bozza in attesa di prova: i passaggi e le immagini previste devono essere verificati con una cattura del flusso reale prima della pubblicazione.</Note>\n\n';

export function instructorDraftPages() {
    const image = (number, caption) => `<Frame>![${caption}](/images/istruttori/creazione-ore/${number}.png)</Frame>`;
    const step = (title, body) => `<Step title="${title}">\n${body}\n</Step>`;
    const steps = (...items) => `<Steps>\n${items.join('\n')}\n</Steps>`;
    const doc = 'docs/istruttori.mdx';
    const tutorial = 'tutorials/come-impostare-gestire-istruttori.mdx';
    const page = (path, title, id, level, body, imageNumbers) => ({path, title, id,
        body: '#'.repeat(level) + ' ' + title + '\n\n' + pendingProof + body, imageNumbers});
    const opening = step('Apri gli istruttori', `Dal menu laterale apri **Corsi e Abbonamenti → Istruttori**.
Premi **Istruttore**, il pulsante in alto a destra, per aprire **Nuovo Istruttore**.
${image(1, 'Lista istruttori inizialmente vuota e pulsante Istruttore')}`);
    const mandatory = step('Compila i campi obbligatori', `Inserisci **Nome**, **Cognome** ed **Email**, contrassegnati con l'asterisco.
Nell'**Associazione Sportiva Aurora** usiamo dati fittizi: **Paolo Riva**, email **paolo@example.test**.
Controlla il formato dell'email prima di salvare: il modulo la richiede anche se non colleghi un account collaboratore.
${image(2, 'Nome, cognome, email e dati anagrafici di Paolo Riva')}`);
    const personal = step('Completa i dati anagrafici', `Compila i dati pertinenti all'istruttore: **Data di nascita**, **Città di nascita**, **Provincia di nascita** e **Codice fiscale**.
Per la residenza usa **Città di residenza**, **Indirizzo**, **Civico** e **Provincia di residenza**; puoi aggiungere **Telefono** e **Telefono 2**.
Nell'esempio inseriamo nascita **10/05/1985** a **Roma (RM)** e residenza a **Roma**, **Via dello Sport**, civico **8**, provincia **RM**.
Lasciamo vuoti codice fiscale e telefoni: per un caso reale controlla questi dati sui documenti della persona.
${image(2, 'Dati anagrafici e residenza nel modulo Nuovo Istruttore')}`);
    const contract = step('Controlla le informazioni del contratto', `Nell'esempio **È un volontario** resta disattivato.
Compila **Data stipula contratto** con la data effettiva del contratto; la dimostrazione usa la data di riferimento della guida.
Inserisci **Titolo di studio**, nell'esempio **Laurea in scienze motorie**, e controlla **Ruolo**, che parte da **Istruttore**.
${image(3, 'Informazioni contratto, account facoltativo e tariffe predefinite')}`);
    const rates = step('Imposta le tariffe predefinite', `Nel campo **Tariffa oraria di default (€/ora)** inserisci **15**.
Nel campo **Percentuale di default (%)** inserisci **20**.
I valori vengono salvati nel profilo e proposti nel modulo delle ore; questo esempio registra un compenso **Orario**.
La percentuale salvata non produce da sola un compenso sugli incassi.
${image(3, 'Tariffa oraria di 15 euro e percentuale predefinita di 20')}`);
    const account = step('Controlla l’account collaboratore', `Il campo **Account collaboratore associato** è facoltativo.
Nell'esempio lo lasciamo vuoto: nella lista, la colonna **Account** mostrerà **nessuno**.
Non occorre creare un nuovo account per salvare questo profilo.
${image(3, 'Account collaboratore associato lasciato senza selezione nell’esempio')}`);
    const create = step('Salva e verifica il profilo', `Premi **Crea Istruttore** dopo aver controllato i dati.
Torni nella lista: verifica la riga **PAOLO RIVA**, l'email e la colonna **Account**.
Ricarica la pagina e controlla che la riga sia ancora presente.
${image(4, 'Profilo di Paolo Riva salvato nella lista dopo il ricaricamento')}`);
    const edit = step('Modifica la tariffa nel profilo', `Dalla lista **Istruttori**, premi la matita con suggerimento **Modifica** nella riga **PAOLO RIVA**.
Si apre **Modifica Istruttore**. Scorri fino a **Tariffa oraria di default (€/ora)** e cambia il valore da **15** a **18**.
${image(5, 'Finestra Modifica Istruttore con tariffa aggiornata a 18 euro')}
Premi **Salva**. Ricarica la lista, poi apri la scheda cliccando sul nome **PAOLO RIVA**.
La prossima registrazione di ore proporrà la tariffa aggiornata di **18,00 €/ora**.`);
    const card = step('Apri la scheda compensi', `Nella lista **Istruttori**, premi il nome **PAOLO RIVA**.
La **Scheda compensi** mostra gli indicatori **ORE LAVORATE**, **COMPENSO TOTALE**, **COMPENSO DA PAGARE** e **COMPENSO PAGATO**.
Prima della prima registrazione sono a zero e la tabella delle ore è vuota.
${image(6, 'Scheda compensi prima della registrazione di ore')}`);
    const hourOpen = step('Apri una nuova registrazione', `Dalla **Scheda compensi**, premi **Aggiungi** in alto a destra.
Si apre **Aggiungi Orario Istruttore**.
${image(6, 'Pulsante Aggiungi nella scheda compensi dell’istruttore')}`);
    const hourFields = step('Controlla data e stato', `Nel campo **Data** scegli il giorno di lavoro; nell'esempio usiamo la data di riferimento della guida.
Nel campo **Stato** lascia **Da Pagare**, poiché il compenso non è ancora stato saldato.
Controlla che il tipo di compenso attivo sia **Orario**.
${image(7, 'Data, stato Da Pagare e tipo di compenso Orario')}`);
    const hourly = step('Inserisci ore e controlla il calcolo', `Nel campo **Ore** inserisci **3**.
Il campo **€/ora** propone **18**, la tariffa modificata nel profilo.
Controlla **Compenso totale**: **3 × 18,00 € = 54,00 €**.
Il campo Ore accetta anche incrementi di mezz'ora; usa il numero di ore effettivamente lavorate.
${image(7, 'Tre ore a 18 euro producono un compenso totale di 54 euro')}`);
    const note = step('Aggiungi una nota e salva', `Nel campo **Note** scrivi una descrizione utile a riconoscere la registrazione.
Nell'esempio: **Lezione dimostrativa di ginnastica**. Il modulo ammette al massimo **500 caratteri**.
Ricontrolla data, stato, ore, tariffa e **Compenso totale**, poi premi **Salva**.
${image(7, 'Nota della lezione e compenso totale prima del salvataggio')}`);
    const persisted = step('Verifica la registrazione e il riepilogo', `Ricarica la scheda e controlla la riga della lezione: **3 ore**, tariffa **18,00 €**, importo **54,00 €** e stato **Da pagare**.
Nel periodo che comprende la registrazione, gli indicatori mostrano **3,00** ore lavorate, **54,00 €** di compenso totale e da pagare, **0,00 €** di compenso pagato.
${image(8, 'Ore salvate, stato Da pagare e riepilogo dopo il ricaricamento')}
<Note>Il salvataggio registra le ore e il loro importo. In questo esempio non crea un pagamento contabile o un documento del compenso. La procedura **Crea compenso** è un'operazione successiva distinta.</Note>`);
    const access = `<Note>Per creare o modificare un profilo e registrare ore servono i relativi permessi.
Un collaboratore con accesso in sola lettura vede i dati dell'esempio, ma trova disattivati **Istruttore**, la matita **Modifica** e **Aggiungi** nella scheda compensi.</Note>`;
    return [
        page(doc, 'Aggiungere un Istruttore', 'aggiungere-un-istruttore', 2,
            'Questa procedura crea un profilo con dati fittizi, senza account collaboratore associato.\n\n' + steps(opening, mandatory, rates, create) + '\n\n' + access, [1, 2, 3, 4]),
        page(doc, 'Informazioni obbligatorie', 'informazioni-obbligatorie', 3, steps(mandatory), [2]),
        page(doc, 'Informazioni anagrafiche', 'informazioni-anagrafiche', 3, steps(personal), [2]),
        page(doc, 'Informazioni contratto', 'informazioni-contratto', 3, steps(contract), [3]),
        page(doc, 'Tariffe predefinite', 'tariffe-predefinite', 3, steps(rates), [3]),
        page(doc, 'Account collaboratore associato', 'account-collaboratore-associato', 3, steps(account, create), [3, 4]),
        page(doc, 'Modificare un Istruttore', 'modificare-un-istruttore', 2, steps(edit) + '\n\n' + access, [5]),
        page(doc, "La scheda dell'Istruttore", 'la-scheda-dell-istruttore', 2, steps(card, persisted), [6, 8]),
        page(doc, 'Registrare le ore lavorate', 'registrare-le-ore-lavorate', 2,
            steps(hourOpen, hourFields, hourly, note, persisted) + '\n\n' + access, [6, 7, 8]),
        page(doc, 'Campi principali', 'campi-principali', 3, steps(hourFields), [7]),
        page(doc, 'Tipi di compenso', 'tipi-di-compenso', 3,
            'Il modulo propone le schede **Orario** e **Percentuale**. Questa procedura usa **Orario**.\n\n' + steps(hourly), [7]),
        page(doc, 'Compenso orario', 'compenso-orario', 4, steps(hourly, persisted), [7, 8]),
        page(doc, 'Note e salvataggio', 'note-e-salvataggio', 3, steps(note, persisted), [7, 8]),
        page(doc, 'Stato dei compensi', 'stato-dei-compensi', 3,
            'La registrazione dell’esempio è **Da pagare**: il suo importo è presente nel riepilogo e non è stato saldato.\n\n' + steps(persisted), [8]),
        page(tutorial, 'Aggiungere un nuovo istruttore', 'aggiungere-un-nuovo-istruttore', 1,
            steps(opening, mandatory, personal, contract, rates, account, create) + '\n\n' + access, [1, 2, 3, 4]),
        page(tutorial, 'Tipologie di compenso', 'tipologie-di-compenso', 2,
            'Imposta la tariffa nel profilo, poi controllala nel modulo della singola registrazione. L’esempio usa un compenso **Orario**.\n\n' + steps(rates, hourly), [3, 7]),
        page(tutorial, 'Registrare le ore lavorate', 'registrare-le-ore-lavorate', 2,
            steps(hourOpen, hourFields, hourly, note, persisted), [6, 7, 8]),
        page(tutorial, 'Riepilogo ore', 'riepilogo-ore', 2, steps(card, persisted), [6, 8]),
        page(tutorial, 'Modificare i dati', 'modificare-i-dati', 2, steps(edit) + '\n\n' + access, [5]),
    ];
}

export function instructorPages(id, report, source) {
    if (id !== 'instructors-create-edit-hours') return [];
    if (report.fixture_version !== 8 || report.fixture_profile !== 'baseline')
        throw new Error('Instructor workflow requires fixture version 7 with baseline profile');
    const [prefix, checkpoints] = instructorCaptureSpecs[id];
    if (report.screenshots.length !== checkpoints.length || report.screenshots.some((image, index) =>
        image.path !== prefix + (index + 1) + '.png' || image.checkpoint !== checkpoints[index]))
        throw new Error('Instructor workflow checkpoints do not match the reviewed steps');
    if (Object.entries(instructorExpectedOutcome).some(([key, value]) => report.instructor_workflow?.[key] !== value))
        throw new Error('Instructor workflow outcomes differ from the reviewed example');
    const refs = [
        ['UI/src/components/Sidebar.svelte', '<span class="menu-text">Corsi e Abbonamenti</span>', 75],
        ['UI/src/routes.js', "'/course/instructor/list':", 65],
        ['UI/src/utils/Permissions.js', 'export const canPerformAction', 30],
        ['UI/src/utils/Functions.js', 'export const getDataFromForm', 44],
        ['UI/src/components/buttons/EditButton.svelte', "export let popover_text = 'Modifica'", 36],
        ['UI/src/routes/association/course/instructor/InstructorList.svelte', 'const columns =', 220],
        ['UI/src/routes/association/course/instructor/add/AddInstructor.svelte', 'async function createInstructor(data)', 142],
        ['UI/src/routes/association/course/instructor/add/sections/Section1.svelte', 'export let data =', 355],
        ['UI/src/routes/association/course/instructor/modals/EditModal.svelte', 'async function update(data)', 126],
        ['UI/src/routes/association/course/instructor/info/Instructor.svelte', 'async function fetchInfoWidget()', 252],
        ['UI/src/routes/association/course/instructor/info/modals/AddEditModal.svelte', "if (row.compensation_type == 'hourly')", 545],
        ['BE/application/views/instructor_views.py', 'def instructor_add(request):', 30],
        ['BE/application/views/instructor_views.py', 'def instructor_info(request, uid):', 79],
        ['BE/application/views/instructor_views.py', 'def instructor_hours_list(request, uid):', 60],
        ['BE/application/views/instructor_views.py', 'def instructor_hours_add(request, uid):', 22],
        ['BE/application/views/instructor_views.py', 'def instructor_update(request, uid):', 40],
        ['BE/application/serializers/user_serializers.py', 'class InstructorSerializer(', 32],
        ['BE/application/models/user_models.py', 'class Instructor(models.Model):', 72],
        ['BE/application/permissions_registry.py', "'instructor/list'", 15],
        ['BE/core/settings.py', '"DATE_INPUT_FORMATS"', 2],
    ];
    const evidence = refs.map(args => source(report, ...args));
    return instructorDraftPages().map(({imageNumbers, ...page}) => ({...page,
        body: page.body.replace(pendingProof, ''), evidence,
        screenshots: imageNumbers.map(number => report.screenshots[number - 1])}));
}

// Declared references copied from the actual legacy workflow.
const instructorEditorialSourceContracts = [
    [
        "UI/src/components/Sidebar.svelte",
        "<span class=\"menu-text\">Corsi e Abbonamenti</span>",
        75
    ],
    [
        "UI/src/routes.js",
        "'/course/instructor/list':",
        65
    ],
    [
        "UI/src/utils/Permissions.js",
        "export const canPerformAction",
        30
    ],
    [
        "UI/src/utils/Functions.js",
        "export const getDataFromForm",
        44
    ],
    [
        "UI/src/components/buttons/EditButton.svelte",
        "export let popover_text = 'Modifica'",
        36
    ],
    [
        "UI/src/routes/association/course/instructor/InstructorList.svelte",
        "const columns =",
        220
    ],
    [
        "UI/src/routes/association/course/instructor/add/AddInstructor.svelte",
        "async function createInstructor(data)",
        142
    ],
    [
        "UI/src/routes/association/course/instructor/add/sections/Section1.svelte",
        "export let data =",
        355
    ],
    [
        "UI/src/routes/association/course/instructor/modals/EditModal.svelte",
        "async function update(data)",
        126
    ],
    [
        "UI/src/routes/association/course/instructor/info/Instructor.svelte",
        "async function fetchInfoWidget()",
        252
    ],
    [
        "UI/src/routes/association/course/instructor/info/modals/AddEditModal.svelte",
        "if (row.compensation_type == 'hourly')",
        545
    ],
    [
        "BE/application/views/instructor_views.py",
        "def instructor_add(request):",
        30
    ],
    [
        "BE/application/views/instructor_views.py",
        "def instructor_info(request, uid):",
        79
    ],
    [
        "BE/application/views/instructor_views.py",
        "def instructor_hours_list(request, uid):",
        60
    ],
    [
        "BE/application/views/instructor_views.py",
        "def instructor_hours_add(request, uid):",
        22
    ],
    [
        "BE/application/views/instructor_views.py",
        "def instructor_update(request, uid):",
        40
    ],
    [
        "BE/application/serializers/user_serializers.py",
        "class InstructorSerializer(",
        32
    ],
    [
        "BE/application/models/user_models.py",
        "class Instructor(models.Model):",
        72
    ],
    [
        "BE/application/permissions_registry.py",
        "'instructor/list'",
        15
    ],
    [
        "BE/core/settings.py",
        "\"DATE_INPUT_FORMATS\"",
        2
    ]
];

// Metadata is fresh on every call and never grants publication verification.
export function instructorEditorialContracts() {
    return instructorDraftPages().map(page => ({
        path: page.path, id: page.id, title: page.title, status: 'pending',
        reason: 'Procedura in attesa di esecuzione e revisione delle fonti.',
        recipe_module: 'docs/manuale/instructor-recipes.mjs', verified: false,
        source_contracts: instructorEditorialSourceContracts.map(([path, symbol, length]) => ({
            path, symbol, length, reviewed_sha256: instructorReviewedSources[path],
        })),
    }));
}
