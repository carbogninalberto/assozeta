<script lang="ts">
	import type { Component } from 'svelte';
	import Breadcrumbs from '$lib/components/Breadcrumbs.svelte';
	import CtaSection from '$lib/components/CtaSection.svelte';
	import Footer from '$lib/components/Footer.svelte';
	import Nav from '$lib/components/Nav.svelte';
	import Seo from '$lib/components/Seo.svelte';
	import { Button } from '$lib/components/ui/button';
	import { reveal } from '$lib/actions/reveal';
	import { url } from '$lib/config/site';
	import { HUBS, breadcrumbsFrom, webPageLd } from '$lib/data/routes';
	import ArrowRight from '@lucide/svelte/icons/arrow-right';
	import DatabaseBackup from '@lucide/svelte/icons/database-backup';
	import Download from '@lucide/svelte/icons/download';
	import RefreshCw from '@lucide/svelte/icons/refresh-cw';
	import Server from '@lucide/svelte/icons/server';
	import Settings from '@lucide/svelte/icons/settings';
	import ShieldCheck from '@lucide/svelte/icons/shield-check';
	import Stethoscope from '@lucide/svelte/icons/stethoscope';

	const hub = HUBS.selfHosting;
	const title = 'Installare Assozeta sul tuo server con Docker | Assozeta';
	const description =
		'Requisiti, installazione con Docker Compose, configurazione, aggiornamenti, backup e manutenzione di un’istanza Assozeta sul tuo server.';
	const canonical = url(hub.href);

	const crumbs = breadcrumbsFrom('self-hosting', 'Self-hosting');
	const jsonLd = webPageLd(title, description, canonical, crumbs);

	const facts = ['Docker Compose v2.20+', '4 GB di RAM', 'HTTPS automatico', 'Licenza AGPL-3.0'];

	const steps = [
		{
			number: '01',
			title: 'Prepara il server',
			description: 'Linux, Docker Compose 2.20 o superiore, un nome DNS pubblico e le porte 80 e 443 aperte.',
			href: '/self-hosting/requisiti',
			cta: 'Vedi i requisiti'
		},
		{
			number: '02',
			title: 'Installa',
			description: 'Scarica il pacchetto della release, estrailo ed esegui il comando install con dominio ed email.',
			href: '/self-hosting/installazione',
			cta: 'Guida all’installazione'
		},
		{
			number: '03',
			title: 'Completa la procedura guidata',
			description: 'Apri l’indirizzo stampato, incolla il token di setup e crea proprietario e associazione.',
			href: '/self-hosting/installazione',
			cta: 'Token e primo avvio'
		},
		{
			number: '04',
			title: 'Aggiorna e fai i backup',
			description: 'Porta l’istanza a una nuova release e conserva copie dei dati anche fuori dal server.',
			href: '/self-hosting/aggiornamenti',
			cta: 'Aggiornamenti e backup'
		}
	];

	const services = [
		{ name: 'Caddy', role: 'Gestisce HTTPS, ottiene il certificato da solo e serve l’applicazione web.' },
		{ name: 'Applicazione web', role: 'L’interfaccia che apri nel browser, su PC, tablet e smartphone.' },
		{ name: 'API', role: 'Risponde alle richieste dell’interfaccia e delle integrazioni attive.' },
		{ name: 'Worker e scheduler', role: 'Eseguono i processi in background e le attività pianificate.' },
		{ name: 'PostgreSQL', role: 'Il database dell’associazione: soci, corsi, pagamenti, contabilità.' },
		{ name: 'Redis', role: 'Code di lavoro, sessioni e stato condiviso tra i servizi.' },
		{ name: 'MinIO', role: 'Archivio oggetti privato per documenti, allegati e firme.' },
		{ name: 'Generatore PDF', role: 'Un Chromium privato che produce i documenti in PDF.' }
	];

	const guideIcons: Record<string, Component> = {
		requisiti: Server,
		installazione: Download,
		configurazione: Settings,
		aggiornamenti: RefreshCw,
		'backup-e-ripristino': DatabaseBackup,
		manutenzione: Stethoscope
	};
