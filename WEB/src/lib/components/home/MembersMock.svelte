<script lang="ts">
	import Search from '@lucide/svelte/icons/search';
	import MockFrame from './MockFrame.svelte';

	type Certificate = 'Valido' | 'In scadenza' | 'Scaduto';

	const members: {
		name: string;
		card: string;
		tags: string[];
		certificate: Certificate;
		note?: string;
	}[] = [
		{ name: 'Giulia Ferri', card: 'T-0231', tags: ['Agonisti'], certificate: 'Valido' },
		{
			name: 'Marco Bellini',
			card: 'T-0244',
			tags: ['U14'],
			certificate: 'In scadenza',
			note: 'Minore · tutore: Paola Bellini'
		},
		{ name: 'Sara Conti', card: 'T-0252', tags: ['U14', 'Danza'], certificate: 'Scaduto' },
		{ name: 'Andrea Greco', card: 'T-0198', tags: ['Consiglio direttivo'], certificate: 'Valido' },
		{ name: 'Elena Russo', card: 'T-0207', tags: ['Adulti', 'Ginnastica'], certificate: 'Valido' }
	];

	const certificateStyle: Record<Certificate, { dot: string; text: string }> = {
		Valido: { dot: 'bg-success', text: 'text-success-text' },
		'In scadenza': { dot: 'bg-warning', text: 'text-warning-text' },
		Scaduto: { dot: 'bg-danger', text: 'text-danger-text' }
	};
</script>

<MockFrame
	label="Illustrazione dell'archivio soci di Assozeta con ricerca per nome o codice fiscale, importazione da CSV e XLSX, tessere, tag, stato dei certificati medici e un minore collegato al suo tutore."
	path="riviera.assozeta.example / soci"
>
	<div class="text-left">
		<div class="flex flex-col gap-2 p-3 sm:flex-row sm:items-center">
			<div class="flex min-w-0 flex-1 items-center gap-1.5 rounded-md border border-border bg-subtle px-2 py-1.5">
				<Search class="size-3 shrink-0 text-muted-foreground" />
				<span class="truncate text-[0.7rem] text-muted-foreground">Cerca per nome o codice fiscale</span>
			</div>
			<div class="flex shrink-0 items-center gap-1.5">
				<span class="rounded-md border border-border px-2 py-1 text-[0.65rem] whitespace-nowrap text-muted-foreground"
					>Importa CSV/XLSX</span
				>
				<span class="rounded-md bg-primary px-2 py-1 text-[0.65rem] font-medium whitespace-nowrap text-primary-foreground"
					>Nuovo socio</span
				>
			</div>
		</div>

		<div class="border-t border-border">
			<div class="hidden items-center gap-2 border-b border-border bg-subtle px-3 py-1.5 text-[0.6rem] text-muted-foreground sm:flex">
				<span class="size-5 shrink-0"></span>
				<span class="min-w-0 flex-1">Socio</span>
				<span class="w-16 shrink-0">Tessera</span>
				<span class="w-36 shrink-0">Tag</span>
				<span class="w-24 shrink-0">Certificato</span>
			</div>

			<div class="divide-y divide-border">
				{#each members as member}
					<div class="flex items-center gap-2 px-3 py-2">
						<span
							class="grid size-5 shrink-0 place-items-center rounded-full bg-brand-soft font-mono text-[0.55rem] leading-none text-brand"
							>{member.name.charAt(0)}</span
						>
						<div class="min-w-0 flex-1">
							<p class="truncate text-[0.7rem] leading-tight font-medium text-foreground">{member.name}</p>
							{#if member.note}
								<p class="truncate text-[0.6rem] leading-tight text-muted-foreground">{member.note}</p>
							{/if}
							<div class="mt-1 flex flex-wrap gap-1 sm:hidden">
								{#each member.tags as tag}
									<span class="rounded bg-muted px-1 py-0.5 text-[0.55rem] leading-none text-muted-foreground">{tag}</span>
								{/each}
							</div>
						</div>
						<span class="hidden w-16 shrink-0 font-mono text-[0.65rem] text-muted-foreground sm:block">{member.card}</span>
						<div class="hidden w-36 shrink-0 flex-wrap gap-1 sm:flex">
							{#each member.tags as tag}
								<span class="max-w-full truncate rounded bg-muted px-1 py-0.5 text-[0.55rem] leading-none text-muted-foreground"
									>{tag}</span
								>
							{/each}
						</div>
						<span class="flex w-24 shrink-0 items-center gap-1 text-[0.6rem] {certificateStyle[member.certificate].text}">
							<span class="size-1.5 shrink-0 rounded-full {certificateStyle[member.certificate].dot}"></span>
							<span class="truncate">{member.certificate}</span>
						</span>
					</div>
				{/each}
			</div>
		</div>

		<div class="flex items-center justify-between gap-2 border-t border-border px-3 py-1.5 font-mono text-[0.6rem] text-muted-foreground">
			<span>248 soci</span>
			<span class="truncate">Libro soci · stagione 2026/27</span>
		</div>
	</div>
</MockFrame>
