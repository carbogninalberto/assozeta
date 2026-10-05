// Draft editorial provenance only; this map does not alter runtime verification.
export const contentReviewedSources = Object.freeze({
    "BE/application/models/courses_models.py": "6a9cd0b923ab5b6a9dd120bcb7f81c1dac30914657fcd14b3fdb9e9c427d78c6",
    "BE/application/permissions_registry.py": "b8318ad63daa9452ac736b2152449b14c49f73bc1417622a4181b7c3ceb3b1fa",
    "BE/application/views/course_views.py": "5d32e168d9af6100c280b0d0043ca381742f7c2cc300e94f4edd1fd89cb9fc98",
    "BE/application/views/payment_views.py": "4f45b99318afaa7189177f73cf82863f6f281dc8a2992b15dcf299b11f8e8c34",
    "BE/application/views/subscriptions_views.py": "d7022114e515be2da57e83b59fbe8042b06a273b61db7030839b31c1297022dc",
    "UI/src/components/Sidebar.svelte": "9f8344602ae269b5dbbf90dfd6f75e5154f3d5774ad544ac41105b74af2e333c",
    "UI/src/components/buttons/ArchiveButton.svelte": "1586f271e9651a1126b3f2662d32808de697c4d8b1cccd95150b6371ed32f4bb",
    "UI/src/routes.js": "58bddc98548095f14f39fa3141277de8cd00e2133faf49f6761131b109d254b0",
    "UI/src/routes/association/Members/MembersList.svelte": "2205d4071d49dc3c96bad02299dd157668037807f33811b395f811197841650a",
    "UI/src/routes/association/Members/MembersListArchive.svelte": "8426ce4abf1e1146aaf85d87a983642170548305820bbdb8590813fe666e3578",
    "UI/src/routes/association/Members/shared/NavigationTab.svelte": "b00baefb6bd572542cf2b6d2cd74ba67b6bcd87085b923088156617374b9b432",
    "UI/src/routes/association/course/CourseList.svelte": "d68233a0b121dcbf15b840ea120348b331d017b4dd7ab936da95c46f1c50ad84",
    "UI/src/routes/association/course/add/AddCourse.svelte": "c1e10ddcd4c3f258a6e3f4fe387b77a68a85b0ff74191701e169dfad2d38f4a5",
    "UI/src/routes/association/course/add/sections/Section1.svelte": "f3e456ade95d87006f5a490dfef3ce9dd0e1f49ace814205a4cca049bb7622f7",
    "UI/src/routes/association/course/overview/OverviewCourse.svelte": "5aed27d46e9ce99c75452e3cd766db412daaaeca7ac2084a20581358195c06ce"
});

