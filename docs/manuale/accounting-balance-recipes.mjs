import {accountingBalanceSourceContracts} from '../../selfhost/tests/browser/manuale/accounting-balance-sources.mjs';

export const accountingBalanceCaptureSpecs = {
    'accounting-balance-manage': ['images/contabilita/bilancio/', [
        'baseline-cash-account-and-current-balance', 'new-bank-account-filled-before-save',
        'bank-account-created-and-persisted-after-reload', 'account-edit-initial-balance-before-save',
        'edited-bank-account-persists-after-reload', 'transfer-filled-with-distinct-accounts',
        'transfer-persists-after-reload', 'cash-bank-balances-reflect-internal-transfer',
        'balance-current-year-six-available-years', 'manual-balance-row-description-and-two-amounts',
        'saved-manual-row-persists-after-reload', 'publication-confirmation-is-reversible',
        'published-balance-persists-after-reload', 'unpublish-confirmation',
        'draft-and-manual-row-restored-after-unpublish-reload', 'transfer-delete-confirmation',
        'deleted-transfer-absent-and-balances-restored', 'unused-bank-account-deleted-baseline-restored',
    ]],
};
export const accountingBalanceExpectedOutcomes = Object.freeze({
    'accounting-balance-manage': Object.freeze({baseline_cash: 50, bank_created_initial_balance: 100,
        bank_saved_initial_balance: 125, transfer_amount: 20, cash_after_transfer: 30, bank_after_transfer: 145,
        liquid_total_after_transfer: 175, automatic_income: 50, available_year_count: 6,
        manual_row_description: 'Contributo dimostrativo direttivo', manual_institutional: 15,
        manual_commercial: 5, resulting_income: 70, manual_row_persisted_after_reload: true,
        published_persisted_after_reload: true, unpublish_persisted_after_reload: true,
        automatic_payments_unchanged: true, transfer_deleted_after_reload: true,
        bank_deleted_after_reload: true, baseline_cash_restored: 50, reader_read_status: 403,
        reader_write_status: 403, export_exercised: false, invoices_exercised: false,
        suppliers_exercised: false, categories_exercised: false, fiscal_change_exercised: false,
        associated_member_visibility_exercised: false}),
});

