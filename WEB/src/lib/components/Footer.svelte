<script lang="ts">
	import Logo from '$lib/components/Logo.svelte';
	import { FOOTER_COLUMNS } from '$lib/data/navigation';
	import { LINKS } from '$lib/config/site';

	const year = new Date().getFullYear();

	function starPoints(cx: number, cy: number, outer: number, inner: number, rot: number) {
		const pts: string[] = [];
		for (let i = 0; i < 10; i++) {
			const r = i % 2 === 0 ? outer : inner;
			const angle = rot + (i * Math.PI) / 5 - Math.PI / 2;
			pts.push(`${cx + r * Math.cos(angle)},${cy + r * Math.sin(angle)}`);
		}
		return pts.join(' ');
	}
</script>

<footer class="border-t border-border bg-subtle">
	<div class="mx-auto max-w-6xl px-6 py-12">
		<div class="grid gap-10 sm:grid-cols-2 lg:grid-cols-5">
			<div class="flex min-w-0 flex-col gap-3 sm:col-span-2">
				<Logo class="h-6 w-auto self-start" width={124} height={24} />
				<p class="text-xs text-muted-foreground">
					Il gestionale open source per associazioni e società sportive dilettantistiche. Nato dall'esperienza di
					Bakney.
				</p>
				<address class="mt-2 text-xs leading-relaxed text-muted-foreground not-italic">
					<span class="font-medium text-foreground/80">Bakney srl</span><br />
					P. IVA 04921790236<br />
					Sede legale: Q.re Aldo Moro 49, 37032 Monteforte d'Alpone (VR), Italia<br />
					N. REA VR-456410 · Registro delle Imprese di Verona<br />
					Capitale sociale € 1.000,00 i.v.
				</address>
			</div>

			{#each FOOTER_COLUMNS as column}
				<nav aria-label={column.title} class="flex min-w-0 flex-col gap-2.5 text-sm">
					<p class="text-xs font-semibold tracking-widest text-muted-foreground uppercase">{column.title}</p>
					{#each column.links as link}
						<a
							href={link.href}
							target={link.external ? '_blank' : undefined}
							rel={link.external ? 'noopener' : undefined}
							class="[overflow-wrap:anywhere] text-muted-foreground transition-colors hover:text-foreground"
							>{link.label}</a
						>
					{/each}
				</nav>
			{/each}
		</div>
		<p class="mt-8 text-xs text-muted-foreground">
			Contatti: <a href="mailto:{LINKS.email}" class="transition-colors hover:text-foreground">{LINKS.email}</a>
		</p>

		<div class="mt-10 flex flex-col items-center justify-between gap-3 border-t border-border pt-6 sm:flex-row">
			<p class="text-xs text-muted-foreground">
				© {year} Bakney srl. Assozeta è software libero sotto licenza GNU AGPL v3.0.
			</p>
			<p class="flex items-center gap-1.5 text-xs text-muted-foreground" title="Fatto con cura in Europa">
				Fatto con cura in
				<svg
					viewBox="0 0 60 40"
					class="size-3.5 rounded-[2px] ring-1 ring-border"
					role="img"
					aria-label="Bandiera dell'Unione europea"
				>
					<rect width="60" height="40" fill="#003399" />
					<g fill="#FFCC00">
						{#each Array(12) as _, i}
							{@const a = ((i * 30 - 90) * Math.PI) / 180}
							{@const x = 30 + 9 * Math.cos(a)}
							{@const y = 20 + 9 * Math.sin(a)}
							<polygon points={starPoints(x, y, 1.7, 0.75, a)} />
						{/each}
					</g>
				</svg>
				Europa
			</p>
		</div>
	</div>
</footer>
