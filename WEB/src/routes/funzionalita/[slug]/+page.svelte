<script lang="ts">
	import PageLayout from '$lib/components/PageLayout.svelte';
	import { url } from '$lib/config/site';
	import { FEATURE_RELATED } from '$lib/data/features';
	import { HUBS, breadcrumbsFrom } from '$lib/data/routes';
	import type { PageData } from './$types';

	let { data }: { data: PageData } = $props();

	const parent = HUBS.funzionalita;
	const content = $derived(data.feature);
	const slug = $derived(content.slug);

	const meta = $derived({
		title: content.title,
		description: content.description,
		canonical: url(`/funzionalita/${slug}`),
		ogType: 'article' as const
	});

	const crumbs = $derived(breadcrumbsFrom('funzionalita', 'Funzionalità', content.heading, slug));

	const relatedLinks = $derived(
		(FEATURE_RELATED[slug] ?? [])
			.map((relatedSlug) => parent.children.find((child) => child.slug === relatedSlug))
			.filter((child): child is (typeof parent.children)[number] => Boolean(child))
	);
</script>

<PageLayout
	{meta}
	heading={content.heading}
	intro={content.intro}
	sections={content.sections}
	footnotes={content.footnotes}
	breadcrumbs={crumbs}
	hubLinks={parent.children}
	hubTitle="Funzionalità"
	currentSlug={slug}
	{relatedLinks}
/>
