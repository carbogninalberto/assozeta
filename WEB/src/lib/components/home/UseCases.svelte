<script lang="ts">
	import Baby from '@lucide/svelte/icons/baby';
	import Briefcase from '@lucide/svelte/icons/briefcase';
	import LayoutGrid from '@lucide/svelte/icons/layout-grid';
	import Tent from '@lucide/svelte/icons/tent';
	import Ticket from '@lucide/svelte/icons/ticket';
	import Trophy from '@lucide/svelte/icons/trophy';
	import { reveal } from '$lib/actions/reveal';

	const cases = [
		{
			icon: Trophy,
			who: 'ASD e SSD',
			setup: 'Libro soci · tesseramenti · ricevute',
			body: 'Anagrafiche di soci e atleti, iscrizioni, rinnovi e tesseramenti con tessera digitale. Quote, scadenze e ricevute si gestiscono dallo stesso gestionale.'
		},
		{
			icon: LayoutGrid,
			who: 'Polisportive',
			setup: 'Più corsi · istruttori · sedi',
			body: 'Corsi, calendari, lezioni e presenze per più discipline, con istruttori, collaboratori e sedi diverse. Ogni collaboratore entra con il proprio accesso e i permessi che gli assegni.'
		},
		{
			icon: Ticket,
			who: 'Scuole di danza e palestre',
			setup: 'Carnet · check-in QR',
			body: 'Carnet e pacchetti di ingressi per chi frequenta a lezioni singole, con check-in tramite QR eseguito da un operatore autenticato. I moduli online raccolgono iscrizioni, documenti e firme.'
		},
		{
			icon: Baby,
			who: 'Settori giovanili',
			setup: 'Minori e tutori · iscrizioni familiari',
			body: 'Anagrafiche dei minori con i tutori collegati, iscrizioni familiari per più figli e certificati medici archiviati insieme agli altri documenti. Documenti e storico delle attività restano consultabili dalla scheda del socio.'
		},
		{
			icon: Tent,
			who: 'Camp estivi e ritiri',
			setup: 'Camp · moduli online',
			body: 'Camp e ritiri sportivi con moduli d’iscrizione pubblici, condivisibili con un link. I pagamenti online passano da Stripe per i flussi supportati.'
		},
		{
			icon: Briefcase,
			who: 'Segreterie e commercialisti',
			setup: 'Collaboratori · export CSV/XLSX',
			body: 'Invita commercialista e segreteria con un accesso dedicato, senza condividere la password del proprietario. Entrate, uscite, conti e riepiloghi di bilancio si consultano e si esportano in CSV e XLSX.'
		}
	];
</script>

<section id="per-chi" class="py-20 sm:py-28" aria-labelledby="per-chi-heading">
	<div class="mx-auto max-w-6xl px-6">
		<div class="max-w-2xl" use:reveal>
			<p class="font-mono text-xs tracking-widest text-brand uppercase">Per chi è</p>
			<h2 id="per-chi-heading" class="mt-3 text-3xl font-bold tracking-tight sm:text-4xl">
				Pensato per chi manda avanti un’associazione
			</h2>
		</div>
		<ul class="mt-10 grid gap-4 sm:grid-cols-2 lg:grid-cols-3">
			{#each cases as item, i}
				<li
					class="group rounded-2xl border border-border bg-card p-6 shadow-xs transition duration-200 hover:-translate-y-0.5 hover:border-primary/30 hover:shadow-mock"
					use:reveal={{ delay: i * 40 }}
				>
					<div class="flex items-center gap-3">
						<span class="flex size-10 shrink-0 items-center justify-center rounded-xl bg-brand-soft text-brand ring-1 ring-primary/15">
							<item.icon class="size-5" aria-hidden="true" />
						</span>
						<h3 class="font-semibold">{item.who}</h3>
					</div>
					<p class="mt-3 font-mono text-[0.7rem] text-brand">{item.setup}</p>
					<p class="mt-2 text-sm leading-relaxed text-muted-foreground">{item.body}</p>
				</li>
			{/each}
		</ul>
	</div>
</section>
