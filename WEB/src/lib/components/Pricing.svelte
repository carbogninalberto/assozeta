<script lang="ts">
	import ArrowRight from '@lucide/svelte/icons/arrow-right';
	import Check from '@lucide/svelte/icons/check';
	import { reveal } from '$lib/actions/reveal';

	const costs = [
		{ label: 'Licenza Assozeta', value: '0 €', free: true },
		{ label: 'Costo per socio o per utente', value: '0 €', free: true },
		{ label: 'Funzioni a pagamento', value: 'Nessuna', free: false },
		{ label: 'Server Linux con Docker', value: 'A carico tuo', free: false, note: 'Almeno 4 GB di RAM, 8 GB consigliati per i PDF' },
		{ label: 'Email SMTP', value: 'Dal tuo fornitore', free: false },
		{ label: 'Pagamenti online', value: 'Commissioni Stripe', free: false }
	];

	const included = [
		'Soci, corsi, presenze, pagamenti e contabilità',
		'Moduli d’iscrizione online con firme e allegati',
		'Aggiornamenti dalla pagina Istanza',
		'Backup e ripristino completi',
		'Export di tutti i dati dell’associazione'
	];
</script>

<section
	id="prezzi"
	class="relative isolate overflow-hidden border-y border-border bg-tint py-20 sm:py-28"
	aria-labelledby="prezzi-heading"
>
	<div
		class="absolute -top-32 right-0 -z-10 h-[360px] w-[560px] rounded-full bg-primary/10 blur-[140px]"
		aria-hidden="true"
	></div>
	<div class="mx-auto max-w-6xl px-6">
		<div class="max-w-2xl" use:reveal>
			<p class="font-mono text-xs tracking-widest text-brand uppercase">Prezzi</p>
			<h2 id="prezzi-heading" class="mt-3 text-3xl font-bold tracking-tight text-balance sm:text-4xl">
				0 € di licenza, con qualsiasi numero di soci
			</h2>
			<p class="mt-4 text-lg text-muted-foreground">
				Assozeta non ha canoni, costi per socio o versioni a pagamento. Paghi solo il server su cui lo installi e
				gli eventuali servizi esterni che scegli di collegare.
			</p>
		</div>

		<div class="mt-12 grid gap-8 lg:grid-cols-[1.4fr_1fr]">
			<div class="rounded-2xl border border-border bg-card p-5 shadow-xs sm:p-7" use:reveal>
				<h3 class="text-sm font-medium">Cosa paghi</h3>
				<ul class="mt-6 divide-y divide-dashed divide-border border-t border-dashed border-border">
					{#each costs as row}
						<li class="py-3">
							<div class="grid gap-1 sm:grid-cols-[1fr_auto] sm:items-baseline sm:gap-4">
								<span class="text-sm">{row.label}</span>
								<span
									class="font-mono text-sm sm:text-right"
									class:text-brand={row.free}
									class:font-semibold={row.free}
									class:text-muted-foreground={!row.free}
								>
									{row.value}
								</span>
							</div>
							{#if row.note}
								<p class="mt-1 text-xs leading-relaxed text-muted-foreground">{row.note}</p>
							{/if}
						</li>
					{/each}
				</ul>
				<p class="mt-5 text-xs leading-relaxed text-muted-foreground">
					Assozeta è distribuito sotto licenza
					<a href="/licenza" class="text-foreground underline underline-offset-4 hover:text-brand">GNU AGPL v3.0</a>.
					Il costo reale è l’infrastruttura e il tempo per gestirla.
				</p>
			</div>

			<div
				class="flex flex-col rounded-2xl border border-primary/30 bg-linear-to-br from-primary/15 via-card to-card p-5 shadow-mock sm:p-7"
				use:reveal={{ delay: 100 }}
			>
				<h3 class="text-lg font-semibold">Tutto incluso, niente a parte</h3>
				<p class="mt-1 text-sm text-muted-foreground">Una sola edizione, il codice è tutto pubblico.</p>
				<ul class="mt-5 mb-6 space-y-3">
					{#each included as item}
						<li class="flex gap-3">
							<span class="mt-0.5 flex size-5 shrink-0 items-center justify-center rounded-full bg-brand-soft text-brand">
								<Check class="size-3" aria-hidden="true" />
							</span>
							<span class="text-sm leading-relaxed">{item}</span>
						</li>
					{/each}
				</ul>
				<div class="mt-auto border-t border-primary/20 pt-5">
					<p class="text-sm leading-relaxed text-muted-foreground">
						Usavi Bakney Sport? Puoi portare i dati della tua associazione in Assozeta.
					</p>
					<a
						href="/self-hosting/backup-e-ripristino#importare-i-dati-da-bakney"
						class="mt-3 inline-flex items-center gap-1 text-sm font-medium text-brand hover:text-brand-strong"
					>
						Importare i dati da Bakney
						<ArrowRight class="size-4" aria-hidden="true" />
					</a>
				</div>
			</div>
		</div>
	</div>
</section>
