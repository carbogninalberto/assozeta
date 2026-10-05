import {attendanceCarnetSources} from '../../selfhost/tests/browser/manuale/attendance-carnet-sources.mjs';

export const attendanceCarnetCaptureSpecs = {
    'attendance-carnet-manual': ['images/registro-presenze/carnet-manuale/', [
        'course-enrollment-selected-current-member', 'course-enrollment-persists-and-course-fee-created',
        'filled-five-lesson-carnet-wizard', 'created-public-carnet-without-assignment-payment',
        'make-carnet-private-in-details', 'select-member-for-new-carnet-assignment',
        'assigned-carnet-five-of-five-with-unpaid-payment', 'select-enrolled-course-for-carnet',
        'linked-course-and-five-lessons-persist', 'approve-carnet-payment-without-pdf-or-email',
        'create-single-lesson-with-date-and-time', 'published-single-lesson-in-attendance-register',
        'manual-attendance-saved-for-giulia', 'carnet-consumed-four-of-five-persists-after-reload',
        'removed-attendance-restores-five-lessons-and-clears-usage',
    ]],
};

export const attendanceCarnetExpectedOutcome = Object.freeze({initial_registrations: 0, final_registrations: 1,
    initial_carnets: 0, final_carnets: 1, initial_assignments: 0, final_assignments: 1,
    course_fee: 120, course_payment_paid: false, carnet_fee: 50, carnet_private: true,
    carnet_payment_paid: true, payment_count: 5, link_creates_payment: false,
    calendar_published: true, lesson_count: 1, lessons_initial: 5, lessons_after_checkin: 4,
    usage_after_checkin: 1, lessons_after_removal: 5, usage_after_removal: 0,
    attendance_after_removal: 0, persisted_after_reload: true, reader_write_denials: 6,
    denial_left_state_unchanged: true, email_sent: false, automatic_attendance_tested: false});

const pendingProof = '<Note>Bozza in attesa di prova: i passaggi e le immagini previste devono essere verificati con una cattura del flusso reale prima della pubblicazione.</Note>\n\n';
const image = (number, caption) => `<Frame>![${caption}](/images/registro-presenze/carnet-manuale/${number}.png)</Frame>`;
const step = (title, body) => `<Step title="${title}">\n${body}\n</Step>`;
const steps = (...items) => `<Steps>\n${items.join('\n')}\n</Steps>`;
const slug = value => value.toLowerCase().normalize('NFD').replace(/[\u0300-\u036f]/g, '').replace(/[^a-z0-9]+/g, '-').replace(/^-|-$/g, '');
const access = '<Note>Un collaboratore con il solo accesso in lettura può consultare corso, carnet e presenze. Per iscrivere un tesserato, creare o assegnare un carnet, modificare il calendario o segnare presenze servono i permessi delle singole operazioni.</Note>';

function sectionIntent(path, id) {
    if (['correggere-una-presenza-errata', 'ripristino-delle-lezioni', 'restituzione-alla-rimozione',
        'gestire-le-lezioni-perse-e-le-correzioni'].includes(id)) return 'attendance.remove';
    if (['aggiungere-o-rimuovere-atleti', 'corso-con-pagamento-per-lezione'].includes(id)) return 'courses.enroll';
    if (['calendario-delle-lezioni', 'gestire-le-lezioni', 'lezione-singola', 'pubblicare-il-calendario'].includes(id))
        return 'courses.calendar.create';
    if (id === 'creare-e-gestire-un-carnet') return 'carnet.create';
    if (id === 'carnet-pubblici-e-privati') return 'carnet.update';
    if (['assegnare-un-carnet-ad-un-atleta', 'assegnare-un-corso-ad-un-carnet', 'assegnare-un-nuovo-carnet'].includes(id))
        return 'carnet.assign';
    if (['controllare-le-lezioni-rimanenti', 'consigli-per-la-gestione-dei-carnet', 'riepilogo-delle-regole-di-funzionamento',
        'cos-e-un-carnet', 'come-funziona-un-carnet', 'registro-lezioni-nel-carnet'].includes(id)) return 'carnet.read';
    if (id === 'ricaricare-un-carnet') return 'carnet.topup';
    if (id === 'modificare-le-lezioni-rimanenti') return 'carnet.balance.update';
    if (id.includes('automatic') || id === 'blocco-per-assenza-prevista' || id === 'segnalazione-assenze-da-parte-degli-atleti'
        || id === 'come-funziona') return 'attendance.automatic';
    if (id === 'logica-di-scalamento-lezioni' || id === 'casi-particolari-con-i-carnet') return 'carnet.variants';
    if (id.includes('sincronizzazione')) return 'courses.calendar.update';
    if (id === 'eliminare-una-lezione-dal-registro') return 'attendance.date.delete';
    if (['introduzione', 'visualizzare-il-registro-presenze', 'consultare-lo-storico-presenze-di-un-atleta',
        'risorse-correlate', 'indice', 'conclusione'].includes(id)) return 'attendance.read';
    return 'attendance.update';
}

