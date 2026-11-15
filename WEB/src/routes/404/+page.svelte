<script lang="ts">
	import ArrowRight from '@lucide/svelte/icons/arrow-right';
	import Footer from '$lib/components/Footer.svelte';
	import Nav from '$lib/components/Nav.svelte';
	import Seo from '$lib/components/Seo.svelte';
	import { Button } from '$lib/components/ui/button';
	import { url } from '$lib/config/site';

	// Served by the web server with HTTP 404 for unknown paths (deploy/Caddyfile).
	// Not indexed, not linked and not in the sitemap.
	const title = 'Pagina non trovata · Assozeta';
	const description =
		'La pagina che cerchi non esiste o è stata spostata. Torna alla home oppure continua dalle funzionalità e dalla guida al self-hosting.';
	const canonical = url('/404');
</script>

<Seo meta={{ title, description, canonical, noindex: true }} />

<Nav />

<main id="main" tabindex="-1" class="outline-none mx-auto max-w-2xl px-6 pt-40 pb-28 text-center">
	<p class="font-mono text-xs tracking-widest text-brand uppercase">Errore 404</p>
	<h1 class="mt-3 text-4xl font-bold tracking-tight sm:text-5xl">Pagina non trovata</h1>
	<p class="mt-4 text-lg leading-relaxed text-muted-foreground">
		Il link può essere vecchio o scritto male. Da qui puoi ripartire.
	</p>
	<div class="mt-8 flex flex-wrap justify-center gap-3">
		<Button href="/" class="bg-primary text-primary-foreground hover:bg-primary-hover">
			Torna alla home
			<ArrowRight />
		</Button>
		<Button href="/funzionalita" variant="outline">Funzionalità</Button>
		<Button href="/self-hosting" variant="outline">Self-hosting</Button>
	</div>
</main>

<Footer />
