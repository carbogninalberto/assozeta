/**
 * Centralized site configuration.
 * The single source of truth for canonical origin, default metadata,
 * and reusable SEO utilities. Import this from any page or component
 * that needs absolute URLs or shared defaults.
 */

export const ORIGIN = 'https://assozeta.it';

export const SITE = {
	name: 'Assozeta',
	shortName: 'Assozeta',
	description:
		'Assozeta è il gestionale open source per ASD e SSD: soci, iscrizioni online, corsi, presenze, pagamenti e contabilità. Licenza AGPL, nessun costo per socio, installato sul tuo server.',
	tagline: 'Il gestionale open source per le associazioni sportive',
	origin: ORIGIN,
	defaultImage: `${ORIGIN}/social-card.png`,
	defaultImageAlt: 'Assozeta, il gestionale open source per associazioni sportive',
	ogImageWidth: 1200,
	ogImageHeight: 630,
	locale: 'it_IT',
	themeColor: '#ffffff'
} as const;

/** Canonical destinations used across navigation, footer and CTAs. */
export const LINKS = {
	github: 'https://github.com/carbogninalberto/assozeta',
	issues: 'https://github.com/carbogninalberto/assozeta/issues',
	releases: 'https://github.com/carbogninalberto/assozeta/releases',
	telegram: 'https://t.me/+Cxbg24Az1S0wMjI0',
	bakney: 'https://bakney.com',
	email: 'support@bakney.com'
} as const;

/** Last release known at writing; used when the build cannot reach the GitHub API. */
export const FALLBACK_RELEASE = {
	version: 'v1.0.40',
	url: 'https://github.com/carbogninalberto/assozeta/releases/tag/v1.0.40',
	publishedAt: '2026-11-15'
} as const;

/** Minimum GitHub star count worth showing next to the repository link. */
export const MIN_STARS_SHOWN = 50;

type OpenGraphType = 'website' | 'article';

export interface PageMeta {
	title: string;
	description: string;
	canonical?: string;
	noindex?: boolean;
	image?: string;
	imageAlt?: string;
	imageWidth?: number;
	imageHeight?: number;
	ogType?: OpenGraphType;
	publishedAt?: string;
	modifiedAt?: string;
	jsonLd?: Record<string, unknown> | Record<string, unknown>[];
}

/**
 * Build absolute URL for a path (adds origin).
 */
export function url(path: string): string {
	const base = ORIGIN.replace(/\/+$/, '');
	const p = path.startsWith('/') ? path : `/${path}`;
	return `${base}${p}`;
}

/**
 * Return complete metadata including derived defaults and absolute URLs.
 */
export function resolveMeta(meta: PageMeta) {
	const canonical = meta.canonical ?? url('');
	const imageAbsolute = meta.image
		? meta.image.startsWith('http')
			? meta.image
			: url(meta.image)
		: SITE.defaultImage;

	return {
		title: meta.title,
		description: meta.description,
		canonical,
		noindex: meta.noindex ?? false,
		ogType: meta.ogType ?? 'website',
		image: imageAbsolute,
		imageAlt: meta.imageAlt ?? SITE.defaultImageAlt,
		imageWidth: meta.imageWidth ?? SITE.ogImageWidth,
		imageHeight: meta.imageHeight ?? SITE.ogImageHeight,
		publishedAt: meta.publishedAt,
		modifiedAt: meta.modifiedAt,
		jsonLd: meta.jsonLd
	};
}