export function attendanceCarnetDraftPages() {
    const carnetDoc = 'docs/carnet.mdx', attendanceDoc = 'docs/registro-presenze.mdx';
    const tutorial = 'tutorials/come-gestire-registro-presenze-carnet.mdx', courses = 'docs/corsi.mdx';
    const page = (path, title, level, body, imageNumbers = [], pendingReason) => ({path, title, id: slug(title),
        intent: sectionIntent(path, slug(title)),
        body: '#'.repeat(level) + ' ' + title + '\n\n' + pendingProof + body, imageNumbers,
        ...(pendingReason ? {pendingReason} : {})});
    const enroll = step('Iscrivi il tesserato al corso', `Apri **Attività → Corsi e Abbonamenti** e scegli **Ginnastica per tutti**.
Nella scheda **Iscritti al corso**, premi **Tesserato**. Nel modulo **Iscrivi al corso**, cerca **Giulia** in **Tesserati** e seleziona **GIULIA BIANCHI (Anno corrente)**.
Premi **Aggiungi** e controlla che Giulia compaia nell'elenco anche dopo aver ricaricato la pagina.
${image(1, 'Giulia Bianchi selezionata nel modulo Iscrivi al corso')}
${image(2, 'Iscrizione di Giulia conservata nel corso')}
<Note>Il corso dell'esempio ha una quota di **120,00 €**: l'iscrizione genera il relativo pagamento in attesa. Il carnet viene assegnato separatamente e aggiunge il proprio pagamento; non sostituisce questa quota.</Note>`);
    const create = step('Crea il pacchetto di lezioni', `Apri **Attività → Carnet** e premi **Carnet**.
In **Nuovo Carnet**, compila **Titolo**, **Descrizione**, **Prezzo del carnet** e **Numero lezioni**.
Nell'esempio usiamo **Carnet ginnastica 5 lezioni**, **50,00 €** e **5** lezioni.
${image(3, 'Informazioni del carnet: titolo, descrizione, prezzo e cinque lezioni')}
Premi **Continua**. Nella seconda fase puoi selezionare gli atleti a cui assegnarlo subito; nell'esempio lasciamo la lista vuota per assegnarlo in seguito.
Premi **Completa**, controlla i dati e conferma **Continua**. Il nuovo carnet compare nell'elenco come **pubblico**.
${image(4, 'Pacchetto creato nell’elenco Carnet')}
<Note>Creare il pacchetto senza assegnazioni non genera un pagamento. Il pagamento nasce quando lo assegni a un tesserato.</Note>`);
    const privacy = step('Rendi privato il pacchetto', `Apri il titolo del carnet nell'elenco e resta nella scheda **Dettagli**.
Disattiva **Carnet pubblico**, poi premi **Salva**. La scelta resta salvata dopo il ricaricamento.
${image(5, 'Carnet pubblico disattivato nella scheda Dettagli')}
<Note>Il modulo iniziale non contiene il campo Pubblico: per ottenere il carnet privato dell'esempio lo modifichiamo dopo la creazione. L'acquisto dall'area atleta richiede una verifica separata.</Note>`);
    const assign = step('Assegna un nuovo carnet a Giulia', `Apri il carnet e scegli **Utilizzo**.
Premi **Assegna carnet**. Nel modulo **Assegna un carnet**, cerca Giulia in **Associa carnet a**, seleziona **Giulia Bianchi** e premi **Assegna**.
${image(6, 'Giulia selezionata per l’assegnazione del carnet')}
Nell'elenco **Utilizzo del Carnet** compare una nuova riga con **5/5** lezioni. L'assegnazione genera un pagamento di **50,00 €**, inizialmente in attesa.
${image(7, 'Carnet assegnato a Giulia con cinque lezioni disponibili')}
<Note>Una riga senza collegamenti mostra **Valido per tutti i corsi**. Il carnet può essere usato per le presenze nei corsi a cui il tesserato è iscritto; il collegamento permette di limitarlo al corso scelto.</Note>`);
    const link = step('Collega il carnet al corso', `Nella riga di Giulia, premi **collega corso**.
In **Assegna carnet ad un corso**, seleziona **Ginnastica per tutti** e premi **Assegna carnet**.
${image(8, 'Corso già frequentato da Giulia selezionato per il collegamento')}
Il titolo del corso compare nella colonna **Corsi** e il conteggio rimane **5/5** anche dopo il ricaricamento.
${image(9, 'Collegamento al corso conservato nella riga del carnet')}
<Warning>La lista propone i corsi a cui il tesserato è già iscritto. Se manca il corso desiderato, esegui prima l'iscrizione al corso. Il collegamento dell'assegnazione esistente non genera un secondo pagamento.</Warning>`);
    const pay = step('Incassa il pagamento del carnet', `Apri **Pagamenti** e trova il pagamento di **50,00 €** intestato a Giulia, creato dall'assegnazione.
Premi il pulsante con il suggerimento **Segna come pagato**. In **Incassare il pagamento?**, indica **Data Incasso** e premi **Incassa**.
Nell'esempio togliamo **Genera ricevuta** e lasciamo **Invia email** disattivato: il pagamento viene incassato senza produrre subito il PDF o inviare un messaggio.
${image(10, 'Incasso del pagamento del carnet senza PDF immediato o email')}
<Note>Il pagamento della quota corso da 120,00 € resta separato e in attesa. Controlla importo e intestatario prima di confermare l'incasso del carnet.</Note>`);
    const calendar = step('Aggiungi la lezione al calendario', `Apri **Ginnastica per tutti** e premi **Calendario**.
Nel mese visualizzato clicca sul giorno della lezione. In **Crea lezione**, compila **Nome**, **Inizio** e **Fine**; l'istruttore è facoltativo.
Nell'esempio inseriamo **Lezione dimostrativa di ginnastica**, dalle **08:00** alle **09:00** nella data del registro dimostrativo.
${image(11, 'Lezione singola con nome e orario nel modulo Crea lezione')}
Premi **Crea**. Il salvataggio pubblica il calendario e rende disponibile la nuova riga nelle presenze.
<Note>Questa versione salva direttamente la lezione quando premi Crea. Per la lezione singola dell'esempio non serve un ulteriore comando Salva e Aggiorna.</Note>`);
    const register = step('Apri la scheda Presenze', `Dalla pagina del corso premi **Presenze**.
Nel **Registro delle presenze** trova **Lezione dimostrativa di ginnastica**, con data e orario.
${image(12, 'Nuova lezione nel Registro delle presenze')}
Premi il pulsante blu con l'icona della lista nella riga della lezione per aprire **Modifica Presenze**.
<Note>La navigazione attuale usa le schede Iscritti al corso, Calendario e Presenze. Non è necessario cercare il vecchio pulsante Mostra registro.</Note>`);
    const checkin = step('Segna Giulia come presente', `In **Modifica Presenze**, cerca l'atleta se la lista è lunga usando **Ricerca atleta**.
Trova **Bianchi Giulia** e attiva il suo interruttore. Ogni cambio viene salvato subito; attendi la conferma prima di chiudere.
${image(13, 'Presenza di Giulia attiva nella lezione')}
Premi **Chiudi**, poi apri **Carnet → Carnet ginnastica 5 lezioni → Utilizzo**.
La riga di Giulia mostra **4/5**: la presenza ha consumato una sola lezione. Il conteggio rimane dopo il ricaricamento.
${image(14, 'Conteggio conservato di quattro lezioni disponibili su cinque')}`);
    const remove = step('Correggi una presenza e controlla il carnet', `Riapri **Ginnastica per tutti → Presenze** e la stessa lezione con il pulsante blu della lista.
In **Modifica Presenze**, disattiva l'interruttore di **Bianchi Giulia**. Attendi il salvataggio e premi **Chiudi**.
Torna al carnet nella scheda **Utilizzo**: la riga torna a **5/5** e rimane così anche dopo il ricaricamento.
${image(15, 'Cinque lezioni ripristinate dopo la rimozione della presenza')}
<Note>Disattivare una presenza corregge la partecipazione alla lezione e restituisce la lezione usata. Eliminare un'intera data dal registro è un'altra operazione.</Note>`);
    const automaticGap = 'Le presenze automatiche e le assenze previste dell’area atleta richiedono una prova dedicata. La procedura qui illustrata registra e corregge manualmente una presenza; non dimostra l’esecuzione del processo automatico.';
    const syncGap = 'La creazione della lezione singola è illustrata nel calendario e nel registro. Lo spostamento di date, la cancellazione di eventi e la conservazione di presenze già registrate richiedono esempi separati; non applicare a queste operazioni le conclusioni dell’esempio di creazione.';
    const topupGap = 'In Utilizzo è disponibile il comando **Ricarica carnet**. Prima di usarlo controlla persona, conteggio e stato del pagamento. La ricarica crea una nuova assegnazione con un nuovo pagamento e copia i collegamenti ai corsi; questo caso richiede ancora una prova del flusso reale.';
    const sourcesGap = '<CardGroup cols={2}><Card title="Registro presenze e carnet" icon="clipboard-list" href="/tutorials/come-gestire-registro-presenze-carnet">Segui il tutorial per iscrivere l’atleta, assegnare il carnet, segnare e correggere una presenza.</Card><Card title="Carnet" icon="ticket" href="/docs/carnet">Consulta creazione, assegnazione e conteggio delle lezioni.</Card></CardGroup>';
    return [
        page(carnetDoc, "Cos'è un carnet?", 2, `Un carnet è un pacchetto di lezioni associato a un tesserato. Nell'esempio Giulia dispone di **5** lezioni a **50,00 €**.
L'assegnazione e il suo pagamento sono distinti dall'iscrizione al corso. Una presenza manuale consuma una lezione; la sua rimozione la restituisce.\n\n${steps(assign, checkin, remove)}`, [6, 7, 13, 14, 15]),
        page(carnetDoc, 'Come funziona un carnet?', 2, `Prepara un pacchetto, assegnalo al tesserato, collegalo al corso e controlla che il suo pagamento sia incassato prima di segnare la presenza.\n\n${steps(assign, link, pay)}`, [6, 7, 8, 9, 10]),
        page(carnetDoc, 'Logica di scalamento lezioni', 3, 'Con più carnet il sistema cerca quello attivo con il minor numero positivo di lezioni rimaste; controlla poi il suo pagamento. La scelta tra più carnet e il caso di carnet non pagato richiedono ancora una prova specifica. Non usare l’esempio con un solo carnet per dedurre il comportamento di tutti i pacchetti.', [], 'Multiple carnet priority and unpaid selected carnet need browser coverage'),
        page(carnetDoc, 'Carnet pubblici e privati', 3, steps(privacy), [5]),
        page(carnetDoc, 'Registro presenze automatico con carnet', 3, automaticGap, [], 'Automatic worker and athlete UI are not exercised'),
        page(carnetDoc, 'Blocco per assenza prevista', 4, automaticGap, [], 'Expected absence needs athlete role and actual worker execution'),
        page(carnetDoc, 'Creare e gestire un carnet', 2, steps(create, privacy) + '\n\n' + access, [3, 4, 5]),
        page(carnetDoc, 'Assegnare un carnet ad un atleta', 2, steps(assign, pay) + '\n\n' + access, [6, 7, 10]),
        page(carnetDoc, 'Assegnare un corso ad un carnet', 3, steps(enroll, link), [1, 2, 8, 9]),
        page(carnetDoc, 'Ricaricare un carnet', 2, topupGap, [], 'Top-up has reviewed implementation but no captured workflow'),
        page(carnetDoc, 'Riepilogo delle regole di funzionamento', 2, `| Operazione illustrata | Risultato nell'esempio |\n| --- | --- |\n| Assegna carnet | Cinque lezioni e nuovo pagamento di 50,00 € |\n| Collega corso | Collegamento senza un secondo pagamento |\n| Segna presente | Conteggio da 5/5 a 4/5 |\n| Rimuovi presenza | Conteggio da 4/5 a 5/5 |\n\n${image(14, 'Conteggio dopo la presenza')}${image(15, 'Conteggio dopo la correzione')}\n\n<Note>Scelta tra più carnet, esaurimento e presenze automatiche hanno ancora bisogno dei propri esempi verificati.</Note>`, [14, 15]),
        page(carnetDoc, 'Risorse correlate', 2, sourcesGap),
        page(attendanceDoc, 'Introduzione', 2, `Il registro raccoglie le presenze alle lezioni del corso. Prima iscrivi il tesserato, poi crea una lezione nel calendario e apri **Presenze**.\n\n${steps(calendar, register)}`, [11, 12]),
        page(attendanceDoc, 'Segnare le presenze manualmente', 2, steps(register, checkin) + '\n\n' + access, [12, 13, 14]),
        page(attendanceDoc, 'Presenze automatiche (Pro/Teams)', 2, automaticGap, [], 'Automatic attendance is outside this manual scenario'),
        page(attendanceDoc, 'Come funziona', 3, automaticGap, [], 'Actual automatic worker execution is unverified'),
        page(attendanceDoc, 'Segnalazione assenze da parte degli atleti', 3, automaticGap, [], 'Athlete absence UI has no captured evidence'),
        page(attendanceDoc, 'Attivare le presenze automatiche', 3, 'Il calendario della lezione e l’opzione **Segna Presenze Automaticamente** sono due impostazioni distinte. La pubblicazione del calendario, da sola, non dimostra che la registrazione automatica sia attiva. L’attivazione nelle impostazioni e il risultato del processo automatico richiedono un esempio dedicato.', [], 'Settings activation and real worker results need separate coverage'),
        page(attendanceDoc, 'Interazione con i carnet', 2, steps(pay, checkin, remove), [10, 13, 14, 15]),
        page(attendanceDoc, 'Scalamento alla presenza', 3, steps(checkin), [13, 14]),
        page(attendanceDoc, 'Restituzione alla rimozione', 3, steps(remove), [15]),
        page(attendanceDoc, 'Casi particolari con i carnet', 3, 'L’esempio usa un solo carnet pagato, non esaurito e collegato a un solo corso. Carnet non pagati, esauriti, disabilitati o concorrenti e presenze senza carnet richiedono prove separate. Se un’operazione è rifiutata, controlla pagamento e lezioni disponibili prima di riprovare.', [], 'Unpaid, exhausted, disabled, multiple and no-carnet variants are not captured'),
        page(attendanceDoc, 'Eliminare una lezione dal registro', 2, 'Il registro dispone di un pulsante rosso con il cestino accanto alla lezione. La conferma **Elimina** rimuove la data e le assenze segnalate. La cancellazione non esegue la correzione delle presenze: prima di usarla su una lezione con carnet, controlla gli utilizzi. Questa cancellazione richiede ancora un esempio dedicato.', [], 'Date deletion and usage implications need their own scenario'),
        page(attendanceDoc, 'Sincronizzazione con il calendario', 2, syncGap, [], 'Calendar modification and removal synchronization are not exercised'),
        page(attendanceDoc, 'Come funziona la sincronizzazione', 3, syncGap, [], 'Calendar edit/delete and previous attendance preservation need evidence'),
        page(attendanceDoc, 'Consultare lo storico presenze di un atleta', 2, 'Lo storico personale delle presenze richiede una verifica nella scheda del tesserato. La procedura di questa pagina consulta il registro del corso e il conteggio del carnet; non illustra ancora la scheda personale o le statistiche degli ultimi 30 giorni.', [], 'Member attendance history and statistics need a separate UI workflow'),
        page(attendanceDoc, 'Risorse correlate', 2, sourcesGap),
        page(tutorial, 'Indice', 1, `<Steps><Step title="Prepara corso e carnet">Iscrivi il tesserato al corso e assegna il carnet con il relativo pagamento.</Step><Step title="Registra una presenza">Crea la lezione e apri la scheda Presenze per segnare l'atleta.</Step><Step title="Controlla e correggi">Controlla il conteggio in Utilizzo e rimuovi la presenza per restituire la lezione.</Step></Steps>\n\n<Note>Le presenze automatiche, le assenze previste, la ricarica e la modifica manuale del saldo sono ancora da verificare con esempi dedicati.</Note>`),
        page(tutorial, 'Visualizzare il registro presenze', 1, steps(calendar, register), [11, 12]),
        page(tutorial, 'Segnare le presenze manualmente', 1, steps(register, checkin) + '\n\n' + access, [12, 13, 14]),
        page(tutorial, 'Presenze automatiche (Pro/Teams)', 1, automaticGap, [], 'Automatic worker is not exercised'),
        page(tutorial, 'Come funziona', 2, automaticGap, [], 'Automatic scheduling and expected absence handling are unverified'),
        page(tutorial, 'Come si attivano le presenze automatiche', 2, 'Pubblicare il calendario rende disponibili le date. L’opzione delle presenze automatiche è distinta e deve essere verificata nelle impostazioni: non dare per attiva l’automazione soltanto perché hai creato una lezione.', [], 'Calendar publishing alone must not imply automatic attendance activation'),
        page(tutorial, 'Come funzionano i carnet con le presenze', 1, steps(assign, link, pay, checkin, remove), [6, 7, 8, 9, 10, 13, 14, 15]),
        page(tutorial, 'Scalamento delle lezioni', 2, steps(pay, checkin), [10, 13, 14]),
        page(tutorial, 'Ripristino delle lezioni', 2, steps(remove), [15]),
        page(tutorial, 'Registro lezioni nel carnet', 2, 'L’utilizzo registrato per questa presenza conserva data, corso, titolo della lezione e riferimento alla data del registro. Il conteggio è consultabile nella colonna **Lezioni** di **Utilizzo del Carnet**. La rimozione della presenza elimina il relativo utilizzo e ripristina la lezione.\n\n' + steps(checkin, remove), [13, 14, 15]),
        page(tutorial, 'Gestire le lezioni perse e le correzioni', 1, steps(remove) + '\n\n<Warning>Questa correzione riguarda una presenza nella stessa lezione. Spostare o eliminare la data è un’operazione diversa, ancora da illustrare.</Warning>', [15]),
        page(tutorial, 'Correggere una presenza errata', 2, steps(remove), [15]),
        page(tutorial, 'Aggiungere una presenza mancante', 2, steps(register, checkin), [12, 13, 14]),
        page(tutorial, 'Consigli per la gestione dei carnet', 1, steps(assign, link, pay) + '\n\n' + access, [6, 7, 8, 9, 10]),
        page(tutorial, 'Controllare le lezioni rimanenti', 2, `Apri **Attività → Carnet**, scegli il pacchetto e la scheda **Utilizzo**.
Nella colonna **Lezioni** il primo numero indica le lezioni rimaste e il secondo il totale dell'assegnazione.
Nell'esempio **4/5** dopo la presenza torna a **5/5** quando la rimuovi.\n\n${image(14, 'Quattro lezioni rimaste dopo la presenza')}${image(15, 'Cinque lezioni dopo la correzione')}`, [14, 15]),
        page(tutorial, 'Assegnare un nuovo carnet', 2, steps(assign, pay) + '\n\n<Note>Assegna carnet crea una nuova assegnazione del pacchetto scelto. Il comando Ricarica carnet, con copia dei collegamenti esistenti, richiede ancora il proprio esempio.</Note>', [6, 7, 10]),
        page(tutorial, 'Modificare le lezioni rimanenti', 2, 'La correzione manuale del saldo richiede una prova nella scheda del tesserato. Il valore accettato deve essere compreso tra zero e il totale dell’assegnazione. Per correggere una partecipazione sbagliata usa invece l’interruttore della presenza, come illustrato sopra: il sistema aggiorna saldo e utilizzo insieme.', [], 'Manual balance editor and bounds need dedicated browser evidence'),
        page(tutorial, 'Conclusione', 1, 'Il percorso illustrato collega iscrizione al corso, assegnazione del carnet, incasso e presenza manuale. Controlla il saldo dopo ogni correzione. Le altre operazioni indicate nelle sezioni in attesa di verifica richiedono esempi aggiuntivi.'),
        page(courses, 'Corso con pagamento per lezione', 4, 'Se vuoi usare un carnet, configura prima la quota del corso secondo le esigenze dell’associazione. La normale assegnazione del carnet non rimuove una quota corso già generata. Nell’esempio manteniamo la quota di 120,00 € e aggiungiamo il carnet separato di 50,00 €.\n\n' + steps(enroll, assign, link), [1, 2, 6, 7, 8, 9]),
        page(courses, 'Calendario delle lezioni', 3, steps(calendar), [11]),
        page(courses, 'Gestire le lezioni', 4, 'Questa procedura illustra una lezione singola. Le lezioni periodiche e la modifica o cancellazione di lezioni esistenti richiedono esempi separati.\n\n' + steps(calendar), [11]),
        page(courses, 'Lezione singola', 4, steps(calendar), [11]),
        page(courses, 'Pubblicare il calendario', 4, steps(calendar, register), [11, 12]),
        page(courses, 'Aggiungere o rimuovere atleti', 3, steps(enroll) + '\n\n<Note>Il percorso illustrato aggiunge un tesserato già accettato dall’associazione. Rimozione dell’iscrizione al corso, accettazione di richieste spontanee e relativi effetti sui pagamenti richiedono procedure separate.</Note>\n\n' + access, [1, 2]),
    ];
}

