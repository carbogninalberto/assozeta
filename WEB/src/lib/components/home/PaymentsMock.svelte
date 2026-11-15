<script lang="ts">
	import Download from '@lucide/svelte/icons/download';
	import Mail from '@lucide/svelte/icons/mail';
	import MockFrame from './MockFrame.svelte';

	type Status = 'Pagato' | 'Scade il 15/01' | 'Pagato online' | 'Scaduto';

	const installments: { title: string; amount: string; status: Status; stripe?: boolean }[] = [
		{ title: 'Quota associativa 2026/27', amount: '30,00', status: 'Pagato' },
		{ title: 'Danza moderna · rata 2 di 3', amount: '120,00', status: 'Scade il 15/01' },
		{ title: 'Nuoto agonisti · rata 1 di 2', amount: '210,00', status: 'Pagato online', stripe: true },
		{ title: 'Camp estivo · acconto', amount: '80,00', status: 'Scaduto' }
	];

	const statusStyle: Record<Status, { dot: string; text: string }> = {
		Pagato: { dot: 'bg-success', text: 'text-success-text' },
		'Scade il 15/01': { dot: 'bg-warning', text: 'text-warning-text' },
		'Pagato online': { dot: 'bg-success', text: 'text-success-text' },
		Scaduto: { dot: 'bg-danger', text: 'text-danger-text' }
	};
</script>

<MockFrame
	label="Illustrazione dei pagamenti in Assozeta: quote e rate con importi, scadenze e stato, un pagamento online con Stripe e una ricevuta in PDF pronta da scaricare o inviare per email."
	path="riviera.assozeta.example / pagamenti"
>
	<div class="text-left">
		<div class="flex items-center justify-between gap-2 border-b border-border px-3 py-2">
			<p class="text-[0.8rem] leading-tight font-semibold text-foreground">Pagamenti</p>
			<span class="shrink-0 font-mono text-[0.6rem] text-muted-foreground tabular-nums">Stagione 2026/27</span>
		</div>

		<div class="grid gap-3 p-3 sm:grid-cols-[minmax(0,1fr)_11rem]">
			<div class="min-w-0 divide-y divide-border rounded-md border border-border bg-subtle">
				{#each installments as row}
					<div class="flex items-center gap-2 px-2.5 py-2">
						<div class="min-w-0 flex-1">
							<p class="truncate text-[0.7rem] leading-tight text-foreground">{row.title}</p>
							<p class="mt-0.5 flex items-center gap-1 text-[0.6rem] {statusStyle[row.status].text}">
								<span class="size-1.5 shrink-0 rounded-full {statusStyle[row.status].dot}"></span>
								<span class="truncate">{row.status}</span>
								{#if row.stripe}
									<span class="ml-1 shrink-0 rounded bg-info/15 px-1 py-0.5 font-mono text-[0.5rem] leading-none text-info"
										>Stripe</span
									>
								{/if}
							</p>
						</div>
						<span class="shrink-0 font-mono text-[0.7rem] text-foreground tabular-nums">€ {row.amount}</span>
					</div>
				{/each}
			</div>

			<div class="flex min-w-0 flex-col rounded-md border border-border bg-subtle p-2.5">
				<p class="text-[0.6rem] tracking-wide text-muted-foreground uppercase">Ricevuta</p>
				<p class="mt-1 truncate font-mono text-[0.65rem] text-foreground">n. 2026/148</p>
				<p class="mt-1 truncate text-[0.7rem] text-foreground">Giulia Ferri</p>
				<p class="truncate font-mono text-[0.7rem] text-foreground tabular-nums">€ 210,00</p>
				<p class="mt-1 truncate text-[0.6rem] text-muted-foreground">Nuoto agonisti · rata 1 di 2</p>
				<div class="mt-auto flex flex-wrap gap-1 pt-2">
					<span class="flex items-center gap-1 rounded border border-border px-1.5 py-0.5 text-[0.55rem] text-muted-foreground">
						<Download class="size-2.5" /> Scarica PDF
					</span>
					<span class="flex items-center gap-1 rounded border border-border px-1.5 py-0.5 text-[0.55rem] text-muted-foreground">
						<Mail class="size-2.5" /> Invia per email
					</span>
				</div>
			</div>
		</div>

		<div class="flex items-center justify-between gap-2 border-t border-border px-3 py-1.5 font-mono text-[0.6rem] text-muted-foreground">
			<span class="hidden truncate sm:block">3 rate da incassare</span>
			<span class="shrink-0 tabular-nums">Totale mese · € 3.420,00</span>
		</div>
	</div>
</MockFrame>