const pending = '<Note>Bozza in attesa di prova: questa sezione deve essere confermata con una procedura reale prima della pubblicazione.</Note>\n\n';
const image = (number, caption) => `<Frame>![${caption}](/images/contabilita/bilancio/${number}.png)</Frame>`;
const step = (title, body) => `<Step title="${title}">\n${body}\n</Step>`;
const steps = (...items) => `<Steps>\n${items.join('\n')}\n</Steps>`;
const accountTypes = `| Tipologia | Uso del conto |\n| --- | --- |\n| **Cassa** | Contanti dell'associazione |\n| **Banca** | Conto corrente bancario |\n| **Altro** | Altri strumenti di incasso o pagamento |`;
const accountCreate = steps(
    step('Apri Conti Finanziari', `Nel menu **Contabilità**, apri **Gestione > Conti Finanziari**.
Occorrono l'accesso ai conti e il permesso per crearli. Nell'esempio trovi **Cassa Aurora**, con saldo iniziale **0,00 €** e saldo attuale **50,00 €**.
${image(1, 'Conti finanziari e saldo attuale della Cassa Aurora')}`),
    step('Compila il nuovo conto', `Premi **Conto** con il simbolo **+**.
Nella finestra **Creazione di un conto economico** inserisci **Nome**, **Tipologia**, **Saldo Iniziale** e **Codice**.
Nell'esempio: **Banca Aurora**, **Banca**, **100,00** e **BANCA-AURORA**. Tutti questi campi sono richiesti.
Lo **Stato** si modifica successivamente: il nuovo conto nasce attivo.
${image(2, 'Nuovo conto Banca Aurora prima del salvataggio')}`),
    step('Salva e controlla il risultato', `Premi **Salva**. Dopo la conferma **Conto creato con successo.**, la finestra si chiude e l'elenco si aggiorna.
Ricarica la pagina e controlla nome, tipologia e saldo iniziale. Per rileggere il codice, apri la matita **Modifica** del conto. Un conto senza movimenti ha saldo attuale uguale al saldo iniziale.
${image(3, 'Conto Banca Aurora conservato dopo il ricaricamento')}`));
const accountEdit = steps(
    step('Apri la modifica del conto', `In **Gestione > Conti Finanziari** premi la matita **Modifica** nella riga del conto.
Puoi aggiornare **Nome**, **Tipologia**, **Stato**, **Saldo Iniziale** e **Codice**.
Nell'esempio correggi il saldo iniziale di **Banca Aurora** da **100,00** a **125,00**.
${image(4, 'Correzione del saldo iniziale nella finestra di modifica')}`),
    step('Salva e ricontrolla', `Premi **Salva**, attendi **Conto modificato con successo.** e ricarica l'elenco.
Il saldo iniziale e quello attuale di questo conto, ancora senza movimenti, diventano **125,00 €**.
Correggere il saldo iniziale cambia la base di calcolo del conto; usa un movimento per registrare un nuovo incasso o una nuova spesa.
${image(5, 'Saldo iniziale corretto conservato dopo il ricaricamento')}`));
const transferCreate = steps(
    step('Apri Giroconti', `Da **Contabilità**, scegli **Gestione > Giroconti** e premi **Giroconto** con il simbolo **+**.
Prima crea i due conti fra cui spostare il denaro. Occorrono i permessi di lettura dei conti e di creazione dei giroconti.`),
    step('Indica data, origine, destinazione e importo', `Compila **Data giroconto**, **Dal conto**, **Al conto** e **Importo**.
Nell'esempio trasferisci **20,00 €** da **Cassa Aurora** a **Banca Aurora** alla data mostrata.
Inserisci l'importo con due decimali, ad esempio **20,00**. Le due scelte devono indicare conti diversi: l'interfaccia esclude dalla seconda scelta il conto già selezionato.
${image(6, 'Giroconto compilato con origine e destinazione diverse')}`),
    step('Salva e controlla il trasferimento', `Premi **Salva**, attendi la conferma e ricarica la pagina.
Controlla data, conti e importo nella riga creata.
${image(7, 'Giroconto conservato dopo il ricaricamento')}
Apri **Conti Finanziari**: la cassa passa da **50,00 €** a **30,00 €** e la banca da **125,00 €** a **145,00 €**.
Il totale dei due conti rimane **175,00 €**: il giroconto sposta denaro e non crea un'entrata o una spesa.
${image(8, 'Saldi della cassa e della banca dopo il giroconto')}`));
const transferDelete = steps(
    step('Scegli il giroconto da eliminare', `Apri **Gestione > Giroconti** e individua data, origine, destinazione e importo.
Premi il cestino **Elimina** nella sua riga. È necessario il permesso per eliminare i giroconti.
${image(16, 'Conferma per eliminare il giroconto')}`),
    step('Conferma e verifica i saldi', `Nella richiesta **Vuoi eliminare il giroconto?**, premi **Elimina**; **Annulla** lascia il trasferimento presente.
Ricarica l'elenco e controlla che la riga sia assente. In **Conti Finanziari** i saldi tornano a **50,00 €** per la cassa e **125,00 €** per la banca.
${image(17, 'Giroconto eliminato e saldi ripristinati')}
<Warning>Elimina solo un trasferimento registrato per errore. Se il denaro è stato spostato davvero, correggi la registrazione mantenendo una traccia coerente con i movimenti reali.</Warning>`));
const balanceConsult = steps(
    step('Apri il rendiconto e scegli l’anno', `Da **Contabilità > Gestione**, premi **Bilancio**.
Si apre **Rendiconto economico finanziario**. Il selettore **Anno sociale** offre sei anni: quello corrente e i cinque precedenti.
Controlla il periodo indicato sopra il selettore. Nell'esempio l'esercizio comincia il **1° gennaio**.
${image(9, 'Rendiconto, periodo selezionato e scelta dell’anno sociale')}`),
    step('Controlla entrate, spese e liquidità', `Leggi **A) Entrate**, **B) Uscite**, **C) Rendiconto della gestione** e **D) Liquidità**.
Le voci dei pagamenti sono divise fra **Istituzionali** e **Commerciali** in base alla causale.
Nell'esempio le entrate automatiche sono **50,00 €**: due quote pagate da **25,00 €**. La terza quota, non pagata, è esclusa.
La liquidità mostra **30,00 €** in cassa e **145,00 €** in banca, per un totale di **175,00 €**.
<Note>Le entrate e le spese automatiche comprendono i pagamenti saldati con importo positivo, non eliminati e non archiviati.
La data considerata è quella di pagamento; se manca, viene usata la data di creazione. Il saldo dei conti ha un calcolo diverso e comprende i movimenti precedenti: non aspettarti che coincida con la differenza delle sole entrate e spese dell'esercizio.</Note>`),
    step('Aggiungi una voce manuale e salvala', `In una bozza, nella sezione **Entrate**, premi **Aggiungi riga**.
Inserisci la **Descrizione** e gli importi nelle colonne **Istituzionali** e **Commerciali**.
Nell'esempio **Contributo dimostrativo direttivo** contiene **15,00 €** istituzionali e **5,00 €** commerciali.
${image(10, 'Riga manuale con descrizione e importi prima del salvataggio')}
Premi **Salva** in alto, poi ricarica la pagina. Controlla che la descrizione e gli importi siano conservati: il totale delle entrate diventa **70,00 €**.
Il pulsante con il dischetto della singola riga chiude la modifica della riga; per conservare il rendiconto premi **Salva**.
${image(11, 'Riga manuale e importi conservati dopo il ricaricamento')}
<Warning>Una riga manuale modifica il rendiconto, ma non crea un pagamento e non cambia il saldo dei conti.
Evita di aggiungere manualmente un importo già conteggiato da un pagamento. I valori delle righe automatiche restano calcolati dai pagamenti.</Warning>
<Note>I comandi **Aggiorna**, **Reset**, il cambio verso un esercizio precedente e lo scaricamento con **Esporta PDF/Excel** richiedono prove dedicate. Questa procedura verifica il rendiconto dell'esercizio corrente e il salvataggio di una riga manuale.</Note>`));
const balancePublish = steps(
    step('Verifica il rendiconto prima di pubblicarlo', `Controlla l'anno selezionato, le quote saldate, le spese, le righe manuali e i saldi dei conti.
Premi **Salva** e ricarica la pagina per controllare l'ultima bozza. Il comando richiede il permesso di modifica del bilancio.
${image(11, 'Bozza salvata da ricontrollare prima della pubblicazione')}`),
    step('Conferma la pubblicazione', `Premi **Pubblica**, poi **Pubblica** nella richiesta **Vuoi pubblicare il bilancio?**.
La finestra ricorda che puoi annullare la pubblicazione.
${image(12, 'Conferma della pubblicazione con indicazione che è annullabile')}
Ricarica la pagina: il pulsante diventa **Annulla pubblicazione** e le righe non presentano i comandi di modifica della bozza.
Il rendiconto pubblicato conserva i dati salvati invece di essere ricalcolato durante questa consultazione.
${image(13, 'Rendiconto pubblicato conservato dopo il ricaricamento')}`),
    step('Torna alla bozza quando occorre correggere', `Premi **Annulla pubblicazione** e conferma con lo stesso comando nella finestra.
${image(14, 'Conferma per annullare la pubblicazione')}
Ricarica la pagina: torna **Pubblica** e sono di nuovo disponibili i comandi della bozza.
La riga **Contributo dimostrativo direttivo** conserva i suoi importi **15,00 €** e **5,00 €**.
${image(15, 'Bozza ripristinata e riga manuale conservata')}
<Note>La prova riguarda lo stato del rendiconto gestito dal titolare. La visibilità per i singoli associati, l'invio di comunicazioni e la stampa dei file richiedono verifiche separate: la pubblicazione qui non dimostra che tutti gli associati ricevano o vedano il documento.</Note>`));

