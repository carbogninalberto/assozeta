<script lang="ts">
	import type { Component, Snippet } from 'svelte';
	import ArrowRight from '@lucide/svelte/icons/arrow-right';
	import Check from '@lucide/svelte/icons/check';
	import { reveal } from '$lib/actions/reveal';

	let {
		id,
		number,
		eyebrow,
		title,
		body,
		points = [],
		link,
		reverse = false,
		icon,
		tinted = false,
		visual
	}: {
		id: string;
		number: string;
		eyebrow: string;
		title: string;
		body: string;
		points?: string[];
		link?: { label: string; href: string };
		reverse?: boolean;
		icon?: Component;
		tinted?: boolean;
		visual: Snippet;
	} = $props();

	const Icon = $derived(icon);
</script>

<section
	{id}
	class="relative overflow-hidden py-20 sm:py-28 {tinted ? 'border-y border-border bg-tint' : ''}"
	aria-labelledby="{id}-heading"
>
	{#if tinted}
		<div
			class="dot-grid pointer-events-none absolute inset-0 -z-0 opacity-60 [mask-image:radial-gradient(ellipse_at_center,black,transparent_70%)]"
			aria-hidden="true"
		></div>
	{/if}
	<div class="relative mx-auto grid max-w-6xl items-center gap-12 px-6 lg:grid-cols-2 lg:gap-16">
		<div class="min-w-0 {reverse ? 'lg:order-2' : ''}" use:reveal>
			{#if Icon}
				<div class="mb-5 flex size-11 items-center justify-center rounded-xl bg-brand-soft text-brand ring-1 ring-primary/15">
					<Icon class="size-5" aria-hidden="true" />
				</div>
			{/if}
			<p class="font-mono text-xs tracking-widest text-brand uppercase">
				<span class="text-muted-foreground">{number}</span> · {eyebrow}
			</p>
			<h2 id="{id}-heading" class="mt-3 text-3xl font-bold tracking-tight text-balance sm:text-4xl">{title}</h2>
			<p class="mt-4 text-lg leading-relaxed text-muted-foreground">{body}</p>
			{#if points.length > 0}
				<ul class="mt-6 space-y-2.5 text-sm">
					{#each points as point}
						<li class="flex items-start gap-3">
							<span class="mt-px flex size-5 shrink-0 items-center justify-center rounded-full bg-success/15 text-success-text">
								<Check class="size-3" aria-hidden="true" />
							</span>
							<span class="text-foreground/80">{point}</span>
						</li>
					{/each}
				</ul>
			{/if}
			{#if link}
				<a
					href={link.href}
					class="mt-6 inline-flex items-center gap-1 text-sm font-medium text-brand hover:text-brand-strong"
				>
					{link.label}
					<ArrowRight class="size-4" aria-hidden="true" />
				</a>
			{/if}
		</div>
		<div class="relative isolate min-w-0 {reverse ? 'lg:order-1' : ''}" use:reveal={{ delay: 100 }}>
			<div class="absolute -inset-4 -z-10 rounded-[2rem] bg-primary/10 blur-2xl sm:-inset-8" aria-hidden="true"></div>
			{@render visual()}
		</div>
	</div>
</section>