const sourceSymbols = {
    'UI/src/components/Sidebar.svelte': '<span class="menu-text">Corsi e Abbonamenti</span>',
    'UI/src/routes.js': "'/course/carnet/list':",
    'UI/src/utils/Permissions.js': 'export const canPerformAction',
    'UI/src/routes/association/course/overview/OverviewCourse.svelte': "params.page === 'attendance'",
    'UI/src/routes/association/course/overview/components/CourseNavigationTab.svelte': "title: 'Presenze'",
    'UI/src/routes/association/course/overview/partials/course-subscriptions-list.svelte': 'aria-label="Aggiungi tesserato"',
    'UI/src/routes/association/course/overview/modals/AddEditCourseSubscriptionModal.svelte': 'async function create(formData)',
    'UI/src/routes/association/course/overview/components/Calendar.svelte': 'async function saveCalendarDirectly(updateEvents)',
    'UI/src/routes/association/course/overview/components/modals/AddCalendarEvent.svelte': 'id="form_add_calendar_event"',
    'UI/src/utils/eventCalendar.js': 'export function serializeCalendarEvent(event)',
    'UI/src/routes/association/course/overview/components/Registry.svelte': 'data-target="#attendance-day-',
    'UI/src/routes/association/course/overview/components/modals/MarkAttendees.svelte': 'async function updateData(idx, checked)',
    'UI/src/routes/association/course/carnet/CarnetList.svelte': "field: 'public'",
    'UI/src/routes/association/course/carnet/add/AddCarnet.svelte': 'async function createCarnet()',
    'UI/src/routes/association/course/carnet/add/sections/Section1.svelte': 'name="fee"',
    'UI/src/routes/association/course/carnet/add/sections/Section2.svelte': 'id="bkn_select2_athletes"',
    'UI/src/routes/association/course/carnet/detail/CarnetDetail.svelte': 'async function updateData()',
    'UI/src/routes/association/course/carnet/detail/sections/Info.svelte': 'bind:checked={info.public}',
    'UI/src/routes/association/course/carnet/detail/sections/Usage.svelte': 'function assignCarnet(',
    'UI/src/routes/association/course/carnet/detail/modals/AddCarnetModal.svelte': 'async function update(formData)',
    'UI/src/routes/accounting/payment/PaymentList.svelte': 'window.markAsPaid =',
    'BE/application/urls.py': "path(r'course/<str:uid>/attendees/<str:attendance_day_uid>/update'",
    'BE/application/permissions_registry.py': "'course/*/attendees/*/update'",
    'BE/application/views/course_views.py': 'class CourseSubscriptionViewSet(',
    'BE/application/views/carnet_views.py': 'def carnet_assign(request, uid, uid_subscription):',
    'BE/application/views/attendee_views.py': 'def attendees_update(request, uid, attendance_day_uid):',
    'BE/application/views/payment_views.py': 'def payment_approve(request, uid):',
    'BE/application/utils/subscriptions_utils.py': 'def add_course_subscription(',
    'BE/application/utils/attendance_utils.py': 'def map_unlinked_attendance(',
    'BE/application/serializers/courses_serializers.py': 'class CourseSubscriptionOverviewSerializer(',
    'BE/application/serializers/carnet_serializers.py': 'class CarnetAddSerializer(',
    'BE/application/models/courses_models.py': 'class CourseSubscription(models.Model):',
    'BE/application/models/carnet_models.py': 'class CarnetSubscription(models.Model):',
    'BE/application/models/attendee_models.py': 'class AttendanceDay(models.Model):',
    'BE/application/models/payment_models.py': 'class Payment(GroupModelMixin):',
    'BE/application/models/user_models.py': 'auto_mark_attendance =',
    'BE/application/tasks.py': 'def auto_mark_attendance():',
};

