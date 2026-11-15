<script lang="ts">
	import StandalonePage from '$lib/components/StandalonePage.svelte';
	import { LINKS, url } from '$lib/config/site';
	import { breadcrumbsFrom } from '$lib/data/routes';

	const meta = {
		title: 'Licenza GNU AGPL v3.0 · Assozeta',
		description:
			'Cosa puoi fare con Assozeta sotto GNU AGPL v3.0, quali obblighi hai se modifichi il codice e dove leggere il testo completo della licenza.',
		canonical: url('/licenza')
	};

	const crumbs = breadcrumbsFrom('licenza', 'Licenza');

	const sections = [
		{
			heading: 'Licenza GNU AGPL v3.0',
			body: 'Assozeta è distribuito sotto GNU Affero General Public License versione 3.0, indicata nei metadati del progetto come AGPL-3.0-only. Il testo integrale accompagna il codice nel file LICENSE alla radice del repository; la Free Software Foundation pubblica la stessa versione sul proprio sito.',
			list: [
				'Licenza: GNU Affero General Public License v3.0 (AGPL-3.0-only)',
				'Testo integrale: file LICENSE nel repository',
				'Copia di riferimento: gnu.org/licenses/agpl-3.0.html'
			],
			links: [
				{
					label: 'LICENSE nel repository GitHub',
					href: 'https://github.com/carbogninalberto/assozeta/blob/main/LICENSE'
				},
				{ label: 'Testo della licenza sul sito GNU', href: 'https://www.gnu.org/licenses/agpl-3.0.html' }
			]
		},
		{
			heading: 'Cosa puoi fare',
			body: 'La licenza ti riconosce le libertà del software libero, per qualsiasi scopo, anche commerciale.',
			list: [
				'Usare Assozeta per gestire la tua associazione',
				'Studiare il codice e capire come funziona',
				'Modificarlo per adattarlo alle tue esigenze',
				'Ridistribuire copie originali o modificate'
			]
		},
		{
			heading: 'Cosa richiede',
			body: 'L’AGPL è una licenza copyleft: se distribuisci una versione modificata, quella versione deve restare sotto la stessa licenza e chi la riceve deve poter accedere al codice sorgente corrispondente.',
			list: [
				'Mantenere gli avvisi di copyright e di licenza',
				'Rendere disponibile il codice sorgente della versione modificata',
				'Indicare le modifiche apportate al codice originale',
				'Applicare la stessa licenza alle versioni che distribuisci'
			]
		},
		{
			heading: 'Se modifichi Assozeta e lo offri in rete',
			body: 'L’AGPL estende l’obbligo al software usato attraverso una rete. Se pubblichi una versione modificata come servizio accessibile agli utenti, devi mettere a loro disposizione il codice sorgente corrispondente.',
			list: [
				'L’obbligo vale anche quando gli utenti non scaricano il programma',
				'Riguarda la versione che stai eseguendo, non solo quella che distribuisci',
				'Il codice deve essere raggiungibile dagli utenti del servizio'
			]
		},
		{
			heading: 'Nessuna garanzia',
			body: 'Il software è fornito così com’è, senza garanzie di funzionamento o di idoneità a uno scopo specifico. Chi installa Assozeta resta responsabile di configurazione, backup, aggiornamenti e accessi all’istanza.'
		},
		{
			heading: 'Dipendenze e asset di terze parti',
			body: 'Le librerie e gli asset di terze parti mantengono le rispettive licenze. L’elenco aggiornato è nel repository, così puoi verificare caso per caso.',
			links: [
				{
					label: 'UI/THIRD-PARTY-LICENSES.md su GitHub',
					href: 'https://github.com/carbogninalberto/assozeta/blob/main/UI/THIRD-PARTY-LICENSES.md'
				},
				{ label: 'Il progetto su GitHub', href: LINKS.github }
			]
		}
	];

	const footnotes =
		'Questa pagina è un riassunto e non sostituisce il testo della licenza. Per una valutazione legale fai riferimento al testo integrale e, se serve, a un consulente.';
</script>

<StandalonePage
	{meta}
	heading="Licenza GNU AGPL v3.0"
	intro="Assozeta è distribuito sotto GNU Affero General Public License versione 3.0. Puoi usarlo, studiarlo, modificarlo e ridistribuirlo; se modifichi il codice e lo offri in rete, devi rendere disponibile il sorgente della tua versione."
	{sections}
	{footnotes}
	breadcrumbs={crumbs}
/>
