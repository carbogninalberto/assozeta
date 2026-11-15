<script lang="ts">
	import { onMount } from 'svelte';
	import Moon from '@lucide/svelte/icons/moon';
	import Sun from '@lucide/svelte/icons/sun';
	import { Button } from '$lib/components/ui/button';
	import { theme } from '$lib/theme.svelte';

	let { class: className = '' }: { class?: string } = $props();

	// The prerendered HTML is light, so the first label and icon are the light ones.
	onMount(() => theme.sync());

	const isDark = $derived(theme.current === 'dark');
	const label = $derived(isDark ? 'Attiva il tema chiaro' : 'Attiva il tema scuro');
</script>

<Button
	variant="ghost"
	size="icon"
	class={className}
	aria-label={label}
	title={label}
	onclick={() => theme.toggle()}
>
	{#if isDark}
		<Sun class="size-4" />
	{:else}
		<Moon class="size-4" />
	{/if}
</Button>