// These inspected canonical source digests are editorial inputs only. The
// existing attendance capture builder still validates its own real-run evidence.
export const attendanceCarnetReviewedSources = Object.freeze({
    "UI/src/components/Sidebar.svelte": "9f8344602ae269b5dbbf90dfd6f75e5154f3d5774ad544ac41105b74af2e333c",
    "UI/src/routes.js": "58bddc98548095f14f39fa3141277de8cd00e2133faf49f6761131b109d254b0",
    "UI/src/utils/Permissions.js": "100a24508b57b6c73a323ab4f128ee2c79aef3212a2cd0178172199d59e51854",
    "UI/src/routes/association/course/overview/OverviewCourse.svelte": "5aed27d46e9ce99c75452e3cd766db412daaaeca7ac2084a20581358195c06ce",
    "UI/src/routes/association/course/overview/components/CourseNavigationTab.svelte": "d93d905aada618ee7254ff78c57808f100a32dcdd426ecd64ddc42aba84e1bf3",
    "UI/src/routes/association/course/overview/partials/course-subscriptions-list.svelte": "061c496436d114b9ed587e8c9f5c8b83ab94a518a142e5118f0a911f3be6ea93",
    "UI/src/routes/association/course/overview/modals/AddEditCourseSubscriptionModal.svelte": "cdb4e69ab6a4b231385aecacdd8c427aa0ca03652c79e50bee154ee3626ea7d7",
    "UI/src/routes/association/course/overview/components/Calendar.svelte": "5cdbdd7e450b7ccbcc87eee2349be0b156e9bdb2b79f79f750da99656fb6224e",
    "UI/src/routes/association/course/overview/components/modals/AddCalendarEvent.svelte": "8731b27625a0804f17e0eb1960e1daaa609a54befd29c2a9277f056caedf6c57",
    "UI/src/utils/eventCalendar.js": "fb9858b1170a32edd2562e71f05059ecadecf59e6bddc2c830df09114aa9aa4e",
    "UI/src/routes/association/course/overview/components/Registry.svelte": "c6940274a3eba0b6cc14a28493622aeaa988b7c95c8b974aec3ee22c94b3ee11",
    "UI/src/routes/association/course/overview/components/modals/MarkAttendees.svelte": "2dc6d14c9f8ee7b5251af66b84488197472ca714a59d66c455e4c511c7cccd7c",
    "UI/src/routes/association/course/carnet/CarnetList.svelte": "2db1b30b6d9750f9a605e8cb5e150d35341f8e4fb64975c4f82bf3dc744fe8a7",
    "UI/src/routes/association/course/carnet/add/AddCarnet.svelte": "50d83cb2bb0b1c3d32001d41dbceaa959ac214681cf4263da14f32bab7232f74",
    "UI/src/routes/association/course/carnet/add/sections/Section1.svelte": "2b0e02708ea64a0074510839aa89981afa1b772f2fd87694cfa249b1ed61510d",
    "UI/src/routes/association/course/carnet/add/sections/Section2.svelte": "ff2cf863264b2cf0d5656ac2061f8407a5d18c9a0c314fdb75811f6674631965",
    "UI/src/routes/association/course/carnet/detail/CarnetDetail.svelte": "c6b9df096fda3c920fcabd52cbb53733072e6b6059637db47421755409395407",
    "UI/src/routes/association/course/carnet/detail/sections/Info.svelte": "6326b6ff03fc46c26d120fce5a0198c7b392b84d43d398d8a81805320f86c2f7",
    "UI/src/routes/association/course/carnet/detail/sections/Usage.svelte": "569092d44ead6627058eecaf5c0f7bb391dc47f16be54cde5808ca9769c4be4d",
    "UI/src/routes/association/course/carnet/detail/modals/AddCarnetModal.svelte": "00dcea9bfea001c1b12209d79d485448640826173e5bf33da8fc886c7e50857f",
    "UI/src/routes/accounting/payment/PaymentList.svelte": "34017cc9d3bb103ff10b326cf95876190ce6060886c018816bd85bf8bbaa64fe",
    "BE/application/urls.py": "b475e7ed8c26a891854c4021d7308aefb192d2a26df8b3ee916a33f88e7cc359",
    "BE/application/permissions_registry.py": "b8318ad63daa9452ac736b2152449b14c49f73bc1417622a4181b7c3ceb3b1fa",
    "BE/application/views/course_views.py": "5d32e168d9af6100c280b0d0043ca381742f7c2cc300e94f4edd1fd89cb9fc98",
    "BE/application/views/carnet_views.py": "b79144c75ca4b119d0c7c850f538f30fe8432447be88dc6c27c5a1cb8ad5fcd2",
    "BE/application/views/attendee_views.py": "ee9f49b1907855cd6f3fddd93aafdeffe561917a33d40257c434c3b25384edb7",
    "BE/application/views/payment_views.py": "4f45b99318afaa7189177f73cf82863f6f281dc8a2992b15dcf299b11f8e8c34",
    "BE/application/utils/subscriptions_utils.py": "ab922f87b8b1dd491bba41dd86eaabdefd1127c08bdaeb7be4f990657effd22a",
    "BE/application/utils/attendance_utils.py": "f86f4c8294a50d56c5fe5b26582810acec9fcd0cd5fbd192a4ff511d72bb0a22",
    "BE/application/serializers/courses_serializers.py": "45dc937b0da921f8466e4c21d22bedb8e74a52bd62b66f41f60e3c42b0320632",
    "BE/application/serializers/carnet_serializers.py": "50c7c38f09e9f60146c418c1816172dfb66310106a25faac6583fc2633146b7c",
    "BE/application/models/courses_models.py": "6a9cd0b923ab5b6a9dd120bcb7f81c1dac30914657fcd14b3fdb9e9c427d78c6",
    "BE/application/models/carnet_models.py": "3e3cb2cfb3d2be09cc5db6b97b43e2a2f391d91ccb61dfb38c99bf0ec7913713",
    "BE/application/models/attendee_models.py": "5f7e748de06d17bd2d8eb1bbfa4c9d69eb9d3af6d6ec63bfd0e919b3af5f58f4",
    "BE/application/models/payment_models.py": "9dbcab8698adbde447a9c4f6eb52143409cf197fde3ce90d0aa8da56a1f8ebda",
    "BE/application/models/user_models.py": "3834a5655391ca750830d03175d7f663276697e50ab3fcfffcfd9ad45b7b931c",
    "BE/application/tasks.py": "fa819d44d7013b60455306b01d1e9a0f4a33e3f6e8759754316ff3cf937e6e8a"
});

