<script lang="ts">
	import Check from '@lucide/svelte/icons/check';
	import CircleAlert from '@lucide/svelte/icons/circle-alert';
	import MockFrame from './MockFrame.svelte';
	import { FALLBACK_RELEASE } from '$lib/config/site';

	/** Installed version shown in the mock; the homepage passes the live release so it matches the hero badge. */
	let { version = FALLBACK_RELEASE.version }: { version?: string } = $props();

	const tabs: { label: string; mobile: boolean; active?: boolean }[] = [
		{ label: 'Panoramica', mobile: true, active: true },
		{ label: 'Backup', mobile: true },
		{ label: 'Aggiornamenti', mobile: true },
		{ label: 'Impostazioni', mobile: false }
	];

	const diagnostics = [
		{ name: 'Database', ok: true },
		{ name: 'Storage', ok: true },
		{ name: 'Coda attività', ok: true },
		{ name: 'Email', ok: false, note: 'Non configurata' }
	];

	const audit = [
		{ text: 'Laura Neri · corso Nuoto agonisti', time: '10:42' },
		{ text: 'Accesso con 2FA · Marco Rossi', time: '09:15' },
		{ text: 'Backup automatico completato', time: '03:00' }
	];
</script>

<MockFrame
	label="Illustrazione della pagina Istanza di Assozeta: schede Panoramica, Backup, Aggiornamenti e Impostazioni, versione installata aggiornata, ultimo backup automatico, verifica dei servizi e registro delle attività."
	path="riviera.assozeta.example / profilo / istanza"
>
	<div class="text-left">
		<div class="flex flex-wrap items-center gap-1 border-b border-border px-2 py-1.5">
			{#each tabs as tab}
				<span
					class="{tab.mobile ? 'inline-flex' : 'hidden sm:inline-flex'} items-center rounded px-1.5 py-0.5 text-[0.6rem] whitespace-nowrap {tab.active
						? 'bg-brand-soft font-medium text-brand'
						: 'text-muted-foreground'}"
				>
					{tab.label}
				</span>
			{/each}
		</div>

		<div class="space-y-2 p-3">
			<div class="flex items-center justify-between gap-2 rounded-md border border-border bg-subtle p-2.5">
				<div class="min-w-0">
					<p class="truncate text-[0.7rem] text-foreground">
						Versione installata <span class="font-mono text-[0.65rem]">{version}</span>
					</p>
					<p class="mt-0.5 text-[0.6rem] leading-snug text-muted-foreground">Ultimo backup · oggi 03:00 · automatico</p>
				</div>
				<span class="shrink-0 rounded bg-success/15 px-1.5 py-0.5 text-[0.55rem] leading-none text-success-text"
					>Aggiornata</span
				>
			</div>

			<div class="rounded-md border border-border bg-subtle p-2.5">
				<div class="flex items-center justify-between gap-2">
					<p class="text-[0.6rem] tracking-wide text-muted-foreground uppercase">Stato dei servizi</p>
					<span class="rounded border border-border bg-card px-1.5 py-0.5 text-[0.55rem] leading-none text-foreground"
						>Verifica ora</span
					>
				</div>
				<div class="mt-1.5 grid grid-cols-1 gap-x-2 gap-y-1 sm:grid-cols-2">
					{#each diagnostics as item}
						<p class="flex min-w-0 items-center gap-1 text-[0.6rem]">
							{#if item.ok}
								<Check class="size-3 shrink-0 text-success-text" />
								<span class="truncate text-foreground">{item.name}</span>
							{:else}
								<CircleAlert class="size-3 shrink-0 text-warning-text" />
								<span class="truncate text-warning-text">{item.name} · {item.note}</span>
							{/if}
						</p>
					{/each}
				</div>
			</div>

			<div class="rounded-md border border-border bg-subtle p-2.5">
				<p class="text-[0.6rem] tracking-wide text-muted-foreground uppercase">Registro attività</p>
				<ul class="mt-1.5 space-y-1">
					{#each audit as entry}
						<li class="flex items-baseline justify-between gap-2">
							<span class="min-w-0 truncate text-[0.65rem] text-foreground">{entry.text}</span>
							<span class="shrink-0 font-mono text-[0.6rem] text-muted-foreground tabular-nums">{entry.time}</span>
						</li>
					{/each}
				</ul>
			</div>
		</div>
	</div>
</MockFrame>
