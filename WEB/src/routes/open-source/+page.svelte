<script lang="ts">
	import StandalonePage from '$lib/components/StandalonePage.svelte';
	import { LINKS, url } from '$lib/config/site';
	import { breadcrumbsFrom } from '$lib/data/routes';

	const meta = {
		title: 'Open source: un solo repository pubblico · Assozeta',
		description:
			'Il codice di Assozeta è pubblico su GitHub sotto licenza AGPL: nessuna edizione a pagamento, nessun costo per socio e la possibilità di adattare il brand della tua istanza.',
		canonical: url('/open-source')
	};

	const crumbs = breadcrumbsFrom('open-source', 'Open source');

	const sections = [
		{
			heading: 'Perché Assozeta è open source',
			body: 'Assozeta nasce dall’esperienza di Bakney, il gestionale cloud sviluppato da Bakney srl, e rende disponibile alla community una base completa e personalizzabile. Non esiste una versione separata a pagamento: quello che leggi su questo sito è quello che trovi nel repository.',
			list: [
				'Un solo repository pubblico su GitHub, con interfaccia, backend e stack di self-hosting',
				'Licenza GNU AGPL v3.0, indicata come AGPL-3.0-only',
				'Nessuna licenza da acquistare, nessun costo per socio o per utente'
			]
		},
		{
			heading: 'Cosa trovi nel repository',
			body: 'Il repository contiene tutto ciò che serve per studiare il progetto, avviare un’installazione e mantenerla nel tempo.',
			list: [
				'L’interfaccia Svelte nella cartella UI/',
				'Il backend Django e le API nella cartella BE/',
				'Lo stack Docker Compose per il self-hosting nella cartella selfhost/',
				'La configurazione di esempio documentata in selfhost/.env.example',
				'Il manuale utente in italiano, incluso nell’applicazione'
			],
			links: [{ label: 'Apri il repository su GitHub', href: LINKS.github }]
		},
		{
			heading: 'Come contribuire',
			body: 'Issue, correzioni e nuove funzionalità sono benvenute. Il flusso di lavoro è quello classico di GitHub.',
			list: [
				'Apri una issue per descrivere un bug o una modifica rilevante',
				'Crea un fork e un branch dedicato, ad esempio feature/nome-funzionalita',
				'Mantieni lo stile del codice esistente e limita la modifica allo scopo della pull request',
				'Esegui i controlli disponibili e aggiorna la documentazione interessata',
				'Apri una pull request spiegando comportamento, motivazione e modalità di verifica'
			],
			links: [
				{ label: 'Issue e richieste su GitHub', href: LINKS.issues },
				{ label: 'Repository e codice sorgente', href: LINKS.github }
			]
		},
		{
			heading: 'Community',
			body: 'Le domande su installazione e uso trovano risposta più in fretta nella community Telegram. Bug e richieste tracciabili passano invece da GitHub Issues, dove restano visibili a tutti.',
			links: [
				{ label: 'Community Assozeta su Telegram', href: LINKS.telegram },
				{ label: 'GitHub Issues', href: LINKS.issues }
			]
		},
		{
			heading: 'Personalizza il brand della tua istanza',
			body: 'Dalla pagina Profilo → Istanza → Impostazioni → Identità, riservata al proprietario dell’istanza, puoi cambiare l’identità dell’associazione senza toccare il codice e senza creare un fork.',
			list: [
				'Nome e abbreviazione dell’associazione',
				'Email di supporto mostrata nell’applicazione',
				'Colore primario e logo',
				'Le impostazioni restano modificabili anche dopo l’installazione'
			],
			links: [{ label: 'Come si installa Assozeta', href: '/self-hosting/installazione' }]
		},
		{
			heading: 'Dipendenze di terze parti',
			body: 'Le dipendenze e gli asset di terze parti mantengono le proprie licenze: quella di Assozeta non le sostituisce. L’elenco aggiornato è pubblicato nel repository.',
			links: [
				{
					label: 'UI/THIRD-PARTY-LICENSES.md su GitHub',
					href: 'https://github.com/carbogninalberto/assozeta/blob/main/UI/THIRD-PARTY-LICENSES.md'
				}
			]
		}
	];

	const footnotes =
		'Questa pagina descrive il progetto come è oggi. Per lo stato aggiornato di codice, issue e release fai riferimento al repository GitHub.';
</script>

<StandalonePage
	{meta}
	heading="Open source"
	intro="Assozeta è software libero: il codice è pubblico, la licenza è la GNU AGPL v3.0 e non esiste un’edizione a pagamento. Qui trovi cosa significa in pratica, come contribuire e come personalizzare la tua istanza."
	{sections}
	{footnotes}
	breadcrumbs={crumbs}
/>
