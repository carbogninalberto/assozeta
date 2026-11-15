<script lang="ts">
	import { reveal } from '$lib/actions/reveal';
	import * as Accordion from '$lib/components/ui/accordion';
	import { LINKS } from '$lib/config/site';
	import { HOME_FAQ } from '$lib/data/faq';
</script>

<section id="domande" class="py-20 sm:py-28" aria-labelledby="domande-heading">
	<div class="mx-auto max-w-6xl px-6">
		<div class="grid gap-10 lg:grid-cols-[1fr_1.6fr] lg:gap-16">
			<div use:reveal>
				<p class="font-mono text-xs tracking-widest text-brand uppercase">FAQ</p>
				<h2 id="domande-heading" class="mt-3 text-3xl font-bold tracking-tight sm:text-4xl">Domande frequenti</h2>
				<p class="mt-4 text-muted-foreground leading-relaxed">
					Costi, installazione, dati e aggiornamenti: le risposte essenziali prima di iniziare.
				</p>
				<p class="mt-6 text-sm text-muted-foreground">
					Altre domande?
					<a
						href={LINKS.telegram}
						target="_blank"
						rel="noopener"
						class="font-medium text-brand underline-offset-4 hover:text-brand-strong hover:underline"
					>
						Scrivici nella community su Telegram
					</a>
				</p>
			</div>

			<div use:reveal={{ delay: 100 }}>
				<Accordion.Root type="multiple" class="rounded-2xl border border-border bg-card shadow-xs">
					{#each HOME_FAQ as item, i}
						<Accordion.Item value={`faq-${i}`} class="border-border px-4">
							<Accordion.Trigger class="cursor-pointer gap-3 py-3 text-left font-medium">{item.question}</Accordion.Trigger>
							<Accordion.Content class="pb-3 text-muted-foreground leading-relaxed">{item.answer}</Accordion.Content>
						</Accordion.Item>
					{/each}
				</Accordion.Root>
			</div>
		</div>
	</div>
</section>