// Each descriptor and contract list is freshly allocated. Draft discovery never
// calls attendanceCarnetPages and cannot mark a procedure or screenshot verified.
export function attendanceCarnetEditorialContracts() {
    return attendanceCarnetDraftPages().map(page => ({
        path: page.path, id: page.id, title: page.title, status: 'pending',
        reason: page.pendingReason || 'Richiede la prova browser reale di questa sezione.',
        recipe_module: 'docs/manuale/attendance-carnet-recipes.mjs', verified: false,
        source_contracts: attendanceCarnetSources.map(path => ({
            path, symbol: sourceSymbols[path], length: path.endsWith('attendee_views.py') ? 135 : 75,
            reviewed_sha256: attendanceCarnetReviewedSources[path],
        })),
    }));
}

export function attendanceCarnetPages(id, report, source) {
    if (!attendanceCarnetCaptureSpecs[id]) return [];
    if (report.status !== 'passed' || report.backend !== 'real' || report.fixture_version !== 8 || report.fixture_profile !== 'baseline')
        throw new Error('Attendance/carnet generation requires passed real-backend fixture 7 baseline evidence');
    const [prefix, checkpoints] = attendanceCarnetCaptureSpecs[id];
    if (report.screenshots?.length !== checkpoints.length || report.screenshots.some((capture, index) =>
        capture.path !== prefix + (index + 1) + '.png' || capture.checkpoint !== checkpoints[index]))
        throw new Error('Attendance/carnet checkpoints differ from reviewed steps');
    if (Object.entries(attendanceCarnetExpectedOutcome).some(([key, value]) => report.attendance_carnet_workflow?.[key] !== value))
        throw new Error('Attendance/carnet persisted outcomes differ from reviewed workflow');
    if (attendanceCarnetSources.some(relative => !/^[a-f0-9]{64}$/.test(report.source_hashes?.[relative] || '')))
        throw new Error('Attendance/carnet requires every captured implementation hash');
    const evidence = attendanceCarnetSources.map(relative => source(report, relative, sourceSymbols[relative],
        relative.endsWith('attendee_views.py') ? 135 : 75));
    return attendanceCarnetDraftPages().map(({imageNumbers, pendingReason, ...page}) => ({...page,
        body: page.body.replace(pendingProof, ''), evidence,
        screenshots: imageNumbers.map(number => report.screenshots[number - 1]),
        ...(pendingReason ? {status: 'pending', reason: pendingReason} : {})}));
}
