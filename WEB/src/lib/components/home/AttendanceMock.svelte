<script lang="ts">
	import QrCode from '@lucide/svelte/icons/qr-code';
	import MockFrame from './MockFrame.svelte';

	const athletes = [
		{ name: 'Giulia Ferri', present: true, carnet: '8/10 ingressi' },
		{ name: 'Marco Bellini', present: true, carnet: '3/10 ingressi' },
		{ name: 'Sara Conti', present: false, carnet: 'Abbonamento' },
		{ name: 'Andrea Greco', present: true, carnet: '5/10 ingressi' },
		{ name: 'Elena Russo', present: true, carnet: 'Abbonamento' }
	];

	const week = [
		{ day: 'Lun', lesson: false },
		{ day: 'Mar', lesson: true },
		{ day: 'Mer', lesson: false },
		{ day: 'Gio', lesson: true },
		{ day: 'Ven', lesson: true }
	];
</script>

<MockFrame
	label="Illustrazione della rilevazione presenze di Assozeta per il corso Nuoto agonisti: elenco degli atleti con presenze e assenze, carnet di ingressi, check-in tramite QR e la settimana delle lezioni."
	path="riviera.assozeta.example / corsi / nuoto-agonisti"
>
	<div class="text-left">
		<div class="flex flex-wrap items-start justify-between gap-2 border-b border-border p-3">
			<div class="min-w-0">
				<p class="truncate text-[0.8rem] leading-tight font-semibold text-foreground">Nuoto agonisti</p>
				<p class="mt-0.5 truncate text-[0.65rem] text-muted-foreground">Martedì 18:30 · Piscina comunale</p>
				<p class="mt-1 truncate text-[0.6rem] text-muted-foreground">Istruttrice: Laura Neri</p>
			</div>
			<div class="hidden shrink-0 items-end gap-1 sm:flex">
				{#each week as day}
					<div class="flex w-6 flex-col items-center gap-1">
						<span class="size-1.5 rounded-full {day.lesson ? 'bg-primary' : 'bg-muted-foreground/25'}"></span>
						<span class="font-mono text-[0.55rem] leading-none text-muted-foreground">{day.day}</span>
					</div>
				{/each}
			</div>
		</div>

		<div class="divide-y divide-border">
			{#each athletes as athlete}
				<div class="flex items-center gap-2 px-3 py-1.5">
					<span
						class="grid size-4 shrink-0 place-items-center rounded-full bg-brand-soft font-mono text-[0.5rem] text-brand"
						>{athlete.name.charAt(0)}</span
					>
					<span class="min-w-0 flex-1 truncate text-[0.7rem] text-foreground">{athlete.name}</span>
					<span class="hidden w-24 shrink-0 truncate text-right font-mono text-[0.6rem] text-muted-foreground sm:block"
						>{athlete.carnet}</span
					>
					<span
						class="shrink-0 rounded px-1.5 py-0.5 text-[0.55rem] leading-none {athlete.present
							? 'bg-success/15 text-success-text'
							: 'border border-border text-danger-text'}"
					>
						{athlete.present ? 'Presente' : 'Assente'}
					</span>
				</div>
			{/each}
		</div>

		<div class="flex items-center justify-between gap-2 border-t border-border p-2.5">
			<span class="flex items-center gap-1 rounded border border-border px-1.5 py-0.5 text-[0.6rem] text-muted-foreground">
				<QrCode class="size-3" />
				Check-in QR · 4 presenti
			</span>
			<span class="hidden truncate font-mono text-[0.6rem] text-muted-foreground sm:block">5 atleti iscritti</span>
		</div>
	</div>
</MockFrame>
