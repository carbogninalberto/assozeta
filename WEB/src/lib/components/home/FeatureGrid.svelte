<script lang="ts">
	import CalendarCheck from '@lucide/svelte/icons/calendar-check';
	import ClipboardList from '@lucide/svelte/icons/clipboard-list';
	import Mail from '@lucide/svelte/icons/mail';
	import ShieldCheck from '@lucide/svelte/icons/shield-check';
	import Users from '@lucide/svelte/icons/users';
	import Wallet from '@lucide/svelte/icons/wallet';
	import { reveal } from '$lib/actions/reveal';
	import { ASSOZETA_FEATURE_GROUPS, INTEGRATIONS } from '$lib/data/assozeta-facts';

	const icons = [Users, ClipboardList, CalendarCheck, Wallet, Mail, ShieldCheck];
	const total = ASSOZETA_FEATURE_GROUPS.reduce((sum, group) => sum + group.items.length, 0);
</script>

<section id="funzionalita" class="py-20 sm:py-28" aria-labelledby="funzionalita-heading">
	<div class="mx-auto max-w-6xl px-6">
		<div class="max-w-2xl" use:reveal>
			<p class="font-mono text-xs tracking-widest text-brand uppercase">Tutto incluso · {total}</p>
			<h2 id="funzionalita-heading" class="mt-3 text-3xl font-bold tracking-tight sm:text-4xl">
				Ogni funzione, in ogni installazione
			</h2>
			<p class="mt-4 text-lg text-muted-foreground">
				Le funzioni dipendono dalla configurazione dell’istanza e dai servizi esterni che attivi.
			</p>
		</div>

		<div class="mt-10 grid gap-px overflow-hidden rounded-2xl border border-border bg-border sm:grid-cols-2 lg:grid-cols-3">
			{#each ASSOZETA_FEATURE_GROUPS as group, i}
				{@const Icon = icons[i]}
				<div class="bg-card p-6" use:reveal={{ delay: i * 40 }}>
					<div class="flex items-center justify-between gap-3">
						<span class="flex size-9 items-center justify-center rounded-lg bg-brand-soft text-brand"
							><Icon class="size-4.5" aria-hidden="true" /></span
						>
						<span class="font-mono text-xs text-muted-foreground">{String(group.items.length).padStart(2, '0')}</span>
					</div>
					<h3 class="mt-4 font-semibold">{group.group}</h3>
					<p class="mt-1 text-xs text-muted-foreground">{group.blurb}</p>
					<ul class="mt-4 space-y-1.5 text-sm">
						{#each group.items as item}
							<li>
								{#if item.href}
									<a href={item.href} class="text-muted-foreground transition-colors hover:text-foreground">
										{item.label}
									</a>
								{:else}
									<span class="text-muted-foreground">{item.label}</span>
								{/if}
							</li>
						{/each}
					</ul>
				</div>
			{/each}
		</div>

		<div class="mt-8 rounded-2xl border border-primary/20 bg-brand-soft p-5 sm:p-6" use:reveal>
			<h3 class="text-sm font-medium">Integrazioni facoltative</h3>
			<ul class="mt-3 flex flex-wrap gap-2">
				{#each INTEGRATIONS as integration}
					<li class="rounded-full border border-border bg-card px-3 py-1.5 text-sm" title={integration.note}>
						{integration.name}
					</li>
				{/each}
			</ul>
			<p class="mt-3 text-xs leading-relaxed text-muted-foreground">
				Ogni integrazione richiede le tue credenziali e resta disattivata finché il campo è vuoto.
			</p>
		</div>
	</div>
</section>
