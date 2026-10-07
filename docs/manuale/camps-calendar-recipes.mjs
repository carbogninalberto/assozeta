import {campsCalendarSourceContracts} from '../../selfhost/tests/browser/manuale/camps-calendar-sources.mjs';

export const campsCalendarCaptureSpecs = {
    'camps-calendar-manage': ['images/camp-calendario/gestione/', [
        'camp-list-empty-and-create-control', 'camp-title-description-before-create',
        'camp-created-and-public-link-visible-after-reload', 'period-selected-range-fee-capacity-before-create',
        'created-period-and-dates-persist-after-reload', 'period-updated-dates-fee-and-unlimited-capacity-before-save',
        'updated-period-persists-after-reload', 'service-title-fee-income-category-before-create',
        'service-created-and-income-category-persists-after-reload', 'service-edit-title-cost-and-delete-control',
        'edited-service-persists-after-reload', 'deleted-service-absent-after-reload',
        'reader-consults-period-without-create-or-save-controls', 'general-calendar-default-day-list-and-toolbar',
        'general-calendar-month-view-and-navigation', 'general-calendar-week-view', 'general-calendar-day-view',
        'general-calendar-week-list-view', 'global-event-name-dates-description-without-course-or-reminder',
        'global-event-persists-after-reload', 'global-event-edit-title-description-and-fixed-dates',
        'updated-global-event-persists-after-reload', 'new-event-with-course-selected-before-save',
        'global-calendar-shows-persisted-course-lesson-and-global-event',
        'course-calendar-contains-lesson-created-from-general-calendar',
        'course-share-modal-direct-link-embed-whatsapp-and-email-controls',
        'reader-sees-global-event-with-disabled-save-and-delete',
        'deleted-global-event-absent-course-lesson-preserved-after-reload',
    ]],
};
export const campsCalendarExpectedOutcomes = {
    'camps-calendar-manage': Object.freeze({camp_title: 'Ritiro Aurora 2026', camp_persisted_after_reload: true,
        period_initial_start: '2026-09-28', period_initial_end: '2026-09-29',
        period_updated_start: '2026-09-29', period_updated_end: '2026-09-30', period_fee: '90.00',
        period_unlimited_capacity: true, period_persisted_after_reload: true,
        service_initial_fee: '15.00', service_updated_fee: '18.00', service_category_preserved: true,
        service_persisted_after_reload: true, service_deleted_after_reload: true, no_camp_enrollments_or_payments: true,
        reader_camp_update_status: 403, global_event_created_after_reload: true,
        global_event_updated_after_reload: true, global_event_dates_preserved: true,
        global_event_deleted_after_reload: true, course_lesson_persisted_after_reload: true,
        course_calendar_published: true, course_lesson_survived_global_deletion: true,
        views_exercised: 'month,week,day,listDay,listWeek', anonymous_course_calendar_status: 200,
        share_panel_exercised: true, ics_download_contains_course_lesson: true, ics_download_excludes_global_event: true,
        reader_global_update_status: 403, reminders_enabled: false,
        camp_registration_exercised: false, attendance_write_exercised: false, print_dialog_exercised: false,
        external_share_dispatch_exercised: false, google_link_export_revoke_exercised: false}),
};
const pending = '<Note>Bozza in attesa di prova: questa sezione deve essere confermata dal flusso reale prima di essere considerata verificata.</Note>\n\n';
const localGap = 'Procedura locale non eseguita: occorre una prova dedicata con salvataggio e rilettura dei dati.';
const googleGap = 'Collegamento OAuth, gestione dei token, esportazione e revoca presso Google richiedono una verifica esterna; nessun account è stato collegato.';
const externalGap = 'Il controllo locale è visibile, ma l’operazione nel servizio esterno non è stata eseguita.';

