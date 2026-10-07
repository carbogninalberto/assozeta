// Source-reviewed Italian drafts; publication requires this exact real workflow.
import {registrationFormsSourceContracts} from '../../selfhost/tests/browser/manuale/registration-forms-sources.mjs';

export const registrationFormsCaptureSpecs = {'registration-forms-manage': [
    'images/tutorials/moduli-iscrizione/', [
        'owner-registration-settings-and-three-enabled-types',
        'only-socio-e-tesserato-enabled-before-save',
        'single-association-fee-thirty-before-save',
        'custom-section-title-text-and-both-only-visibility',
        'optional-shirt-size-field-label-placeholder-and-help',
        'registration-type-persists-after-reload',
        'custom-section-uppercase-title-and-visibility-persist',
        'custom-field-properties-persist-after-reload',
        'members-book-current-registration-link-action',
        'registration-share-dialog-clipboard-and-options',
        'public-registration-start-shows-configured-single-type',
        'public-registration-personal-data-shows-shirt-size-without-submission',
        'reader-registration-save-disabled',
    ],
]};
export const registrationFormsExpectedOutcome = Object.freeze({
    configured_type: 'associate-membership', subscription_fee: '30.00', membership_fee: '0.00',
    quotes_enabled: true, multiple_fees_exercised: false,
    section_name: 'MATERIALE PER ALLENAMENTO', section_text: 'Porta borraccia e abbigliamento comodo.',
    section_both_visible: true, section_members_visible: false, section_athletes_visible: false,
    field_type: 'text', field_label: 'Taglia maglietta', field_placeholder: 'Esempio: M',
    field_helper: 'Indica la taglia desiderata.', field_required: false,
    persisted_after_reload: true, copied_link_matches_display: true, copied_link_is_current_registration: true,
    public_config_matches_saved: true, public_custom_field_visible: true,
    reader_template_write_status: 403, reader_save_disabled: true, denied_write_preserved_configuration: true,
    registration_submissions: 0, invitation_dispatches: 0, external_share_actions: 0,
    subscription_records_unchanged: true, existing_payment_ids_unchanged: true, baseline_restored: true,
});
const pending = '<Note>Bozza in attesa di prova: i passaggi e le immagini previste richiedono la verifica del flusso reale prima della pubblicazione.</Note>\n\n';
const tutorial = 'tutorials/come-creare-moduli-iscrizione-personalizzati.mdx';
const faq = 'faq/come-condividere-il-link-iscrizioni.mdx';
const scenarioId = 'registration-forms-manage';