export function accountingBalanceDraftPages() {
    const drafts = [];
    const add = (path, title, id, level, body, imageNumbers = [], supported = false,
        reason = 'Procedura non esercitata in questo flusso; occorrono salvataggio, controllo dopo il ricaricamento e prova dei permessi.', external = false) => {
        const unsupported = ['modificare-una-fattura-attiva', 'segnare-una-fattura-come-pagata'].includes(id);
        drafts.push({path, title, id, body: '#'.repeat(level) + ' ' + title + '\n\n' + pending + body,
            evidence: [], screenshots: [], intent: 'accounting.' + id, status: unsupported ? 'unsupported' : 'pending',
            reason: unsupported ? 'La pagina Fatture Attive non monta un comando di modifica o incasso; il solo endpoint backend non costituisce una procedura disponibile.' : reason,
            imageNumbers, supported, external});
    };
    const a = (title, id, level, body, numbers = [], supported = false, reason, external) =>
        add('docs/contabilita-avanzata.mdx', title, id, level, body, numbers, supported, reason, external);
    a('Panoramica', 'panoramica', 2, `La sezione **Contabilità** raccoglie pagamenti, documenti fiscali e strumenti di gestione.
Apri **Documenti fiscali** per le **Fatture Attive** e le **Fatture Passive**; apri **Gestione** per **Fornitori e Clienti**, **Conti Finanziari**, **Giroconti** e **Bilancio**.
Le **Causali** si gestiscono dalla pagina dei **Pagamenti**.
<Info>Il titolare dell'associazione usa i comandi descritti nell'esempio. Per un collaboratore servono i permessi delle singole sezioni e delle operazioni: il solo accesso ai pagamenti non autorizza a leggere o modificare conti, giroconti e bilancio.</Info>
<Note>Questo percorso verifica conti, giroconti e rendiconto. Le procedure sulle fatture, sulle anagrafiche e sulle causali sono descritte nelle bozze seguenti e restano da provare.</Note>`);
    a('Fatture attive', 'fatture-attive', 2, `Le **Fatture Attive** riguardano i documenti emessi verso i clienti.
Preparare e registrare un documento, contrassegnarlo come pagato o inviato e trasmetterlo al destinatario sono operazioni diverse.
Prima prepara i dati dell'associazione, quelli del cliente e le righe della fattura.
<Note>Creazione, generazione del documento e file XML non sono esercitate in questa procedura. La conformità fiscale e la consegna tramite servizi esterni richiedono una verifica separata.</Note>`);
    a('Creare una fattura attiva', 'creare-una-fattura-attiva', 3, steps(
        step('Apri Fatture Attive', 'Scegli **Contabilità > Documenti fiscali > Fatture Attive** e apri il comando per una nuova fattura. Occorre il permesso per creare fatture attive.'),
        step('Controlla i dati generali e la numerazione', 'Verifica paese, formato, tipo documento, valuta, destinatario SDI e **Data Fattura**. Compila **Prefisso**, **Numero** e **Anno**: il modulo contiene questi campi, quindi non presumere una numerazione completamente automatica.'),
        step('Completa cedente, cliente e righe', `Compila denominazione, dati fiscali e sede del cedente e del cliente. Verifica i recapiti del destinatario.
Per ogni riga indica descrizione, quantità, prezzo unitario, aliquota IVA e unità di misura. Controlla i totali e le condizioni di pagamento prima di salvare.`),
        step('Salva e verifica il documento', 'Dopo **Salva**, torna all’elenco, cerca il numero appena inserito e controlla che il documento sia presente anche dopo il ricaricamento. Un messaggio di errore durante la preparazione del file non dimostra che la fattura sia pronta per l’invio.')) +
        '\n\n<Warning>Generazione, scaricamento XML e trasmissione al Sistema di Interscambio restano da verificare. Questa bozza non attesta che un documento sia stato accettato o consegnato.</Warning>', [], false,
        'Creazione e documenti non esercitati; la trasmissione esterna richiede una verifica autorizzata.', true);
    a('Modificare una fattura attiva', 'modificare-una-fattura-attiva', 3,
        'La pagina **Fatture Attive** non espone un comando di modifica in questa versione. Non seguire i precedenti passaggi con la matita: il modulo non è disponibile dalla pagina.');
    a('Segnare una fattura come pagata', 'segnare-una-fattura-come-pagata', 3,
        'La pagina **Fatture Attive** non espone un comando per cambiare lo stato **Pagata**. Per registrare un incasso consulta la guida **Pagamenti**, evitando di duplicare una somma già registrata.');
    a('Fatture passive', 'fatture-passive', 2, `Le **Fatture Passive** registrano i documenti ricevuti dai fornitori, con importo, scadenza, conto e stato di pagamento.
Prepara l'anagrafica del fornitore e il conto da usare prima della registrazione.
<Note>Questa procedura non crea né paga una fattura passiva. Il collegamento con il pagamento deve essere controllato con una prova dedicata.</Note>`);
    a('Registrare una fattura passiva', 'registrare-una-fattura-passiva', 3, steps(
        step('Apri Fatture Passive', 'Scegli **Contabilità > Documenti fiscali > Fatture Passive** e premi **Fattura** con il simbolo **+**.'),
        step('Inserisci il documento del fornitore', `Compila **Identificativo**, **Importo**, **Data pagamento**, **Data scadenza**, **Conto**, **Fornitore** e **Pagata**; aggiungi le **Note** se servono.
Usa il numero del documento ricevuto. Il conto e il fornitore devono essere già disponibili. Tieni separata la scadenza dalla data dell'effettivo pagamento.`),
        step('Salva e controlla dopo il ricaricamento', 'Premi **Salva**. Ricarica l’elenco e verifica identificativo, importo, fornitore, conto e stato. Controlla anche il pagamento collegato prima di registrare un’altra spesa per lo stesso documento.')));
    a('Modificare una fattura passiva', 'modificare-una-fattura-passiva', 3, steps(
        step('Apri la fattura da correggere', 'In **Fatture Passive** premi la matita **Modifica** nella riga del documento.'),
        step('Correggi e salva', 'Ricontrolla identificativo, importo, date, conto, fornitore, stato e note. Premi **Salva**, poi ricarica la pagina e verifica sia il documento sia il pagamento collegato. La verifica del mantenimento di tale collegamento è ancora da eseguire.')));
    a('Segnare una fattura passiva come pagata', 'segnare-una-fattura-passiva-come-pagata', 3, steps(
        step('Verifica il pagamento al fornitore', 'Controlla conto, importo e data effettiva dell’uscita.'),
        step('Salva lo stato della fattura', 'Apri **Modifica**, imposta **Pagata**, controlla **Data pagamento** e premi **Salva**. Ricarica l’elenco e controlla la registrazione della spesa collegata.')) +
        '\n\n<Warning>Non registrare due volte la stessa uscita. Lo stato e la data devono corrispondere al pagamento effettivo; il loro effetto sul rendiconto non è verificato da questo percorso sui giroconti.</Warning>');
    a('Fornitori e clienti', 'fornitori-e-clienti', 2, `In **Contabilità > Gestione > Fornitori e Clienti** raccogli le anagrafiche dei soggetti con cui l'associazione ha rapporti economici.
Ogni anagrafica ha un tipo **Fornitore** oppure **Cliente**. Prima di crearne una, cerca il nome per evitare duplicati.
Occorrono i permessi per consultare e gestire queste anagrafiche. Creazione, modifica e rimozione sono ancora da provare in una procedura dedicata.`);
    a('Aggiungere un fornitore o cliente', 'aggiungere-un-fornitore-o-cliente', 3, steps(
        step('Apri una nuova anagrafica', 'Da **Gestione > Fornitori e Clienti** apri il comando per una nuova anagrafica.'),
        step('Compila i dati del soggetto', `Inserisci **Nome** e scegli **Tipo** fra **Fornitore** e **Cliente**.
Completa quando disponibili codice fiscale, partita IVA, indirizzo, email, telefono, città, CAP, provincia, paese, nazionalità e note.
Il nome è richiesto. Un indirizzo email inserito deve essere valido; lascia vuoti i dati che non conosci anziché inventarli.`),
        step('Salva e cerca il risultato', 'Premi **Salva**, torna all’elenco e cerca il nome. Ricarica e riapri la scheda per verificare tipo, recapiti e dati fiscali.')));
    a('Modificare un fornitore o cliente', 'modificare-un-fornitore-o-cliente', 3, steps(
        step('Apri la scheda corretta', 'Cerca il soggetto in **Fornitori e Clienti** e apri la sua scheda. Controlla nome e dati fiscali prima di correggere un omonimo.'),
        step('Salva le correzioni', 'Aggiorna i campi e premi **Salva**. Dopo la conferma riapri la scheda e controlla i dati conservati. Non presumere che cambino anche i documenti già emessi: questa variante resta da verificare.')));
    a('Eliminare un fornitore o cliente', 'eliminare-un-fornitore-o-cliente', 3, steps(
        step('Controlla i documenti collegati', 'Prima della rimozione verifica se il soggetto è utilizzato in fatture o pagamenti. Conservalo se serve a ricostruire documenti o movimenti esistenti.'),
        step('Conferma solo il soggetto corretto', 'Usa il cestino **Elimina**, controlla il nome nella richiesta di conferma e conferma solo dopo la verifica. Ricarica l’elenco e controlla l’assenza della voce.')) +
        '\n\n<Warning>La rimozione e i suoi effetti sui collegamenti non sono esercitati. Questa bozza non promette che il programma impedisca ogni eliminazione di un soggetto già utilizzato.</Warning>');
    a('Conti finanziari', 'conti-finanziari', 2, `I conti rappresentano cassa, banca e altri strumenti finanziari.
Apri **Contabilità > Gestione > Conti Finanziari** per leggere **Saldo iniziale**, **Saldo attuale**, **Tipo** e **Stato**.
Il saldo attuale parte da quello iniziale, aggiunge le entrate saldate e i giroconti ricevuti, e sottrae le spese saldate e i giroconti inviati.
${image(8, 'Elenco conti con i saldi dopo il trasferimento')}
<Note>L'elenco dei conti mostra il saldo complessivo dei movimenti; la liquidità del rendiconto considera la fine dell'esercizio selezionato. Le condizioni sui pagamenti archiviati e sulle date non coincidono in tutti i prospetti: controlla i movimenti se trovi una differenza.</Note>`, [8], true);
    a('Tipologie di conto', 'tipologie-di-conto', 3, accountTypes + `\n\nLa tipologia stabilisce se il conto viene riepilogato in **Cassa**, **Banca** o **Altro** nella liquidità.
Nell'esempio vengono usate cassa e banca. Un conto di tipo **Altro** resta una variante da esercitare.
${image(2, 'Tipologia Banca nel modulo di creazione')}`, [2], true);
    a('Creare un nuovo conto', 'creare-un-nuovo-conto', 3, accountCreate, [1, 2, 3], true);
    a('Modificare un conto', 'modificare-un-conto', 3, accountEdit + `
<Note>I conti predefiniti possono essere modificati; il loro cestino non consente l'eliminazione.
Per un conto personale, il cestino è disabilitato se ci sono pagamenti o giroconti collegati. Nell'esempio la banca può essere rimossa solo dopo avere eliminato il giroconto e aver controllato che non abbia pagamenti.
La rimozione richiede conferma; dopo il ricaricamento rimane la sola cassa dimostrativa.</Note>
${image(18, 'Conto bancario inutilizzato rimosso e cassa iniziale conservata')}
<Warning>Disattivare un conto non dimostra che sia escluso dal calcolo del rendiconto: non usare lo stato per nascondere movimenti già registrati. Questa prova corregge il saldo iniziale e lascia il conto attivo.</Warning>`, [4, 5, 18], true);
    a('Giroconti', 'giroconti', 2, `I giroconti spostano denaro fra i conti dell'associazione e si consultano in **Contabilità > Gestione > Giroconti**.
Un trasferimento dalla cassa alla banca riduce la cassa e aumenta la banca dello stesso importo.
Nell'esempio **20,00 €** passano da **Cassa Aurora** a **Banca Aurora**: il totale della liquidità resta **175,00 €**.
${image(8, 'Effetto del giroconto sui due saldi senza variazione del totale')}
<Tip>Per un incasso da un socio o una spesa verso un fornitore usa il pagamento corrispondente. Il giroconto serve allo spostamento interno.</Tip>`, [8], true);
    a('Creare un giroconto', 'creare-un-giroconto', 3, transferCreate, [6, 7, 8], true);
    a('Eliminare un giroconto', 'eliminare-un-giroconto', 3, transferDelete, [16, 17], true);
    a('Causali di pagamento', 'causali-di-pagamento', 2, `Le causali classificano entrate e spese e ne determinano il raggruppamento nel rendiconto.
Apri **Pagamenti** e **Gestisci Causali** per consultarle. Prima di aggiungerne una, controlla quelle esistenti.
<Note>Le operazioni di creazione, modifica, ripartizione fra più causali e riepilogo delle detrazioni restano da provare. Il flusso di bilancio usa soltanto la causale già presente sulle quote dimostrative.</Note>`);
    a('Tipologie di causali', 'tipologie-di-causali', 3, `Ogni causale distingue **Entrata** oppure **Uscita** e **Istituzionale** oppure **Commerciale**.
Queste scelte hanno scopi diversi: il movimento stabilisce la direzione, la tipologia stabilisce la colonna del rendiconto.
Per le entrate può essere presente **Detraibile fiscalmente**.
<Note>La scelta fiscale dipende dalla situazione dell'associazione e del pagamento; il programma registra la classificazione scelta, senza attestare che ogni spesa dia diritto a una detrazione.</Note>`);
    a('Creare una causale', 'creare-una-causale', 3, steps(
        step('Apri Gestisci Causali', 'Dalla pagina **Pagamenti** scegli **Gestisci Causali**, poi **Causale** con il simbolo **+**.'),
        step('Classifica la voce', 'Compila **Nome**, **Movimento** e **Tipologia**. Per un’entrata controlla **Detraibile fiscalmente**. In **Gestione IVA** verifica **Tipo IVA** e **Tipo detrazione** secondo la classificazione concordata per la tua associazione.'),
        step('Salva e ricontrolla la scelta', 'Premi **Salva**, ricarica l’elenco e controlla nome, direzione, tipologia e opzioni. Prima di usare la causale su molti pagamenti, verifica il risultato su una singola registrazione.')));
    a('Modificare una causale', 'modificare-una-causale', 3, steps(
        step('Apri la matita della causale personale', 'In **Gestisci Causali** individua la voce e premi **Modifica**. Le causali comuni fornite dal sistema hanno i comandi di modifica e rimozione disabilitati.'),
        step('Salva e controlla il rendiconto', 'Correggi i campi disponibili, premi **Salva** e ricontrolla dopo il ricaricamento. Le classificazioni sono utilizzate nel calcolo dei pagamenti: l’effetto su registrazioni precedenti e rendiconti già pubblicati resta da esercitare.')));
    a('Causali aggiuntive nei pagamenti', 'causali-aggiuntive-nei-pagamenti', 3, steps(
        step('Apri il pagamento', 'Nel modulo del pagamento trova **Causali aggiuntive** e premi **Aggiungi causale**.'),
        step('Ripartisci l’importo senza duplicarlo', 'Per ogni riga scegli una causale coerente con **Entrata** o **Uscita** e indica l’importo assegnato. Il rendiconto sposta questi importi dalla causale principale alle aggiuntive: non sono nuovi incassi.'),
        step('Salva e confronta la ripartizione', 'Salva il pagamento, riaprilo e controlla causali e importi. Confronta poi le voci del rendiconto mantenendo il totale del pagamento. Questa prova di ripartizione resta da eseguire.')));
    a('Causali detraibili 730', 'causali-detraibili-730', 3, `**Detraibile fiscalmente** contrassegna le causali delle entrate che intendi utilizzare nei riepiloghi delle spese detraibili.
Controlla persona, causale, importo, data e modalità di pagamento prima di preparare la documentazione richiesta dal socio.
<Note>La produzione del riepilogo 730 e la sua completezza non sono esercitate. Questo contrassegno non è una conferma automatica del diritto alla detrazione e non sostituisce la verifica della documentazione.</Note>`);
    const b = (title, id, body, numbers = [], supported = false, reason) =>
        add('docs/bilancio.mdx', title, id, 2, body, numbers, supported, reason);
    b('Bilancio', 'bilancio', `Il **Bilancio** riepiloga le entrate e le uscite dell'esercizio e mostra la liquidità dei conti.
Apri **Contabilità > Gestione > Bilancio**. Sono necessari i permessi per consultare il rendiconto; per salvare e pubblicare serve anche quello di modifica.
Il collaboratore dimostrativo può leggere i pagamenti, ma non ha accesso a conti, giroconti e rendiconto.
${balanceConsult}`, [9, 10, 11], true);
    b('Anno fiscale', 'anno-fiscale', `L'esercizio del rendiconto segue il **giorno e mese d'inizio dell'anno fiscale**.
Apri **Profilo > Generali**, trova **Anno fiscale** e controlla la scelta: **Anno solare**, **Anno sportivo (1)**, **Anno sportivo (2)** oppure **Altro periodo**.
L'anno solare comincia il 1° gennaio; le due scelte sportive cominciano rispettivamente il 1° settembre e il 1° giugno. Per un altro periodo indica giorno e mese.
<Warning>La stagione sportiva e l'anno fiscale sono impostazioni distinte. Ricontrolla l'intervallo del rendiconto dopo ogni modifica; non presumere che cambino insieme.</Warning>
<Note>Questa procedura consulta il rendiconto con inizio al 1° gennaio e non cambia le impostazioni dell'anno fiscale. Il cambio di periodo e gli esercizi precedenti richiedono la guida e la prova dedicate.</Note>`);
    b('Conti economici', 'conti-economici', `Nel menu attuale questa funzione si chiama **Conti Finanziari** e si trova in **Contabilità > Gestione**.
${accountTypes}
\n\n${accountCreate}
<Note>Quando registri un pagamento controlla il campo **Conto**. Questa procedura non verifica un'assegnazione automatica alla banca: le quote dell'esempio sono collegate a **Cassa Aurora**.</Note>`, [1, 2, 3], true);
    b('Giroconti', 'giroconti', transferCreate + '\n\n' + transferDelete, [6, 7, 8, 16, 17], true);
    b('Pubblicazione del bilancio', 'pubblicazione-del-bilancio', balancePublish, [11, 12, 13, 14, 15], true);
    b('Risorse correlate', 'risorse-correlate', `<CardGroup cols={2}>
<Card title="Come generare il bilancio" icon="file-invoice-dollar" href="/faq/come-generare-bilancio">Consulta il rendiconto, configura i conti e verifica pubblicazione e ritorno alla bozza.</Card>
</CardGroup>
\n\n<Note>Il pulsante **Esporta PDF/Excel** è presente nel rendiconto. Scaricamento e contenuto dei file richiedono una prova separata prima di usare una guida all'esportazione.</Note>`, [], true);
    const f = (title, id, body, numbers = [], supported = false, reason) =>
        add('faq/come-generare-bilancio.mdx', title, id, 1, body, numbers, supported, reason);
    f("Cos'è il bilancio", 'cos-e-il-bilancio', `Il rendiconto economico finanziario raccoglie **A) Entrate**, **B) Uscite**, **C) Rendiconto della gestione** e **D) Liquidità**.
Le prime due sezioni distinguono attività istituzionali e commerciali; il rendiconto della gestione confronta entrate e spese; la liquidità riepiloga cassa, banca e altri conti.
Le entrate automatiche dell'esempio sono **50,00 €**. Dopo la riga manuale da **20,00 €**, diventano **70,00 €**; la liquidità rimane **175,00 €** perché una voce manuale non è un pagamento.
${image(11, 'Rendiconto con quote automatiche e contributo manuale salvato')}
<Note>Sono conteggiati i pagamenti saldati con importo positivo, non eliminati e non archiviati, secondo la data di pagamento oppure, se manca, quella di creazione. Una quota ancora da incassare non entra nelle entrate automatiche.</Note>`, [11], true);
    f('Configurare i conti economici', 'configurare-i-conti-economici', accountTypes + '\n\n' + accountCreate + '\n\n' + accountEdit,
        [1, 2, 3, 4, 5], true);
    f('I giroconti (trasferimenti tra conti)', 'i-giroconti-trasferimenti-tra-conti', transferCreate + '\n\n' + transferDelete,
        [6, 7, 8, 16, 17], true);
    f('Generare e consultare il bilancio', 'generare-e-consultare-il-bilancio', balanceConsult, [9, 10, 11], true);
    f("Allineamento dell'anno fiscale", 'allineamento-dell-anno-fiscale', steps(
        step('Controlla l’inizio dell’esercizio', 'Apri **Profilo > Generali > Anno fiscale**. Controlla la scelta del periodo e, per **Altro periodo**, il giorno e il mese iniziali. La stagione sportiva ha campi separati.'),
        step('Salva e ricontrolla il periodo', 'Se devi cambiare la scelta, salva dalle impostazioni, ricaricale e verifica i valori. Torna al rendiconto e controlla l’intervallo prima di confrontare gli importi.')) +
        '\n\n<Note>Il cambio dell’anno fiscale non è eseguito in questo percorso. Il rendiconto dimostrativo usa l’anno solare; un esercizio che comincia in settembre o in un giorno personalizzato richiede una verifica dedicata.</Note>');
    f('Pubblicare il bilancio', 'pubblicare-il-bilancio', balancePublish, [11, 12, 13, 14, 15], true);
    return drafts;
}

