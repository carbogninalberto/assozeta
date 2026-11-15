<script lang="ts">
	const SPORTS = [
		'Calcio',
		'Pallavolo',
		'Basket',
		'Nuoto',
		'Danza',
		'Arti marziali',
		'Ginnastica',
		'Tennis e padel',
		'Atletica',
		'Fitness',
		'Ciclismo',
		'Equitazione'
	];
</script>

<section aria-labelledby="sports-heading" class="relative overflow-hidden border-y border-border bg-tint py-10">
	<div class="mx-auto flex max-w-6xl flex-col gap-5 px-6 md:flex-row md:items-center md:justify-between">
		<h2 id="sports-heading" class="shrink-0 text-sm font-medium text-muted-foreground">
			Per associazioni e società sportive di ogni disciplina
		</h2>
		<ul class="flex flex-wrap gap-2">
			{#each SPORTS as sport}
				<li class="rounded-full border border-border bg-card px-3 py-1.5 text-sm shadow-xs">{sport}</li>
			{/each}
		</ul>
	</div>
</section>