export function campsCalendarDraftPages() {
    const image = (number, label) => `<Frame>![${label}](/images/camp-calendario/gestione/${number}.png)</Frame>`;
    const step = (title, body) => `<Step title="${title}">\n${body}\n</Step>`;
    const steps = (...items) => `<Steps>\n${items.join('\n')}\n</Steps>`;
    const page = (path, title, id, level, body, imageNumbers = [], intent = '', targetStatus = 'verified', targetReason = '') =>
        ({path, title, id, body: '#'.repeat(level) + ' ' + title + '\n\n' + pending + body,
            imageNumbers, intent, targetStatus, targetReason, status: 'pending', reason: 'In attesa della prova reale completa.',
            evidence: [], screenshots: []});
    const campPath = 'docs/camp-e-ritiri.mdx', calendarPath = 'docs/calendario.mdx', faqPath = 'faq/come-collegare-google-calendar.mdx';
    const campOpen = step('Apri Camp e Ritiri', `Dal menu laterale apri **Corsi → Camp e Ritiri**.
Nell'elenco trovi **Titolo** e **Descrizione**; premi il titolo di un camp per aprirne la gestione.
Nell'esempio l'associazione Aurora parte da un elenco vuoto.
${image(1, 'Elenco Camp e Ritiri e pulsante di creazione')}`);
    const campCreate = step('Compila e crea il camp', `Premi **Camp e Ritiri** in alto per aprire **Crea nuovo Camp o Ritiro**.
Compila **Titolo** e **Descrizione**, entrambi richiesti. Nell'esempio usiamo **Ritiro Aurora 2026** e descriviamo le due giornate di allenamento.
${image(2, 'Titolo e descrizione del nuovo ritiro prima della creazione')}
Premi **Crea**. Dopo la conferma si apre la gestione del camp, con **Settimane o periodi**, il collegamento di iscrizione e il codice QR.
Ricarica la pagina e controlla che il titolo sia ancora presente.
${image(3, 'Camp creato e pagina di gestione dopo il ricaricamento')}`);
    const periodCreate = step('Aggiungi un periodo con le date', `Nella gestione del camp, sotto **Settimane o periodi**, premi **Periodo**.
La finestra **Aggiungi Settimana/Periodo** mostra un unico campo **Periodo**: aprilo, scegli il giorno iniziale e quello finale nel calendario, poi premi **Applica**.
Nell'esempio selezioniamo **28/09/2026–29/09/2026**. Controlla l'intervallo riportato nel campo prima di continuare.
Inserisci **Quota di partecipazione**: **80,00**, **Numero massimo di partecipanti**: **20**, **Titolo**: **Due giornate Aurora** e una **Descrizione**.
${image(4, 'Intervallo, quota e numero massimo del periodo prima di Crea')}
Premi **Crea**, poi ricarica la gestione: la scheda del periodo deve mostrare il titolo e le date salvate.
${image(5, 'Periodo aggiunto e presente dopo il ricaricamento')}`);
    const periodUpdate = step('Modifica e salva il periodo', `Premi **Dettagli** sulla scheda del periodo. Nella pagina puoi modificare l'intervallo, la quota, il numero massimo, il titolo e la descrizione.
Per cambiare le date, apri **Periodo**, seleziona il nuovo inizio e la nuova fine, poi premi **Applica**.
Nell'esempio passiamo al **29/09/2026–30/09/2026**, cambiamo la quota in **90,00** e il titolo in **Due giornate aggiornate**; lasciamo vuoto **Numero massimo di partecipanti**.
${image(6, 'Nuove date, quota e limite vuoto prima del salvataggio')}
Premi **Salva** in alto. Ricarica la pagina e ricontrolla l'intervallo e i valori: l'esempio conserva **90,00** e il limite vuoto.
${image(7, 'Periodo aggiornato e conservato dopo il ricaricamento')}`);
    const campReadOnly = `<Note>Un collaboratore con il solo permesso di lettura può consultare camp e periodi; non trova i pulsanti **Camp e Ritiri**, **Periodo**, **Servizio** e **Salva** usati in questa procedura.
I campi del periodo possono comunque apparire compilabili: questo non concede il permesso di salvare. Chiedi al responsabile l'autorizzazione necessaria.
${image(13, 'Periodo consultato dal collaboratore senza i comandi di creazione e salvataggio')}</Note>`;
    const serviceCreate = step('Aggiungi un servizio al periodo', `Apri **Dettagli** del periodo, scorri a **Servizi della settimana** e premi **Servizio**.
Nella finestra **Aggiungi Servizio** compila **Costo servizio**, **Causale**, **Titolo** e **Descrizione**.
Nell'esempio usiamo **15,00**, la causale **Quote e attività associative** e il titolo **Pranzo**.
Il menu propone le causali di entrata; se quella necessaria manca, il collegamento **qui** sotto il menu apre la gestione delle causali.
${image(8, 'Servizio Pranzo con costo e causale di entrata prima di Crea')}
Premi **Crea**. Ricarica il periodo: la scheda del servizio mostra nome, costo e causale salvati.
${image(9, 'Servizio aggiunto e causale conservata dopo il ricaricamento')}`);
    const serviceUpdate = step('Aggiorna il servizio', `Nella scheda **Pranzo**, premi **Modifica**. La finestra mantiene il titolo **Aggiungi Servizio**, ma propone **Salva** e **Elimina**.
Nell'esempio cambia **Titolo** in **Pranzo completo** e **Costo servizio** in **18,00**, poi premi **Salva**.
${image(10, 'Modifica del servizio con i comandi Salva ed Elimina')}
Ricarica il periodo e verifica il nuovo nome e il nuovo costo nella scheda.
${image(11, 'Nome e costo del servizio aggiornati dopo il ricaricamento')}`);
    const serviceDelete = step('Elimina un servizio non utilizzato', `Apri di nuovo **Modifica** sulla scheda del servizio e premi **Elimina**.
In questa finestra l'eliminazione parte direttamente dal pulsante: controlla prima di procedere.
Ricarica il periodo e verifica che la scheda sia scomparsa.
${image(12, 'Servizio eliminato e assente dopo il ricaricamento')}
<Warning>L'esempio elimina un servizio prima di iscrivere persone. La gestione di servizi già collegati a iscrizioni o pagamenti richiede una verifica specifica; non usare questa prova per dedurne gli effetti sulle quote già emesse.</Warning>`);
    const calendarOpen = step('Apri il calendario generale', `Dal menu laterale apri **Calendario**. Il titolo della pagina è **Eventi e Promemoria**.
La vista iniziale è **lista giorno**; in alto trovi **Nuovo evento**, **Stampa**, **Esporta** e i comandi delle viste.
${image(14, 'Calendario generale nella vista lista giorno e barra dei comandi')}`);
    const globalCreate = step('Crea un evento senza corso', `Premi **Nuovo evento**, utile anche quando la lista del giorno è vuota.
Nella finestra **Crea evento** lascia disattivato **Tutto il giorno**, scegli **Inizio** e **Fine**, compila **Nome evento** e **Descrizione**.
Nell'esempio: **Riunione organizzativa Aurora**, il **30/09/2026** dalle **10:00** alle **11:00**.
Lascia **Corso** vuoto per un appuntamento generale. **Colore** distingue l'evento; **Istruttore** consente di selezionare le persone quando servono.
Nell'esempio il promemoria resta disattivato.
${image(19, 'Evento generale con nome, orari e descrizione prima del salvataggio')}
Premi **Salva**, ricarica la pagina e controlla che l'evento compaia nel giorno scelto.
${image(20, 'Evento generale salvato e presente dopo il ricaricamento')}`);
    const courseCreate = step('Collega una lezione a un corso', `Per una lezione ripeti **Nuovo evento**, compila nome e orari, poi seleziona il corso nel menu **Corso**.
L'esempio crea **Lezione Aurora dal calendario** per **Ginnastica per tutti**.
${image(23, 'Nuovo evento con il corso Ginnastica per tutti selezionato')}
Premi **Salva** e ricarica. La lezione compare nel calendario generale e nel **Calendario delle lezioni** del corso.
${image(24, 'Calendario generale con la lezione del corso e l’appuntamento generale')}
${image(25, 'Calendario del corso con la stessa lezione salvata')}
<Note>Per creare o modificare una lezione collegata a un corso occorre il permesso di aggiornare i corsi. Gli eventi senza corso usano invece i permessi degli eventi.</Note>`);
    const globalUpdate = step('Modifica il contenuto dell’evento', `Premi l'evento **Riunione organizzativa Aurora** nel calendario. Si apre **Modifica evento**.
Nell'esempio cambia **Nome evento** in **Riunione Aurora aggiornata** e aggiorna la **Descrizione**, poi premi **Salva**.
In questa finestra **Inizio**, **Fine** e **Corso** sono disattivati durante la modifica.
${image(21, 'Modifica del nome e della descrizione con le date disattivate')}
Ricarica il calendario e verifica il nuovo nome: gli orari iniziali rimangono gli stessi.
${image(22, 'Evento aggiornato e presente dopo il ricaricamento')}`);
    const globalDelete = step('Elimina l’appuntamento scelto', `Apri l'evento, premi **Elimina** e controlla la conferma **Vuoi eliminare l'evento a calendario?**.
Premi **Elimina** nella conferma per procedere, oppure **Annulla** per conservarlo.
Ricarica la pagina e verifica che l'appuntamento sia assente. Nell'esempio la lezione del corso rimane presente.
${image(28, 'Evento generale eliminato e lezione del corso ancora presente')}
<Warning>La conferma elimina l'evento selezionato. Controlla il nome prima di procedere.</Warning>`);
    const calendarReadOnly = `<Note>Il collaboratore in sola lettura può aprire gli eventi, ma non trova **Nuovo evento** e ha **Salva** ed **Elimina** disattivati.
Per vedere anche gli appuntamenti generali servono sia la lettura del calendario sia la lettura degli eventi.
${image(27, 'Evento aperto dal collaboratore con salvataggio ed eliminazione disattivati')}</Note>`;
    const share = step('Apri le opzioni di condivisione del corso', `Apri **Corsi**, scegli **Ginnastica per tutti** e raggiungi **Calendario delle lezioni**.
Premi **Condividi**. La finestra **Condividi calendario** mostra il collegamento del singolo corso e **Codice HTML da incorporare nel tuo sito**.
Sono presenti anche **Whatsapp**, **Invia Email** e **Chiudi**.
${image(26, 'Finestra Condividi calendario con collegamento e codice per il sito')}`);
    const googleLocal = `La funzione locale propone il collegamento in **Profilo → Integrazioni**, con **Connetti a Google** quando l'account non è collegato.
Per un account collegato compare **Disconnetti**. La connessione richiede la configurazione Google dell'installazione e l'autorizzazione dell'account interessato.
<Warning>Questa guida non conferma un collegamento Google: non sono state eseguite autorizzazione, gestione dei token, creazione di calendari o revoca presso Google. Se il servizio mostra un avviso di sicurezza o un'app non verificata, interrompi e verifica la configurazione con il responsabile dell'installazione.</Warning>`;
    const googleSync = `Nel **Calendario delle lezioni** del corso il comando è **Sincronizza con Google**; un corso già collegato può mostrare **Sincronizzato** oppure **Sincronizzato in passato**.
Il comando chiede una conferma e usa il collegamento Google dell'account.
<Note>La presenza del pulsante non dimostra che le lezioni siano arrivate su Google. Una prova completa deve controllare l'esito nel servizio esterno, comprese le eventuali modifiche successive e gli errori. Questa verifica non è stata eseguita.</Note>`;
    return [
        page(campPath, "Cos'è un Camp o Ritiro", 'cos-e-un-camp-o-ritiro', 2,
            `Un camp raccoglie uno o più **periodi**, con date, quota e un eventuale numero massimo di partecipanti. Ogni periodo può avere **servizi** con costo e causale propri.
Puoi usarlo per un ritiro di due giorni, per settimane di un camp estivo o per uno stage. Il camp contiene titolo e descrizione; le date e le quote si impostano nei periodi.
Nell'esempio creiamo **Ritiro Aurora 2026**, aggiungiamo un periodo e ne aggiorniamo date e quota. La creazione del camp e dei servizi, da sola, non iscrive persone e non genera pagamenti.\n\n` + steps(campOpen), [1], 'camps.read'),
        page(campPath, 'Creare un Camp o Ritiro', 'creare-un-camp-o-ritiro', 2, steps(campOpen, campCreate), [1, 2, 3], 'camps.create'),
        page(campPath, 'Gestire i periodi', 'gestire-i-periodi', 2,
            `Le schede sotto **Settimane o periodi** mostrano titolo, date e quota. Il pulsante **Dettagli** apre la modifica del periodo e i suoi servizi.
Un **Numero massimo di partecipanti** vuoto viene conservato senza limite numerico impostato. L'esempio verifica il valore salvato; non prova l'iscrizione oltre un limite né la disponibilità dei posti.\n\n` + steps(periodUpdate) + '\n\n' + campReadOnly, [6, 7, 13], 'camps.periods.update'),
        page(campPath, 'Aggiungere un periodo', 'aggiungere-un-periodo', 3, steps(periodCreate), [4, 5], 'camps.periods.create'),
        page(campPath, 'Modificare un periodo', 'modificare-un-periodo', 3, steps(periodUpdate) + '\n\n' + campReadOnly, [6, 7, 13], 'camps.periods.update'),
        page(campPath, 'Gestire i servizi', 'gestire-i-servizi', 2,
            `I servizi appartengono al singolo periodo: ad esempio pranzo, trasporto o pernottamento. Il costo del servizio è distinto dalla quota del periodo e la scheda mostra la causale scelta.
Apri **Dettagli** del periodo e scorri a **Servizi della settimana** per consultarli. Nell'esempio creiamo e modifichiamo un pranzo prima di raccogliere iscrizioni.\n\n` + steps(serviceCreate), [8, 9], 'camps.services.create'),
        page(campPath, 'Aggiungere un servizio', 'aggiungere-un-servizio', 3, steps(serviceCreate), [8, 9], 'camps.services.create'),
        page(campPath, 'Modificare o eliminare un servizio', 'modificare-o-eliminare-un-servizio', 3, steps(serviceUpdate, serviceDelete), [10, 11, 12], 'camps.services.update'),
        page(campPath, 'Iscrivere gli atleti', 'iscrivere-gli-atleti', 2,
            `L'iscrizione al camp collega una persona già iscritta all'associazione ai periodi e ai servizi selezionati. Prima di procedere, controlla di avere completato date, quote e causali.
La pagina di gestione mostra la sezione **Iscritti** e il pulsante **Atleta**. Il flusso di iscrizione e i relativi pagamenti devono ancora essere provati: la verifica di questo esempio si ferma alla preparazione del camp.`, [], 'camps.enrollment.create', 'pending', localGap),
        page(campPath, 'Aggiungere un iscritto manualmente', 'aggiungere-un-iscritto-manualmente', 3,
            steps(step('Scegli la persona', `Nella gestione del camp, sotto **Iscritti**, premi **Atleta**. La finestra **Aggiungi Iscritto** propone le iscrizioni dell'associazione; riconosci la persona dal nome prima di selezionarla.`),
                step('Controlla periodi e servizi', `Seleziona i periodi richiesti e, nelle sezioni che si aprono, i servizi aggiuntivi. Leggi date, descrizione e importi prima di confermare.`),
                step('Verifica prima di usare la procedura', `Il pulsante **Iscrivi** avvia il salvataggio. Questa prova non lo preme: prima dell'uso occorre verificare una vera iscrizione, la rilettura nell'elenco e il pagamento generato. Non considerare il solo modulo una conferma dell'iscrizione.`)), [], 'camps.enrollment.create', 'pending', localGap),
        page(campPath, 'Modulo di iscrizione pubblico', 'modulo-di-iscrizione-pubblico', 3,
            `La pagina del camp mostra **Condividi il link per raccogliere le iscrizioni al Camp/Ritiro**, un collegamento e il codice QR.
Il collegamento porta al modulo del camp; il pulsante con gli appunti serve a copiarlo.
${image(3, 'Collegamento e codice QR visibili nella gestione del camp')}
<Note>Questa prova controlla la presenza del collegamento, ma non completa un'iscrizione pubblica, il riconoscimento della persona o i pagamenti successivi. La condivisione con atleti reali richiede prima quella verifica.</Note>`, [3], 'camps.enrollment.public', 'pending', localGap),
        page(campPath, "Modificare un'iscrizione", 'modificare-un-iscrizione', 3,
            `Nell'elenco **Iscritti**, la matita apre la scelta dei periodi e dei servizi della persona. I periodi con pagamento già saldato vengono presentati come non modificabili.
<Warning>Il flusso di modifica non è stato eseguito. Deve essere verificato sia con quote ancora aperte sia con quote saldate, controllando anche i pagamenti conservati o rigenerati. Non rimuovere o modificare un pagamento per aggirare il blocco senza averne valutato gli effetti contabili.</Warning>`, [], 'camps.enrollment.update', 'pending', localGap),
        page(campPath, "Rimuovere un'iscrizione", 'rimuovere-un-iscrizione', 3,
            `La riga dell'iscritto contiene il cestino e una richiesta di conferma. Prima di confermare identifica la persona e controlla i periodi e i pagamenti collegati.
<Warning>La rimozione di una vera iscrizione non è stata provata. I periodi non saldati e quelli già pagati seguono regole diverse: occorre controllare il risultato nell'elenco del camp e in Contabilità prima di considerare completa questa procedura.</Warning>`, [], 'camps.enrollment.delete', 'pending', localGap),
        page(campPath, 'Gestire i pagamenti', 'gestire-i-pagamenti', 2,
            `Le quote del camp si consultano in **Contabilità → Pagamenti**. La selezione dei periodi e dei servizi determina l'importo da controllare.
L'attuale generazione raggruppa i periodi non pagati della stessa iscrizione in **un pagamento complessivo**, includendo le quote dei periodi e i costi dei servizi scelti: non aspettarti necessariamente un pagamento distinto per periodo.
<Note>La creazione, la rigenerazione e il saldo delle quote camp non sono stati eseguiti in questa prova. Prima di usare il flusso, verifica importo, causali, collegamenti ai periodi e comportamento delle quote già saldate.</Note>`, [], 'camps.payments.read', 'pending', localGap),
        page(campPath, 'Flusso di lavoro consigliato', 'flusso-di-lavoro-consigliato', 2,
            steps(step('Prepara il camp', 'Crea titolo e descrizione da **Corsi → Camp e Ritiri**, poi ricontrolla la pagina dopo il salvataggio.'),
                step('Completa periodi e servizi', 'Per ciascun periodo scegli date, quota e limite, poi aggiungi i servizi con la causale corretta. Verifica i valori dopo il ricaricamento.'),
                step('Verifica iscrizione e quota', 'Prima di distribuire il collegamento prova un’iscrizione completa e controlla il relativo pagamento. Questi passaggi non sono compresi nella prova descritta qui.'),
                step('Condividi dopo il controllo', 'Solo dopo aver verificato il modulo e gli importi condividi il collegamento o il codice QR con i partecipanti.')), [], 'camps.manage', 'pending', localGap),
        page(calendarPath, 'Il calendario generale', 'il-calendario-generale', 2,
            `Il calendario generale riunisce le lezioni dei corsi e gli eventi senza corso che l'account è autorizzato a vedere. L'esempio distingue una riunione organizzativa da una lezione di **Ginnastica per tutti**.
Se hai un accesso limitato, un elenco vuoto non dimostra che l'associazione non abbia altri appuntamenti.\n\n` + steps(calendarOpen) + '\n\n' + calendarReadOnly, [14, 27], 'calendar.read'),
        page(calendarPath, 'Modalità di visualizzazione', 'modalita-di-visualizzazione', 2,
            `Sopra il calendario trovi **mese**, **settimana**, **giorno**, **lista giorno** e **lista settimana**.
Scegli la griglia per leggere la distribuzione delle attività, oppure una lista per scorrere gli appuntamenti. Cambiare vista non modifica gli eventi salvati.
${image(14, 'Comandi delle viste e lista del giorno iniziale')}`, [14], 'calendar.read'),
        page(calendarPath, 'Vista Mese', 'vista-mese', 3,
            `Premi **mese** per mostrare la griglia mensile. Le celle contengono gli eventi delle rispettive date; quando lo spazio non basta, il calendario propone un riepilogo per quelli aggiuntivi.
Il numero visibile dipende dallo spazio disponibile, quindi non fare affidamento su un limite fisso di tre eventi.
${image(15, 'Vista mensile del calendario generale')}`, [15], 'calendar.read'),
        page(calendarPath, 'Vista Settimana', 'vista-settimana', 3,
            `Premi **settimana** per vedere le giornate affiancate, suddivise per orario. Le attività **Tutto il giorno** occupano l'area dedicata sopra le fasce orarie.
Usa questa vista per confrontare gli orari; per leggere i dettagli di un appuntamento premi la sua voce.
${image(16, 'Vista settimanale per fasce orarie')}`, [16], 'calendar.read'),
        page(calendarPath, 'Vista Giorno', 'vista-giorno', 3,
            `Premi **giorno** per vedere le fasce orarie della sola giornata selezionata. Il titolo sopra la griglia indica la data consultata.
Controlla la data prima di creare un evento: puoi sempre correggerla nella finestra di creazione.
${image(17, 'Vista del giorno con le fasce orarie')}`, [17], 'calendar.read'),
        page(calendarPath, 'Vista Lista', 'vista-lista', 3,
            `**lista giorno** è la vista iniziale e raccoglie gli appuntamenti della giornata. **lista settimana** amplia l'elenco alla settimana selezionata, mantenendo le attività raggruppate per data.
Le viste lista sono adatte alla consultazione rapida; anche da qui puoi aprire un evento premendo il nome.
${image(14, 'Lista giorno iniziale')}
${image(18, 'Lista settimana del calendario generale')}`, [14, 18], 'calendar.read'),
        page(calendarPath, 'Navigare nel calendario', 'navigare-nel-calendario', 2,
            `Usa le frecce in alto a sinistra per passare al periodo precedente o successivo della vista attiva. **Oggi** torna alla data corrente.
Prima di creare un evento controlla il titolo del periodo visualizzato.
${image(15, 'Frecce, Oggi e periodo consultato nella vista mese')}
<Note>Premere una data nella griglia, con il permesso necessario, apre la creazione di un evento. Per passare alla vista del giorno usa **giorno**: la data non è un comando che cambia automaticamente vista.</Note>`, [15], 'calendar.read'),
        page(calendarPath, 'Creare un evento o promemoria', 'creare-un-evento-o-promemoria', 2,
            steps(calendarOpen, globalCreate, courseCreate) + '\n\n<Note>Il riquadro **Promemoria** propone la campanella, un numero e **Minuti**, **Ore** o **Giorni**. Questa prova mantiene il promemoria spento: il salvataggio di un evento non dimostra la consegna di un avviso. L’attivazione e l’invio richiedono una verifica del servizio dell’installazione.</Note>',
            [14, 19, 20, 23, 24, 25], 'calendar.events.create'),
        page(calendarPath, 'Modificare o eliminare un evento', 'modificare-o-eliminare-un-evento', 2,
            steps(globalUpdate, globalDelete) + '\n\n' + calendarReadOnly, [21, 22, 28, 27], 'calendar.events.update'),
        page(calendarPath, 'Gestire le presenze dal calendario', 'gestire-le-presenze-dal-calendario', 2,
            `Quando l'evento è una lezione e ha un registro collegato, la finestra può mostrare **Gestisci Presenze**. Gli appuntamenti senza corso non hanno le presenze degli atleti.
<Note>Questa procedura non apre né salva un registro presenze dal calendario generale. Serve una prova dedicata per verificare la lezione corretta, i partecipanti, i permessi e la conservazione delle presenze.</Note>`, [], 'calendar.attendance.update', 'pending', localGap),
        page(calendarPath, 'Stampare il calendario', 'stampare-il-calendario', 2,
            `Il pulsante **Stampa** è in alto nella pagina **Eventi e Promemoria** e apre la funzione di stampa del browser per la vista corrente.
Scegli prima **mese** o **settimana**, poi controlla l'anteprima prima di stampare o salvare un PDF.
${image(14, 'Pulsante Stampa nel calendario generale')}
<Note>La finestra di stampa e il documento prodotto non sono stati verificati in questa prova. Controlla tagli, orientamento e numero di pagine nella tua anteprima.</Note>`, [14], 'calendar.print', 'pending', localGap),
        page(calendarPath, 'Esportare il calendario', 'esportare-il-calendario', 2,
            steps(step('Scarica il file delle lezioni', `Nella pagina **Eventi e Promemoria**, premi **Esporta**. Il browser scarica un file **.ics**, con la data e il nome **calendario_completo**.
L'esempio contiene **Lezione Aurora dal calendario**, già salvata nel corso.
${image(24, 'Calendario con una lezione esportabile e un evento generale')}`)) + '\n\n<Warning>In questa versione il file include le lezioni dei corsi dell’associazione, ma **non gli eventi generali né i loro promemoria**. Anche se il nome contiene “completo”, controlla questo limite prima di usare il file come riepilogo di tutta l’attività.</Warning>\n\n<Note>Questa procedura riguarda il download locale. L’importazione del file in Google Calendar, Outlook o altre applicazioni deve essere controllata nell’applicazione scelta e non stabilisce una sincronizzazione continua.</Note>', [24], 'calendar.export'),
        page(calendarPath, 'Condivisione del calendario con gli atleti', 'condivisione-del-calendario-con-gli-atleti', 2,
            steps(share) + '\n\n<Info>Il collegamento riguarda il singolo corso. Gli appuntamenti generali dell’associazione non vengono aggiunti a quel calendario. Una nuova apertura legge le lezioni salvate; non è stata provata una notifica immediata a chi ha già la pagina aperta.</Info>', [26], 'calendar.share.read'),
        page(calendarPath, 'Link diretto', 'link-diretto', 3,
            steps(share, step('Copia il collegamento del corso', `Premi l'icona con i fogli accanto al collegamento. Il destinatario può consultare il calendario del corso senza autenticarsi.
Nell'esempio la lettura senza accesso restituisce la stessa lezione salvata nel calendario del corso. Condividi il collegamento soltanto con le persone a cui vuoi mostrare queste lezioni.`)), [26], 'calendar.share.read'),
        page(calendarPath, 'Condivisione tramite WhatsApp', 'condivisione-tramite-whatsapp', 3,
            `Nella finestra **Condividi calendario**, il pulsante attuale è **Whatsapp**. Prepara l'apertura del servizio con un testo che contiene il collegamento del corso.
${image(26, 'Comando Whatsapp nella finestra di condivisione')}
<Note>Il pulsante non è stato premuto. La scelta del destinatario e l'invio nel servizio esterno richiedono una prova separata; l'apertura di un collegamento non equivale alla consegna di un messaggio.</Note>`, [26], 'calendar.share.whatsapp', 'needs_external_verification', externalGap),
        page(calendarPath, 'Condivisione tramite Email', 'condivisione-tramite-email', 3,
            `La finestra propone **Invia Email**, che apre il programma di posta con un testo contenente il collegamento del corso. Controlla e sostituisci il destinatario prima dell'invio.
${image(26, 'Comando Invia Email nella finestra di condivisione')}
<Note>Nessuna email è stata preparata nel programma esterno o inviata. Il comando locale non garantisce la consegna al destinatario: questa parte resta da verificare con il servizio di posta usato.</Note>`, [26], 'calendar.share.email', 'needs_external_verification', externalGap),
        page(calendarPath, 'Incorporare il calendario nel tuo sito web', 'incorporare-il-calendario-nel-tuo-sito-web', 3,
            steps(share) + '\n\nIl campo **Codice HTML da incorporare nel tuo sito** contiene il codice da fornire a chi gestisce il sito. Usa il codice del corso corretto.\n\n<Note>Il controllo locale propone il codice; l’inserimento in un sito esterno e la sua visualizzazione non sono stati eseguiti. Occorre controllare accessibilità, dimensioni e aggiornamento nel sito di destinazione.</Note>', [26], 'calendar.share.embed', 'needs_external_verification', externalGap),
        page(calendarPath, 'Integrazione con Google Calendar', 'integrazione-con-google-calendar', 2, googleLocal, [], 'calendar.google.connect', 'needs_external_verification', googleGap),
        page(calendarPath, 'Collegare il tuo account Google', 'collegare-il-tuo-account-google', 3,
            steps(step('Individua il comando locale', 'Apri **Profilo → Integrazioni** e cerca la sezione Google Calendar. Il pulsante è **Connetti a Google** quando non esiste un collegamento.')) + '\n\n' + googleLocal, [], 'calendar.google.connect', 'needs_external_verification', googleGap),
        page(calendarPath, 'Sincronizzare un corso con Google Calendar', 'sincronizzare-un-corso-con-google-calendar', 3, googleSync, [], 'calendar.google.export', 'needs_external_verification', googleGap),
        page(calendarPath, 'Disconnettere Google Calendar', 'disconnettere-google-calendar', 3,
            `In **Profilo → Integrazioni**, per l'account collegato il comando è **Disconnetti** e richiede conferma.
La disconnessione coinvolge anche la revoca presso Google.
<Warning>La revoca e lo stato dei calendari già presenti su Google non sono stati verificati. Non considerare il messaggio locale una garanzia di cancellazione o conservazione dei calendari esterni.</Warning>`, [], 'calendar.google.disconnect', 'needs_external_verification', googleGap),
        page(calendarPath, 'Risorse correlate', 'risorse-correlate', 2,
            '<CardGroup cols={2}>\n<Card title="Collegare Google Calendar" icon="calendar-plus" href="/faq/come-collegare-google-calendar">Consulta i comandi locali e i passaggi che richiedono una verifica presso Google.</Card>\n</CardGroup>', [], 'calendar.reference.read', 'pending', 'Rinvio alla guida Google: il collegamento editoriale non esegue una connessione esterna.'),
        page(faqPath, 'Collegare Google Calendar', 'collegare-google-calendar', 1,
            `Il calendario dei corsi e il file **.ics** possono essere consultati localmente. Il collegamento a Google Calendar richiede invece una prova del servizio esterno: questa guida distingue i comandi disponibili dai risultati ancora da confermare.\n\n` + googleLocal, [], 'calendar.google.connect', 'needs_external_verification', googleGap),
        page(faqPath, 'Cosa serve per iniziare?', 'cosa-serve-per-iniziare', 2,
            `Servono un account Google, la configurazione dell'integrazione nell'installazione e l'accesso ai comandi di **Profilo → Integrazioni**. Il collegamento può richiedere autorizzazioni e credenziali che dipendono dall'installazione.
<Note>Questi requisiti non dimostrano che l'integrazione sia operativa. Prima dell'uso devono essere controllati autorizzazione, callback, token e permessi effettivi dell'account presso Google.</Note>`, [], 'calendar.google.connect', 'needs_external_verification', googleGap),
        page(faqPath, 'Come collegare il tuo account Google', 'come-collegare-il-tuo-account-google', 2,
            steps(step('Apri le Integrazioni', 'Dal menu laterale apri **Profilo**, poi **Integrazioni**. Cerca la sezione dedicata a Google Calendar.'),
                step('Riconosci il comando di collegamento', 'Il comando locale si chiama **Connetti a Google**. La procedura può aprire il servizio esterno in una nuova scheda; questa prova non lo avvia.')) + '\n\n' + googleLocal, [], 'calendar.google.connect', 'needs_external_verification', googleGap),
        page(faqPath, 'Come sincronizzare un corso', 'come-sincronizzare-un-corso', 2, googleSync, [], 'calendar.google.export', 'needs_external_verification', googleGap),
        page(faqPath, 'Cosa viene sincronizzato', 'cosa-viene-sincronizzato', 2,
            `L'esportazione locale verso Google prepara titolo, inizio e fine delle lezioni del registro del corso; i dettagli dell'istruttore dipendono dai dati assegnati alla lezione.
<Note>Non è stata controllata la presenza di questi dati in un calendario Google reale. Non sono garantiti aggiornamento automatico, completezza degli istruttori, gestione degli errori o consegna dei promemoria del servizio esterno. Il download **.ics** resta una procedura distinta dal collegamento Google.</Note>`, [], 'calendar.google.export', 'needs_external_verification', googleGap),
        page(faqPath, 'Come disconnettere Google Calendar', 'come-disconnettere-google-calendar', 2,
            `Per un account collegato **Profilo → Integrazioni** propone **Disconnetti** e una conferma. La richiesta locale contatta Google per revocare il collegamento e aggiorna lo stato dell'account.
<Warning>La revoca esterna, l'invalidazione dei token e lo stato dei calendari già creati non sono stati eseguiti o verificati. Controlla questi risultati con il responsabile dell'integrazione prima di considerare completa la disconnessione.</Warning>`, [], 'calendar.google.disconnect', 'needs_external_verification', googleGap),
    ];
}

