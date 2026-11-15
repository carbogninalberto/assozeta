import { error } from '@sveltejs/kit';
import { FEATURE_PAGES } from '$lib/data/features';
import { HUBS } from '$lib/data/routes';

export const prerender = true;

export function entries() {
	return HUBS.funzionalita.children.map((child) => ({ slug: child.slug }));
}

export function load({ params }: { params: { slug: string } }) {
	const feature = FEATURE_PAGES[params.slug];

	if (!feature) {
		error(404, 'Funzionalità non trovata');
	}

	return { feature };
}
