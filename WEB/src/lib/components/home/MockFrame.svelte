<script lang="ts">
	import type { Snippet } from 'svelte';

	let { label, path, children }: { label: string; path: string; children: Snippet } = $props();
</script>

<figure class="min-w-0 overflow-hidden rounded-xl border border-border bg-surface shadow-mock">
	<div class="flex items-center gap-2 border-b border-border bg-surface-bar px-3 py-2" aria-hidden="true">
		<span class="flex shrink-0 gap-1.5">
			<span class="size-2.5 rounded-full bg-muted-foreground/25"></span>
			<span class="size-2.5 rounded-full bg-muted-foreground/25"></span>
			<span class="size-2.5 rounded-full bg-muted-foreground/25"></span>
		</span>
		<span class="ml-2 truncate font-mono text-[0.7rem] text-muted-foreground">{path}</span>
	</div>
	<div aria-hidden="true" class="select-none">
		{@render children()}
	</div>
	<figcaption class="sr-only">{label}</figcaption>
</figure>