</script>

<Seo meta={{ title, description, canonical, jsonLd }} />

<Nav />

<main id="main" tabindex="-1" class="outline-none">
	<section class="relative overflow-hidden pt-28 pb-12 sm:pt-36 sm:pb-16">
		<div class="hero-grid pointer-events-none absolute inset-0 -z-10 opacity-60" aria-hidden="true"></div>
		<div
			class="pointer-events-none absolute -top-44 left-1/2 -z-10 h-[420px] w-[680px] -translate-x-1/2 rounded-full bg-primary/10 blur-[140px]"
			aria-hidden="true"
		></div>
		<div class="mx-auto max-w-6xl px-6">
			<div class="mb-8">
				<Breadcrumbs breadcrumbs={crumbs} />
			</div>

			<div use:reveal class="max-w-3xl">
				<p class="font-mono text-xs tracking-widest text-brand uppercase">Self-hosting · Docker Compose</p>
				<h1 class="mt-4 text-4xl font-bold tracking-tight sm:text-5xl">
					<span class="gradient-text">Self-hosting</span>
				</h1>
				<p class="mt-5 text-lg leading-relaxed text-muted-foreground">
					Assozeta si installa sul tuo server Linux con Docker Compose: i dati restano tuoi, in un database e in un
					archivio oggetti privato. Queste guide coprono requisiti, installazione, configurazione, aggiornamenti, backup e
					manutenzione, con i comandi così come sono documentati nel pacchetto self-host.
				</p>
				<div class="mt-7 flex flex-wrap items-center gap-3">
					<Button
						href="/self-hosting/requisiti"
						size="lg"
						class="h-11 bg-primary px-6 text-base text-primary-foreground hover:bg-primary-hover"
					>
						Inizia dai requisiti
						<ArrowRight />
					</Button>
					<Button href="/self-hosting/installazione" variant="outline" size="lg" class="h-11 px-6 text-base">
						Guida all’installazione
					</Button>
				</div>
			</div>

			<ul class="mt-10 flex flex-wrap items-center gap-x-4 gap-y-2 font-mono text-xs text-muted-foreground">
				{#each facts as fact, i}
					{#if i > 0}
						<li aria-hidden="true" class="text-muted-foreground/40">/</li>
					{/if}
					<li>{fact}</li>
				{/each}
			</ul>
		</div>
	</section>

	<section class="mx-auto max-w-6xl px-6 pt-6 pb-16 sm:pt-8 sm:pb-20" aria-labelledby="passi">
		<div class="flex flex-wrap items-end justify-between gap-3">
			<h2 id="passi" class="text-2xl font-semibold tracking-tight sm:text-3xl">Quattro passi per partire</h2>
			<p class="font-mono text-xs tracking-widest text-muted-foreground uppercase">Dal server vuoto alla prima iscrizione</p>
		</div>

		<ol class="mt-6 grid gap-4 sm:grid-cols-2 lg:grid-cols-4">
			{#each steps as step, i}
				<li use:reveal={{ delay: i * 50 }}>
					<a
						href={step.href}
						class="group flex h-full flex-col rounded-xl border border-border bg-card p-5 transition-all duration-300 hover:-translate-y-1 hover:border-primary/30"
					>
						<span
							class="inline-flex size-9 items-center justify-center rounded-lg border border-border bg-subtle font-mono text-sm text-brand"
							>{step.number}</span
						>
						<h3 class="mt-4 text-base font-semibold">{step.title}</h3>
						<p class="mt-1.5 flex-1 text-sm leading-relaxed text-muted-foreground">{step.description}</p>
						<span class="mt-4 inline-flex items-center gap-1 text-xs font-medium text-brand">
							{step.cta}
							<ArrowRight class="size-3.5 transition-transform group-hover:translate-x-0.5" aria-hidden="true" />
						</span>
					</a>
				</li>
			{/each}
		</ol>
	</section>

	<section class="mx-auto max-w-6xl px-6 pb-16 sm:pb-20" aria-labelledby="servizi">
		<div class="grid gap-8 lg:grid-cols-[minmax(0,1fr)_minmax(0,1.5fr)] lg:gap-12">
			<div use:reveal>
				<p class="font-mono text-xs tracking-widest text-brand uppercase">Cosa gira sul server</p>
				<h2 id="servizi" class="mt-3 text-2xl font-semibold tracking-tight sm:text-3xl">Cosa viene installato</h2>
				<p class="mt-3 leading-relaxed text-muted-foreground">
					Tutto in un unico progetto Docker Compose. Non devi configurare un database, un reverse proxy o uno storage: li
					prepara il comando di installazione, e i dati restano sulla macchina che scegli tu.
				</p>
			</div>

			<ul class="grid gap-x-8 gap-y-4 sm:grid-cols-2">
				{#each services as service}
					<li class="border-t border-border pt-3">
						<p class="text-sm font-semibold">{service.name}</p>
						<p class="mt-0.5 text-sm leading-relaxed text-muted-foreground">{service.role}</p>
					</li>
				{/each}
			</ul>
		</div>

		<div class="mt-8 flex items-start gap-3 rounded-xl border border-primary/30 bg-brand-soft p-4">
			<ShieldCheck class="mt-0.5 size-5 shrink-0 text-brand" aria-hidden="true" />
			<p class="text-sm leading-relaxed text-muted-foreground">
				<span class="font-medium text-foreground">Solo Caddy è esposto su internet.</span>
				PostgreSQL, Redis, MinIO e il generatore PDF non pubblicano porte: restano sulla rete interna di Docker e si
				raggiungono solo dall’applicazione e dai comandi di manutenzione.
			</p>
		</div>
	</section>

	<section class="mx-auto max-w-6xl px-6 pb-4" aria-labelledby="guide">
		<p class="font-mono text-xs tracking-widest text-brand uppercase">Le guide</p>
		<h2 id="guide" class="mt-3 text-2xl font-semibold tracking-tight sm:text-3xl">
			Sei guide, nell’ordine in cui servono
		</h2>
		<p class="mt-3 max-w-2xl leading-relaxed text-muted-foreground">
			Si leggono in sequenza la prima volta, poi restano come riferimento: ogni pagina contiene i comandi esatti e cosa
			aspettarsi dopo averli eseguiti.
		</p>

		<div class="mt-6 grid gap-4 sm:grid-cols-2 lg:grid-cols-3">
			{#each hub.children as child, i}
				{@const Icon = guideIcons[child.slug] ?? Server}
				<a
					href={child.href}
					use:reveal={{ delay: i * 50 }}
					class="group flex h-full flex-col rounded-xl border border-border bg-card p-5 transition-all duration-300 hover:-translate-y-1 hover:border-primary/30"
				>
					<div class="flex items-center justify-between gap-3">
						<span class="inline-flex size-10 items-center justify-center rounded-lg bg-brand-soft text-brand">
							<Icon class="size-5" aria-hidden="true" />
						</span>
						<span class="font-mono text-xs text-muted-foreground/60">{String(i + 1).padStart(2, '0')}</span>
					</div>
					<h3 class="mt-4 text-base font-semibold">{child.label}</h3>
					{#if child.description}
						<p class="mt-1.5 flex-1 text-sm leading-relaxed text-muted-foreground">{child.description}</p>
					{/if}
					<span class="mt-3 inline-flex items-center gap-1 text-xs font-medium text-brand">
						Apri la guida
						<ArrowRight class="size-3.5 transition-transform group-hover:translate-x-0.5" aria-hidden="true" />
					</span>
				</a>
			{/each}
		</div>

		<CtaSection />
	</section>
</main>

<Footer />
