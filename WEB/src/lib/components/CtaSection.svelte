<script lang="ts">
	import ArrowRight from '@lucide/svelte/icons/arrow-right';
	import Github from '$lib/components/GithubIcon.svelte';
	import { Button } from '$lib/components/ui/button';
	import { LINKS } from '$lib/config/site';

	let {
		heading = 'Porta la tua associazione su Assozeta',
		description = 'Codice aperto sotto licenza AGPL, nessun costo per socio. Leggi il sorgente, poi installalo sul tuo server con Docker Compose.'
	}: {
		heading?: string;
		description?: string;
	} = $props();
</script>

<section class="relative mt-20">
	<div
		class="animate-glow absolute -inset-3 -z-10 rounded-3xl bg-gradient-to-tr from-primary/20 via-primary/10 to-transparent blur-2xl"
	></div>
	<div
		class="ring-gradient noise-overlay overflow-hidden rounded-2xl border border-primary/30 bg-card p-5 text-center sm:p-10"
	>
		<h2 class="text-2xl font-bold tracking-tight sm:text-3xl">{heading}</h2>
		<p class="mt-3 text-muted-foreground">{description}</p>
		<div class="mt-6 flex flex-wrap items-center justify-center gap-3">
			<Button
				href="/self-hosting/installazione"
				size="lg"
				class="h-11 bg-primary px-6 text-base text-primary-foreground hover:bg-primary-hover"
			>
				Guida all'installazione
				<ArrowRight />
			</Button>
			<Button href={LINKS.github} target="_blank" rel="noopener" variant="outline" size="lg" class="h-11 px-6 text-base">
				<Github />
				Codice su GitHub
			</Button>
		</div>
	</div>
</section>
