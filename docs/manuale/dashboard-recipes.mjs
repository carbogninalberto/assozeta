export const dashboardCaptureSpecs = {
    'dashboard-personalize': ['images/bacheca/personalizzazione/', [
        'default-dashboard-eight-widgets', 'edit-mode-size-remove-and-save-controls',
        'add-widget-modal-expired-certificates-selected', 'added-widget-resized-to-full-width',
        'added-widget-and-width-persist-after-reload', 'remove-added-widget-with-trash-control',
        'removed-widget-absent-after-reload', 'default-restored-before-save',
        'default-layout-persists-after-reload', 'reader-dashboard-staff-permission-limit',
        'reader-personal-width-persists-owner-layout-unchanged',
    ]],
};

export const dashboardExpectedOutcome = Object.freeze({initial_widget_count: 8, available_widget_count: 12,
    initially_offered_widget_count: 4, added_widget: 'expiredmedicalcertificates',
    added_default_size: 4, added_saved_size: 12, associates_saved_size: 4,
    added_persisted_after_reload: true, removed_persisted_after_reload: true,
    reset_unsaved_left_database_unchanged: true, reset_persisted_after_reload: true,
    reader_layout_status: 200, reader_payments_size: 8, reader_layout_persisted_after_reload: true,
    owner_layout_preserved_after_reader_save: true, reader_staff_permission_message: true,
    drag_exercised: false, attendance_write_exercised: false, widget_business_actions_exercised: false});

const pending = '<Note>Bozza in attesa di prova: il contenuto e le immagini previste devono essere confermati dal flusso reale prima della pubblicazione.</Note>\n\n';
const widgetSections = [
    ['Soci', 'soci', 'Associates', 'associates'],
    ['Pagamenti incassati', 'pagamenti-incassati', 'Payments', 'payments'],
    ['Migliori 3 corsi per iscritti', 'migliori-3-corsi-per-iscritti', 'BestCourses', 'bestcourses'],
    ['Iscrizioni anno corrente', 'iscrizioni-anno-corrente', 'Subscriptions', 'subscriptions'],
    ['Lezioni di oggi', 'lezioni-di-oggi', 'TodayLessons', 'todaylessons'],
    ['Carnet in esaurimento', 'carnet-in-esaurimento', 'ExpiringCarnets', 'expiringcarnets'],
    ['Iscrizioni da approvare', 'iscrizioni-da-approvare', 'SubscriptionsToApprove', 'subscriptionstoapprove'],
    ['Certificati medici in scadenza', 'certificati-medici-in-scadenza', 'ExpiringMedicalCertificates', 'expiringmedicalcertificates'],
    ['Entrate e uscite', 'entrate-e-uscite', 'IncomeAndExpenses', 'incomeAndExpenses'],
    ['Pagamenti scaduti', 'pagamenti-scaduti', 'ExpiredPayments', 'expiredPayments'],
    ['Certificati medici scaduti', 'certificati-medici-scaduti', 'ExpiredMedicalCertificates', 'expiredmedicalcertificates'],
];
// Classification only. A descriptor is not independently installed evidence.
export const dashboardReferenceSections = Object.freeze(widgetSections.map(([title, id]) =>
    Object.freeze({path: 'docs/bacheca.mdx', title, id, kind: 'reviewed-reference'})));

