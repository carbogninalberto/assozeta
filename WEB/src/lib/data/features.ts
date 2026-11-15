/**
 * Content registry for the Funzionalità detail pages (/funzionalita/[slug]).
 *
 * Every claim here comes from BRIEF section 5, the repository README or the
 * Italian user manual. Optional integrations (SMTP, Stripe, Google Calendar,
 * sign-in, AI agent) are described as needing credentials on the instance.
 */
import type { ContentPage } from './routes';

export const FEATURE_PAGES: Record<string, ContentPage> = {
	'soci-e-tesserati': {
		slug: 'soci-e-tesserati',
		title: 'Gestione soci e tesserati per ASD e SSD | Assozeta',
		description:
			'Anagrafiche di soci, atleti, minori e tutori, tesseramenti, tessere digitali, import da CSV e XLSX, libro soci e archivio.',
		heading: 'Soci e tesserati',
		intro:
			'Il libro soci di Assozeta tiene insieme anagrafiche, iscrizioni, rinnovi e tesseramenti. Ogni persona ha una scheda con i dati, i documenti, i certificati e lo storico delle attività, così la segreteria non ricopia lo stesso nome in elenchi diversi.',
		sections: [
			{
				heading: 'Anagrafiche di soci, atleti, minori e tutori',
				body: 'Una sola scheda raccoglie dati anagrafici, recapiti, residenza e informazioni sportive della persona. Per i minori il tutore è collegato all’iscrizione, con i suoi recapiti per le comunicazioni. Il codice fiscale si può inserire a mano oppure calcolare dai dati anagrafici già compilati.',
				list: [
					'Tipologie distinte: Socio, Socio e Tesserato, Tesserato',
					'Ruolo del socio e quote previste per ogni tipologia',
					'Minori collegati al tutore e ai suoi recapiti',
					'Calcolo del codice fiscale dai dati della scheda',
					'Tag per organizzare gruppi, squadre e corsi'
				]
			},
			{
				heading: 'Iscrizioni, rinnovi e tessere',
				body: 'Ogni iscrizione ha uno stato leggibile: non firmata, in attesa, accettata, rifiutata o ritirata. Aprire una richiesta, approvarla e incassare la quota restano passaggi separati, quindi sai sempre a che punto sei. La durata di iscrizioni e tesseramenti segue la stagione sportiva oppure l’anno dalla data di iscrizione, secondo le impostazioni dell’associazione.',
				list: [
					'Approvazione di una richiesta dalla riga dell’elenco',
					'Tessera con numero, tipologia e QR code facoltativo',
					'Anteprima e stampa della tessera dal browser',
					'Rinnovo con date coerenti con la stagione',
					'Libro soci e scheda Tesserati sugli stessi dati'
				]
			},
			{
				heading: 'Ricerca, filtri e tag',
				body: 'La barra di ricerca trova una persona per nome o cognome. I filtri restringono l’elenco per periodo, stato dell’iscrizione, tag e situazione del certificato medico. I tag si applicano alle righe selezionate: sono un modo rapido per preparare un gruppo o una lista di contatti.',
				list: [
					'Ricerca per nome e cognome',
					'Filtri per periodo, stato, tag e certificato',
					'Assegnazione dei tag alle iscrizioni selezionate',
					'Eliminazione di un tag da tutte le iscrizioni',
					'Viste Libro soci, Tesserati, Bozze e Archivio'
				]
			},
			{
				heading: 'Importare i soci da CSV o XLSX',
				body: 'Puoi preparare un foglio con una riga per persona e caricarlo dall’applicazione. Le righe arrivano come bozze: colleghi le colonne ai campi, correggi quello che manca e approvi solo le iscrizioni valide. Prima di creare l’iscrizione, un controllo cerca duplicati con lo stesso nome, cognome e codice fiscale.',
				list: [
					'File XLSX oppure CSV con separatore punto e virgola',
					'Codice fiscale e CAP letti come testo, zeri compresi',
					'Collegamento delle colonne ai campi dell’applicazione',
					'Bozze da controllare prima dell’approvazione',
					'Verifica dei possibili duplicati'
				]
			},
			{
				heading: 'Archivio e storico delle attività',
				body: 'Le iscrizioni che non servono più si archiviano senza cancellarle: restano consultabili e si possono riportare nell’elenco attivo. Corsi, pagamenti e documenti contabili hanno archivi dedicati, così ogni dato si ritrova nella sezione che gli appartiene. Lo storico della persona raccoglie iscrizioni, presenze, quote e documenti nel tempo.',
				list: [
					'Archivio delle iscrizioni separato dall’elenco attivo',
					'Ripristino nell’elenco attivo quando serve',
					'Archivi distinti per corsi, pagamenti e documenti',
					'Ricerca nell’archivio con gli stessi filtri dell’elenco'
				]
			}
		]
	},

	'iscrizioni-online': {
		slug: 'iscrizioni-online',
		title: 'Iscrizioni online con moduli personalizzati | Assozeta',
		description:
			'Moduli pubblici personalizzabili con campi, allegati e firme: iscrizioni individuali, multiple e familiari, approvazione e raccolta documenti.',
		heading: 'Iscrizioni e moduli online',
		intro:
			'Raccogli le iscrizioni con un modulo pubblico personalizzato, condiviso come link o QR code. Le richieste arrivano nell’applicazione con dati, firma e documenti già ordinati, pronte da controllare e approvare.',
		sections: [
			{
				heading: 'Un modulo pubblico per la tua associazione',
				body: 'Il modulo di iscrizione vive a un indirizzo pubblico dell’istanza e si apre anche da un QR code. Prima di condividerlo scegli le tipologie accettate, le quote e i testi, perché la pagina legge la configurazione salvata. Chi compila non deve installare nulla: basta il browser di un telefono o di un computer.',
				list: [
					'Tipologie accettate: Socio, Socio e Tesserato, Tesserato',
					'Quote associative semplici oppure con più piani',
					'Apertura senza account, dal link o dal QR code',
					'Anteprima del modulo reale prima della diffusione'
				]
			},
			{
				heading: 'Campi, sezioni e firma con il form builder',
				body: 'Aggiungi campi all’anagrafica — testo, email, descrizione e data — e decidi quali sono obbligatori. Le sezioni del modulo contengono i testi da leggere prima di firmare, come il regolamento o una clausola sull’abbigliamento, e puoi limitarne la visibilità a una tipologia. La firma si raccoglie con il mouse o con il dito e resta allegata alla richiesta.',
				list: [
					'Campi aggiuntivi con etichetta, segnaposto e testo di aiuto',
					'Campi obbligatori che bloccano la compilazione',
					'Sezioni di testo per regolamento e clausole',
					'Visibilità delle sezioni per tipologia di iscrizione',
					'Firma raccolta su schermo e salvata con la domanda'
				]
			},
			{
				heading: 'Iscrizioni individuali, multiple e familiari',
				body: 'Il modulo gestisce una sola persona, più persone nella stessa compilazione oppure un nucleo familiare. Per i minori servono i dati del tutore, che restano collegati all’iscrizione e alle comunicazioni. I dati e i documenti raccolti finiscono direttamente nella scheda della persona, senza trascrizioni manuali.',
				list: [
					'Iscrizione di una singola persona',
					'Più iscrizioni nella stessa compilazione',
					'Percorso familiare con i dati del tutore',
					'Documenti e firme collegati alla richiesta',
					'Nessuna trascrizione nella scheda del socio'
				]
			},
			{
				heading: 'Approvare le richieste',
				body: 'Le domande arrivano con lo stato in attesa o non firmata e restano nell’elenco finché qualcuno le controlla. Apri la riga, verifica dati, firma, certificato e quota, poi approva: la persona risulta accettata. Firma, approvazione e incasso restano informazioni distinte, quindi vedi subito cosa manca.',
				list: [
					'Stati: non firmata, in attesa, accettata, rifiutata, ritirata',
					'Controllo di firma, documenti e quota prima di approvare',
					'Approvazione dalla riga dell’elenco',
					'Rifiuto e ritiro come operazioni distinte',
					'Permessi di lettura e modifica separati'
				]
			},
			{
				heading: 'Condividere il link e raccogliere i documenti',
				body: 'Dalla sezione iscrizioni copi il link del modulo, lo apri in una nuova scheda per verificarlo e poi lo condividi via WhatsApp o email, oppure stampi il QR code. I documenti allegati, certificato medico compreso, restano collegati all’iscrizione e si consultano dalla scheda.',
				list: [
					'Copia del link e apertura di verifica',
					'Condivisione tramite WhatsApp ed email',
					'QR code pronto per la stampa',
					'Allegati e certificati nella scheda della persona'
				]
			}
		],
		footnotes:
			'Il modulo pubblico non richiede servizi esterni. L’invio dei link via email e i promemoria dipendono dal servizio SMTP configurato sull’istanza: senza credenziali restano disattivati. Trovi le impostazioni nella pagina /self-hosting/configurazione.'
	},

	'corsi-e-presenze': {
		slug: 'corsi-e-presenze',
		title: 'Corsi, calendari e registro presenze | Assozeta',
		description:
			'Corsi, abbonamenti, lezioni e registro presenze con carnet, camp, check-in QR, calendari pubblici e sincronizzazione Google Calendar.',
		heading: 'Corsi e presenze',
		intro:
			'Organizza corsi, abbonamenti e lezioni in un calendario condiviso con istruttori e atleti. Il registro presenze tiene traccia delle partecipazioni e dei carnet, mentre camp e ritiri hanno periodi, servizi e iscrizioni proprie.',
		sections: [
			{
				heading: 'Corsi, abbonamenti e quote',
				body: 'Crei un corso con titolo, descrizione e quota attività, oppure un abbonamento con durata e rinnovo. La tipologia Standard usa una quota con rate opzionali, Quote Multiple offre più piani con nome e importo, l’Abbonamento gestisce la durata in mesi o stagione. L’iscrizione di un atleta genera il pagamento collegato, che resta separato dalla presenza.',
				list: [
					'Corsi standard, a quote multiple e abbonamenti',
					'Rateizzazioni con scadenze e importi modificabili',
					'Pagamento in unica soluzione come alternativa alle rate',
					'Corsi gratuiti e quote a importo zero',
					'Iscrizione degli atleti dalla scheda del corso'
				]
			},
			{
				heading: 'Calendario delle lezioni',
				body: 'Il calendario del corso si compila cliccando sul giorno e indicando nome, inizio e fine della lezione. Le viste mese, settimana, giorno e lista aiutano a controllare la programmazione, mentre un calendario generale raccoglie gli appuntamenti dell’associazione. Le lezioni salvate compaiono subito nel registro presenze.',
				list: [
					'Viste mese, settimana, giorno e lista',
					'Lezioni con titolo, data e orario',
					'Calendario generale oltre a quello del singolo corso',
					'Stampa ed esportazione del calendario',
					'Collegamento diretto tra lezione e registro'
				]
			},
			{
				heading: 'Istruttori, collaboratori e sedi',
				body: 'Ogni corso può essere assegnato a un istruttore, con una tariffa oraria e una percentuale predefinite nel suo profilo. Le ore lavorate si registrano per data, con nota e stato da pagare o pagato, e il riepilogo del periodo mostra ore e compensi. Sedi e collaboratori completano l’organizzazione delle attività.',
				list: [
					'Anagrafica istruttori con contratto e tariffe',
					'Registrazione delle ore lavorate',
					'Ore da pagare o pagate, con nota e data',
					'Riepilogo per periodo e creazione del compenso',
					'Sedi e collaboratori collegati alle attività'
				]
			},
			{
				heading: 'Registro presenze',
				body: 'Apri la lezione e attivi l’interruttore della persona: la presenza viene salvata subito, senza un pulsante finale. Il registro conserva lo storico per atleta e per corso, utile a fine mese o a fine stagione. Se l’istanza lo prevede, le presenze possono essere calcolate automaticamente per le lezioni trascorse, rispettando le assenze segnalate dagli atleti.',
				list: [
					'Presenze manuali per ogni lezione',
					'Storico delle presenze per atleta e per corso',
					'Assenze segnalate dall’area personale dell’atleta',
					'Presenze automatiche attivabili nelle impostazioni',
					'Correzione di una presenza con ripristino del carnet'
				]
			},
			{
				heading: 'Carnet, pacchetti e check-in',
				body: 'Un carnet è un pacchetto di lezioni con un prezzo: assegnarlo a una persona crea il saldo e il pagamento corrispondente. Il consumo avviene alla presenza e segue regole precise, ad esempio il pacchetto con meno lezioni residue e il pagamento registrato. Il check-in con QR si esegue da un operatore autenticato.',
				list: [
					'Pacchetti con numero di lezioni e prezzo',
					'Assegnazione, collegamento al corso e ricarica',
					'Scalamento automatico della lezione alla presenza',
					'Blocco se il carnet non è pagato o è esaurito',
					'Check-in tramite QR da operatore autenticato'
				]
			},
			{
				heading: 'Camp, ritiri e calendari pubblici',
				body: 'Un camp raccoglie periodi con date, quota e numero massimo di partecipanti, e ogni periodo può avere servizi con costo proprio. Le iscrizioni ai camp arrivano anche da un modulo pubblico con QR code. Per i corsi puoi condividere il calendario con un link pubblico o con il codice da incorporare nel sito, e sincronizzarlo con Google Calendar.',
				list: [
					'Camp e ritiri con periodi e servizi',
					'Iscrizioni ai camp dal modulo pubblico',
					'Link pubblico al calendario del corso',
					'Codice HTML per incorporarlo nel sito',
					'Sincronizzazione con Google Calendar'
				]
			}
		],
		footnotes:
			'La sincronizzazione con Google Calendar richiede le credenziali Google configurate sull’istanza e l’autorizzazione dell’account interessato: senza credenziali la funzione resta disattivata. Trovi le impostazioni nella pagina /self-hosting/configurazione.'
	},

	'certificati-e-documenti': {
		slug: 'certificati-e-documenti',
		title: 'Certificati medici e documenti dei soci | Assozeta',
		description:
			'Certificati medici con scadenze e promemoria, documenti e firme dei soci, modelli personalizzati e rendering PDF di tessere e ricevute.',
		heading: 'Certificati e documenti',
		intro:
			'Certificati medici, documenti e firme restano nella scheda della persona, con la data di scadenza sotto controllo. L’applicazione genera i PDF di tessere, ricevute e documenti, così la segreteria lavora su file ordinati invece che su scansioni sparse.',
		sections: [
			{
				heading: 'Il certificato medico nella scheda del socio',
				body: 'Carichi il certificato dalla scheda della persona oppure durante una nuova iscrizione, in PDF o come immagine. La data di scadenza si inserisce a mano e si può correggere senza sostituire il file, mentre per un documento nuovo usi la sostituzione. Un interruttore indica se il certificato è agonistico.',
				list: [
					'Caricamento dalla scheda o da una nuova iscrizione',
					'Sostituzione del documento o modifica della sola scadenza',
					'Indicazione del certificato agonistico',
					'Etichetta di esenzione per i minori di sei anni',
					'Allegati consultabili da chi gestisce l’associazione'
				]
			},
			{
				heading: 'Scadenze sotto controllo',
				body: 'I promemoria automatici si attivano dalle impostazioni e seguono la scadenza registrata. L’email va all’atleta; se manca, al tutore principale e poi all’account collegato. In bacheca i widget mostrano i certificati in scadenza nei prossimi trenta giorni e quelli già scaduti.',
				list: [
					'Promemoria 30 e 7 giorni prima e un giorno dopo la scadenza',
					'Invio all’atleta, al tutore o all’account collegato',
					'Widget per certificati in scadenza e scaduti',
					'Filtri per certificato assente o scaduto',
					'Promemoria attivabili da Profilo → Generali'
				]
			},
			{
				heading: 'Documenti, firme e modulistica',
				body: 'La scheda Documenti raccoglie i file caricati per la persona e i documenti generati dall’applicazione. La firma raccolta nel modulo di iscrizione resta allegata alla domanda, mentre l’archivio dell’associazione conserva file e cartelle. Con la modulistica prepari modelli con dati dinamici e generi il PDF per una persona.',
				list: [
					'Documenti caricati e generati nella stessa scheda',
					'Firme delle iscrizioni conservate con la richiesta',
					'Archivio di file e cartelle dell’associazione',
					'Modelli di documento con campi dinamici',
					'Generazione del PDF dalla scheda della persona'
				]
			},
			{
				heading: 'Rendering dei PDF',
				body: 'Ricevute, tessere e documenti generati diventano PDF pronti da stampare o condividere. Nello stack self-host il rendering è affidato a un servizio Chromium privato incluso nell’installazione, quindi non serve un servizio esterno. La generazione avviene in background: il documento compare nell’elenco quando il PDF è disponibile.',
				list: [
					'Ricevute e documenti contabili in PDF',
					'Tessere stampabili dal browser',
					'Documenti generati dai modelli della modulistica',
					'Servizio di rendering incluso nello stack self-host'
				]
			},
			{
				heading: 'Elenchi pronti per i controlli',
				body: 'Dai widget della bacheca stampi l’elenco dei certificati in scadenza o scaduti in PDF oppure in Excel. Gli elenchi dei tesserati si filtrano per certificato e si esportano per condividerli con chi organizza le attività. Il file prodotto è una copia di lavoro: la scheda della persona resta il riferimento.',
				list: [
					'Stampa in PDF dei gruppi di certificati',
					'Foglio Excel per i controlli della segreteria',
					'Elenco tesserati filtrato per certificato',
					'Confronto con la data salvata nella scheda'
				]
			}
		],
		footnotes:
			'I promemoria via email richiedono il servizio SMTP e lo scheduler configurati sull’istanza: senza credenziali restano disattivati. Trovi le impostazioni nella pagina /self-hosting/configurazione.'
	},

	'pagamenti-e-ricevute': {
		slug: 'pagamenti-e-ricevute',
		title: 'Quote, pagamenti e ricevute per ASD | Assozeta',
		description:
			'Quote, scadenze, rate e incassi con ricevute PDF numerate, solleciti email ed export; pagamenti online con Stripe per i flussi supportati.',
		heading: 'Pagamenti e ricevute',
		intro:
			'Quote associative, quote di tesseramento e quote dei corsi arrivano in un unico elenco con scadenze, stato e ricevuta. Registri l’incasso quando avviene davvero, e l’applicazione prepara il PDF con il numero progressivo.',
		sections: [
			{
				heading: 'Quote, scadenze e rate',
				body: 'Le quote nascono dall’iscrizione o dal corso e compaiono nell’elenco pagamenti con descrizione, importo, intestatario e scadenza. Un corso può essere rateizzato: il piano rate divide la quota in scadenze che puoi correggere prima di assegnarlo. In alternativa offri il pagamento in unica soluzione, anche con un importo diverso dal totale delle rate.',
				list: [
					'Quote associative, di tesseramento e di corso',
					'Piani rate con scadenze e importi modificabili',
					'Unica soluzione come alternativa al piano rate',
					'Stato in attesa o pagato per ogni registrazione',
					'Causali e conti collegati al movimento'
				]
			},
			{
				heading: 'Registrare un incasso',
				body: 'Crei un pagamento indicando descrizione, importo, persona, conto, causale e metodo: contanti, bonifico, PoS, Stripe o altro. Con l’incasso automatico disattivato il pagamento nasce in attesa e si incassa con «Segna come pagato», scegliendo la data. L’importo può comprendere causali aggiuntive: controlla il totale prima di salvare.',
				list: [
					'Metodi di pagamento distinti da conto e causale',
					'Incasso con data e conferma',
					'Incasso automatico attivabile nelle impostazioni',
					'Causali aggiuntive sullo stesso pagamento',
					'Modifica ed eliminazione con i relativi permessi'
				]
			},
			{
				heading: 'Ricevute numerate in PDF',
				body: 'All’incasso di un’entrata intestata a una persona l’applicazione assegna un numero progressivo alla ricevuta e prepara il PDF. La numerazione segue l’anno fiscale e il numero iniziale impostato. Se elimini una ricevuta, il documento sparisce ma il pagamento resta e torna in attesa.',
				list: [
					'Progressivo legato all’anno fiscale',
					'Numero visibile o nascosto nel modello',
					'PDF consultabile e link di condivisione',
					'Rigenerazione dopo la modifica del numero',
					'Eliminazione con pagamento riportato in attesa'
				]
			},
			{
				heading: 'Pagamenti online con Stripe',
				body: 'Con Stripe Connect configurato sull’istanza, i flussi supportati si pagano online tramite l’account Stripe collegato. Nelle impostazioni scegli i metodi da proporre, come carte e addebito SEPA, e scrivi il testo che accompagna il checkout. Le registrazioni arrivate da Stripe si ritrovano filtrando per metodo, separate dai pagamenti inseriti a mano.',
				list: [
					'Stripe Connect collegato all’istanza',
					'Preferenze per carte e addebito SEPA',
					'Filtro per metodo Stripe nell’elenco pagamenti',
					'Testo di checkout personalizzabile',
					'Confronto tra stato nell’applicazione e account Stripe'
				]
			},
			{
				heading: 'Solleciti, export e controllo',
				body: 'Le quote in attesa si sollecitano via email dalla riga del pagamento, dopo aver controllato i destinatari. L’elenco si esporta in CSV o Excel, per intero o secondo la ricerca corrente, e le colonne della tabella si scelgono in base a cosa serve. Un export è una copia: non cambia lo stato dei pagamenti.',
				list: [
					'Sollecito email per i pagamenti in attesa',
					'Esportazione CSV ed Excel',
					'Esportazione della sola ricerca corrente',
					'Colonne della tabella configurabili',
					'Archivio separato dei pagamenti'
				]
			}
		],
		footnotes:
			'Stripe e l’invio delle email sono integrazioni facoltative: senza le credenziali configurate sull’istanza restano disattivate. Trovi le impostazioni nella pagina /self-hosting/configurazione.'
	},

	contabilita: {
		slug: 'contabilita',
		title: 'Contabilità, conti e bilancio per ASD | Assozeta',
		description:
			'Entrate, uscite, conti, trasferimenti e causali; clienti e fornitori, documenti contabili e bilancio dell’anno sportivo con export PDF ed Excel.',
		heading: 'Contabilità e bilancio',
		intro:
			'Entrate e uscite dell’associazione finiscono nello stesso registro, con conti, causali e documenti collegati. Il rendiconto economico finanziario mette insieme l’esercizio e la liquidità dei conti, pronto da rivedere e pubblicare.',
		sections: [
			{
				heading: 'Entrate e uscite nello stesso registro',
				body: 'Ogni movimento ha una tipologia, entrata o uscita, e un tipo quota che dice da dove arriva: iscrizione, corso, giroconto o altro. Metodo, conto e causale completano la registrazione e restano modificabili finché il pagamento è in attesa. Le uscite seguono lo stesso elenco delle entrate, con importi e destinatari propri.',
				list: [
					'Entrate e uscite con stato di incasso',
					'Tipi quota: iscrizione, corso, giroconto, altro',
					'Metodi di pagamento e conti finanziari',
					'Causali con gestione IVA e detraibilità',
					'Esportazione dell’elenco filtrato'
				]
			},
			{
				heading: 'Conti, saldi e trasferimenti',
				body: 'I conti finanziari rappresentano cassa, banca o altri strumenti di incasso, ognuno con saldo iniziale e codice. Un giroconto sposta denaro tra due conti senza creare un’entrata o una spesa, e i saldi si aggiornano di conseguenza. Il saldo attuale di un conto segue i movimenti registrati.',
				list: [
					'Conti di tipo cassa, banca o altro',
					'Saldo iniziale e codice del conto',
					'Giroconti tra conti diversi',
					'Saldi aggiornati dopo ogni movimento',
					'Eliminazione di un giroconto registrato per errore'
				]
			},
			{
				heading: 'Clienti, fornitori e documenti',
				body: 'Clienti e fornitori hanno un’anagrafica dedicata, collegata ai documenti contabili. I documenti si registrano con importo, date e stato pagato o da pagare, e si ritrovano nell’elenco della contabilità. Le ricevute emesse verso le persone restano invece nei documenti fiscali.',
				list: [
					'Anagrafiche di clienti e fornitori',
					'Documenti contabili con stato di pagamento',
					'Collegamento tra documento e anagrafica',
					'Archivio dedicato dei documenti',
					'Ricerca per periodo e controparte'
				]
			},
			{
				heading: 'Bilancio dell’anno sportivo',
				body: 'Il rendiconto economico finanziario somma entrate e uscite dell’esercizio e mostra la liquidità dei conti. Le voci si dividono tra istituzionali e commerciali in base alla causale, e puoi aggiungere righe manuali per contributi o costi non registrati come pagamento. La bozza si salva, si controlla e poi si pubblica; la pubblicazione resta annullabile.',
				list: [
					'Riepilogo di entrate, uscite, gestione e liquidità',
					'Ripartizione tra voci istituzionali e commerciali',
					'Righe manuali nella bozza',
					'Pubblicazione e annullamento della pubblicazione',
					'Selettore dell’anno sociale'
				]
			},
			{
				heading: 'Anno fiscale e stagione sportiva',
				body: 'L’anno fiscale decide l’inizio dell’esercizio usato dal bilancio: anno solare, stagione settembre-agosto, giugno-maggio oppure un periodo personalizzato. La stagione sportiva e la durata di iscrizioni e tesseramenti sono impostazioni separate. Cambiare l’anno fiscale non riscrive le date già presenti nelle iscrizioni.',
				list: [
					'Anno solare, due stagioni sportive o periodo personalizzato',
					'Stagione sportiva con inizio configurabile',
					'Durata di iscrizioni e tesseramenti separata',
					'Periodo del rendiconto coerente con l’impostazione',
					'Verifica dei valori dopo il salvataggio'
				]
			},
			{
				heading: 'Riepiloghi ed esportazioni',
				body: 'Il rendiconto si esporta in PDF o Excel per il consiglio direttivo e per chi rivede i conti. Gli elenchi dei pagamenti si esportano in CSV o Excel e la prima nota ha i propri filtri di periodo. Prima di consegnare un file controlla ambito e periodo: l’esportazione non modifica i dati.',
				list: [
					'Rendiconto in PDF ed Excel',
					'Prima nota con filtri di periodo',
					'Export dei pagamenti per intervallo o ricerca',
					'Copie di lavoro da verificare prima della consegna'
				]
			}
		],
		footnotes:
			'Bilancio ed esportazioni contengono dati dell’associazione: conservali in modo protetto e condividili solo con chi ne ha diritto. I movimenti online richiedono Stripe configurato sull’istanza, come spiegato nella pagina /self-hosting/configurazione.'
	},

	'comunicazioni-e-automazioni': {
		slug: 'comunicazioni-e-automazioni',
		title: 'Email, automazioni e notifiche per l’associazione | Assozeta',
		description:
			'Email tramite SMTP, post e messaggi agli iscritti, automazioni su eventi con attese e azioni email, notifiche in tempo reale nell’applicazione.',
		heading: 'Comunicazioni e automazioni',
		intro:
			'Comunichi con soci e atleti dall’applicazione: post, email e automazioni che partono da un evento. Le notifiche in tempo reale tengono aggiornati gli account mentre l’associazione lavora.',
		sections: [
			{
				heading: 'Email dall’applicazione con SMTP',
				body: 'Le email partono dal servizio SMTP configurato sull’istanza: mittente, server e credenziali si impostano una volta sola. Da Comunicazioni prepari il messaggio, scegli i destinatari e richiedi l’invio. Lo storico conserva le richieste effettuate, utile per capire cosa è già partito.',
				list: [
					'Configurazione SMTP con mittente e credenziali',
					'Invio a una lista di indirizzi',
					'Importazione degli atleti nella lista',
					'Storico delle email richieste',
					'Messaggi salvati senza invio immediato'
				]
			},
			{
				heading: 'Post e messaggi per gli iscritti',
				body: 'Un post pubblica un avviso nell’area degli atleti senza spedire email: è comodo per una variazione di orario o una comunicazione breve. Un messaggio email ha oggetto e contenuto e si può conservare prima di decidere quando inviarlo. La bacheca interna resta uno spazio separato, riservato ad amministratori e collaboratori.',
				list: [
					'Post destinati agli atleti iscritti',
					'Email con oggetto e contenuto',
					'Salvataggio del messaggio senza invio',
					'Invio immediato a una lista di indirizzi',
					'Bacheca interna separata dai post'
				]
			},
			{
				heading: 'Automazioni su eventi',
				body: 'Un’automazione collega un evento a una sequenza di passaggi: un’email, un’attesa in minuti, ore o giorni, poi un’altra email. Puoi partire da una nuova iscrizione, dalla firma, dall’approvazione oppure da una data e ora specifica, con un filtro sulle iscrizioni. Le automazioni nascono spente e si attivano dall’elenco dopo aver controllato il contenuto.',
				list: [
					'Eventi: nuova iscrizione, firma, approvazione, data specifica',
					'Azioni email con destinatari, oggetto e contenuto',
					'Attese tra un passaggio e il successivo',
					'Automazioni create spente e attivate a mano',
					'Esecuzione affidata allo scheduler dell’istanza'
				]
			},
			{
				heading: 'Email di benvenuto ai nuovi iscritti',
				body: 'L’esempio più comune è il messaggio di benvenuto: evento «nuova iscrizione», prima email con le informazioni della segreteria, eventuale attesa e seconda email con i dettagli del corso. Per la prima prova invii a te stesso e controlli testo, mittente e formattazione. Solo dopo attivi l’automazione per le nuove richieste.',
				list: [
					'Benvenuto alla nuova iscrizione',
					'Promemoria dopo qualche giorno',
					'Prova su un indirizzo controllato',
					'Attivazione separata dal salvataggio',
					'Controllo dello storico e della casella'
				]
			},
			{
				heading: 'Notifiche in tempo reale',
				body: 'Le notifiche arrivano in tempo reale tramite WebSocket mentre lavori, senza ricaricare la pagina. Il canale è gestito dalla tua installazione di Assozeta, quindi non serve un servizio esterno.',
				list: [
					'Notifiche in tempo reale nell’applicazione',
					'Aggiornamenti senza ricaricare la pagina',
					'Nessun servizio esterno richiesto'
				]
			},
			{
				heading: 'Controllare prima di inviare',
				body: 'Prima di una comunicazione collettiva controlla destinatari, testo e allegati, e verifica che il servizio SMTP risponda. Un messaggio accettato dall’applicazione non è la prova che sia arrivato nella casella: per le prove usa un indirizzo concordato. Le comunicazioni contengono dati personali, quindi vanno inviate solo a chi è interessato.',
				list: [
					'Revisione di oggetto, testo e destinatari',
					'Prova su un indirizzo concordato',
					'Controllo dello storico email',
					'Attenzione ai dati personali nel testo'
				]
			}
		],
		footnotes:
			'SMTP, invio programmato e automazioni richiedono le credenziali e i processi configurati sull’istanza, worker e scheduler compresi. Senza configurazione le email restano disattivate: trovi le impostazioni nella pagina /self-hosting/configurazione.'
	},

	'bacheca-e-report': {
		slug: 'bacheca-e-report',
		title: 'Bacheca, report ed esportazioni CSV e XLSX | Assozeta',
		description:
			'Bacheca con widget e liste operative, report e grafici, esportazioni CSV e XLSX, collaboratori con accesso personalizzato per area.',
		heading: 'Bacheca e report',
		intro:
			'La bacheca è il punto di partenza della giornata: iscrizioni da approvare, certificati in scadenza, pagamenti aperti e lezioni di oggi in un solo colpo d’occhio. Report ed esportazioni permettono di portare i dati fuori dall’applicazione quando serve.',
		sections: [
			{
				heading: 'Una bacheca personale',
				body: 'Ogni account sceglie quali widget vedere, in che ordine e con quale larghezza, e salva la propria disposizione. La disposizione del titolare e quella di un collaboratore restano separate. Su telefono i riquadri si dispongono in una colonna, mentre i comandi di personalizzazione sono pensati per il computer.',
				list: [
					'Widget aggiunti e rimossi dalla modalità modifica',
					'Riordino e larghezze personalizzate',
					'Salvataggio del layout per singolo account',
					'Ripristino della disposizione predefinita',
					'Riquadri in colonna sugli schermi piccoli'
				]
			},
			{
				heading: 'Le liste operative di ogni giorno',
				body: 'I widget più utili sono le liste da controllare: iscrizioni da approvare, certificati in scadenza o scaduti, pagamenti scaduti, lezioni di oggi, carnet in esaurimento. Ogni riquadro apre la scheda della persona o della lezione, così passi dal controllo all’operazione. Ogni conteggio segue un periodo preciso: tienine conto quando lo confronti con gli elenchi.',
				list: [
					'Iscrizioni da approvare',
					'Certificati medici in scadenza e scaduti',
					'Pagamenti scaduti o in scadenza',
					'Lezioni di oggi e carnet in esaurimento',
					'Riepiloghi di soci, incassi ed entrate e uscite'
				]
			},
			{
				heading: 'Report e grafici',
				body: 'Oltre alle liste, la bacheca mostra conteggi e grafici: iscrizioni degli ultimi trenta giorni, pagamenti incassati per giorno, corsi con più iscritti, riepilogo di entrate e uscite. Sono indicatori di lavoro, non sostituti degli elenchi: per correggere un dato si apre la sezione di origine. I gruppi di certificati si stampano in PDF o Excel direttamente dal widget.',
				list: [
					'Grafico delle nuove iscrizioni',
					'Andamento degli incassi',
					'Corsi con più iscritti',
					'Riepilogo di entrate e uscite',
					'Stampa di un widget in PDF o Excel'
				]
			},
			{
				heading: 'Esportazioni CSV e XLSX',
				body: 'Gli elenchi si esportano in CSV o Excel, per intero o secondo la ricerca impostata. L’applicazione esporta anche tutti i dati dell’associazione nel formato applicativo, utile per una copia o per un trasferimento. Gli export contengono dati personali e, nel caso completo, anche gli hash delle password degli account: vanno protetti.',
				list: [
					'Export CSV ed Excel degli elenchi',
					'Esportazione della ricerca corrente',
					'Export completo nel formato applicativo',
					'Dati sensibili da conservare in modo protetto'
				]
			},
			{
				heading: 'Collaboratori con il loro accesso',
				body: 'Inviti un collaboratore con la sua email e scegli tra accesso completo e permessi personalizzati per area: lettura, creazione, modifica o eliminazione. Così il commercialista lavora sulla contabilità e un istruttore sulle presenze, ognuno con i permessi che gli hai assegnato. Lo stato dell’invito distingue un account accettato da uno ancora in attesa, e i permessi si aggiornano senza cambiare account.',
				list: [
					'Invito via email con accesso completo o personalizzato',
					'Permessi per singola area e operazione',
					'Stato dell’invito: accettato o in attesa',
					'Modifica dei permessi in ogni momento',
					'Ruolo del socio distinto dai permessi di accesso'
				]
			}
		],
		footnotes:
			'Inviti e notifiche via email richiedono il servizio SMTP configurato sull’istanza; gli export contengono dati personali e vanno protetti. Trovi le impostazioni nella pagina /self-hosting/configurazione.'
	}
};

/** Cross-links shown as "Pagine correlate" on every feature page. */
export const FEATURE_RELATED: Record<string, string[]> = {
	'soci-e-tesserati': ['iscrizioni-online', 'certificati-e-documenti', 'pagamenti-e-ricevute'],
	'iscrizioni-online': ['soci-e-tesserati', 'certificati-e-documenti', 'pagamenti-e-ricevute'],
	'corsi-e-presenze': ['soci-e-tesserati', 'certificati-e-documenti', 'pagamenti-e-ricevute'],
	'certificati-e-documenti': ['soci-e-tesserati', 'iscrizioni-online', 'bacheca-e-report'],
	'pagamenti-e-ricevute': ['contabilita', 'soci-e-tesserati', 'bacheca-e-report'],
	contabilita: ['pagamenti-e-ricevute', 'bacheca-e-report', 'comunicazioni-e-automazioni'],
	'comunicazioni-e-automazioni': ['bacheca-e-report', 'iscrizioni-online', 'pagamenti-e-ricevute'],
	'bacheca-e-report': ['contabilita', 'pagamenti-e-ricevute', 'comunicazioni-e-automazioni']
};
