<script lang="ts">
	import Check from '@lucide/svelte/icons/check';
	import type { Snippet } from 'svelte';
	import Breadcrumbs from '$lib/components/Breadcrumbs.svelte';
	import CtaSection from '$lib/components/CtaSection.svelte';
	import Footer from '$lib/components/Footer.svelte';
	import HubLinks from '$lib/components/HubLinks.svelte';
	import Nav from '$lib/components/Nav.svelte';
	import Seo from '$lib/components/Seo.svelte';
	import { reveal } from '$lib/actions/reveal';
	import { url, type PageMeta } from '$lib/config/site';
	import { HUBS, STANDALONE_PAGES, webPageLd, type ContentSection, type Crumb } from '$lib/data/routes';
	import { slugify } from '$lib/utils';

	interface HubLink {
		slug: string;
		label: string;
		href: string;
		description?: string;
	}

	let {
		meta,
		heading,
		intro,
		sections,
		footnotes,
		breadcrumbs,
		hubLinks,
		hubTitle = 'In questa sezione',
		currentSlug,
		relatedLinks,
		children
	}: {
		meta: PageMeta;
		heading: string;
		intro: string;
		sections: ContentSection[];
		footnotes?: string;
		breadcrumbs: Crumb[];
		hubLinks: HubLink[];
		hubTitle?: string;
		currentSlug: string;
		relatedLinks?: HubLink[];
		children?: Snippet;
	} = $props();

	const jsonLd = $derived(
		webPageLd(meta.title, meta.description, meta.canonical ?? url('/'), breadcrumbs, meta.modifiedAt)
	);

	// Stable anchor per H2 (e.g. #importare-i-dati-da-bakney); repeated headings get a numeric suffix.
	const sectionIds = $derived.by(() => {
		const seen = new Map<string, number>();
		return sections.map((section) => {
			const base = slugify(section.heading) || 'sezione';
			const n = (seen.get(base) ?? 0) + 1;
			seen.set(base, n);
			return n === 1 ? base : `${base}-${n}`;
		});
	});

	const isExternal = (href: string) => /^https?:\/\//.test(href);

	// Internal routes mentioned as plain paths in footnotes become links labelled with the page name.
	const routeLabels = new Map<string, string>([
		...Object.values(HUBS).flatMap((hub) => [
			[hub.href, hub.label] as const,
			...hub.children.map((child) => [child.href, child.label] as const)
		]),
		...STANDALONE_PAGES.map((page) => [page.path, page.label] as const)
	]);

	const footnoteParts = $derived(
		(footnotes ?? '').split(/(\/[a-z0-9-]+(?:\/[a-z0-9-]+)*)/).map((text) => ({
			text,
			label: routeLabels.get(text)
		}))
	);
</script>

<Seo meta={{ ...meta, jsonLd }} />

<Nav />

<main id="main" tabindex="-1" class="outline-none mx-auto max-w-6xl px-6 pt-28 pb-20">
	<div class="mb-8">
		<Breadcrumbs {breadcrumbs} />
	</div>

	<div class="grid gap-10 lg:grid-cols-[1fr_260px]">
		<!-- Main content -->
		<article class="min-w-0 [overflow-wrap:anywhere]">
			<div use:reveal>
				<h1 class="text-4xl font-bold tracking-tight sm:text-5xl">
					<span class="gradient-text">{heading}</span>
				</h1>
				<p class="mt-4 text-lg leading-relaxed text-muted-foreground">{intro}</p>
			</div>

			<div class="mt-12 space-y-14">
				{#each sections as section, i}
					<section id={sectionIds[i]} class="scroll-mt-24" use:reveal={{ delay: i * 50 }}>
						<h2 class="flex items-center gap-3 text-2xl font-semibold tracking-tight">
							<span class="h-px w-6 bg-primary/40"></span>
							{section.heading}
						</h2>
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
					{#each footnoteParts as part}{#if part.label}<a
								href={part.text}
								class="text-brand underline underline-offset-4 hover:text-brand-strong">{part.label}</a
							>{:else}{part.text}{/if}{/each}
				</aside>
			{/if}

		</article>

		<!-- Sidebar -->
		<aside class="space-y-6" use:reveal={{ delay: 100 }}>
			<HubLinks links={hubLinks} {currentSlug} title={hubTitle} />

			{#if relatedLinks && relatedLinks.length > 0}
				<HubLinks links={relatedLinks} title="Pagine correlate" />
			{/if}
		</aside>
	</div>

	<CtaSection />
</main>

<Footer />