export function dashboardDraftPages() {
    const image = (number, caption) => `<Frame>![${caption}](/images/bacheca/personalizzazione/${number}.png)</Frame>`;
    const step = (title, body) => `<Step title="${title}">\n${body}\n</Step>`;
    const steps = (...items) => `<Steps>\n${items.join('\n')}\n</Steps>`;
    const page = (title, id, level, body, imageNumbers = []) => ({path: 'docs/bacheca.mdx', title, id,
        body: '#'.repeat(level) + ' ' + title + '\n\n' + pending + body, imageNumbers});
    const edit = step('Attiva la modalità di modifica', `Apri **Bacheca** e premi **Modifica** in basso a destra.
Su un computer, sopra ciascun riquadro compaiono i pulsanti con rettangoli per cambiare larghezza e il cestino per rimuoverlo.
In basso trovi **Aggiungi widget**, **Ripristina default** e **Salva**.
${image(2, 'Modalità modifica e controlli per larghezza, rimozione e salvataggio')}`);
    const add = step('Scegli il widget da aggiungere', `Premi **Aggiungi widget**. La finestra propone i riquadri che non sono già nella bacheca.
Nell'esempio dell'**Associazione Sportiva Aurora** seleziona **Certificati medici scaduti**, poi premi **Aggiungi Widget**.
La finestra si chiude e il riquadro viene inserito alla fine. La selezione aggiunge un widget per volta: ripeti il passaggio per aggiungerne altri.
${image(3, 'Finestra Aggiungi widget con Certificati medici scaduti selezionato')}`);
    const resize = step('Scegli la larghezza del riquadro', `I rettangoli sopra il widget vanno dalla larghezza minore a quella maggiore.
Per **Certificati medici scaduti** sono disponibili quattro larghezze. Premi il quarto pulsante per usare tutta la riga.
Nell'esempio impostiamo anche il riquadro **Soci**, che nella bacheca è intitolato **Iscrizioni**, alla prima larghezza.
Questi pulsanti cambiano la larghezza; non impostano un'altezza personalizzata.
${image(4, 'Widget Certificati medici scaduti alla larghezza massima')}`);
    const save = step('Salva e controlla dopo il ricaricamento', `Premi **Salva** in basso a destra e attendi **Layout salvato con successo**.
Ricarica la pagina e controlla che **Certificati medici scaduti** sia ancora presente con la larghezza scelta.
Le modifiche si conservano dopo il salvataggio: aggiungere o ridimensionare un riquadro da solo non salva il layout.
${image(5, 'Widget aggiunto e larghezza conservati dopo il ricaricamento')}`);
    const remove = step('Rimuovi il riquadro', `Premi **Modifica** e individua il widget da togliere.
Nell'esempio premi il cestino sopra **Certificati medici scaduti**: il riquadro scompare dal layout in modifica.
${image(6, 'Cestino sopra il widget da rimuovere')}
La rimozione riguarda soltanto la bacheca; non elimina le iscrizioni o i certificati mostrati dal widget.`);
    const removed = step('Salva la rimozione', `Premi **Salva**, attendi la conferma e ricarica la pagina.
Controlla che **Certificati medici scaduti** sia assente. Per farlo tornare puoi scegliere di nuovo **Aggiungi widget**.
${image(7, 'Bacheca dopo la rimozione salvata e il ricaricamento')}`);
    const reset = step('Ripristina la disposizione iniziale', `Premi **Modifica**, poi **Ripristina default**.
I riquadri tornano alla disposizione iniziale: nell'esempio **Iscrizioni** torna alla larghezza iniziale e sono presenti otto widget.
${image(8, 'Disposizione iniziale ripristinata prima del salvataggio')}`);
    const resetSave = step('Conferma il ripristino', `Premi **Salva** e attendi la conferma.
Ricarica la bacheca: la disposizione iniziale deve essere ancora presente.
${image(9, 'Disposizione iniziale conservata dopo il ricaricamento')}
<Warning>Il ripristino sostituisce le tue scelte di widget e larghezze. Il risultato viene conservato quando premi **Salva**.</Warning>`);
    const widgetBodies = {
        soci: `Nella finestra di scelta il widget si chiama **Soci**; il riquadro nella bacheca mostra **Iscrizioni**.
Il numero con il segno **+** e il grafico contano le iscrizioni create negli ultimi 30 giorni nell'associazione.
Non rappresentano il totale di tutte le persone iscritte: usa l'elenco **Iscrizioni** per consultare le singole schede.`,
        'pagamenti-incassati': `Il riquadro **Pagamenti incassati** mostra la somma dei pagamenti con importo positivo e stato pagato, selezionati in base alla loro data di creazione negli ultimi 30 giorni.
Il grafico ripartisce tale importo per giorno. La data considerata qui è quella di creazione del pagamento.
<Note>Questo indicatore può includere anche spese saldate o pagamenti archiviati. Per leggere separatamente entrate e uscite consulta **Entrate e spese**.</Note>`,
        'migliori-3-corsi-per-iscritti': `Nella finestra il nome è **I 3 corsi con più iscritti**.
Mostra fino a tre corsi attivi, ordinati per numero di iscrizioni al corso. Se ne esistono meno di tre, l'elenco è più breve.
Il grafico aggiuntivo riguarda le iscrizioni ai corsi create nell'ultima settimana; il totale riguarda le iscrizioni ai corsi dell'associazione.`,
        'iscrizioni-anno-corrente': `Il widget si sceglie come **Tesserati e Soci anno corrente**; nella bacheca il titolo è **Tesserati anno corrente**.
Il grafico distingue le iscrizioni **In attesa**, **Non firmate**, **Rifiutate** e **Accettate**.
Sono conteggiate le iscrizioni non archiviate valide alla data corrente, di tipo socio e tesserato oppure solo tesserato. Le iscrizioni di tipo solo socio non entrano in questo grafico.`,
        'lezioni-di-oggi': `Il nome attuale è **Registro presenze lezioni**. Elenca le lezioni nella finestra della giornata, con il corso, gli istruttori quando indicati e i riepiloghi di presenza.
In assenza di lezioni compare **Nessuna lezione oggi**, come nella bacheca dimostrativa.
Un collaboratore associato a un profilo istruttore vede le lezioni collegate a quell'istruttore; gli altri account vedono quelle dell'associazione.
<Note>La registrazione delle presenze dal pulsante **Presenze** richiede una verifica dedicata. Questa pagina descrive il riquadro e non conferma un salvataggio di presenze. Il pulsante ha inoltre un limite nell'interfaccia per il piano gratuito; non dedurre altre differenze fra edizioni da questo esempio.</Note>`,
        'carnet-in-esaurimento': `Elenca i carnet con meno di quattro lezioni residue, ordinati dal numero minore di lezioni rimaste.
Un carnet senza lezioni residue viene incluso soltanto se il suo registro contiene attività negli ultimi 30 giorni.
Le voci mostrano la persona, il carnet e le lezioni rimaste. Un elenco vuoto non è una conferma generale dell'assenza di carnet nell'associazione.`,
        'iscrizioni-da-approvare': `Mostra fino a 100 iscrizioni non archiviate, valide alla data corrente, con stato **In attesa** o **Non firmate**.
La provenienza dal modulo online non è un requisito per comparire qui.
La larghezza cambia lo spazio disponibile per leggere l'elenco, senza modificare il numero massimo di voci selezionate.
<Note>I comandi di approvazione e rifiuto hanno i permessi delle iscrizioni. Questo esempio non esegue tali operazioni dal widget.</Note>`,
        'certificati-medici-in-scadenza': `Mostra i certificati con scadenza dalla data corrente fino ai successivi 30 giorni, compreso quello che scade oggi.
L'elenco riguarda iscrizioni non archiviate create nel periodo dell'esercizio corrente e con una scadenza medica presente.
Le voci sono ordinate dalle scadenze più vicine. I certificati già scaduti appartengono al riquadro separato **Certificati medici scaduti**.`,
        'entrate-e-uscite': `Nella finestra il widget è **Entrate e spese**; il riquadro si intitola **Riepilogo Pagamenti**.
Separa **Entrate** e **Uscite** per **Oggi**, **Ultimi 30gg** e **Da inizio anno**, usando i pagamenti saldati, non archiviati e con data di pagamento presente.
Il periodo annuale parte dal 1° gennaio. La voce **Ultimi 30gg** usa attualmente un intervallo che comincia 31 giorni prima dell'inizio della giornata: tieni conto di questa differenza fra etichetta e periodo nei confronti con altri prospetti.`,
        'pagamenti-scaduti': `Il nome nella finestra è **Pagamenti scaduti o in scadenza oggi**.
La selezione attuale comprende importi positivi non pagati, non archiviati, collegati a una persona e a un'iscrizione compatibile, creati fra 30 giorni fa e 7 giorni dopo la data corrente.
La dicitura di ritardo è calcolata dalla data di creazione del pagamento: possono comparire anche voci future con **scade tra ... gg.**.
<Note>Controlla i dati del pagamento prima di sollecitare la persona. La guida non conferma che il widget selezioni esclusivamente scadenze già trascorse. Il comando d'incasso richiede il permesso di modifica dei pagamenti e non è eseguito in questo esempio.</Note>`,
        'certificati-medici-scaduti': `Mostra i certificati con scadenza precedente alla data corrente, ordinati in base alla distanza dalla scadenza.
Sono considerate le iscrizioni create nell'esercizio corrente con un certificato e una data di scadenza presenti.
La selezione di questo widget non esclude esplicitamente le iscrizioni archiviate: controlla la scheda della persona prima di intervenire.
L'aggiunta e la rimozione del riquadro non modificano il documento o la sua scadenza.`,
    };
    const bodyAvailable = `La finestra **Aggiungi widget** comprende attualmente **12 tipi** di riquadro.
Quelli già inseriti non sono proposti di nuovo. La disposizione iniziale ne contiene otto; nell'esempio restano quattro scelte nella finestra.

| Nome nella finestra | Informazione principale |
| --- | --- |
| Soci | Iscrizioni create negli ultimi 30 giorni |
| Pagamenti incassati | Importi positivi pagati, secondo la data di creazione |
| I 3 corsi con più iscritti | Corsi attivi con più iscrizioni |
| Tesserati e Soci anno corrente | Stati delle iscrizioni valide oggi |
| Registro presenze lezioni | Lezioni della giornata |
| Carnet in esaurimento | Carnet con meno di quattro lezioni residue |
| Iscrizioni da approvare | Iscrizioni in attesa o non firmate |
| Certificati medici in scadenza | Scadenze da oggi ai prossimi 30 giorni |
| Entrate e spese | Riepilogo dei pagamenti saldati |
| Pagamenti scaduti o in scadenza oggi | Pagamenti non saldati nella finestra prevista |
| Certificati medici scaduti | Scadenze precedenti a oggi |
| Bacheca Staff | Anteprima degli ultimi messaggi dello staff |

**Bacheca Staff** è una scelta aggiuntiva rispetto agli undici riquadri descritti sotto.
Richiede il permesso di lettura dei messaggi: il collaboratore dimostrativo vede **Permessi insufficienti** e non riceve l'anteprima dei messaggi.
La disponibilità di un widget nella finestra non assegna i permessi per tutte le operazioni che il riquadro può proporre.
${image(10, 'Bacheca del collaboratore con limite di lettura dei messaggi dello staff')}`;
    return [
        page('Introduzione', 'introduzione', 2, `La **Bacheca** raccoglie i riquadri informativi dell'associazione e si apre come pagina iniziale per gli account con accesso alla bacheca.
Puoi scegliere quali riquadri vedere e quanto spazio occupano su un computer.
La disposizione è personale per il titolare e per ciascun collaboratore; per conservarla dopo il ricaricamento devi premere **Salva**.
${image(1, 'Bacheca iniziale dell’Associazione Sportiva Aurora con otto widget')}` , [1]),
        page('Personalizzare la bacheca', 'personalizzare-la-bacheca', 2,
            steps(edit, add, resize, save) + `\n\n<Note>Il collaboratore dimostrativo ha il permesso di lettura della bacheca e può salvare la propria disposizione.
Questo salvataggio non cambia quella del titolare e non autorizza le operazioni di modifica sulle iscrizioni o sui pagamenti.</Note>\n${image(11, 'Larghezza personale del collaboratore conservata senza cambiare la bacheca del titolare')}`, [2, 3, 4, 5, 11]),
        page('Riordinare i widget', 'riordinare-i-widget', 3,
            `In modalità modifica l'interfaccia prevede il trascinamento dei riquadri per cambiarne la posizione.
<Note>Il riordino con trascinamento non è stato esercitato in questa procedura. La sequenza operativa e il risultato dopo il salvataggio restano da verificare.</Note>`),
        page('Ridimensionare i widget', 'ridimensionare-i-widget', 3, steps(edit, resize, save) +
            '\n\n<Tip>Le larghezze disponibili dipendono dal widget: **Tesserati e Soci anno corrente** ne offre tre, gli altri riquadri della finestra ne offrono quattro.</Tip>', [2, 4, 5]),
        page('Aggiungere un widget', 'aggiungere-un-widget', 3, steps(edit, add, save) +
            '\n\n<Info>Quando tutte le scelte sono già presenti, la finestra mostra **Hai già aggiunto tutti i widget disponibili!**. Questa condizione non è esercitata nell’esempio con nove riquadri.</Info>', [2, 3, 5]),
        page('Rimuovere un widget', 'rimuovere-un-widget', 3, steps(remove, removed), [6, 7]),
        page('Ripristinare il layout predefinito', 'ripristinare-il-layout-predefinito', 3, steps(reset, resetSave), [8, 9]),
        page('Widget disponibili', 'widget-disponibili', 2, bodyAvailable, [10]),
        ...widgetSections.map(([title, id]) => page(title, id, 3, widgetBodies[id])),
        page('Consigli utili', 'consigli-utili', 2, `<Tip>Metti in bacheca i riquadri che consulti spesso e scegli una larghezza che renda leggibili le informazioni.</Tip>
<Tip>Dopo ogni personalizzazione premi **Salva** e ricarica la pagina per controllare la disposizione.</Tip>
<Note>Un riquadro vuoto o una limitazione dei permessi non sostituiscono il controllo nella sezione dedicata. Verifica i dati della persona prima di avvisarla di una scadenza.</Note>`),
    ];
}