// Populated from the inspected working tree. A fresh capture cannot silently
// upgrade changed implementation semantics to a reviewed recipe.
export const campsCalendarReviewedSources = Object.freeze({
    "UI/src/components/Sidebar.svelte": "5fd791241452c301697730caf21a841c29a186876c33834b579d7846610d2568",
    "UI/src/routes.js": "58bddc98548095f14f39fa3141277de8cd00e2133faf49f6761131b109d254b0",
    "UI/src/utils/Permissions.js": "100a24508b57b6c73a323ab4f128ee2c79aef3212a2cd0178172199d59e51854",
    "UI/src/utils/Functions.js": "c9ed79a8214d4e2ceb9d91e2cd3d52f42a32706f96e2890082ba5584c6787147",
    "UI/src/routes/association/course/campsAndRetreats/CampsAndRetreatsList.svelte": "22150f2bff9e259b3c9ac25e30f3727d3bfa41fb70d5cacf2cfca4ec3bc626f6",
    "UI/src/routes/association/course/campsAndRetreats/modals/AddModal.svelte": "061914f717490b6266becc60010d682806eb14e1e1a101df51a3ba5549f1427d",
    "UI/src/routes/association/course/campsAndRetreats/overview/CampsAndRetreatsOverview.svelte": "4c2acaf4a470d6f58d978075cc48ec9385ecc90d42efa689516432fa0777cb33",
    "UI/src/routes/association/course/campsAndRetreats/overview/CampsAndRetreatsPeriodOverview.svelte": "547f651a018373db48da0397d21185fc7f3bb0a160cfa996dc5c75935ed98563",
    "UI/src/routes/association/course/campsAndRetreats/overview/modals/AddModal.svelte": "8f1ff99f429affb76796f67f67e6b1e2af7c2a612a7cadef80073afec8f90b2c",
    "UI/src/routes/association/course/campsAndRetreats/overview/modals/camps-and-retreats-periods-form.svelte": "a50bef6baae85c88823eca243160fb248e58752c3ab250e1eba854307e60f5ed",
    "UI/src/components/inputs/DateRangePicker.svelte": "d54685f975199b3acd301e0f26ea5f13fe3f9c6f22d11da187efeb7a782c0687",
    "UI/src/components/inputs/DateRangeCalendar.svelte": "0f1347e4bcc861917f53f0d118c9c35f8207172644a9191b0f9ef6a50440b281",
    "UI/src/routes/association/course/campsAndRetreats/overview/modals/AddServiceModal.svelte": "7ebb41be26d34c7fe84b5a60915ade6460ba12212e55f6610d7f0630d5e1dfca",
    "UI/src/routes/association/course/campsAndRetreats/overview/modals/EditServiceModal.svelte": "2a153566f8486a9bb1e088e5e9a87c0ec808eeb386a318168c2bf846b4f7857a",
    "UI/src/routes/association/course/campsAndRetreats/overview/modals/camps-and-retreats-services-form.svelte": "1eb7e2a5a959b13c6b5fc94c1e0001f5c3d84d86367e421abee704a006c94d78",
    "UI/src/routes/association/course/campsAndRetreats/overview/modals/camps-and-retreats-subscriptions-form.svelte": "4d9a4e38e4c0ed3c2751c8c22a77c614007867d9af8a89d2860752cc23b9abf2",
    "UI/src/routes/association/course/campsAndRetreats/overview/modals/components/PeriodAndServiceSelector.svelte": "045c64688e145e887b0d6025067c6162c87100607cc66d497cbd4c92ac53ef97",
    "UI/src/routes/forms/CampsAndRetreatsForm.svelte": "a0b375e7da6827d50a9af7200bab512c8b93b33b75757bac076fa16d47ee00c5",
    "BE/application/views/camp_and_retreats_views.py": "9cceb73c7d4587564b990fb95ac8c038ac32a828527e9b6c3479903e9fff10f2",
    "BE/application/serializers/camps_and_retreats_serializers.py": "1376005299ec37442d6599ab272ebf1fb649274c994ee4330ce64f0bb5d1b3c2",
    "BE/application/utils/camps_and_retreats_utils.py": "1ecc605be1970691270a2f296f7b6185cf31258f09630244db83352362c04ce1",
    "BE/application/models/courses_models.py": "6a9cd0b923ab5b6a9dd120bcb7f81c1dac30914657fcd14b3fdb9e9c427d78c6",
    "BE/application/permissions_registry.py": "b8318ad63daa9452ac736b2152449b14c49f73bc1417622a4181b7c3ceb3b1fa",
    "UI/src/routes/calendar/Calendar.svelte": "4ad1177ade6622f183d521c009ccfb8dd455e26454f225b69360a3749e50d74f",
    "UI/src/routes/calendar/modals/AddCalendarEvent.svelte": "764483c367969b378edbbf2b0348e6d6fe5626d53de98782fbfd79a1b9b5db6f",
    "UI/src/routes/association/course/overview/OverviewCourse.svelte": "5aed27d46e9ce99c75452e3cd766db412daaaeca7ac2084a20581358195c06ce",
    "UI/src/routes/association/course/overview/components/Calendar.svelte": "5cdbdd7e450b7ccbcc87eee2349be0b156e9bdb2b79f79f750da99656fb6224e",
    "UI/src/routes/calendar/SharedCalendar.svelte": "c8774b4f846e72ec7412c19981427767379ac3f63c6a06040dc64c00f57ca923",
    "BE/application/views/attendee_views.py": "ee9f49b1907855cd6f3fddd93aafdeffe561917a33d40257c434c3b25384edb7",
    "BE/application/utils/global_calendar.py": "eac4ba8a31d10419af31d18fd36d972ba85a6d3f7251193a56aeda97ec717b47",
    "BE/application/models/attendee_models.py": "5f7e748de06d17bd2d8eb1bbfa4c9d69eb9d3af6d6ec63bfd0e919b3af5f58f4",
    "UI/src/routes/profile/sections/Integrations.svelte": "939a23d1aa9a1e73f98387275cee630504a8c863fa2e901d5ad683fe470ad6ae",
    "BE/application/views/google_views.py": "a5caee899a34f44ec77f9ffaaca0960d68dd11f4bfb23745907d26f16b5c2ae9",
    "BE/application/urls.py": "b475e7ed8c26a891854c4021d7308aefb192d2a26df8b3ee916a33f88e7cc359"
});
const reviewedFiles = campsCalendarReviewedSources;

