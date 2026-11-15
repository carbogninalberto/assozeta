<script lang="ts">
	import Mail from '@lucide/svelte/icons/mail';
	import MessagesSquare from '@lucide/svelte/icons/messages-square';
	import Github from '$lib/components/GithubIcon.svelte';
	import StandalonePage from '$lib/components/StandalonePage.svelte';
	import { LINKS, url } from '$lib/config/site';
	import { breadcrumbsFrom } from '$lib/data/routes';

	const meta = {
		title: 'Chi siamo: il progetto e Bakney · Assozeta',
		description:
			'Assozeta nasce dall’esperienza di Bakney srl ed è sviluppato con la community: un gestionale open source che ogni associazione sportiva può installare e controllare.',
		canonical: url('/chi-siamo')
	};

	const crumbs = breadcrumbsFrom('chi-siamo', 'Chi siamo');

	const sections = [
		{
			heading: 'Perché esiste Assozeta',
			body: 'Assozeta nasce dall’esperienza di Bakney, il gestionale cloud sviluppato da Bakney srl, e rende disponibile alla community una base open source completa e personalizzabile. L’obiettivo è semplice: dare alle associazioni sportive dilettantistiche un gestionale completo che possano installare e controllare.',
			list: [
				'Un gestionale per ASD e SSD, non un insieme di fogli di calcolo',
				'Software libero sotto licenza GNU AGPL v3.0',
				'Installazione su un proprio server Linux con Docker Compose',
				'Una sola associazione per installazione'
			]
		},
		{
			heading: 'Installare e controllare',
			body: 'Assozeta è self-hosted: l’associazione installa l’applicazione sul proprio server e resta proprietaria dei dati che inserisce. Il primo avvio crea l’account del proprietario e l’associazione, e la procedura guidata può essere completata una sola volta.',
			list: [
				'Database e archivio dei file restano sul server scelto dall’associazione',
				'Il proprietario gestisce account, collaboratori e configurazione',
				'Export e import completi dei dati nel formato applicativo',
				'Backup e aggiornamenti documentati nella guida al self-hosting'
			],
			links: [{ label: 'Come si installa Assozeta', href: '/self-hosting/installazione' }]
		},
		{
			heading: 'Sviluppato con la community',
			body: 'Il codice è pubblico su GitHub e il progetto è aperto ai contributi esterni. Issue, fork e pull request sono il canale per proporre correzioni e nuove funzionalità; la community Telegram è il posto giusto per confrontarsi su installazione e uso.',
			links: [
				{ label: 'Repository e codice sorgente', href: LINKS.github },
				{ label: 'Issue e richieste', href: LINKS.issues },
				{ label: 'Community su Telegram', href: LINKS.telegram }
			]
		},
		{
			heading: 'Il legame con Bakney',
			body: 'Bakney srl ha sviluppato Bakney Sport, il gestionale cloud per le associazioni sportive, annunciato in chiusura dal 30 settembre 2026. Il 5 agosto 2026 ha pubblicato il codice sorgente e la documentazione per il self-hosting di Assozeta sotto licenza AGPL-3.0, come continuazione open source nata da quell’esperienza. Bakney srl resta l’azienda che pubblica questo sito e risponde a support@bakney.com.',
			list: [
				'Chi usava Bakney può importare in Assozeta il proprio export nel formato bakney_sport_export_v1',
				'Prima di migrare va verificata la compatibilità dell’export con la versione installata',
				'Bakney srl, Q.re Aldo Moro 49, 37032 Monteforte d’Alpone (VR), Italia',
				'P. IVA 04921790236 · N. REA VR-456410',
				'Capitale sociale € 1.000,00 i.v.'
			],
			links: [
				{ label: 'Importare i dati: backup e ripristino', href: '/self-hosting/backup-e-ripristino#importare-i-dati-da-bakney' },
				{ label: 'Che cosa permette la licenza AGPL', href: '/licenza' }
			]
		}
	];
</script>

<StandalonePage
	{meta}
	heading="Chi siamo"
	intro="Assozeta è un progetto open source per le associazioni sportive dilettantistiche, nato dall’esperienza di Bakney e sviluppato insieme a chi lo usa."
	{sections}
	breadcrumbs={crumbs}
>
	{#snippet children()}
		<h2 class="flex items-center gap-3 text-2xl font-semibold tracking-tight">
			<span class="h-px w-6 bg-primary/40"></span>
			Contatti
		</h2>
		<p class="mt-3 leading-relaxed text-muted-foreground">
			Per domande sul progetto, sull’installazione o sui contributi puoi scriverci o passare dalla community.
		</p>
		<ul class="mt-6 grid gap-3 sm:grid-cols-3">
			<li
				class="rounded-xl border border-border bg-card p-4 transition-colors hover:border-primary/30 hover:bg-subtle"
			>
				<a href="mailto:{LINKS.email}" class="flex flex-col gap-1.5">
					<Mail class="size-4 text-brand" />
					<span class="text-sm font-medium text-foreground">Email</span>
					<span class="text-xs text-muted-foreground">{LINKS.email}</span>
				</a>
			</li>
			<li
				class="rounded-xl border border-border bg-card p-4 transition-colors hover:border-primary/30 hover:bg-subtle"
			>
				<a href={LINKS.telegram} target="_blank" rel="noopener noreferrer" class="flex flex-col gap-1.5">
					<MessagesSquare class="size-4 text-brand" />
					<span class="text-sm font-medium text-foreground">Telegram</span>
					<span class="text-xs text-muted-foreground">Community Assozeta</span>
				</a>
			</li>
			<li
				class="rounded-xl border border-border bg-card p-4 transition-colors hover:border-primary/30 hover:bg-subtle"
			>
				<a href={LINKS.github} target="_blank" rel="noopener noreferrer" class="flex flex-col gap-1.5">
					<Github class="size-4 text-brand" />
					<span class="text-sm font-medium text-foreground">GitHub</span>
					<span class="text-xs text-muted-foreground">Codice e issue</span>
				</a>
			</li>
		</ul>
	{/snippet}
</StandalonePage>