const commonRefs = [
    ['UI/src/routes.js', "'/': wrap({", 37],
    ['UI/src/routes/dashboard/Dashboard.svelte', 'let componentsMap = {', 132],
    ['UI/src/routes/dashboard/Dashboard.svelte', 'use:dndzone={{', 137],
    ['UI/src/components/modals/WidgetModal.svelte', 'async function addWidget()', 114],
    ['UI/src/components/widgets/StaffBoard.svelte', 'const canRead =', 78],
    ['UI/src/utils/Permissions.js', 'export const canPerformAction', 32],
    ['BE/application/views/statistic_views.py', 'def statistic_dashboard_layout(request):', 19],
    ['BE/application/views/profile_views.py', 'def profile_info(request):', 45],
    ['BE/application/serializers/auth_serializers.py', "'dashboard_layout',", 4],
    ['BE/application/models/user_models.py', 'dashboard_layout =', 2],
    ['BE/application/models/subscriptions_models.py', 'class Subscription(GroupModelMixin):', 101],
    ['BE/application/models/payment_models.py', 'class PaymentManager(', 91],
    ['BE/application/models/courses_models.py', 'class CourseManager(', 74],
    ['BE/application/models/courses_models.py', 'class CourseSubscription(models.Model):', 43],
    ['BE/application/models/carnet_models.py', 'class CarnetSubscription(models.Model):', 20],
    ['BE/application/utils/api_utils.py', 'def get_range_from_year_and_starting_date(', 67],
    ['BE/application/permissions_registry.py', "'statistic/dashboard/layout'", 2],
    ['BE/application/impersonation.py', 'def resolve_request_identity(', 62],
    ['BE/application/impersonation.py', 'def acting_user(request):', 3],
];

