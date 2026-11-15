<script lang="ts">
	import PageLayout from '$lib/components/PageLayout.svelte';
	import { url } from '$lib/config/site';
	import { HUBS, breadcrumbsFrom } from '$lib/data/routes';
	import { SELF_HOSTING_PAGES, SELF_HOSTING_RELATED } from '$lib/data/self-hosting';
	import type { PageData } from './$types';

	let { data }: { data: PageData } = $props();

	const hub = HUBS.selfHosting;
	const content = $derived(SELF_HOSTING_PAGES[data.slug]);
	const child = $derived(hub.children.find((item) => item.slug === data.slug));

	const meta = $derived({
		title: content.title,
		description: content.description,
		canonical: url(`/${hub.slug}/${data.slug}`),
		ogType: 'article' as const
	});

	const crumbs = $derived(breadcrumbsFrom(hub.slug, hub.label, child?.label ?? content.heading, data.slug));

	const relatedLinks = $derived(
		(SELF_HOSTING_RELATED[data.slug] ?? [])
			.map((related) => hub.children.find((item) => item.slug === related))
			.filter((item): item is (typeof hub.children)[number] => Boolean(item))
	);
</script>

<PageLayout
	{meta}
	heading={content.heading}
	intro={content.intro}
	sections={content.sections}
	footnotes={content.footnotes}
	breadcrumbs={crumbs}
	hubLinks={hub.children}
	hubTitle="Self-hosting"
	currentSlug={data.slug}
	{relatedLinks}
/>
