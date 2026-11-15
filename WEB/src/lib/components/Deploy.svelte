<script lang="ts">
	import ArrowRight from '@lucide/svelte/icons/arrow-right';
	import Check from '@lucide/svelte/icons/check';
	import Copy from '@lucide/svelte/icons/copy';
	import { reveal } from '$lib/actions/reveal';
	import { Button } from '$lib/components/ui/button';
	import { FALLBACK_RELEASE, LINKS } from '$lib/config/site';

	/** Release tag shown in the commands, e.g. `v1.0.40` (the same value as the hero badge). */
	let { version = FALLBACK_RELEASE.version }: { version?: string } = $props();

	const tag = $derived(version.startsWith('v') ? version : `v${version}`);
	const bare = $derived(tag.slice(1));
	const bundle = $derived(`assozeta-selfhost-${tag}.tar.gz`);
	const downloadUrl = $derived(`${LINKS.releases}/download/${tag}/${bundle}`);

	const commands = $derived(`curl -LO ${downloadUrl}
tar -xzf ${bundle}
./selfhost/bin/assozeta install \\
  --domain assozeta.example.org \\
  --email admin@example.org \\
  --version ${bare}
# apri l'indirizzo stampato e incolla il token di setup`);

	let copied = $state(false);

	async function copy() {
		await navigator.clipboard.writeText(commands);
		copied = true;
		setTimeout(() => (copied = false), 2000);
	}
</script>

<section
	id="installa"
	class="relative isolate overflow-hidden border-y border-border bg-tint py-20 sm:py-28"
	aria-labelledby="installa-heading"
>
	<div
		class="dot-grid pointer-events-none absolute inset-0 -z-10 opacity-70 [mask-image:radial-gradient(ellipse_at_top,black,transparent_65%)]"
		aria-hidden="true"
	></div>
	<div
		class="absolute -bottom-40 left-1/2 -z-10 h-[380px] w-[680px] -translate-x-1/2 rounded-full bg-primary/10 blur-[150px]"
		aria-hidden="true"
	></div>

	<div class="mx-auto max-w-3xl px-6 text-center">
		<div use:reveal>
			<p class="font-mono text-xs tracking-widest text-brand uppercase">
				<span class="text-muted-foreground">06</span> · Installa
			</p>
			<h2 id="installa-heading" class="mt-3 text-3xl font-bold tracking-tight sm:text-4xl">
				Installalo sul tuo server con Docker
			</h2>
			<p class="mt-4 text-lg text-muted-foreground">
				Lo stack self-host comprende Caddy con HTTPS automatico, PostgreSQL, Redis, storage MinIO privato, i
				worker per le attività pianificate e il generatore di PDF. Un comando prepara la configurazione con
				segreti generati, scarica le immagini e avvia tutto; poi completi la procedura guidata dal browser.
			</p>
		</div>

		<div use:reveal={{ delay: 150 }} class="mt-10 text-left">
			<div class="ring-gradient overflow-hidden rounded-2xl border border-border bg-surface-bar shadow-mock">
				<div class="flex items-center justify-between gap-2 border-b border-border px-4 py-2.5">
					<div class="flex min-w-0 items-center gap-1.5">
						<span class="size-3 shrink-0 rounded-full bg-danger/80"></span>
						<span class="size-3 shrink-0 rounded-full bg-warning/80"></span>
						<span class="size-3 shrink-0 rounded-full bg-success/80"></span>
						<span class="ml-3 truncate font-mono text-[10px] text-muted-foreground">~/assozeta — bash</span>
					</div>
					<Button
						variant="ghost"
						size="sm"
						onclick={copy}
						class="flex shrink-0 items-center gap-1.5 rounded-md px-2 py-1 text-xs text-muted-foreground transition-colors hover:bg-muted hover:text-foreground"
					>
						{#if copied}
							<Check class="size-3.5 text-success-text" /> Copiato
						{:else}
							<Copy class="size-3.5" /> Copia
						{/if}
					</Button>
					<span class="sr-only" aria-live="polite">{copied ? 'Comandi copiati negli appunti' : ''}</span>
				</div>
				<pre class="overflow-x-auto p-4 font-mono text-xs leading-6 sm:p-5 sm:text-sm sm:leading-7"><code
						><span class="text-muted-foreground">$</span> <span class="text-brand">curl</span> -LO {downloadUrl}
<span class="text-muted-foreground">$</span> <span class="text-brand">tar</span> -xzf {bundle}
<span class="text-muted-foreground">$</span> <span class="text-brand">./selfhost/bin/assozeta</span> install \
  --domain assozeta.example.org \
  --email admin@example.org \
  --version {bare}
<span class="text-muted-foreground">#</span> apri l'indirizzo stampato e incolla il token di setup<span
							class="caret ml-1 inline-block h-4 w-2 translate-y-0.5 bg-brand"
							style="animation: caret-blink 1.2s step-end infinite"
						></span></code
					></pre>
			</div>
		</div>

		<div use:reveal={{ delay: 250 }} class="mt-10 flex flex-wrap items-center justify-center gap-3">
			<Button
				href="/self-hosting/installazione"
				size="lg"
				class="h-11 bg-primary px-6 text-base text-primary-foreground hover:bg-primary-hover"
			>
				Guida all’installazione
				<ArrowRight />
			</Button>
			<Button href="/self-hosting/requisiti" variant="outline" size="lg" class="h-11 bg-card px-6 text-base">
				Requisiti del server
			</Button>
		</div>
	</div>
</section>
