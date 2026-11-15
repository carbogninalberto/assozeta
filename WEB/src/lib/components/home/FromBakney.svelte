<script lang="ts">
	import ArrowRight from '@lucide/svelte/icons/arrow-right';
	import FileArchive from '@lucide/svelte/icons/file-archive';
	import Github from '$lib/components/GithubIcon.svelte';
	import { reveal } from '$lib/actions/reveal';
	import { Button } from '$lib/components/ui/button';
	import { LINKS } from '$lib/config/site';
</script>

<section id="da-bakney" class="py-12 sm:py-16" aria-labelledby="da-bakney-heading">
	<div class="mx-auto max-w-6xl px-6">
		<div class="relative isolate overflow-hidden rounded-3xl border border-primary/20 bg-brand-soft p-6 sm:p-10" use:reveal>
			<div class="absolute -top-24 -right-20 -z-10 size-72 rounded-full bg-primary/15 blur-3xl" aria-hidden="true"></div>
			<div class="absolute -bottom-28 -left-16 -z-10 size-64 rounded-full bg-primary/10 blur-3xl" aria-hidden="true"></div>
			<div class="grid items-center gap-8 lg:grid-cols-[1.5fr_1fr]">
				<div>
					<p
						class="inline-flex items-center gap-2 rounded-full border border-primary/20 bg-card px-3 py-1 text-xs font-medium text-brand"
					>
						<FileArchive class="size-3.5" aria-hidden="true" /> Vieni da Bakney Sport?
					</p>
					<h2 id="da-bakney-heading" class="mt-4 text-2xl font-bold tracking-tight text-balance sm:text-3xl">
						Porta i dati della tua associazione in Assozeta
					</h2>
					<p class="mt-3 max-w-2xl leading-relaxed text-muted-foreground">
						Assozeta nasce dall’esperienza di Bakney Sport, il cui servizio in cloud è stato annunciato in chiusura dal 30
						settembre 2026. Se hai un export nel formato <code class="font-mono text-sm">bakney_sport_export_v1</code>, lo
						importi dalla pagina Istanza della tua installazione (Profilo → Istanza → Backup).
					</p>
					<p class="mt-2 text-sm text-muted-foreground">
						Verifica la compatibilità prima di migrare: l’importazione è diversa dal ripristino di un backup.
					</p>
				</div>
				<div class="flex flex-col gap-3 sm:flex-row lg:flex-col lg:items-stretch">
					<Button
						href="/self-hosting/backup-e-ripristino#importare-i-dati-da-bakney"
						size="lg"
						class="h-11 bg-primary px-6 text-base text-primary-foreground hover:bg-primary-hover"
					>
						Come importare i dati <ArrowRight />
					</Button>
					<Button
						href={LINKS.github}
						target="_blank"
						rel="noopener"
						variant="outline"
						size="lg"
						class="h-11 bg-card px-6 text-base"
					>
						<Github /> Codice su GitHub
					</Button>
				</div>
			</div>
		</div>
	</div>
</section>