// Frozen reviewed hashes are generated only after inspecting the implementation.
export const accountingBalanceReviewedSources = Object.freeze({
    "BE/application/impersonation.py": "04337e8ece4eb8027f396392835170e56ff176a06b2565b70c87455417b5035e",
    "BE/application/models/balance_sheet_models.py": "08aef0835594ebea11087743e7e327289c2682a0944bc492eb9b2739c72b2aca",
    "BE/application/models/invoices_models.py": "5848c1bcdbafdd6d644f5dd70e82979095d1bdbe85c989075784e54418fc1af6",
    "BE/application/models/payment_models.py": "9dbcab8698adbde447a9c4f6eb52143409cf197fde3ce90d0aa8da56a1f8ebda",
    "BE/application/permissions_registry.py": "b8318ad63daa9452ac736b2152449b14c49f73bc1417622a4181b7c3ceb3b1fa",
    "BE/application/serializers/balance_sheet.py": "718b937c896e289fcf232351859da538de98ea829511f1b93baefdbf45d7019e",
    "BE/application/serializers/invoice_serializers.py": "3a52147b36fe7977661358f629cfc45101aa59d15aae06f5f7078c1662ff3fb9",
    "BE/application/serializers/payment_serializers.py": "abc31bd21b536db8cfe5972c24d0befed834914fb8bdeb832205c2e8b62b4e4f",
    "BE/application/utils/api_utils.py": "5db286fc73e92356b476514f4d6561a3300b36668ee99831ca1b197835a5cabf",
    "BE/application/utils/balance_sheet_utils.py": "16edc940ec32f5a0ec086edd1a8b1852af19994a4f8499441297b2b7e79cd317",
    "BE/application/views/balance_sheet_views.py": "4527dba2bdf399cc9ba36728e25866f42e4793789e98aa84eb16b399b8673757",
    "BE/application/views/invoice_views.py": "bb0f44ec825d4050322f306d280e078178b1978e42a5964c80c8d400b187a331",
    "BE/application/views/payment_views.py": "4f45b99318afaa7189177f73cf82863f6f281dc8a2992b15dcf299b11f8e8c34",
    "BE/application/views/supplier_views.py": "a332d25f18ba6b713cf75785aa58194bdf1165e313f8c3981efd33f3d3628321",
    "UI/src/components/Sidebar.svelte": "9f8344602ae269b5dbbf90dfd6f75e5154f3d5774ad544ac41105b74af2e333c",
    "UI/src/components/invoice/InvoiceForm.svelte": "2038f91a400229decbec387a661cdc95336afea0c2c2c565253826db67955b30",
    "UI/src/routes.js": "58bddc98548095f14f39fa3141277de8cd00e2133faf49f6761131b109d254b0",
    "UI/src/routes/accounting/accounts/Accounts.svelte": "c8cd2533480465f1abd729ac3b822dfd2d1b967a48140d1081d6bc6664bf3d56",
    "UI/src/routes/accounting/accounts/modals/EditModal.svelte": "a1b92bebde85ff40520c7f563d7cbec3c6786f47426c12f8a77566cd85a9a413",
    "UI/src/routes/accounting/accountsTransfer/AccountsTransfer.svelte": "79b536b9887e5c70e99f6ba7e57f4c540ee659866e7b1236da9e803d7c52de0f",
    "UI/src/routes/accounting/balanceSheet/BalanceSheetEditor.svelte": "2af140853dabbcc5b989229cead80dc0e9bb5f16c94a0450ed4bb6dab563740d",
    "UI/src/routes/accounting/balanceSheet/balanceInputs/EditableSection.svelte": "b7d892a8b298248a9f028c3e3c9988a42848bf3b0b5153aa08690111b71eef25",
    "UI/src/routes/accounting/balanceSheet/balanceInputs/NumericInput.svelte": "a50bcd1a4fab2f6da29ca726604bc69228c3b12dcc45cafae45a1d9a4d20ae73",
    "UI/src/routes/accounting/customersInvoice/CustomersInvoiceList.svelte": "5d9c9df2b2e8c6d57cbfb7736ca4da707b8b0ced3954317e456e2c52ddf5aade",
    "UI/src/routes/accounting/customersInvoice/modals/EditModal.svelte": "9af750273290f04d6237392c0306a167616732dc5b1cf14887d76b073aacb8c6",
    "UI/src/routes/accounting/payment/category/Categories.svelte": "e358b8768d55f757cada0af4dabd2443e2168248057f5442f428a6bed58ad768",
    "UI/src/routes/accounting/payment/category/modals/EditModal.svelte": "270b2663c133c813a352959e9b4177ae4f6361ce879537242a7d9af06bed51a8",
    "UI/src/routes/accounting/payment/modals/partials/meta-payment-categories.svelte": "f308dd00d75f43f09aa620da3aea449bfa3a3fc42f60e037c6d72ac670a50602",
    "UI/src/routes/accounting/suppliers-and-customers/detail/sections/Info.svelte": "424373aefff1e91347540e0c6f6236fd0a8bafc7e1859e559c3d3420b88602e6",
    "UI/src/routes/accounting/suppliers-and-customers/partials/supplier-customer-form.svelte": "05845d83a87bccabdf06c33a7cef340d146030056e6209f629da5f3ed9f92d0f",
    "UI/src/routes/accounting/suppliersInvoice/SuppliersInvoiceList.svelte": "d75d7352440b1a92ffe9b392b0cb0757605a96e3f64d638891a9db209872c5d5",
    "UI/src/routes/accounting/suppliersInvoice/modals/EditModal.svelte": "edaee3b087e7bc1d36b324e8328cb337979af4c2ac9ccea2bc4959ba0f86a6f6",
    "UI/src/routes/profile/sections/Settings.svelte": "f620ddf62f5a04e8b8b978674e32338496dcd55e15a290a9cb8804b21e28fe2e",
    "UI/src/utils/Permissions.js": "100a24508b57b6c73a323ab4f128ee2c79aef3212a2cd0178172199d59e51854"
});
const reviewedFiles = accountingBalanceReviewedSources;