// Reviewed projections for the named real workflows. Every behavioral claim
// below is bound to the source references supplied by the generation validator.
export function additionalPages(id, report, source) {
    if (id === 'courses-create-edit') {
        const evidence = [
            source(report, 'UI/src/components/Sidebar.svelte', '<span class="menu-text">Attività</span>', 45),
            source(report, 'UI/src/routes.js', "'/course/list':", 19),
            source(report, 'UI/src/routes/association/course/CourseList.svelte', "title: 'Aggiungi corso o abbonamento'", 18),
            source(report, 'UI/src/routes/association/course/add/AddCourse.svelte', 'function validateForm()', 41),
            source(report, 'UI/src/routes/association/course/add/AddCourse.svelte', 'async function createCourse()', 58),
            source(report, 'UI/src/routes/association/course/add/sections/Section1.svelte', 'placeholder="Titolo Corso"', 25),
            source(report, 'UI/src/routes/association/course/add/sections/Section1.svelte', "label: 'Quota attività'", 22),
            source(report, 'UI/src/routes/association/course/overview/OverviewCourse.svelte', 'async function updateData()', 40),
            source(report, 'UI/src/routes/association/course/overview/OverviewCourse.svelte', "{editingInfo ? 'Annulla' : 'Modifica'}", 18),
            source(report, 'BE/application/views/course_views.py', 'def course_add(', 111),
            source(report, 'BE/application/views/course_views.py', 'def course_update(', 93),
            source(report, 'BE/application/models/courses_models.py', 'class Course(', 56),
            source(report, 'BE/application/permissions_registry.py', "'course/add'", 8),
        ];
        return [{path: 'docs/corsi.mdx', title: 'Creare un corso', id: 'creare-un-corso', evidence,
            screenshots: report.screenshots.slice(0, 2), body: `## Creare un corso

<Steps>
  <Step title="Apri i corsi">
    Dal menu laterale apri **Attività → Corsi** e premi **Corso o Abbonamento**.
  </Step>
  <Step title="Compila un corso Standard">
    Inserisci **Titolo** e **Descrizione**. Mantieni la tipologia **Standard** e indica una **Quota attività** maggiore di zero, per esempio **90,00**.
    <Frame>![Dati di un nuovo corso Standard](/images/corsi/creazione-modifica/1.png)</Frame>
  </Step>
  <Step title="Salva il corso">
    Premi **Salva**. Il corso compare nell'elenco **Corsi e abbonamenti**.
    <Frame>![Nuovo corso nell'elenco](/images/corsi/creazione-modifica/2.png)</Frame>
  </Step>
</Steps>

<Note>Un collaboratore con accesso in sola lettura può consultare i corsi. Per crearli serve il permesso di creazione; il pulsante non compare quando manca.</Note>`},
        {path: 'docs/corsi.mdx', title: 'Modificare le informazioni del corso', id: 'modificare-le-informazioni-del-corso', evidence,
            screenshots: report.screenshots.slice(2), body: `### Modificare le informazioni del corso

<Steps>
  <Step title="Apri e modifica">
    Nell'elenco dei corsi premi il **titolo** del corso, poi **Modifica**. Cambia il campo **Titolo**.
    <Frame>![Modifica del titolo di un corso](/images/corsi/creazione-modifica/3.png)</Frame>
  </Step>
  <Step title="Salva e controlla">
    Premi **Salva**. Puoi ricaricare la scheda e verificare che mostri il nuovo titolo.
    <Frame>![Titolo modificato dopo il ricaricamento](/images/corsi/creazione-modifica/4.png)</Frame>
  </Step>
</Steps>

<Note>Per salvare una modifica serve il relativo permesso. Il solo accesso in lettura non autorizza il salvataggio.</Note>`}];
    }
    if (id === 'members-archive-restore') {
        const evidence = [
            source(report, 'UI/src/routes.js', "'/members/archive':", 20),
            source(report, 'UI/src/routes/association/Members/shared/NavigationTab.svelte', "title: 'Archivio'", 8),
            source(report, 'UI/src/routes/association/Members/MembersList.svelte', 'async function archiveSelected()', 64),
            source(report, 'UI/src/routes/association/Members/MembersList.svelte', 'window.archiveSubscription = async', 60),
            source(report, 'UI/src/routes/association/Members/MembersListArchive.svelte', "text: \"Vuoi spostare l'atleta nel libro soci?\"", 51),
            source(report, 'UI/src/components/buttons/ArchiveButton.svelte', 'export let popover_text', 26),
            source(report, 'BE/application/views/subscriptions_views.py', 'def subscription_archive(', 88),
            source(report, 'BE/application/views/payment_views.py', 'def payment_list(', 69),
            source(report, 'BE/application/permissions_registry.py', "'subscription/*/archive'", 8),
        ];
        const manual = `# Archiviazione manuale

<Steps>
  <Step title="Seleziona l'iscrizione">
    Apri **Organizzazione → Iscrizioni** e seleziona la casella accanto alla persona da archiviare.
    <Frame>![Iscrizione selezionata per l'archiviazione](/images/faq/archiviazione/1.png)</Frame>
  </Step>
  <Step title="Conferma l'archiviazione">
    Premi **Archivia** nella barra delle azioni, poi **Archivia selezionati** nella finestra di conferma. L'iscrizione viene tolta dall'elenco attivo.
    <Frame>![Conferma dell'archiviazione](/images/faq/archiviazione/2.png)</Frame>
  </Step>
</Steps>

<Note>Un collaboratore in sola lettura può consultare le iscrizioni, ma non archiviarle. Per l'archiviazione servono i relativi permessi.</Note>`;
        const restore = `# Ripristinare i dati archiviati

<Steps>
  <Step title="Apri l'archivio delle iscrizioni">
    Apri **Organizzazione → Iscrizioni**, quindi la scheda **Archivio**. Nella riga della persona premi il pulsante con il suggerimento **Rimuovi dall'archivio**.
    <Frame>![Iscrizione archiviata e pulsante di ripristino](/images/faq/archiviazione/3.png)</Frame>
  </Step>
  <Step title="Conferma lo spostamento">
    Nella finestra di conferma premi **Sposta nel libro soci**.
    <Frame>![Conferma del ritorno nell'elenco attivo](/images/faq/archiviazione/4.png)</Frame>
  </Step>
  <Step title="Controlla l'elenco attivo">
    Torna alla scheda **Tesserati**. L'iscrizione compare di nuovo e rimane visibile dopo il ricaricamento.
    <Frame>![Iscrizione ripristinata nell'elenco attivo](/images/faq/archiviazione/5.png)</Frame>
  </Step>
</Steps>`;
        return [
            {path: 'faq/come-archiviare-dati.mdx', title: 'Cosa significa archiviare', id: 'cosa-significa-archiviare', evidence,
                screenshots: [], body: `# Cosa significa archiviare

In Assozeta, archiviare un'iscrizione la sposta dall'elenco **Tesserati** alla scheda **Archivio** delle iscrizioni. Puoi consultarla e riportarla nell'elenco attivo con l'azione di ripristino.

Il comportamento dei pagamenti collegati è descritto nella sezione **Cosa viene archiviato**.`},
            {path: 'faq/come-archiviare-dati.mdx', title: 'Archiviazione manuale', id: 'archiviazione-manuale', evidence,
                screenshots: report.screenshots.slice(0, 2), body: manual},
            {path: 'faq/come-archiviare-dati.mdx', title: 'Consultare i dati archiviati', id: 'consultare-i-dati-archiviati', evidence,
                screenshots: report.screenshots.slice(2, 3), body: `# Consultare i dati archiviati

Per consultare le iscrizioni archiviate apri **Organizzazione → Iscrizioni** e scegli la scheda **Archivio**.

<Frame>![Archivio delle iscrizioni](/images/faq/archiviazione/3.png)</Frame>`},
            {path: 'faq/come-archiviare-dati.mdx', title: 'Ripristinare i dati archiviati', id: 'ripristinare-i-dati-archiviati', evidence,
                screenshots: report.screenshots.slice(2), body: restore},
            {path: 'faq/come-archiviare-dati.mdx', title: 'Cosa viene archiviato', id: 'cosa-viene-archiviato', evidence,
                screenshots: [], body: `# Cosa viene archiviato

L'operazione archivia l'iscrizione. Archivia anche il suo pagamento collegato quando non è saldato e gli altri pagamenti non saldati della persona che rientrano nel periodo dell'iscrizione.

<Warning>Ripristinare l'iscrizione non ripristina automaticamente i pagamenti archiviati. Nell'esempio di Sara Conti, la quota non saldata resta nell'archivio dei pagamenti anche dopo il ritorno della persona nell'elenco attivo.</Warning>`},
            {path: 'docs/archivio.mdx', title: 'Introduzione', id: 'introduzione', evidence,
                screenshots: report.screenshots.slice(2, 3), body: `## Introduzione

Le iscrizioni archiviate si consultano da **Organizzazione → Iscrizioni → Archivio**. Questa scheda conserva le iscrizioni spostate fuori dall'elenco attivo.

<Frame>![Archivio delle iscrizioni in Assozeta](/images/faq/archiviazione/3.png)</Frame>`},
            {path: 'docs/archivio.mdx', title: 'Ripristinare un elemento dall\'archivio', id: 'ripristinare-un-elemento-dall-archivio', evidence,
                screenshots: report.screenshots.slice(4), body: `## Ripristinare un elemento dall'archivio

Per riportare un'iscrizione nell'elenco attivo, dalla scheda **Archivio** premi **Rimuovi dall'archivio** nella riga della persona e conferma con **Sposta nel libro soci**. Torna alla scheda **Tesserati** per controllare il risultato.

<Frame>![Iscrizione tornata nell'elenco attivo](/images/faq/archiviazione/5.png)</Frame>

<Warning>Il ripristino dell'iscrizione non ripristina automaticamente i suoi pagamenti archiviati.</Warning>`},
        ];
    }
    return [];
}