// Frozen semantic review, updated only after inspecting the changed sources.
export const dashboardReviewedSources = Object.freeze({
    "UI/src/routes.js": "58bddc98548095f14f39fa3141277de8cd00e2133faf49f6761131b109d254b0",
    "UI/src/routes/dashboard/Dashboard.svelte": "d5cd8e48b8fb6f07c310735ae87169911c145daae237a5e84ec21d1876e41f9a",
    "UI/src/components/modals/WidgetModal.svelte": "49dcfaaf9fbb33e2b3503b9d6dd1bfdc64551a251fa97a9a32729638b1ea525a",
    "UI/src/components/widgets/StaffBoard.svelte": "1e22ec4c29067df662454be5e7acccc81388ae2717b86a0ab742818be0b6ffc0",
    "UI/src/utils/Permissions.js": "100a24508b57b6c73a323ab4f128ee2c79aef3212a2cd0178172199d59e51854",
    "BE/application/views/statistic_views.py": "c948d6e6c9d0846eca66f8d1c13ffa97d7e2071a7e4f27ebd2022ccd28623fb7",
    "BE/application/views/profile_views.py": "37aca983a9edb10673bd76786b7f8efce7adfeaa129e9d4848df85ead31936d1",
    "BE/application/serializers/auth_serializers.py": "edecff4a6618fc1862dc681056df9ab699de1a8cbb80cc004cf3f5642b7a1798",
    "BE/application/models/user_models.py": "3834a5655391ca750830d03175d7f663276697e50ab3fcfffcfd9ad45b7b931c",
    "BE/application/permissions_registry.py": "b8318ad63daa9452ac736b2152449b14c49f73bc1417622a4181b7c3ceb3b1fa",
    "BE/application/impersonation.py": "04337e8ece4eb8027f396392835170e56ff176a06b2565b70c87455417b5035e",
    "UI/src/components/widgets/Associates.svelte": "5bb400b1d4716eaa0a8fb48186f328ecedddf2b0ab2aab66893ba426acf2eeab",
    "UI/src/components/widgets/Payments.svelte": "4e3e6fe698b9208613e9a2ee270294b601ab5a5b89abb7ba3a48c7eec86328b5",
    "UI/src/components/widgets/BestCourses.svelte": "345edb15c8c27c746c2bb2d88da65293d257d59f12a2761ea19d4549c2e0cdb0",
    "UI/src/components/widgets/Subscriptions.svelte": "275ee58e414aa192eb400c03fe47bb34c9e2c5eb7a2376f36e413f1f93c95b25",
    "UI/src/components/widgets/TodayLessons.svelte": "89c5f1bac42bb7c462cf47b84711a0065852d9c99fd6a4e119857c143c263c34",
    "UI/src/components/widgets/ExpiringCarnets.svelte": "053b91063fc92d73ed9a928529947aaa419bdea44281bbb07221647773c6a2a8",
    "UI/src/components/widgets/SubscriptionsToApprove.svelte": "643903b6af944a5e63ca9f12e5b449e637af6125e870ed3a6c7472c5501a8d8a",
    "UI/src/components/widgets/ExpiringMedicalCertificates.svelte": "92b85d8bcd047a6fd097096a0b455244540408feddaac6eaff318477ad5e0592",
    "UI/src/components/widgets/IncomeAndExpenses.svelte": "d7c5a167a21ce0ad992cf4ebd50db81165766b6e305cf1d4e6db352a2dce41de",
    "UI/src/components/widgets/ExpiredPayments.svelte": "c27713e3023614df7e997cf10f1b53d740cda7a3aa5af9d8a84d12311f2f552e",
    "UI/src/components/widgets/ExpiredMedicalCertificates.svelte": "9ef5000617081a07a048cc6a502b283fca7b860f6385404c6b3ee4ac21709615",
    "BE/application/models/subscriptions_models.py": "b6b592e5ced8b685df6196e877ad75679bdf650c0d79774de9c537455eab465a",
    "BE/application/models/payment_models.py": "9dbcab8698adbde447a9c4f6eb52143409cf197fde3ce90d0aa8da56a1f8ebda",
    "BE/application/models/courses_models.py": "6a9cd0b923ab5b6a9dd120bcb7f81c1dac30914657fcd14b3fdb9e9c427d78c6",
    "BE/application/models/carnet_models.py": "3e3cb2cfb3d2be09cc5db6b97b43e2a2f391d91ccb61dfb38c99bf0ec7913713",
    "BE/application/utils/api_utils.py": "5db286fc73e92356b476514f4d6561a3300b36668ee99831ca1b197835a5cabf"
});
const reviewedFiles = dashboardReviewedSources;

