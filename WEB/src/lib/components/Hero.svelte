<script lang="ts">
	import ArrowRight from '@lucide/svelte/icons/arrow-right';
	import Star from '@lucide/svelte/icons/star';
	import Github from '$lib/components/GithubIcon.svelte';
	import { Button } from '$lib/components/ui/button';
	import HeroDashboard from '$lib/components/home/HeroDashboard.svelte';
	import { LINKS, MIN_STARS_SHOWN } from '$lib/config/site';

	let {
		stars = null,
		release
	}: {
		stars?: number | null;
		release: { version: string; url: string; publishedAt: string | null };
	} = $props();

	const facts = ['Licenza AGPL-3.0', '0 € per socio', 'Nessuna versione a pagamento', 'Self-hosted', 'Interfaccia in italiano'];

	const starLabel = $derived(
		// A small count reads as a weak signal: show it only from MIN_STARS_SHOWN upwards.
		stars === null || stars < MIN_STARS_SHOWN ? null : new Intl.NumberFormat('it-IT', { notation: 'compact', maximumFractionDigits: 1 }).format(stars)
	);
</script>

<section class="relative overflow-hidden pt-32 pb-24 sm:pt-40">
	<!-- Faint grid and one calm glow behind the headline -->
	<div class="hero-grid pointer-events-none absolute inset-0 -z-10 opacity-60" aria-hidden="true"></div>
	<div
		class="absolute -top-48 left-1/2 -z-10 h-[460px] w-[760px] -translate-x-1/2 rounded-full bg-primary/10 blur-[150px] dark:bg-primary/15"
		aria-hidden="true"
	></div>
	<div
		class="absolute top-[38rem] -left-40 -z-10 h-[360px] w-[520px] rounded-full bg-primary/10 blur-[140px]"
		aria-hidden="true"
	></div>

	<div class="mx-auto max-w-6xl px-6">
		<div class="mx-auto max-w-4xl text-center">
			<a
				href={release.url}
				target="_blank"
				rel="noopener"
				class="group inline-flex max-w-full items-center gap-2 rounded-full border border-border bg-subtle py-1 pr-3 pl-1.5 text-xs text-muted-foreground transition-colors hover:border-primary/30 hover:text-foreground"
			>
				<span class="shrink-0 rounded-full bg-brand-soft px-2 py-0.5 font-mono text-brand">{release.version}</span>
				<span class="min-w-0 text-left leading-snug"
					><span class="sm:hidden">Ultima versione</span><span class="hidden sm:inline"
						>Ultima versione · open source con licenza AGPL</span
					></span
				>
				<ArrowRight class="size-3 shrink-0 transition-transform group-hover:translate-x-0.5" />
			</a>

			<h1 class="mx-auto mt-7 max-w-4xl text-4xl leading-[1.08] font-bold tracking-tight text-balance sm:text-5xl md:text-6xl">
				Il gestionale <span class="gradient-text">open source</span> per la tua associazione sportiva
			</h1>
			<p class="mx-auto mt-6 max-w-2xl text-lg leading-relaxed text-muted-foreground sm:text-xl">
				Soci, iscrizioni online, corsi, presenze, pagamenti e contabilità in un solo posto. Assozeta si installa sul tuo
				server, è gratuito e non ha costi per socio.
			</p>

			<div class="mt-9 flex flex-wrap items-center justify-center gap-3">
				<Button href="/self-hosting/installazione" size="lg" class="h-11 bg-primary px-6 text-base text-primary-foreground hover:bg-primary-hover">
					Installa Assozeta
					<ArrowRight />
				</Button>
				<Button
					href={LINKS.github}
					target="_blank"
					rel="noopener"
					variant="outline"
					size="lg"
					class="h-11 px-6 text-base"
				>
					<Github />
					Codice su GitHub
					{#if starLabel}
						<span
							class="ml-1 inline-flex items-center gap-1 border-l border-border pl-2.5 font-mono text-sm text-muted-foreground"
						>
							<Star class="size-3.5" aria-hidden="true" />{starLabel}<span class="sr-only"> stelle su GitHub</span>
						</span>
					{/if}
				</Button>
			</div>

			<ul class="mt-7 flex flex-wrap items-center justify-center gap-x-5 gap-y-2 font-mono text-xs text-muted-foreground lg:gap-x-4">
				{#each facts as fact, i}
					<li class="flex items-center gap-4">
						{#if i > 0}<span class="hidden text-muted-foreground/40 lg:inline" aria-hidden="true">/</span>{/if}
						{fact}
					</li>
				{/each}
			</ul>
		</div>

		<div class="relative isolate mx-auto mt-16 max-w-5xl">
			<div
				class="absolute inset-x-6 top-8 -bottom-6 -z-10 rounded-[2rem] bg-primary/15 blur-3xl dark:bg-primary/20"
				aria-hidden="true"
			></div>
			<HeroDashboard />
		</div>
		<p class="mt-8 text-center text-sm text-muted-foreground sm:mt-20">Illustrazione con dati di esempio.</p>
	</div>
</section>
