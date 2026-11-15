<script lang="ts">
	import ChevronDown from '@lucide/svelte/icons/chevron-down';
	import CircleCheck from '@lucide/svelte/icons/circle-check';
	import Paperclip from '@lucide/svelte/icons/paperclip';
	import MockFrame from './MockFrame.svelte';

	const fields = [
		{ label: 'Nome e cognome', value: 'Elena Russo', mono: false },
		{ label: 'Codice fiscale', value: 'RSSLNE92C45D548X', mono: true }
	];

	const family = [
		{ name: 'Matteo Russo', detail: '8 anni · Danza moderna' },
		{ name: 'Sofia Russo', detail: '11 anni · Danza moderna' }
	];
</script>

<MockFrame
	label="Illustrazione del modulo pubblico di iscrizione di Assozeta: campi per nome, codice fiscale e corso, familiari iscritti insieme, certificato medico allegato, firma e invio della richiesta."
	path="riviera.assozeta.example / iscrizioni / stagione-2026-27"
>
	<div class="p-3 text-left sm:p-4">
		<div class="mx-auto min-w-0 max-w-md rounded-lg border border-border bg-subtle p-3">
			<div class="flex items-start justify-between gap-2">
				<div class="min-w-0">
					<p class="text-[0.8rem] leading-tight font-semibold text-foreground">Iscrizione stagione 2026/27</p>
					<p class="mt-0.5 truncate text-[0.6rem] text-muted-foreground">ASD Polisportiva Riviera</p>
				</div>
				<span class="shrink-0 rounded bg-brand-soft px-1 py-0.5 text-[0.55rem] leading-none text-brand">Modulo pubblico</span>
			</div>

			<div class="mt-3 space-y-2">
				{#each fields as field}
					<div class="min-w-0">
						<p class="text-[0.6rem] text-muted-foreground">{field.label}</p>
						<div class="mt-1 truncate rounded-md border border-border bg-surface px-2 py-1.5 text-[0.7rem] text-foreground {field.mono
							? 'font-mono text-[0.65rem]'
							: ''}">
							{field.value}
						</div>
					</div>
				{/each}

				<div class="min-w-0">
					<p class="text-[0.6rem] text-muted-foreground">Corso</p>
					<div class="mt-1 flex items-center justify-between gap-2 rounded-md border border-border bg-surface px-2 py-1.5">
						<span class="truncate text-[0.7rem] text-foreground">Danza moderna · lun e gio 17:30</span>
						<ChevronDown class="size-3 shrink-0 text-muted-foreground" />
					</div>
				</div>
			</div>

			<div class="mt-3 rounded-md border border-border bg-surface p-2">
				<p class="text-[0.6rem] font-medium tracking-wide text-muted-foreground uppercase">Familiari</p>
				<ul class="mt-1.5 space-y-1.5">
					{#each family as child}
						<li class="flex items-center gap-1.5">
							<span class="grid size-4 shrink-0 place-items-center rounded-full bg-brand-soft font-mono text-[0.5rem] text-brand"
								>{child.name.charAt(0)}</span
							>
							<span class="min-w-0 flex-1 truncate text-[0.65rem] text-foreground">{child.name}</span>
							<span class="hidden shrink-0 text-[0.6rem] text-muted-foreground sm:block">{child.detail}</span>
						</li>
					{/each}
				</ul>
				<p class="mt-1.5 border-t border-dashed border-border pt-1.5 text-[0.6rem] text-brand">+ Aggiungi familiare</p>
			</div>

			<div class="mt-3 flex items-center gap-1.5 rounded-md border border-dashed border-border px-2 py-1.5">
				<Paperclip class="size-3 shrink-0 text-muted-foreground" />
				<span class="min-w-0 flex-1 truncate text-[0.65rem] text-foreground">Certificato medico.pdf</span>
				<span class="flex shrink-0 items-center gap-0.5 text-[0.6rem] text-success-text">
					<CircleCheck class="size-3" /> caricato
				</span>
			</div>

			<div class="mt-3">
				<div class="rounded-md border border-dashed border-border bg-surface px-2 py-1">
					<svg viewBox="0 0 160 36" class="h-9 w-full text-foreground" fill="none" aria-hidden="true">
						<path
							d="M5 27c6-8 9-15 12-15s2 12 7 12 7-14 11-14 2 13 8 13 9-11 13-11 1 9 7 9 10-8 14-8 3 7 8 7 8-3 12-5"
							stroke="currentColor"
							stroke-width="1.4"
							stroke-linecap="round"
						/>
					</svg>
				</div>
				<p class="mt-1 text-[0.6rem] text-muted-foreground">Firma</p>
			</div>

			<div class="mt-3 rounded-md bg-primary px-2 py-1.5 text-center text-[0.7rem] font-medium text-primary-foreground">
				Invia iscrizione
			</div>
			<p class="mt-2 text-center text-[0.55rem] leading-snug text-muted-foreground">
				I campi e gli allegati sono configurati dall'associazione.
			</p>
		</div>
	</div>
</MockFrame>