export function contentDraftPages() {
    return ['courses-create-edit', 'members-archive-restore']
        .flatMap(id => additionalPages(id, {screenshots: []}, () => ({})))
        .map(({evidence, screenshots, ...page}) => page);
}

// Original legacy section identities, without invoking a proof builder.
const contentEditorialSections = [
    [
        "docs/corsi.mdx",
        "creare-un-corso",
        "Creare un corso"
    ],
    [
        "docs/corsi.mdx",
        "modificare-le-informazioni-del-corso",
        "Modificare le informazioni del corso"
    ],
    [
        "faq/come-archiviare-dati.mdx",
        "cosa-significa-archiviare",
        "Cosa significa archiviare"
    ],
    [
        "faq/come-archiviare-dati.mdx",
        "archiviazione-manuale",
        "Archiviazione manuale"
    ],
    [
        "faq/come-archiviare-dati.mdx",
        "consultare-i-dati-archiviati",
        "Consultare i dati archiviati"
    ],
    [
        "faq/come-archiviare-dati.mdx",
        "ripristinare-i-dati-archiviati",
        "Ripristinare i dati archiviati"
    ],
    [
        "faq/come-archiviare-dati.mdx",
        "cosa-viene-archiviato",
        "Cosa viene archiviato"
    ],
    [
        "docs/archivio.mdx",
        "introduzione",
        "Introduzione"
    ],
    [
        "docs/archivio.mdx",
        "ripristinare-un-elemento-dall-archivio",
        "Ripristinare un elemento dall'archivio"
    ]
];
const contentEditorialSourceContracts = {
    "courses": [
        [
            "UI/src/components/Sidebar.svelte",
            "<span class=\"menu-text\">Attività</span>",
            45
        ],
        [
            "UI/src/routes.js",
            "'/course/list':",
            19
        ],
        [
            "UI/src/routes/association/course/CourseList.svelte",
            "title: 'Aggiungi corso o abbonamento'",
            18
        ],
        [
            "UI/src/routes/association/course/add/AddCourse.svelte",
            "function validateForm()",
            41
        ],
        [
            "UI/src/routes/association/course/add/AddCourse.svelte",
            "async function createCourse()",
            58
        ],
        [
            "UI/src/routes/association/course/add/sections/Section1.svelte",
            "placeholder=\"Titolo Corso\"",
            25
        ],
        [
            "UI/src/routes/association/course/add/sections/Section1.svelte",
            "label: 'Quota attività'",
            22
        ],
        [
            "UI/src/routes/association/course/overview/OverviewCourse.svelte",
            "async function updateData()",
            40
        ],
        [
            "UI/src/routes/association/course/overview/OverviewCourse.svelte",
            "{editingInfo ? 'Annulla' : 'Modifica'}",
            18
        ],
        [
            "BE/application/views/course_views.py",
            "def course_add(",
            111
        ],
        [
            "BE/application/views/course_views.py",
            "def course_update(",
            93
        ],
        [
            "BE/application/models/courses_models.py",
            "class Course(",
            56
        ],
        [
            "BE/application/permissions_registry.py",
            "'course/add'",
            8
        ]
    ],
    "archive": [
        [
            "UI/src/routes.js",
            "'/members/archive':",
            20
        ],
        [
            "UI/src/routes/association/Members/shared/NavigationTab.svelte",
            "title: 'Archivio'",
            8
        ],
        [
            "UI/src/routes/association/Members/MembersList.svelte",
            "async function archiveSelected()",
            64
        ],
        [
            "UI/src/routes/association/Members/MembersList.svelte",
            "window.archiveSubscription = async",
            60
        ],
        [
            "UI/src/routes/association/Members/MembersListArchive.svelte",
            "text: \"Vuoi spostare l'atleta nel libro soci?\"",
            51
        ],
        [
            "UI/src/components/buttons/ArchiveButton.svelte",
            "export let popover_text",
            26
        ],
        [
            "BE/application/views/subscriptions_views.py",
            "def subscription_archive(",
            88
        ],
        [
            "BE/application/views/payment_views.py",
            "def payment_list(",
            69
        ],
        [
            "BE/application/permissions_registry.py",
            "'subscription/*/archive'",
            8
        ]
    ]
};

// Metadata is fresh on every call and never grants publication verification.
export function contentEditorialContracts() {
    return contentEditorialSections.map(([path, id, title]) => ({path, id, title})).map(page => ({
        path: page.path, id: page.id, title: page.title, status: 'pending',
        reason: 'Procedura in attesa di esecuzione e revisione delle fonti.',
        recipe_module: 'docs/manuale/content-recipes.mjs', verified: false,
        source_contracts: contentEditorialSourceContracts[page.path === 'docs/corsi.mdx' ? 'courses' : 'archive'].map(([path, symbol, length]) => ({
            path, symbol, length, reviewed_sha256: contentReviewedSources[path],
        })),
    }));
}
