<script lang="ts">
	import type { Component } from 'svelte';
	import ArrowRight from '@lucide/svelte/icons/arrow-right';
	import CalendarCheck from '@lucide/svelte/icons/calendar-check';
	import FileHeart from '@lucide/svelte/icons/file-heart';
	import FilePenLine from '@lucide/svelte/icons/file-pen-line';
	import Landmark from '@lucide/svelte/icons/landmark';
	import LayoutDashboard from '@lucide/svelte/icons/layout-dashboard';
	import Mail from '@lucide/svelte/icons/mail';
	import Receipt from '@lucide/svelte/icons/receipt';
	import Users from '@lucide/svelte/icons/users';
	import Breadcrumbs from '$lib/components/Breadcrumbs.svelte';
	import CtaSection from '$lib/components/CtaSection.svelte';
	import Footer from '$lib/components/Footer.svelte';
	import Nav from '$lib/components/Nav.svelte';
	import Seo from '$lib/components/Seo.svelte';
	import { reveal } from '$lib/actions/reveal';
	import { url } from '$lib/config/site';
	import { HUBS, breadcrumbsFrom, webPageLd } from '$lib/data/routes';

	const hub = HUBS.funzionalita;
	const title = 'Funzionalità del gestionale per ASD e SSD | Assozeta';
	const description =
		'Soci, iscrizioni online, corsi e presenze, certificati, pagamenti, contabilità, comunicazioni e report: cosa puoi fare con Assozeta.';
	const canonical = url(hub.href);
	const crumbs = breadcrumbsFrom(hub.slug, hub.label);
	const jsonLd = webPageLd(title, description, canonical, crumbs);

	type IconComponent = Component<{ class?: string }>;

	const icons: Record<string, IconComponent> = {
		'soci-e-tesserati': Users,
		'iscrizioni-online': FilePenLine,
		'corsi-e-presenze': CalendarCheck,
		'certificati-e-documenti': FileHeart,
		'pagamenti-e-ricevute': Receipt,
		contabilita: Landmark,
		'comunicazioni-e-automazioni': Mail,
		'bacheca-e-report': LayoutDashboard
	};

	const integrations = [
		{
			name: 'SMTP',
			description:
				'Invio delle email dell’associazione: senza un server SMTP configurato le comunicazioni restano disattivate.'
		},
		{
			name: 'Stripe',
			description:
				'Pagamenti online per i flussi supportati, tramite l’account Stripe Connect collegato all’istanza.'
		},
		{
			name: 'Google Calendar',
			description: 'Sincronizzazione dei calendari dei corsi con l’account Google dell’associazione.'
		},
		{
			name: 'Accesso con Google e Apple',
			description: 'Accesso con l’account Google o Apple, quando i client ID dei due servizi sono configurati.'
		},
		{
			name: 'Agente AI compatibile OpenAI',
			description:
				'Un agente AI collegato a un provider con API compatibili OpenAI, con chiave, modello e indirizzo configurati sull’istanza.'
		}
	];
</script>

<Seo meta={{ title, description, canonical, jsonLd }} />

<Nav />

<main id="main" tabindex="-1" class="outline-none mx-auto max-w-6xl px-6 pt-28 pb-20">
	<div class="mb-8">
		<Breadcrumbs breadcrumbs={crumbs} />
	</div>

	<div use:reveal class="max-w-3xl">
		<p class="font-mono text-xs tracking-widest text-brand uppercase">Gestionale per ASD e SSD</p>
		<h1 class="mt-3 text-4xl font-bold tracking-tight sm:text-5xl">
			<span class="gradient-text">Funzionalità</span>
		</h1>
		<p class="mt-4 text-lg leading-relaxed text-muted-foreground">
			Assozeta riunisce in un unico gestionale soci, iscrizioni, corsi, presenze, documenti, pagamenti, contabilità e
			comunicazioni. Le pagine qui sotto raccontano cosa può fare la segreteria in ogni area, con i passaggi
			principali.
		</p>
	</div>

	<div class="mt-12 grid gap-4 sm:grid-cols-2 lg:grid-cols-3">
		{#each hub.children as child, i}
			{@const Icon = icons[child.slug] ?? Users}
			<a
				href={child.href}
				use:reveal={{ delay: i * 50 }}
				class="group flex flex-col rounded-xl border border-border bg-card p-6 transition-colors hover:border-primary/40"
			>
				<span class="flex size-10 items-center justify-center rounded-lg bg-brand-soft text-brand">
					<Icon class="size-5" />
				</span>
				<h2 class="mt-4 text-lg font-semibold tracking-tight">{child.label}</h2>
				{#if child.description}
					<p class="mt-2 flex-1 text-sm leading-relaxed text-muted-foreground">{child.description}</p>
				{/if}
				<span class="mt-4 inline-flex items-center gap-1.5 text-sm font-medium text-brand">
					Scopri di più
					<ArrowRight class="size-4 transition-transform group-hover:translate-x-0.5" />
				</span>
			</a>
		{/each}
	</div>

	<section use:reveal class="mt-20">
		<p class="font-mono text-xs tracking-widest text-brand uppercase">Integrazioni</p>
		<h2 class="mt-3 text-2xl font-bold tracking-tight sm:text-3xl">Integrazioni facoltative</h2>
		<p class="mt-3 max-w-3xl leading-relaxed text-muted-foreground">
			Alcune funzioni si appoggiano a servizi esterni. Ognuno richiede le proprie credenziali, configurate
			sull’istanza: se i campi restano vuoti, l’integrazione è disattivata e il resto del gestionale continua a
			funzionare.
		</p>

		<ul class="mt-8 grid gap-4 sm:grid-cols-2">
			{#each integrations as integration, i}
				<li
					use:reveal={{ delay: i * 50 }}
					class="rounded-xl border border-border bg-subtle p-5"
				>
					<h3 class="font-semibold tracking-tight">{integration.name}</h3>
					<p class="mt-1.5 text-sm leading-relaxed text-muted-foreground">{integration.description}</p>
				</li>
			{/each}
		</ul>

		<p class="mt-6 text-sm text-muted-foreground">
			Trovi variabili e passaggi nella pagina
			<a
				href="/self-hosting/configurazione"
				class="text-brand underline-offset-4 hover:text-brand-strong hover:underline"
				>Configurazione e integrazioni</a
			>.
		</p>
	</section>

	<CtaSection />
</main>

<Footer />
