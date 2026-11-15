<script lang="ts">
	import { cn } from '$lib/utils';

	let {
		class: className = 'h-7 w-auto',
		width = 145,
		height = 28
	}: { class?: string; width?: number; height?: number } = $props();
</script>

<img src="/logo.svg" {width} {height} alt="Assozeta" class={cn(className, 'dark:hidden')} />
<img src="/logo-white.svg" {width} {height} alt="" aria-hidden="true" class={cn(className, 'hidden dark:block')} />