export function registrationFormsDraftPages() {
    const image = (n, caption) => `<Frame>![${caption}](/${registrationFormsCaptureSpecs[scenarioId][0]}${n}.png)</Frame>`;
    const step = (title, body) => `<Step title="${title}">\n${body}\n</Step>`;
    const steps = (...items) => `<Steps>\n${items.join('\n')}\n</Steps>`;
    const page = (path, title, id, level, intent, body, imageNumbers = [], reviewStatus = 'verified', reason = '') => ({
        path, title, id, intent, body: '#'.repeat(level) + ' ' + title + '\n\n' + pending + body,
        imageNumbers, reviewStatus, status: 'pending', reason: reason || 'Richiede la prova browser reale di questa sezione.',
    });
    const open = step('Apri il Modulo Iscrizioni', `Accedi come proprietario dell'associazione.
Dal menu laterale apri **Organizzazione → Modulo Iscrizioni**: il titolo della pagina è **Modulo Iscrizione**.
Trovi le schede **Impostazioni**, **Quote**, **Sezioni Modulo** e **Campi Aggiuntivi**.
Nell'esempio lavoriamo sull'**Associazione Sportiva Aurora**.
${image(1, 'Impostazioni del modulo Aurora con le tre tipologie di iscrizione disponibili')}`);
    const types = step('Scegli le tipologie da raccogliere', `Nella scheda **Impostazioni**, il riquadro **Accetta iscrizioni di** offre **Socio**, **Socio e Tesserato** e **Tesserato**.
Per l'esempio lasciamo selezionato soltanto **Socio e Tesserato**, togliendo le altre due spunte.
La selezione cambia le opzioni del modulo online; non converte il tipo delle iscrizioni già presenti.
${image(2, 'Solo Socio e Tesserato selezionato prima del salvataggio')}`);
    const save = step('Salva e ricontrolla', `Premi **Salva** in alto nella pagina e attendi **Modulo d'iscrizione aggiornato correttamente**.
Ricarica la pagina: nella scheda **Impostazioni** rimane selezionato soltanto **Socio e Tesserato**.
Il salvataggio conserva insieme tipologie, quote, sezioni e campi aggiuntivi: controlla le altre schede prima di confermarlo.
${image(6, 'Tipologia di iscrizione conservata dopo il salvataggio e il ricaricamento')}`);
    const fee = step('Imposta la quota associativa semplice', `Apri **Quote** e lascia attiva **Associa quote iscrizione**.
Nel riquadro **Quota associativa** inserisci **30,00** nel campo **Quota**. Nell'esempio **Quota Tesseramento** resta **0,00**, e **Quote multiple** rimane disattivato.
L'interruttore collega la gestione delle quote alle iscrizioni; impostare l'importo non incassa una quota esistente.
${image(3, 'Quota associativa semplice di 30,00 e quota tesseramento di 0,00 prima del salvataggio')}`);
    const feeSaved = step('Verifica la quota conservata', `Premi **Salva**, attendi la conferma, poi ricarica e riapri **Quote**.
Controlla che **Quota associativa** sia **30,00** e **Quota Tesseramento** sia **0,00**.
Le iscrizioni e i pagamenti già presenti nell'esempio restano quelli originali.`);
    const section = step('Aggiungi una sezione con titolo e testo', `Apri **Sezioni Modulo**. Oltre a **Sezioni base**, con **REGOLAMENTO DELL'ASSOCIAZIONE** e **RICHIESTA**, trovi **Sezioni aggiuntive**.
Nel riquadro **Aggiungi nuova sezione** premi **Aggiungi**.
Scrivi **Materiale per allenamento** in **Titolo** e **Porta borraccia e abbigliamento comodo.** in **Testo**.
Seleziona **Visibile a SOCI E TESSERATI** e lascia senza spunta le opzioni per i soli soci e i soli tesserati.
${image(4, 'Titolo, testo e visibilità della nuova sezione per soci e tesserati')}`);
    const sectionSaved = step('Salva e verifica la sezione', `Premi **Salva**, ricarica e riapri **Sezioni Modulo**.
Il titolo dell'esempio viene conservato in maiuscolo: **MATERIALE PER ALLENAMENTO**.
Ricontrolla il testo e la visibilità per **SOCI E TESSERATI**.
${image(7, 'Sezione con titolo in maiuscolo e visibilità conservati dopo il ricaricamento')}
<Warning>L'editor controlla che ogni sezione aggiuntiva abbia titolo e testo compilati. Il messaggio previsto per i campi vuoti è **Alcune sezioni contengono errori. Controlla i campi evidenziati.**; il caso non viene provocato in questa sequenza.</Warning>
<Note>Il cestino richiede conferma per togliere una sezione. Eliminazione, riordino e modifiche dei testi base richiedono una prova dedicata; non sono eseguiti nell'esempio.</Note>`);
    const field = step('Aggiungi il campo Taglia maglietta', `Apri **Campi Aggiuntivi**. In **Campi disponibili** sono proposti **Testo**, **Email**, **Descrizione** e **Data**.
Premi **Testo**, poi clicca sul campo inserito nell'**Anteprima** per aprire le proprietà.
Imposta **Etichetta**: **Taglia maglietta**, **Placeholder**: **Esempio: M** e **Etichetta aiuto**: **Indica la taglia desiderata.**.
Disattiva **Obbligatorio**: l'atleta potrà lasciare il campo vuoto.
${image(5, 'Proprietà del campo aggiuntivo facoltativo Taglia maglietta')}`);
    const fieldSaved = step('Conserva il campo aggiuntivo', `Premi **Salva** nella pagina **Modulo Iscrizione**, ricarica e riapri **Campi Aggiuntivi**.
Clicca sul campo nell'**Anteprima** e ricontrolla etichetta, placeholder, aiuto e interruttore **Obbligatorio**.
La piccola icona di salvataggio nelle proprietà chiude la modifica del campo: per conservare il modulo usa il **Salva** della pagina.
${image(8, 'Campo Taglia maglietta e proprietà conservati dopo il ricaricamento')}
<Warning>**Reset** svuota i campi aggiuntivi nella modifica in corso. Controlla il risultato prima di salvare; il ripristino e il riordino dei campi non sono dimostrati qui.</Warning>`);
    const book = step('Apri Libro Soci', `Dal menu **Organizzazione** apri **Libro Soci**.
In alto premi **Condividi link iscrizioni**. Il comando vicino **Condividi link pre-iscrizioni** riguarda invece la stagione successiva.
${image(9, 'Libro Soci con il comando Condividi link iscrizioni')}`);
    const dialog = step('Controlla il link della tua associazione', `Si apre **Condividi link iscrizioni**, con il QR code, il campo del link, l'icona per copiarlo e **Apri**.
Sono presenti anche **Whatsapp**, **Invia Email**, **QR code** e **Chiudi**.
Il link delle iscrizioni correnti usa il nome utente dell'associazione. Nell'esempio link e QR sono oscurati nell'immagine: controlla quelli della tua istanza.
${image(10, 'Finestra Condividi link iscrizioni con link e QR oscurati per la cattura')}`);
    const copy = step('Copia il link e controlla gli appunti', `Nella finestra **Condividi link iscrizioni**, premi l'icona con i due fogli accanto al campo del link.
Attendi **Link copiato negli appunti** e incolla il contenuto nel messaggio o nel documento che stai preparando.
Controlla che il collegamento incollato corrisponda a quello mostrato nella finestra e che riguardi le iscrizioni correnti.
La copia negli appunti non invia messaggi o email.
${image(10, 'Controlli per copiare il link delle iscrizioni correnti')}`);
    const publicStart = step('Apri il modulo senza inviare una richiesta', `Nella finestra premi **Apri**: il modulo si apre in una nuova scheda.
Controlla il nome **Associazione Sportiva Aurora**. Con una sola tipologia attiva compare **Stai compilando l'iscrizione come Socio e Tesserato**.
La pagina iniziale comprende la scelta dell'account e le informazioni sulle quote configurate.
${image(11, 'Avvio del modulo pubblico Aurora con la sola tipologia Socio e Tesserato')}`);
    const publicField = step('Controlla il campo nell’anagrafica', `Nell'esempio usiamo **Continua come** con l'account dimostrativo già collegato e osserviamo il passaggio dell'anagrafica.
Il campo **Taglia maglietta** mostra **Esempio: M** e **Indica la taglia desiderata.**, senza obbligo di compilazione.
La verifica termina in questo passaggio: non vengono compilati dati di una nuova persona, firmati documenti o inviate richieste.
${image(12, 'Campo aggiuntivo nel modulo pubblico, con gli altri dati personali oscurati')}`);
    const limits = `<Note>Questa procedura usa il proprietario. Il collaboratore dimostrativo può consultare il modulo, ma il pulsante superiore **Salva** è disattivato e il server rifiuta il suo tentativo di modifica.
La disponibilità della voce **Modulo Iscrizioni** dipende anche dal piano: nel menu del piano gratuito conduce all'aggiornamento del piano. Non dedurre da questa prova differenze ulteriori tra piani.</Note>
${image(13, 'Modulo consultato dal collaboratore con il pulsante Salva disattivato')}`;
    const notRun = 'La variante richiede una propria prova browser e controlli dello stato salvato.';
    return [
        page(tutorial, 'Indice', 'indice', 1, 'registration.forms.overview', `Il tutorial distingue configurazione dei periodi, tipologie di iscrizione, quote, sezioni e campi aggiuntivi.
La procedura dimostrativa configura una sola tipologia, una quota associativa semplice, una sezione di testo e il campo facoltativo **Taglia maglietta**, poi controlla il modulo tramite il link.
<Note>Periodi fiscali, quote multiple, modelli dell'Archivio e invio di una nuova iscrizione hanno verifiche separate. L'esempio di personalizzazione non certifica tutte queste varianti.</Note>`, [], 'pending', 'L’indice comprende argomenti che richiedono prove separate.'),
        page(tutorial, "Configurare l'anno sportivo, l'anno fiscale e le causali per i pagamenti", 'configurare-l-anno-sportivo-l-anno-fiscale-e-le-causali-per-i-pagamenti', 1, 'organization.settings.read', `Prima di raccogliere iscrizioni controlla **Profilo → Generali**.
**Anno fiscale** e **Stagione sportiva** sono due configurazioni distinte: il primo offre periodi predefiniti e **Altro Periodo (personalizzato)**, la seconda indica mese e giorno iniziale e, con **Dura meno di un anno**, la fine.
Consulta [Come cambiare anno sportivo e fiscale](/faq/come-cambiare-anno-sportivo-fiscale) per la procedura dedicata.
<Note>Questa sequenza non modifica i periodi. La scelta dell'anno fiscale non equivale all'impostazione automatica della stagione sportiva.</Note>`, [], 'pending', notRun),
        page(tutorial, 'Causali predefinite', 'causali-predefinite', 2, 'payments.categories.update', `Prima di collegare quote e pagamenti, controlla le causali dell'associazione in **Contabilità → Pagamenti** e le impostazioni contabili.
La causale descrive il motivo del pagamento; il conto indica dove viene registrato il denaro.
<Note>Creazione di una causale e scelta della causale predefinita per le nuove iscrizioni richiedono una procedura dedicata. La modifica del modulo dimostrata qui non cambia causali o conti.</Note>`, [], 'pending', 'Le causali e la loro applicazione a nuovi pagamenti non sono esercitate.'),
        page(tutorial, 'Durata di iscrizioni e Tesseramenti', 'durata-di-iscrizioni-e-tesseramenti', 2, 'organization.season.update', `In **Profilo → Generali** trovi i menu separati **Durata delle iscrizioni** e **Durata dei tesseramenti**.
Le scelte comprendono **Un anno solare dalla data di iscrizione**, **Da inizio stagione sportiva** e **Dall'iscrizione al termine della stagione sportiva**.
Controlla prima le date della stagione e poi il criterio di durata. Il tipo **Socio e Tesserato** nel modulo è una scelta diversa dalla durata.
<Note>Le durate non vengono cambiate in questo esempio; consultare la procedura sui periodi per il salvataggio e i limiti delle nuove iscrizioni.</Note>`, [], 'pending', notRun),
        page(tutorial, "Configurare il modulo d'iscrizione", 'configurare-il-modulo-d-iscrizione', 1, 'registration.forms.configure', steps(open, types, save) + '\n\n' + limits, [1, 2, 6, 13]),
        page(tutorial, 'Cosa scegliere per le iscrizioni?', 'cosa-scegliere-per-le-iscrizioni', 2, 'registration.forms.types', `Scegli le categorie che la tua associazione raccoglie: **Socio**, **Socio e Tesserato**, **Tesserato**.
L'applicazione propone queste tre categorie; la scelta deve rispecchiare le procedure dell'associazione.
Non usare il menu come una valutazione automatica della posizione della persona presso una federazione.
${steps(types, save)}
<Tip>Con una sola categoria abilitata il modulo indica direttamente il tipo scelto. Controlla il risultato dal pulsante **Apri** nella finestra del link.</Tip>`, [2, 6]),
        page(tutorial, 'Impostare le quote', 'impostare-le-quote', 1, 'registration.forms.fees', steps(open, fee, feeSaved) + `\n\n<Note>Questa prova conserva la quota semplice e verifica che i pagamenti esistenti non cambino. La creazione automatica di pagamenti per una nuova iscrizione, l'incasso, le ricevute e Stripe richiedono i rispettivi flussi; non vengono eseguiti qui.</Note>`, [1, 3]),
        page(tutorial, 'Impostare quote multiple', 'impostare-quote-multiple', 2, 'registration.forms.multiple-fees', `La scheda **Quote** offre **Quote multiple** separatamente per **Quota associativa** e **Quota Tesseramento**.
L'editor delle quote associative include **Aggiungi quota**, **Nome quota**, **Quota** e **Parziale**, con opzioni per collegare una quota antecedente e rateizzare.
<Note>La configurazione di quote multiple o parziali e l'assegnazione delle quote successive richiedono una prova dedicata. L'esempio mantiene entrambe le opzioni multiple disattivate e non dimostra una scadenza a sei mesi o un pagamento automatico.</Note>`, [], 'pending', 'Non sono creati piani multipli, quote parziali o pagamenti successivi.'),
        page(tutorial, "Visualizzare un'anteprima del modulo d'iscrizione digitale", 'visualizzare-un-anteprima-del-modulo-d-iscrizione-digitale', 3, 'registration.forms.preview', `Salva prima la configurazione: il modulo online legge i dati conservati sul server.
${steps(book, dialog, publicStart, publicField)}
<Note>Su un computer la pagina del modulo offre anche i riquadri **Anteprima Modulo Iscrizioni** e **Anteprima Modulo Preiscrizioni**, ciascuno con **Apri**. L'esempio utilizza la nuova scheda del link corrente; la preview integrata e le preiscrizioni restano da verificare separatamente.</Note>`, [9, 10, 11, 12]),
        page(tutorial, "Personalizzare il modulo d'iscrizione", 'personalizzare-il-modulo-d-iscrizione', 1, 'registration.forms.personalize', `Usa **Sezioni Modulo** per aggiungere testi da leggere durante l'iscrizione; usa **Campi Aggiuntivi** per raccogliere informazioni nell'anagrafica.
Nell'esempio la sezione **MATERIALE PER ALLENAMENTO** riguarda i soci e tesserati, mentre **Taglia maglietta** è un campo facoltativo.
${steps(section, field, save)}
<Note>Modifiche e anteprime nell'editor non sostituiscono **Salva**. Prima di distribuirlo controlla il modulo online con la tipologia interessata.</Note>`, [4, 5, 6]),
        page(tutorial, "Sezioni del modulo d'iscrizione", 'sezioni-del-modulo-d-iscrizione', 2, 'registration.forms.sections', steps(open, section, sectionSaved), [1, 4, 7]),
        page(tutorial, 'Campi aggiuntivi', 'campi-aggiuntivi', 2, 'registration.forms.fields', steps(open, field, fieldSaved, publicField) + '\n\n<Tip>Per una scelta breve, come la taglia, puoi usare **Testo**. Le altre tipologie disponibili, l’obbligatorietà attiva, la cancellazione e il riordino richiedono prove proprie prima di considerare verificati i relativi risultati.</Tip>', [1, 5, 8, 12]),
        page(tutorial, 'Creare un modulo completamente personalizzato', 'creare-un-modulo-completamente-personalizzato', 1, 'documents.templates.create', `Il modello dell'**Archivio → Modulistica** è un documento da generare, distinto dal modulo online che raccoglie iscrizioni.
${steps(step('Prepara un modello nell’Archivio', 'In **Modulistica** il comando con il simbolo più è **Modello** e apre **Aggiungi Modello**. Compila **Nome del modello** e **Contenuto del documento**.'),
step('Scegli intestazione, piè di pagina e dati dinamici', 'Controlla **Includi intestazione nel documento**, **Includi piè di pagina nel documento** e **Personalizza Intestazione e Piè di Pagina**. Nell’editor il simbolo **@** propone dati dinamici da inserire durante la generazione.'),
step('Controlla prima di salvare', 'Il nome e il contenuto sono obbligatori. Usa **Salva** e ricontrolla il modello nell’elenco prima di generare un documento.'))}
<Note>Questi controlli sono presenti nell'editor del modello; creazione, modifica, sostituzione dei dati dinamici e PDF finale non sono eseguiti dalla procedura di personalizzazione del modulo online.</Note>`, [], 'pending', 'Manca una prova browser e PDF dedicata ai modelli dell’Archivio.'),
        page(tutorial, 'Come assegnare un modulo personalizzato?', 'come-assegnare-un-modulo-personalizzato', 2, 'documents.templates.generate', `Nella scheda della persona, la sezione **Documenti** offre **Genera da Modello**.
La finestra **Genera Documento** contiene **Seleziona il modello**, **Annulla** e **Genera Documento**.
<Note>La generazione del PDF e il collegamento del documento alla persona richiedono una verifica dedicata. Questa funzione crea un documento; non configura le quote del modulo online e non sostituisce da sola la procedura standard d'iscrizione.</Note>`, [], 'pending', 'Generazione del PDF, contenuto finale e associazione alla persona non dimostrati.'),
        page(tutorial, 'Conclusione', 'conclusione', 1, 'registration.forms.overview', `Dopo aver salvato, ricontrolla tipologie, quota semplice, testi e campi aggiuntivi. Apri il link dell'associazione e verifica il risultato prima di distribuirlo.
<Note>Il percorso dimostrativo termina prima dell'invio di una nuova iscrizione. Periodi, quote multiple, pagamenti online e modelli dell'Archivio restano procedure separate, con i propri controlli.</Note>`, [], 'pending', 'Il riepilogo comprende varianti ancora da verificare.'),
        page(faq, 'Condividere il link per le iscrizioni', 'condividere-il-link-per-le-iscrizioni', 1, 'registration.links.read', `Il link delle iscrizioni correnti apre il modulo online della tua associazione. Puoi copiarlo dalla finestra di **Libro Soci** e controllarlo con **Apri**.
La procedura seguente prepara il collegamento e ne controlla il modulo; l'invio ai destinatari richiede il canale scelto.
${image(9, 'Libro Soci dell’associazione Aurora con il comando per condividere il link')}`, [9]),
        page(faq, 'Dove trovo il link di iscrizione?', 'dove-trovo-il-link-di-iscrizione', 2, 'registration.links.read', steps(book, dialog) + '\n\n<Info>Lo stesso link corrente compare nella pagina **Organizzazione → Modulo Iscrizioni**. Non confonderlo con **Link Preiscrizioni Prossima Stagione**.</Info>', [9, 10]),
        page(faq, 'Modalità di condivisione', 'modalita-di-condivisione', 2, 'registration.links.read', `La finestra propone la copia negli appunti, **Apri**, **Whatsapp**, **Invia Email** e **QR code**.
${steps(dialog, copy)}
<Note>È verificata la preparazione tramite copia e apertura. La consegna di messaggi/email, il QR stampato e la scansione con un telefono richiedono verifiche esterne o una prova dedicata.</Note>`, [10]),
        page(faq, '1. Copia il link', '1-copia-il-link', 3, 'registration.links.copy', steps(book, dialog, copy), [9, 10]),
        page(faq, '2. WhatsApp', '2-whatsapp', 3, 'registration.links.whatsapp', `Il pulsante nell'applicazione si chiama **Whatsapp** e prepara un collegamento al servizio con un testo contenente il nome dell'associazione e il link.
<Note>La scelta del destinatario e la spedizione dipendono da WhatsApp. Apertura del servizio, invio e ricezione del messaggio non sono provati in questa procedura.</Note>`, [], 'needs_external_verification', 'Servizio WhatsApp, invio e consegna non esercitati.'),
        page(faq, '3. Email', '3-email', 3, 'registration.links.email', `**Invia Email** apre il programma di posta tramite un collegamento **mailto**, con oggetto e testo preparati.
Il destinatario iniziale è dimostrativo: sostituiscilo con quello corretto nel programma di posta prima dell'invio.
<Warning>Il pulsante non dimostra una spedizione da parte dell'applicazione. Invio e consegna dipendono dal programma e dal servizio di posta.</Warning>
<Note>Nessuna email viene inviata durante questa verifica.</Note>`, [], 'needs_external_verification', 'Client email, destinatari effettivi, invio e consegna non esercitati.'),
        page(faq, '4. QR Code', '4-qr-code', 3, 'registration.links.qr', `La finestra mostra un QR code del link corrente. Il pulsante **QR code** prepara una finestra di stampa con il codice e il nome dell'associazione.
<Note>La visualizzazione del QR è presente nella finestra del link; stampa, scansione da telefono e qualità su volantini non sono esercitate. Il controllo non offre qui un pulsante dedicato per scaricare un PNG.</Note>`, [], 'pending', 'Mancano stampa e scansione reali; il download diretto come immagine descritto nel vecchio testo non è dimostrato.'),
        page(faq, 'Cosa vede chi apre il link?', 'cosa-vede-chi-apre-il-link', 2, 'registration.forms.preview', steps(publicStart, publicField) + `\n\n<Info>Per cambiare le opzioni del modulo apri **Organizzazione → Modulo Iscrizioni**. Consulta il [tutorial sui moduli personalizzati](/tutorials/come-creare-moduli-iscrizione-personalizzati).</Info>
<Note>I passaggi successivi prevedono anagrafica, clausole e firma, certificato e riepilogo. Il percorso dimostrato qui si ferma all'anagrafica: invio, notifica e approvazione di una nuova richiesta non sono verificati da questa apertura.</Note>`, [11, 12]),
    ];
}

