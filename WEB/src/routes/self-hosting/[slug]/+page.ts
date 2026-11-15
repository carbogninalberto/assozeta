import { error } from '@sveltejs/kit';
import { HUBS } from '$lib/data/routes';
import { SELF_HOSTING_PAGES } from '$lib/data/self-hosting';

export const prerender = true;

export function entries() {
	return HUBS.selfHosting.children.map((child) => ({ slug: child.slug }));
}

export function load({ params }) {
	if (!SELF_HOSTING_PAGES[params.slug]) {
		error(404, 'Guida non trovata');
	}

	return { slug: params.slug };
}
