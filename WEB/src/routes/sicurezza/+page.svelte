<script lang="ts">
	import StandalonePage from '$lib/components/StandalonePage.svelte';
	import { LINKS, url } from '$lib/config/site';
	import { breadcrumbsFrom } from '$lib/data/routes';

	const meta = {
		title: 'Sicurezza e responsabilità di chi installa · Assozeta',
		description:
			'Cosa fa Assozeta per la sicurezza, come sono protetti segreti e backup e cosa resta a chi installa l’istanza: aggiornamenti, firewall, accessi e segnalazione di vulnerabilità.',
		canonical: url('/sicurezza')
	};

	const crumbs = breadcrumbsFrom('sicurezza', 'Sicurezza');

	const sections = [
		{
			heading: 'Responsabilità condivisa',
			body: 'Assozeta fornisce il codice dell’applicazione e uno stack Docker Compose di riferimento. Chi installa l’istanza controlla il server, la rete, i segreti, i backup e il calendario degli aggiornamenti: sono due ambiti distinti e vanno considerati entrambi.',
			list: [
				'Il progetto: codice applicativo, migrazioni del database e configurazione di riferimento',
				'Chi installa: sistema operativo, accessi SSH, firewall e rete',
				'Chi installa: backup cifrati, conservati anche fuori dal server',
				'Chi installa: verifica e applicazione degli aggiornamenti'
			]
		},
		{
			heading: 'Accessi e autenticazione',
			body: 'Gli account possono proteggersi con l’autenticazione a due fattori TOTP. Le sessioni usano token JWT firmati con chiavi EdDSA (Ed25519), generate dall’installazione; il primo avvio richiede un token di setup generato dall’installer e la procedura guidata può essere completata una sola volta.',
			list: [
				'Autenticazione a due fattori TOTP per gli account',
				'Audit log delle attività sull’istanza',
				'Token JWT firmati con EdDSA (Ed25519)',
				'Token di setup per il primo avvio, da trattare come un segreto',
				'Collaboratori invitati con un accesso proprio, ad esempio commercialista o istruttori'
			]
		},
		{
			heading: 'Segreti e configurazione',
			body: 'L’installazione genera selfhost/.env con i segreti dell’istanza e lo crea con permessi 0600, leggibili solo dall’utente che esegue i comandi. Il file non va versionato né condiviso; selfhost/.env.example documenta ogni variabile senza contenere valori reali.',
			list: [
				'File dei segreti generato dall’installer con permessi 0600',
				'Chiavi JWT Ed25519 create durante l’installazione',
				'Variabili documentate in selfhost/.env.example',
				'Modifiche applicate con ./selfhost/bin/assozeta restart'
			]
		},
		{
			heading: 'Rete ed esposizione',
			body: 'Nello stack di riferimento solo Caddy pubblica le porte 80 e 443 e ottiene il certificato HTTPS in modo automatico quando il nome DNS punta al server. Database, Redis, storage degli oggetti e renderer PDF restano sulla rete interna di Docker Compose.',
			list: [
				'Ingresso pubblico unicamente da Caddy, sulle porte 80 e 443',
				'HTTPS automatico con certificati rinnovati da Caddy',
				'PostgreSQL, Redis e MinIO senza porte pubblicate',
				'Renderer PDF Chromium in un servizio privato dello stack',
				'HTTPS funziona solo se il DNS e la rete sono configurati correttamente'
			]
		},
		{
			heading: 'Dati, firme e backup',
			body: 'Nello stack self-hosted lo storage degli oggetti (MinIO) è privato: le firme sono salvate con una chiave privata e lette attraverso il backend. I backup contengono il dump del database, la copia dell’archivio, la configurazione e il manifest: includono segreti e dati personali.',
			list: [
				'Firme nello storage privato, senza URL pubblici',
				'Backup con database, archivio, configurazione e manifest',
				'Backup da cifrare, proteggere con permessi stretti e copiare fuori dal server',
				'Rotazione automatica degli archivi programmati e pre-aggiornamento',
				'Archivi manuali e pre-ripristino conservati finché non li rimuovi'
			],
			links: [{ label: 'Backup e ripristino: procedura documentata', href: '/self-hosting/backup-e-ripristino' }]
		},
		{
			heading: 'Aggiornamenti',
			body: 'Il proprietario dell’istanza sceglie una release stabile dopo aver letto le note di rilascio e quelle di tutte le versioni intermedie, poi conferma l’aggiornamento dalla pagina Istanza oppure da riga di comando. Ogni aggiornamento crea prima un backup, da conservare.',
			list: [
				'Solo release stabili, con revisione delle note di rilascio',
				'Conferma dal proprietario, dalla pagina Istanza o dalla CLI',
				'Backup automatico prima di ogni aggiornamento',
				'Pagina di manutenzione servita da Caddy durante backup, migrazioni e ripristino',
				'Ripristinare le immagini precedenti non annulla le migrazioni del database'
			],
			links: [{ label: 'Come funzionano gli aggiornamenti', href: '/self-hosting/aggiornamenti' }]
		},
		{
			heading: 'Cosa resta a chi installa',
			body: 'Nessuna configurazione di riferimento può sostituire la manutenzione del server. Prima di mettere Assozeta in produzione vale la pena definire chi se ne occupa e con quale periodicità.',
			list: [
				'Mantenere aggiornato il sistema operativo del server',
				'Configurare il firewall e limitare gli accessi SSH',
				'Gestire utenti, ruoli e rimozione degli accessi non più necessari',
				'Conservare backup esterni e provare il ripristino',
				'Monitorare spazio disco, log e stato dei servizi'
			]
		},
		{
			heading: 'Segnalare una vulnerabilità',
			body: 'Se trovi una vulnerabilità, scrivi in privato a support@bakney.com invece di aprire una issue pubblica. Indica la versione interessata, i passi per riprodurre il problema e l’impatto che hai osservato, usando solo i dati necessari.',
			list: [
				'Segnalazione privata a support@bakney.com',
				'Nessuna issue pubblica per le vulnerabilità',
				'Tempo per analizzare il problema e preparare una correzione prima della divulgazione'
			],
			links: [{ label: 'Scrivi a support@bakney.com', href: `mailto:${LINKS.email}` }]
		}
	];

	const footnotes =
		'Questa pagina descrive i controlli presenti nel codice e nella configurazione di riferimento. Non contiene dichiarazioni di conformità a norme o certificazioni: la sicurezza di un’installazione dipende anche da come viene gestita.';
</script>

<StandalonePage
	{meta}
	heading="Sicurezza"
	intro="Assozeta mette a disposizione autenticazione a due fattori, audit log, token firmati e uno stack in cui solo il proxy web è esposto. Il resto dipende da come gestisci il server su cui lo installi."
	{sections}
	{footnotes}
	breadcrumbs={crumbs}
/>