// These canonical hashes seal the semantic review, separately from capture hashes.
export const registrationFormsReviewedSources = Object.freeze({
    "UI/src/components/Sidebar.svelte": "5fd791241452c301697730caf21a841c29a186876c33834b579d7846610d2568",
    "UI/src/routes.js": "58bddc98548095f14f39fa3141277de8cd00e2133faf49f6761131b109d254b0",
    "UI/src/routes/association/Members/subscription/Template.svelte": "e01e73eb4435b7ea9ecf35971dd59182c4b6752fd404921e44a0770a64682903",
    "UI/src/routes/association/Members/subscription/partials/TypesModule.svelte": "f83c5113b5bf8ce41420ceafe5a8311802284cb4c9e4ca2891e94c0cca5924bd",
    "UI/src/routes/association/Members/subscription/partials/QuoteIscrizione.svelte": "c9d41719a3ec8cff5fd5d7896564d7a6c471f8904cae7d3765ceb36ae2d0351c",
    "UI/src/routes/association/Members/subscription/partials/MultipleQuotesSubscription.svelte": "bd2fb5fd7c6806d6790f4c6bd45f8da7d0d509b2914060a2dd32beedac349fe9",
    "UI/src/routes/association/Members/subscription/partials/ModuleSections.svelte": "93b6aa5457512d0c4a7180de0c495e18f77875138e0cbc64a93da15867b579d9",
    "UI/src/routes/association/Members/subscription/partials/AdditionalFields.svelte": "cb891aa575f32dcd3dde1e51c7d45dec9c2527875c96d7beb3f110b933617872",
    "UI/src/components/formBuilder/composer-sidebar.svelte": "a18c05c665ae9b954989076a54a38ba5198ac4e52265fca5ec80cdc3e819492f",
    "UI/src/components/formBuilder/composer-preview.svelte": "9ca8a42453e4dddf78eb261e731c55b008c86f052a0da0bb8dda91b745b6d829",
    "UI/src/components/formBuilder/inputs.js": "e6cebe776d4449644dcd0e63cfa2217ad57278f0de6e3becaa5a9d5152aee08e",
    "UI/src/components/formBuilder/preview-blocks/text-input.svelte": "5d6a0020e819ef413c5c3e407a664957526378312c059ca4410a659b27df6ba1",
    "UI/src/routes/association/Members/MembersBook.svelte": "e123edd1de7d765f5a9b98d9ad019940ed2de36c40df3d78d1e605205849ae7d",
    "UI/src/components/modals/ShareModuleSubscriptionLink.svelte": "999bf9e16be7b8389a0181cd7f5c45f383dcb9c690925b653b9bf7d3333261ea",
    "UI/src/routes/association/Members/subscription/share/link-share-header.svelte": "384f60f3b95d3d1acaecf7ede4d2d01d9d29763740a31c84d0c87994a1a30246",
    "UI/src/routes/subscribe/Subscribe.svelte": "631e8c05133e9267e9e82bec8a941b61ac3bf5afbb7b79548156f32e63e6160c",
    "UI/src/routes/subscribe/wizard/Step0.svelte": "764b6c9c39b7845650f0e8fec792c051d6d9fe259f4fd32a9e630726fc5ad737",
    "UI/src/routes/subscribe/wizard/Step1.svelte": "50bbcd39de0d80293f59f6a0953a2498efd488ebf062582ae6b964c4b9a4c4f5",
    "UI/src/routes/subscribe/wizard/partials/additional-fields.svelte": "25eacb675ca1f76cf2ceae6af39126b8e32032acc6e395a414b087d838d311b3",
    "UI/src/routes/subscribe/wizard/Step2.svelte": "871d4ff3b52672efd93b0348c3b587d355aeaa31edd60696d777e1b5db35a128",
    "UI/src/utils/Permissions.js": "100a24508b57b6c73a323ab4f128ee2c79aef3212a2cd0178172199d59e51854",
    "BE/application/urls.py": "b475e7ed8c26a891854c4021d7308aefb192d2a26df8b3ee916a33f88e7cc359",
    "BE/application/views/profile_views.py": "37aca983a9edb10673bd76786b7f8efce7adfeaa129e9d4848df85ead31936d1",
    "BE/application/views/search_views.py": "0cc8b4378133ef496ae8d193ad0904456dc92f394f8a04aebf9a5e3d58e1e380",
    "BE/application/serializers/auth_serializers.py": "edecff4a6618fc1862dc681056df9ab699de1a8cbb80cc004cf3f5642b7a1798",
    "BE/application/models/user_models.py": "3834a5655391ca750830d03175d7f663276697e50ab3fcfffcfd9ad45b7b931c",
    "BE/application/permissions_registry.py": "b8318ad63daa9452ac736b2152449b14c49f73bc1417622a4181b7c3ceb3b1fa",
    "UI/src/routes/profile/sections/Settings.svelte": "f620ddf62f5a04e8b8b978674e32338496dcd55e15a290a9cb8804b21e28fe2e",
    "UI/src/routes/association/archive/TemplatesList.svelte": "2fb5b32a1326a36787f24bca61ad8a456f420c90742d8da30938fa3d72d6f173",
    "UI/src/routes/association/archive/detail/partials/template-form.svelte": "83348b084cf65b145bbd366c0f388abe6cdb8156064e07708346dc72d6141cad",
    "UI/src/routes/association/Members/detail/sections/Cloud.svelte": "dc79dccd5dd5c90a20903493c5483d8cb84eab327f33fc7ee3f47512e46d8896",
    "UI/src/routes/association/archive/modals/GenerateFromTemplateModal.svelte": "d68a581779ef24b65f2ed0afa0de99ee880079d1b7a6cab218f05b5dfa064c81",
    "BE/application/views/archive_views.py": "4507583ea5811c7a0287ea0e83456f6a5b1707efe38bb950cedb23a0efcfae96",
    "BE/docmanager/views/printing_views.py": "f9127869836c46408d9e5f48fc16733c6fce66efedb2e2fa7f96344095e01753"
});
const reviewedFiles = registrationFormsReviewedSources;