export function accountingBalancePages(id, report, source) {
    if (id !== 'accounting-balance-manage') return [];
    if (report?.id !== id || report.status !== 'passed' || report.backend !== 'real' ||
        report.fixture_version !== 8 || report.fixture_profile !== 'baseline' ||
        report.capture_format !== 'full-hd-v1' || report.viewport?.width !== 1920 ||
        report.viewport?.height !== 1080 || report.device_scale_factor !== 1)
        throw new Error('Accounting workflow requires passed real backend, baseline fixture 7 and Full HD DPR 1');
    const [prefix, checkpoints] = accountingBalanceCaptureSpecs[id];
    if (!Array.isArray(report.screenshots) || report.screenshots.length !== checkpoints.length ||
        report.screenshots.some((capture, index) => capture?.checkpoint !== checkpoints[index] ||
            capture.path !== prefix + (index + 1) + '.png' || capture.master?.width !== 1920 ||
            capture.master?.height !== 1080))
        throw new Error('Accounting captures differ from the exact reviewed checkpoints');
    if (Object.entries(accountingBalanceExpectedOutcomes[id]).some(([key, value]) =>
        report.accounting_balance_manage?.[key] !== value))
        throw new Error('Accounting outcomes differ from the reviewed persisted example');
    if (!report.external_gaps?.some(gap => gap.operation === 'active-invoice-xml-and-sdi-delivery' &&
        gap.status === 'needs_external_verification'))
        throw new Error('External invoice gap must remain explicit');
    const evidence = accountingBalanceSourceContracts[id].map(args => {
        const reference = source(report, ...args);
        if (reference.sha256 !== report.source_hashes?.[args[0]] ||
            reference.canonical_source_sha256 !== reviewedFiles[args[0]])
            throw new Error('Accounting source changed; review required: ' + args[0]);
        return reference;
    });
    return accountingBalanceDraftPages().map(({imageNumbers, supported, external, ...page}) => ({...page,
        body: supported ? page.body.replace(pending, '') : page.body,
        evidence, screenshots: supported ? imageNumbers.map(number => report.screenshots[number - 1]) : [],
        status: page.status === 'unsupported' ? 'unsupported' : supported ? 'verified' : external ? 'needs_external_verification' : 'pending',
        reason: supported ? '' : page.reason,
    }));
}

// Discovery describes draft dependencies only. It never supplies a workflow
// report, calls a verified builder, or attests that a browser procedure ran.
export function accountingBalanceEditorialContracts() {
    return accountingBalanceDraftPages().map(page => ({
        path: page.path, id: page.id, title: page.title,
        status: page.status === 'unsupported' ? 'unsupported' : page.external ? 'needs_external_verification' : 'pending',
        reason: page.reason || 'Procedura in attesa di esecuzione e revisione delle fonti.',
        recipe_module: 'docs/manuale/accounting-balance-recipes.mjs', verified: false,
        source_contracts: accountingBalanceSourceContracts['accounting-balance-manage']
            .map(([path, symbol, length]) => ({path, symbol, length,
                reviewed_sha256: accountingBalanceReviewedSources[path]})),
    }));
}