// Editorial discovery describes draft inputs only. It never invokes the
// evidence builder or confers verification, including on locally captured steps.
export function campsCalendarEditorialContracts() {
    return campsCalendarDraftPages().map(page => ({
        path: page.path, id: page.id, title: page.title,
        status: ['unsupported', 'needs_external_verification'].includes(page.targetStatus) ? page.targetStatus : 'pending',
        reason: page.targetReason || page.reason || 'Richiede la prova browser reale di questa sezione.',
        recipe_module: 'docs/manuale/camps-calendar-recipes.mjs', verified: false,
        source_contracts: campsCalendarSourceContracts['camps-calendar-manage'].map(([path, symbol, length]) => ({
            path, symbol, length, reviewed_sha256: campsCalendarReviewedSources[path],
        })),
    }));
}

export function campsCalendarPages(id, report, source) {
    if (!campsCalendarCaptureSpecs[id]) return [];
    if (report?.id !== id || report.status !== 'passed' || report.backend !== 'real'
        || report.fixture_version !== 8 || report.fixture_profile !== 'baseline'
        || report.capture_format !== 'full-hd-v1' || report.viewport?.width !== 1920
        || report.viewport?.height !== 1080 || report.device_scale_factor !== 1)
        throw new Error('Camp/calendar requires the passed real v7 baseline Full HD scenario');
    const [prefix, checkpoints] = campsCalendarCaptureSpecs[id];
    if (!Array.isArray(report.screenshots) || report.screenshots.length !== checkpoints.length
        || report.screenshots.some((capture, index) => capture?.path !== prefix + (index + 1) + '.png'
            || capture.checkpoint !== checkpoints[index] || capture.master?.width !== 1920 || capture.master?.height !== 1080))
        throw new Error('Camp/calendar captures differ from the reviewed checkpoints');
    if (Object.entries(campsCalendarExpectedOutcomes[id]).some(([key, value]) => report.camps_calendar_manage?.[key] !== value))
        throw new Error('Camp/calendar outcomes differ from the reviewed workflow');
    for (const operation of ['google-calendar-oauth-export-revocation', 'calendar-reminder-delivery', 'calendar-share-whatsapp-email-dispatch'])
        if (!report.external_gaps?.some(gap => gap.operation === operation && gap.status === 'needs_external_verification'))
            throw new Error('External calendar operation must remain a gap: ' + operation);
    const evidence = campsCalendarSourceContracts[id].map(args => {
        const reference = source(report, ...args);
        if (!report.source_hashes?.[args[0]] || reference.sha256 !== report.source_hashes[args[0]])
            throw new Error('Changed or uncaptured camp/calendar source: ' + args[0]);
        if (reference.canonical_source_sha256 !== reviewedFiles[args[0]])
            throw new Error('Camp/calendar source changed; review required: ' + args[0]);
        return reference;
    });
    return campsCalendarDraftPages().map(({imageNumbers, targetStatus, targetReason, ...page}) => ({...page,
        body: targetStatus === 'verified' ? page.body.replace(pending, '') : page.body,
        status: targetStatus, reason: targetReason, evidence,
        screenshots: imageNumbers.map(number => report.screenshots[number - 1])}));
}