// Editorial discovery describes draft inputs only. It never invokes the
// evidence builder or confers verification, including on locally captured steps.
export function registrationFormsEditorialContracts() {
    return registrationFormsDraftPages().map(page => ({
        path: page.path, id: page.id, title: page.title,
        status: ['unsupported', 'needs_external_verification'].includes(page.reviewStatus) ? page.reviewStatus : 'pending',
        reason: page.reason || 'Richiede la prova browser reale di questa sezione.',
        recipe_module: 'docs/manuale/registration-forms-recipes.mjs', verified: false,
        source_contracts: registrationFormsSourceContracts.map(([path, symbol, length]) => ({
            path, symbol, length, reviewed_sha256: registrationFormsReviewedSources[path],
        })),
    }));
}

export function registrationFormsPages(id, report, source) {
    if (id !== scenarioId) return [];
    if (report?.id !== id || report.status !== 'passed' || report.backend !== 'real' ||
        report.fixture_version !== 8 || report.fixture_profile !== 'baseline' ||
        report.capture_format !== 'full-hd-v1' || report.viewport?.width !== 1920 ||
        report.viewport?.height !== 1080 || report.device_scale_factor !== 1)
        throw new Error('Registration forms require the passed real fixture7 baseline Full HD workflow');
    const [prefix, checkpoints] = registrationFormsCaptureSpecs[id];
    if (!Array.isArray(report.screenshots) || report.screenshots.length !== checkpoints.length ||
        report.screenshots.some((capture, n) => !capture || capture.path !== prefix + (n + 1) + '.png' ||
            capture.checkpoint !== checkpoints[n] || capture.master?.width !== 1920 || capture.master?.height !== 1080))
        throw new Error('Registration forms checkpoints differ from the reviewed steps');
    if (Object.entries(registrationFormsExpectedOutcome).some(([key, value]) => report.registration_forms_workflow?.[key] !== value))
        throw new Error('Registration forms outcomes differ from the reviewed workflow');
    if (!report.external_gaps?.some(gap => gap.operation === 'registration-share-delivery-print-and-submission' &&
        gap.status === 'needs_external_verification')) throw new Error('Registration share external gap must remain explicit');
    const evidence = registrationFormsSourceContracts.map(args => {
        const ref = source(report, ...args);
        if (!report.source_hashes?.[args[0]] || ref.sha256 !== report.source_hashes[args[0]])
            throw new Error('Registration forms source was not captured: ' + args[0]);
        if (ref.canonical_source_sha256 !== reviewedFiles[args[0]])
            throw new Error('Registration forms source changed; review required: ' + args[0]);
        return ref;
    });
    return registrationFormsDraftPages().map(({imageNumbers, reviewStatus, ...page}) => ({
        ...page, body: reviewStatus === 'verified' ? page.body.replace(pending, '') : page.body,
        evidence, screenshots: imageNumbers.map(n => report.screenshots[n - 1]), status: reviewStatus,
        reason: reviewStatus === 'verified' ? '' : page.reason,
        kind: reviewStatus === 'verified' ? 'browser-workflow' : 'unexercised-variant',
    }));
}