function reviewedEvidence(report, source, refs) {
    return refs.map(args => {
        const evidence = source(report, ...args);
        if (evidence.canonical_source_sha256 !== reviewedFiles[args[0]])
            throw new Error('Dashboard description source changed; review required: ' + args[0]);
        return evidence;
    });
}

export function dashboardPages(id, report, source) {
    if (id !== 'dashboard-personalize') return [];
    if (report?.fixture_version !== 8 || report.fixture_profile !== 'baseline')
        throw new Error('Dashboard workflow requires fixture version 7 with baseline profile');
    if (report.status !== 'passed' || report.backend !== 'real' || report.capture_format !== 'full-hd-v1' ||
        report.viewport?.width !== 1920 || report.viewport?.height !== 1080 || report.device_scale_factor !== 1)
        throw new Error('Dashboard workflow requires passed real-backend Full HD captures');
    const [prefix, checkpoints] = dashboardCaptureSpecs[id];
    if (!Array.isArray(report.screenshots) || report.screenshots.length !== checkpoints.length ||
        report.screenshots.some((image, index) => !image || image.path !== prefix + (index + 1) + '.png' ||
            image.checkpoint !== checkpoints[index]))
        throw new Error('Dashboard checkpoints do not match the reviewed steps');
    if (Object.entries(dashboardExpectedOutcome).some(([key, value]) => report.dashboard_workflow?.[key] !== value))
        throw new Error('Dashboard outcomes differ from the reviewed example');
    const common = reviewedEvidence(report, source, commonRefs);
    return dashboardDraftPages().filter(page => page.id !== 'riordinare-i-widget').map(({imageNumbers, ...page}) => {
        const widget = widgetSections.find(([, id]) => id === page.id);
        const refs = widget ? [
            [`UI/src/components/widgets/${widget[2]}.svelte`, '<script>', 250],
            ['BE/application/views/statistic_views.py', widget[3] === 'subscriptions'
                ? "if widget == 'subscriptions':" : "elif widget == '" + widget[3] + "':", 100],
        ] : [];
        return {...page, body: page.body.replace(pending, ''),
            kind: widget ? 'reviewed-reference' : 'browser-workflow', status: 'verified',
            evidence: [...common, ...reviewedEvidence(report, source, refs)],
            screenshots: imageNumbers.map(number => report.screenshots[number - 1])};
    });
}

// Pure discovery never calls verified builders or fabricates source evidence.
const editorialDescriptor = (page, refs, reviewed, recipe_module, status = page.status, reason = page.reason) => ({
    path: page.path, id: page.id, title: page.title,
    status: ['unsupported', 'needs_external_verification'].includes(status) ? status : 'pending',
    reason: reason || 'Editorial draft metadata only; implementation and workflow proof remain required.',
    recipe_module, verified: false,
    source_contracts: refs.map(([path, symbol, length]) => ({path, symbol, length, reviewed_sha256: reviewed[path]})),
});

export function dashboardEditorialContracts() {
    const module = "docs/manuale/dashboard-recipes.mjs";
    return dashboardDraftPages().map(page => {
        const widget = widgetSections.find(([,id]) => id === page.id);
        const refs = widget ? [...commonRefs,
            [`UI/src/components/widgets/${widget[2]}.svelte`, '<script>', 250],
            ['BE/application/views/statistic_views.py', widget[3] === 'subscriptions'
                ? "if widget == 'subscriptions':" : "elif widget == '" + widget[3] + "':", 100],
        ] : commonRefs;
        return editorialDescriptor(page, refs, dashboardReviewedSources, module);
    });
}
