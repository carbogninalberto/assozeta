<script lang="ts">
	import Check from '@lucide/svelte/icons/check';
	import type { Snippet } from 'svelte';
	import Breadcrumbs from '$lib/components/Breadcrumbs.svelte';
	import CtaSection from '$lib/components/CtaSection.svelte';
	import Footer from '$lib/components/Footer.svelte';
	import Nav from '$lib/components/Nav.svelte';
	import Seo from '$lib/components/Seo.svelte';
	import { reveal } from '$lib/actions/reveal';
	import { url, type PageMeta } from '$lib/config/site';
	import { webPageLd, type ContentSection, type Crumb } from '$lib/data/routes';

	let {
		meta: metaInput,
		heading,
		intro,
		sections,
		footnotes,
		breadcrumbs: crumbs,
		children
	}: {
		meta: PageMeta;
		heading: string;
		intro?: string;
		sections: ContentSection[];
		footnotes?: string;
		breadcrumbs: Crumb[];
		children?: Snippet;
	} = $props();

	const jsonLd = $derived(
		webPageLd(metaInput.title, metaInput.description, metaInput.canonical ?? url('/'), crumbs, metaInput.modifiedAt)
	);

	const isExternal = (href: string) => /^https?:\/\//.test(href);
</script>

<Seo meta={{ ...metaInput, ogType: metaInput.ogType ?? 'article', jsonLd }} />

<Nav />

<main id="main" tabindex="-1" class="outline-none mx-auto max-w-3xl px-6 pt-28 pb-20">
	<div class="mb-8">
		<Breadcrumbs breadcrumbs={crumbs} />
	</div>

	<article class="min-w-0 [overflow-wrap:anywhere]">
		<div use:reveal>
			<h1 class="text-4xl font-bold tracking-tight sm:text-5xl">
				<span class="gradient-text">{heading}</span>
			</h1>
			{#if intro}
				<p class="mt-4 text-lg leading-relaxed text-muted-foreground">{intro}</p>
			{/if}
		</div>

		<div class="mt-12 space-y-14">
			{#each sections as section, i}
				<section use:reveal={{ delay: i * 50 }}>
					{#if section.heading}
						<h2 class="flex items-center gap-3 text-2xl font-semibold tracking-tight">
							<span class="h-px w-6 bg-primary/40"></span>
							{section.heading}
						</h2>
					{/if}
					{#if section.body}
						<p class="mt-3 leading-relaxed text-muted-foreground">{section.body}</p>
					{/if}
					{#if section.code}
						<pre
							class="mt-4 overflow-x-auto rounded-xl border border-border bg-surface-bar p-4 font-mono text-sm"><code
								>{section.code}</code
							></pre>
					{/if}
					{#if section.list && section.list.length > 0}
						<ul class="mt-4 space-y-2.5 text-sm text-muted-foreground">
							{#each section.list as item}
								<li class="flex items-start gap-2.5">
									<Check class="mt-0.5 size-4 shrink-0 text-brand" />
									<span>{item}</span>
								</li>
							{/each}
						</ul>
					{/if}
					{#if section.links && section.links.length > 0}
						<ul class="mt-4 space-y-2 text-sm">
							{#each section.links as link}
								<li>
									<a
										class="text-brand underline-offset-4 hover:text-brand-strong hover:underline"
										href={link.href}
										target={isExternal(link.href) ? '_blank' : undefined}
										rel={isExternal(link.href) ? 'noopener noreferrer' : undefined}>{link.label}</a
									>
								</li>
							{/each}
						</ul>
					{/if}
				</section>
			{/each}
		</div>

		{#if children}
			<div class="mt-14">{@render children()}</div>
		{/if}

		{#if footnotes}
			<aside class="mt-12 rounded-lg border border-border bg-subtle p-4 text-xs text-muted-foreground">
				{footnotes}
			</aside>
		{/if}

		<CtaSection />
	</article>
</main>

<Footer />
