/**
 * Content registry for the self-hosting guide pages.
 * Each entry maps to /self-hosting/[slug] and is aligned with the
 * selfhost/ documentation of the Assozeta repository (release 1.0.39).
 *
 * Only documented commands are shown in `code` blocks: every one of them
 * exists verbatim in selfhost/README.md, selfhost/UPDATES.md or
 * selfhost/OPERATIONS.md, apart from the example domain and version.
 */
import type { ContentPage } from './routes';

export const SELF_HOSTING_PAGES: Record<string, ContentPage> = {
	requisiti: {
		slug: 'requisiti',
		title: 'Requisiti del server per installare Assozeta | Assozeta',
		description:
			'Server Linux con Docker Engine, Docker Compose 2.20 o superiore, DNS pubblico, porte 80 e 443 e almeno 4 GB di RAM per installare Assozeta.',
		heading: 'Requisiti per installare Assozeta',
		intro:
			'Assozeta gira su un server Linux con Docker Compose. Prima di iniziare, verifica che la macchina abbia Docker, un nome DNS pubblico e spazio sufficiente per i backup.',
		sections: [
			{
				heading: 'Server e Docker',
				body: 'Serve un server Linux con Docker Engine in esecuzione e Docker Compose v2.20.0 o superiore. Il server non compila nulla: scarica le immagini già pronte dell’applicazione e le avvia.',
				list: [
					'Linux con Docker Engine in esecuzione',
					'Docker Compose v2.20.0 o superiore',
					'Un utente amministratore con accesso a Docker e permessi di scrittura sulla cartella di installazione'
				]
			},
			{
				heading: 'Nome DNS e porte',
				body: 'Caddy ottiene da solo il certificato HTTPS, a condizione che il dominio sia pubblico e che le porte siano raggiungibili. Prima dell’installazione crea il record DNS che punta al server e controlla il firewall. Caddy è l’unico servizio che pubblica porte: la 80 e la 443.',
				list: [
					'Un record DNS pubblico che punta all’indirizzo del server',
					'Porta 80 raggiungibile da internet',
					'Porta 443 raggiungibile da internet'
				]
			},
			{
				heading: 'Utility richieste',
				body: 'Lo script di installazione e i comandi di manutenzione usano quattro utility di base, già presenti nella maggior parte delle distribuzioni. Su un sistema minimale installale con il gestore di pacchetti.',
				list: ['openssl', 'curl', 'tar', 'flock, dal pacchetto util-linux']
			},
			{
				heading: 'Memoria e disco',
				body: 'La macchina deve avere almeno 4 GB di RAM; con 8 GB la generazione dei PDF, affidata a un Chromium privato, ha più margine. Oltre alle immagini Docker servono spazio per il database, per l’archivio oggetti e per i backup.',
				list: [
					'Almeno 4 GB di RAM',
					'8 GB consigliati se usi la generazione dei PDF',
					'Spazio disco per immagini, database, archivio oggetti e archivi di backup'
				]
			},
			{
				heading: 'Cosa installa lo stack',
				body: 'Il pacchetto self-host avvia un unico insieme di servizi su una rete Docker privata. Solo Caddy pubblica porte verso internet: database, Redis, archivio oggetti e generatore PDF restano interni.',
				list: [
					'Caddy: gestisce HTTPS e serve l’applicazione web',
					'API: risponde alle richieste dell’interfaccia',
					'Worker e scheduler: processi in background e attività pianificate',
					'PostgreSQL: il database dell’associazione',
					'Redis: code di lavoro, sessioni e stato condiviso',
					'MinIO: archivio oggetti privato per documenti, allegati e firme',
					'Generatore PDF: Chromium privato che produce i documenti'
				]
			},
			{
				heading: 'Spazio per i backup',
				body: 'Un backup contiene il dump del database, lo specchio dell’archivio oggetti, la configurazione e un manifest. Durante la creazione lo specchio viene preparato non compresso accanto all’archivio: servono quindi circa il doppio dello spazio occupato da database e oggetti, più un margine di 1024 MB (variabile ASSOZETA_BACKUP_SPACE_MARGIN_MB). Un aggiornamento riserva inoltre 2048 MB per le nuove immagini (ASSOZETA_UPGRADE_IMAGE_SPACE_MB). Prima di fermare qualcosa il comando controlla lo spazio libero: se non basta si ferma senza toccare i servizi.',
				list: [
					'Circa il doppio dello spazio di database e archivio oggetti',
					'Più 1024 MB di margine per il backup',
					'Più 2048 MB riservati alle immagini durante un aggiornamento'
				],
				links: [
					{
						label: 'selfhost/README.md su GitHub',
						href: 'https://github.com/carbogninalberto/assozeta/blob/main/selfhost/README.md'
					}
				]
			}
		],
		footnotes:
			'Requisiti e valori citati sono quelli della release 1.0.39 (novembre 2026) e valgono per un’installazione su un singolo server con il pacchetto self-host ufficiale.'
	},

	installazione: {
		slug: 'installazione',
		title: 'Installare Assozeta con Docker Compose | Assozeta',
		description:
			'Scarica il pacchetto self-host della release 1.0.39, esegui il comando install e completa la procedura guidata con il token di setup.',
		heading: 'Installare Assozeta sul tuo server',
		intro:
			'L’installazione parte da un pacchetto di release pubblicato su GitHub e si esegue con un solo comando. Alla fine apri l’indirizzo stampato, incolli il token di setup e completi la procedura guidata.',
		sections: [
			{
				heading: 'Scarica il pacchetto della release',
				body: 'Ogni release allega l’archivio assozeta-selfhost-v1.0.39.tar.gz. Scaricalo dalla pagina Releases del progetto su GitHub e mettilo in una cartella del server. L’archivio non ha una cartella di versione: estraendolo ottieni direttamente selfhost/, con compose.yml, .env.example e lo script bin/assozeta. Spostati nella cartella che contiene selfhost/ ed esegui i comandi qui sotto da lì, senza cambiare i percorsi.',
				links: [
					{
						label: 'Release di Assozeta su GitHub',
						href: 'https://github.com/carbogninalberto/assozeta/releases'
					}
				]
			},
			{
				heading: 'Esegui l’installazione',
				body: 'Il comando install prepara la configurazione e avvia i servizi. Sostituisci dominio e indirizzo email con i tuoi: il dominio deve essere quello pubblico che punta al server, e serve anche per il certificato HTTPS.',
				code: './selfhost/bin/assozeta install \\\n  --domain assozeta.example.org \\\n  --email admin@example.org \\\n  --version 1.0.39'
			},
			{
				heading: 'Cosa fa il comando',
				body: 'Il comando fa tutto da solo e non chiede di modificare file a mano. In ordine:',
				list: [
					'genera selfhost/.env con permessi 0600',
					'genera la coppia di chiavi Ed25519 per i token e il token di setup del primo avvio',
					'scarica le immagini della versione richiesta',
					'inizializza PostgreSQL e MinIO',
					'applica le migrazioni del database e carica i dati di riferimento',
					'attende che sito e API rispondano prima di dichiarare l’installazione pronta'
				]
			},
			{
				heading: 'Token di setup e procedura guidata',
				body: 'Alla fine il comando stampa l’indirizzo da aprire e il token di setup. Apri l’indirizzo, incolla il token e crea l’account proprietario e l’associazione. Il token resta recuperabile da selfhost/.env come INSTANCE_SETUP_TOKEN e va trattato come un segreto finché la procedura non è conclusa. La procedura guidata si completa una sola volta: ogni installazione gestisce una sola associazione.',
				list: [
					'Il token è generato dall’installazione: un valore scelto a caso viene rifiutato',
					'La procedura guidata crea proprietario e associazione e non è ripetibile',
					'Conserva il token in un posto sicuro finché non hai finito'
				]
			},
			{
				heading: 'Provare in locale',
				body: 'Per valutare Assozeta senza dominio pubblico e senza certificato puoi installare su localhost. L’etichetta latest segue l’ultima release pubblicata ed è pensata solo per la valutazione: in produzione usa sempre una versione esatta.',
				code: './selfhost/bin/assozeta install --domain localhost --version latest'
			},
			{
				heading: 'Porte personalizzate',
				body: 'Se Caddy non può usare 80 e 443, indica la porta insieme al dominio, per esempio --domain localhost:58080 oppure --domain https://assozeta.example.org:8443. La variabile HTTP_PORT o HTTPS_PORT generata segue la porta dell’indirizzo, così Docker mappa la stessa porta sull’host e sul container.'
			},
			{
				heading: 'Fissa la versione in produzione',
				body: 'Un’installazione pubblica dovrebbe sempre indicare una versione esatta, come 1.0.39, invece di latest. In questo modo sai cosa stai eseguendo e puoi decidere quando aggiornare, dopo aver letto le note della nuova release.',
				links: [
					{
						label: 'selfhost/README.md su GitHub: installazione e configurazione',
						href: 'https://github.com/carbogninalberto/assozeta/blob/main/selfhost/README.md'
					}
				]
			}
		],
		footnotes:
			'Gli esempi usano il dominio assozeta.example.org e la release 1.0.39: sostituisci il dominio con il tuo e indica sempre una versione esatta in produzione.'
	},

	configurazione: {
		slug: 'configurazione',
		title: 'Configurare Assozeta: variabili e integrazioni | Assozeta',
		description:
			'Cosa impostare in selfhost/.env, come applicare le modifiche e quali integrazioni opzionali attivare: SMTP, Google, Apple, Stripe e agente AI.',
		heading: 'Configurazione e integrazioni',
		intro:
			'La configurazione dell’installazione vive in selfhost/.env. Le integrazioni esterne sono opzionali e restano spente finché non inserisci le credenziali. Parte delle impostazioni si gestisce anche dall’interfaccia, in Profilo → Istanza.',
		sections: [
			{
				heading: 'Il file .env',
				body: 'selfhost/.env.example documenta tutte le impostazioni supportate; selfhost/.env è la configurazione attiva, generata dall’installazione con permessi 0600 e ignorata da git. Contiene segreti: non copiarlo in un repository, non allegarlo a un ticket e non condividerlo. Conserva la SECRET_KEY insieme ai backup protetti: cifra le credenziali salvate dall’interfaccia, che senza di lei non si possono più leggere.',
				list: [
					'Dominio, indirizzo pubblico e host consentiti',
					'Connessione a PostgreSQL e Redis',
					'Archivio oggetti MinIO e relative credenziali',
					'Chiavi di firma, come SECRET_KEY e la coppia JWT',
					'Credenziali delle integrazioni opzionali, vuote se non le usi'
				]
			},
			{
				heading: 'Applicare le modifiche',
				body: 'Dopo aver modificato il file riavvia i servizi: il comando ricrea i container e carica i nuovi valori. Le modifiche salvate dall’interfaccia non richiedono un riavvio, quelle fatte a mano al file sì.',
				code: './selfhost/bin/assozeta restart'
			},
			{
				heading: 'Integrazioni opzionali',
				body: 'SMTP, accesso con Google e Apple, Stripe e agente compatibile OpenAI si attivano compilando le rispettive credenziali. Se le lasci vuote restano disattivate e l’applicazione funziona lo stesso: soci, iscrizioni, corsi, presenze, pagamenti registrati a mano e contabilità non dipendono da servizi esterni.',
				list: [
					'Email SMTP: EMAIL_HOST, EMAIL_PORT, EMAIL_HOST_USER, EMAIL_HOST_PASSWORD, EMAIL_USE_TLS o EMAIL_USE_SSL',
					'Accesso con Google: SOCIAL_AUTH_GOOGLE_OAUTH2_KEY',
					'Accesso con Apple (verifica del token): SOCIAL_AUTH_APPLE_ID_CLIENT',
					'Stripe: STRIPE_PUBLIC_KEY, STRIPE_KEY e STRIPE_WEBHOOK_SECRET',
					'Agente compatibile OpenAI: AI_API_KEY, AI_MODEL e AI_BASE_URL'
				]
			},
			{
				heading: 'Cosa si imposta dall’interfaccia',
				body: 'Il proprietario dell’istanza trova in Profilo → Istanza (la pagina Self Instance della documentazione tecnica) le schede Panoramica, Backup, Aggiornamenti e Impostazioni. In Impostazioni, Identità comprende nome, abbreviazione, email di supporto, colore primario e logo; Email permette di salvare il server SMTP e provarlo; Integrazioni copre Stripe, Google e Apple. Salvare dall’interfaccia non riscrive .env: i valori salvati hanno la precedenza, e le modifiche manuali al file richiedono comunque il riavvio dei servizi.',
				list: [
					'La pagina è riservata al proprietario dell’istanza: collaboratori e altri amministratori non possono usarla',
					'I segreti già configurati restano nascosti e non vengono restituiti dall’API',
					'Salvare l’identità non sovrascrive le impostazioni operative',
					'Dopo aver cambiato un’integrazione ricarica le pagine aperte nel browser'
				]
			},
			{
				heading: 'Archivio oggetti privato',
				body: 'Documenti, allegati e firme finiscono in MinIO, usato come storage S3-compatibile privato. La configurazione generata imposta AWS_S3_USE_OBJECT_ACL a False e lascia vuoto AWS_S3_PUBLIC_BASE_URL: gli oggetti non sono pubblici e l’applicazione li legge tramite il backend. Non aggiungere una rotta Caddy né una porta host per MinIO: database, Redis e archivio restano sulla rete interna di Docker.',
				links: [
					{
						label: 'selfhost/.env.example su GitHub',
						href: 'https://github.com/carbogninalberto/assozeta/blob/main/selfhost/.env.example'
					}
				]
			},
			{
				heading: 'Prima di andare in produzione',
				body: 'Poche regole rendono l’installazione solida nel tempo. Le stesse indicazioni valgono per una macchina appena preparata e per una già in uso.',
				list: [
					'Non committare .env né gli archivi di backup',
					'Usa versioni esatte delle immagini anche in produzione',
					'Mantieni PostgreSQL, Redis, MinIO e il generatore PDF privati',
					'Configura SMTP prima di affidarti alle email di reset della password',
					'Tieni aggiornati Docker e il sistema operativo del server'
				]
			}
		],
		footnotes:
			'I nomi delle variabili sono quelli di selfhost/.env.example nella release 1.0.39. Non copiare i valori di esempio: l’installazione genera segreti propri.'
	},

	aggiornamenti: {
		slug: 'aggiornamenti',
		title: 'Aggiornare Assozeta a una nuova release | Assozeta',
		description:
			'Aggiornamento dalla pagina Istanza o da riga di comando, con backup automatico, pagina di manutenzione e istruzioni di recupero.',
		heading: 'Aggiornamenti',
		intro:
			'Assozeta si aggiorna a una release stabile pubblicata su GitHub. L’aggiornamento crea prima un backup, mette il sito in manutenzione e alla fine verifica i servizi. Puoi avviarlo dall’interfaccia o da riga di comando.',
		sections: [
			{
				heading: 'Aggiornare dall’interfaccia',
				body: 'Il proprietario dell’istanza apre Profilo → Istanza → Aggiornamenti. La pagina elenca le release stabili pubblicate: scegli quella che vuoi installare e leggi le note, comprese quelle delle versioni intermedie. Dopo la conferma l’operazione parte e la release scelta resta bloccata anche se nel frattempo ne compare un’altra.',
				list: [
					'La versione in esecuzione e quella configurata sono mostrate separatamente',
					'Leggi le note di tutte le versioni comprese tra quella installata e quella scelta',
					'Confermando, l’aggiornamento crea un backup e applica la nuova versione'
				]
			},
			{
				heading: 'Cosa succede durante l’aggiornamento',
				body: 'L’aggiornamento ferma API, worker e scheduler, applica le migrazioni e riavvia i servizi con le nuove immagini. Caddy resta attivo e serve una pagina di manutenzione con stato HTTP 503, così chi apre il sito capisce che l’interruzione è temporanea. L’operazione si considera conclusa solo quando i controlli di prontezza su sito e API sono passati.'
			},
			{
				heading: 'Aggiornare da riga di comando',
				body: 'Se preferisci il terminale, il comando upgrade accetta la versione esatta da installare: usa la stessa versione che avresti scelto dall’interfaccia. Backup, ripristini e aggiornamenti condividono un lock, quindi non possono sovrapporsi.',
				code: './selfhost/bin/assozeta upgrade 1.0.39',
				list: [
					'La configurazione e le credenziali esistenti vengono conservate',
					'Un file gestito modificato a mano che confligge con la release interrompe l’aggiornamento invece di essere sostituito',
					'Se le immagini non si scaricano, la distribuzione precedente viene ripristinata'
				]
			},
			{
				heading: 'Installazioni più vecchie: aggiornamento una tantum',
				body: 'Le installazioni precedenti all’introduzione della pagina Istanza (Self Instance) devono scaricare una volta lo script di aggiornamento dalla release. Il bootstrap aggiorna l’installazione e prepara il servizio che gestisce gli aggiornamenti successivi, senza modificare a mano .env o i file Compose. Non eseguire git pull sull’installazione attiva prima di questo passaggio: l’updater deve prima salvare i file esistenti.',
				links: [
					{
						label: 'Aggiornamento una tantum delle installazioni esistenti (UPDATES.md)',
						href: 'https://github.com/carbogninalberto/assozeta/blob/main/selfhost/UPDATES.md#one-time-update-for-existing-installations'
					}
				]
			},
			{
				heading: 'Tieni il backup pre-aggiornamento',
				body: 'Ogni aggiornamento crea un archivio prima di cambiare la versione delle immagini. Conservalo: tornare alle immagini precedenti non annulla le migrazioni già applicate, quindi per tornare indietro servono il backup e la procedura di recupero. La rotazione automatica non elimina il backup dell’ultimo aggiornamento riuscito.'
			},
			{
				heading: 'Se qualcosa va storto',
				body: 'Un aggiornamento interrotto lascia un’operazione da recuperare e blocca nuove richieste, così nessuno lavora su uno stato incerto. Se l’interruzione è avvenuta prima dello snapshot della distribuzione, avvia la release di partenza e verifica lo stato:',
				code: './selfhost/bin/assozeta start\n./selfhost/bin/assozeta reconcile-updates'
			},
			{
				heading: 'Recuperare un aggiornamento non riuscito',
				body: 'Se esiste uno snapshot della distribuzione, recupera con il backup indicato nel file di transazione dell’aggiornamento. Il comando chiede conferma prima di sostituire configurazione e dati; per un uso non interattivo aggiungi --yes. Se il recupero fallisce, API e worker restano fermi e la transazione resta aperta: riprendi con lo script di recupero conservato in selfhost/.updater/recover-upgrade e lo stesso backup.',
				code: './selfhost/bin/assozeta recover-upgrade ./selfhost/backups/assozeta-TIMESTAMP.tar.gz',
				links: [
					{
						label: 'selfhost/UPDATES.md su GitHub: aggiornamenti e recupero',
						href: 'https://github.com/carbogninalberto/assozeta/blob/main/selfhost/UPDATES.md'
					}
				]
			}
		],
		footnotes:
			'Comandi e comportamenti descritti valgono per la release 1.0.39. Il dettaglio delle operazioni, dello stato privato e delle procedure di recupero è in selfhost/UPDATES.md.'
	},

	'backup-e-ripristino': {
		slug: 'backup-e-ripristino',
		title: 'Backup e ripristino di un’istanza Assozeta | Assozeta',
		description:
			'Cosa contiene un backup di Assozeta, dove viene salvato, come ruota, come copiarlo fuori dal server e come ripristinarlo in sicurezza.',
		heading: 'Backup e ripristino',
		intro:
			'Il backup di Assozeta raccoglie database, archivio oggetti e configurazione in un solo archivio. Contiene segreti: va protetto e copiato fuori dal server. Il ripristino sostituisce i dati attuali e va provato almeno una volta su un’altra macchina.',
		sections: [
			{
				heading: 'Creare un backup',
				body: 'Il comando crea un dump logico di PostgreSQL, uno specchio dell’archivio MinIO, una copia della configurazione e un manifest, e li scrive in selfhost/backups/. Durante l’operazione API, worker e scheduler sono fermi e Caddy mostra la pagina di manutenzione: metti in conto qualche minuto in cui l’applicazione non è disponibile. Lo stesso backup si avvia da Profilo → Istanza → Backup con Crea backup.',
				code: './selfhost/bin/assozeta backup',
				list: [
					'Dump logico di PostgreSQL',
					'Specchio dell’archivio oggetti MinIO',
					'Copia della configurazione',
					'Manifest con data, versione, dimensione e origine dell’archivio'
				]
			},
			{
				heading: 'Dove finiscono e come proteggerli',
				body: 'Gli archivi stanno in selfhost/backups/ per impostazione predefinita. Contengono segreti, quindi vanno cifrati o conservati con permessi ristretti; un backup sullo stesso server non protegge da un guasto del disco o della macchina. Copiali su uno storage indipendente e verifica che la copia si apra.',
				list: [
					'Il download dalla pagina Istanza → Backup chiede di nuovo la password del proprietario',
					'Il file scaricato è l’archivio non cifrato del server, segreti compresi: trattalo come il server stesso',
					'La pagina Backup mostra data, dimensione, versione e origine di ogni archivio'
				]
			},
			{
				heading: 'Spazio libero e rotazione',
				body: 'Prima di fermare i servizi il comando controlla lo spazio nella destinazione: serve circa il doppio dello spazio di database e archivio oggetti, più un margine di 1024 MB (variabile ASSOZETA_BACKUP_SPACE_MARGIN_MB). Se lo spazio è poco, libera prima le copie già caricate fuori sede e gli archivi automatici più vecchi; se ancora non basta si ferma senza toccare i servizi e la pagina mostra lo spazio richiesto e quello disponibile.',
				list: [
					'La rotazione riguarda solo gli archivi automatici: conserva i più recenti ASSOZETA_BACKUP_KEEP (predefinito 3, 0 disattiva la rotazione)',
					'Non vengono mai eliminati gli archivi manuali, quelli pre-ripristino, quelli marcati Conserva e il backup dell’ultimo aggiornamento riuscito',
					'Ogni archivio ha un piccolo file .meta che registra l’origine: manual, pre-upgrade, scheduled o pre-restore',
					'Dalla pagina puoi conservare un archivio fuori rotazione, eliminarlo o pianificare backup automatici giornalieri o settimanali'
				]
			},
			{
				heading: 'Copie fuori sede',
				body: 'Da Profilo → Istanza → Backup → Copia remota puoi inviare una copia cifrata di ogni backup a un bucket S3-compatibile (AWS S3, Backblaze B2, Wasabi, MinIO e altri). La funzione è spenta per impostazione predefinita. La cifratura è obbligatoria e usa age: il server genera la coppia di chiavi e mostra la chiave di recupero una sola volta. Senza quella chiave le copie remote non si possono ripristinare.',
				list: [
					'Impostazioni: endpoint, regione, bucket, prefisso, chiavi di accesso, path-style e quante copie remote conservare (predefinito 3)',
					'I segreti stanno in selfhost/.updater/remote-backup.json con permessi 0600 e la chiave segreta non viene più mostrata',
					'Prova connessione scrive, legge e cancella un piccolo oggetto di test',
					'Accanto a ogni copia c’è un file .json con data, versione, dimensione, origine e id della chiave: nessun dato'
				]
			},
			{
				heading: 'Ripristinare un archivio',
				body: 'Il ripristino sostituisce database e archivio oggetti attuali. Prima di cambiare qualunque cosa, il comando salva i dati correnti in un nuovo archivio con origine pre-restore, e i servizi restano fermi fino alla fine. L’opzione --no-backup serve solo su un server nuovo, senza nulla da conservare. La sintassi è:',
				code: './selfhost/bin/assozeta restore \\\n  selfhost/backups/assozeta-20260101T120000Z.tar.gz',
				list: [
					'Il ripristino usa solo il .env attuale: non ripristina la configurazione contenuta nell’archivio',
					'Il dump viene prima caricato in un database di staging e gli oggetti in un bucket di staging',
					'Database e bucket correnti restano come copie di rollback finché migrazioni e controlli di prontezza non passano',
					'Redis viene svuotato prima del riavvio, per non riusare sessioni, cache e code vecchie'
				]
			},
			{
				heading: 'Ripristinare una copia remota',
				body: 'Su un server nuovo, già installato e configurato, puoi ripristinare direttamente dal bucket. Le impostazioni S3 arrivano dai flag o dalle variabili ASSOZETA_S3_*; le chiavi di accesso solo dalle variabili d’ambiente. Esporta prima ASSOZETA_S3_ACCESS_KEY e ASSOZETA_S3_SECRET_KEY. La chiave di recupero si legge dallo standard input: il primo comando elenca le copie disponibili, il secondo scarica, decifra e ripristina quella indicata.',
				code: './selfhost/bin/assozeta restore --from-s3 --bucket my-bucket --endpoint https://s3.example.com\n./selfhost/bin/assozeta restore --from-s3 assozeta-20260101T120000Z.tar.gz \\\n  --bucket my-bucket --endpoint https://s3.example.com'
			},
			{
				heading: 'Importare i dati da Bakney',
				body: 'Importare un export Bakney (ZIP nel formato bakney_sport_export_v1) è un’operazione diversa dal ripristino: si fa da Profilo → Istanza → Backup → Trasferisci dati → Importa da Bakney, sostituisce i dati dell’associazione e conserva configurazione, proprietario e accesso. Non passare mai uno ZIP Bakney al comando assozeta restore: quel comando vuole un archivio completo del server.',
				links: [
					{
						label: 'selfhost/DATA_RESTORE.md su GitHub',
						href: 'https://github.com/carbogninalberto/assozeta/blob/main/selfhost/DATA_RESTORE.md'
					}
				]
			}
		],
		footnotes:
			'Comandi, percorsi e valori predefiniti valgono per la release 1.0.39. Prova sempre un ripristino su un host separato prima di affidarti a una politica di backup.'
	},

	manutenzione: {
		slug: 'manutenzione',
		title: 'Diagnostica e manutenzione di Assozeta | Assozeta',
		description:
			'Stato, log, avvio e arresto dei servizi, migrazioni, lock delle operazioni, diagnostica dalla pagina Istanza e creazione di un amministratore.',
		heading: 'Diagnostica e manutenzione',
		intro:
			'Il pacchetto self-host include i comandi per guardare dentro l’installazione: stato dei servizi, log, avvio e arresto, migrazioni. La pagina Istanza aggiunge una diagnostica guidata, pensata per il proprietario.',
		sections: [
			{
				heading: 'Comandi di tutti i giorni',
				body: 'Cinque comandi coprono le verifiche più comuni. Eseguili dalla cartella che contiene selfhost/.',
				code: './selfhost/bin/assozeta status\n./selfhost/bin/assozeta logs api\n./selfhost/bin/assozeta stop\n./selfhost/bin/assozeta start\n./selfhost/bin/assozeta migrate',
				list: [
					'status mostra lo stato dei servizi',
					'logs api mostra il log dell’API',
					'stop ferma lo stack, Caddy compreso; start lo riavvia',
					'migrate applica le migrazioni del database'
				]
			},
			{
				heading: 'Un’operazione alla volta',
				body: 'Le operazioni che modificano l’installazione (install, start, stop, backup, restore, upgrade, uninstall) sono serializzate da un lock in selfhost/.lifecycle.lock/: due comandi non possono sovrapporsi. Se un arresto brusco lascia un lock vecchio, verifica che non ci sia davvero un’operazione in corso e solo allora rimuovi la cartella a mano.'
			},
			{
				heading: 'La pagina di manutenzione',
				body: 'Durante backup, migrazioni, ripristino e aggiornamento Caddy resta attivo e serve una pagina di manutenzione con stato HTTP 503, mentre API, worker e scheduler sono fermi. Il comando crea selfhost/.operations/maintenance.flag prima di fermarli e lo rimuove quando i servizi e i controlli di prontezza sono tornati a posto. Se l’avvio fallisce il flag resta: risolvi il problema, avvia l’applicazione e solo dopo rimuovilo. Dalla pagina Istanza, Riavvia applicazione riavvia tutti i servizi in circa un minuto senza aggiornare l’installazione.'
			},
			{
				heading: 'Diagnostica dalla pagina Istanza',
				body: 'In Profilo → Istanza → Panoramica il proprietario avvia i controlli con Verifica ora: sono limitati nel tempo, in sola lettura sui dati e non avviano pagamenti, email, backup o aggiornamenti. Ogni risultato dichiara il tipo di prova (Configurazione, Connessione o Test funzionale) con l’ora del controllo. Un segno di spunta vale solo per quella verifica e descrive l’ultimo controllo, non un monitoraggio continuo.',
				list: [
					'URL pubblico e HTTPS, con versione installata e certificato TLS',
					'API, database con una SELECT 1 e archivio oggetti',
					'Worker, scheduler e generatore PDF',
					'Email configurata, aggiornamenti e preparazione dei backup',
					'Lo stato generale dipende da URL pubblico, API, database, storage, worker, scheduler e generatore PDF; i problemi di email, integrazioni, aggiornamenti e backup restano visibili senza cambiarlo'
				]
			},
			{
				heading: 'Email di sistema: verifica e invio di prova',
				body: 'In Impostazioni → Email puoi salvare host, porta, cifratura, utente e mittente; la password viene cifrata con una chiave derivata dalla SECRET_KEY dell’installazione e non viene mai restituita. Verifica connessione apre una connessione e autentica senza inviare nulla; Invia email di prova manda un solo messaggio di verifica all’indirizzo indicato. Salvare non riscrive .env: le modifiche fatte a mano al file richiedono ancora il riavvio di API e worker.',
				list: [
					'Un invio riuscito significa che il server SMTP ha accettato il messaggio, non che sia arrivato: controlla anche la cartella spam',
					'Se la password salvata non è più leggibile serve sostituirla o rimuoverla esplicitamente',
					'Il reset torna ai valori dell’ambiente: elimina l’override nel database e non riscrive .env'
				]
			},
			{
				heading: 'Creare un amministratore',
				body: 'Se ti serve un account amministratore con accesso all’area Istanza, usa il comando dedicato con l’API in esecuzione. Chiede username, email, dati facoltativi e una password nascosta con conferma; username ed email già presenti vengono rifiutati senza modificare gli account esistenti.',
				code: './selfhost/bin/assozeta create-superuser'
			},
			{
				heading: 'Pulizia del disco dopo gli aggiornamenti',
				body: 'Ogni aggiornamento riuscito, da interfaccia o da riga di comando, esegue una pulizia automatica: rimuove le immagini Assozeta ufficiali non più usate e conserva la release corrente, quella immediatamente precedente e le immagini usate da container attivi o fermi. La cache di build Docker più vecchia di sette giorni viene potata. La pulizia è best effort: un errore appare come avviso e non trasforma un aggiornamento verificato in un fallimento. Volumi, backup, configurazione e snapshot di recupero non vengono toccati.',
				links: [
					{
						label: 'selfhost/OPERATIONS.md su GitHub',
						href: 'https://github.com/carbogninalberto/assozeta/blob/main/selfhost/OPERATIONS.md'
					}
				]
			}
		],
		footnotes:
			'Comandi e sezioni valgono per la release 1.0.39. Permessi, stato privato e casi limite sono descritti in selfhost/OPERATIONS.md.'
	}
};

/**
 * Related guide slugs shown in the page sidebar, per page.
 */
export const SELF_HOSTING_RELATED: Record<string, string[]> = {
	requisiti: ['installazione', 'configurazione'],
	installazione: ['requisiti', 'configurazione', 'aggiornamenti'],
	configurazione: ['installazione', 'manutenzione'],
	aggiornamenti: ['backup-e-ripristino', 'manutenzione'],
	'backup-e-ripristino': ['aggiornamenti', 'manutenzione'],
	manutenzione: ['requisiti', 'aggiornamenti', 'backup-e-ripristino']
};
