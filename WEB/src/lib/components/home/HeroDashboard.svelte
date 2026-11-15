<script lang="ts">
	import MockFrame from './MockFrame.svelte';

	const navItems = [
		'Bacheca',
		'Soci',
		'Corsi',
		'Calendario',
		'Presenze',
		'Pagamenti',
		'Contabilità',
		'Comunicazioni'
	];

	const kpis = [
		{ label: 'Soci attivi', value: '248', dot: '' },
		{ label: 'Iscrizioni da approvare', value: '6', dot: '' },
		{ label: 'Certificati in scadenza', value: '4', dot: 'bg-warning' },
		{ label: 'Incassi del mese', value: '€ 3.420', dot: '' }
	];

	const pending = [
		{ name: 'Giulia Ferri', course: 'Nuoto agonisti', badge: 'Da approvare' },
		{ name: 'Marco Bellini', course: 'Pallavolo U14', badge: 'Da approvare' },
		{ name: 'Sara e Luca Conti', course: 'Danza moderna', badge: 'Familiare' },
		{ name: 'Andrea Greco', course: 'Ginnastica adulti', badge: 'Da approvare' }
	];

	const months = [
		{ label: 'Giu', income: 52, expense: 36 },
		{ label: 'Lug', income: 40, expense: 28 },
		{ label: 'Ago', income: 26, expense: 18 },
		{ label: 'Set', income: 70, expense: 45 },
		{ label: 'Ott', income: 88, expense: 56 },
		{ label: 'Nov', income: 63, expense: 40 }
	];
</script>

<div class="relative">
	<MockFrame
		label="Illustrazione della bacheca di Assozeta: voci di menu dell'associazione, soci attivi, iscrizioni da approvare, certificati medici in scadenza, incassi del mese, un grafico di entrate e uscite e una notifica di nuova iscrizione online."
		path="riviera.assozeta.example / bacheca"
	>
		<div class="flex text-left">
			<!-- Sidebar -->
			<div class="hidden w-44 shrink-0 border-r border-border p-3 text-[0.7rem] text-muted-foreground md:block">
				<p class="truncate font-medium text-foreground">ASD Polisportiva Riviera</p>
				<ul class="mt-3 space-y-0.5">
					{#each navItems as item, i}
						<li
							class="truncate rounded px-2 py-1 {i === 0
								? 'bg-brand-soft font-medium text-brand'
								: 'text-muted-foreground'}"
						>
							{item}
						</li>
					{/each}
				</ul>
			</div>

			<!-- Main -->
			<div class="min-w-0 flex-1 p-3">
				<div class="grid grid-cols-2 gap-2 sm:grid-cols-4">
					{#each kpis as kpi}
						<div class="min-w-0 rounded-md border border-border bg-subtle p-2.5">
							<p class="flex items-start gap-1 text-[0.6rem] leading-snug text-muted-foreground">
								{#if kpi.dot}<span class="mt-1 size-1.5 shrink-0 rounded-full {kpi.dot}"></span>{/if}
								<span class="line-clamp-2">{kpi.label}</span>
							</p>
							<p class="mt-1 font-mono text-sm leading-none font-semibold tabular-nums">{kpi.value}</p>
						</div>
					{/each}
				</div>

				<div class="mt-2 grid gap-2 sm:grid-cols-2">
					<!-- Iscrizioni da approvare -->
					<div class="min-w-0 rounded-md border border-border bg-subtle">
						<p class="border-b border-border px-2.5 py-1.5 text-[0.6rem] font-medium tracking-wide text-muted-foreground uppercase">
							Iscrizioni da approvare
						</p>
						<div class="divide-y divide-border">
							{#each pending as row}
								<div class="flex items-center justify-between gap-2 px-2.5 py-1.5">
									<div class="min-w-0">
										<p class="truncate text-[0.7rem] leading-tight font-medium">{row.name}</p>
										<p class="truncate text-[0.6rem] leading-tight text-muted-foreground">{row.course}</p>
									</div>
									<span class="shrink-0 rounded bg-brand-soft px-1 py-0.5 text-[0.55rem] leading-none text-brand"
										>{row.badge}</span
									>
								</div>
							{/each}
						</div>
					</div>

					<!-- Entrate e uscite -->
					<div class="hidden min-w-0 rounded-md border border-border bg-subtle sm:block">
						<p class="border-b border-border px-2.5 py-1.5 text-[0.6rem] font-medium tracking-wide text-muted-foreground uppercase">
							Entrate e uscite
						</p>
						<div class="p-2.5">
							<div class="flex items-end gap-1">
								{#each months as month}
									<div class="flex min-w-0 flex-1 flex-col items-center gap-1">
										<div class="flex h-16 w-full items-end justify-center gap-1">
											<span class="w-2 rounded-sm bg-primary" style="height: {month.income}%"></span>
											<span class="w-2 rounded-sm bg-muted-foreground/30" style="height: {month.expense}%"></span>
										</div>
										<span class="font-mono text-[0.55rem] leading-none text-muted-foreground">{month.label}</span>
									</div>
								{/each}
							</div>
							<div class="mt-2 flex items-center gap-3 text-[0.6rem] text-muted-foreground">
								<span class="flex items-center gap-1">
									<span class="size-1.5 rounded-sm bg-primary"></span> Entrate
								</span>
								<span class="flex items-center gap-1">
									<span class="size-1.5 rounded-sm bg-muted-foreground/30"></span> Uscite
								</span>
							</div>
						</div>
					</div>
				</div>
			</div>
		</div>
	</MockFrame>

	<!-- Floating notification, like Kuayle's command palette -->
	<div
		class="relative z-10 mx-auto -mt-4 w-[88%] rounded-lg border border-border bg-surface p-2.5 text-left shadow-mock sm:absolute sm:right-8 sm:-bottom-14 sm:mx-0 sm:mt-0 sm:w-72"
		aria-hidden="true"
	>
		<p class="flex items-center justify-between gap-2 text-[0.6rem] font-medium tracking-wide text-muted-foreground uppercase">
			Notifica
			<span class="size-1.5 shrink-0 rounded-full bg-success animate-pulse-dot"></span>
		</p>
		<p class="mt-1.5 text-[0.7rem] leading-snug">
			<span class="font-medium text-foreground">Nuova iscrizione online</span>
			<span class="text-muted-foreground">· Elena Russo, Danza classica</span>
		</p>
		<div class="mt-2 border-t border-border pt-2">
			<p class="text-[0.7rem] leading-snug">
				<span class="font-medium text-foreground">Certificato medico in scadenza</span>
				<span class="text-muted-foreground">· Marco Bellini, 12/12</span>
			</p>
		</div>
	</div>
</div>
